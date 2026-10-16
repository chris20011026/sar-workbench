"""Isolated metadata worker boundary (web server remains standard-library only)."""
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from .core import find_conda
from .environment import isolated_env


def enrich(plan, environment=None, allow_cross_sensor=False):
    plan['metadata_environment'] = str(environment or '')
    if not environment:
        plan['metadata'] = dict(products=[],groups=[],errors=[],warnings=['尚無 ISCE2 環境，目前只有檔名清單。安裝工具後重新檢查，或自行確認來源。'],complete=False,bounds=None)
        return plan
    prefix = Path(environment)
    conda = find_conda(prefix)
    if not conda or not (prefix/'bin/python').is_file():
        raise ValueError('找不到選定環境的 Python／Conda，請重新選擇。')
    with tempfile.TemporaryDirectory(prefix='sar-metadata-') as directory:
        folder = Path(directory)
        request, response = folder/'request.json', folder/'response.json'
        request.write_text(json.dumps(dict(entries=plan['entries'], allow_cross_sensor=allow_cross_sensor)))
        with (folder/'worker.log').open('w+') as log:
            proc = subprocess.Popen([conda,'run','--no-capture-output','-p',str(prefix),'python',
                                     str(Path(__file__).with_name('metadata_worker.py')),str(request),str(response)],
                                    env=isolated_env(folder/'cache'),cwd=folder,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                if proc.wait(timeout=90):
                    log.seek(0)
                    raise ValueError('影像辨識未完成：'+log.read()[-1600:])
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL);proc.wait()
                raise ValueError('影像辨識逾時；請分批選取，並確認 NAS 連線。') from None
        result = json.loads(response.read_text())
    result['environment'] = str(prefix)
    plan['metadata'] = result
    plan['problems'] += result['errors']
    plan['warnings'] += result['warnings']
    plan['can_import'] = plan['can_import'] and not result['errors']
    return plan
