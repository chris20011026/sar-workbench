"""Read-only radar metadata review; never an interferometric approval.

The byte positions are from JAXA PALSAR-2 CEOS Rev G table 3.3-5 and
PALSAR-3 CEOS Rev A table 4.6-5. Positions after the common header differ:
the range-Doppler approximation is at 1735/1751 in P2 and 2331/2347 in P3.
No source pixels are read and no environment or processing state is changed.
"""
from __future__ import annotations

import math
import re


C = 299792458.0
SOURCES = {
    'PALSAR-2': 'https://www.eorc.jaxa.jp/ALOS/en/alos-2/pdf/product_format_description/PALSAR-2_xx_Format_CEOS_E_g.pdf',
    'PALSAR-3': 'https://www.eorc.jaxa.jp/ALOS/en/alos-4/pdf/FTR-240031A_ALOS-4_PALSAR-3_StandardProduct_Format_CEOS_En.pdf',
}
READER_SOURCE = 'https://github.com/isce-framework/isce2/blob/main/components/isceobj/Sensor/MultiMode/ALOS2.py'
NOTE = ('此頁整理原始產品中繼資料，沒有重新取樣、濾波或匹配影像。'
        '取樣率相同、名義頻帶重疊或相同影格編號，都不能證明可干涉或對位成功。')


def _number(raw, offset, length, label, issues, positive=False):
    data = raw[offset:offset + length]
    try:
        if len(data) != length:
            raise ValueError()
        value = data.decode('ascii').strip().rstrip('\0')
        if not value:
            return None
        if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?', value):
            raise ValueError()
        number = float(value.replace('D', 'E').replace('d', 'e'))
        if not math.isfinite(number) or (positive and number <= 0):
            raise ValueError()
        return number
    except (UnicodeError, ValueError):
        issues.append(label + ' 缺漏或數值不合法，保留為未知。')
        return None


def radar_metadata(raw, layout):
    """Read already-bounded dataset-summary bytes using explicit format offsets."""
    if layout not in SOURCES or len(raw) != {'PALSAR-2': 4096, 'PALSAR-3': 4680}[layout]:
        return dict(issues=['尚未核對此雷達中繼資料版型。'], field_sources={})
    issues, sources = [], {}
    specs = {
        'wavelength_m': (500, 16, '雷達波長', 'm', True),
        'chirp_rate_hz_per_s': (550, 16, '線性調頻率', 'Hz/s', False),
        'pulse_length_us': (742, 16, '脈衝長度', 'microseconds', True),
        'range_sampling_mhz': (710, 16, '產品距離取樣率', 'MHz', True),
        'prf_millihz': (934, 16, '產品 PRF', 'mHz', True),
        'processor_range_bandwidth_khz': (1254, 16, '產品距離頻寬', 'kHz', True),
        'azimuth_bandwidth_hz': (1238, 16, '產品方位向頻寬', 'Hz', True),
        'doppler_constant_hz': (1734 if layout == 'PALSAR-2' else 2330, 16, 'Doppler 常數', 'Hz', False),
        'doppler_linear_hz_per_km': (1750 if layout == 'PALSAR-2' else 2346, 16, 'Doppler 線性係數', 'Hz/km', False),
    }
    result = {}
    for key, (offset, length, label, unit, positive) in specs.items():
        result[key] = _number(raw, offset, length, label, issues, positive)
        sources[key] = dict(record='dataset_summary', bytes=[offset + 1, offset + length],
                            source=SOURCES[layout], unit=unit, layout=layout)
    try:
        result['pulse_code'] = raw[518:534].decode('ascii').strip() or None
    except UnicodeError:
        result['pulse_code'] = None
        issues.append('脈衝編碼未確認。')
    sources['pulse_code'] = dict(record='dataset_summary', bytes=[519, 534], source=SOURCES[layout], layout=layout)
    result.update(field_sources=sources, issues=issues)
    return result


def _finite(value, positive=False):
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value) and (not positive or value > 0))


def _scaled(value, factor=1.0, positive=True):
    if not _finite(value, positive):
        return None
    result = value * factor
    return float(result) if _finite(result, positive) else None


def _row(product):
    meta = product.get('metadata', {})
    radar = meta.get('radar', {})
    sources = dict(radar.get('field_sources') or {})
    issues = list(radar.get('issues') or [])
    wavelength = _scaled(radar.get('wavelength_m'))
    frequency = _scaled(C / wavelength) if wavelength else None
    slope = _scaled(radar.get('chirp_rate_hz_per_s'), positive=False)
    length = _scaled(radar.get('pulse_length_us'), 1e-6)
    bandwidth = None
    if slope is not None and length is not None and radar.get('pulse_code') == 'LINEAR FM CHIRP':
        bandwidth = _scaled(abs(slope * length))
    if bandwidth is None:
        issues.append('線性調頻率、脈衝長度或 LINEAR FM CHIRP 標記不足，無法推估名義距離頻寬。')
    sources['carrier_frequency_hz'] = dict(derived_from=['wavelength_m'], definition='c / wavelength; c = 299792458 m/s', source=SOURCES.get(meta.get('layout')))
    sources['range_bandwidth_hz'] = dict(derived_from=['chirp_rate_hz_per_s', 'pulse_length_us', 'pulse_code'],
        definition='abs(chirp_rate_hz_per_s * pulse_length_us * 1e-6); nominal chirp bandwidth, not measured spectrum', source=READER_SOURCE)
    for key, raw_key, factor in (
            ('range_sampling_hz', 'range_sampling_mhz', 1e6),
            ('prf_hz', 'prf_millihz', 1e-3),
            ('processor_range_bandwidth_hz', 'processor_range_bandwidth_khz', 1e3)):
        sources[key] = dict(derived_from=[raw_key], scale=factor, source=SOURCES.get(meta.get('layout')))
    if product.get('errors'):
        issues.append('產品中繼資料含讀取錯誤；此產品不納入名義頻帶估算。')
    if meta.get('converted'):
        issues.append('此列讀取相容格式 LED；衛星名稱依轉換來源紀錄。請保留原始 ALOS-4 產品供核對。')
    if meta.get('level') != '1.1':
        issues.append('未確認為 Level 1.1；不推估此產品的共同頻帶。')
    mode = meta.get('sensor_mode')
    mode_code = mode[10:12] if isinstance(mode, str) and len(mode) >= 12 else None
    if mode_code not in ('01', '02', '03', '18', '19'):
        issues.append('此模式尚無逐子波束／突發頻譜檢查；名義共同頻帶估算僅支援已辨識的 Stripmap 模式。')
    return dict(product_id=product['id'], date=product.get('date'), frame=meta.get('frame'),
        sensor=product.get('sensor'), level=meta.get('level'), mode=mode, mode_code=mode_code,
        wavelength_m=wavelength, carrier_frequency_hz=frequency, range_bandwidth_hz=bandwidth,
        processor_range_bandwidth_hz=_scaled(radar.get('processor_range_bandwidth_khz'), 1e3),
        azimuth_bandwidth_hz=_scaled(radar.get('azimuth_bandwidth_hz')),
        range_sampling_hz=_scaled(radar.get('range_sampling_mhz'), 1e6),
        prf_hz=_scaled(radar.get('prf_millihz'), 1e-3),
        doppler=dict(constant_hz=_scaled(radar.get('doppler_constant_hz'), positive=False),
                     linear_hz_per_km=_scaled(radar.get('doppler_linear_hz_per_km'), positive=False),
                     equation='f_D(R_km) = a + b × R_km',
                     note='a 為近似式常數，不是影像中心像素的 Doppler；尚未估算共同方位頻帶。'),
        field_sources=sources, issues=issues, metadata_valid=not product.get('errors'),
        source_path=product.get('path'), source_fingerprint=product.get('source_fingerprint'))


def compatibility_report(products, expected=None):
    """Compare only chosen processing products; no candidate or frame-ID pairing.

The range intersection is a labelled *nominal rectangular envelope* centred on
c/lambda, not a measurement of spectral support. It does not estimate geometry-
dependent range shifts or common azimuth bandwidth. A missing selected product
invalidates the group estimate; known subsets cannot masquerade as the stack.
"""
    selected = [p for p in products if p.get('selected') is True]
    rows = [_row(p) for p in selected]
    expected = set(expected) if expected is not None else {(str(r['date'])[-6:], r['frame']) for r in rows}
    selected_keys = [(str(r['date'])[-6:], r['frame']) for r in rows]
    actual = set(selected_keys)
    duplicate_selection = len(selected_keys) != len(actual)
    complete = bool(rows) and expected == actual and not duplicate_selection
    warnings = [NOTE,
        '頻帶估算採名義載頻與矩形線性調頻頻帶；未包含幾何頻譜偏移、實測頻譜、共同頻帶濾波或相干性驗證。',
        '表內取樣率／PRF 來自原始 LED，可能與讀取器換算值或統一取樣後的值不同；處理用數值請看取樣率與尺寸表。']
    if duplicate_selection:
        warnings.append('本次處理有重複日期／影格的產品，無法確認唯一對應；共同頻帶保持未知。')
    if not complete:
        warnings.append('部分本次處理影格沒有完整中繼資料；共同頻帶保持未知。')
    overlap = dict(status='unknown', bandwidth_hz=None, lower_hz=None, upper_hz=None, fractions={},
        method='Nominal rectangular intervals [c/lambda − abs(chirp_rate × pulse_length)/2, c/lambda + abs(chirp_rate × pulse_length)/2]',
        reason='需要至少兩個完整選定的 Stripmap Level 1.1 產品，以及有效波長與線性調頻資訊。')
    eligible = (complete and len(rows) >= 2 and all(r['metadata_valid'] and r['level'] == '1.1'
                and r['mode_code'] in ('01', '02', '03', '18', '19')
                and _finite(r['carrier_frequency_hz'], True) and _finite(r['range_bandwidth_hz'], True) for r in rows))
    if eligible:
        low = max(r['carrier_frequency_hz'] - r['range_bandwidth_hz'] / 2 for r in rows)
        high = min(r['carrier_frequency_hz'] + r['range_bandwidth_hz'] / 2 for r in rows)
        width = max(0.0, high - low)
        overlap.update(status='nominal_overlap' if width > 0 else 'no_nominal_overlap', bandwidth_hz=width,
                       lower_hz=low if width > 0 else None, upper_hz=high if width > 0 else None,
                       fractions={r['product_id']: min(1.0, width / r['range_bandwidth_hz']) for r in rows},
                       reason='僅為中繼資料的名義頻帶交集，不能用來確認實際可干涉頻帶。')
        if width == 0:
            warnings.append('名義距離頻帶沒有交集，請先核對產品與頻率定義；未判定實際頻譜或啟動處理。')
    checks = []
    for key, label in [('wavelength_m', '波長'), ('range_bandwidth_hz', '名義距離頻寬'),
                       ('range_sampling_hz', '原始距離取樣率'), ('prf_hz', '原始 PRF')]:
        values = [r[key] for r in rows]
        status = 'unknown' if not complete or len(values) < 2 or any(v is None for v in values) else 'same' if len(set(values)) == 1 else 'different'
        checks.append(dict(key=key, label=label, status=status,
                           detail='僅比較中繼資料數值，差異或一致均不代表科學處理已驗證。'))
    return dict(schema_version=1, status='review_required', scope='selected_products_only', complete=complete,
                rows=rows, range_overlap=overlap, checks=checks, warnings=warnings, note=NOTE,
                scientific_validation='not_performed', actual_filtering='not_verified', processing_unlocked=False)
