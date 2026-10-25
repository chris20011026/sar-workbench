"""Self-contained, inert alosStack inputs for a fresh downstream analysis.

A folder package includes only the validated final rasters and reconstructed
metadata needed by QC/MintPy. It never carries executable checkpoints, Conda
files, raw SLC or approvals into the new project. All slow operations run in the
portable job subprocess. Nothing here modifies the source or registers projects.
"""
from pathlib import Path, PurePosixPath
import copy
import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from .core import atomic_json, find_conda
from .legacy import inspect_legacy, KINDS, _component, _prop, _snapshot
from .migration import directory
from .recovery import process_alive

FORMAT = 'sar-alosstack-analysis-package-v1'
MANIFEST = 'sar-analysis-package.json'
INCOMPLETE = '.sar-portable-incomplete.json'
RECEIPT = 'portable-source.json'
MAX_FILES = 20000
MAX_JSON = 16 * 1024**2
RESERVE = 64 * 1024**2
CHUNK = 4 * 1024**2
EXCLUDED_BY_SCOPE = ['raw_slc', 'isce_intermediates', 'executable_checkpoints', 'conda_environment', 'mintpy_result_arrays']
WARNINGS = [
    '這是 alosStack 成果的獨立品質檢查／MintPy 輸入包；不含原始 SLC、完整 ISCE 處理中間檔或 Conda 環境。',
    '原判讀與參數僅保存為歷史紀錄；匯入後需要重新做品質檢查與 MintPy 人工判讀，不直接續跑舊指令。',
    '完整 SHA-256 驗證確認檔案內容一致，不代表科學品質已通過驗證。',
]


def _json(path):
    path = Path(path)
    with directory(path.parent) as fd:
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON:
            raise ValueError('可攜資料包紀錄格式或大小不合法。')
        value = json.loads(stream.read(MAX_JSON + 1))
    if not isinstance(value, dict): raise ValueError('可攜資料包紀錄必須是物件。')
    return value


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('資料包內路徑不合法。')
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ('.', '..') for p in value.split('/')) or str(path) != value:
        raise ValueError('資料包內路徑必須是不含上層跳轉的相對路徑。')
    return Path(value)


def _notify(callback, **values):
    if callback: callback(values)


def _identity(path):
    with directory(path) as fd:
        info = os.fstat(fd)
        return [info.st_dev, info.st_ino]


def _destination(value, source, environment=None):
    if not isinstance(value, str) or not value.strip(): raise ValueError('請選擇新資料夾位置。')
    raw = Path(value).expanduser()
    if not raw.is_absolute() or raw.name in ('', '.', '..') or any(ord(c)<32 for c in str(raw)):
        raise ValueError('請使用完整的新資料夾路徑。')
    parent = raw.parent.resolve(strict=True)
    _identity(parent)
    target = parent / raw.name
    if target.exists() or target.is_symlink(): raise ValueError('目的資料夾已存在，請選擇新名稱；不會覆寫既有資料。')
    source = Path(source).resolve(strict=True)
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('新資料夾與來源不可互相包含。')
    if environment:
        env = Path(environment).expanduser().resolve(strict=True)
        if target.is_relative_to(env) or env.is_relative_to(target): raise ValueError('資料包或新專案不能與運算環境重疊。')
    return target


def _hash(path, expected=None):
    """Hash one ordinary file without following path components or special files."""
    path = Path(path)
    with directory(path.parent) as fd:
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode): raise ValueError('來源必須是一般檔案：' + str(path))
        h = hashlib.sha256()
        while block := stream.read(CHUNK): h.update(block)
        after = os.fstat(stream.fileno())
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if fields(before) != fields(after): raise ValueError('讀取時來源已變更：' + str(path))
    record = dict(size=after.st_size, sha256=h.hexdigest())
    if expected and any(record[k] != expected[k] for k in ('size', 'sha256')):
        raise ValueError('檔案內容已變更或驗證失敗：' + str(path))
    return record


def _state_record(state):
    """Keep old actions inert; never install them as the new project's history."""
    root = Path(state['config']['root'])
    analyses = []
    for path in sorted((root/'analyses').glob('*/analysis.json')):
        if len(analyses) >= 1000: raise ValueError('分析紀錄過多，請先整理來源專案。')
        record = _json(path)
        analyses.append({k:record.get(k) for k in ('id','kind','created','environment','status','options','history','review_decisions')})
    packages = []
    env = Path(state['config'].get('env') or root/'.missing-environment')
    for path in sorted((env/'conda-meta').glob('*.json')):
        if len(packages) >= 5000: raise ValueError('環境套件紀錄超過上限。')
        record = _json(path); packages.append({k:record.get(k) for k in ('name','version','build','subdir')})
    return copy.deepcopy({
        'source_project_id': state.get('id'),
        'source_config': state.get('config', {}),
        'source_name': state.get('config', {}).get('name'),
        'processing_environment': state.get('config', {}).get('env'),
        'processing_status': state.get('status'),
        'history': state.get('history', []),
        'ion': state.get('ion', {}),
        'ion_excluded': state.get('excluded', []),
        'properties': state.get('config', {}).get('properties', {}),
        'legacy_import': state.get('legacy_import'),
        'portable_import': state.get('portable_import'),
        'analysis_records': analyses,
        'processing_reviews': {k:v for k,v in state.items() if 'approval' in k or k in ('matching_quality','ion_postcheck','cmd4_acceptance')},
        'environment_packages': packages,
    })


def _inspect_export(state, destination, environment=None):
    config = state.get('config', {})
    if config.get('workflow', 'alosStack') != 'alosStack':
        raise ValueError('可攜分析包目前只接受 alosStack 多日期成果。')
    if state.get('status') != 'complete' and not (state.get('status') == 'imported' and state.get('import_only')):
        raise ValueError('請先完成 alosStack，或選擇已匯入的完整成果。')
    root = Path(config['root']).expanduser().resolve(strict=True)
    if process_alive(root / '.helper'): raise ValueError('來源仍有處理程序，請等待工作結束。')
    for path in (root / 'analyses').glob('*/analysis.json'):
        if path.is_symlink() or path.parent.is_symlink(): raise ValueError('分析紀錄不可是連結。')
        if _json(path).get('status') == 'running': raise ValueError('請先完成或停止來源的 MintPy 分析。')
    source = root / 'proc'
    if state.get('portable_import'):
        require_portable_source(state)
    elif state.get('import_only'):
        from .legacy import require_legacy_source
        require_legacy_source(state)
        source = Path(_json(root / '.helper/legacy-source.json')['source'])
    env = environment or config.get('env')
    dest = _destination(destination, root, env)
    if dest.is_relative_to(source.resolve()) or source.resolve().is_relative_to(dest):
        raise ValueError('資料包與原始成果不可互相包含。')
    request = dict(source=str(source), root=str(dest), environment=env,
                   name=str(config.get('name') or root.name), reference=config.get('reference'))
    report = inspect_legacy(request)
    expected = state.get('expected_pairs')
    if expected and sorted(expected) != report['pairs']:
        raise ValueError('成果中的配對與專案紀錄不一致，請先檢查後再匯出。')
    return report, dest


def plan_export(state, destination, environment=None):
    report, dest = _inspect_export(state, destination, environment)
    sources = []
    for row in report['manifest']:
        sources.append(dict(path=row['relative'], target=row['target'], **_hash(row['target'])))
    total = sum(row['size'] for row in sources)
    free = shutil.disk_usage(dest.parent).free
    record = _state_record(state)
    provenance_bytes = len(json.dumps(record, ensure_ascii=False, indent=2).encode())
    if provenance_bytes > MAX_JSON: raise ValueError('來源參數與歷史紀錄過大，請先整理專案再匯出。')
    total += provenance_bytes
    return dict(format=FORMAT, operation='export', source=report['source'], destination=str(dest),
                root=str(dest), environment=report['environment'], name=report['name'],
                parent_identity=_identity(dest.parent), source_identity=_identity(Path(report['source'])),
                reference=report['reference'], pairs=report['pairs'], dates=report['dates'],
                width=report['width'], height=report['height'], looks=report['looks'], sensor=report['sensor'],
                files=len(sources)+1, bytes=total, required=total+RESERVE, free=free, space_ok=free>=total+RESERVE,
                warnings=list(WARNINGS), provenance=record, sources=sources,
                legacy_fingerprint=report['fingerprint'], rasters=report['rasters'], metadata=report['metadata'])


def _assert_parent(preview):
    target = Path(preview['root'])
    if _identity(target.parent) != preview['parent_identity']:
        raise ValueError('目的地父資料夾已改變，請重新預覽。')
    if target.exists() or target.is_symlink(): raise ValueError('目的資料夾已存在，請重新選擇新位置。')
    if shutil.disk_usage(target.parent).free < preview['required']:
        raise ValueError('可用空間不足，請改用容量較大的目的地。')
    return target


def _mkdir(target):
    with directory(target.parent) as fd: os.mkdir(target.name, 0o700, dir_fd=fd)
    return _identity(target)


def _same_root(root, identity):
    if _identity(root) != identity: raise ValueError('目的資料夾已被替換，操作已停止。')


def _copy(path, destination, expected, progress, done, root, identity):
    path, destination = Path(path), Path(destination)
    _same_root(root, identity)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with directory(path.parent) as fd:
        source_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(source_fd, 'rb') as source:
        initial = os.fstat(source.fileno())
        if not stat.S_ISREG(initial.st_mode) or initial.st_size != expected['size']:
            raise ValueError('來源大小或類型已改變：' + str(path))
        with directory(destination.parent) as fd:
            output_fd = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(output_fd, 'wb') as output:
            h = hashlib.sha256(); copied = 0
            while block := source.read(CHUNK):
                output.write(block); h.update(block); copied += len(block)
                _notify(progress, files_done=done['files'], bytes_done=done['bytes']+copied,
                        current=str(destination.relative_to(root)))
            output.flush(); os.fsync(output.fileno())
        after = os.fstat(source.fileno())
    if (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('複製時來源已變更：' + str(path))
    if copied != expected['size'] or h.hexdigest() != expected['sha256']:
        raise ValueError('來源內容與預覽不同，已保留未完成副本：' + str(path))
    _same_root(root, identity); _hash(destination, expected)
    done['files'] += 1; done['bytes'] += copied


def _write_descriptors(row, proc, absolute=False):
    target = proc / _relative(row['relative'])
    x = ET.Element('imageFile')
    name = str(target) if absolute else target.name
    for key, value in dict(file_name=name, extra_file_name=name+'.vrt', width=row['width'], length=row['height'],
                           number_bands=row['bands'], data_type=KINDS[row['kind']][1], scheme=row['scheme'], byte_order='l', access_mode='READ').items():
        p = ET.SubElement(x, 'property', name=key); ET.SubElement(p, 'value').text = str(value)
    for key, count in [('coordinate1', row['width']), ('coordinate2', row['height'])]:
        c = _component(x, key, 'isceobj.Image', 'createCoordinate')
        for k, value in dict(size=count, startingvalue=0, delta=1, endingvalue=count).items(): _prop(c, k, value)
    ET.ElementTree(x).write(str(target)+'.xml', encoding='utf-8', xml_declaration=True)
    v = ET.Element('VRTDataset', rasterXSize=str(row['width']), rasterYSize=str(row['height']))
    for i, offsets in enumerate(row['offsets'], 1):
        b = ET.SubElement(v, 'VRTRasterBand', dataType=row['kind'], band=str(i), subClass='VRTRawRasterBand')
        ET.SubElement(b, 'SourceFilename', relativeToVRT='1').text = target.name
        for key, value in zip(('ImageOffset', 'PixelOffset', 'LineOffset'), offsets): ET.SubElement(b, key).text = str(value)
        ET.SubElement(b, 'ByteOrder').text = 'LSB'
    ET.ElementTree(v).write(str(target)+'.vrt', encoding='utf-8', xml_declaration=True)


def _files(root, ignored=()):
    """No links, other mounts, special files, duplicate case-folded names."""
    root = Path(root); device = root.stat().st_dev; result = []; folded = set()
    pending = [root]
    while pending:
        folder = pending.pop()
        with directory(folder) as fd:
            with os.scandir(fd) as entries:
                for entry in entries:
                    relative = (folder / entry.name).relative_to(root).as_posix()
                    if relative in ignored: continue
                    info = entry.stat(follow_symlinks=False)
                    if entry.name == '.DS_Store' and stat.S_ISREG(info.st_mode) and info.st_nlink == 1: continue
                    if info.st_dev != device or stat.S_ISLNK(info.st_mode):
                        raise ValueError('資料包不能含連結或其他掛載磁碟：' + relative)
                    if relative.casefold() in folded: raise ValueError('資料包內有大小寫衝突的路徑。')
                    folded.add(relative.casefold())
                    if stat.S_ISDIR(info.st_mode): pending.append(folder / entry.name)
                    elif stat.S_ISREG(info.st_mode):
                        if info.st_nlink != 1: raise ValueError('資料包內容必須是獨立檔案，不能是硬連結。')
                        result.append(relative)
                    else: raise ValueError('資料包包含特殊檔案。')
                    if len(folded) > MAX_FILES * 2: raise ValueError('資料包檔案數量超過上限。')
    if len(result) > MAX_FILES: raise ValueError('資料包檔案數量超過上限。')
    return sorted(result)


def _publish_folder(staging, destination, parent_identity):
    """Atomic rename with no replacement, including an empty destination."""
    if _identity(destination.parent) != parent_identity: raise ValueError('目的地父資料夾已改變。')
    library = ctypes.CDLL(None, use_errno=True)
    with directory(destination.parent) as fd:
        if sys.platform == 'darwin':
            function = library.renameatx_np; flag = 4  # RENAME_EXCL
        elif sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
            function = library.renameat2; flag = 1  # RENAME_NOREPLACE
        else: raise ValueError('此系統缺少不覆寫目的地的原子發布功能。')
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(fd, os.fsencode(staging.name), fd, os.fsencode(destination.name), flag):
            code = ctypes.get_errno(); raise OSError(code, '資料包尚未發布，目的位置可能已存在或無法寫入', str(destination))


def export_package(state, preview, progress=None):
    report, target = _inspect_export(state, preview['destination'], preview.get('environment'))
    if report['fingerprint'] != preview['legacy_fingerprint'] or _state_record(state) != preview['provenance'] or _identity(Path(report['source'])) != preview['source_identity']:
        raise ValueError('來源或專案紀錄已變更，請重新預覽。')
    _assert_parent(preview)
    for row in preview['sources']: _hash(row['target'], row)
    destination = target
    target = destination.parent / ('.sar-portable-' + uuid.uuid4().hex)
    ident = _mkdir(target); atomic_json(target / INCOMPLETE, {'format':FORMAT, 'status':'copying'})
    _notify(progress, files_done=0, bytes_done=0, current='已建立未完成副本的位置', staging_path=str(target))
    done = dict(files=0, bytes=0)
    lookup = {row['path']: row for row in preview['sources']}
    proc = target / 'proc'; proc.mkdir()
    for row in report['rasters']:
        source = lookup[row['relative']]
        _copy(source['target'], proc / row['relative'], source, progress, done, target, ident)
        _write_descriptors(row, proc)
    for relative, content in report['metadata'].items():
        _same_root(target, ident); path = proc / _relative(relative); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    atomic_json(target / 'provenance.json', preview['provenance'])
    for row in preview['sources']: _hash(row['target'], row)
    files = [dict(path=p, **_hash(target/p)) for p in _files(target, (INCOMPLETE,))]
    manifest = dict(format=FORMAT, package_id=uuid.uuid4().hex, status='complete', created=time.strftime('%Y-%m-%d %H:%M:%S'),
                    purpose='alosStack QC and fresh MintPy analysis', excluded_by_scope=list(EXCLUDED_BY_SCOPE), reference=report['reference'], dates=report['dates'], pairs=report['pairs'],
                    width=report['width'], height=report['height'], looks=report['looks'], sensor=report['sensor'],
                    files=files, bytes=sum(r['size'] for r in files), warnings=list(WARNINGS))
    _same_root(target, ident)
    # A marker published last is the sole acceptance signal. A killed copy has
    # no manifest and cannot be imported or mistaken for a workbench project.
    (target / INCOMPLETE).unlink()
    atomic_json(target / MANIFEST, manifest)
    _publish_folder(target, destination, preview['parent_identity'])
    target = destination
    _notify(progress, files_done=len(files), bytes_done=manifest['bytes'], current='資料包完整驗證通過')
    return dict(source=preview['source'], root=str(target), destination=str(target), package_id=manifest['package_id'],
                files=len(files), bytes=manifest['bytes'], status='complete', warnings=list(WARNINGS))


def _verify_package(source, progress=None):
    raw = Path(source).expanduser()
    if raw.is_symlink(): raise ValueError('請選擇資料包本身，不能使用符號連結。')
    source = raw.resolve(strict=True); _identity(source)
    if (source / INCOMPLETE).exists() or (source / INCOMPLETE).is_symlink(): raise ValueError('資料包尚未完成，請重新建立完整資料包。')
    value = _json(source / MANIFEST)
    if value.get('format') != FORMAT or value.get('status') != 'complete' or not re.fullmatch(r'[0-9a-f]{32}', str(value.get('package_id', ''))):
        raise ValueError('不是支援的完整 alosStack 可攜分析包。')
    rows = value.get('files')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES: raise ValueError('資料包檔案清單不合法。')
    expected = {}; total = 0
    for row in rows:
        if not isinstance(row, dict): raise ValueError('資料包檔案紀錄不合法。')
        relative = _relative(row.get('path')).as_posix()
        if relative == MANIFEST or relative == INCOMPLETE or relative in expected:
            raise ValueError('資料包含重複或保留路徑。')
        if type(row.get('size')) is not int or row['size'] < 0 or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256', ''))):
            raise ValueError('資料包檔案大小或雜湊不合法。')
        expected[relative] = row; total += row['size']
    if type(value.get('bytes')) is not int or value['bytes'] != total: raise ValueError('資料包總容量與檔案清單不符。')
    actual = _files(source, (MANIFEST,))
    if sorted(expected) != actual: raise ValueError('資料包檔案清單不完整，或含未列出的檔案。')
    done = 0
    for i, relative in enumerate(actual, 1):
        _hash(source / relative, expected[relative]); done += expected[relative]['size']
        _notify(progress, files_done=i, bytes_done=done, current='驗證 · ' + relative)
    return source, value


def inspect_package(request):
    source, manifest = _verify_package(request.get('source', ''))
    env = request.get('environment')
    target = _destination(request.get('root'), source, env)
    report = inspect_legacy(dict(source=str(source/'proc'), root=str(target), environment=env,
                                 name=request.get('name'), reference=manifest.get('reference')))
    semantic = {k: report[k] for k in ('reference','dates','pairs','width','height','looks','sensor')}
    if any(manifest.get(k) != value for k, value in semantic.items()):
        raise ValueError('資料包摘要與實際成果不一致。')
    expected = {'provenance.json'} | {'proc/'+r['relative'] for r in report['manifest']}
    if expected != {r['path'] for r in manifest['files']}:
        raise ValueError('資料包含用途以外的內容；只接受標準成果與靜態來源紀錄。')
    provenance = _json(source/'provenance.json')
    free = shutil.disk_usage(target.parent).free; total = manifest['bytes']
    return dict(format=FORMAT, operation='import', source=str(source), root=str(target), destination=str(target),
                environment=report['environment'], conda=report['conda'], name=report['name'],
                package_id=manifest['package_id'], package_manifest_sha256=_hash(source/MANIFEST)['sha256'],
                source_identity=_identity(source), parent_identity=_identity(target.parent),
                **semantic, files=len(manifest['files']), bytes=total, required=total+RESERVE, free=free, space_ok=free>=total+RESERVE,
                warnings=list(WARNINGS), sources=manifest['files'], rasters=report['rasters'], metadata=report['metadata'],
                provenance=provenance)


def import_package(request, preview, key, progress=None):
    if not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{12}', key): raise ValueError('無效的新專案代號。')
    current = inspect_package(request)
    for field in ('source','root','environment','name','source_identity','parent_identity','package_manifest_sha256'):
        if current[field] != preview[field]: raise ValueError('資料包或匯入設定已變更，請重新預覽。')
    target = _assert_parent(preview); source = Path(preview['source'])
    ident = _mkdir(target); meta = target/'.helper'; meta.mkdir(); proc = target/'proc'; proc.mkdir()
    atomic_json(target/INCOMPLETE, dict(format=FORMAT, status='importing'))
    done = dict(files=0, bytes=0); lookup = {r['path']:r for r in current['sources']}
    for row in current['rasters']:
        rel = 'proc/'+row['relative']; expected = lookup[rel]
        _copy(source/rel, proc/row['relative'], expected, progress, done, target, ident)
        _write_descriptors(row, proc, absolute=True)
    for relative, content in current['metadata'].items():
        _same_root(target, ident); path = proc/_relative(relative); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    atomic_json(meta/'portable-provenance.json', current['provenance'])
    _verify_package(source)
    if _hash(source/MANIFEST)['sha256'] != current['package_manifest_sha256']:
        raise ValueError('匯入期間資料包紀錄已變更，未加入專案。')
    payload = [dict(path=relative, **_hash(proc/relative), check=_snapshot(proc/relative,proc,Path(relative).suffix in ('.xml','.vrt','.txt'))) for relative in _files(proc)]
    receipt = dict(format=FORMAT, package_id=current['package_id'], imported_at=time.strftime('%Y-%m-%d %H:%M:%S'),
                   files=payload, bytes=sum(r['size'] for r in payload), full_image_hash=True)
    atomic_json(meta/RECEIPT, receipt)
    config = dict(name=current['name'], workflow='alosStack', root=str(target), env=current['environment'], conda=current['conda'],
                  reference=current['reference'], source=str(proc), data=str(proc),
                  properties={'polarization':current['sensor']['polarization']}, import_only=True)
    state = dict(id=key, config=config, status='imported', stage='可攜成果已匯入，請先檢查品質', import_only=True,
                 dates=[dict(date=d) for d in current['dates']], expected_pairs=current['pairs'], history=[], excluded=[], recovery_enabled=False,
                 ion=dict(estimate=None, apply=None, source='unknown'),
                 portable_import=dict(format=FORMAT, package_id=current['package_id'], imported_at=receipt['imported_at'],
                                      receipt_sha256=_hash(meta/RECEIPT)['sha256'], provenance_sha256=_hash(meta/'portable-provenance.json')['sha256'], warnings=list(WARNINGS)))
    _same_root(target, ident)
    # State is the only registration candidate. Publish only after independent
    # copies, metadata and source/package verification have all completed.
    (target/INCOMPLETE).unlink()
    atomic_json(meta/'state.json', state)
    _notify(progress, files_done=len(payload), bytes_done=receipt['bytes'], current='獨立專案建立完成，等待品質檢查')
    return state


def require_portable_source(state):
    """A copied wrapper needs no old package or original machine to run QC."""
    record = state.get('portable_import')
    if not isinstance(record, dict) or record.get('format') != FORMAT: raise ValueError('可攜專案識別不合法。')
    root = Path(state['config']['root']); proc = root/'proc'; receipt_path = root/'.helper'/RECEIPT
    if (root/INCOMPLETE).exists(): raise ValueError('可攜專案尚未匯入完成。')
    if _hash(receipt_path)['sha256'] != record.get('receipt_sha256'):
        raise ValueError('可攜專案驗證紀錄已改變，請重新匯入。')
    receipt = _json(receipt_path)
    if receipt.get('format') != FORMAT or receipt.get('package_id') != record.get('package_id'):
        raise ValueError('可攜專案來源紀錄不符。')
    rows = receipt.get('files')
    if not isinstance(rows, list) or not 1<=len(rows)<=MAX_FILES: raise ValueError('可攜專案檔案清單不合法。')
    expected = {_relative(r['path']).as_posix():r for r in rows}
    if len(expected)!=len(rows) or sorted(expected)!=_files(proc): raise ValueError('可攜專案成果清單已改變。')
    for relative,row in expected.items():
        now = _snapshot(proc/relative,proc,Path(relative).suffix in ('.xml','.vrt','.txt'))
        if now != row.get('check'): raise ValueError('可攜專案成果或描述資料已變更，請重新匯入：' + relative)


def read_provenance(state):
    """Read the inert record bound to this import; never interpret it as settings."""
    record = state.get('portable_import')
    if not isinstance(record, dict) or record.get('format') != FORMAT:
        raise ValueError('此專案不是可攜分析包匯入的專案。')
    path = Path(state['config']['root'])/'.helper/portable-provenance.json'
    value = _json(path)
    if _hash(path)['sha256'] != record.get('provenance_sha256'):
        raise ValueError('可攜專案的來源與歷史紀錄已改變，不能當成原始匯入紀錄。')
    if not isinstance(value.get('source_config'), dict) or not isinstance(value.get('history'), list):
        raise ValueError('可攜專案來源與歷史紀錄格式不合法。')
    return value
