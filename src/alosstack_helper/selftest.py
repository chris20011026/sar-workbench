"""Isolated, cancellable native science self-test; no user project is opened."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

from .core import atomic_json, find_conda
from .environment import isolated_env

CASES = [
    {'id': 'pipeline', 'label': '已知位移與分段判讀'},
    {'id': 'closure', 'label': '整週解纏錯誤偵測'},
    {'id': 'dem', 'label': '已知 DEM 誤差校正'},
    {'id': 'rms', 'label': '異常日期殘差偵測'},
    {'id': 'geography', 'label': '地圖、範圍與成果匯出'},
]
LIMITATIONS = [
    '全部為人工合成的小型資料，不代表任何真實研究區域。',
    '驗證目前環境的 MintPy 數值、診斷與地理輸出；不驗證原始 SAR 對位或真實資料科學精度。',
    '自動確認只用於這些已知答案的合成案例；真實專案的人工判讀不會被略過。',
    '未涵蓋真實軌道與地理定位精度、解纏修正方法、電離層、對流層或固體潮校正。',
]


class SelfTests:
    def __init__(self, engine, home=None):
        self.engine = engine
        self.home = Path(home) if home is not None else engine.home / 'selftests'
        self.state_file = self.home / 'latest.json'
        self.mutex = threading.Lock()
        self.thread = None
        self.process = None
        self.cancelled = threading.Event()

    def _locked(self):
        if not (self.home / 'run.lock').exists(): return False
        with (self.home / 'run.lock').open('a') as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            except BlockingIOError: return True

    def status(self):
        state = dict(status='idle', stage='尚未執行進階自測', synthetic=True,
                     checks=[], cases=[dict(c, status='pending') for c in CASES], limitations=LIMITATIONS)
        try:
            state.update(json.loads(self.state_file.read_text()))
            run = state.get('run_id', '')
            if len(run) != 12 or any(c not in '0123456789abcdef' for c in run): raise ValueError('invalid self-test id')
            folder = self.home / run
            report = folder / 'report.json'
            if report.is_file():
                details = json.loads(report.read_text())
                for key in ('checks', 'cases', 'versions', 'limitations', 'progress'):
                    if key in details: state[key] = details[key]
                if state['status'] in ('running', 'passed') and details.get('stage'):
                    state['stage'] = details['stage']
                state['report_path'] = str(report)
            if state['status'] == 'running' and not self._locked():
                state.update(status='interrupted', stage='上次自測中斷', error='可重新執行；未完成的紀錄已保留。')
            if state['status'] in ('failed', 'cancelled', 'interrupted'):
                state['cases'] = [dict(c, status=state['status']) if c.get('status') == 'running' else c
                                  for c in state['cases']]
            log = folder / 'selftest.log'
            if log.is_file():
                with log.open('rb') as f:
                    f.seek(max(0, log.stat().st_size - 12000))
                    state['log_tail'] = f.read().decode(errors='replace')
            # Preserve failed/stopped status even if the last worker report had
            # successfully completed an earlier individual case.
            state['passed_checks'] = sum(c.get('status') == 'passed' for c in state['checks'])
            state['failed_checks'] = sum(c.get('status') == 'failed' for c in state['checks'])
        except (OSError, ValueError, TypeError): pass
        return state

    def report(self):
        state = self.status()
        if not state.get('report_path'): raise ValueError('尚無自測報告，請先執行進階自測。')
        report = json.loads(Path(state['report_path']).read_text())
        report['status'] = state['status']
        report['passed'] = state['status'] == 'passed'
        report['stage'] = state['stage']
        report['cases'] = state['cases']
        if state.get('error'): report['error'] = state['error']
        return report

    def start(self, environment, synchronous=False):
        prefix = Path(environment).expanduser().resolve(strict=True)
        conda = find_conda(prefix)
        if not (prefix / 'bin/python').is_file() or not conda:
            raise ValueError('請選擇含 Python、ISCE2、MintPy 的 Conda 環境。')
        with self.mutex, self.engine.lock:
            if self.engine.active or (self.thread and self.thread.is_alive()):
                raise ValueError('已有工作執行中，請等待完成後再自測。')
            # A scientific child can survive an ungraceful server restart.
            # Inspect only its process records; do not reconcile/write any
            # real project's state just to run an isolated synthetic check.
            from .recovery import process_alive
            from .analysis_recovery import unfinished_recovery_alive
            roots = set(getattr(self.engine, 'projects', {}).values())
            if any(process_alive(Path(root)/'.helper') or
                   unfinished_recovery_alive(Path(root)/'analyses') for root in roots):
                raise ValueError('仍有研究專案的背景程序存活，請先完成或停止原本的運算，再執行自測。')
            self.home.mkdir(parents=True, exist_ok=True)
            handle = (self.home / 'run.lock').open('a')
            try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                raise ValueError('另一個工作台正在執行進階自測。') from None
            run = uuid.uuid4().hex[:12]
            folder = self.home / run
            folder.mkdir(mode=0o700)
            self.cancelled.clear()
            state = dict(run_id=run, status='running', stage='準備合成資料',
                         environment=str(prefix), started=time.strftime('%Y-%m-%d %H:%M:%S'),
                         synthetic=True, checks=[], error='')
            atomic_json(self.state_file, state)
            self.engine.active = 'selftest:' + run
            self.thread = threading.Thread(target=self._run, args=(state, conda, handle), daemon=True)
        if synchronous: self._run(state, conda, handle)
        else: self.thread.start()
        return self.status()

    def _run(self, state, conda, handle):
        folder = self.home / state['run_id']
        try:
            env = isolated_env(folder / 'cache')
            args = [conda, 'run', '--no-capture-output', '-p', state['environment'], 'python',
                    str(Path(__file__).with_name('selftest_worker.py')), '--output', str(folder)]
            with (folder / 'selftest.log').open('w') as log:
                # The inherited lock prevents a second server from starting a
                # test while an orphan worker is still finishing.
                self.process = subprocess.Popen(args, cwd=folder, env=env, stdout=log,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    start_new_session=True, pass_fds=(handle.fileno(),))
                if self.cancelled.is_set(): self._terminate()
                try: code = self.process.wait(timeout=1200)
                except subprocess.TimeoutExpired:
                    self._terminate()
                    raise RuntimeError('進階自測超過 20 分鐘，已停止並保留日誌。') from None
            if self.cancelled.is_set():
                state.update(status='cancelled', stage='自測已停止', error='未完成的檢核不算通過。')
            elif code:
                raise RuntimeError('進階自測未全部通過，請查看檢核項目與日誌。')
            else:
                report = json.loads((folder / 'report.json').read_text())
                if report.get('status') != 'passed' or not report.get('checks') or any(c['status'] != 'passed' for c in report['checks']):
                    raise RuntimeError('自測報告未完整通過。')
                state.update(status='passed', stage='進階自測完成')
        except Exception as error:
            state.update(status='cancelled' if self.cancelled.is_set() else 'failed',
                         stage='自測已停止' if self.cancelled.is_set() else '自測未通過', error=str(error))
        finally:
            self.process = None
            state['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
            atomic_json(self.state_file, state)
            handle.close()
            with self.engine.lock:
                if self.engine.active == 'selftest:' + state['run_id']: self.engine.active = None

    def _terminate(self):
        process = self.process
        if process and process.poll() is None:
            with contextlib.suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)

    def stop(self):
        # Never delegate to engine.stop(): it could cancel a real project.
        if self.thread and self.thread.is_alive() or self.process is not None:
            self.cancelled.set()
            self._terminate()
        elif self._locked():
            raise ValueError('自測由另一個工作台程序執行，請在原本的視窗停止，或等待完成。')
        return self.status()

    def shutdown(self):
        if self.thread and self.thread.is_alive() or self.process is not None: self.stop()
        if self.thread and self.thread.is_alive(): self.thread.join(timeout=12)
