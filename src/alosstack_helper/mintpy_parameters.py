"""Portable MintPy parameter catalogue and strict public-input validation.

This module deliberately uses only the standard library. Native MintPy is
loaded by the scientific worker, where data-dependent checks take place.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
from pathlib import Path


_BUNDLE = json.loads(Path(__file__).with_name('parameter_catalogue.json').read_text())
_OPTIONS = {r['key']: r for r in _BUNDLE['options']}
BASELINES = {k: r['baseline'] for k, r in _OPTIONS.items() if r['baseline'] is not None}
EDITABLE_KEYS = frozenset(k for k, r in _OPTIONS.items() if r['editable'])
PROTECTED_KEYS = frozenset(_OPTIONS) - EDITABLE_KEYS
_INTEGER = frozenset({
    'network.connNumMax', 'unwrapError.numSample', 'unwrapError.bridgePtsRadius',
    'networkInversion.minNumPixel', 'troposphericDelay.looks',
    'timeFunc.polynomial', 'timeFunc.bootstrapCount',
})
_OPTIONAL = frozenset({
    'network.connNumMax', 'network.tempBaseMax', 'network.perpBaseMax',
    'network.aoiYX', 'network.aoiLALO',
    'topographicResidual.stepDate', 'topographicResidual.excludeDate',
    'timeFunc.startDate', 'timeFunc.endDate', 'timeFunc.excludeDate',
    'timeFunc.periodic', 'timeFunc.stepDate', 'timeFunc.exp', 'timeFunc.log',
})


def _template(path):
    result = {}
    for line in path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if line.startswith('mintpy.') and '=' in line:
            key, value = line.split('=', 1)
            result[key.strip()] = value.strip()
    return result


def catalogue(environment=None):
    """Return an independent JSON-ready catalogue without importing the env.

    Read the selected environment's shipped templates to display its defaults.
    Missing/unsupported environments remain explainable rather than preventing
    the user from browsing parameter help. No environment code is executed.
    """
    result = copy.deepcopy(_BUNDLE)
    meta = result['metadata']
    meta.update(available=False, environment_version=None,
                compatibility='bundled', warnings=[])
    if not environment:
        return result
    env = Path(str(environment)).expanduser()
    if not env.is_absolute() or not env.is_dir():
        meta['compatibility'] = 'unavailable'
        meta['warnings'].append('無法讀取所選環境；顯示內附參數說明，執行前仍須檢查環境。')
        return result
    matches = sorted({p.resolve() for p in env.glob('lib/python*/site-packages/mintpy/defaults/smallbaselineApp.cfg')})
    if len(matches) != 1:
        meta['compatibility'] = 'unavailable'
        meta['warnings'].append('所選環境沒有唯一可辨識的 MintPy 範本；目前顯示內附說明。')
        return result
    template = matches[0]
    auto = template.with_name('smallbaselineApp_auto.cfg')
    try:
        keys = _template(template)
        defaults = _template(auto) if auto.is_file() else {}
        version = None
        site = template.parents[2]
        for metadata in sorted(site.glob('mintpy-*.dist-info/METADATA')):
            for line in metadata.read_text().splitlines():
                if line.startswith('Version: '):
                    version = line[9:].strip()
                    break
        digest = hashlib.sha256(template.read_bytes()).hexdigest()
        meta.update(available=True, environment_version=version,
                    compatibility='matched' if digest == meta['template_sha256'] else 'template_changed',
                    environment_template_sha256=digest)
        if meta['compatibility'] != 'matched':
            meta['warnings'].append('所選 MintPy 範本與盤點版本不同；已讀取其預設值，執行前會再驗證相容性。')
        missing = sorted(set(_OPTIONS) - set(keys))
        added = sorted(set(keys) - set(_OPTIONS))
        meta.update(missing_keys=missing, additional_keys=added)
        if added:
            meta['warnings'].append(f'所選版本另有 {len(added)} 項尚未整合的參數。')
        for option in result['options']:
            key = option['key']
            option['default_source'] = 'selected_environment' if key in defaults else 'bundled'
            if key in defaults:
                option['default'] = defaults[key]
            if key not in keys:
                option.update(editable=False, status='unsupported',
                              disabled_reason='所選 MintPy 版本的主流程範本沒有此參數。')
    except (OSError, UnicodeError) as exc:
        meta['compatibility'] = 'unavailable'
        meta['warnings'].append(f'無法讀取環境範本（{type(exc).__name__}）；顯示內附參數說明。')
    return result


def _text(value, key):
    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if not isinstance(value, (str, int, float)):
        raise ValueError(f'{key} 必須是文字、數字或布林值。')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f'{key} 不可使用 NaN 或無限值。')
    value = str(value)
    if len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f'{key} 不可含換行／控制字元，且長度不能超過 4096。')
    if any(c in value for c in '#='):
        raise ValueError(f'{key} 不可含設定檔分隔字元 # 或 =。')
    return value.strip()


def _number(value, key, *, minimum=None, maximum=None, integer=False, exclusive=False):
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'{key} 必須是有效數字。') from None
    if not math.isfinite(number):
        raise ValueError(f'{key} 必須是有限數字。')
    if minimum is not None and (number <= minimum if exclusive else number < minimum):
        raise ValueError(f'{key} 必須{">" if exclusive else "≥"} {minimum}。')
    if maximum is not None and number > maximum:
        raise ValueError(f'{key} 必須 ≤ {maximum}。')
    if integer and not number.is_integer():
        raise ValueError(f'{key} 必須是整數。')
    return str(int(number)) if number.is_integer() else format(number, '.15g')


def _date(value, *, event=False, short=False):
    if short and re.fullmatch(r'\d{6}', value):
        value = ('19' if int(value[:2]) >= 70 else '20') + value
    pattern = r'\d{8}(?:T\d{4})?' if event else r'\d{8}'
    if not re.fullmatch(pattern, value):
        raise ValueError('日期請使用 YYYYMMDD' + (' 或 YYYYMMDDTHHMM。' if event else '。'))
    try:
        dt.datetime.strptime(value, '%Y%m%dT%H%M' if 'T' in value else '%Y%m%d')
    except ValueError:
        raise ValueError(f'日期無效：{value}') from None
    return value


def _list(value):
    values = [v.strip() for v in value.replace(';', ',').split(',')]
    if not values or any(not v for v in values) or len(values) > 512:
        raise ValueError('清單不可有空項目，且最多 512 項。')
    return values


def _date_list(value, event=False):
    values = [_date(v, event=event) for v in _list(value)]
    if len(set(values)) != len(values):
        raise ValueError('日期清單有重複項目。')
    return ','.join(sorted(values))


def _path(value, key, kind):
    if value.lower() in {'no', 'none'}:
        return 'no'
    if any(c in value for c in '*?[]$'):
        raise ValueError(f'{key} 請指定明確路徑，不可使用萬用字元或環境變數。')
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f'{key} 請使用完整絕對路徑。')
    path = path.resolve()
    if not (path.is_dir() if kind == 'directory' else path.is_file()):
        raise ValueError(f'{key} 指定的{"資料夾" if kind == "directory" else "檔案"}不存在。')
    if not os.access(path, os.R_OK):
        raise ValueError(f'{key} 指定路徑無法讀取。')
    return str(path)


def _structured(key, value):
    suffix = key.removeprefix('mintpy.')
    if suffix in {'network.aoiYX', 'network.aoiLALO'}:
        if not re.fullmatch(r'[^:,]+:[^:,]+,[^:,]+:[^:,]+', value):
            raise ValueError(f'{key} 請使用 y0:y1,x0:x1 或 S:N,W:E 格式。')
        fields = re.split('[:,]', value)
        integers = suffix.endswith('YX')
        vals = [float(_number(v, key, integer=integers, minimum=0 if integers else None)) for v in fields]
        if vals[0] >= vals[1] or vals[2] >= vals[3]:
            raise ValueError(f'{key} 兩組範圍的下限必須小於上限。')
        if not integers and not (-90 <= vals[0] < vals[1] <= 90 and -180 <= vals[2] < vals[3] <= 180):
            raise ValueError(f'{key} 經緯度超出有效範圍。')
        values = [_number(v, key, integer=integers) for v in fields]
        return f'{values[0]}:{values[1]},{values[2]}:{values[3]}'
    if suffix.endswith(('startDate', 'endDate')):
        return _date(value)
    if suffix.endswith('excludeDate'):
        return _date_list(value)
    if suffix.endswith('stepDate'):
        return _date_list(value, event=True)
    if suffix == 'timeFunc.periodic':
        values = [_number(v, key, minimum=0, exclusive=True) for v in _list(value)]
        if len(set(values)) != len(values):
            raise ValueError('週期清單有重複項目。')
        return ','.join(values)
    if suffix in {'timeFunc.exp', 'timeFunc.log'}:
        groups = value.split(';')
        if len(groups) > 128:
            raise ValueError('事件模型清單過長。')
        results = []
        onsets = set()
        for group in groups:
            parts = [v.strip() for v in group.split(',')]
            if len(parts) < 2:
                raise ValueError(f'{key} 每組需有事件日期及至少一個正整數時間常數（天）。')
            onset = _date(parts[0], event=True)
            taus = [_number(v, key, minimum=1, integer=True) for v in parts[1:]]
            if onset in onsets or len(set(taus)) != len(taus):
                raise ValueError(f'{key} 有重複事件或時間常數。')
            onsets.add(onset)
            results.append(','.join([onset] + taus))
        return ';'.join(results)
    raise ValueError(f'{key} 尚未提供可驗證的輸入格式。')


def normalize_overrides(raw, retained_dates=None):
    """Sanitize public overrides; blank/None restores workbench baselines.

    Parameters owned by basic controls or adapters are never accepted here,
    even when their value appears harmless. Validate native data constraints
    separately in the selected scientific environment before actual processing.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > len(_OPTIONS):
        raise ValueError('MintPy 進階設定必須是參數名稱對應值的物件。')
    result = {}
    for key, value in raw.items():
        if not isinstance(key, str) or key not in _OPTIONS:
            raise ValueError(f'未知的 MintPy 參數：{key}')
        row = _OPTIONS[key]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not row['editable']:
            raise ValueError(f'{key} 不可在進階設定覆寫：{row["disabled_reason"]}')
        value = _text(value, key)
        if value.lower() == 'auto':
            result[key] = 'auto'
            continue
        suffix = key.removeprefix('mintpy.')
        if suffix in _OPTIONAL and value.lower() in {'no', 'none'}:
            result[key] = 'no'
            continue
        kind = row['input_type']
        if kind == 'boolean':
            aliases = {'yes': 'yes', 'true': 'yes', 'no': 'no', 'false': 'no'}
            if value.lower() not in aliases:
                raise ValueError(f'{key} 請選擇 yes 或 no。')
            value = aliases[value.lower()]
        elif kind == 'enum':
            canonical = {str(v).lower(): str(v) for v in row['choices']}
            if value.lower() not in canonical:
                raise ValueError(f'{key} 可用選项：{", ".join(row["choices"])}')
            value = canonical[value.lower()]
        elif kind == 'number':
            value = _number(value, key, minimum=row.get('min'), maximum=row.get('max'),
                            integer=suffix in _INTEGER, exclusive=row.get('exclusive_min', False))
        elif kind == 'path':
            if value.lower() in {'no', 'none'} and not row.get('allow_no', False):
                raise ValueError(f'{key} 此版本不支援可靠停用，請使用 auto 或明確檔案。')
            value = _path(value, key, row.get('path_kind', 'file'))
        else:
            value = _structured(key, value)
        result[key] = value
    validate_combination(result, {'retained_dates': retained_dates} if retained_dates else {})
    return result


def validate_combination(overrides, options=None):
    """Reject obvious incompatible explicit choices; native checks follow.

    An explicit auto is resolved by native MintPy later, never guessed here.
    Return the same override dict to allow callers to compose validations.
    """
    options = options or {}
    active = lambda key: overrides.get('mintpy.' + key) not in (None, 'auto', 'no', 'none')
    get = lambda key: overrides.get('mintpy.' + key)
    if active('network.aoiYX') and active('network.aoiLALO'):
        raise ValueError('配對品質統計區請擇一使用雷達像素範圍或經緯度範圍。')
    start, end = get('timeFunc.startDate'), get('timeFunc.endDate')
    if active('timeFunc.startDate') and active('timeFunc.endDate') and start > end:
        raise ValueError('速度模型開始日期不可晚於結束日期。')
    if get('networkInversion.maskDataset') == 'coherence' and active('networkInversion.maskThreshold'):
        if float(get('networkInversion.maskThreshold')) > 1:
            raise ValueError('相干性遮罩門檻必須介於 0 到 1。')
    if get('timeFunc.uncertaintyQuantification') == 'bootstrap':
        if get('timeFunc.polynomial') not in (None, 'auto', '1') or any(active('timeFunc.' + k) for k in ('periodic', 'stepDate', 'exp', 'log')):
            raise ValueError('此 MintPy 版本的 bootstrap 僅支援純線性速度模型。')
    retained = options.get('retained_dates')
    if retained:
        dates = sorted({_date(str(d), short=True) for d in retained})
        for suffix in ('topographicResidual.excludeDate', 'timeFunc.excludeDate'):
            if active(suffix):
                missing = set(get(suffix).split(',')) - set(dates)
                if missing:
                    raise ValueError(f'{suffix} 含未保留的日期：{", ".join(sorted(missing))}')
        for suffix in ('topographicResidual.stepDate', 'timeFunc.stepDate'):
            if active(suffix):
                for event in get(suffix).split(','):
                    if not (dates[0] < event < dates[-1]):
                        raise ValueError(f'{suffix} 事件日期必須位於第一期與最後一期觀測之間。')
        for suffix in ('timeFunc.exp', 'timeFunc.log'):
            if active(suffix) and any(group.split(',')[0] >= dates[-1] for group in get(suffix).split(';')):
                raise ValueError(f'{suffix} 事件日期必須早於最後一期觀測。')
    return overrides
