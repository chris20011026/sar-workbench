"""Read-only MintPy comparisons, with all derivatives kept in local state.

The web process has no scientific dependencies. Expensive checks and rendering
run in the selected Conda environment and never write into the source project.
"""
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid

from .core import atomic_json, find_conda
from .environment import isolated_env

WORKER = Path(__file__).with_name('comparison_worker.py')
ACTIVE = ('queued', 'running')


class Comparisons:
    def __init__(self, analyses):
        self.analyses = analyses
        self.engine = analyses.engine
        self.lock = threading.RLock()
        self.processes = {}
        self.threads = {}
        self.cancelled = set()
        self.owned = set()

    def base(self, key):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', key):
            raise ValueError('無效專案代號。')
        base = self.engine.home / 'research' / 'comparisons' / key
        for p in (base, base.parent, base.parent.parent):
            if p.is_symlink():
                raise ValueError('比較成果資料夾不能是符號連結。')
        return base

    def folder(self, key, comparison):
        if not re.fullmatch(r'[0-9a-f]{12}', comparison):
            raise ValueError('無效比較代號。')
        folder = self.base(key) / comparison
        if folder.is_symlink():
            raise ValueError('比較成果資料夾不能是符號連結。')
        return folder

    def candidates(self, key):
        self.engine.get(key)
        rows = []
        for run in self.analyses.listing(key):
            if run.get('kind') != 'mintpy' or run.get('status') != 'complete':
                continue
            folder = self.analyses.folder(key, run['id'])
            needed = ('velocity.h5', 'maskTempCoh.h5', 'temporalCoherence.h5',
                      'inputs/geometryRadar.h5', 'results.json')
            missing = [name for name in needed if not (folder / name).is_file()]
            rows.append(dict(id=run['id'], name=run.get('options', {}).get('name') or run['id'],
                             created=run.get('created'), environment=run.get('environment'),
                             options=run.get('options', {}), available=not missing,
                             reason='缺少成果：' + '、'.join(missing) if missing else ''))
        return dict(project_id=key, runs=rows)

    def listing(self, key):
        rows = []
        for p in sorted(self.base(key).glob('*/comparison.json'), reverse=True):
            try:
                rows.append(self.get(key, p.parent.name))
            except (OSError, ValueError):
                continue
        return rows

    def get(self, key, comparison):
        folder = self.folder(key, comparison)
        with self.lock:
            value = json.loads((folder / 'comparison.json').read_text())
            if value.get('status') in ACTIVE and comparison not in self.owned:
                value.update(status='failed', error='比較服務曾中斷；來源保持不變，請重新建立比較。')
                atomic_json(folder / 'comparison.json', value)
            progress = folder / 'progress.json'
            if progress.is_file():
                value['progress'] = json.loads(progress.read_text())
            return value

    def start(self, key, left, right):
        if left == right:
            raise ValueError('請選擇兩個不同的 MintPy 分析版本。')
        state = self.engine.get(key)
        jobs = [self.analyses.get(key, run) for run in (left, right)]
        if any(j.get('status') != 'complete' or j.get('kind') != 'mintpy' for j in jobs):
            raise ValueError('比較需要兩個已完成的 MintPy 分析版本。')
        environment = Path(jobs[0]['environment']).expanduser().resolve()
        if not (environment / 'bin/python').is_file() or not find_conda(environment):
            raise ValueError('左側分析環境已無法使用，請先恢復此環境或選擇其他版本。')
        with self.engine.lock, self.lock:
            if self.engine.active or self.processes or any(self.owned):
                raise ValueError('請先完成或取消目前的運算，再建立比較。')
            comparison = uuid.uuid4().hex[:12]
            folder = self.folder(key, comparison)
            folder.mkdir(parents=True, exist_ok=False)
            value = dict(id=comparison, project_id=key, status='queued',
                         created=time.strftime('%Y-%m-%d %H:%M:%S'),
                         left_run=left, right_run=right, error='')
            spec = dict(id=comparison, project_id=key, out=str(folder),
                        left=str(self.analyses.folder(key, left)),
                        right=str(self.analyses.folder(key, right)),
                        project_root=state['config']['root'])
            atomic_json(folder / 'comparison.json', value)
            atomic_json(folder / 'request.json', spec)
            self.owned.add(comparison)
            self.engine.active = key + ':comparison:' + comparison
            self.engine.cancel.clear()
            thread = threading.Thread(target=self._worker, args=(key, comparison, environment), daemon=True)
            self.threads[comparison] = thread
            thread.start()
            return value

    def compare(self, key, left, right, timeout=1800):
        """Synchronous convenience for CLI/tests; HTTP should use start/get."""
        value = self.start(key, left, right)
        deadline = time.monotonic() + timeout
        while value['status'] in ACTIVE:
            if time.monotonic() > deadline:
                self.cancel(key, value['id'])
                raise TimeoutError('比較超過等待時間，已要求停止。')
            time.sleep(.05)
            value = self.get(key, value['id'])
        if value['status'] != 'complete':
            raise ValueError(value.get('error') or '比較未完成。')
        return value

    def _worker(self, key, comparison, environment):
        folder = self.folder(key, comparison)
        process = None
        read_fd = write_fd = None
        marker = key + ':comparison:' + comparison
        try:
            value = json.loads((folder / 'comparison.json').read_text())
            value['status'] = 'running'
            atomic_json(folder / 'comparison.json', value)
            cache = folder / 'cache'
            cache.mkdir()
            read_fd, write_fd = os.pipe()
            command = [find_conda(environment), 'run', '--no-capture-output', '-p', str(environment),
                       'python', str(WORKER), 'compare', str(folder / 'request.json'),
                       '--parent-fd', '0']
            with (folder / 'run.log').open('w') as log:
                process = subprocess.Popen(command, cwd=folder, env=isolated_env(cache),
                                           stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True, stdin=read_fd)
                os.close(read_fd)
                read_fd = None
                with self.lock:
                    self.processes[comparison] = process
                    self.engine.process = process
                    if comparison in self.cancelled or self.engine.cancel.is_set():
                        os.killpg(process.pid, signal.SIGTERM)
                try:
                    code = process.wait(timeout=1800)
                except subprocess.TimeoutExpired as exc:
                    raise ValueError('比較超過 30 分鐘；已保留紀錄。請縮小分析範圍後重試。') from exc
            if comparison in self.cancelled or self.engine.cancel.is_set():
                value.update(status='cancelled', error='比較已取消，來源資料保持不變。')
            elif code:
                from .analysis import failure_detail
                raise ValueError(failure_detail(folder / 'run.log') or '比較工具無法完成，請查看日誌。')
            else:
                result = json.loads((folder / 'result.json').read_text())
                value.update(result, status='complete', error='')
        except Exception as exc:
            value = json.loads((folder / 'comparison.json').read_text())
            value.update(status='failed', error=str(exc))
        finally:
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    pass
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
            with self.engine.lock, self.lock:
                atomic_json(folder / 'comparison.json', value)
                self.processes.pop(comparison, None)
                self.threads.pop(comparison, None)
                self.cancelled.discard(comparison)
                self.owned.discard(comparison)
                if self.engine.active == marker:
                    self.engine.active = None
                    self.engine.process = None

    def cancel(self, key, comparison):
        value = self.get(key, comparison)
        with self.lock:
            if value['status'] not in ACTIVE:
                return value
            self.cancelled.add(comparison)
            process = self.processes.get(comparison)
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            return dict(value, status='cancelling')

    def shutdown(self):
        """Stop only this service's children; never leave hashing on an offline NAS."""
        with self.lock:
            self.cancelled.update(self.owned)
            processes = list(self.processes.values())
            threads = list(self.threads.values())
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout=4)

    def artifact(self, key, comparison, name):
        value = self.get(key, comparison)
        if value['status'] != 'complete':
            raise ValueError('比較尚未完成，不能下載半成品。')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or name not in value.get('artifacts', []):
            raise ValueError('無效比較成果名稱。')
        path = self.folder(key, comparison) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('找不到比較成果。')
        return path

    def point(self, key, comparison, y, x, side='left'):
        if type(y) is not int or type(x) is not int or side not in ('left', 'right'):
            raise ValueError('請提供原生像素的整數行列位置。')
        value = self.get(key, comparison)
        if value['status'] != 'complete':
            raise ValueError('請先完成比較。')
        self.engine.get(key)
        folder = self.folder(key, comparison)
        job = self.analyses.get(key, value['left_run'])
        conda = find_conda(job['environment'])
        if not conda:
            raise ValueError('比較使用的科學環境無法使用。')
        command = [conda, 'run', '--no-capture-output', '-p', job['environment'], 'python',
                   str(WORKER), 'point', str(folder / 'result.json'), str(y), str(x), side]
        result = subprocess.run(command, cwd=folder, env=isolated_env(folder / 'cache'),
                                capture_output=True, text=True, timeout=60)
        values = [row[7:] for row in result.stdout.splitlines() if row.startswith('RESULT:')]
        if result.returncode or not values:
            error = next((row.split('ValueError: ', 1)[1] for row in result.stderr.splitlines()
                          if 'ValueError: ' in row), '無法讀取此位置的比較曲線。')
            raise ValueError(error)
        return json.loads(values[-1])

    def verify(self, key, comparison):
        """Revalidate the SHA-bound input identities before reusing a snapshot."""
        value = self.get(key, comparison)
        if value['status'] != 'complete':
            raise ValueError('比較尚未完成，不能使用半成品。')
        self.engine.get(key)
        folder = self.folder(key, comparison)
        job = self.analyses.get(key, value['left_run'])
        conda = find_conda(job['environment'])
        if not conda:
            raise ValueError('比較使用的科學環境無法使用。')
        result = subprocess.run([conda, 'run', '--no-capture-output', '-p', job['environment'],
                                 'python', str(WORKER), 'verify', str(folder / 'result.json')],
                                cwd=folder, env=isolated_env(folder / 'cache'),
                                capture_output=True, text=True, timeout=60)
        values = [row[7:] for row in result.stdout.splitlines() if row.startswith('RESULT:')]
        if result.returncode or not values:
            error = next((row.split('ValueError: ', 1)[1] for row in result.stderr.splitlines()
                          if 'ValueError: ' in row), '來源無法確認，請重新建立比較。')
            raise ValueError(error)
        return json.loads(values[-1])
