"""Explicit, cached, read-only validation of an existing science environment."""
from pathlib import Path
import hashlib
import json
import os
import signal
import subprocess
import tempfile
import threading
import time

from .core import atomic_json, find_conda
from .environment import isolated_env

CHECK_VERSION = 2


def signature(prefix):
    p=Path(prefix)
    files=[p/'bin/python', *sorted((p/'conda-meta').glob('*.json'))]
    files.extend(p.glob('lib/python*/site-packages/isce*/__init__.py'))
    files.extend(p.glob('lib/python*/site-packages/nisar/workflows/insar.py'))
    rows=[]
    for f in files:
        try:
            s=f.stat(); rows.append((str(f),str(f.resolve()),s.st_size,s.st_mtime_ns))
        except OSError: rows.append((str(f),'missing'))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


class EnvironmentChecks:
    def __init__(self, home):
        self.home=Path(home)/'environment-checks'
        self.lock=threading.Lock()

    def path(self, prefix):
        return self.home/(hashlib.sha256(str(Path(prefix).resolve()).encode()).hexdigest()+'.json')

    def catalogue(self, candidates, managed=None):
        rows=[]
        for candidate in candidates:
            capabilities=[k for k in ('isce','mintpy','isce3') if candidate.get(k)]
            if not capabilities: continue
            row=dict(candidate, capabilities=capabilities, managed=str(candidate['path'])==str(managed), validation='detected', report=None)
            try:
                report=json.loads(self.path(candidate['path']).read_text())
                if report.get('check_version')==CHECK_VERSION and report['signature']==signature(candidate['path']):
                    row.update(validation='verified' if report['ok'] else 'failed',report=report)
                else: row['validation']='changed'
            except (OSError,ValueError,KeyError): pass
            rows.append(row)
        return rows

    def run(self, prefix, candidates):
        prefix=str(Path(prefix).expanduser().resolve())
        selected=next((e for e in candidates if str(Path(e['path']).resolve())==prefix),None)
        if not selected: raise ValueError('請先重新偵測環境，再選擇清單中的環境。')
        capabilities=[k for k in ('isce','mintpy','isce3') if selected.get(k)]
        conda=find_conda(prefix)
        if not conda or not capabilities: raise ValueError('此環境沒有已辨識的科學工具或 Conda。')
        if not self.lock.acquire(blocking=False): raise ValueError('另一個環境正在驗證，請稍候。')
        try:
            before=signature(prefix)
            with tempfile.TemporaryDirectory(prefix='sar-environment-check-') as d:
                folder=Path(d); result=folder/'result.json'
                args=[conda,'run','--no-capture-output','-p',prefix,'python',str(Path(__file__).with_name('environment_probe.py')),str(result),json.dumps(capabilities)]
                with (folder/'check.log').open('w+') as log:
                    proc=subprocess.Popen(args,cwd=folder,env=isolated_env(folder/'cache'),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    try: code=proc.wait(timeout=120)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                        raise ValueError('環境驗證逾時；未修改環境，請確認安裝與磁碟連線。') from None
                    if not result.is_file():
                        log.seek(0)
                        raise ValueError('無法啟動所選環境：'+log.read()[-1200:])
                    report=json.loads(result.read_text())
                if signature(prefix)!=before: raise ValueError('驗證期間環境有變動，請等待安裝結束後再驗證。')
                report.update(path=prefix,signature=before,check_version=CHECK_VERSION,checked_at=time.time(),ok=code==0 and report.get('ok') is True)
                atomic_json(self.path(prefix),report)
                return report
        finally:self.lock.release()
