"""Cancellable, metadata-only storage inventories. Never mutate research files.

The scanner lives in a separate process: a stalled NAS stat cannot hold the
server or Stop button. A lifetime pipe makes it exit if its owning server dies.
Only the local registry receives progress, including when media disappears.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time

from .core import atomic_json
from .storage import absolute, INPUT_PATHS, mounted_volumes, read_project

CATEGORIES = dict(raw='原始／整理影像', processing='ISCE2 處理成果', mintpy='MintPy 分析與匯出',
                  recovery='恢復副本', cache='地圖與預覽快取', terrain='DEM／水體資料',
                  metadata='工作紀錄與設定', other='其他專案檔案')
ACTIVE = {'checking', 'running'}


def empty_counts():
    return dict(bytes=0, allocated=0, files=0, directories=0, links=0, hardlinks=0, special=0,
                allocation_unknown=0)


def category(path, configured=()):
    parts = path.parts
    if parts[:2] == ('.helper', 'recovery'): return 'recovery'
    if (parts[:2] in (('analyses', '.map-cache'), ('.helper', 'cache')) or
            len(parts) >= 3 and parts[0] == 'analyses' and parts[2] == 'cache'): return 'cache'
    if parts and parts[0] == 'proc': return 'processing'
    if parts and parts[0] == 'analyses': return 'mintpy'
    if parts and parts[0] == '.helper': return 'metadata'
    for prefix, kind in configured:
        if path.is_relative_to(prefix): return kind
    if parts and parts[0] == 'data': return 'raw'
    if parts and parts[0] in ('dem', 'wbd'): return 'terrain'
    return 'other'


class Inventory:
    def __init__(self, request, publish):
        self.request = request
        self.publish = publish
        self.root = absolute(request['root'])
        self.last_update = 0
        self.seen = set()
        self.devices = set()
        self.report = dict(id=request['id'], root=str(self.root), status='checking', started=time.time(),
                           updated=time.time(), finished=None, include_external=request['include_external'],
                           current='檢查專案與磁碟身分', project=empty_counts(), external=empty_counts(),
                           categories={k: dict(label=v, **empty_counts()) for k, v in CATEGORIES.items()},
                           sources=[], disks=[], errors=[], error_count=0, boundaries=0, visited=0)

    def update(self, force=False):
        if force or time.monotonic() - self.last_update >= .4:
            self.report['updated'] = time.time()
            self.publish(copy.deepcopy(self.report))
            self.last_update = time.monotonic()

    def error(self, path, message):
        self.report['error_count'] += 1
        if len(self.report['errors']) < 20:
            self.report['errors'].append(dict(path=str(path), message=str(message)))

    def check_binding(self, binding):
        if not binding: return
        matches = [v for v in self.mounts if v['mount'] == binding['mount']]
        if not matches: raise OSError('儲存裝置未連線。')
        if binding.get('identity') and matches[0].get('identity') != binding['identity']:
            raise OSError('原位置的磁碟身分已改變。')

    def disk(self, path):
        info = path.stat()
        if info.st_dev in self.devices: return
        usage = os.statvfs(path)
        self.devices.add(info.st_dev)
        mounts = [v for v in self.mounts if path.is_relative_to(absolute(v['mount']))]
        volume = max(mounts, key=lambda v: len(v['mount'])) if mounts else {}
        self.report['disks'].append(dict(path=volume.get('mount', str(path)), label=volume.get('label', '專案所在磁碟'),
                                         total=usage.f_blocks*usage.f_frsize, free=usage.f_bavail*usage.f_frsize,
                                         checked=time.time()))

    def walk(self, start, group, configured=(), exclude=()):
        """Visit only this tree, not symlink targets or nested mounted filesystems."""
        initial = start.lstat()
        pending = [start]
        while pending:
            path = pending.pop()
            if any(path == p or path.is_relative_to(p) for p in exclude): continue
            self.report['current'] = str(path)
            self.report['visited'] += 1
            self.update()
            try:
                info = path.lstat()
                if info.st_dev != initial.st_dev:
                    self.report['boundaries'] += 1
                    self.error(path, '此子目錄位於另一個磁碟，未跨磁碟統計。')
                    continue
                buckets = [group]
                if group is self.report['project']:
                    buckets.append(self.report['categories'][category(path.relative_to(start), configured)])
                else:
                    buckets.append(self.report['external'])
                if stat.S_ISLNK(info.st_mode):
                    for row in buckets: row['links'] += 1
                elif stat.S_ISDIR(info.st_mode):
                    for row in buckets: row['directories'] += 1
                    # scandir does not load file contents or materialize a tree.
                    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        opened = os.fstat(fd)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise OSError('讀取時資料夾已被替換，請重新統計。')
                        with os.scandir(fd) as entries:
                            for entry in entries:
                                child = path / entry.name
                                try:
                                    child_info = entry.stat(follow_symlinks=False)
                                    if child_info.st_dev != initial.st_dev:
                                        self.report['boundaries'] += 1
                                        self.error(child, '此項目位於另一個磁碟，未跨磁碟統計。')
                                    elif stat.S_ISDIR(child_info.st_mode):
                                        pending.append(child)
                                    else:
                                        self.file(child, child_info, group, start, configured)
                                except OSError as error: self.error(child, error)
                                self.update()
                    finally: os.close(fd)
                else:
                    self.file(path, info, group, start, configured)
            except OSError as error: self.error(path, error)
        # Do not call a removed/replaced root a successful inventory.
        after = start.lstat()
        if (after.st_dev, after.st_ino) != (initial.st_dev, initial.st_ino):
            raise OSError('統計期間資料位置已改變，請重新連線後再統計。')

    def file(self, path, info, group, start, configured):
        buckets = [group]
        if group is self.report['project']:
            buckets.append(self.report['categories'][category(path.relative_to(start), configured)])
        else:
            buckets.append(self.report['external'])
        self.report['visited'] += 1
        self.report['current'] = str(path)
        if stat.S_ISLNK(info.st_mode):
            for row in buckets: row['links'] += 1
            return
        if not stat.S_ISREG(info.st_mode):
            for row in buckets: row['special'] += 1
            return
        identity = (info.st_dev, info.st_ino)
        duplicate = info.st_nlink > 1 and identity in self.seen
        if info.st_nlink > 1: self.seen.add(identity)
        for row in buckets:
            row['files'] += 1
            if duplicate:
                row['hardlinks'] += 1
            else:
                row['bytes'] += info.st_size
                if hasattr(info, 'st_blocks'): row['allocated'] += info.st_blocks * 512
                else: row['allocation_unknown'] += 1

    def run(self):
        self.update(True)
        try:
            self.mounts = mounted_volumes()
            self.check_binding(self.request.get('binding'))
            state = read_project(self.root, self.request['id'])
            self.root = self.root.resolve(strict=True)
            configured = []
            sources = []
            for field, label in INPUT_PATHS.items():
                value = state['config'].get(field)
                if not isinstance(value, str) or not value: continue
                path = absolute(value)
                # Resolve only explicitly configured roots, never links encountered
                # below them. This also identifies symlink-only imported data.
                row = dict(field=field, label=label, path=value, status='not_scanned', **empty_counts())
                self.report['sources'].append(row)
                if (not self.request['include_external'] and not path.is_relative_to(self.root)
                        and not path.is_relative_to(absolute(self.request['root']))): continue
                try:
                    self.check_binding(self.request.get('inputs', {}).get(field, {}).get('binding'))
                    canonical = path.resolve(strict=True)
                    if canonical.is_relative_to(self.root):
                        if canonical != self.root:
                            configured.append((canonical.relative_to(self.root), 'raw' if field in ('source', 'data', 'reference_safe', 'secondary_safe') else 'terrain'))
                        row.update(status='in_project'); continue
                    if not self.request['include_external']: continue
                    if canonical in (Path('/'), Path.home()):
                        raise ValueError('來源範圍過大，請選擇影像專用資料夾。')
                    sources.append((canonical, row))
                except (OSError, ValueError) as error:
                    row.update(status='unavailable', reason=str(error))
                    if self.request['include_external']: self.error(path, error)
            configured.sort(key=lambda item: len(item[0].parts), reverse=True)
            self.report['status'] = 'running'
            self.disk(self.root)
            self.walk(self.root, self.report['project'], configured)
            covered = []
            for path, row in sorted(sources, key=lambda item: (len(item[0].parts), str(item[0]))):
                parent = next((p for p in covered if path.is_relative_to(p)), None)
                if parent is not None:
                    row.update(status='shared', counted_under=str(parent)); continue
                try:
                    self.disk(path)
                    errors = self.report['error_count']
                    self.walk(path, row, exclude=(self.root,))
                    row['status'] = 'partial' if self.report['error_count'] > errors else 'complete'
                except OSError as error:
                    row.update(status='partial', reason=str(error)); self.error(path, error)
                covered.append(path)
            self.mounts = mounted_volumes()
            self.check_binding(self.request.get('binding'))
            read_project(self.request['root'], self.request['id'])
            self.report['status'] = 'partial' if self.report['error_count'] else 'complete'
        except (OSError, ValueError, KeyError) as error:
            self.error(self.root, error)
            self.report['status'] = 'partial' if self.report['visited'] else 'failed'
        self.report.update(finished=time.time(), current='')
        self.update(True)
        return self.report


class StorageUsage:
    """One low-priority scanner per server. All status reads are local only."""
    def __init__(self, engine):
        self.engine = engine
        self.folder = engine.home / 'storage-usage'
        self.folder.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.lifetime = None
        for path in self.folder.glob('*.json'):
            if path.name.endswith('.request.json'): continue
            try:
                record = json.loads(path.read_text())
                if record.get('status') in ACTIVE:
                    record.update(status='interrupted', finished=time.time(), current='')
                    atomic_json(path, record)
            except (OSError, ValueError): pass

    def path(self, key):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', key) or key not in self.engine.projects:
            raise ValueError('找不到這個專案。')
        return self.folder / (key + '.json')

    def _record(self, key):
        path = self.folder / (key + '.json')
        return json.loads(path.read_text()) if path.exists() else None

    def reconcile(self):
        if self.process and self.process.poll() is not None:
            record = self._record(self.active)
            if record and record['status'] in ACTIVE:
                record.update(status='failed', finished=time.time(), current='', reason='統計程序中斷，請重新統計。')
                atomic_json(self.folder / (self.active + '.json'), record)
            self.process = None; self.active = None
            if self.lifetime is not None: os.close(self.lifetime); self.lifetime = None

    def get(self, key):
        with self.lock:
            self.path(key); self.reconcile()
            record = self._record(key)
            previous_path = self.folder / (key + '.last.json')
            previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
            return dict(report=record, previous=previous, busy=self.active,
                        location_changed=bool(record and absolute(record['root']) != absolute(self.engine.projects[key])))

    def start(self, key, include_external=False):
        if type(include_external) is not bool: raise ValueError('請選擇是否統計外部來源。')
        with self.lock:
            output = self.path(key); self.reconcile()
            if self.active: raise ValueError('已有空間統計進行中，請完成或停止後再開始。')
            entry = self.engine.storage.entry(key)
            request = dict(id=key, root=self.engine.projects[key], include_external=include_external,
                           binding=entry.get('root_binding'), inputs=entry.get('inputs', {}))
            request_path = self.folder / (key + '.request.json')
            atomic_json(request_path, request)
            previous = self._record(key)
            if previous and previous.get('status') == 'complete':
                atomic_json(self.folder / (key + '.last.json'), previous)
            atomic_json(output, Inventory(request, lambda _: None).report)
            reader, writer = os.pipe()
            try:
                # Explicit source path works for both installed and shipped apps.
                env = os.environ.copy(); env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
                self.process = subprocess.Popen([sys.executable, '-m', 'alosstack_helper.storage_usage',
                                                 str(request_path), str(output), str(reader)],
                                                env=env, pass_fds=(reader,), start_new_session=True,
                                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.active = key; self.lifetime = writer
            except Exception:
                os.close(writer)
                record = self._record(key); record.update(status='failed', finished=time.time(), reason='無法啟動空間統計。')
                atomic_json(output, record)
                raise
            finally: os.close(reader)
            return self.get(key)

    def stop(self, key=None, shutdown=False):
        with self.lock:
            self.reconcile()
            if not self.process: return
            if key is not None and key != self.active: raise ValueError('這個專案沒有進行中的空間統計。')
            process = self.process
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                # Uninterruptible filesystem I/O can delay OS cleanup. Never
                # block the server waiting for it or start a second scanner.
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired: pass
            record = self._record(self.active)
            record.update(status='interrupted' if shutdown else 'cancelled', finished=time.time(), current='')
            if process.poll() is None:
                record['reason'] = '已要求停止；作業系統仍在處理磁碟讀取，結束後才可重新統計。'
            atomic_json(self.folder / (self.active + '.json'), record)
            self.reconcile()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('request'); parser.add_argument('output'); parser.add_argument('parent_fd', type=int)
    args = parser.parse_args()
    def parent_watch():
        try: os.read(args.parent_fd, 1)
        finally: os._exit(1)
    threading.Thread(target=parent_watch, daemon=True).start()
    try: os.nice(5)
    except OSError: pass
    Inventory(json.loads(Path(args.request).read_text()), lambda row: atomic_json(Path(args.output), row)).run()


if __name__ == '__main__': main()
