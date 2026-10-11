"""Scientific guards executed only by the selected MintPy Python interpreter.

This adapter resolves the installed defaults; it does not install dependencies,
download weather data, or edit source rasters / external auxiliary files.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import datetime
import hashlib
import importlib
import importlib.metadata
import json
import os
import shutil

import numpy as np


CORRECTIONS = {
    'correct_unwrap_error': ('mintpy.unwrapError.method', '解纏錯誤修正'),
    'correct_SET': ('mintpy.solidEarthTides', '固體潮校正'),
    'correct_ionosphere': ('mintpy.ionosphericDelay.method', '電離層校正'),
    'correct_troposphere': ('mintpy.troposphericDelay.method', '對流層校正'),
    'deramp': ('mintpy.deramp', '空間斜坡移除'),
    'correct_topography': ('mintpy.topographicResidual', 'DEM 誤差校正'),
}
MASK_KEYS = ('mintpy.network.maskFile', 'mintpy.reference.maskFile',
             'mintpy.unwrapError.waterMaskFile', 'mintpy.networkInversion.waterMaskFile',
             'mintpy.deramp.maskFile', 'mintpy.residualRMS.maskFile')
INTERNAL_FILES = {'waterMask.h5', 'maskTempCoh.h5', 'maskConnComp.h5', 'avgSpatialCoh.h5', 'exclude_date.txt', 'reference_date.txt'}


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    tmp.replace(path)


def read_job(path):
    return json.loads(Path(path).read_text())


def save_report(path, report):
    job = read_job(path)
    job['parameter_report'] = report
    write_json(path, job)
    write_json(Path(path).parent / 'parameter-report.json', report)


def date8(value):
    value = str(value)
    return datetime.datetime.strptime(value, '%y%m%d' if len(value) == 6 else '%Y%m%d').strftime('%Y%m%d')


def values(value):
    if value in (None, False, '', 'no', 'none'):
        return []
    return [x.strip() for x in str(value).replace(';', ',').split(',') if x.strip()]


def fingerprint(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def resolve_template(config):
    """Use exactly the selected installation's defaults and auto resolver."""
    import mintpy
    from mintpy.utils import readfile, utils as ut
    default = Path(mintpy.__file__).parent / 'defaults/smallbaselineApp.cfg'
    resolved = readfile.read_template(str(default))
    missing = set(config) - set(resolved)
    if missing:
        raise ValueError('所選 MintPy 版本不支援本次設定：'+', '.join(sorted(missing)))
    resolved.update({k: str(v) for k, v in config.items()})
    return ut.check_template_auto_value(resolved)


def resolve_file(path):
    from mintpy.utils import readfile
    return resolve_template(readfile.read_template(str(path)))


def dependency(module, feature):
    try:
        importlib.import_module(module)
    except Exception as exc:
        # Native-loader errors can expose paths, but never include credentials.
        raise ValueError(f'{feature}需要所選環境可載入 {module}。請先在獨立環境安裝／修復並驗證，再建立分析。') from exc


def external_file(value, key, folder):
    if value in (None, False, '', 'no'):
        return None
    p = Path(os.path.expanduser(str(value)))
    if not p.is_absolute():
        if str(p) in INTERNAL_FILES:
            return None
        raise ValueError(f'{key} 請選擇外部檔案的完整路徑。')
    if not p.is_file() or not p.stat().st_size:
        raise ValueError(f'{key} 指定的檔案不存在或為空。')
    if Path(folder).resolve() in p.resolve().parents:
        return None
    return p


def excluded_dates(value, dates, folder):
    if not value:
        return []
    p = Path(str(value))
    if not p.is_absolute():
        p = Path(folder) / p
    if p.is_file():
        from mintpy.utils import ptime
        excluded = ptime.read_date_list(str(p))
    elif str(value) == 'exclude_date.txt':
        return []
    else:
        excluded = [date8(x) for x in values(value)]
    unknown = set(excluded) - set(dates)
    if unknown:
        raise ValueError('模型排除日期不在保留時序內：' + ', '.join(sorted(unknown)))
    return excluded


def time_model(config, dates, folder, dem=False):
    from mintpy.utils import time_func
    prefix = 'mintpy.topographicResidual.' if dem else 'mintpy.timeFunc.'
    excluded = excluded_dates(config.get(prefix + 'excludeDate'), dates, folder)
    kept = [d for d in dates if d not in excluded]
    if not dem:
        start, end = config.get(prefix + 'startDate'), config.get(prefix + 'endDate')
        kept = [d for d in kept if (not start or d >= date8(start)) and (not end or d <= date8(end))]
    polynomial = int(config[prefix + ('polyOrder' if dem else 'polynomial')])
    if polynomial < 1:
        raise ValueError('工作台的速度成果需要至少一次線性項，polynomial 請設定為 1 或更高。')
    def decay(key):
        v = config.get(prefix + key)
        return [x.split(',') for x in str(v).replace('/', ';').replace('|', ';').split(';')] if v else []
    inps = SimpleNamespace(polynomial=polynomial, periodic=[] if dem else [float(x) for x in values(config.get(prefix+'periodic'))],
                           stepDate=values(config.get(prefix+'stepDate')), polyline=[],
                           exp=[] if dem else decay('exp'), log=[] if dem else decay('log'))
    if len(kept) < 3:
        raise ValueError(('DEM 誤差' if dem else '速度') + '模型至少需保留三個日期。')
    model = time_func.inps2model(inps, date_list=kept, print_msg=False)
    matrix = time_func.get_design_matrix4time_func(kept, model=model).astype('float64')
    rank = int(np.linalg.matrix_rank(matrix))
    # DEM estimates one additional baseline-correlated unknown.
    columns = matrix.shape[1] + int(dem)
    if rank < matrix.shape[1]:
        raise ValueError(('DEM 誤差' if dem else '速度') + '時間模型秩不足；日期取樣無法區分所選季節／事件／多項式，請減少參數。')
    if len(kept) <= columns:
        raise ValueError(f'{"DEM 誤差" if dem else "速度"}模型有 {columns} 個參數，但只有 {len(kept)} 個日期；需要正的殘差自由度，請減少參數或增加日期。')
    if not dem and config.get('mintpy.timeFunc.uncertaintyQuantification') == 'bootstrap':
        if polynomial != 1 or any(model[x] for x in ('periodic', 'stepDate', 'exp', 'log')):
            raise ValueError('此 MintPy 版本的 bootstrap 僅支援單純線性模型。')
        if int(config['mintpy.timeFunc.bootstrapCount']) <= 1:
            raise ValueError('bootstrap 次數需大於 1，不能靜默退回殘差法。')
    nonlinear = polynomial != 1 or any(model[x] for x in ('periodic', 'stepDate', 'exp', 'log'))
    meaning = 'LOS 模型的一次項係數；已同時估計其他時間項，不能解讀為整段時序的單一直線平均斜率。' if nonlinear else 'LOS 位移的線性擬合速度；相對所選參考點與日期。'
    return {'dates': kept, 'model': model, 'rank': rank, 'parameters': columns,
            'residual_dof': len(kept)-columns, 'nonlinear': bool(nonlinear), 'velocity_meaning': meaning}


def validate_dependencies(config, dates, folder):
    if config.get('mintpy.ionosphericDelay.method'):
        raise ValueError('目前沒有獨立的 MintPy 電離層輸入轉接；禁止再次校正上游 alosStack 產物。')
    if config.get('mintpy.solidEarthTides'):
        dependency('mintpy.solid_earth_tides', '固體潮校正（PySolid）')
    unwrap = config.get('mintpy.unwrapError.method')
    if unwrap:
        dependency('mintpy.unwrap_error_bridging', '解纏錯誤修正')
        dependency('mintpy.unwrap_error_phase_closure', '相位閉合／解纏修正（cvxopt）')
    trop = config.get('mintpy.troposphericDelay.method')
    if trop == 'height_correlation':
        dependency('mintpy.tropo_phase_elevation', '高程相關大氣校正')
    elif trop == 'gacos':
        dependency('mintpy.tropo_gacos', 'GACOS 大氣校正')
        directory = Path(os.path.expanduser(str(config['mintpy.troposphericDelay.gacosDir'])))
        missing = [d for d in dates if not any((directory/(d+ext)).is_file() for ext in ('.ztd', '.ztd.tif'))]
        if missing:
            raise ValueError('GACOS 必須涵蓋全部保留日期；缺少：'+', '.join(missing))
        for date in dates:
            ztd = directory/(date+'.ztd')
            if ztd.is_file() and not Path(str(ztd)+'.rsc').is_file():
                raise ValueError('GACOS 原始 .ztd 缺少對應 .rsc 描述檔：'+date)
    elif trop == 'pyaps':
        if config.get('mintpy.troposphericDelay.weatherModel') != 'ERA5':
            raise ValueError('此工作台目前僅驗證 PyAPS 的 ERA5 模型。')
        dependency('mintpy.tropo_pyaps3', 'ERA5 大氣校正（PyAPS）')
        directory = Path(os.path.expandvars(os.path.expanduser(str(config['mintpy.troposphericDelay.weatherDir']))))/'ERA5'
        if not directory.is_dir() or any(not list(directory.glob('*'+d+'_*.grb')) for d in dates):
            raise ValueError('ERA5 需先準備涵蓋全部保留日期的 weatherDir/ERA5/*.grb 快取；工作台不會自動下載或讀取帳密。')
    elif trop:
        raise ValueError('此對流層方法尚未由工作台驗證。')
    uq = config.get('mintpy.timeFunc.uncertaintyQuantification')
    if uq == 'covariance':
        if not external_file(config.get('mintpy.timeFunc.timeSeriesCovFile'), '時間序列共變異數', folder):
            value = config.get('mintpy.timeFunc.timeSeriesCovFile')
            if not value or not Path(str(value)).is_file():
                raise ValueError('covariance 需要有效的共變異數 HDF5 檔；不能靜默退回殘差法。')


def preflight(job_path, config):
    """Validate before raster loading; persist requested and native resolved values."""
    job = read_job(job_path)
    folder = Path(job_path).parent
    dates = [date8(d) for d in job['options']['retained_dates']]
    resolved = resolve_template(config)
    report = {'version': importlib.metadata.version('mintpy'), 'requested': job['options'].get('mintpy_overrides', {}),
              'resolved': resolved, 'corrections': [], 'warnings': [], 'external_inputs': []}
    for name, (key, label) in CORRECTIONS.items():
        report['corrections'].append({'key': key, 'step': name, 'label': label,
                                     'state': 'pending' if resolved.get(key) else 'disabled',
                                     'detail': '已要求；尚未執行與驗證產物。' if resolved.get(key) else '本次設定關閉。'})
    save_report(job_path, report)
    report['time_model'] = time_model(resolved, dates, folder)
    if resolved.get('mintpy.topographicResidual'):
        report['dem_model'] = time_model(resolved, dates, folder, dem=True)
    validate_dependencies(resolved, dates, folder)
    external_keys = MASK_KEYS + ('mintpy.reference.coherenceFile', 'mintpy.timeFunc.timeSeriesCovFile', 'mintpy.network.referenceFile',
                                 'mintpy.topographicResidual.excludeDate', 'mintpy.timeFunc.excludeDate')
    # External files are copied so native tools can never mutate the originals.
    for key in external_keys:
        value = resolved.get(key)
        if key.endswith('excludeDate') and (not value or str(value) == 'exclude_date.txt' or ',' in str(value) or str(value).isdigit()):
            continue
        source = external_file(value, key, folder)
        if source:
            report['external_inputs'].append(fingerprint(source))
            dest = folder/'external-inputs'/(key.replace('.', '-')+source.suffix)
            dest.parent.mkdir(exist_ok=True)
            shutil.copy2(source, dest)
            config[key] = str(dest)
    if resolved.get('mintpy.troposphericDelay.method') == 'gacos':
        source = Path(os.path.expanduser(str(resolved['mintpy.troposphericDelay.gacosDir'])))
        target = folder/'external-inputs/GACOS'
        target.mkdir(parents=True, exist_ok=True)
        for date in dates:
            for ext in ('.ztd', '.ztd.rsc', '.ztd.tif'):
                f = source/(date+ext)
                if f.is_file():
                    report['external_inputs'].append(fingerprint(f));shutil.copy2(f, target/f.name)
        config['mintpy.troposphericDelay.gacosDir'] = str(target)
    elif resolved.get('mintpy.troposphericDelay.method') == 'pyaps':
        source = Path(os.path.expandvars(os.path.expanduser(str(resolved['mintpy.troposphericDelay.weatherDir']))))/'ERA5'
        target = folder/'external-inputs/weather/ERA5'
        target.mkdir(parents=True, exist_ok=True)
        for date in dates:
            for f in source.glob('*'+date+'_*.grb'):
                report['external_inputs'].append(fingerprint(f));shutil.copy2(f, target/f.name)
        config['mintpy.troposphericDelay.weatherDir'] = str(target.parent)
    report['resolved'] = resolve_template(config)
    save_report(job_path, report)
    return config


def seal_template(job_path):
    report = read_job(job_path)['parameter_report']
    report['template_sha256'] = hashlib.sha256((Path(job_path).parent/'mintpy.cfg').read_bytes()).hexdigest()
    save_report(job_path, report)


def validate_external_inputs(job_path):
    for saved in read_job(job_path).get('parameter_report', {}).get('external_inputs', []):
        try:
            current = fingerprint(saved['path'])
        except OSError as exc:
            raise ValueError('外部輔助資料已移動或刪除，請另建分析以保存一致的輸入。') from exc
        if current != saved:
            raise ValueError('外部輔助資料已改變，請另建分析以保存一致的輸入。')


def verify_network(dates, kept, required_dates):
    required = {date8(d) for d in required_dates}
    graph = {d:set() for d in required}
    edges = []
    for (a, b), keep in zip(dates, kept):
        if keep:
            if a not in graph or b not in graph:
                raise ValueError('MintPy 原生網路篩選保留了已排除日期，請檢查設定。')
            graph[a].add(b);graph[b].add(a);edges.append(a+'_'+b)
    isolated = sorted(d for d, neighbours in graph.items() if not neighbours)
    seen=set();todo=[min(required)] if required else []
    while todo:
        d=todo.pop()
        if d not in seen:
            seen.add(d);todo.extend(graph[d]-seen)
    if isolated or seen != required or not edges:
        raise ValueError('MintPy 原生網路篩選後，原本要求保留的日期已孤立或配對網路不連通；請放寬基線／相干性門檻或重新選擇日期。'+(' 孤立日期：'+', '.join(isolated) if isolated else ''))
    return {'dates': sorted(required), 'pairs': edges, 'connected': True}


def read_mask(value, shape, folder, required=False):
    if not value:
        return np.ones(shape, dtype=bool)
    p = Path(str(value));p = p if p.is_absolute() else Path(folder)/p
    if not p.is_file():
        if str(value) in INTERNAL_FILES and not required:
            return np.ones(shape, dtype=bool)
        raise ValueError('遮罩不存在：'+p.name)
    from mintpy.utils import readfile
    a, attributes = readfile.read(str(p), print_msg=False)
    if a.shape != tuple(shape):
        raise ValueError('遮罩尺寸與本次裁切後的雷達網格不同；請先準備相同網格的遮罩。')
    check_grid_metadata(attributes,folder)
    return np.isfinite(a)&(a!=0)


def check_grid_metadata(attributes,folder):
    reference=Path(folder)/'inputs/geometryRadar.h5'
    if not reference.is_file():return
    from mintpy.utils import readfile
    original=readfile.read_attribute(str(reference))
    if ('Y_FIRST' in attributes)!=('Y_FIRST' in original):
        raise ValueError('外部遮罩／相干性影像與本次雷達座標格網不同，不能直接使用已地理編碼的影像。')
    for key in ('X_FIRST','Y_FIRST','X_STEP','Y_STEP','SUBSET_XMIN','SUBSET_YMIN'):
        if key in original and key in attributes and not np.isclose(float(original[key]),float(attributes[key]),rtol=0,atol=1e-8):
            raise ValueError('外部遮罩／相干性影像的格網原點或解析度與本次分析不一致。')


def reference_mask(folder, shape, config):
    mask = np.ones(shape, dtype=bool)
    for key in ('mintpy.reference.maskFile', 'mintpy.networkInversion.waterMaskFile', 'mintpy.unwrapError.waterMaskFile'):
        mask &= read_mask(config.get(key), shape, folder)
    return mask


def reference_coherence(folder, average, config):
    value=config.get('mintpy.reference.coherenceFile')
    if not value or str(value)=='avgSpatialCoh.h5':return average
    path=Path(str(value));path=path if path.is_absolute() else Path(folder)/path
    from mintpy.utils import readfile
    a,attributes=readfile.read(str(path),print_msg=False)
    if a.shape!=average.shape:raise ValueError('參考點相干性影像必須與本次裁切網格相同。')
    check_grid_metadata(attributes,folder)
    finite=a[np.isfinite(a)]
    if finite.size and (finite.min()<0 or finite.max()>1):raise ValueError('參考點相干性影像需介於 0–1。')
    return a


def validate_loaded(job_path, dates, kept, shape):
    import h5py
    folder = Path(job_path).parent;job=read_job(job_path)
    config = resolve_file(folder/'mintpy.cfg')
    report = job['parameter_report']
    report['network'] = verify_network(dates, kept, job['options']['retained_dates'])
    retained = report['network']['dates']
    # A connected graph alone does not guarantee triangle redundancy for closure.
    if 'phase_closure' in str(config.get('mintpy.unwrapError.method')):
        from mintpy.objects import ifgramStack
        closure = ifgramStack.get_design_matrix4triplet(report['network']['pairs'])
        if closure is None or not len(closure):
            raise ValueError('相位閉合修正需要至少一組三角配對；目前保留網路沒有可用三角形。')
    for key in MASK_KEYS:
        read_mask(config.get(key), shape, folder)
    if config.get('mintpy.timeFunc.uncertaintyQuantification') == 'covariance':
        from mintpy.utils import readfile
        cov = Path(str(config['mintpy.timeFunc.timeSeriesCovFile']))
        with h5py.File(cov, 'r') as f:
            datasets=[];f.visititems(lambda n, obj: datasets.append(obj) if isinstance(obj, h5py.Dataset) and obj.ndim in (3,4) else None)
            if any(d.ndim==4 for d in datasets):
                raise ValueError('此 MintPy 版本的預設讀取器尚無法可靠載入 4D 完整共變異數；工作台目前僅支援 N×Y×X 的逐日期對角變異數。')
            expected = [(len(retained), *shape)]
            if len(datasets)!=1 or datasets[0].shape not in expected:
                raise ValueError('變異數需為本次保留日期順序與裁切網格的 N×Y×X。')
            if 'date' not in f or [v.decode() if isinstance(v,bytes) else str(v) for v in f['date'][:]] != retained:
                raise ValueError('共變異數檔需附 date 資料集且順序與保留日期完全相符。')
            dataset=datasets[0]
            unit=str(dataset.attrs.get('UNIT',f.attrs.get('UNIT','')))
            if unit not in ('m^2','m2','m**2','m²'):raise ValueError('共變異數檔必須明確標記 UNIT=m^2（目前時序單位為公尺）。')
            check_grid_metadata(dict(f.attrs),folder)
            # Bound memory even for large full covariance cubes.
            planes=int(np.prod(dataset.shape[:-2]));columns=max(1,min(shape[1],2_000_000//planes))
            rows=max(1,min(64,2_000_000//(planes*columns)))
            for row in range(0,shape[0],rows):
                for col in range(0,shape[1],columns):
                    a=dataset[...,row:row+rows,col:col+columns]
                    if not np.isfinite(a).all():raise ValueError('共變異數檔含非有限值。')
                    if np.any(a<0):raise ValueError('變異數不可為負值。')
        # Verify native dataset dispatch is unambiguous without reading the full cube.
        readfile.read(str(cov), box=(0,0,1,1), print_msg=False)
    with h5py.File(folder/'inputs/geometryRadar.h5','r') as geometry:
        if config.get('mintpy.solidEarthTides'):
            missing={'latitude','longitude','incidenceAngle','azimuthAngle'}-set(geometry)
            if missing:raise ValueError('固體潮校正缺少幾何資料：'+', '.join(sorted(missing)))
        if config.get('mintpy.troposphericDelay.method') and 'height' not in geometry:
            raise ValueError('大氣校正缺少 height 幾何資料。')
        if config.get('mintpy.troposphericDelay.method')=='gacos':
            from mintpy.utils import readfile
            lat=geometry['latitude'][:];lon=geometry['longitude'][:]
            valid=np.isfinite(lat)&np.isfinite(lon)&(np.abs(lat)<=90)&(np.abs(lon)<=180)&((lat!=0)|(lon!=0))
            if not valid.any():raise ValueError('無有效地理位置可檢查 GACOS 覆蓋。')
            south,north=float(lat[valid].min()),float(lat[valid].max())
            west,east=float(lon[valid].min()),float(lon[valid].max())
            directory=Path(str(config['mintpy.troposphericDelay.gacosDir']))
            for date in retained:
                ztd=next(directory/(date+ext) for ext in ('.ztd','.ztd.tif') if (directory/(date+ext)).is_file())
                atr=readfile.read_attribute(str(ztd))
                y0=float(atr['Y_FIRST']);y1=y0+float(atr['Y_STEP'])*(int(atr['LENGTH'])-1)
                x0=float(atr['X_FIRST']);x1=x0+float(atr['X_STEP'])*(int(atr['WIDTH'])-1)
                if not min(y0,y1)<=south<=north<=max(y0,y1) or not min(x0,x1)<=west<=east<=max(x0,x1):
                    raise ValueError('GACOS 地理覆蓋不足：'+date+'；需涵蓋本次裁切的所有像素。')
    if config.get('mintpy.solidEarthTides') or config.get('mintpy.troposphericDelay.method')=='pyaps':
        with h5py.File(folder/'inputs/ifgramStack.h5','r') as stack:
            utc=stack.attrs.get('CENTER_LINE_UTC')
        if utc is None or not np.isfinite(float(utc)):
            raise ValueError('固體潮／ERA5 校正缺少有效的影像擷取 UTC 時刻。')
        report['acquisition_utc_seconds']=float(utc)
    if config.get('mintpy.troposphericDelay.method')=='pyaps':
        from mintpy import tropo_pyaps3
        inps=SimpleNamespace(weather_dir=str(config['mintpy.troposphericDelay.weatherDir']),tropo_model='ERA5',
                             date_list=retained,hour=tropo_pyaps3.closest_weather_model_hour(utc),
                             dis_file=None,geom_file=str(folder/'inputs/geometryRadar.h5'))
        tropo_pyaps3.get_grib_info(inps)
        missing=[Path(f).name for f in inps.grib_files if not Path(f).is_file() or not Path(f).stat().st_size]
        if missing:raise ValueError('ERA5 快取缺少所需範圍／時刻；不會自動下載：'+', '.join(missing))
        report['weather_files']=[Path(f).name for f in inps.grib_files]
    save_report(job_path, report)
    return config


def before_step(job_path, name):
    """Revalidate late products immediately before the dependent native method."""
    folder=Path(job_path).parent;job=read_job(job_path)
    if 'parameter_report' not in job:
        return  # Backward-compatible continuation of an older analysis.
    config=resolve_file(folder/'mintpy.cfg');report=job['parameter_report']
    if name=='modify_network':
        import h5py
        with h5py.File(folder/'inputs/ifgramStack.h5','r') as f:shape=f['unwrapPhase'].shape[1:]
        read_mask(config.get('mintpy.network.maskFile'),shape,folder)
        aoi=config.get('mintpy.network.aoiYX')
        if aoi:
            yr,xr=str(aoi).split(',');y0,y1=[int(v) for v in yr.split(':')];x0,x1=[int(v) for v in xr.split(':')]
            if not 0<=y0<y1<=shape[0] or not 0<=x0<x1<=shape[1]:
                raise ValueError('網路篩選的 AOI 像素範圍需在本次裁切後網格內。')
    if name in ('velocity','correct_topography'):
        import h5py
        with h5py.File(folder/'timeseries.h5','r') as f:
            dates=[v.decode() for v in f['date'][:]]
            baselines=f['bperp'][:] if 'bperp' in f else None
        model=time_model(config,dates,folder,dem=name=='correct_topography') if name=='velocity' or config.get('mintpy.topographicResidual') else None
        if model:
            report['time_model' if name=='velocity' else 'dem_model']=model
        if name=='correct_topography' and model:
            if baselines is None:raise ValueError('DEM 誤差校正缺少日期基線，無法驗證其與變形模型可分離。')
            from mintpy.utils import time_func
            idx=[dates.index(d) for d in model['dates']]
            design=time_func.get_design_matrix4time_func(model['dates'],model['model']).astype('float64')
            design=np.column_stack([np.asarray(baselines)[idx].ravel(),design])
            if config.get('mintpy.topographicResidual.phaseVelocity'):
                design=np.diff(design,axis=0);design=np.delete(design,1,axis=1)
            if np.linalg.matrix_rank(design)<design.shape[1]:
                raise ValueError('DEM 誤差模型的基線項與變形項無法分離（秩不足）；請停用 DEM 校正或調整時間模型／日期。')
        if name=='velocity':
            # Native timeFunc.excludeDate may have been populated by residual_RMS.
            report['resolved']=config
    if name in ('correct_troposphere','deramp','residual_RMS'):
        import h5py
        with h5py.File(folder/'inputs/ifgramStack.h5','r') as f:shape=f['unwrapPhase'].shape[1:]
        key={'deramp':'mintpy.deramp.maskFile','residual_RMS':'mintpy.residualRMS.maskFile'}.get(name)
        if key and (name!='deramp' or config.get('mintpy.deramp')):
            read_mask(config.get(key),shape,folder,required=True)
    for correction in report['corrections']:
        if correction['step']==name and config.get(correction['key']):
            correction.update(state='running',detail='正在執行，尚未驗證產物。')
    save_report(job_path,report)


def after_step(job_path, name, error=None):
    folder=Path(job_path).parent;job=read_job(job_path)
    if 'parameter_report' not in job:return
    report=job['parameter_report'];config=resolve_file(folder/'mintpy.cfg')
    for correction in report['corrections']:
        if correction['step']!=name:continue
        if error:
            correction.update(state='failed',detail='執行或產物驗證失敗。')
        elif not config.get(correction['key']):
            correction.update(state='disabled',detail='本次設定關閉，MintPy 已跳過。')
        elif name=='correct_unwrap_error':
            import h5py
            method=config['mintpy.unwrapError.method']
            dataset='unwrapPhase_bridging_phaseClosure' if method=='bridging+phase_closure' else ('unwrapPhase_bridging' if method=='bridging' else 'unwrapPhase_phaseClosure')
            with h5py.File(folder/'inputs/ifgramStack.h5','r') as f:
                if dataset not in f:raise ValueError('解纏修正未產生預期資料集：'+dataset)
            correction.update(state='applied',detail='原生步驟完成並確認修正資料集；不代表修正效果已通過科學驗證。')
        else:
            from mintpy.smallbaselineApp import TimeSeriesAnalysis
            output=TimeSeriesAnalysis.get_timeseries_filename(config,str(folder))[name]
            if output['input']==output['output'] or not Path(output['output']).is_file():
                raise ValueError('校正未產生預期時序產物：'+correction['label'])
            correction.update(state='applied',detail='原生步驟完成並確認時序產物；不代表誤差已完全移除。',artifact=Path(output['output']).name)
            if name=='correct_troposphere' and config.get(correction['key'])=='height_correlation':
                correction['detail']='已執行高程相關估計並確認時序產物；未達 minCorrelation 的日期可能保留原值，需檢查日誌及校正前後差異。'
    save_report(job_path,report)


@contextmanager
def offline_weather():
    """Native ERA5 code must never silently download or delete original caches."""
    try:
        module=importlib.import_module('mintpy.tropo_pyaps3')
    except ImportError:
        yield;return
    original=module.dload_grib_files
    def existing_only(files,**kwargs):
        missing=[Path(f).name for f in files if not Path(f).is_file() or not Path(f).stat().st_size]
        if missing:raise ValueError('ERA5 快取缺少所需範圍／時刻的檔案；請補齊後另建分析：'+', '.join(missing))
        return files
    module.dload_grib_files=existing_only
    try:yield
    finally:module.dload_grib_files=original
