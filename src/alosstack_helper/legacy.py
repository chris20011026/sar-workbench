"""Read-only adoption of completed, manually produced alosStack results.

The source is never an executable project: scripts, shelve and pickle files are
not opened.  Raster bytes stay at the source; descriptors are rebuilt in a new
workspace, including fixed, inert ISCE metadata for MintPy's ProductManager.
"""
from pathlib import Path
import ast
import datetime
import hashlib
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET

from .core import atomic_json, find_conda
from .workflows import network

FORMAT = 'alosstack-readonly-v1'
MAX_XML = 8 * 1024**2
MAX_FILES = 20000
PAIR = re.compile(r'\d{6}-\d{6}')
SUFFIX = re.compile(r'_(\d+)rlks_(\d+)alks')
KINDS = {'Float32': (4, 'FLOAT'), 'Float64': (8, 'DOUBLE'), 'Byte': (1, 'BYTE')}


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{6}', value):
        raise ValueError('日期需使用 YYMMDD 格式。')
    datetime.datetime.strptime(value, '%y%m%d')
    return value


def _xml(path):
    if path.stat().st_size > MAX_XML:
        raise ValueError('描述檔超過大小限制：' + str(path))
    raw = path.read_bytes()
    try: text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as error: raise ValueError('只支援 UTF-8 XML 描述檔：' + str(path)) from error
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('不接受含 DTD／實體宣告的 XML：' + str(path))
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError('XML 格式不完整：' + str(path)) from error
    todo = [(root, 0)]; count = 0
    while todo:
        node, depth = todo.pop(); count += 1
        if depth > 18 or count > 30000 or not isinstance(node.tag, str) or '{' in node.tag:
            raise ValueError('不支援此 XML 結構：' + str(path))
        todo.extend((child, depth + 1) for child in node)
    return root


def _contained(path, source):
    """Only ordinary files/directories and links resolving inside this source."""
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(source):
            raise ValueError('來源連結指向處理資料夾以外：' + str(path))
        mode = resolved.stat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('不接受特殊檔案：' + str(path))
        return resolved
    except (OSError, RuntimeError) as error:
        raise ValueError('來源不存在、連結失效或磁碟未連線：' + str(path)) from error


def _snapshot(path, source, metadata=False):
    resolved = _contained(path, source); info = resolved.stat()
    if not stat.S_ISREG(info.st_mode): raise ValueError('預期為檔案：' + str(path))
    h = hashlib.sha256()
    with resolved.open('rb') as stream:
        if metadata:
            if info.st_size > MAX_XML: raise ValueError('描述資料過大：' + str(path))
            h.update(stream.read())
        else:
            # Deliberately a sample, not a claim of full-image integrity.
            for offset in sorted({0, max(0, info.st_size // 2 - 4096), max(0, info.st_size - 8192)}):
                stream.seek(offset); h.update(str(offset).encode() + b'\0' + stream.read(8192))
    after = resolved.stat()
    if (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev) != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev):
        raise ValueError('掃描時來源正在改變，請等待手動處理結束。')
    return dict(relative=str(path.relative_to(source)), target=str(resolved), size=info.st_size,
                mtime_ns=info.st_mtime_ns, inode=info.st_ino, device=info.st_dev,
                sha256=h.hexdigest(), hash_scope='full' if metadata else 'three-8KiB-samples')


def _literal(text):
    text = str(text or '').strip()
    try: value = ast.literal_eval(text)
    except (SyntaxError, ValueError): value = text
    if isinstance(value, float) and not math.isfinite(value): raise ValueError('描述資料含無效數值。')
    return value


def _props(node):
    values = {}
    for p in node.findall('property'):
        if set(p.attrib) != {'name'} or any(c.tag not in ('value', 'doc', 'units') for c in p):
            raise ValueError('不支援此 metadata 屬性結構。')
        name = p.get('name', '').lower().replace(' ', '')
        if name in values: raise ValueError('metadata 含重複屬性：' + name)
        children = p.findall('value')
        if len(children) > 1 or any(len(v) for v in children): raise ValueError('metadata 的值必須是純文字。')
        values[name] = _literal(children[0].text if children else p.text)
    return values


def _prop(parent, name, value):
    p = ET.SubElement(parent, 'property', name=name)
    ET.SubElement(p, 'value').text = repr(value)


def _component(parent, name, module, factory, args=None):
    c = ET.SubElement(parent, 'component', name=name)
    ET.SubElement(c, 'factorymodule').text = module
    ET.SubElement(c, 'factoryname').text = factory
    if args is not None: ET.SubElement(c, 'args').text = repr(args)
    return c


def _one_component(node, name):
    found = [c for c in node.findall('component') if c.get('name', '').lower().replace(' ', '') == name]
    if len(found) != 1: raise ValueError('缺少唯一的 metadata 元件：' + name)
    return found[0]


def _number(values, key, integer=False):
    raw = values.get(key)
    if isinstance(raw, bool): raise ValueError('metadata 數值不合法：' + key)
    try: value = float(raw)
    except (TypeError, ValueError) as error: raise ValueError('缺少有效的 metadata：' + key) from error
    if not math.isfinite(value) or value <= 0 or (integer and value != int(value)):
        raise ValueError('metadata 數值不合法：' + key)
    return int(value) if integer else value


def _time(value):
    try: result = datetime.datetime.fromisoformat(str(value))
    except ValueError as error: raise ValueError('metadata 時間格式不合法。') from error
    if result.tzinfo is not None: raise ValueError('此版本需要 ISCE 標準無時區 UTC 時間。')
    return result.isoformat(' ')


def _product(path, kind):
    """Rebuild only values needed by alosStack/MintPy, with fixed factories.

    Source factory strings are never imported. Catalogs, dynamic parser hooks,
    constants and any other executable configuration constructs are rejected.
    """
    raw = _xml(path)
    allowed = {'component', 'property', 'value', 'doc', 'units', 'factorymodule', 'factoryname', 'args', 'kwargs'}
    for n in raw.iter():
        if n is not raw and n.tag not in allowed: raise ValueError('metadata 含非靜態設定：' + n.tag)
        if n.tag in ('factorymodule', 'factoryname', 'args', 'kwargs') and len(n):
            raise ValueError('metadata factory 設定格式不支援。')
    source = _one_component(raw, 'instance'); values = _props(source)
    expected = ('isceobj.Sensor.MultiMode.' + kind, kind)
    if (source.findtext('factorymodule'), source.findtext('factoryname')) != expected:
        raise ValueError('只支援標準 ISCE2 ' + kind + ' metadata。')
    root = ET.Element('productmanager_name'); _prop(root, 'family', 'productmanager'); _prop(root, 'name', 'productmanager_name')
    dest = _component(root, 'instance', *expected); _prop(dest, 'family', kind.lower()); _prop(dest, 'name', kind.lower() + '_name')
    numeric = ['numberofsamples', 'numberoflines', 'startingrange', 'rangesamplingrate', 'rangepixelsize', 'pulserepetitionfrequency', 'azimuthpixelsize', 'azimuthlineinterval']
    for key in numeric: _prop(dest, key, _number(values, key, key.startswith('numberof')))
    sensing = _time(values.get('sensingstart')); _prop(dest, 'sensingstart', sensing)
    summary = {}
    if kind == 'Track':
        for key, choices in [('passdirection', ('ascending', 'descending')), ('pointingdirection', ('right', 'left'))]:
            val = str(values.get(key, '')).lower()
            if val not in choices: raise ValueError('缺少有效的 metadata：' + key)
            _prop(dest, key, val); summary[key] = val
        mode = str(values.get('operationmode', '')).upper()
        if not re.fullmatch(r'[A-Z0-9]{2,8}', mode): raise ValueError('無法辨識 ALOS 觀測模式。')
        _prop(dest, 'operationmode', mode); _prop(dest, 'radarwavelength', _number(values, 'radarwavelength'))
        orbit = _one_component(source, 'orbit'); seq = _one_component(orbit, 'state_vectors')
        vectors = seq.findall('component')
        if not 4 <= len(vectors) <= 10000: raise ValueError('track 需要至少四筆可讀軌道向量。')
        output = _component(dest, 'orbit', 'isceobj.Orbit.Orbit', 'createOrbit')
        outseq = _component(output, 'state_vectors', 'iscesys.Component', 'createTraitSeq', ('statevector',))
        names = []; times = []
        for i, item in enumerate(vectors, 1):
            row = _props(item); name = 'statevector' + str(i); names.append(name)
            out = _component(outseq, name, 'isceobj.Orbit.Orbit', 'StateVector')
            for key in ('position', 'velocity'):
                vec = row.get(key)
                if not isinstance(vec, (list, tuple)) or len(vec) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) for x in vec):
                    raise ValueError('軌道向量格式不合法：' + key)
                _prop(out, key, [float(x) for x in vec])
            t = _time(row.get('time')); times.append(t); _prop(out, 'time', t)
        if sorted(set(times)) != times: raise ValueError('軌道向量時間需依序排列且不能重複。')
        stop = datetime.datetime.fromisoformat(sensing) + datetime.timedelta(seconds=(_number(values, 'numberoflines', True) - 1) * _number(values, 'azimuthlineinterval'))
        if not times[0] <= sensing <= stop.isoformat(' ') <= times[-1]: raise ValueError('軌道向量未涵蓋影像起訖時間。')
        _trait(outseq, names, 'StateVector'); _prop(output, 'min_time', times[0]); _prop(output, 'max_time', times[-1])
        summary.update(mode=mode, wavelength=_number(values, 'radarwavelength'))
    else:
        frame = str(values.get('framenumber', ''))
        if not re.fullmatch(r'\d{4}', frame): raise ValueError('frame number 必須為四位數字。')
        _prop(dest, 'framenumber', frame)
        seq = _one_component(source, 'swaths'); swaths = seq.findall('component')
        if not 1 <= len(swaths) <= 10: raise ValueError('frame 缺少有效的 swath。')
        outseq = _component(dest, 'swaths', 'iscesys.Component', 'createTraitSeq', ('swath',)); names = []; polarizations = []
        for i, item in enumerate(swaths, 1):
            row = _props(item); name = 'swath' + str(i); names.append(name)
            out = _component(outseq, name, 'isceobj.Sensor.MultiMode.Swath', 'Swath')
            pol = str(row.get('polarization', '')).upper()
            if pol not in ('HH', 'HV', 'VH', 'VV'): raise ValueError('無法辨識極化。')
            polarizations.append(pol); _prop(out, 'polarization', pol)
            _prop(out, 'swathnumber', _number(row, 'swathnumber', True)); _prop(out, 'rangebandwidth', _number(row, 'rangebandwidth'))
        _trait(outseq, names, 'Swath'); summary.update(frame=frame, polarizations=sorted(set(polarizations)))
    return ET.tostring(root, encoding='unicode'), summary


def _trait(node, names, module):
    for key, value in dict(_factorname='default', _factory=True, _mandatory=False, _modulename=module, _private=True, family='TraitSeq', name=names).items():
        _prop(node, key, value)


def _raster(path, source, role):
    _contained(path, source)
    xmlpath, vrtpath = Path(str(path) + '.xml'), Path(str(path) + '.vrt')
    _contained(xmlpath, source); _contained(vrtpath, source)
    original = _xml(xmlpath)
    if original.tag != 'imageFile': raise ValueError('只支援 ISCE imageFile 描述檔：' + str(xmlpath))
    # Validate static syntax even though descriptors below are rebuilt.
    _props(original)
    v = _xml(vrtpath)
    if v.tag != 'VRTDataset' or any(n.tag not in ('VRTDataset', 'VRTRasterBand', 'SourceFilename', 'ImageOffset', 'PixelOffset', 'LineOffset', 'ByteOrder', 'NoDataValue', 'ColorInterp', 'Description') for n in v.iter()):
        raise ValueError('只支援原始影像 VRT，不能包含運算或外部資料集。')
    try: width, height = int(v.get('rasterXSize')), int(v.get('rasterYSize'))
    except (ValueError, TypeError) as error: raise ValueError('VRT 缺少影像尺寸。') from error
    if not 1 <= width <= 1000000 or not 1 <= height <= 1000000: raise ValueError('VRT 尺寸不合法。')
    bands = v.findall('VRTRasterBand'); spec = []; types = set()
    expected = {'unw': (2,), 'masked': (2,), 'coherence': (1, 2), 'components': (1,), 'hgt': (1,), 'lat': (1,), 'lon': (1,), 'los': (2,), 'wbd': (1,)}
    if len(bands) not in expected[role]: raise ValueError('影像波段數與用途不符：' + str(path))
    for band in bands:
        kind = band.get('dataType'); types.add(kind)
        if band.get('subClass') != 'VRTRawRasterBand' or kind not in KINDS: raise ValueError('不支援此 VRT 像素類型。')
        if band.findtext('ByteOrder', 'LSB') != 'LSB': raise ValueError('目前匯入只支援 little-endian 原始影像。')
        sources = band.findall('SourceFilename')
        if len(sources) != 1 or not sources[0].text: raise ValueError('VRT 缺少唯一影像來源。')
        name = Path(sources[0].text)
        if sources[0].get('relativeToVRT', '0') == '1': name = path.parent / name
        elif not name.is_absolute(): raise ValueError('VRT 相對路徑需標明 relativeToVRT。')
        if _contained(name, source) != path.resolve(): raise ValueError('VRT 指向不同影像：' + str(path))
        try: offsets = tuple(int(band.findtext(k)) for k in ('ImageOffset', 'PixelOffset', 'LineOffset'))
        except (ValueError, TypeError) as error: raise ValueError('VRT 位移值無效。') from error
        spec.append(offsets)
    if len(types) != 1: raise ValueError('同一影像的波段類型需一致。')
    kind = types.pop(); size = KINDS[kind][0]; count = len(bands)
    required_types = {'unw': ('Float32',), 'masked': ('Float32',), 'coherence': ('Float32',), 'components': ('Byte',), 'hgt': ('Float32', 'Float64'), 'lat': ('Float64',), 'lon': ('Float64',), 'los': ('Float32',), 'wbd': ('Byte',)}
    if kind not in required_types[role]: raise ValueError('影像資料型別與用途不符：' + str(path))
    schemes = {'BIL': [(i * width * size, size, width * size * count) for i in range(count)],
               'BSQ': [(i * width * height * size, size, width * size) for i in range(count)],
               'BIP': [(i * size, size * count, width * size * count) for i in range(count)]}
    scheme = next((k for k, values in schemes.items() if values == spec), None)
    if scheme is None or path.stat().st_size != width * height * size * count:
        raise ValueError('影像大小或波段排列與 VRT 不符：' + str(path))
    old = _props(original)
    for key, expected_value in [('width', width), ('length', height), ('number_bands', count), ('bands', count)]:
        if key in old and old[key] != expected_value: raise ValueError('XML 與 VRT 的尺寸／波段資料不一致：' + str(path))
    return dict(relative=str(path.relative_to(source)), role=role, width=width, height=height, kind=kind, bands=count, scheme=scheme,
                offsets=spec, snapshots=[_snapshot(path, source), _snapshot(xmlpath, source, True), _snapshot(vrtpath, source, True)])


def inspect_legacy(data):
    for key in ('source', 'root', 'environment'):
        if not isinstance(data.get(key), str) or not data[key].strip(): raise ValueError('請選擇來源、獨立工作資料夾與分析環境。')
    source = Path(data['source']).expanduser().resolve(strict=True)
    if not (source / 'pairs').is_dir() and (source / 'proc/pairs').is_dir(): source = source / 'proc'
    if not (source / 'pairs').is_dir() or not (source / 'dates_resampled').is_dir():
        raise ValueError('目前只支援含 pairs、dates_resampled、baseline 的 alosStack 成果；TOPS／stripmap／雙日期格式尚未開放此匯入。')
    root = Path(data['root']).expanduser().absolute()
    if not root.parent.is_dir() or root.parent.is_symlink(): raise ValueError('請先選擇已存在的父資料夾，再輸入新的工作資料夾名稱。')
    root = root.parent.resolve() / root.name
    if root.exists() or root.is_symlink(): raise ValueError('請選擇尚不存在的新資料夾；不接管或覆寫任何既有檔案。')
    if root.is_relative_to(source) or source.is_relative_to(root): raise ValueError('工作資料夾與來源成果不可互相包含。')
    env = Path(data['environment']).expanduser().resolve(strict=True)
    if not (env / 'bin/python').is_file() or not find_conda(env): raise ValueError('請選擇含 Python 的 ISCE2／MintPy Conda 環境。')
    name = str(data.get('name') or root.name).strip()
    if not name or len(name) > 100: raise ValueError('專案名稱請限制在 1–100 字。')
    pairdir = source / 'pairs'; _contained(pairdir, source)
    pairs = sorted(p.name for p in pairdir.iterdir() if PAIR.fullmatch(p.name))
    if not pairs or len(pairs) > MAX_FILES // 12: raise ValueError('找不到配對，或配對數量超過目前匯入上限。')
    for pair in pairs:
        a, b = pair.split('-'); _date(a); _date(b)
        if a >= b: raise ValueError('配對日期需由早至晚，不能是同一天：' + pair)
    net = network(pairs)
    if not net['connected']: raise ValueError('配對網路不連通，請先整理為一個連通的成果資料夾。')
    rasters = []; suffix = None; shape = None
    for pair in pairs:
        folder = source / 'pairs' / pair / 'insar'; _contained(folder, source)
        choices = [f for f in folder.glob('filt_' + pair + '_*rlks_*alks.unw') if re.fullmatch('filt_' + pair + r'_(\d+)rlks_(\d+)alks\.unw', f.name)]
        if len(choices) != 1: raise ValueError('無法唯一辨識解纏版本（請保留單一 looks 版本）：' + pair)
        match = re.fullmatch('filt_' + pair + r'(_\d+rlks_\d+alks)\.unw', choices[0].name); current = match[1]
        if any(int(v) < 1 for v in SUFFIX.fullmatch(current).groups()): raise ValueError('looks 必須大於零。')
        if suffix is not None and suffix != current: raise ValueError('各配對的 looks 不一致。')
        suffix = current
        for role, filename in [('unw', f'filt_{pair}{suffix}.unw'), ('masked', f'filt_{pair}{suffix}_msk.unw'), ('coherence', f'{pair}{suffix}.cor'), ('components', f'filt_{pair}{suffix}.unw.conncomp')]:
            rasters.append(_raster(folder / filename, source, role))
    _contained(source / 'dates_resampled', source)
    candidates = sorted(d.name for d in (source / 'dates_resampled').iterdir() if re.fullmatch(r'\d{6}', d.name)
                        and (d / (d.name + '.track.xml')).is_file() and all((d / 'insar' / (d.name + suffix + '.' + ext)).is_file() for ext in ('hgt', 'lat', 'lon', 'los', 'wbd')))
    reference = str(data.get('reference') or '')
    if not reference:
        if len(candidates) != 1: raise ValueError('無法唯一判斷參考日期，請填入其中一個完整幾何日期：' + '、'.join(candidates))
        reference = candidates[0]
    if reference not in candidates or reference not in net['dates']: raise ValueError('參考日期需位於配對網路，且具有完整幾何資料。')
    geom = source / 'dates_resampled' / reference / 'insar'
    for role in ('hgt', 'lat', 'lon', 'los', 'wbd'): rasters.append(_raster(geom / (reference + suffix + '.' + role), source, role))
    for row in rasters:
        wh = row['width'], row['height']
        if shape is not None and shape != wh: raise ValueError('配對與幾何影像尺寸不一致。')
        shape = wh
    if min(shape) < 8: raise ValueError('目前 MintPy 的 alosStack metadata 讀取需要影像各邊至少 8 像素。')
    base = geom.parent; track = base / (reference + '.track.xml'); _contained(track, source)
    clean, sensor = _product(track, 'Track'); metadata = {str(track.relative_to(source)): clean}
    snapshots = [snapshot for row in rasters for snapshot in row.pop('snapshots')]
    snapshots.append(_snapshot(track, source, True))
    frames = sorted(base.glob('f*_*/' + reference + '.frame.xml'))
    if not frames: raise ValueError('缺少標準 f序號_影格／日期.frame.xml。')
    polarizations = set()
    for frame in frames:
        if not re.fullmatch(r'f\d+_\d{4}', frame.parent.name): raise ValueError('frame 資料夾名稱不符合 alosStack 標準。')
        _contained(frame, source); clean, info = _product(frame, 'Frame')
        if info['frame'] != frame.parent.name.split('_')[1]: raise ValueError('frame number 與資料夾名稱不一致。')
        metadata[str(frame.relative_to(source))] = clean; snapshots.append(_snapshot(frame, source, True)); polarizations.update(info['polarizations'])
    if len(polarizations) != 1: raise ValueError('參考 frame 的極化不一致。')
    baseline = source / 'baseline/baseline_center.txt'; _contained(baseline, source); snapshots.append(_snapshot(baseline, source, True))
    lines = [line for line in baseline.read_text().splitlines() if line.strip()]; entries = {}; baseref = set()
    for line in lines[2:]:
        cols = line.split()
        if len(cols) != 4: raise ValueError('baseline_center.txt 欄位不符標準 alosStack 格式。')
        a, b = map(_date, cols[:2]); vals = [float(v) for v in cols[2:]]
        if any(not math.isfinite(v) for v in vals) or b in entries: raise ValueError('baseline 包含無效或重複數值。')
        baseref.add(a); entries[b] = vals
    if len(baseref) != 1 or not set(net['dates']).issubset(set(entries) | baseref): raise ValueError('baseline 未完整涵蓋配對日期。')
    base_reference = next(iter(baseref))
    if base_reference != reference: raise ValueError('baseline 參考日期與幾何參考日期不一致。')
    metadata['baseline/baseline_center.txt'] = 'reference secondary parallel perpendicular\nYYMMDD YYMMDD meters meters\n' + ''.join(f'{reference} {day} {a:.17g} {b:.17g}\n' for day, (a, b) in sorted(entries.items()))
    snapshots.sort(key=lambda r: r['relative']); source_stat = source.stat()
    result = dict(format=FORMAT, source=str(source), root=str(root), environment=str(env), conda=find_conda(env), name=name,
                  reference=reference, pairs=pairs, dates=net['dates'], network=net, looks=suffix, width=shape[0], height=shape[1],
                  sensor={**sensor, 'polarization':next(iter(polarizations))}, rasters=rasters, metadata=metadata, manifest=snapshots,
                  source_identity=dict(device=source_stat.st_dev, inode=source_stat.st_ino), source_bytes=sum(x['size'] for x in snapshots),
                  warnings=['原始處理、解纏與電離層校正的執行歷史未知；匯入不代表科學品質已驗證。', '大型影像仍依賴來源硬碟；新工作資料夾不是完整備份。', '影像使用大小、時間與三段抽樣雜湊檢查變動；沒有計算整幅影像雜湊。'])
    if len(net['dates']) < 3: result['warnings'].append('目前僅兩個日期，可做品質檢查；MintPy 時序至少需要三個日期。')
    result['fingerprint'] = hashlib.sha256(json.dumps({k: result[k] for k in ('source', 'source_identity', 'manifest', 'pairs', 'reference', 'looks', 'metadata')}, sort_keys=True).encode()).hexdigest()
    return result


def _write_raster(row, source, proc):
    target = proc / row['relative']; target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to((source / row['relative']).resolve(strict=True))
    x = ET.Element('imageFile')
    # MintPy also reads these XML values directly (without Python literals).
    # Strings here are our fixed enums or absolute paths, never source values.
    for key, value in dict(file_name=str(target), extra_file_name=str(target)+'.vrt', width=row['width'], length=row['height'], number_bands=row['bands'], data_type=KINDS[row['kind']][1], scheme=row['scheme'], byte_order='l', access_mode='READ').items():
        p = ET.SubElement(x, 'property', name=key); ET.SubElement(p, 'value').text = str(value)
    for name, count in [('coordinate1', row['width']), ('coordinate2', row['height'])]:
        c = _component(x, name, 'isceobj.Image', 'createCoordinate')
        for key, value in dict(size=count, startingvalue=0, delta=1, endingvalue=count).items(): _prop(c, key, value)
    ET.ElementTree(x).write(str(target)+'.xml', encoding='utf-8', xml_declaration=True)
    v = ET.Element('VRTDataset', rasterXSize=str(row['width']), rasterYSize=str(row['height']))
    for i, offsets in enumerate(row['offsets'], 1):
        b = ET.SubElement(v, 'VRTRasterBand', dataType=row['kind'], band=str(i), subClass='VRTRawRasterBand')
        ET.SubElement(b, 'SourceFilename', relativeToVRT='1').text = target.name
        for key, val in zip(('ImageOffset', 'PixelOffset', 'LineOffset'), offsets): ET.SubElement(b, key).text = str(val)
        ET.SubElement(b, 'ByteOrder').text = 'LSB'
    ET.ElementTree(v).write(str(target)+'.vrt', encoding='utf-8', xml_declaration=True)


def create_wrapper(request, preview, key):
    current = inspect_legacy(request)
    if current['fingerprint'] != preview['fingerprint'] or any(current[k] != preview[k] for k in ('root','source','environment','name')):
        raise ValueError('來源內容、位置或匯入設定已變更，請重新預覽後再匯入。')
    source, root = Path(current['source']), Path(current['root'])
    # This mkdir is exclusive and is the only source-independent tree we own.
    root.mkdir(exist_ok=False); meta = root / '.helper'; meta.mkdir(); (root / 'proc').mkdir()
    atomic_json(meta / 'legacy-import-incomplete.json', dict(format=FORMAT, project_id=key, source=str(source)))
    for row in current['rasters']: _write_raster(row, source, root / 'proc')
    for relative, content in current['metadata'].items():
        target = root / 'proc' / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content)
    # The source must still match after descriptors were staged; no science was run.
    verify_request = dict(request, root=str(root.parent / ('.legacy-validation-' + uuid.uuid4().hex)))
    if inspect_legacy(verify_request)['fingerprint'] != current['fingerprint']:
        raise ValueError('建立連結時來源內容改變，已保留未完成資料夾，未加入專案清單。')
    provenance = {k: current[k] for k in ('format','source','source_identity','fingerprint','manifest','warnings','sensor')}
    provenance['wrapper_metadata'] = {str(f.relative_to(root / 'proc')):hashlib.sha256(f.read_bytes()).hexdigest()
                                      for f in (root / 'proc').rglob('*') if f.is_file() and not f.is_symlink()}
    provenance['wrapper_rasters'] = {row['relative']:str((source / row['relative']).resolve()) for row in current['rasters']}
    provenance.update(imported_at=time.strftime('%Y-%m-%d %H:%M:%S'), processing_history='unknown', ionosphere_history='unknown', full_image_hash=False)
    atomic_json(meta / 'legacy-source.json', provenance)
    config = dict(name=current['name'], workflow='alosStack', root=str(root), env=current['environment'], conda=current['conda'],
                  reference=current['reference'], source=str(source), data=str(source), properties={'polarization':current['sensor']['polarization']}, import_only=True)
    state = dict(id=key, config=config, status='imported', stage='既有成果已匯入，請先檢查品質', import_only=True,
                 dates=[dict(date=d) for d in current['dates']], expected_pairs=current['pairs'], history=[], excluded=[], recovery_enabled=False,
                 ion=dict(estimate=None, apply=None, source='unknown'), legacy_import={k:provenance[k] for k in ('format','source','fingerprint','imported_at','processing_history','ionosphere_history','warnings')})
    atomic_json(meta / 'state.json', state); (meta / 'legacy-import-incomplete.json').unlink()
    return state


def require_legacy_source(state):
    """Check the import receipt before any new QC/MintPy run; never write source."""
    if not state.get('import_only'): return
    if state.get('portable_import'):
        from .portable import require_portable_source
        return require_portable_source(state)
    root = Path(state['config']['root']); receipt = root / '.helper/legacy-source.json'
    if receipt.is_symlink(): raise ValueError('匯入來源紀錄不能是連結。')
    saved = json.loads(receipt.read_text()); source = Path(saved['source'])
    proc = root / 'proc'
    if proc.is_symlink(): raise ValueError('匯入工作目錄不能改成連結。')
    for relative, target in saved['wrapper_rasters'].items():
        raster = proc / relative
        if not raster.is_symlink() or str(raster.resolve(strict=True)) != target:
            raise ValueError('匯入影像連結已改變，請重新匯入。')
    for relative, checksum in saved['wrapper_metadata'].items():
        path = proc / relative
        if not path.resolve(strict=True).is_relative_to(proc.resolve()) or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('匯入描述資料已改變，請重新匯入。')
    current = source.stat()
    if dict(device=current.st_dev, inode=current.st_ino) != saved['source_identity']:
        raise ValueError('來源硬碟或處理資料夾已更換，請重新匯入驗證。')
    for item in saved['manifest']:
        relative = Path(item['relative'])
        if relative.is_absolute() or '..' in relative.parts: raise ValueError('匯入來源紀錄路徑不合法。')
        now = _snapshot(source / relative, source, item['hash_scope'] == 'full')
        if now != item: raise ValueError('匯入後來源已變更或未連線，請重新匯入：' + str(relative))


class LegacyImports:
    """Cancellable subprocess scans, kept separate from the HTTP request thread."""
    def __init__(self, engine):
        self.engine = engine; self.home = engine.home / 'legacy-imports'; self.home.mkdir(exist_ok=True)
        self.lock = threading.RLock(); self.processes = {}; self.cancelled = set()

    def folder(self, token):
        if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{24}', token): raise ValueError('無效的匯入預覽代號。')
        return self.home / token

    def get(self, token):
        p = self.folder(token)
        if not (p / 'job.json').is_file(): raise ValueError('找不到匯入預覽。')
        job = json.loads((p / 'job.json').read_text())
        if job['status'] in ('running', 'importing') and token not in self.processes:
            job.update(status='interrupted', error='服務曾中斷；請重新預覽。若已有未完成工作資料夾，保留原樣並選擇新位置。')
        if (p / 'preview.json').is_file():
            preview = json.loads((p / 'preview.json').read_text())
            job['preview'] = {k:v for k,v in preview.items() if k not in ('rasters','metadata','manifest','conda')}
            job['preview']['file_count'] = len(preview['manifest'])
        return job

    def _reserve(self, token):
        if self.engine.active: raise ValueError('已有工作執行中，請等待完成再匯入成果。')
        self.engine.active = 'legacy:' + token

    def preview(self, data):
        with self.engine.lock, self.lock:
            token = uuid.uuid4().hex[:24]; self._reserve(token); p = self.folder(token)
            try:
                p.mkdir(); atomic_json(p / 'request.json', data)
                atomic_json(p / 'job.json', dict(preview_id=token, status='running', stage='檢查既有 alosStack 成果', created=time.time()))
                self.processes[token] = None
                threading.Thread(target=self._worker, args=(token, 'preview'), daemon=True).start()
            except Exception:
                self.engine.active = None; self.processes.pop(token, None); raise
        return self.get(token)

    def create(self, token, reviewed=False):
        with self.engine.lock, self.lock:
            job = self.get(token)
            if job['status'] != 'ready': raise ValueError('請先完成來源預覽。')
            if reviewed is not True: raise ValueError('請先確認來源依賴與未知的原始處理紀錄。')
            self._reserve(token); p = self.folder(token); job.pop('preview', None)
            job.update(status='importing', stage='再次核對來源並建立獨立工作資料夾', project_id=uuid.uuid4().hex[:12]); atomic_json(p / 'job.json', job)
            self.processes[token] = None
            threading.Thread(target=self._worker, args=(token, 'create'), daemon=True).start()
        return self.get(token)

    def stop(self, token):
        self.folder(token)
        with self.lock:
            if token not in self.processes: return self.get(token)
            self.cancelled.add(token); process = self.processes.get(token)
            if process and process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
        return self.get(token)

    def shutdown(self):
        with self.lock: active = list(self.processes)
        for token in active: self.stop(token)
        deadline = time.monotonic() + 4
        while self.processes and time.monotonic() < deadline: time.sleep(.05)
        with self.lock:
            for process in self.processes.values():
                if process and process.poll() is None:
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError: pass

    def _worker(self, token, mode):
        p = self.folder(token); process = None; reader = writer = None
        try:
            reader, writer = os.pipe()
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(Path(__file__).parent.parent))
            with (p / 'worker.log').open('a') as log:
                process = subprocess.Popen([sys.executable, '-m', 'alosstack_helper.legacy_worker', mode, str(p), str(reader)], env=env,
                                           stdout=log, stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(reader,))
                os.close(reader); reader = None
                with self.lock:
                    self.processes[token] = process
                    if token in self.cancelled: os.killpg(process.pid, signal.SIGTERM)
                code = process.wait(timeout=600)
            if token in self.cancelled: raise ValueError('已停止匯入；來源保持原樣。未完成的工作資料夾不會加入清單。')
            result = json.loads((p / 'result.json').read_text()) if (p / 'result.json').is_file() else {}
            if code or not result.get('ok'): raise ValueError(result.get('error') or '來源讀取中斷，請確認磁碟連線並重新預覽。')
            job = json.loads((p / 'job.json').read_text())
            if mode == 'create':
                state = json.loads((p / 'project.json').read_text())
                with self.lock:
                    if token in self.cancelled: raise ValueError('已停止匯入；保留產物，未加入清單。')
                    self.engine.register(state['id'], state['config']['root'])
                    job.update(status='complete', stage='已加入工作台，可開始品質檢查', project_id=state['id'])
            else: job.update(status='ready', stage='預覽完成，等待確認匯入')
        except Exception as error:
            if process and process.poll() is None:
                try: os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired): pass
            job = json.loads((p / 'job.json').read_text())
            job.update(status='stopped' if token in self.cancelled else 'failed', error=str(error), stage='需要檢查')
        finally:
            for fd in (reader, writer):
                if fd is not None: os.close(fd)
            try:
                with self.lock: atomic_json(p / 'job.json', job)
            finally:
                with self.lock: self.processes.pop(token, None); self.cancelled.discard(token)
                with self.engine.lock:
                    if self.engine.active == 'legacy:' + token: self.engine.active = None
