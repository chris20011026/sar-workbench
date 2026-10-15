"""Process boundary for slow/offline external disks during legacy adoption."""
from pathlib import Path
import json
import os
import signal
import sys
import threading

from .core import atomic_json
from .legacy import create_wrapper, inspect_legacy


def main():
    mode, folder, lifetime = sys.argv[1:]; p = Path(folder)
    def parent_lifetime():
        try: os.read(int(lifetime), 1)
        finally: os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=parent_lifetime, daemon=True).start()
    try:
        request = json.loads((p / 'request.json').read_text())
        if mode == 'preview': atomic_json(p / 'preview.json', inspect_legacy(request))
        elif mode == 'create':
            preview = json.loads((p / 'preview.json').read_text()); job = json.loads((p / 'job.json').read_text())
            atomic_json(p / 'project.json', create_wrapper(request, preview, job['project_id']))
        else: raise ValueError('未知的匯入階段。')
        atomic_json(p / 'result.json', {'ok':True})
    except Exception as error:
        atomic_json(p / 'result.json', {'ok':False, 'error':str(error)})
        raise


if __name__ == '__main__': main()
