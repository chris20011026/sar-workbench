"""Verified, resumable copies of completed projects; never remove the source.

Manifests and progress live on the local system disk. A separate, lifetime-bound
worker keeps slow/removable storage away from the HTTP server. Copies are private
regular files (no hardlinks/clones); interruption resumes at file boundaries.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import uuid

from .core import atomic_json
from .recovery import process_alive
from .storage import absolute, INPUT_PATHS, WORKFLOWS, ProjectStorage, mounted_volumes, read_project

ACTIVE = {'planning', 'copying', 'verifying', 'switch_check'}
MARKER = '.sar-transfer.json'
CHUNK = 4 * 1024**2
RESERVE = 64 * 1024**2


def identity(info):
    return [info.st_dev, info.st_ino]


def same_folder(info, expected, binding):
    # A re-mounted UUID-bearing volume can receive a new device number.
    # check_volume() must have succeeded before using this relaxed comparison.
    return identity(info) == expected or bool(len(expected)==2 and binding and binding.get('identity') and info.st_ino == expected[1])


def check_volume(binding):
    if not binding: return
    current = next((v for v in mounted_volumes() if v['mount'] == binding['mount']), None)
    if not current or binding.get('identity') and current.get('identity') != binding['identity']:
        raise ValueError('儲存裝置未連線或身分已改變；接回原磁碟後再接續。')


@contextlib.contextmanager
def directory(path, root_fd=None):
    """Open every path component without following links, anchored by directory fd."""
    path = Path(path)
    if root_fd is None:
        if not path.is_absolute(): raise ValueError('需要完整資料夾路徑。')
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        parts = path.parts[1:]
    else:
        if path.is_absolute() or '..' in path.parts: raise ValueError('無效的相對路徑。')
        fd = os.dup(root_fd); parts = path.parts
    try:
        for part in parts:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = new
        yield fd
    finally: os.close(fd)


def marker_at(root):
    with directory(root) as fd:
        try: handle = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError: return None
        with os.fdopen(handle) as stream:
            value = stream.read(65537)
        if len(value) > 65536: raise ValueError('搬移識別檔格式錯誤。')
        return json.loads(value)


def require_finished_copy(root):
    value = marker_at(Path(root).resolve(strict=True))
    if value is not None:
        if not isinstance(value,dict) or value.get('status') != 'verified':
            raise ValueError('這是尚未驗證完成的搬移副本，請由安全搬移精靈接續。')
        if value.get('id') != read_project(root)['id'] or not re.fullmatch(r'[a-f0-9]{32}',str(value.get('transfer',''))):
            raise ValueError('搬移識別檔與專案不符，請檢查。')


def completed(root, key):
    s = read_project(root, key)
    if s['status'] != 'complete' or s['config'].get('workflow', 'alosStack') not in WORKFLOWS:
        raise ValueError('第一版只支援運算已完成的 ISCE2 專案。')
    if process_alive(root/'.helper'): raise ValueError('專案仍有背景程序，請等工作結束。')
    for path in (root/'analyses').glob('*/analysis.json'):
        if path.is_symlink() or path.parent.is_symlink(): raise ValueError('分析紀錄不可是外部連結。')
        if json.loads(path.read_text()).get('status') == 'running': raise ValueError('請先完成或停止 MintPy 分析。')
    return s


def walk(root_fd):
    """Bounded directory traversal; entries never follow symbolic links or mounts."""
    device = os.fstat(root_fd).st_dev
    pending = [Path('.')]
    while pending:
        rel = pending.pop()
        with directory(rel, root_fd) as fd:
            with os.scandir(fd) as items:
                for entry in items:
                    if rel == Path('.') and entry.name == MARKER: continue
                    info = entry.stat(follow_symlinks=False)
                    if info.st_dev != device: raise ValueError('專案含另一個掛載磁碟，請先將它獨立管理。')
                    path = rel/entry.name
                    if stat.S_ISDIR(info.st_mode): kind = 'dir'; pending.append(path)
                    elif stat.S_ISREG(info.st_mode): kind = 'file'
                    elif stat.S_ISLNK(info.st_mode): kind = 'link'
                    else: raise ValueError('專案含不支援的特殊檔案：'+str(path))
                    yield dict(path=str(path), kind=kind, size=info.st_size, mtime=info.st_mtime_ns,
                               mode=stat.S_IMODE(info.st_mode), inode=info.st_ino, device=info.st_dev,
                               target=os.readlink(entry.name, dir_fd=fd) if kind == 'link' else None)


class Transfer:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder/'state.json'
        self.s = json.loads(self.path.read_text())
        self.root = Path(self.s['source']); self.dest = Path(self.s['destination'])
        self.last = 0
        self.db = sqlite3.connect(self.folder/'manifest.sqlite')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE IF NOT EXISTS entries (path TEXT PRIMARY KEY, kind TEXT, size INTEGER, mtime INTEGER, mode INTEGER, inode INTEGER, device INTEGER, target TEXT, mapped TEXT, sha TEXT)')

    def publish(self, force=False, **values):
        self.s.update(values)
        if force or time.monotonic()-self.last > .4:
            self.s['updated'] = time.time(); atomic_json(self.path, self.s); self.last = time.monotonic()

    def check(self):
        check_volume(self.s.get('source_binding')); check_volume(self.s.get('destination_binding'))
        with directory(self.root) as fd:
            if not same_folder(os.fstat(fd), self.s['source_identity'], self.s.get('source_binding')): raise ValueError('原專案資料夾已被替換，請重新預覽。')
        with directory(self.dest.parent) as fd:
            if not same_folder(os.fstat(fd), self.s['parent_identity'], self.s.get('destination_binding')): raise ValueError('目的資料夾已被替換，請重新預覽。')
        completed(self.root, self.s['id'])

    def plan(self):
        # Path resolution/stat can block on an offline NAS. Do it only in this
        # cancellable child, never while the server holds its manager lock.
        check_volume(self.s.get('source_binding'))
        self.root = self.root.resolve(strict=True)
        self.dest = self.dest.parent.resolve(strict=True)/self.dest.name
        home = self.folder.parents[1].resolve()
        if self.dest.is_relative_to(self.root) or self.root.is_relative_to(self.dest) or self.dest.is_relative_to(home):
            raise ValueError('新舊資料夾不能重疊，也不能放進工作台紀錄目錄。')
        storage = ProjectStorage(home)
        with directory(self.root) as fd: self.s['source_identity'] = identity(os.fstat(fd))
        with directory(self.dest.parent) as fd: self.s['parent_identity'] = identity(os.fstat(fd))
        self.publish(True,source=str(self.root),destination=str(self.dest),
                     source_binding=storage.binding(self.root),destination_binding=storage.binding(self.dest.parent))
        self.check(); require_finished_copy(self.root)
        with directory(self.dest.parent) as fd:
            try: os.stat(self.dest.name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise ValueError('目的資料夾已存在；請換一個新名稱，避免覆寫資料。')
        self.db.execute('DELETE FROM entries')
        total = files = links = internal = count = 0
        external = []
        with directory(self.root) as fd:
            for row in walk(fd):
                mapped = row['target']
                if row['kind'] == 'link':
                    links += 1
                    if Path(row['target']).is_absolute() and '..' in Path(row['target']).parts:
                        raise ValueError('絕對連結含不明確的上層路徑，請先檢查：'+row['path'])
                    target = absolute(self.root/Path(row['path']).parent/row['target'])
                    if target.is_relative_to(self.root):
                        internal += 1
                        mapped = row['target'] if not Path(row['target']).is_absolute() else os.path.relpath(self.dest/target.relative_to(self.root), self.dest/Path(row['path']).parent)
                    else:
                        # A relative external link would resolve differently at the destination.
                        mapped = str(self.root/Path(row['path']).parent/row['target'])
                        if len(external) < 50: external.append(dict(path=row['path'], target=str(target)))
                if row['kind'] == 'file': total += row['size']; files += 1
                self.db.execute('INSERT INTO entries VALUES (:path,:kind,:size,:mtime,:mode,:inode,:device,:target,:mapped,NULL)', dict(row, mapped=mapped))
                count += 1
                if count % 500 == 0: self.db.commit()
                self.publish(current=row['path'], files=files, bytes=total, links=links)
        self.db.commit()
        state = read_project(self.root, self.s['id'])
        dependencies = []
        for field, label in INPUT_PATHS.items():
            value = state['config'].get(field)
            if value and not absolute(value).is_relative_to(self.root):
                if self.dest.is_relative_to(absolute(value)) or absolute(value).is_relative_to(self.dest):
                    raise ValueError('目的地不能與外部影像或地形來源重疊。')
                dependencies.append(dict(label=label, path=value))
        # Never place a project inside its analysis/ISCE runtime or local registry.
        for value in [state['config'].get('env')] + [json.loads(p.read_text()).get('environment') for p in (self.root/'analyses').glob('*/analysis.json')]:
            if value and self.dest.is_relative_to(absolute(value)): raise ValueError('目的地不能位於運算環境內。')
        self.check()
        free = os.statvfs(self.dest.parent); free = free.f_bavail*free.f_frsize
        self.publish(True, status='preview', current='', files=files, bytes=total, links=links,
                     internal_links=internal, external_links=links-internal, external=external,
                     dependencies=dependencies, free=free, required=total+RESERVE,
                     space_ok=free >= total+RESERVE, copied=0, verified=0)

    def inventory_check(self, source=True):
        root = self.root if source else self.dest
        count = 0
        with directory(root) as fd:
            for row in walk(fd):
                previous = self.db.execute('SELECT * FROM entries WHERE path=?', (row['path'],)).fetchone()
                if not previous or row['kind'] != previous['kind']: raise ValueError('檔案清單已變更：'+row['path'])
                fields = ('size','mtime','inode','target') if (self.s.get('source_binding') or {}).get('identity') else ('size','mtime','inode','device','target')
                if source and any(row[k] != previous[k] for k in fields if row['kind'] != 'dir'):
                    raise ValueError('原檔案已變更，請保留目前副本並重新預覽：'+row['path'])
                if row['kind'] == 'link' and row['target'] != previous['target' if source else 'mapped']:
                    raise ValueError('連結內容已變更：'+row['path'])
                count += 1; self.publish(current='檢查清單 · '+row['path'])
        if count != self.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]: raise ValueError('檔案清單不完整，不能切換位置。')

    def dest_open(self, create=False):
        """Create only once; never reuse an unrelated or replaced directory."""
        with directory(self.dest.parent) as fd:
            if create and not self.s.get('destination_identity'):
                os.mkdir(self.dest.name, 0o700, dir_fd=fd)
                self.s['destination_identity'] = identity(os.stat(self.dest.name, dir_fd=fd, follow_symlinks=False))
                self.publish(True)
        with directory(self.dest) as fd:
            if not same_folder(os.fstat(fd), self.s.get('destination_identity',[]), self.s.get('destination_binding')): raise ValueError('搬移副本或目的磁碟已被替換。')
        mark = marker_at(self.dest)
        if mark and (mark.get('transfer') != self.s['transfer'] or mark.get('id') != self.s['id']): raise ValueError('搬移識別不符，不會更動這個資料夾。')
        return os.open(self.dest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def marker(self, fd, status):
        value = dict(transfer=self.s['transfer'], id=self.s['id'], source=str(self.root), status=status,
                     checked=time.time(), method='SHA-256', external_links=self.s.get('external_links', 0))
        handle = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        with os.fdopen(handle, 'w') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('搬移識別檔已被替換。')
            os.ftruncate(stream.fileno(), 0)
            json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())

    def hash_file(self, fd, row, source=False):
        with directory(Path(row['path']).parent, fd) as parent:
            handle = os.open(Path(row['path']).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(handle, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != row['size']: raise ValueError('檔案大小或類型改變：'+row['path'])
            if not source and before.st_nlink != 1: raise ValueError('副本不可與其他檔案共用硬連結：'+row['path'])
            if source and (before.st_ino != row['inode'] or before.st_mtime_ns != row['mtime']): raise ValueError('原檔案已變更：'+row['path'])
            digest = hashlib.sha256(); read = 0
            while block := stream.read(CHUNK):
                digest.update(block); read += len(block)
                self.publish(current=row['path'], file_bytes=read, file_size=row['size'])
            after = os.fstat(stream.fileno())
            if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns): raise ValueError('讀取期間檔案被更動：'+row['path'])
            return digest.hexdigest()

    def copy_file(self, source, dest, row):
        rel = Path(row['path'])
        with directory(rel.parent, source) as parent:
            handle = os.open(rel.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(handle, 'rb') as reader:
            before = os.fstat(reader.fileno())
            if not stat.S_ISREG(before.st_mode) or (before.st_size,before.st_mtime_ns,before.st_ino) != (row['size'],row['mtime'],row['inode']):
                raise ValueError('原檔案已變更：'+row['path'])
            # An interrupted file is recopied. O_EXCL works on APFS, exFAT and
            # NAS without requiring hardlink support or overwriting other data.
            with directory(rel.parent, dest) as parent:
                handle = os.open(rel.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with os.fdopen(handle, 'wb') as writer:
                self.publish(True, partial=dict(path=row['path'], identity=identity(os.fstat(writer.fileno()))))
                digest = hashlib.sha256(); copied = 0
                while block := reader.read(CHUNK):
                    writer.write(block); digest.update(block); copied += len(block)
                    self.publish(current=row['path'], file_bytes=copied, file_size=row['size'])
                writer.flush(); os.fsync(writer.fileno())
                os.fchmod(writer.fileno(), (row['mode'] & 0o777) | 0o600)
                os.utime(writer.fileno(), ns=(row['mtime'],row['mtime']))
            after = os.fstat(reader.fileno())
            if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns) or copied != row['size']:
                raise ValueError('複製期間原檔案被更動：'+row['path'])
            self.publish(True, partial=None)
        return digest.hexdigest()

    def copy(self):
        self.check(); self.inventory_check()
        remaining = self.db.execute("SELECT COALESCE(SUM(size),0) FROM entries WHERE kind='file' AND sha IS NULL").fetchone()[0]
        space = os.statvfs(self.dest.parent)
        if space.f_bavail*space.f_frsize < remaining+RESERVE: raise ValueError('目的磁碟可用空間不足；釋出空間後再接續。')
        dest = self.dest_open(create=True)
        try:
            self.marker(dest, 'incomplete')
            partial = self.s.get('partial')
            if partial:
                rel = Path(partial['path'])
                with directory(rel.parent, dest) as parent:
                    info = os.stat(rel.name,dir_fd=parent,follow_symlinks=False)
                    if not same_folder(info,partial['identity'],self.s.get('destination_binding')) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ValueError('中斷檔案已被替換，保留現況且不覆寫。')
                    os.unlink(rel.name,dir_fd=parent)
                self.publish(True,partial=None)
            self.publish(True, status='copying', copied=0, verified=0)
            with directory(self.root) as source:
                for row in self.db.execute("SELECT * FROM entries ORDER BY length(path),path"):
                    rel = Path(row['path']); self.publish(current=row['path'], file_bytes=0, file_size=row['size'])
                    with directory(rel.parent, dest) as parent:
                        try: existing = os.stat(rel.name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError: existing = None
                        if row['kind'] == 'dir':
                            if existing and not stat.S_ISDIR(existing.st_mode): raise ValueError('副本的資料夾已被替換：'+row['path'])
                            if not existing: os.mkdir(rel.name, 0o700, dir_fd=parent)
                            continue
                        if row['kind'] == 'link':
                            if existing:
                                if not stat.S_ISLNK(existing.st_mode) or os.readlink(rel.name,dir_fd=parent) != row['mapped']: raise ValueError('副本連結已變更：'+row['path'])
                            else: os.symlink(row['mapped'], rel.name, dir_fd=parent)
                            continue
                    if existing:
                        expected = self.hash_file(source,row,source=True)
                        if self.hash_file(dest,row) != expected or row['sha'] and row['sha'] != expected:
                            raise ValueError('副本驗證失敗，保留現況且不覆寫：'+row['path'])
                    else:
                        expected = self.copy_file(source,dest,row)
                        if self.hash_file(dest,row) != expected: raise ValueError('寫入後驗證失敗：'+row['path'])
                    self.db.execute('UPDATE entries SET sha=? WHERE path=?',(expected,row['path'])); self.db.commit()
                    self.publish(copied=self.s['copied']+1)
            self.publish(True, status='verifying')
            self.verify(dest)
        finally: os.close(dest)
        self.publish(True, status='ready', current='', finished=time.time())

    def verify(self, dest):
        self.check(); self.inventory_check(); self.inventory_check(source=False)
        self.publish(True, verified=0)
        with directory(self.root) as source:
            for row in self.db.execute("SELECT * FROM entries WHERE kind='file' ORDER BY path"):
                if not row['sha'] or self.hash_file(source,row,source=True) != row['sha'] or self.hash_file(dest,row) != row['sha']:
                    raise ValueError('內容驗證失敗：'+row['path'])
                self.publish(verified=self.s['verified']+1)
        self.check(); self.inventory_check(); self.inventory_check(source=False)
        # Check the current path again, not only the still-open disk descriptor.
        reopened = self.dest_open(); os.close(reopened)
        self.marker(dest,'verified')

    def run(self, mode):
        self.s['mode'] = mode
        try:
            if mode == 'plan': self.plan()
            elif mode == 'copy': self.copy()
            elif mode == 'switch':
                dest = self.dest_open()
                try: self.verify(dest)
                finally: os.close(dest)
                self.publish(True,status='switch_ready',current='')
            else: raise ValueError('未知的搬移操作。')
        except Exception as error:
            self.publish(True, status='failed', current='', error=str(error), finished=time.time())
        finally: self.db.close()
        return self.s


class Migrations:
    def __init__(self, engine):
        self.engine = engine; self.folder = engine.home/'migrations'; self.folder.mkdir(exist_ok=True)
        self.lock = threading.RLock(); self.process = None; self.active = None; self.fds = []
        for path in self.folder.glob('*/state.json'):
            try:
                state = json.loads(path.read_text())
                if state['status'] in ACTIVE or state['status']=='switch_ready':
                    state.update(status='paused',error='工作台曾中斷；接續前會重新核對來源與副本。')
                    atomic_json(path,state)
            except (OSError,ValueError,KeyError): pass

    def record(self, key):
        if key not in self.engine.projects or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',key): raise ValueError('找不到專案。')
        path = self.folder/key/'state.json'
        return json.loads(path.read_text()) if path.exists() else None

    def reconcile(self):
        if not self.process or self.process.poll() is None: return
        key = self.active; s = self.record(key)
        try:
            if s['status']=='switch_ready':
                if self.engine.projects[key] != s['registered_source']: raise ValueError('專案登記位置已改變，未切換。')
                # The child just re-hashed both trees after explicit consent.
                # Only local bookkeeping remains; never stat a NAS in this lock.
                self.engine.register_verified_copy(key,s['registered_source'],s['destination'],s.get('destination_binding'))
                s.update(status='complete',current='',finished=time.time())
            elif s['status'] in ACTIVE:
                s.update(status='paused',error='搬移程序中斷；已完成的檔案會在接續時重新核對。')
        except (OSError,ValueError,KeyError) as error: s.update(status='failed',error=str(error))
        finally:
            atomic_json(self.folder/key/'state.json',s)
            self.process=None; self.active=None
            for fd in self.fds: os.close(fd)
            self.fds=[]

    def get(self, key):
        with self.lock:
            self.reconcile()
            return dict(transfer=self.record(key),busy=self.active)

    def busy(self):
        with self.lock: self.reconcile(); return self.active

    def ensure_idle_worker(self,key):
        path=self.folder/key/'worker.lock'
        if not path.exists():return
        with path.open('a') as guard:
            try:fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise ValueError('前一次搬移程序仍在結束中，請稍後重試。') from None

    def archive(self,key,state):
        folder=self.folder/key
        atomic_json(folder/(state['transfer']+'.json'),state)
        manifest=folder/'manifest.sqlite'
        if manifest.exists(): manifest.replace(folder/(state['transfer']+'.sqlite'))

    def launch(self,key,mode):
        self.reconcile()
        if self.active or self.engine.active: raise ValueError('請先完成或暫停目前的運算／搬移。')
        folder=self.folder/key; state=self.record(key)
        if self.engine.projects[key] != state['registered_source']: raise ValueError('專案位置已改變，請重新預覽。')
        guard=os.open(folder/'worker.lock',os.O_CREAT|os.O_RDWR,0o600)
        try: fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            os.close(guard); raise ValueError('前一次搬移程序仍在結束中，請稍後重試。') from None
        reader,writer=os.pipe()
        try:
            state.update(status=dict(plan='planning',copy='copying',switch='switch_check')[mode],mode=mode,error='',current='準備檢查',verified=0,file_bytes=0,file_size=0)
            atomic_json(folder/'state.json',state)
            env=os.environ.copy();env['PYTHONPATH']=str(Path(__file__).resolve().parents[1])
            self.process=subprocess.Popen([sys.executable,'-m','alosstack_helper.migration',str(folder),mode,str(reader)],
                                          env=env,pass_fds=(reader,guard),start_new_session=True,
                                          stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.active=key;self.fds=[writer,guard]
        except Exception:
            os.close(writer);os.close(guard);state.update(status='failed',error='無法啟動搬移程序。');atomic_json(folder/'state.json',state);raise
        finally: os.close(reader)
        return self.get(key)

    def plan(self,key,parent,name):
        with self.lock:
            self.reconcile()
            if self.active or self.engine.active: raise ValueError('請先完成或暫停目前工作。')
            previous=self.record(key)
            self.ensure_idle_worker(key)
            if previous and previous.get('destination_identity') and previous['status']!='complete':
                raise ValueError('已有未完成的副本，請接續或先結束這次搬移紀錄。')
            if not isinstance(parent,str) or not parent.strip() or not Path(parent).expanduser().is_absolute(): raise ValueError('請先選擇目的資料夾。')
            name=str(name).strip()
            if not name or name in ('.','..',MARKER) or len(name)>160 or any(c in name for c in '/\\\x00') or any(ord(c)<32 for c in name): raise ValueError('請輸入有效的新資料夾名稱。')
            incident=self.engine.home/'interrupted-projects'/(key+'.json')
            if incident.exists():raise ValueError('請先檢查這個專案的儲存中斷紀錄。')
            root=absolute(self.engine.projects[key]);parent=absolute(parent);dest=parent/name
            if dest.is_relative_to(root) or root.is_relative_to(dest) or dest.is_relative_to(self.engine.home.resolve()): raise ValueError('新舊資料夾不能重疊，也不能放進工作台紀錄目錄。')
            folder=self.folder/key;folder.mkdir(exist_ok=True)
            if previous:
                self.archive(key,previous)
            s=dict(id=key,transfer=uuid.uuid4().hex,source=str(root),registered_source=self.engine.projects[key],destination=str(dest),
                   source_binding=self.engine.storage.entry(key).get('root_binding'),
                   destination_binding=None,status='planning',mode='plan',created=time.time(),files=0,bytes=0,
                   copied=0,verified=0,current='',error='')
            atomic_json(folder/'state.json',s)
            return self.launch(key,'plan')

    def start(self,key,transfer,mode,consent):
        with self.lock:
            self.reconcile();s=self.record(key)
            if not s or s['transfer']!=transfer: raise ValueError('搬移預覽已改變，請重新確認。')
            if consent is not True: raise ValueError('請確認目的位置與外部依賴。')
            if mode=='switch':
                if s['status']!='ready': raise ValueError('請先完成複製與驗證。')
            elif mode=='copy':
                if s['status'] not in ('preview','paused','failed') or s.get('mode')=='plan' and s['status']!='preview': raise ValueError('請先完成目的地預覽。')
            else: raise ValueError('無效搬移操作。')
            return self.launch(key,mode)

    def stop(self,key=None):
        with self.lock:
            self.reconcile()
            if not self.process: return
            if key is not None and key!=self.active: raise ValueError('這個專案沒有進行中的搬移。')
            self.process.terminate()
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:pass
            s=self.record(self.active);s.update(status='paused',error='已暫停。原檔案與已複製的副本都保留；接續時重新驗證。')
            atomic_json(self.folder/self.active/'state.json',s);self.reconcile()

    def abandon(self,key,transfer):
        with self.lock:
            self.reconcile();s=self.record(key)
            if self.active or not s or s['transfer']!=transfer: raise ValueError('請先暫停搬移並確認紀錄。')
            self.ensure_idle_worker(key)
            self.archive(key,s)
            # Only local bookkeeping is removed. Never remove destination/source.
            (self.folder/key/'state.json').unlink()
            return self.get(key)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder');parser.add_argument('mode');parser.add_argument('parent_fd',type=int)
    args=parser.parse_args()
    def watch():
        try:os.read(args.parent_fd,1)
        finally:os._exit(1)
    threading.Thread(target=watch,daemon=True).start()
    try:os.nice(5)
    except OSError:pass
    Transfer(args.folder).run(args.mode)


if __name__=='__main__':main()
