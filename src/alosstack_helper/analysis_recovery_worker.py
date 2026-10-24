"""Rebase only path attributes in a freshly copied MintPy recovery version."""
from pathlib import Path
import hashlib
import json
import sys
import h5py


def rebase(folder, previous, published=None):
    folder = Path(folder)
    replacement=str(published or folder)
    for path in folder.rglob('*.h5'):
        if path.is_symlink():raise ValueError('恢復版本的 HDF5 不可是外部連結。')
        with h5py.File(path, 'r+') as f:
            def attrs(_, obj):
                for key in list(obj.attrs):
                    value = obj.attrs[key]
                    if isinstance(value, str) and previous in value:
                        obj.attrs.modify(key, value.replace(previous, replacement))
            attrs('', f); f.visititems(attrs)
    path = folder/'analysis.json'; job = json.loads(path.read_text())
    if job.get('parameter_report') and (folder/'mintpy.cfg').is_file():
        job['parameter_report']['template_sha256'] = hashlib.sha256((folder/'mintpy.cfg').read_bytes()).hexdigest()
        (folder/'parameter-report.json').write_text(json.dumps(job['parameter_report'], ensure_ascii=False, indent=2))
    path.write_text(json.dumps(job, ensure_ascii=False, indent=2))


if __name__ == '__main__':rebase(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv)>3 else None)
