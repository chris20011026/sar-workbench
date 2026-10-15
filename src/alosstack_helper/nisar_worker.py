"""ISCE3-only worker. Reads sources; all writes stay in an owned job/output folder."""
from pathlib import Path
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import shutil
import subprocess
import sys
import traceback


def fingerprint(path):
    p = Path(path); s = p.stat()
    return dict(path=str(p.resolve()), size=s.st_size, mtime_ns=s.st_mtime_ns)


def verify_sources(request):
    if [fingerprint(p['path']) for p in request['source']] != request['source']:
        raise ValueError('檔案正在變動，請等資料複製完成後重新檢查。')


def environment(expected):
    import isce3
    from osgeo import gdal
    from nisar.workflows import insar  # imports ALL workflow dependencies, including whirlwind
    version = getattr(isce3, '__version__', '')
    if version != expected:
        raise ValueError(f'此介面已核對 ISCE3 {expected} 的設定格式；目前是 {version}，請使用相容環境。')
    if not gdal.GetDriverByName('HDF5'): raise ValueError('環境缺少 GDAL HDF5 driver。')
    import platform
    return dict(isce3=version, python=sys.version.split()[0], executable=sys.executable, architecture=platform.machine())


def check_inputs(c):
    import h5py
    from shapely import wkt
    from pyproj import CRS, Transformer
    from osgeo import gdal
    if __package__:
        from .library_worker import nisar, pair_checks, scalar
    else:
        from library_worker import nisar, pair_checks, scalar
    gdal.UseExceptions()
    products = [nisar(Path(c[k])) for k in ('reference', 'secondary')]
    if any(p['type'] != 'RSLC' for p in products): raise ValueError('輸入必須是兩期 NISAR RSLC，不能使用 GUNW 或其他產品。')
    pair = pair_checks(products)[0]
    if pair['issues']: raise ValueError('；'.join(pair['issues']))
    if products[0]['metadata']['zeroDopplerStartTime'] >= products[1]['metadata']['zeroDopplerStartTime']:
        raise ValueError('參考影像必須早於比較影像。')
    o = c['options']; freq, pol = o['frequency'], o['polarization']
    if pol not in pair['common'].get(freq, []): raise ValueError('兩期缺少所選頻段／極化。')
    if o['ionosphere'] and any(pol not in pair['common'].get(f, []) for f in ('A', 'B')):
        raise ValueError('本版電離層估計須兩期都有 A/B 頻段的相同極化。')
    footprints = []
    for path in (c['reference'], c['secondary']):
        with h5py.File(path, 'r') as h:
            key = 'science/LSAR/identification/boundingPolygon'
            if key not in h: raise ValueError('RSLC 缺少 boundingPolygon，無法檢查共同覆蓋。')
            p = wkt.loads(scalar(h[key][()]))
            if not p.is_valid or p.is_empty or p.geom_type not in ('Polygon', 'MultiPolygon'):
                raise ValueError('RSLC 覆蓋範圍無效。')
            if p.bounds[2] - p.bounds[0] > 180: raise ValueError('本版尚不支援跨越日期變更線的影像。')
            footprints.append(p)
    overlap = footprints[0].intersection(footprints[1])
    if overlap.is_empty or overlap.area <= 0: raise ValueError('兩期 RSLC 沒有共同地理覆蓋。')
    # Check the entire union, because upstream rdr2geo still processes the full RSLC.
    union = footprints[0].union(footprints[1]); bounds = union.bounds
    rasters = {}
    warnings = ['覆蓋檢查使用產品外框；實際有效資料、基線與科學品質仍需檢查。',
                'DEM 高程基準需與流程相容；此檢查無法由檔案可靠確認高程是橢球高或正高。',
                '輸出為單一配對 GUNW，相位不能直接當作地表垂直位移。',
                '本版尚未完成實際 RSLC 全流程驗證；請先作研究測試。']
    for key in ('dem', 'water'):
        if not c[key]: continue
        ds = gdal.Open(c[key], gdal.GA_ReadOnly)
        if not ds or ds.RasterCount != 1: raise ValueError(key + ' 必須是單波段地理參考 raster。')
        gt = ds.GetGeoTransform(); crs = CRS.from_wkt(ds.GetProjection())
        if gt[2] or gt[4] or gt[1] <= 0 or gt[5] >= 0: raise ValueError(key + ' 需為北向上、未旋轉的網格。')
        transform = Transformer.from_crs(4326, crs, always_xy=True)
        left, bottom, right, top = transform.transform_bounds(*bounds, densify_pts=41)
        raster_bounds = (gt[0], gt[3] + gt[5]*ds.RasterYSize, gt[0] + gt[1]*ds.RasterXSize, gt[3])
        if not all(math.isfinite(v) for v in (left, bottom, right, top)) or left < raster_bounds[0] or bottom < raster_bounds[1] or right > raster_bounds[2] or top > raster_bounds[3]:
            raise ValueError(key + ' 未涵蓋兩期完整 RSLC 外框。影像經緯度範圍：' + ', '.join(f'{v:.5f}' for v in bounds) + '（西、南、東、北）；請準備較大的地形／遮罩資料。')
        band = ds.GetRasterBand(1)
        import numpy as np
        sample = band.ReadAsArray(buf_xsize=min(ds.RasterXSize, 512), buf_ysize=min(ds.RasterYSize, 512))
        valid = np.isfinite(sample)
        nodata = band.GetNoDataValue()
        if nodata is not None: valid &= sample != nodata
        if not valid.any(): raise ValueError(key + ' 抽樣全部為無資料。')
        if key == 'water':
            values = set(np.unique(sample[valid]).tolist())
            if values <= {0, 1, 255}:
                raise ValueError('這份水體檔看起來是二元遮罩。ISCE3 此流程需要水岸距離編碼（0–200），不能直接套用；請先留白。')
            warnings.append('水體檔僅檢查可讀取與值域；請確認使用 NISAR 距離編碼，並核對其岸線與 NoData 定義。')
            if min(values) < 0 or max(values) > 200: raise ValueError('水體距離圖有效值應為 0–200。')
        rasters[key] = dict(epsg=crs.to_epsg(), shape=[ds.RasterYSize, ds.RasterXSize], bounds=raster_bounds, sampled_valid_fraction=float(valid.mean()))
        ds = None
    if o['posting'] is not None:
        crs = CRS.from_epsg(o['epsg'])
        if not crs.is_projected or any(abs(a.unit_conversion_factor - 1) > 1e-8 for a in crs.axis_info[:2]):
            raise ValueError('指定公尺間距時，EPSG 必須是以公尺為單位的投影。')
    if o['epsg'] is not None: CRS.from_epsg(o['epsg'])
    if not c['water']: warnings.append('未使用水體距離圖；低相干與水體區域的解纏結果需另外判讀。')
    estimate = sum(Path(c[k]).stat().st_size for k in ('reference', 'secondary')) * 6 + 10*1024**3
    free = shutil.disk_usage(Path(c['root']).parent).free
    if free < estimate: warnings.append('可用磁碟低於保守估計；開始運算按鈕會阻擋，請改用較大的輸出磁碟。')
    return dict(products=products, common=pair['common'], overlap_bounds=list(overlap.bounds), rasters=rasters,
                warnings=warnings, disk_estimate_bytes=estimate, free_bytes=free, disk_ok=free >= estimate)


def build_config(c, root):
    import yaml
    from nisar.workflows.helpers import WORKFLOW_SCRIPTS_DIR
    default = Path(WORKFLOW_SCRIPTS_DIR) / 'defaults/insar.yaml'
    cfg = yaml.safe_load(default.read_text()); g = cfg['runconfig']['groups']; o = c['options']
    root = Path(root)
    g['input_file_group'].update(reference_rslc_file=c['reference'], secondary_rslc_file=c['secondary'])
    g['dynamic_ancillary_file_group'].update(dem_file=c['dem'], water_mask_file=c['water'] or None)
    g['product_path_group'].update(product_path=str(root), scratch_path=str(root/'scratch'), sas_output_file=str(root/'GUNW.h5'), qa_output_dir=str(root/'qa'))
    g['primary_executable']['product_type'] = 'GUNW'
    g['worker'].update(gpu_enabled=False, internet_access=False, intermediate_files_removal_enabled=False)
    p = g['processing']
    p['input_subset']['list_of_frequencies'] = {o['frequency']: [o['polarization']]}
    p['crossmul'].update(range_looks=o['range_looks'], azimuth_looks=o['azimuth_looks'])
    p['phase_unwrap']['algorithm'] = 'snaphu'
    # 0.25.21 ships overlap=0 but its YAML schema requires >=4. A single tile
    # ignores overlap; use a schema-valid value without changing tiling behavior.
    p['phase_unwrap']['snaphu'].update(ntiles=[1, 1], tile_overlap=[4, 4], nproc=1)
    p['filter_interferogram']['filter_type'] = o['filter']
    for k in ('dense_offsets', 'rubbersheet', 'fine_resample'): p[k]['enabled'] = o['fine_registration']
    p['offsets_product']['enabled'] = False
    p['ionosphere_phase_correction']['enabled'] = o['ionosphere']
    if o['ionosphere']:
        p['ionosphere_phase_correction'].update(spectral_diversity='main_side_band', list_of_frequencies={'A': [o['polarization']], 'B': [o['polarization']]})
    p['geocode']['output_epsg'] = o['epsg']
    if o['posting'] is not None:
        for f in ('A', 'B'):
            p['geocode']['output_posting'][f] = dict(x_posting=o['posting'], y_posting=o['posting'])
    # Explicitly enable masking only when a distance map has been selected.
    if c['water']:
        preprocessing = p['phase_unwrap']['preprocess_wrapped_phase']
        preprocessing['enabled'] = True
        preprocessing['mask']['mask_type'] = ['subswath_mask', 'water']
    # Keep the main log on stdout for the manager, disable upstream persistence.
    return cfg


def validate_config(path):
    from nisar.workflows.insar_runconfig import InsarRunConfig
    args = argparse.Namespace(run_config_path=str(path), log_file=False, restart=True)
    obj = InsarRunConfig(args)
    p = obj.cfg['processing']
    return dict(frequencies=p['input_subset']['list_of_frequencies'], epsg=p['geocode']['output_epsg'],
                crossmul={k:p['crossmul'][k] for k in ('range_looks', 'azimuth_looks')},
                ionosphere=p['ionosphere_phase_correction']['enabled'], unwrap=p['phase_unwrap']['algorithm'])


def main(request):
    import tempfile
    import yaml
    c = request['config']; output = Path(c['root']); yaml_path = Path(request['yaml'])
    verify_sources(request)
    env = environment(request['expected_version'])
    if request['mode'] == 'check':
        result = check_inputs(c)
        with tempfile.TemporaryDirectory(prefix='validation-', dir=yaml_path.parent) as tmp:
            cfg = build_config(c, tmp)
            check = Path(tmp)/'validate.yaml'; check.write_text(yaml.safe_dump(cfg, sort_keys=False))
            result['resolved'] = validate_config(check)
        verify_sources(request)
        yaml_path.write_text(yaml.safe_dump(build_config(c, output), sort_keys=False))
        result['environment'] = env
        return result
    if request['mode'] != 'run': raise ValueError('未知的工作。')
    if yaml_path.is_symlink() or hashlib.sha256(yaml_path.read_bytes()).hexdigest() != request['config_sha256']:
        raise ValueError('設定檔已改變，停止執行。')
    output.mkdir()  # exclusive claim; never reuse an output directory
    shutil.copyfile(yaml_path, output/'insar.yaml')
    (output/'inputs.json').write_text(json.dumps(request['source'], indent=2))
    # Child inherits the worker's process group, so stop also stops every ISCE3 child.
    subprocess.run([sys.executable, '-m', 'nisar.workflows.insar', str(output/'insar.yaml'), '--no-log-file'], cwd=output, check=True)
    verify_sources(request)
    if __package__:
        from .library_worker import nisar
    else:
        from library_worker import nisar
    product = nisar(output/'GUNW.h5')
    available = {p['key'].rsplit('/', 1)[-1] for p in product['layers'] if min(p['shape']) > 0}
    if product['type'] != 'GUNW' or not {'unwrappedPhase','coherenceMagnitude','connectedComponents'} <= available:
        raise ValueError('GUNW 缺少非空的相位、相干性或連通區圖層。')
    return dict(product=product, environment=env, note='程式執行與成果結構檢查完成；尚需人工科學品質檢查。')


if __name__ == '__main__':
    req, res = map(Path, sys.argv[1:3])
    try:
        result = main(json.loads(req.read_text()))
        res.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        traceback.print_exc()
        res.write_text(json.dumps({'error':str(exc)}, ensure_ascii=False))
        sys.exit(1)
