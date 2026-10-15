"""Bounded-memory, read-only scientific result comparison.

Only display products are resampled. Numerical differences use matching native
pixels and are withheld whenever the scientific reference is not established.
"""
from contextlib import ExitStack
from pathlib import Path
import ast
import datetime
import hashlib
import json
import math
import os
import sys
import threading
import time

import h5py
import numpy as np

try:
    from .science_worker import timeseries_path
except ImportError:
    from science_worker import timeseries_path

VERSION = 1
MAX_PREVIEW = 650
MODEL_KEYS = ('polynomial', 'periodic', 'stepDate', 'exp', 'log')


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode('utf-8')
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(clean(value), ensure_ascii=False, allow_nan=False))
    tmp.replace(path)


def progress(out, stage, detail=''):
    save(Path(out) / 'progress.json', dict(stage=stage, detail=detail, updated=time.time()))


def signature(path, include_hash=True):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('比較輸入不存在或是符號連結：' + str(path))
    before = path.stat()
    result = dict(path=str(path), size=before.st_size, mtime_ns=before.st_mtime_ns,
                  ctime_ns=before.st_ctime_ns, device=before.st_dev, inode=before.st_ino)
    if include_hash:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                digest.update(block)
        result['sha256'] = digest.hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino, before.st_dev) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino, after.st_dev):
        raise ValueError('讀取期間輸入檔案改變，請停止其他寫入後重新比較。')
    return result


def tiles(shape):
    height, width = shape
    columns = min(width, 4096)
    rows = max(1, min(height, 128, 262144 // columns))
    for y in range(0, height, rows):
        for x in range(0, width, columns):
            yield np.s_[y:min(y + rows, height), x:min(x + columns, width)]


def geographic(lat, lon):
    return np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180) & ((lat != 0) | (lon != 0))


class Statistics:
    def __init__(self):
        self.count = 0
        self.mean = self.m2 = self.total_square = 0.
        self.minimum = math.inf
        self.maximum = -math.inf

    def add(self, values):
        values = np.asarray(values, dtype='float64')
        values = values[np.isfinite(values)]
        if not values.size:
            return
        count = int(values.size)
        mean = float(values.mean())
        delta = mean - self.mean
        total = self.count + count
        self.m2 += float(np.sum((values - mean) ** 2)) + delta * delta * self.count * count / total
        self.mean += delta * count / total
        self.count = total
        self.total_square += float(np.sum(values ** 2))
        self.minimum = min(self.minimum, float(values.min()))
        self.maximum = max(self.maximum, float(values.max()))

    def result(self):
        return dict(count=self.count, mean=self.mean if self.count else None,
                    std=math.sqrt(max(0., self.m2 / self.count)) if self.count else None,
                    rms=math.sqrt(max(0., self.total_square / self.count)) if self.count else None,
                    min=self.minimum if self.count else None, max=self.maximum if self.count else None)


def unit_factor(unit, velocity=True):
    unit = str(unit).lower().replace(' ', '').replace('years', 'year').replace('/yr', '/year')
    if velocity:
        return {'m/year': 1000., 'mm/year': 1., 'cm/year': 10.}.get(unit)
    return {'m': 1000., 'mm': 1., 'cm': 10.}.get(unit)


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def literal(value):
    if isinstance(value, str):
        if value.lower() in ('none', 'no', 'false', 'auto', ''):
            return None
        try:
            return clean(ast.literal_eval(value))
        except (ValueError, SyntaxError):
            return value
    return clean(value)


def json_signature(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class Run:
    def __init__(self, folder):
        self.folder = Path(folder).resolve(strict=True)
        self.job_path = self.folder / 'analysis.json'
        self.job_text = self.job_path.read_bytes()
        self.job = json.loads(self.job_text)
        if self.job.get('kind') != 'mintpy' or self.job.get('status') != 'complete':
            raise ValueError('只有已完成的 MintPy 分析可比較。')
        if self.job.get('id') != self.folder.name:
            raise ValueError('分析代號與來源資料夾不符。')
        selected = timeseries_path(self.job_path, self.job)
        if not selected.is_absolute():
            selected = self.folder / selected
        if not selected.resolve().is_relative_to(self.folder):
            raise ValueError('已選定時間序列不在該分析資料夾內。')
        # macOS /var is an OS-level alias for /private/var. Check only the
        # analysis-owned path components before normalizing that outer alias.
        for component in (selected, *selected.parents):
            if component.is_symlink():
                raise ValueError('比較輸入路徑包含符號連結，無法保證來源範圍。')
            if component.resolve() == self.folder:
                break
        selected = selected.resolve()
        self.paths = dict(job=self.job_path, velocity=self.folder / 'velocity.h5',
                          mask=self.folder / 'maskTempCoh.h5', coherence=self.folder / 'temporalCoherence.h5',
                          geometry=self.folder / 'inputs/geometryRadar.h5', timeseries=selected,
                          results=self.folder / 'results.json')
        for name in ('mintpy.cfg', 'parameter-report.json'):
            if (self.folder / name).exists():
                self.paths[name] = self.folder / name
        for path in self.paths.values():
            for parent in (path, *path.parents):
                if parent == self.folder.parent:
                    break
                if parent.is_symlink():
                    raise ValueError('比較輸入路徑包含符號連結，無法保證來源範圍。')
        self.results = json.loads(self.paths['results'].read_text())
        self.inputs = {}
        self.files = {}
        self.stats = {}

    def fingerprints(self, full=True):
        return {name: signature(path, full) for name, path in sorted(self.paths.items())}

    def open(self, stack):
        for name in ('velocity', 'mask', 'coherence', 'geometry', 'timeseries'):
            self.files[name] = stack.enter_context(h5py.File(self.paths[name], 'r'))
            # A container hash is not a snapshot of an external or virtual
            # HDF5 payload. Standard MintPy outputs keep these datasets local.
            for key in self.files[name].keys():
                link = self.files[name].get(key, getlink=True)
                if isinstance(link, (h5py.ExternalLink, h5py.SoftLink)):
                    raise ValueError('HDF5 含間接連結，無法以目前來源快照保證資料一致。')
                dataset = self.files[name][key]
                if isinstance(dataset, h5py.Dataset) and (dataset.is_virtual or dataset.external):
                    raise ValueError('HDF5 使用外部／虛擬資料集，請先建立完整獨立的 MintPy 成果。')
        vel, geom, ts = self.files['velocity'], self.files['geometry'], self.files['timeseries']
        self.shape = vel['velocity'].shape
        if len(self.shape) != 2 or min(self.shape) <= 0:
            raise ValueError('速度影像尺寸不合法。')
        # Local derivatives use int32 native indices to keep the browser payload bounded.
        if math.prod(self.shape) >= 2 ** 31:
            raise ValueError('影像超過目前比較索引的 21 億像素上限，請先裁切分析範圍。')
        for file, dataset in ((geom, 'latitude'), (geom, 'longitude'), (geom, 'waterMask'),
                              (self.files['mask'], 'mask'), (self.files['coherence'], 'temporalCoherence')):
            if dataset not in file or file[dataset].shape != self.shape:
                raise ValueError('成果格網或遮罩尺寸不一致：' + dataset)
        if 'timeseries' not in ts or ts['timeseries'].shape[1:] != self.shape or ts['timeseries'].ndim != 3:
            raise ValueError('已選定時間序列的格網與速度影像不一致。')
        self.dates = [str(clean(v)) for v in ts['date'][:]]
        if len(self.dates) != ts['timeseries'].shape[0] or len(set(self.dates)) != len(self.dates):
            raise ValueError('時間序列日期不一致或重複。')
        try:
            for date in self.dates:
                if len(date) != 8:
                    raise ValueError()
                datetime.datetime.strptime(date, '%Y%m%d')
        except ValueError as exc:
            raise ValueError('時間序列日期不是有效 YYYYMMDD 格式。') from exc
        if self.dates != sorted(self.dates):
            raise ValueError('時間序列日期未按時間排序。')
        self.attrs = clean(dict(vel.attrs))
        self.ts_attrs = clean(dict(ts.attrs))
        self.factor = unit_factor(self.attrs.get('UNIT', ''))
        self.ts_factor = unit_factor(self.ts_attrs.get('UNIT', ''), False)
        self.unit = 'mm/year' if self.factor is not None else str(self.attrs.get('UNIT') or '未確認原始單位')
        self.stride = max(1, math.ceil(max(self.shape) / MAX_PREVIEW))
        sample = np.s_[::self.stride, ::self.stride]
        self.lat = geom['latitude'][sample].astype('float64')
        self.lon = geom['longitude'][sample].astype('float64')
        self.preview, self.preview_mask, _ = self.values(sample)

    def values(self, tile):
        values = self.files['velocity']['velocity'][tile].astype('float64') * (self.factor or 1.)
        lat = self.files['geometry']['latitude'][tile]
        lon = self.files['geometry']['longitude'][tile]
        land = self.files['geometry']['waterMask'][tile]
        mask = self.files['mask']['mask'][tile]
        valid = (np.isfinite(mask) & (mask > 0) & np.isfinite(land) & (land > 0)
                 & np.isfinite(values) & geographic(lat, lon))
        coherence = self.files['coherence']['temporalCoherence'][tile].astype('float64')
        return values, valid, coherence

    def summary(self):
        ref = reference(self)
        return dict(id=self.job['id'], name=self.job.get('options', {}).get('name') or self.job['id'],
                    created=self.job.get('created'), unit=self.unit, source_unit=self.attrs.get('UNIT'),
                    native_shape=list(self.shape), dates=self.dates, reference=ref,
                    direction=self.results.get('direction'), selected_timeseries=self.paths['timeseries'].name,
                    model=self.job.get('parameter_report', {}).get('time_model'),
                    statistics=self.stats, versions=self.results.get('versions', {}))


def reference(run):
    values = [number(run.attrs.get(k)) for k in ('REF_Y', 'REF_X')]
    if any(v is None or int(v) != v for v in values):
        return None
    y, x = map(int, values)
    if not 0 <= y < run.shape[0] or not 0 <= x < run.shape[1]:
        return None
    date = str(run.attrs.get('REF_DATE', ''))
    if date not in run.dates:
        return None
    lat = float(run.files['geometry']['latitude'][y, x])
    lon = float(run.files['geometry']['longitude'][y, x])
    if not geographic(np.array([lat]), np.array([lon]))[0]:
        return None
    for name, expected in (('REF_Y', y), ('REF_X', x)):
        if number(run.ts_attrs.get(name)) != expected:
            return None
    if str(run.ts_attrs.get('REF_DATE', '')) != date:
        return None
    supplied = run.results.get('reference_pixel')
    if supplied is not None and supplied != [y, x]:
        return None
    return dict(y=y, x=x, latitude=lat, longitude=lon, date=date)


def equal_arrays(left, right, dataset):
    if dataset not in left or dataset not in right or left[dataset].shape != right[dataset].shape:
        return False
    if len(left[dataset].shape) != 2:
        return False
    for tile in tiles(left[dataset].shape):
        if not np.array_equal(left[dataset][tile], right[dataset][tile], equal_nan=True):
            return False
    return True


def known_look_vectors(run):
    geometry = run.files['geometry']
    if 'incidenceAngle' not in geometry or geometry['incidenceAngle'].shape != run.shape:
        return False
    azimuth = 'azimuthAngle' in geometry and geometry['azimuthAngle'].shape == run.shape
    if not azimuth and number(run.attrs.get('ANTENNA_SIDE')) not in (-1., 1.):
        return False
    observed = False
    for tile in tiles(run.shape):
        _, valid, _ = run.values(tile)
        if not valid.any():
            continue
        observed = True
        incidence = geometry['incidenceAngle'][tile][valid]
        if not np.all(np.isfinite(incidence) & (incidence > 0) & (incidence < 90)):
            return False
        if azimuth and not np.all(np.isfinite(geometry['azimuthAngle'][tile][valid])):
            return False
    return observed


def model_consistent(model, native):
    if not isinstance(model, dict) or not isinstance(native, dict):
        return False
    if any(key not in model or key not in native for key in MODEL_KEYS):
        return False

    def canonical(key, value):
        if key == 'polynomial':
            n = number(value)
            return int(n) if n is not None and int(n) == n else None
        if key in ('exp', 'log'):
            if not value:
                return {}
            if isinstance(value, dict):
                return {str(k): [float(x) for x in v] for k, v in value.items()}
            return {str(v[0]): [float(x) for x in v[1:]] for v in value}
        return list(value or [])
    try:
        return all(canonical(key, model[key]) == canonical(key, native[key]) for key in MODEL_KEYS)
    except (ValueError, TypeError, IndexError, KeyError):
        return False


def compatibility(left, right):
    checks = []

    def check(code, passed, reason):
        checks.append(dict(code=code, passed=bool(passed), reason='' if passed else reason))

    lgeom, rgeom = left.files['geometry'], right.files['geometry']
    same_grid = left.shape == right.shape and all(equal_arrays(lgeom, rgeom, name) for name in ('latitude', 'longitude'))
    check('grid', same_grid, '原生格網或經緯度不同；不自動重採樣後相減。')
    lpro, rpro = left.job.get('source_fingerprint'), right.job.get('source_fingerprint')
    valid_provenance = lambda value: isinstance(value, list) and bool(value) and all(isinstance(v, dict) and v.get('path') and v.get('size') is not None and v.get('mtime_ns') is not None for v in value)
    same_provenance = valid_provenance(lpro) and valid_provenance(rpro) and json_signature(lpro) == json_signature(rpro)
    check('provenance', same_provenance, '來源紀錄不同或不足，無法確認來自同一組處理產物。')
    lref, rref = reference(left), reference(right)
    check('reference_point', bool(lref and rref and all(lref[k] == rref[k] for k in ('y', 'x', 'latitude', 'longitude'))),
          '參考點不同，或速度／時間序列未提供一致的參考點。')
    check('reference_date', bool(lref and rref and lref['date'] == rref['date']), '參考日期不同或無法確認。')
    check('units', left.factor is not None and right.factor is not None and left.ts_factor is not None and right.ts_factor is not None,
          '速度或位移單位無法確認，禁止以預設單位換算差值。')
    # Matching orbital direction alone cannot establish identical viewing vectors.
    orbital = []
    for key in ('HEADING', 'WAVELENGTH'):
        a, b = number(left.attrs.get(key)), number(right.attrs.get(key))
        orbital.append(a is not None and b is not None and abs(a - b) <= 1e-10 and (key != 'WAVELENGTH' or a > 0))
    orbit = str(left.attrs.get('ORBIT_DIRECTION', '')).lower()
    orbital.append(orbit in ('ascending', 'descending') and orbit == str(right.attrs.get('ORBIT_DIRECTION', '')).lower())
    angle = equal_arrays(lgeom, rgeom, 'incidenceAngle')
    if 'azimuthAngle' in lgeom or 'azimuthAngle' in rgeom:
        angle = angle and equal_arrays(lgeom, rgeom, 'azimuthAngle')
    direction = left.results.get('direction')
    check('los', all(orbital) and angle and known_look_vectors(left) and known_look_vectors(right)
          and bool(direction) and direction == right.results.get('direction'),
          'LOS 方向、軌道／波長或入射角資料不同／不足；不能把不同視線當成同一位移。')
    lmodel = left.job.get('parameter_report', {}).get('time_model', {})
    rmodel = right.job.get('parameter_report', {}).get('time_model', {})
    known_models = bool(lmodel.get('model') and rmodel.get('model'))
    model_same = known_models and json_signature(lmodel['model']) == json_signature(rmodel['model'])
    native_models = []
    for run in (left, right):
        values = {key: literal(run.attrs.get('mintpy.timeFunc.' + key)) for key in MODEL_KEYS}
        native_models.append(values if all('mintpy.timeFunc.' + key in run.attrs for key in MODEL_KEYS) else None)
    model_same = model_same and all(native_models) and native_models[0] == native_models[1]
    model_same = model_same and all(model_consistent(report.get('model'), native)
                                   for report, native in zip((lmodel, rmodel), native_models))
    check('time_model', model_same, '時間模型不同或缺少已驗證的模型紀錄；線性項係數不一定代表相同物理意義。')
    fit_dates = [m.get('dates') for m in (lmodel, rmodel)]
    known_dates = all(isinstance(v, list) and len(v) >= 3 and set(v) <= set(run.dates) for v, run in zip(fit_dates, (left, right)))
    fit_same = known_dates and fit_dates[0] == fit_dates[1]
    for run, dates in zip((left, right), fit_dates):
        if dates:
            fit_same = fit_same and str(run.attrs.get('START_DATE', '')) == min(dates) and str(run.attrs.get('END_DATE', '')) == max(dates)
    check('fit_dates', fit_same, '速度擬合使用的日期不同，或實際擬合期間無法確認。')
    check('timeseries_dates', left.dates == right.dates, '時間序列日期不同；不補值或插值來製造差值。')
    return dict(allow_difference=all(item['passed'] for item in checks), same_grid=bool(same_grid),
                paired_points=bool(same_grid), checks=checks,
                reasons=[item['reason'] for item in checks if not item['passed']])


def options_diff(left, right):
    def flatten(value, prefix=''):
        out = {}
        for key, child in value.items():
            path = prefix + str(key)
            if isinstance(child, dict):
                out.update(flatten(child, path + '.'))
            else:
                out[path] = child
        return out
    a = flatten(left.job.get('options', {}))
    b = flatten(right.job.get('options', {}))
    return [dict(key=k, left=a.get(k), right=b.get(k), left_present=k in a, right_present=k in b)
            for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k) or (k in a) != (k in b)]


def full_statistics(left, right, compatible, out):
    counts = dict(shared=0, left_only=0, right_only=0, neither=0) if compatible['same_grid'] else None
    difference = Statistics()
    stores = [(Statistics(), Statistics(), Statistics()) for _ in (left, right)]
    for i, run in enumerate((left, right)):
        for tile in tiles(run.shape):
            values, mask, coherence = run.values(tile)
            stores[i][0].add(values[mask])
            stores[i][1].add(coherence[mask])
            if 'velocityStd' in run.files['velocity'] and run.files['velocity']['velocityStd'].shape == run.shape:
                std = run.files['velocity']['velocityStd'][tile].astype('float64') * (run.factor or 1.)
                stores[i][2].add(std[mask])
            if i == 0 and compatible['same_grid']:
                other, other_mask, _ = right.values(tile)
                shared = mask & other_mask
                counts['shared'] += int(shared.sum())
                counts['left_only'] += int((mask & ~other_mask).sum())
                counts['right_only'] += int((other_mask & ~mask).sum())
                counts['neither'] += int((~mask & ~other_mask).sum())
                if compatible['allow_difference']:
                    difference.add((other - values)[shared])
        run.stats = dict(velocity=stores[i][0].result(), temporal_coherence=stores[i][1].result(),
                         velocity_std=stores[i][2].result(), valid_pixels=stores[i][0].count,
                         total_pixels=math.prod(run.shape),
                         meaning='有效像素為原生品質遮罩、陸地、有效座標與有限速度的交集；相干性與不確定度不是準確率。')
        progress(out, '統計與比較', '已讀取 ' + ('左側' if i == 0 else '右側') + ' 完整原生格網')
    return counts, difference.result() if compatible['allow_difference'] else None


def display_grid(run):
    try:
        try:
            from .geo_worker import grid
        except ImportError:
            from geo_worker import grid
        index, meta = grid(run.lat, run.lon)
        meta.update(coordinate_system='EPSG:3857', approximate=True, preview_stride=run.stride,
                    meaning='經緯度查找表抽樣後以最近鄰呈現；只影響畫面，數值差值與統計使用完整原生格網。')
        return index, meta
    except (ValueError, ImportError) as exc:
        height, width = run.lat.shape
        return np.arange(height * width).reshape(height, width), dict(
            width=width, height=height, coordinate_system='native_pixels', approximate=True,
            preview_stride=run.stride, native_shape=list(run.shape),
            warning='此格網無法建立地理預覽，顯示原生像素：' + str(exc),
            meaning='原生雷達像素預覽；不套用地理底圖。')


def colour_limit(runs):
    values = [np.abs(run.preview[run.preview_mask]) for run in runs]
    values = np.concatenate([v for v in values if v.size]) if any(v.size for v in values) else np.array([])
    limit = float(np.percentile(values, 98)) if values.size else 1.
    return limit if math.isfinite(limit) and limit > 0 else 1.


def image_preview(run, prefix, index, map_meta, out, limit, values=None, mask=None):
    from matplotlib import colormaps
    from PIL import Image
    values = run.preview if values is None else values
    mask = run.preview_mask if mask is None else mask
    usable = index >= 0
    safe = np.maximum(index, 0)
    shown = values.ravel()[safe]
    valid = usable & mask.ravel()[safe] & np.isfinite(shown)
    normalized = np.clip(np.nan_to_num((shown + limit) / (2 * limit), nan=.5), 0, 1)
    rgba = (colormaps['RdBu_r'](normalized) * 255).astype('uint8')
    rgba[~valid] = [0, 0, 0, 0]
    image_name = prefix + '.png'
    Image.fromarray(rgba).save(Path(out) / image_name)
    sy, sx = np.divmod(safe, run.lat.shape[1])
    native = (sy * run.stride * run.shape[1] + sx * run.stride).astype('<i4')
    native[~usable] = -1
    index_name = prefix + '-index.bin'
    native.tofile(Path(out) / index_name)
    meta = dict(map_meta, file=image_name, native_index_file=index_name, index_dtype='int32-le',
                native_shape=list(run.shape), unit=run.unit, min=-limit, max=limit,
                palette='RdBu_r', nodata='transparent')
    return meta


def grid_coordinates(run, prefix, out):
    name = prefix + '-grid.json'
    save(Path(out) / name, dict(stride=run.stride, native_shape=list(run.shape),
                               latitude=run.lat, longitude=run.lon))
    return name


def compare(spec):
    out = Path(spec['out'])
    out.mkdir(parents=True, exist_ok=True)
    progress(out, '檢查來源完整性', '以串流 SHA-256 確認兩個版本的實際輸入；大型時間序列可能需要較久。')
    left, right = Run(spec['left']), Run(spec['right'])
    before = {side: run.fingerprints() for side, run in (('left', left), ('right', right))}
    for side, run in (('left', left), ('right', right)):
        if hashlib.sha256(run.job_text).hexdigest() != before[side]['job']['sha256']:
            raise ValueError('分析設定在開啟時改變，請重新比較。')
    with ExitStack() as stack:
        left.open(stack)
        right.open(stack)
        progress(out, '檢查科學比較條件')
        compatible = compatibility(left, right)
        counts, difference = full_statistics(left, right, compatible, out)
        if counts is not None and not counts['shared']:
            reason = '兩次分析沒有共同有效像素，無法計算差值。'
            compatible['checks'].append(dict(code='common_pixels', passed=False, reason=reason))
            compatible['reasons'].append(reason)
            compatible['allow_difference'] = False
            difference = None
        same_units = left.factor is not None and right.factor is not None
        limit = colour_limit((left, right)) if same_units else None
        progress(out, '建立同步地理預覽')
        li, lm = display_grid(left)
        ri, rm = (li, lm) if compatible['same_grid'] else display_grid(right)
        lmap = image_preview(left, 'left', li, lm, out, limit or colour_limit((left,)))
        rmap = image_preview(right, 'right', ri, rm, out, limit or colour_limit((right,)))
        lmap['grid_file'] = grid_coordinates(left, 'left', out)
        rmap['grid_file'] = grid_coordinates(right, 'right', out)
        diff_map = None
        if compatible['allow_difference']:
            dmask = left.preview_mask & right.preview_mask
            dvalues = right.preview - left.preview
            dv = np.abs(dvalues[dmask])
            dlimit = float(np.percentile(dv, 98)) if dv.size else 1.
            dlimit = dlimit if dlimit > 0 and math.isfinite(dlimit) else 1.
            diff_map = image_preview(left, 'difference', li, lm, out, dlimit, dvalues, dmask)
            diff_map.update(operation='right_minus_left', unit='mm/year')
        result = dict(schema_version=VERSION, id=spec['id'], project_id=spec['project_id'],
                      left_run=left.job['id'], right_run=right.job['id'],
                      created=time.strftime('%Y-%m-%d %H:%M:%S'),
                      left=dict(left.summary(), map=lmap), right=dict(right.summary(), map=rmap),
                      compatibility=compatible, options_diff=options_diff(left, right),
                      colour_scale=dict(shared=same_units, min=-limit if limit else None, max=limit,
                                        unit='mm/year' if same_units else None,
                                        method='兩側有效抽樣像素的絕對值第 98 百分位；畫面以外的數值不裁切。'),
                      coverage=counts, difference=dict(operation='right_minus_left', unit='mm/year',
                                                       statistics=difference, map=diff_map,
                                                       available=compatible['allow_difference']),
                      notes=['正負速度代表 LOS 方向，不能直接解讀為垂直下陷。',
                             '差值呈現兩組處理結果的差異，不是誤差真值，也不能單靠相干性判定哪個設定較好。',
                             '比較成果存於工作台本機資料夾；原專案、HDF5 與處理設定保持不變。'],
                      source_fingerprints=before,
                      sources=dict(left=str(left.folder), right=str(right.folder)))
    progress(out, '再次確認來源完整性', '確認比較期間輸入完全一致，才發布成果。')
    after = {side: run.fingerprints() for side, run in (('left', left), ('right', right))}
    if before != after:
        raise ValueError('比較期間輸入已改變，成果不發布；請重新建立比較。')
    result['input_verification'] = dict(method='streamed SHA-256 plus size/mtime/ctime/device/inode before and after', unchanged=True)
    result['artifacts'] = ['left.png', 'right.png', 'left-index.bin', 'right-index.bin',
                           'left-grid.json', 'right-grid.json', 'comparison.json', 'result.json']
    if diff_map:
        result['artifacts'] += ['difference.png', 'difference-index.bin']
    save(out / 'result.json', result)
    progress(out, '比較完成')
    return clean(result)


def verify_snapshot(run, saved):
    current = run.fingerprints(False)
    for name, value in saved.items():
        if name not in current or current[name] != {k: v for k, v in value.items() if k != 'sha256'}:
            raise ValueError('來源檔案在建立比較後已改變，請重新比較後再選取曲線。')


def verify(manifest):
    for name in ('left', 'right'):
        run = Run(manifest['sources'][name])
        verify_snapshot(run, manifest['source_fingerprints'][name])
    return dict(verified=True, comparison_id=manifest['id'],
                method='size/mtime/ctime/device/inode matched to the completed SHA-256 snapshot',
                checked=time.strftime('%Y-%m-%d %H:%M:%S'))


def curve(run, y, x):
    if not 0 <= y < run.shape[0] or not 0 <= x < run.shape[1]:
        return dict(available=False, reason='位置超出該分析範圍。')
    values, mask, coh = run.values(np.s_[y:y + 1, x:x + 1])
    if not mask[0, 0]:
        return dict(available=False, reason='此位置未通過該分析的品質／陸地／有效值遮罩。')
    if run.ts_factor is None or run.factor is None:
        return dict(available=False, reason='位移或速度單位無法確認。')
    series = run.files['timeseries']['timeseries'][:, y, x].astype('float64') * run.ts_factor
    if not np.isfinite(series).all():
        return dict(available=False, reason='所選定的最終時序含非有限值，不改用其他時序檔。')
    return clean(dict(available=True, analysis_id=run.job['id'], y=y, x=x,
                      latitude=float(run.files['geometry']['latitude'][y, x]),
                      longitude=float(run.files['geometry']['longitude'][y, x]),
                      dates=run.dates, displacement_mm=series, unit='mm',
                      velocity_mm_year=float(values[0, 0]), temporal_coherence=float(coh[0, 0]),
                      reference=reference(run), selected_timeseries=run.paths['timeseries'].name))


def point(manifest, y, x, side='left'):
    if side not in ('left', 'right') or type(y) is not int or type(x) is not int:
        raise ValueError('無效比較位置。')
    left, right = Run(manifest['sources']['left']), Run(manifest['sources']['right'])
    for name, run in (('left', left), ('right', right)):
        verify_snapshot(run, manifest['source_fingerprints'][name])
    with ExitStack() as stack:
        left.open(stack)
        right.open(stack)
        selected = left if side == 'left' else right
        if not 0 <= y < selected.shape[0] or not 0 <= x < selected.shape[1]:
            raise ValueError('位置超出影像範圍。')
        common = manifest['compatibility']['same_grid']
        missing = dict(available=False, reason='格網不同，未自動尋找鄰近像素；請在另一側另選位置。')
        a = curve(left, y, x) if side == 'left' or common else missing
        b = curve(right, y, x) if side == 'right' or common else missing
        difference = None
        if manifest['compatibility']['allow_difference'] and a['available'] and b['available']:
            difference = dict(dates=a['dates'], displacement_mm=(np.array(b['displacement_mm']) - np.array(a['displacement_mm'])).tolist(),
                              velocity_mm_year=b['velocity_mm_year'] - a['velocity_mm_year'],
                              operation='right_minus_left', unit='mm')
        result = dict(y=y, x=x, side=side, same_coordinate=common,
                      latitude=float(selected.files['geometry']['latitude'][y, x]),
                      longitude=float(selected.files['geometry']['longitude'][y, x]),
                      left=a, right=b, difference=difference,
                      reasons=manifest['compatibility']['reasons'])
    for name, run in (('left', left), ('right', right)):
        verify_snapshot(run, manifest['source_fingerprints'][name])
    return clean(result)


def parent_watch(fd):
    def watch():
        try:
            while os.read(fd, 1):
                pass
        finally:
            os._exit(75)
    threading.Thread(target=watch, daemon=True).start()


if __name__ == '__main__':
    if '--parent-fd' in sys.argv:
        parent_watch(int(sys.argv[sys.argv.index('--parent-fd') + 1]))
    action = sys.argv[1]
    payload = json.loads(Path(sys.argv[2]).read_text())
    if action == 'compare':
        value = compare(payload)
        print('RESULT:' + json.dumps(dict(id=value['id'], status='complete'), allow_nan=False), flush=True)
    elif action == 'point':
        print('RESULT:' + json.dumps(point(payload, int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]), ensure_ascii=False, allow_nan=False))
    elif action == 'verify':
        print('RESULT:' + json.dumps(verify(payload), ensure_ascii=False, allow_nan=False))
    else:
        raise ValueError('未知比較操作。')
