"""Child process for large portable-package copies and full verification."""
from pathlib import Path
import json
import os
import signal
import sys
import threading
import time

from .core import atomic_json
from .portable import plan_export, export_package, inspect_package, import_package


def main():
    phase,folder,lifetime,guard=sys.argv[1:];p=Path(folder)
    # guard is an inherited flock descriptor, deliberately held until exit.
    os.fstat(int(guard))
    def watch():
        try: os.read(int(lifetime),1)
        finally: os.kill(os.getpid(),signal.SIGTERM)
    threading.Thread(target=watch,daemon=True).start()
    last=0; pending={}
    def progress(value):
        nonlocal last, pending
        pending.update(value)
        if time.monotonic()-last>.25:
            previous=json.loads((p/'progress.json').read_text()) if (p/'progress.json').exists() else {}
            atomic_json(p/'progress.json',{**previous,**pending});last=time.monotonic()
    try:
        request=json.loads((p/'request.json').read_text());mode=request['mode']
        destination=Path(request['destination']); parent=destination.parent.resolve(strict=True)
        target=parent/destination.name; registry=Path(request['registry']).resolve()
        for protected in (registry,Path(request['managed_home']).resolve()):
            if target.is_relative_to(protected) or protected.is_relative_to(target):
                raise ValueError('資料包或新專案不能與工作台紀錄或安裝目錄重疊。')
        if phase=='preview':
            preview=plan_export(request['state'],request['destination'],request.get('environment')) if mode=='export' else inspect_package(request)
            atomic_json(p/'preview.json',preview)
        elif phase=='execute':
            preview=json.loads((p/'preview.json').read_text())
            if mode=='export': export_package(request['state'],preview,progress=progress)
            else:
                job=json.loads((p/'job.json').read_text())
                state=import_package(request,preview,job['project_id'],progress=progress)
                from .storage import ProjectStorage
                state['_portable_binding']=ProjectStorage(request['registry']).binding(state['config']['root'])
                atomic_json(p/'project.json',state)
        else: raise ValueError('未知的可攜包階段。')
        if pending: atomic_json(p/'progress.json',pending)
        atomic_json(p/(phase+'-result.json'),{'ok':True})
    except Exception as error:
        atomic_json(p/(phase+'-result.json'),{'ok':False,'error':str(error)})
        raise


if __name__=='__main__':main()
