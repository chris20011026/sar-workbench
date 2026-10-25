"""Cancellable, lifetime-bound portable package I/O outside the HTTP thread."""
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from .core import atomic_json
from .runtime import workbench_home

ACTIVE = {'planning', 'running'}


class PortableJobs:
    def __init__(self, engine):
        self.engine = engine
        self.home = engine.home / 'portable-jobs'
        self.lock = threading.RLock()
        self.processes = {}
        self.cancelled = set()

    def folder(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{24}', token):
            raise ValueError('無效的可攜包工作代號。')
        return self.home / token

    def _locked(self):
        path = self.home / 'worker.lock'
        if not path.exists(): return False
        with path.open('a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
                return False
            except BlockingIOError: return True

    def busy(self):
        return bool(self.processes) or self._locked()

    def latest(self):
        path = self.home / 'latest.json'
        return {'job': self.get(json.loads(path.read_text())['job_id']) if path.exists() else None}

    def get(self, token):
        folder = self.folder(token)
        job = json.loads((folder / 'job.json').read_text())
        if job['status'] in ACTIVE and token not in self.processes and not self._locked():
            job.update(status='interrupted', stage='上次工作中斷', error='未完成副本保留。請重新預覽並選擇新的目的資料夾；不會覆寫或自動接續。')
        if (folder / 'preview.json').exists():
            preview = json.loads((folder / 'preview.json').read_text())
            job['preview'] = {k: preview[k] for k in ('source','destination','root','files','bytes','required','free','dates','pairs','reference','warnings','space_ok','name') if k in preview}
        if (folder / 'progress.json').exists(): job['progress'] = json.loads((folder / 'progress.json').read_text())
        return job

    @staticmethod
    def destination(parent, name):
        if not isinstance(parent, str) or not parent.strip() or not Path(parent).expanduser().is_absolute():
            raise ValueError('請選擇完整的存放位置。')
        if not isinstance(name, str) or not name.strip() or name in ('.','..') or len(name) > 160 or any(c in name for c in '/\\\x00\n\r'):
            raise ValueError('請輸入不含斜線的新資料夾名稱。')
        return str(Path(parent).expanduser() / name)

    def _reserve(self, token):
        if self.engine.active or self.busy(): raise ValueError('已有工作執行中，請先等待完成或停止。')
        self.home.mkdir(exist_ok=True)
        handle = (self.home / 'worker.lock').open('a')
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close(); raise ValueError('仍有可攜包程序執行中，請等待結束。')
        self.engine.active = 'portable:' + token
        self.processes[token] = None
        return handle

    def preview(self, mode, data):
        if mode not in ('export','import'): raise ValueError('未知的可攜包操作。')
        token = uuid.uuid4().hex[:24]
        with self.engine.lock, self.lock:
            destination = self.destination(data.get('parent'), data.get('name') if mode=='export' else data.get('folder'))
            request = dict(mode=mode, destination=destination, registry=str(self.engine.home), managed_home=str(workbench_home()))
            if mode == 'export':
                request.update(id=data['id'], state=self.engine.get(data['id']), environment=data.get('environment'))
            else:
                request.update(source=data.get('source'), root=destination, environment=data.get('environment'), name=data.get('name'))
            handle = self._reserve(token)
            try:
                folder = self.folder(token); folder.mkdir()
                atomic_json(folder/'request.json', request)
                atomic_json(folder/'job.json', dict(job_id=token, mode=mode, source_id=data.get('id') if mode=='export' else None, id=data.get('id') if mode=='export' else None, status='planning', stage='整理相依檔案與檢查內容', destination=destination, created=time.time()))
                atomic_json(self.home/'latest.json', {'job_id':token})
                threading.Thread(target=self._worker, args=(token,'preview',handle), daemon=True).start()
            except Exception:
                handle.close(); self.processes.pop(token,None); self.engine.active=None; raise
        return self.get(token)

    def start(self, token, reviewed=False):
        with self.engine.lock, self.lock:
            job = self.get(token)
            if job['status'] != 'ready': raise ValueError('請先完成新的預覽。')
            if reviewed is not True: raise ValueError('請先確認此次複製內容與目的位置。')
            folder = self.folder(token)
            request = json.loads((folder/'request.json').read_text())
            if request['mode']=='export':
                current = self.engine.get(request['id'])
                if current != request['state']: raise ValueError('專案設定或狀態已改變，請重新預覽。')
            handle = self._reserve(token)
            try:
                job.pop('preview',None); job.pop('progress',None)
                job.update(status='running', stage='複製並以 SHA-256 核對每個檔案')
                if request['mode']=='import': job['project_id']=uuid.uuid4().hex[:12]
                atomic_json(folder/'job.json',job)
                threading.Thread(target=self._worker,args=(token,'execute',handle),daemon=True).start()
            except Exception:
                handle.close(); self.processes.pop(token,None); self.engine.active=None; raise
        return self.get(token)

    def stop(self, token):
        self.folder(token)
        with self.lock:
            if token in self.processes:
                self.cancelled.add(token)
                process = self.processes[token]
                if process and process.poll() is None:
                    try: os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
        return self.get(token)

    def shutdown(self):
        with self.lock: tokens = list(self.processes)
        for token in tokens: self.stop(token)
        deadline=time.monotonic()+4
        while self.processes and time.monotonic()<deadline: time.sleep(.05)
        with self.lock:
            for process in self.processes.values():
                if process and process.poll() is None:
                    try: os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError: pass

    def _register_verified(self, state):
        # Child supplied fully verified state and volume binding; registration
        # only writes local bookmarks, never rereads a possibly disconnected NAS.
        binding = state.pop('_portable_binding', None)
        key, root = state['id'], state['config']['root']
        with self.engine.registry_lock:
            if key in self.engine.projects or root in self.engine.projects.values():
                raise ValueError('目的專案已登記，請重新整理清單。')
            entry = dict(cached_name=state['config']['name'], workflow='alosStack', stage=state['stage'],
                         last_status=state['status'], root_binding=binding, inputs={})
            for field in ('source','data'):
                value=state['config'].get(field)
                if value:
                    sub=dict(binding,relative=str(Path(value).relative_to(binding['mount']))) if binding else None
                    entry['inputs'][field]=dict(path=value,binding=sub)
            self.engine.storage._store(key,entry)
            projects={**self.engine.projects,key:root}
            atomic_json(self.engine.registry,projects); self.engine.projects=projects

    def _worker(self, token, phase, handle):
        folder=self.folder(token); process=None; reader=writer=None
        try:
            reader,writer=os.pipe()
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(Path(__file__).parent.parent))
            with (folder/'worker.log').open('a') as log:
                process=subprocess.Popen([sys.executable,'-m','alosstack_helper.portable_worker',phase,str(folder),str(reader),str(handle.fileno())],
                    env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(reader,handle.fileno()))
                os.close(reader);reader=None
                with self.lock:
                    self.processes[token]=process
                    if token in self.cancelled: os.killpg(process.pid,signal.SIGTERM)
                code=process.wait()
            if token in self.cancelled: raise ValueError('已停止；原始資料與未完成副本均保留，未自動加入工作台。')
            result=json.loads((folder/(phase+'-result.json')).read_text()) if (folder/(phase+'-result.json')).exists() else {}
            if code or not result.get('ok'): raise ValueError(result.get('error') or '讀寫中斷；請確認磁碟連線，重新預覽並選擇新位置。')
            job=json.loads((folder/'job.json').read_text())
            with self.lock:
                if token in self.cancelled: raise ValueError('已停止；完成副本保留，未加入工作台。')
                if phase=='preview': job.update(status='ready',stage='預覽完成，等待確認')
                else:
                    if job['mode']=='import':
                        state=json.loads((folder/'project.json').read_text())
                        self._register_verified(state)
                    job.update(status='complete',stage='可攜分析包已完成' if job['mode']=='export' else '已建立獨立專案，可開始品質檢查')
        except Exception as error:
            if process and process.poll() is None:
                try: os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=3)
                except (ProcessLookupError,subprocess.TimeoutExpired): pass
            job=json.loads((folder/'job.json').read_text())
            job.update(status='stopped' if token in self.cancelled else 'failed',stage='工作已停止，需要檢查',error=str(error))
        finally:
            for fd in (reader,writer):
                if fd is not None: os.close(fd)
            try:
                with self.lock: atomic_json(folder/'job.json',job)
            finally:
                with self.lock:
                    self.processes.pop(token,None);self.cancelled.discard(token)
                    # Inherited fd keeps this lock alive even after a server crash.
                    handle.close()
                with self.engine.lock:
                    if self.engine.active=='portable:'+token:self.engine.active=None
