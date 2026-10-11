"""Private, pinned science environment; never installs into a user's Conda."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

from .core import atomic_json, safe_path

RESOURCES = Path(__file__).parent / 'resources'
MANIFEST = json.loads((RESOURCES / 'runtime.json').read_text())
REQUIRED_CHECKS = frozenset(('environment', 'imports', 'topsApp', 'alos2App', 'stripmapApp',
                             'topsStack', 'alosStack', 'stripmapStack', 'shell-selectors',
                             'generator', 'kernels', 'plotting', 'mintpy', 'pip-dependencies', 'architecture'))


def validate_report(result):
    checks = result.get('checks', [])
    if (result.get('passed') is not True or len(checks) != len(REQUIRED_CHECKS)
            or {c.get('name') for c in checks} != REQUIRED_CHECKS
            or any(c.get('status') != 'PASS' for c in checks)):
        raise RuntimeError('15 項必要驗證未全部通過，尚未啟用這個環境。')
    return checks


def workbench_home():
    return Path(os.environ.get('SAR_WORKBENCH_HOME', str(Path.home() / 'sar-workbench'))).expanduser().absolute()


def private_env(home):
    """Do not inherit Python, Conda, compiler or library settings from a shell."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('PYTHON', 'CONDA', 'MAMBA', 'DYLD_', 'ISCE', 'GDAL', 'PROJ',
                                'FONTCONFIG', 'MAGICK_', '_INSAR_ARM_', 'CFLAGS', 'LDFLAGS'))}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', CONDA_SUBDIR='osx-arm64',
               CONDARC=str(home / 'runtime/condarc'),
               CONDA_PKGS_DIRS=str(home / 'runtime/packages'),
               CONDA_ENVS_PATH=str(home / 'runtime/envs'),
               CONDA_NUMBER_CHANNEL_NOTICES='0',
               CONDA_REGISTER_ENVS='false', XDG_CACHE_HOME=str(home / 'runtime/cache'),
               MPLCONFIGDIR=str(home / 'runtime/cache/matplotlib'),
               OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MPLBACKEND='Agg')
    return env


def supported_platform():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        return False, '自動安裝目前支援 Apple Silicon Mac；其他平台可繼續選用既有科學環境。'
    version = tuple(int(x) for x in platform.mac_ver()[0].split('.')[:2])
    if version < (14, 5):
        return False, '這組固定版本套件需要 macOS 14.5 或更新。'
    return True, 'Apple Silicon · macOS ' + platform.mac_ver()[0]


def verified_lock():
    lock = RESOURCES / MANIFEST['lock']['name']
    if hashlib.sha256(lock.read_bytes()).hexdigest() != MANIFEST['lock']['sha256']:
        raise ValueError('環境套件清單校驗失敗，請重新下載完整工作台。')
    lines = lock.read_text().splitlines()
    allowed = ('https://conda.anaconda.org/conda-forge/', 'https://chris20011026.github.io/insar-arm/')
    urls = [s for s in lines if s and not s.startswith(('#', '@'))]
    if '@EXPLICIT' not in lines or not urls or any(
            not u.startswith(allowed) or not re.fullmatch('[0-9a-f]{32}', u.rsplit('#', 1)[-1]) for u in urls):
        raise ValueError('套件來源或校驗碼不符合發布清單。')
    return lock, len(urls)


def ready_environment(home=None):
    home = Path(home or workbench_home())
    try:
        state = json.loads((home / 'runtime/ready.json').read_text())
        prefix = Path(state['prefix'])
        if state['lock_sha256'] != MANIFEST['lock']['sha256']:
            return None
        if prefix.parent != home / 'runtime/envs' or prefix.is_symlink():
            return None
        if all((prefix / p).is_file() for p in ('bin/python', 'bin/insar-check', 'share/isce2/alosStack/create_cmds.py')):
            return prefix
    except (OSError, ValueError, KeyError):
        pass
    return None


class RuntimeManager:
    def __init__(self, home=None):
        self.home = Path(home or workbench_home())
        self.mutex = threading.Lock()
        self.thread = None
        self.process = None
        self.cancelled = threading.Event()
        self.status_file = self.home / 'runtime/install-state.json'

    def status(self):
        supported, platform_note = supported_platform()
        existing = next((p for p in [self.home, *self.home.parents] if p.exists()), Path.home())
        result = dict(status='not_installed', stage='準備第一次使用',
                      home=str(self.home), supported=supported, platform=platform_note,
                      free_gb=round(shutil.disk_usage(existing).free / 1024**3, 1),
                      version=MANIFEST['runtime_version'], bootstrap_ready=(self.home / 'runtime/miniforge/bin/conda').is_file())
        try:
            state = json.loads(self.status_file.read_text())
            result.update(state)
            if state.get('status') == 'running' and not self._locked():
                result.update(status='interrupted', stage='上次安裝中斷',
                              error='可以重新安裝。已下載的套件會重用；未完成的環境會保留供檢查。')
            log = self.home / 'runtime' / str(state.get('log_name', 'install.log'))
            if log.parent == self.home / 'runtime' and log.is_file():
                with log.open('rb') as f:
                    f.seek(max(0, log.stat().st_size - 14000))
                    result['output'] = f.read().decode(errors='replace')
        except (OSError, ValueError):
            pass
        prefix = ready_environment(self.home)
        if prefix:
            result['environment'] = str(prefix)
            if result['status'] not in ('running', 'failed', 'cancelled'):
                result.update(status='ready', stage='運算環境已驗證，可以開始使用')
        return result

    def _locked(self):
        path = self.home / 'runtime/install.lock'
        if not path.exists():
            return False
        with path.open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True

    def start(self, consent=False, recheck=False, synchronous=False):
        if not consent:
            raise ValueError('請先閱讀安裝內容與第三方授權說明，再按安裝。')
        supported, message = supported_platform()
        if not supported:
            raise ValueError(message)
        safe_path(self.home)
        if self.status()['free_gb'] < 12 and not recheck:
            raise ValueError('安裝需要至少 12 GB 可用空間；SAR 影像與處理成果另外計算。')
        with self.mutex:
            if self.thread and self.thread.is_alive():
                raise ValueError('安裝或驗證正在進行，請等待完成。')
            (self.home / 'runtime').mkdir(parents=True, exist_ok=True, mode=0o700)
            handle = (self.home / 'runtime/install.lock').open('a')
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                raise ValueError('另一個工作台正在安裝，請在原視窗查看進度。') from None
            self.cancelled.clear()
            attempt = uuid.uuid4().hex[:10]
            self.state = dict(status='running', stage='檢查安裝來源', attempt=attempt,
                              log_name=f'install-{attempt}.log', error='', checks=[])
            atomic_json(self.status_file, self.state)
            if synchronous:
                self._install(handle, recheck)
            else:
                self.thread = threading.Thread(target=self._install, args=(handle, recheck), daemon=True)
                self.thread.start()
        return self.status()

    def _stage(self, text):
        self.state['stage'] = text
        atomic_json(self.status_file, self.state)

    def _run(self, args, timeout=3600):
        if self.cancelled.is_set():
            raise RuntimeError('已取消安裝。')
        log = self.home / 'runtime' / self.state['log_name']
        with log.open('a') as output:
            self.process = subprocess.Popen(list(map(str, args)), stdout=output, stderr=subprocess.STDOUT,
                                            env=private_env(self.home), start_new_session=True)
            if self.cancelled.is_set():
                self.stop()
            try:
                code = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.stop()
                raise RuntimeError('安裝等待逾時，已保留紀錄與下載快取，可重試。') from None
            finally:
                self.process = None
        if code:
            raise RuntimeError(f'此步驟未成功完成（結束碼 {code}），請查看下方紀錄後重試。')

    def _install(self, handle, recheck):
        try:
            lock, count = verified_lock()
            conda = self.home / 'runtime/miniforge/bin/conda'
            if not conda.is_file():
                raise RuntimeError('請先以 Mac 應用程式開啟，讓啟動器準備必要的安裝工具。')
            config = self.home / 'runtime/condarc'
            config.write_text('channels:\n  - conda-forge\nchannel_priority: strict\nauto_activate_base: false\nnumber_channel_notices: 0\nregister_envs: false\n')
            prefix = ready_environment(self.home)
            if recheck and not prefix:
                raise ValueError('尚未有完成安裝的專用環境。')
            if not prefix:
                prefix = self.home / 'runtime/envs' / ('insar-' + MANIFEST['runtime_version'] + '-' + self.state['attempt'])
                self._stage(f'下載並安裝 {count} 個固定版本套件')
                self._run([conda, 'create', '--yes', '--prefix', prefix, '--file', lock, '--no-default-packages'])
            report = self.home / 'runtime/reports' / self.state['attempt']
            self._stage('執行環境、影像繪製與數值運算驗證')
            self._run([conda, 'run', '--no-capture-output', '--prefix', prefix,
                       'insar-check', '--output', report], timeout=2400)
            result = json.loads((report / 'report.json').read_text())
            checks = validate_report(result)
            # Run the workbench's own adapter against the pinned MintPy, too.
            self._stage('確認工作台與這個科學環境相容')
            self._run([conda, 'run', '--no-capture-output', '-p', prefix, 'python',
                       Path(__file__).with_name('demo_worker.py'), '--output', report / 'workbench-demo'], timeout=600)
            if self.cancelled.is_set():
                raise RuntimeError('已停止驗證；環境尚未啟用。')
            self.state.update(status='ready', stage='安裝與驗證完成',
                              checks=[{'name': c['name'], 'status': c['status']} for c in checks],
                              report=str(report), environment=str(prefix))
            atomic_json(self.home / 'runtime/ready.json', dict(prefix=str(prefix),
                        lock_sha256=MANIFEST['lock']['sha256'], verified_at=time.time(), report=str(report)))
        except Exception as e:
            self.state.update(status='cancelled' if self.cancelled.is_set() else 'failed', error=str(e))
        finally:
            atomic_json(self.status_file, self.state)
            handle.close()

    def stop(self):
        import signal
        self.cancelled.set()
        p = self.process
        if p and p.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGTERM)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', type=Path, required=True)
    p.add_argument('--install', action='store_true')
    a = p.parse_args()
    manager = RuntimeManager(a.home)
    if a.install:
        manager.start(consent=True, synchronous=True)
    result = manager.status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'ready' else 1


if __name__ == '__main__':
    sys.exit(main())
