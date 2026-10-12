"""Conservative phase rollback for alosStack; never reuse a failed phase's files."""
from pathlib import Path
import copy
import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from .core import atomic_json, digest

PHASES = {
    'cmd1 · 對位與幾何': 'run', 'cmd2 · 干涉配對': 'run',
    'cmd3 · 電離層估計': 'run', '產生瀏覽器檢查圖片': 'run',
    '估計各日期電離層': 'continue', '套用電離層校正': 'continue',
    'cmd4 · 解纏與地理編碼': None,
}


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def process_alive(meta):
    # Analysis workers can survive a web-server restart and still read proc.
    for pidfile in (meta.parent/'analyses').glob('*/worker.pid'):
        try:
            pid=int(pidfile.read_text())
            if pid<=1: return True
            os.kill(pid,0)
            return True
        except ProcessLookupError: pass
        except (ValueError,PermissionError,OSError): return True
    p = meta/'worker-process.json'
    if not p.exists(): return False
    try:
        group = int(json.loads(p.read_text())['pgid'])
        if group <= 1: return True
        os.killpg(group, 0)
        return True
    except ProcessLookupError: return False
    except (ValueError, KeyError, PermissionError, json.JSONDecodeError): return True


def input_stamp(s):
    """Large inputs: identity/size/mtime; small metadata and package records: SHA256."""
    c = s['config']; files = set()
    for key in ('source', 'data'):
        if c.get(key):
            seen=set()
            for parent, dirs, names in os.walk(c[key],followlinks=True):
                resolved=Path(parent).resolve()
                if resolved in seen: dirs.clear();continue
                seen.add(resolved)
                files.update(Path(parent)/name for name in names if (Path(parent)/name).is_file())
    for key in ('dem', 'dem_geo', 'wbd'):
        for name in {c.get(key), s.get('prepared_inputs', {}).get(key)} - {None}:
            p = Path(name); files.add(p)
            files.update(x for x in p.parent.glob(p.name+'.*') if x.is_file())
    env = Path(c['env'])
    records = list((env/'conda-meta').glob('*.json'))
    if not records or not (env/'bin/python').is_file():
        raise ValueError('復原保護需要有效的 Conda 套件紀錄與 Python。')
    files.update(records); files.add(env/'bin/python')
    # Include upstream processor scripts so locally edited algorithms are detected.
    files.update((env/'share/isce2/alosStack').glob('*.py'))
    rows = []
    for p in sorted(files):
        st = p.stat()
        row = [str(p), str(p.resolve()), st.st_size, st.st_mtime_ns]
        if p.suffix in ('.xml', '.vrt', '.json', '.py'): row.append(digest(p))
        rows.append(row)
    return signature(rows)


def diagnosis(error, log):
    text = (error+'\n'+log).lower()
    for words, title, advice in [
        (['no space left', '空間不足'], '磁碟空間不足', '先釋放空間。復原需要保留半成品及建立工作副本。'),
        (['已停止', '服務已中斷', '結束碼 -15', '結束碼 -9'], '運算被中斷', '可能是手動停止、服務結束或系統中斷；先確認沒有殘留運算。'),
        (['bad cpu type', 'incompatible architecture', 'library not loaded'], '執行環境或函式庫問題', '先驗證所選環境。更換套件或環境會使原復原點失效，需另建專案。'),
        (['no such file', 'not found', '缺少', '找不到'], '檔案或工具缺失', '確認 NAS 已掛載、輸入路徑及必要工具。不要手動拼接半成品。'),
        (['permission denied', 'operation not permitted'], '存取權限問題', '確認工作台可讀取來源與寫入專案資料夾。'),
    ]:
        if any(w in text for w in words): return dict(title=title, advice=advice)
    return dict(title='需要檢查執行日誌', advice='目前無法確定根因。先閱讀最後錯誤；復原只避免混用半成品，不會自動修正演算法或資料問題。')


class Recovery:
    def __init__(self, engine, s):
        self.e = engine; self.s = s
        self.root, self.proc, self.meta = engine.paths(s)
        self.base = self.meta/'recovery'

    def contained(self):
        for p in (self.proc, self.meta, self.base):
            if p.is_symlink() or self.root.resolve() not in p.resolve().parents:
                raise ValueError('復原路徑不可使用外部或符號連結資料夾。')

    def checkpoint_path(self, ident):
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('無效的復原點識別碼。')
        p = self.base/ident
        if p.is_symlink(): raise ValueError('復原點不可是符號連結。')
        return p

    def cancelled(self):
        if self.e.cancel.is_set(): raise RuntimeError('已停止復原保護；未開始新的科學運算。')

    def manifest(self, folder, cancel=True):
        rows = {}
        for parent, dirs, files in os.walk(folder, followlinks=False):
            for name in sorted(dirs+files):
                if cancel: self.cancelled()
                p = Path(parent)/name; rel = str(p.relative_to(folder)); st = p.lstat()
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(p)
                    original = self.proc/rel
                    resolved = (original.parent/target).resolve()
                    allowed = [self.proc.resolve(), (self.meta/'inputs').resolve()]
                    allowed += [Path(self.s['config'][k]).resolve() for k in ('source','data') if self.s['config'].get(k)]
                    if not any(resolved == a or a in resolved.parents for a in allowed):
                        raise ValueError('產物含未受支援的外部連結：'+rel)
                    effective=folder/resolved.relative_to(self.proc.resolve()) if self.proc.resolve() in resolved.parents else resolved
                    if not effective.exists(): raise ValueError('產物含失效連結：'+rel)
                    rows[rel] = ['link', target]
                elif stat.S_ISDIR(st.st_mode): rows[rel] = ['dir']
                elif stat.S_ISREG(st.st_mode):
                    h = hashlib.sha256()
                    with p.open('rb') as f:
                        for chunk in iter(lambda:f.read(4*1024*1024), b''):
                            if cancel: self.cancelled()
                            h.update(chunk)
                    rows[rel] = ['file', st.st_size, h.hexdigest()]
                else: raise ValueError('產物含特殊檔案，無法建立可靠復原點：'+rel)
        return rows

    def copy_tree(self, source, dest):
        """Independent copies, APFS clones when available. Never hard-link data."""
        def copy_file(src, dst):
            self.cancelled()
            cloned = False
            if sys.platform == 'darwin':
                libc = ctypes.CDLL(None, use_errno=True)
                result = libc.clonefile(os.fsencode(src), os.fsencode(dst), 0)
                if result == 0: cloned = True
                elif ctypes.get_errno() == errno.ENOSPC: raise OSError(errno.ENOSPC, 'No space left for recovery copy')
            if not cloned:
                with open(src, 'rb') as r, open(dst, 'xb') as w:
                    for block in iter(lambda:r.read(4*1024*1024), b''):
                        self.cancelled(); w.write(block)
            shutil.copystat(src, dst)
            return dst
        shutil.copytree(source, dest, symlinks=True, copy_function=copy_file)

    def check_rasters(self, folder):
        sizes = {'BYTE':1,'CHAR':1,'SHORT':2,'USHORT':2,'INT':4,'UINT':4,'LONG':8,
                 'FLOAT':4,'DOUBLE':8,'CFLOAT':8,'CDOUBLE':16}
        count = 0
        for xml in folder.rglob('*.xml'):
            if xml.is_symlink(): continue
            try: tree = ET.parse(xml).getroot()
            except ET.ParseError: raise ValueError('描述檔 XML 已損壞：'+str(xml.relative_to(folder)))
            if tree.tag != 'imageFile': continue
            p = Path(str(xml)[:-4])
            if p.is_symlink():
                original=self.proc/p.relative_to(folder)
                resolved=(original.parent/os.readlink(p)).resolve()
                if self.proc.resolve() in resolved.parents:
                    p=folder/resolved.relative_to(self.proc.resolve())
            if not p.exists():
                if Path(str(p)+'.vrt').is_file(): continue  # virtual SLC, checked by ISCE at execution
                raise ValueError('描述檔缺少影像：'+str(xml.relative_to(folder)))
            props = {n.get('name','').lower():n.findtext('value', n.text or '').strip() for n in tree.findall('property')}
            try:
                w = int(tree.findtext("component[@name='coordinate1']/property[@name='size']/value") or props['width'])
                h = int(tree.findtext("component[@name='coordinate2']/property[@name='size']/value") or props['length'])
                bands = int(props.get('number_bands',props.get('bands','1'))); size = sizes[props['data_type'].upper()]
                if min(w,h,bands)<=0 or p.stat().st_size != w*h*bands*size: raise ValueError()
            except (ValueError, KeyError):
                raise ValueError('影像大小與描述檔不符或型別不支援：'+str(p.relative_to(folder)))
            count += 1
        return count

    def capture(self, label, action):
        self.contained()
        if process_alive(self.meta): raise ValueError('仍有運算程序存在，不能建立復原點。')
        self.e.verify_commands(self.s)
        if action not in ('run','continue') or label not in PHASES:
            raise ValueError('未知的復原階段。')
        ident = uuid.uuid4().hex; path = self.checkpoint_path(ident)
        self.base.mkdir(exist_ok=True)
        before = self.manifest(self.proc)
        size = sum(r[1] for r in before.values() if r[0]=='file')
        if shutil.disk_usage(self.root).free < size + 1024**3:
            raise ValueError('空間不足以建立完整復原副本，請釋放空間或在恢復中心關閉後續保護。')
        self.check_rasters(self.proc)
        stamp = input_stamp(self.s)
        old = self.s.get('recovery',{}).get('id')
        path.mkdir()
        try:
            self.copy_tree(self.proc, path/'proc')
            if self.manifest(path/'proc') != before or input_stamp(self.s) != stamp:
                raise ValueError('建立副本時檔案發生變動；未開始此階段。')
            state = copy.deepcopy(self.s); state.pop('recovery',None)
            report = dict(id=ident, phase=label, action=action, state=state, manifest=before,
                          inputs=stamp, bytes=size, config=signature(self.s['config']),
                          time=time.strftime('%Y-%m-%d %H:%M:%S'))
            atomic_json(path/'checkpoint.json', report)
            self.s['recovery'] = {k:report[k] for k in ('id','phase','action','bytes','time')}
            self.s['recovery']['metadata_sha256'] = digest(path/'checkpoint.json')
            self.e.save(self.s)
        except BaseException:
            shutil.rmtree(path)
            raise
        # Only discard our previous snapshot after the new pointer is committed.
        if old and old != ident:
            previous = self.checkpoint_path(old)
            if (previous/'checkpoint.json').is_file(): shutil.rmtree(previous)

    def report(self, restoring=False):
        log = self.meta/'run.log'
        if log.exists():
            with log.open('rb') as f:
                f.seek(max(0,log.stat().st_size-12000)); tail=f.read().decode(errors='replace')
        else: tail=''
        result = dict(enabled=bool(self.s.get('recovery_enabled')), can_recover=False,
                      phase=self.s.get('failed_phase',self.s.get('stage','')),
                      last_completed=(self.s.get('history') or [{}])[-1].get('stage'),
                      diagnosis=diagnosis(self.s.get('error',''),tail), error=self.s.get('error',''),
                      log_tail=tail, preserved=str(self.base), checks=[])
        try:
            self.contained()
            if self.s['config'].get('workflow','alosStack') != 'alosStack': raise ValueError('本版恢復中心先支援 ALOS-2 多日期。')
            if self.s['status'] != 'failed' and not (restoring and self.s['status']=='running'): raise ValueError('目前沒有失敗工作需要復原。')
            if (self.e.active and not (restoring and self.e.active==self.s['id'])) or process_alive(self.meta): raise ValueError('仍有工作或殘留程序執行，請先停止並確認。')
            info = self.s.get('recovery')
            if not info: raise ValueError('此工作沒有階段前復原副本。不能只依歷史紀錄安全續跑，請保留成果另行診斷。')
            path = self.checkpoint_path(info['id'])
            if digest(path/'checkpoint.json') != info['metadata_sha256']: raise ValueError('復原點紀錄已變動。')
            record = json.loads((path/'checkpoint.json').read_text())
            if record['id']!=info['id'] or record['phase'] not in PHASES or record['action'] not in ('run','continue'):
                raise ValueError('復原點資訊不完整。')
            if record['config'] != signature(self.s['config']) or record['state'].get('excluded') != self.s.get('excluded'): raise ValueError('專案設定或排除清單已變動，不能使用原復原點。')
            result.update(can_recover=True, snapshot=info,
                          notice='已有階段前副本。按下復原後會逐檔核對 SHA256、輸入與環境紀錄；驗證通過才移開半成品並重跑此階段。')
        except (ValueError, KeyError, OSError) as e: result['blocked']=str(e)
        return result

    def restore(self, ident):
        report = self.report(restoring=True)
        if not report['can_recover']: raise ValueError(report.get('blocked','不能復原。'))
        if report['snapshot']['id'] != ident: raise ValueError('復原點已更新，請重新診斷。')
        path = self.checkpoint_path(ident); record=json.loads((path/'checkpoint.json').read_text())
        if input_stamp(self.s) != record['inputs']: raise ValueError('輸入或環境已變動，已停止復原。')
        if self.manifest(path/'proc') != record['manifest']: raise ValueError('復原副本缺檔或內容已變動，已停止復原。')
        self.check_rasters(path/'proc')
        # Never accept hand-edited generated commands, including on the failed tree.
        if self.proc.exists(): self.e.verify_commands(self.s)
        journal = self.base/'restore.json'
        if journal.exists():
            move = json.loads(journal.read_text())
            if move['snapshot'] != ident: raise ValueError('尚有不同復原點的搬移未完成。')
            attempt = self.checkpoint_path(move['attempt'])
            staging = attempt/'restored'; archived = attempt/'failed-proc'
        else:
            if shutil.disk_usage(self.root).free < record['bytes']+1024**3:
                raise ValueError('空間不足：需要保留半成品並建立復原工作副本。')
            attempt = self.checkpoint_path(uuid.uuid4().hex); attempt.mkdir()
            staging=attempt/'restored'; archived=attempt/'failed-proc'
            try:
                self.copy_tree(path/'proc', staging)
                if self.manifest(staging) != record['manifest']: raise ValueError('復原工作副本核對失敗。')
                atomic_json(attempt/'failed-state.json', self.s)
                atomic_json(journal, dict(snapshot=ident,attempt=attempt.name))
            except BaseException:
                shutil.rmtree(attempt)
                raise
        self.cancelled()
        if input_stamp(self.s) != record['inputs']: raise ValueError('驗證期間輸入或環境發生變動，已停止復原。')
        if process_alive(self.meta): raise ValueError('仍有殘留運算，停止復原。')
        if staging.exists():
            if self.manifest(staging) != record['manifest']: raise ValueError('待搬移復原副本不完整。')
            if self.proc.exists():
                if archived.exists(): raise ValueError('復原搬移狀態不明，已保留所有資料。')
                self.proc.rename(archived)
            staging.rename(self.proc)
        elif not archived.exists() or not self.proc.exists() or self.manifest(self.proc) != record['manifest']:
            raise ValueError('復原搬移未完成，請保留所有副本並檢查。')
        restored = record['state']; restored['recovery']=self.s['recovery']
        restored.update(status='running',error='',stage='復原驗證完成，準備續跑',
                        recovery_attempt=str(attempt),recovery_enabled=True)
        self.s.clear(); self.s.update(restored); self.e.save(self.s)
        journal.unlink()
        self.e.log(self.s, '復原已驗證；半成品保留於 '+str(archived)+'；重跑 '+record['phase'])
        return record['action'], record['phase']
