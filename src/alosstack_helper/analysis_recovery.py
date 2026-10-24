"""Verified pre-stage copies for MintPy, restored only as a new analysis.

The failed analysis is never repaired in place. Only one sealed copy is retained
per run. Large source rasters remain read-only links; generated HDF5 files are
independent copies (APFS clones where available), never hard links.
"""
from pathlib import Path
import copy
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import uuid

from .core import atomic_json, digest
from .storage import atomic_existing_json
from . import mintpy_diagnostics as diagnostics


STAGES = {
    'load': ('ready', '重新載入資料'),
    'invert': ('loaded', '確認參考點'),
    'corrections': ('diagnostic_review', '判讀解纏診斷'),
    'finalize': ('residual_review', '判讀殘差與日期'),
}
SKIP = {'.recovery', 'cache', 'map-cache', 'worker.pid'}
TEXT_SUFFIXES = {'.json', '.cfg', '.xml', '.vrt', '.rsc', '.txt'}


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def controls(job):
    options = copy.deepcopy(job.get('options', {})); options.pop('name', None)
    return dict(options=options, reference_pixel=job.get('reference_pixel'), review_pending=job.get('review_pending'))


def environment_stamp(prefix):
    prefix = Path(prefix); records = sorted((prefix/'conda-meta').glob('*.json'))
    python = prefix/'bin/python'
    if not records or not python.is_file():
        raise ValueError('恢復保護需要所選環境的 Python 與 Conda 套件紀錄。')
    files = records+[python]
    # Include editable MintPy code as well as the package manager records.
    files.extend(prefix.glob('lib/python*/site-packages/mintpy/**/*.py'))
    rows = [[str(p), str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns, digest(p)] for p in sorted(set(files))]
    # Identical installed code is stable across a checkout/package relocation;
    # changed scientific code still invalidates a checkpoint by content.
    for name in ('science_worker.py', 'mintpy_runtime.py', 'mintpy_diagnostics.py', 'mintpy_parameters.py', 'analysis_recovery_worker.py'):
        rows.append(['workbench', name, digest(Path(__file__).parent/name)])
    return signature(rows)


def project_stamp(state):
    return signature({k:state.get(k) for k in ('id', 'config', 'ion', 'excluded', 'import_only', 'legacy')})


def external_stamp(job):
    rows = []
    for saved in job.get('parameter_report', {}).get('external_inputs', []):
        path = Path(saved['path']).resolve(); s = path.stat()
        current = dict(path=str(path), size=s.st_size, mtime_ns=s.st_mtime_ns)
        if current != saved:
            raise ValueError('外部輔助資料已改變，不能沿用這個恢復點。')
        rows.append(current)
    return rows


def copy_tree(source, destination, cancelled=lambda:None, existing=False):
    def copy_file(src, dst):
        cancelled(); cloned = False
        if sys.platform == 'darwin':
            libc = ctypes.CDLL(None, use_errno=True)
            cloned = libc.clonefile(os.fsencode(src), os.fsencode(dst), 0) == 0
            if not cloned and ctypes.get_errno() == errno.ENOSPC:
                raise OSError(errno.ENOSPC, 'No space left for recovery copy')
        if not cloned:
            with open(src, 'rb') as r, open(dst, 'xb') as w:
                for block in iter(lambda:r.read(4*1024*1024), b''):
                    cancelled(); w.write(block)
        shutil.copystat(src, dst)
        return dst
    shutil.copytree(source, destination, symlinks=True, copy_function=copy_file, dirs_exist_ok=existing,
                    ignore=lambda p,n:SKIP.intersection(n) if Path(p)==Path(source) else set())


def unfinished_recovery_alive(base):
    """A helper may outlive the server; only its private staging remains visible."""
    staging=Path(base)/'.recovery-staging'
    if staging.is_symlink():raise ValueError('分析恢復暫存資料夾不可是符號連結。')
    for marker in staging.glob('*/operation.json'):
        if marker.is_symlink():raise ValueError('分析恢復紀錄不可是符號連結。')
        try:record=json.loads(marker.read_text())
        except (OSError, ValueError):return True
        if record.get('published') is True:continue
        for name in ('server_pid','worker_pid'):
            pid=record.get(name)
            if pid is None:continue
            try:
                pid=int(pid)
                if pid<=1:return True
                os.kill(pid,0)
                return True
            except ProcessLookupError:pass
            except (ValueError, PermissionError):return True
    return False


class AnalysisRecovery:
    def __init__(self, analyses, key, run, reservation=None):
        self.a = analyses; self.e = analyses.engine; self.key = key; self.run = run
        self.folder = analyses.folder(key, run); self.base = self.folder/'.recovery'
        self.reservation=reservation

    def contained(self):
        if self.base.is_symlink() or self.folder.is_symlink():
            raise ValueError('恢復資料夾不可是符號連結。')

    def checkpoint(self, ident):
        self.contained()
        if not isinstance(ident, str) or not re.fullmatch('[0-9a-f]{32}', ident):
            raise ValueError('無效的分析恢復點。')
        path = self.base/ident
        if path.is_symlink() or (path/'data').is_symlink():
            raise ValueError('恢復點不可是符號連結。')
        return path

    def cancelled(self):
        if self.e.cancel.is_set():raise RuntimeError('已停止建立分析恢復副本；未開始下一個科學階段。')

    def manifest(self, folder, job, check_cancel=False):
        rows = {}; allowed = {str(Path(x).resolve()) for x in job['inventory']['files']}
        allowed.update(str(Path(r['path']).resolve()) for r in job.get('parameter_report',{}).get('external_inputs',[]))
        for parent, dirs, files in os.walk(folder, followlinks=False):
            if Path(parent)==folder:
                dirs[:] = [n for n in dirs if n not in SKIP]; files = [n for n in files if n not in SKIP]
            for name in sorted(dirs+files):
                if check_cancel:self.cancelled()
                p = Path(parent)/name; rel = str(p.relative_to(folder)); st = p.lstat()
                if stat.S_ISLNK(st.st_mode):
                    target = os.readlink(p); resolved = (self.folder/rel).parent.joinpath(target).resolve()
                    if str(resolved) not in allowed:
                        raise ValueError('分析含未受支援的外部或內部連結：'+rel)
                    if not resolved.is_file():raise ValueError('分析來源連結已失效：'+rel)
                    rows[rel] = ['link', target]
                elif stat.S_ISDIR(st.st_mode):rows[rel] = ['dir']
                elif stat.S_ISREG(st.st_mode):
                    h = hashlib.sha256()
                    with p.open('rb') as f:
                        for block in iter(lambda:f.read(4*1024*1024), b''):
                            if check_cancel:self.cancelled()
                            h.update(block)
                    rows[rel] = ['file', st.st_size, h.hexdigest()]
                else:raise ValueError('分析含特殊檔案，不能建立恢復點：'+rel)
        return rows

    def source_unchanged(self, job):
        from .analysis import fingerprint
        if fingerprint(job['inventory']['files']) != job['source_fingerprint']:
            raise ValueError('來源影像或描述檔已改變，請另建分析，不能使用此恢復點。')
        external_stamp(job)

    def capture(self, job, mode):
        """Called by the worker before the native mode can change any files."""
        self.contained(); self.cancelled(); self.source_unchanged(job)
        if mode not in STAGES:raise ValueError('不支援的 MintPy 恢復階段。')
        before_status, label = STAGES[mode]
        if mode in ('corrections','finalize'):
            diagnostics.verify(self.folder, job['review_pending']['report_id'])
        state = self.e.require_processing_location(self.key, check_inputs=False)
        inputs = environment_stamp(job['environment']); config = project_stamp(state)
        before = self.manifest(self.folder, job, True)
        size = sum(row[1] for row in before.values() if row[0]=='file')
        if shutil.disk_usage(self.folder).free < size+64*1024**2:
            raise ValueError('磁碟空間不足以建立 MintPy 階段副本，未開始下一階段。請先釋放空間。')
        ident = uuid.uuid4().hex; path = self.checkpoint(ident)
        path.mkdir(parents=True)
        try:
            copy_tree(self.folder, path/'data', self.cancelled)
            if self.manifest(path/'data', job, True) != before:
                raise ValueError('複製期間分析資料已變更，未開始下一階段。')
            self.source_unchanged(job)
            if environment_stamp(job['environment']) != inputs or project_stamp(self.e.get(self.key)) != config:
                raise ValueError('建立副本期間環境或專案設定已變更。')
            report = dict(id=ident, mode=mode, status=before_status, label=label, bytes=size,
                          created=time.strftime('%Y-%m-%d %H:%M:%S'), source_run=self.run,
                          source_folder=str(self.folder), environment=inputs, project=config,
                          controls=controls(job), manifest=before)
            atomic_json(path/'checkpoint.json', report)
            old = job.get('recovery_checkpoint',{}).get('id')
            job['recovery_checkpoint'] = dict(id=ident, sha256=digest(path/'checkpoint.json'), mode=mode,
                                             status=before_status, label=label, bytes=size, created=report['created'])
            atomic_existing_json(self.folder/'analysis.json', job)
            if old and old != ident:
                previous = self.checkpoint(old)
                # A cleanup failure must never invalidate the newly committed
                # checkpoint. An extra obsolete copy is safe to retain.
                try:
                    if (previous/'checkpoint.json').is_file():shutil.rmtree(previous)
                except (OSError, ValueError):pass
            return job
        except BaseException:
            shutil.rmtree(path)
            raise

    def validate(self, expected=None):
        job = self.a.get(self.key, self.run)
        if job.get('kind') != 'mintpy' or job.get('status') != 'failed':
            raise ValueError('只有已停止的 MintPy 分析能建立恢復版本。')
        if self.e.active and self.e.active!=self.reservation:raise ValueError('仍有工作執行中，請先等待結束。')
        if not self.reservation and unfinished_recovery_alive(self.folder.parent):
            raise ValueError('仍有分析恢復程序存活；未完成版本保持隱藏，請先等待該程序結束。')
        from .recovery import process_alive
        state = self.e.require_processing_location(self.key, check_inputs=False)
        if process_alive(Path(state['config']['root'])/'.helper'):
            raise ValueError('仍有背景程序存活，不能使用恢復點。')
        if state.get('import_only'):
            from .legacy import require_legacy_source
            require_legacy_source(state)
        pointer = job.get('recovery_checkpoint',{})
        if not pointer:raise ValueError('此分析沒有完整的階段副本；舊版分析需要另建版本。')
        if expected is not None and pointer.get('id') != expected:raise ValueError('恢復點已變更，請重新檢查。')
        path = self.checkpoint(pointer.get('id')); meta = path/'checkpoint.json'
        if meta.is_symlink() or digest(meta) != pointer.get('sha256'):
            raise ValueError('恢復點紀錄已變更，不能繼續。')
        report = json.loads(meta.read_text())
        if report['source_folder'] != str(self.folder) or report['source_run'] != self.run:
            raise ValueError('恢復點不屬於此分析位置。')
        if report['project'] != project_stamp(state):raise ValueError('專案設定已改變，請另建分析。')
        original = json.loads((path/'data/analysis.json').read_text())
        if original['project_id'] != self.key:raise ValueError('恢復點專案識別不一致。')
        # finalize changes the selected time reference before running velocity;
        # that exact native edit is the only permitted control change.
        current = controls(job); permitted = [report['controls']]
        if report['mode']=='finalize':
            after = copy.deepcopy(report['controls'])
            after['options']['reference_date'] = after['review_pending']['reference_date'][2:]
            permitted.append(after)
        if current not in permitted:raise ValueError('分析參數已改變，請另建分析，不能沿用恢復點。')
        self.source_unchanged(original)
        if environment_stamp(original['environment']) != report['environment']:
            raise ValueError('分析環境或處理程式已改變，請另建分析。')
        if self.manifest(path/'data', original) != report['manifest']:
            raise ValueError('階段副本完整性檢查失敗，不能沿用。')
        if report['mode'] in ('corrections','finalize'):
            diagnostics.verify(path/'data', original['review_pending']['report_id'])
        return report, original

    def report(self):
        result = dict(can_recover=False, source_run=self.run, checks=[], reason='',
                      scope='建立新分析版本，保留原失敗版本；只使用最後完整的階段副本。')
        try:
            report, _ = self.validate()
            result.update(can_recover=True, checkpoint_id=report['id'], status=report['status'],
                          stage=report['label'], bytes=report['bytes'], created=report['created'],
                          checks=['專案與來源一致', '環境與參數一致', '階段副本完整', '沒有背景運算'],
                          requires_review=report['status']!='ready', required_free_bytes=report['bytes']+64*1024**2,
                          copy_note='使用獨立副本（支援時採 APFS 複製）；原失敗版本與最後一份完整階段副本皆保留。',
                          next_action='重新載入資料' if report['status']=='ready' else '回到人工確認關卡，不自動接續')
            result['free_bytes']=shutil.disk_usage(self.folder).free
            if result['free_bytes'] < result['required_free_bytes']:
                result.update(can_recover=False,reason='磁碟空間不足以保留失敗版本並建立恢復版本。')
            else:result['checks'].append('磁碟空間足夠')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result['reason'] = str(exc)
        return result

    def restore(self, expected, destination, new_id, published=None):
        report, original = self.validate(expected)
        if shutil.disk_usage(self.folder).free < report['bytes']+64*1024**2:
            raise ValueError('磁碟空間不足以保留失敗版本並建立恢復版本。')
        source = self.checkpoint(report['id'])/'data'
        # Claim an otherwise absent destination before copying, so cleanup can
        # never remove an unrelated pre-existing analysis on cancellation.
        destination.mkdir()
        try:
            copy_tree(source, destination, self.cancelled, existing=True)
            if self.manifest(destination, original, True) != report['manifest']:
                raise ValueError('新版本複製驗證失敗，未開始任何科學運算。')
            self.validate(expected)
            # Rewrite owned textual paths only. Raster links keep pointing at
            # verified original inputs. HDF5 path attributes are rebased by the
            # selected scientific interpreter, never by reading data arrays.
            published=Path(published or destination)
            for p in destination.rglob('*'):
                if p.is_symlink() or not p.is_file() or p.suffix not in TEXT_SUFFIXES:continue
                text = p.read_text(); changed = text.replace(str(self.folder), str(published))
                if changed != text:p.write_text(changed)
            job = json.loads((destination/'analysis.json').read_text())
            pending = job.pop('review_pending', None)
            if pending:
                decisions = job.get('review_decisions',[])
                if decisions and decisions[-1] == pending:decisions.pop()
            job.pop('recovery_checkpoint',None)
            job.update(id=new_id, status=report['status'], stage=report['label'], error='',
                       created=time.strftime('%Y-%m-%d %H:%M:%S'))
            name=self.a.get(self.key,self.run).get('options',{}).get('name') or 'MintPy'
            job.setdefault('options',{})['name']=str(name)[:95]+' · 恢復'
            job['recovery_origin'] = dict(source_run=self.run, checkpoint_id=report['id'],
                                          recovered_at=job['created'], restart_mode=report['mode'],
                                          preserved_failed_folder=str(self.folder), previous_decision=pending,
                                          science_approval_required=report['status']!='ready')
            job.setdefault('history',[]).append(dict(stage='由完整階段副本建立新分析版本', time=job['created'], source_run=self.run))
            atomic_existing_json(destination/'analysis.json', job)
            return job, report
        except BaseException:
            shutil.rmtree(destination)
            raise
