"""Small, local project bookmarks and read-only mounted-volume identification.

Never search image trees, mount disks, rewrite processing files, or infer identity
from a volume label. Paths are retained lexically because resolving a symlink can
erase the mount point needed when an external volume disappears.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import threading
import time
import uuid

from .core import atomic_json

INPUT_PATHS = dict(source='原始資料', data='處理用資料', dem='對位 DEM', dem_geo='地理編碼 DEM',
                   wbd='水體遮罩', reference_safe='參考影像', secondary_safe='比較影像',
                   reference_orbit='參考軌道', secondary_orbit='比較軌道')
WORKFLOWS = {'alosStack', 'alos2App', 'topsApp'}


def absolute(path):
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def read_project(root, expected=None):
    root = absolute(root)
    path = root / '.helper/state.json'
    if path.is_symlink() or (root / '.helper').is_symlink():
        raise ValueError('專案狀態檔不能是符號連結。')
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError('專案狀態檔過大，請先檢查內容。')
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or not isinstance(state.get('config'), dict):
        raise ValueError('不是有效的工作台專案狀態。')
    key = state.get('id')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key):
        raise ValueError('專案代號不合法。')
    if expected is not None and key != expected:
        raise ValueError('此位置是另一個專案，未自動連結。')
    original = state['config'].get('root')
    if not isinstance(original, str) or not Path(original).is_absolute() or not isinstance(state.get('status'), str):
        raise ValueError('專案缺少原始位置或處理狀態。')
    if not isinstance(state['config'].get('workflow', 'alosStack'), str) or not isinstance(state.get('history', []), list):
        raise ValueError('專案流程或執行紀錄格式不正確。')
    state.setdefault('history', [])
    return state


def atomic_existing_json(path, value):
    """Write through an already-open directory; never recreate an unplugged mount."""
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    temporary = '.' + path.name + '.' + uuid.uuid4().hex + '.tmp'
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try: os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError: pass
        os.close(directory)


def network_identity(source, filesystem):
    # Mount output can contain a login name. Persist only a hash, never credentials.
    share = source.rsplit('@', 1)[-1].lstrip('/')
    return 'share:' + hashlib.sha256((filesystem + ':' + share).encode()).hexdigest()


def unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mounted_volumes():
    rows = []
    if sys.platform == 'darwin':
        result = subprocess.run(['/sbin/mount'], capture_output=True, text=True, timeout=3, check=True)
        for line in result.stdout.splitlines():
            match = re.fullmatch(r'(.+) on (.+) \((.+)\)', line)
            if not match: continue
            source, mount, flags = match.groups(); mount = unescape_mount(mount)
            fs = flags.split(',')[0]
            if mount == '/' or mount.startswith(('/System/', '/dev')) or fs in ('autofs', 'devfs'): continue
            row = dict(mount=mount, label=Path(mount).name, identity=None, kind='disk', filesystem=fs)
            if fs in ('smbfs', 'nfs', 'afpfs', 'webdav'):
                row.update(identity=network_identity(source, fs), kind='network')
            else:
                try:
                    info = subprocess.run(['/usr/sbin/diskutil', 'info', '-plist', mount], capture_output=True, timeout=2, check=True)
                    data = plistlib.loads(info.stdout)
                    if data.get('VolumeUUID'): row['identity'] = 'uuid:' + str(data['VolumeUUID']).lower()
                    row['label'] = data.get('VolumeName') or row['label']
                except (OSError, ValueError, subprocess.SubprocessError): pass
            rows.append(row)
    elif sys.platform.startswith('linux'):
        uuids = {}
        for path in Path('/dev/disk/by-uuid').glob('*'):
            try: uuids[str(path.resolve())] = 'uuid:' + path.name.lower()
            except OSError: pass
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            before, after = line.split(' - ', 1); fields = before.split(); fs, source, *_ = after.split()
            mount = unescape_mount(fields[4]); source = unescape_mount(source)
            if not mount.startswith(('/media/', '/mnt/', '/run/media/')): continue
            identity = network_identity(source, fs) if fs in ('cifs', 'nfs', 'nfs4') else uuids.get(os.path.realpath(source))
            rows.append(dict(mount=mount, label=Path(mount).name, identity=identity,
                             kind='network' if fs in ('cifs', 'nfs', 'nfs4') else 'disk', filesystem=fs))
    return rows


class ProjectStorage:
    def __init__(self, home):
        self.path = Path(home) / 'project-details.json'
        self.lock = threading.RLock()
        self.entries = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.volumes = []; self.checked = 0; self.volume_error = ''

    def mounts(self, force=False):
        with self.lock:
            if force or time.monotonic() - self.checked > 5:
                try:
                    self.volumes = mounted_volumes(); self.volume_error = ''
                except (OSError, ValueError, subprocess.SubprocessError):
                    self.volume_error = '暫時無法讀取磁碟清單；已保留原登記。'
                self.checked = time.monotonic()
            return copy.deepcopy(self.volumes)

    def binding(self, path):
        path = absolute(path)
        matches = [v for v in self.mounts() if path.is_relative_to(absolute(v['mount']))]
        if matches:
            volume = max(matches, key=lambda v: len(v['mount']))
            return dict(volume, relative=str(path.relative_to(absolute(volume['mount']))))
        # A disconnected /Volumes path must not be mistaken for internal storage.
        if sys.platform == 'darwin' and len(path.parts) > 2 and path.parts[1] == 'Volumes':
            mount = Path('/Volumes') / path.parts[2]
            return dict(mount=str(mount), label=mount.name, identity=None, kind='disk', relative=str(path.relative_to(mount)))
        return None

    def entry(self, key):
        with self.lock: return copy.deepcopy(self.entries.get(key, {}))

    def _store(self, key, entry):
        with self.lock:
            if self.entries.get(key) == entry: return
            entries = {**self.entries, key: entry}
            atomic_json(self.path, entries); self.entries = entries

    def remember(self, key, root, state):
        with self.lock: self._remember(key, root, state)

    def _remember(self, key, root, state):
        entry = self.entry(key)
        entry.update(cached_name=state['config'].get('name') or Path(root).name,
                     workflow=state['config'].get('workflow', 'alosStack'),
                     stage=state.get('stage', ''), last_status=state['status'])
        # Explicit relinking resets the root binding. Input bindings retain the
        # last known disk identity even while that input disk is disconnected.
        entry['root_binding'] = self.binding(root)
        inputs = entry.setdefault('inputs', {})
        for field in INPUT_PATHS:
            value = state['config'].get(field)
            if not value or not isinstance(value, str): continue
            current = self.binding(value)
            if field not in inputs or inputs[field]['path'] != value or (not (inputs[field].get('binding') or {}).get('identity') and (current or {}).get('identity')):
                inputs[field] = dict(path=value, binding=self.binding(value))
        self._store(key, entry)

    def edit(self, key, name, notes):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError('專案名稱請填 1～100 字。')
        if not isinstance(notes, str) or len(notes) > 2000:
            raise ValueError('備註請限制在 2,000 字內。')
        with self.lock:
            entry = self.entry(key); entry.update(display_name=name.strip(), notes=notes.strip())
            self._store(key, entry)

    def volume_problem(self, binding):
        if not binding: return ''
        if self.volume_error: return 'unknown'
        mounts = [v for v in self.mounts() if v['mount'] == binding['mount']]
        if not mounts: return 'offline'
        if binding.get('identity') and mounts[0].get('identity') != binding['identity']: return 'wrong_disk'
        return ''

    def locate(self, key, root):
        entry = self.entry(key); self.mounts()
        binding = entry.get('root_binding') or self.binding(root)
        report = dict(id=key, root=str(root), name=entry.get('display_name') or entry.get('cached_name') or Path(root).name,
                      notes=entry.get('notes', ''), volume=binding, candidates=[], status='unavailable',
                      workflow=entry.get('workflow', 'alosStack'), last_stage=entry.get('stage', ''))
        problem = self.volume_problem(binding)
        try:
            if problem: raise OSError(problem)
            state = read_project(root, key)
            self.remember(key, root, state)
            report.update(status='online', reason='', state=state)
            report['name'] = entry.get('display_name') or state['config'].get('name') or Path(root).name
            return report
        except (OSError, ValueError) as error:
            reason = '專案狀態無法讀取；請確認資料夾是否已移動或刪除。' if isinstance(error, OSError) else str(error)
        if binding and binding.get('identity') and not self.volume_error:
            for volume in self.mounts():
                if volume.get('identity') != binding['identity']: continue
                candidate = absolute(volume['mount']) / binding['relative']
                try: read_project(candidate, key)
                except (OSError, ValueError): continue
                if str(candidate) not in report['candidates']: report['candidates'].append(str(candidate))
        if report['candidates']:
            report.update(status='relocated', reason='已找到同一磁碟上的專案。掛載位置已變更，請確認重新連結。' if len(report['candidates'])==1 else '找到多份相同代號的專案，請選擇要使用的位置。')
        elif problem == 'offline':
            report.update(status='offline', reason='儲存裝置尚未連線。接回後會自動重新檢查；不需要重新建立專案。')
        elif problem == 'wrong_disk':
            report.update(status='wrong_disk', reason='原位置目前是另一個磁碟，未自動開啟同名資料夾。請接回原磁碟或選擇專案位置。')
        else:
            report['reason'] = self.volume_error or reason
        return report

    def path_report(self, key, state):
        entry = self.entry(key); rows = []
        for field, label in INPUT_PATHS.items():
            value = state['config'].get(field)
            if not value or not isinstance(value, str): continue
            binding = entry.get('inputs', {}).get(field, {}).get('binding')
            problem = self.volume_problem(binding)
            exists = not problem and Path(value).exists()
            candidates = []
            if not exists and binding and binding.get('identity'):
                for volume in self.mounts():
                    if volume.get('identity') == binding['identity']:
                        candidate = absolute(volume['mount']) / binding['relative']
                        if candidate.exists(): candidates.append(str(candidate))
            rows.append(dict(field=field, label=label, path=value, status='online' if exists else problem or 'missing', candidates=candidates))
        return rows
