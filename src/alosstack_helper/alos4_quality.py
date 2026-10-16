"""Read-only PALSAR-3 conversion audit, run in the selected ISCE2 environment.

The entire complex sample stream is compared, not just a thumbnail or a sample.
Only documented CEOS descriptor/prefix changes are accepted. Reader validation
parses metadata with ISCE2; it never calls readImage(), which writes SLC headers.
Format compatibility is deliberately distinct from an InSAR science validation.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import math
import os
from pathlib import Path
import stat
import struct
import sys

try:
    from .alos4_conversion import MODES, original_output_name, product_files
except ImportError:  # The stand-alone scientific worker imports sibling modules.
    from alos4_conversion import MODES, original_output_name, product_files


CHUNK_BYTES = 8 * 1024 * 1024
METADATA_LIMIT = 32 * 1024 * 1024
SOURCE_LED_RECORDS = (720, 4680, 4680, 16384, 65536, 1620, 325000, 511000, 3072, 728000, 9500)
OUTPUT_LED_RECORDS = (720, 4096, 4680, 16384, 9860, 1620, 325000, 511000, 3072, 728000, 5000)
LIMITATIONS = [
    '通過只表示格式、完整複數樣本保留及 ISCE2 中繼資料可讀，未執行 SLC 擷取或干涉處理。',
    '未驗證跨衛星共同頻帶、影格對應、配準、相干性、電離層校正或地表位移準確性。',
    '輸出使用相容格式與合成影格編號；它仍是 ALOS-4 觀測，不能冒充原生 ALOS-2。',
]


class QualityError(ValueError):
    """An unsuccessful audit with a JSON-safe partial report for the job log."""
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _safe_path(path, directory=False):
    p = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    # Do not resolve first: doing so would erase evidence of a symlink.
    for part in [p, *p.parents]:
        _need(not part.is_symlink(), '檢查不接受符號連結：' + str(part))
    info = p.lstat()
    _need(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
          '不是一般資料夾或檔案：' + str(p))
    return p


def _identity(path):
    s = path.lstat()
    return dict(size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                device=s.st_dev, inode=s.st_ino, mode=s.st_mode)


@contextlib.contextmanager
def _open(path, expected):
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        s = os.fstat(stream.fileno())
        current = dict(size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                       device=s.st_dev, inode=s.st_ino, mode=s.st_mode)
        _need(current == expected, '檔案在開啟前改變：' + path.name)
        yield stream
        s = os.fstat(stream.fileno())
        current = dict(size=s.st_size, mtime_ns=s.st_mtime_ns, ctime_ns=s.st_ctime_ns,
                       device=s.st_dev, inode=s.st_ino, mode=s.st_mode)
        _need(current == expected, '檔案在讀取過程中改變：' + path.name)


def _integer(raw, field):
    try:
        value = raw.decode('ascii').strip()
        _need(value and value.lstrip('+-').isdigit(), field + ' 不是有效整數。')
        return int(value)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(field + ' 不是有效整數。') from exc


def _number(raw, field, positive=False):
    try:
        value = float(raw.decode('ascii').strip())
        _need(math.isfinite(value) and (not positive or value > 0), field + ' 不合法。')
        return value
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(field + ' 不是有效數值。') from exc


def _ascii(raw, field):
    try:
        return raw.decode('ascii').strip()
    except UnicodeDecodeError as exc:
        raise ValueError(field + ' 不是 ASCII 中繼資料。') from exc


def _date(value):
    _need(len(value) == 6 and value.isdigit(), '產品日期不合法。')
    return dt.datetime.strptime('20' + value, '%Y%m%d').date()


def _descriptor(data, prefix_bytes, size, name):
    _need(len(data) == 720, name + ' IMG descriptor 被截斷。')
    _need(struct.unpack_from('>I', data, 8)[0] == 720, name + ' IMG descriptor 長度不是 720。')
    integer = lambda a, b, field: _integer(data[a:b], name + ' ' + field)
    lines = integer(180, 186, '行數')
    record = integer(186, 192, '記錄長度')
    width = integer(248, 256, '寬度')
    _need(lines > 0 and width > 0 and 0 < record <= 999999, name + ' IMG 尺寸不合法。')
    required = [(216, 220, 32, '每樣本位元'), (220, 224, 2, 'I/Q 樣本數'),
                (224, 228, 8, '複數位元組'), (232, 236, 1, '通道數'),
                (236, 244, lines, '資料集行數'), (244, 248, 0, '左填充'),
                (256, 260, 0, '右填充'), (272, 274, 1, '每行記錄數'),
                (274, 276, 1, '多通道每行記錄數'),
                (276, 280, prefix_bytes, 'prefix 位元組'),
                (280, 288, width * 8, '樣本位元組'), (288, 292, 0, 'suffix 位元組')]
    for a, b, expected, field in required:
        _need(integer(a, b, field) == expected, name + ' IMG ' + field + ' 與支援格式不符。')
    _need(_ascii(data[428:432], '複數格式') == 'C*8', name + ' IMG 不是 C*8 複數格式。')
    _need(record == prefix_bytes + width * 8, name + ' IMG 記錄長度與寬度不符。')
    _need(size == 720 + lines * record, name + ' IMG 大小／行數不符，可能被截斷或有額外記錄。')
    return dict(width=width, lines=lines, record_bytes=record, prefix_bytes=prefix_bytes)


def _line_info(prefix, row, info, polarization, expected_date, name):
    unpack = lambda offset: struct.unpack_from('>I', prefix, offset)[0]
    _need(unpack(8) == info['record_bytes'] and unpack(24) == info['width'],
          f'{name} 第 {row+1} 行長度／資料像素數不符。')
    _need(unpack(12) == row + 1 and unpack(16) == 1 and unpack(20) == unpack(28) == 0,
          f'{name} 第 {row+1} 行順序或填充不符。')
    pols = struct.unpack_from('>HH', prefix, 52)
    _need(all(p in (0, 1) for p in pols), name + ' IMG 極化碼不合法。')
    pol = ''.join('HV'[p] for p in pols)
    _need(pol == polarization, name + ' IMG 極化與檔名不符。')
    year, day, ms = unpack(36), unpack(40), unpack(44)
    _need(2000 <= year <= 2100 and 1 <= day <= 366 and 0 <= ms < 86400000,
          f'{name} 第 {row+1} 行觀測日期／時間不合法。')
    time = dt.datetime(year, 1, 1) + dt.timedelta(days=day-1, milliseconds=ms)
    _need(time.year == year and abs((time.date() - expected_date).days) <= 1,
          f'{name} 第 {row+1} 行日期與產品日期不符。')
    return time


def _image(source, target, identities, expected_date, progress):
    pol = source.name[4:6]
    source_hash, target_hash = hashlib.sha256(), hashlib.sha256()
    source_samples, target_samples = hashlib.sha256(), hashlib.sha256()
    with _open(source, identities[source]) as src, _open(target, identities[target]) as dst:
        a, b = src.read(720), dst.read(720)
        first = _descriptor(a, 800, identities[source]['size'], source.name)
        second = _descriptor(b, 544, identities[target]['size'], target.name)
        _need((first['width'], first['lines']) == (second['width'], second['lines']), '轉換前後 IMG 尺寸不一致。')
        expected = bytearray(a)
        expected[48:64] = b'AL2 SARBIMOP    '
        expected[186:192] = str(second['record_bytes']).rjust(6).encode('ascii')
        expected[276:280] = b' 544'
        _need(bytes(expected) == b, target.name + ' IMG descriptor 有非預期的中繼資料變更。')
        source_hash.update(a); target_hash.update(b)
        count, last, beginning, checked, chunk = 0, None, None, 0, 0
        for row in range(first['lines']):
            a, b = src.read(800), dst.read(544)
            _need(len(a) == 800 and len(b) == 544, f'第 {row+1} 行 IMG prefix 被截斷。')
            when = _line_info(a, row, first, pol, expected_date, source.name)
            after = _line_info(b, row, second, pol, expected_date, target.name)
            _need(when == after and (last is None or when >= last), f'第 {row+1} 行觀測時間不一致或倒退。')
            beginning = beginning or when
            last = when
            expected = bytearray(a[:544])
            struct.pack_into('>I', expected, 8, second['record_bytes'])
            struct.pack_into('>I', expected, 32, 0)
            struct.pack_into('>I', expected, 116, struct.unpack_from('>I', a, 116)[0] // 100)
            _need(bytes(expected) == b, f'{target.name} 第 {row+1} 行 prefix 不是預期的 800→544 轉換。')
            source_hash.update(a); target_hash.update(b)
            # A single record is bounded by six ASCII length digits (< 1 MB).
            size = first['width'] * 8
            a, b = src.read(size), dst.read(size)
            _need(len(a) == size and len(b) == size, f'第 {row+1} 行複數樣本被截斷。')
            _need(a == b, f'{target.name} 第 {row+1} 行複數樣本與來源不一致。')
            source_hash.update(a); target_hash.update(b)
            source_samples.update(a); target_samples.update(b)
            count += first['width']; checked += size; chunk += size
            if chunk >= CHUNK_BYTES or row == first['lines'] - 1:
                progress(dict(phase='samples', file=source.name, completed=row+1,
                              total=first['lines'], bytes_checked=checked))
                chunk = 0
        _need(not src.read(1) and not dst.read(1), 'IMG 尾端有未核對的資料。')
    return dict(source=source.name, output=target.name, polarization=pol, **first,
                output_record_bytes=second['record_bytes'], output_prefix_bytes=544,
                complex_samples=count, sample_bytes=checked, all_samples_equal=True,
                source_sha256=source_hash.hexdigest(), output_sha256=target_hash.hexdigest(),
                source_samples_sha256=source_samples.hexdigest(), output_samples_sha256=target_samples.hexdigest(),
                first_acquisition=beginning.isoformat()+'Z', last_acquisition=last.isoformat()+'Z')


def _small_file(path, identities):
    _need(identities[path]['size'] <= METADATA_LIMIT, '中繼資料超過支援大小：' + path.name)
    with _open(path, identities[path]) as stream:
        data = stream.read(METADATA_LIMIT + 1)
    _need(len(data) == identities[path]['size'], '中繼資料讀取不完整：' + path.name)
    return data


def _records(data, lengths, name):
    _need(len(data) == sum(lengths), name + ' 檔案長度不符合已核對的 CEOS 版型。')
    offset, result = 0, []
    for size in lengths:
        record = data[offset:offset+size]
        _need(struct.unpack_from('>I', record, 8)[0] == size, name + ' CEOS record 長度不符。')
        result.append(record); offset += size
    return result


def _scene(record, source, date, look, node):
    timestamp = _ascii(record[68:100], 'LED 觀測時間')
    _need(len(timestamp) >= 14 and timestamp[:14].isdigit(), 'LED 觀測時間不完整。')
    acquired = dt.datetime.strptime(timestamp[:14], '%Y%m%d%H%M%S')
    _need(acquired.date() == date, 'LED 日期與來源產品檔名不符。')
    direction = _ascii(record[1822:1830] if source else record[1534:1542], '升降軌')
    _need(direction in ('ASCEND', 'DESCEND'), 'LED 升降軌欄位無法判讀。')
    _need(direction == ('ASCEND' if node == 'A' else 'DESCEND'), 'LED 升降軌與來源檔名不符。')
    angle = _number(record[476:484], '觀測方向角')
    _need(angle == (90.0 if look == 'R' else -90.0), 'LED 左右視與來源檔名不符。')
    level = _ascii(record[1094:1110], '產品級別')
    _need(level == '1.1', 'LED 不是 Level 1.1。')
    return dict(acquisition=acquired.isoformat()+'Z', date=date.strftime('%Y%m%d'),
                direction='ascending' if node == 'A' else 'descending',
                look='right' if look == 'R' else 'left', level=level,
                radar_wavelength_m=_number(record[500:516], '雷達波長', True),
                range_sampling_mhz=_number(record[710:726], '距離採樣率', True),
                prf_millihz=_number(record[934:950], '脈衝重複頻率 mHz', True),
                orbit=_ascii(record[444:452], '軌道圈次'))


def _metadata(sources, targets, identities, date, look, node, progress):
    raw = {}; fingerprints = {}
    image_count = len(sources) - 3
    expected_sizes = [(image_count+4)*360, sum(SOURCE_LED_RECORDS), 720,
                      (image_count+4)*360, sum(OUTPUT_LED_RECORDS), 720]
    for path, expected in zip([*sources[:3], *targets[:3]], expected_sizes):
        _need(identities[path]['size'] == expected, path.name + ' 檔案大小不符，可能被截斷或版型不支援。')
    for path in [*sources[:3], *targets[:3]]:
        raw[path] = _small_file(path, identities)
        fingerprints[path] = hashlib.sha256(raw[path]).hexdigest()
        progress(dict(phase='metadata', file=path.name, completed=identities[path]['size'], total=identities[path]['size']))
    left = _records(raw[sources[1]], SOURCE_LED_RECORDS, sources[1].name)
    right = _records(raw[targets[1]], OUTPUT_LED_RECORDS, targets[1].name)
    # These records are preserved in their entirety by JAXA's compatibility
    # mapping. Radiometry/facility-5 are intentionally shortened by the tool.
    for index in (2, 3, 5, 6, 7, 8, 9):
        _need(left[index] == right[index], 'LED 軌道／姿態／品質／設施記錄有非預期變更。')
    for index in (4, 10):
        expected = bytearray(left[index][:len(right[index])])
        struct.pack_into('>I', expected, 8, len(expected))
        _need(bytes(expected) == right[index], 'LED 截短記錄與來源對應內容不一致。')
    # Bound the upstream parser's variable-length orbit loop before invoking
    # it, and disallow its silent invalid-ASCII-to-zero fallback for vectors.
    orbit = right[2]
    vectors = _integer(orbit[140:144], '軌道向量數')
    _need(1 <= vectors <= (len(orbit)-386-598)//132, 'LED 軌道向量數超過記錄容量。')
    dt.date(_integer(orbit[144:148], '軌道年份'), _integer(orbit[148:152], '軌道月份'),
            _integer(orbit[152:156], '軌道日期'))
    _number(orbit[182:204], '軌道向量時間間隔', True)
    for index in range(vectors):
        for axis in range(6):
            offset = 386 + index*132 + axis*22
            _number(orbit[offset:offset+22], '軌道位置／速度向量')
    # Strictly compare the retained acquisition/radar fields; CEOS's generic
    # parser can substitute zero for malformed ASCII numbers.
    a, b = left[1], right[1]
    fields = [(68, 68, 32, '觀測時間'), (444, 444, 8, '軌道圈次'),
              (476, 476, 8, '左右視'), (500, 500, 16, '波長'),
              (710, 710, 16, '採樣率'), (934, 934, 16, 'PRF'),
              (1094, 1094, 16, '產品級別'), (1822, 1534, 8, '升降軌'),
              (2282, 1686, 16, '行間距'), (2298, 1702, 16, '像素間距')]
    for start_a, start_b, size, field in fields:
        _need(a[start_a:start_a+size] == b[start_b:start_b+size], 'LED 轉換前後 ' + field + ' 不一致。')
    before = _scene(a, True, date, look, node)
    after = _scene(b, False, date, look, node)
    _need(_ascii(a[396:412], '來源衛星') == 'ALOS4' and _ascii(b[396:412], '相容衛星') == 'ALOS2',
          'LED 衛星標記不符合 ALOS-4→ALOS-2 相容轉換。')
    identity = targets[1].name[4:].rsplit('-', 1)[0]
    _need(_ascii(b[20:68], '輸出場景編號').startswith(identity), 'LED 場景編號與轉換輸出檔名不符。')
    for path in (sources[0], targets[0]):
        _records(raw[path], [360] * (image_count+4), path.name)
    for path in (sources[2], targets[2]):
        _records(raw[path], [720], path.name)
    return dict(status='passed', source=before, output=after,
                checked_fields=[x[3] for x in fields] + ['IMG 全 descriptor 與全行 prefix', '完整軌道／姿態／品質／設施記錄', 'VOL／TRL 記錄長度'],
                limits=['LED 另有格式轉換改寫的欄位，不宣稱整份 LED 與原始位元組相同。',
                        '相容格式中的 radiometry 與 facility-5 依轉換工具規格截短；VOL／TRL 未用於 InSAR 科學判讀。']), fingerprints


def _reader(targets, expected_images, expected_scene, identities):
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import isce
            from isceobj.Sensor.MultiMode.ALOS2 import ALOS2
            try:
                from .metadata_worker import record
            except ImportError:
                from metadata_worker import record
            sensor = ALOS2()
            sensor.leaderFile = str(targets[1])
            result = sensor.readLeader()
            _need(result and len(result) == 4, 'ISCE2 readLeader 未傳回完整中繼資料。')
            leader, scene, orbit, facility = result
            _need(scene.metadata['Product level code'].strip() == '1.1', 'ISCE2 未識別 Level 1.1。')
            # readLeader's decoded CEOS field is part of upstream ISCE2.
            # getRangeSamplingRate exists only in some locally patched readers;
            # this metadata audit must also work with an unmodified installation.
            rate = float(scene.metadata['Range sampling rate in MHz']) * 1e6
            _need(math.isfinite(rate) and rate > 0 and math.isclose(rate, expected_scene['range_sampling_mhz'] * 1e6, rel_tol=1e-5),
                  'ISCE2 讀取的採樣率與來源不一致。')
            _need(0 < orbit.metadata['Number of data points'] <= 10000, 'ISCE2 讀取的軌道向量數不合法。')
            _need(len(orbit.metadata.get('Positional Data Points', [])) == orbit.metadata['Number of data points'], 'ISCE2 軌道向量不完整。')
            for point in orbit.metadata['Positional Data Points']:
                _need(all(math.isfinite(float(point[k])) for k in ('Position vector X', 'Position vector Y', 'Position vector Z',
                                                                 'Velocity vector X', 'Velocity vector Y', 'Velocity vector Z')), 'ISCE2 軌道向量含非有限數。')
            decoded = []
            for path, expected in zip(targets[3:], expected_images):
                with _open(path, identities[path]) as stream:
                    header, offset = record(stream, 'image_file.xml')
                    prefix, _ = record(stream, 'image_record.xml', True)
                _need(offset == 720 and header['Number of SAR DATA records'] == expected['lines'] and
                      header['Number of pixels per line per SAR channel'] == expected['width'] and
                      header['SAR DATA record length'] == expected['output_record_bytes'], 'ISCE2 讀取的 IMG 尺寸與完整逐行檢查不一致。')
                pol = ''.join('HV'[int(prefix[k])] for k in ('Transmitted polarization', 'Received polarization'))
                _need(pol == expected['polarization'], 'ISCE2 讀取的極化不一致。')
                decoded.append(dict(file=path.name, width=expected['width'], lines=expected['lines'], polarization=pol))
        return dict(status='passed', implementation='isceobj.Sensor.MultiMode.ALOS2.readLeader + CEOS.CEOSDB IMG decoder',
                    version=str(getattr(isce, '__version__', 'unknown')), range_sampling_hz=rate,
                    orbit_vectors=int(orbit.metadata['Number of data points']), images=decoded,
                    slc_extraction=False, metadata_only=True)
    except Exception as exc:
        raise ValueError('ISCE2 中繼資料讀取檢查失敗：' + str(exc)) from exc


def verify_conversion(source_product, output_directory, progress=None):
    """Verify one complete source product and its ALOS2_format directory.

    Runs in the selected ISCE2 environment. Inputs are never opened for writing.
    ``progress`` receives dictionaries and may raise to cancel. On any failure,
    ``QualityError.report`` contains the partial, explicitly unsuccessful audit.
    Full IMG content hashes are collected in the same pass as sample comparison;
    inode/size/mtime/ctime and directory identity are checked before and after.
    """
    report = dict(schema_version=1, status='checking', source_sensor='ALOS-4',
                  format={'status': 'pending'}, samples={'status': 'pending'},
                  metadata={'status': 'pending'}, reader={'status': 'pending'}, images=[],
                  fingerprints={'source': [], 'output': []},
                  scientific_validation={'status': 'not_performed', 'limitations': LIMITATIONS.copy()}, errors=[])
    progress = progress or (lambda event: None)
    identities = {}; roots = {}; sources = []; targets = []; phase = 'format'
    try:
        if isinstance(source_product, dict):
            source_product = source_product.get('path') or source_product.get('source')
        _need(source_product is not None, '請提供來源產品資料夾。')
        source = _safe_path(source_product, True); output = _safe_path(output_directory, True)
        _need(source != output and source not in output.parents and output not in source.parents,
              '來源與轉換輸出不可相同或互相包含。')
        roots = {p: _identity(p) for p in (source, output)}
        sources = product_files(source)
        led = sources[1].name
        date, look, node = _date(led[16:22]), led[26], led[27]
        for path in sources:
            name = original_output_name(path.name)
            targets.append(output / (name[:-7] + look + '1.1__' + node))
        expected_names = {p.name for p in targets}
        actual_names = {p.name for p in output.iterdir() if p.name.startswith(('VOL-', 'LED-', 'TRL-', 'IMG-'))}
        _need(actual_names == expected_names, '轉換輸出檔案／日期／極化不完整或混入其他產品。')
        all_paths = sources + targets
        for path in all_paths:
            _safe_path(path); identities[path] = _identity(path)
        _need(len({(v['device'], v['inode']) for v in identities.values()}) == len(all_paths), '來源與輸出含重複實體檔案／硬連結，無法獨立核對。')
        report.update(source=str(source), output=str(output), date=date.strftime('%Y%m%d'), source_mode=led[22:25], output_mode=MODES[led[22:25]])
        phase = 'metadata'
        metadata, hashes = _metadata(sources, targets, identities, date, look, node, progress)
        report['metadata'] = metadata
        report['format'] = dict(status='checking', prefix_change='800 → 544', sample_format='big-endian complex float32 (C*8)')
        phase = 'samples'
        report['samples']['status'] = 'checking'
        for path, target in zip(sources[3:], targets[3:]):
            info = _image(path, target, identities, date, progress)
            report['images'].append(info)
            hashes[path] = info['source_sha256']; hashes[target] = info['output_sha256']
        shapes = {(x['width'], x['lines']) for x in report['images']}
        _need(len(shapes) == 1, '來源不同極化的 IMG 尺寸不同，不能視為同一產品。')
        report['format']['status'] = 'passed'
        report['samples'] = dict(status='passed', all_samples_equal=True,
                                total_complex_samples=sum(x['complex_samples'] for x in report['images']),
                                total_sample_bytes=sum(x['sample_bytes'] for x in report['images']),
                                scope='every complex sample byte in every IMG record; no sampling')
        phase = 'reader'
        report['reader']['status'] = 'checking'
        progress(dict(phase='reader', completed=0, total=1))
        report['reader'] = _reader(targets, report['images'], metadata['output'], identities)
        progress(dict(phase='reader', completed=1, total=1))
        for side, paths in (('source', sources), ('output', targets)):
            report['fingerprints'][side] = [dict(name=p.name, **identities[p], sha256=hashes[p]) for p in paths]
        phase = 'input_verification'
        for path, original in {**roots, **identities}.items():
            _safe_path(path, directory=path in roots)
            _need(_identity(path) == original, '檢查期間來源或輸出改變，不能確認此份報告：' + path.name)
        report['input_verification'] = dict(status='passed', method='full file SHA256 plus before/after device/inode/size/mtime_ns/ctime_ns; no input writes')
        report['status'] = 'passed'
        progress(dict(phase='complete', completed=1, total=1))
        # A progress callback must not be able to mutate sources after the final
        # identity check and leave a successful report behind.
        for path, original in {**roots, **identities}.items():
            _need(_identity(path) == original, '檔案在完成檢查時改變：' + path.name)
        return report
    except Exception as exc:
        report['status'] = 'failed'
        if phase in report and isinstance(report[phase], dict):
            report[phase]['status'] = 'failed'
        report['errors'].append(str(exc))
        report['input_verification'] = {'status': 'not_confirmed'}
        raise QualityError(str(exc), report) from exc
