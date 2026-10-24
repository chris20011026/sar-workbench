"""Run known-answer science checks in a selected scientific Python only.

No test reads a registered project. Every raster and geographic coordinate here
is artificial; passing these checks is not a scientific validation certificate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alosstack_helper.core import atomic_json
from alosstack_helper.selftest import CASES, LIMITATIONS


class Suite:
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.report = dict(run_id=self.output.name, status='running', synthetic=True,
            started=time.strftime('%Y-%m-%d %H:%M:%S'), checks=[],
            cases=[dict(c, status='pending') for c in CASES], limitations=LIMITATIONS,
            stage='準備原生科學檢核', progress=0)
        self.report['known_answers'] = {
            'pipeline': 'phase = 1 + delta(decimal_year) * (x + 0.2*y) * 0.01; velocity = -0.236/(4*pi) * (x + 0.2*y) * 0.01 m/year',
            'closure': 'add +2*pi to one pair only, rows [5,11), cols [10,22); expected nonzero closure only in that patch',
            'dem': 'height error = 1.5*(x+2*y) m; LOS = velocity*time + B_perp/(700000*sin(35 deg))*height_error',
            'rms': 'residual amplitudes [1,2,1.5,2.5,120,1.8,2.1,1.2] mm; expected suggested date 20210101',
            'geography': 'artificial 16x32 grid, latitude=25.01-0.0003*y, longitude=121+0.0003*x; no real observation',
        }
        self.current = None
        self.save()

    def save(self):
        atomic_json(self.output / 'report.json', self.report)

    def case(self, key):
        if self.current:
            self.current['status'] = 'passed'
        self.current = next(c for c in self.report['cases'] if c['id'] == key)
        self.current['status'] = 'running'
        self.report.update(stage=self.current['label'], progress=20 * sum(c['status'] == 'passed' for c in self.report['cases']))
        self.save()

    def check(self, key, label, passed, details=None):
        row = dict(id=key, label=label, case=self.current['id'], status='passed' if bool(passed) else 'failed')
        if details is not None: row['details'] = details
        self.report['checks'].append(row)
        self.save()
        print(('PASS ' if passed else 'FAIL ') + label, flush=True)
        if not passed: raise AssertionError(label + ': ' + str(details))

    @staticmethod
    def hashes(folder):
        return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in folder.rglob('*') if p.is_file()}

    def execute(self):
        try:
            self.pipeline()
            self.closure_error()
            self.dem_error()
            self.anomalous_date()
            self.geography()
            self.current['status'] = 'passed'
            self.report.update(status='passed', stage='五組合成案例全部通過', progress=100)
        except Exception as error:
            if self.current: self.current['status'] = 'failed'
            self.report.update(status='failed', error=str(error), traceback=traceback.format_exc())
            if not self.report['checks'] or self.report['checks'][-1]['status'] != 'failed':
                self.report['checks'].append(dict(id='execution', label='原生步驟執行',
                    case=self.current['id'] if self.current else 'environment', status='failed', details=str(error)))
            traceback.print_exc()
        finally:
            self.report['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
            self.save()
        return self.report['status'] == 'passed'

    def pipeline(self):
        import numpy as np
        import h5py
        from itertools import combinations
        from mintpy.utils import ptime
        from alosstack_helper import science_worker as worker, mintpy_diagnostics as diagnostics
        from alosstack_helper.selftest_fixture import dataset, raster
        from alosstack_helper.legacy import create_wrapper, inspect_legacy
        from alosstack_helper.workflows import inventory, options

        self.case('pipeline')
        self.report['versions'] = worker.versions()
        base = self.output / 'pipeline'
        request, _ = dataset(base, Path(sys.prefix))
        request['name'] = '人工合成自測（非真實 SAR 觀測）'
        source = Path(request['source'])
        dates = ['170826', '181103', '190518', '200101', '210101']
        pairs = ['-'.join(v) for v in combinations(dates, 2)]
        suffix = '_8rlks_16alks'
        y, x = np.mgrid[:16, :32]
        self.expected_velocity = -.236 / (4 * np.pi) * (x + y * .2) * .01
        for pair in pairs:
            p = source / 'pairs' / pair / 'insar'
            if not p.exists():
                for name in (f'filt_{pair}{suffix}.unw', f'filt_{pair}{suffix}_msk.unw'):
                    raster(p / name, bands=2)
                raster(p / f'{pair}{suffix}.cor', bands=2, value=.9)
                raster(p / f'filt_{pair}{suffix}.unw.conncomp', kind='Byte')
            a, b = ptime.yyyymmdd2years(['20' + d for d in pair.split('-')])
            raw = np.ones((16, 2, 32), dtype='<f4')
            raw[:, 1, :] = 1 + (b - a) * (x + y * .2) * .01
            for f in p.glob('*.unw'): f.write_bytes(raw.tobytes())
        (source / 'baseline/baseline_center.txt').write_text(
            'reference secondary parallel perpendicular\nYYMMDD YYMMDD meters meters\n'
            '181103 170826 2.0 12.0\n181103 190518 3.0 24.0\n181103 200101 4.0 -15.0\n181103 210101 5.0 50.0\n')
        geom = source / 'dates_resampled/181103/insar'
        (geom / f'181103{suffix}.lat').write_bytes(np.asarray(25.01 - y * .0003, dtype='<f8').tobytes())
        (geom / f'181103{suffix}.lon').write_bytes(np.asarray(121. + x * .0003, dtype='<f8').tobytes())
        before = self.hashes(source)
        state = create_wrapper(request, inspect_legacy(request), 'aabbccddeeff')
        inv = inventory(state)
        folder = Path(state['config']['root']) / 'analyses' / 'selftest'
        folder.mkdir(parents=True)
        job_path = folder / 'analysis.json'
        job = dict(id='selftest', kind='mintpy', status='running', history=[], inventory=inv,
                   options=options({'dem_error': True}, inv), environment=str(sys.prefix),
                   source_ion=state['ion'], source_ion_excluded=[], reference_pixel=[0, 0])
        atomic_json(job_path, job)
        worker.load(job_path)
        loaded = json.loads((folder / 'loaded.json').read_text())
        self.check('loaded', '載入十組配對與 512 個像素', len(loaded['dates']) == 10 and [loaded['height'], loaded['width']] == [16, 32])
        worker.invert(job_path)
        first = json.loads((folder / 'diagnostics.json').read_text())
        self.check('unwrap_gate', '解纏判讀停在反演之前', first['stage'] == 'unwrap' and not (folder / 'timeseries.h5').exists())
        self.check('network', '五日期網路連通且包含十個閉合三角形', first['network']['connected'] and first['network']['triangles'] == 10)
        self.check('clean_closure', '乾淨案例的原生閉合圖可讀', first['closure']['status'] == 'available')
        choice = diagnostics.decision(first, dict(reviewed=True, notes='已知答案合成自測；不核准任何真實專案。'))
        job = json.loads(job_path.read_text()); job['review_pending'] = choice; atomic_json(job_path, job)
        worker.corrections(job_path)
        second = json.loads((folder / 'diagnostics.json').read_text())
        self.check('residual_gate', '殘差判讀停在速度估計之前', second['stage'] == 'residual' and not (folder / 'velocity.h5').exists())
        self.check('native_rms', '原生 DEM 校正與五日期 RMS 表完成', second['rms']['status'] == 'available' and len(second['rms']['rows']) == 5)
        choices = dict(reviewed=True, notes='合成自測明確保留全部日期。', excluded_dates=[], reference_date=second['dates'][0])
        job = json.loads(job_path.read_text()); job['review_pending'] = diagnostics.decision(second, choices); atomic_json(job_path, job)
        worker.finalize(job_path)
        with h5py.File(folder / 'velocity.h5') as f: actual = f['velocity'][:]
        err = float(np.max(np.abs(actual - self.expected_velocity)))
        self.check('known_velocity', '所有像素速度符合已知答案', np.isfinite(actual).all() and err < 2e-7, {'max_error_mm_year': err * 1000, 'tolerance_mm_year': .0002})
        final = json.loads(job_path.read_text())
        with h5py.File(final['timeseries_file']) as f:
            ts = f['timeseries'][:]; ts_dates = [v.decode() for v in f['date'][:]]
        years = np.asarray(ptime.yyyymmdd2years(ts_dates))
        expected = (years - years[0])[:, None, None] * self.expected_velocity
        error = float(np.max(np.abs(ts - expected)))
        self.check('known_timeseries', '所有日期時序符合已知位移', np.isfinite(ts).all() and error < 2e-7, {'max_error_mm': error * 1000})
        self.check('source_unchanged', '載入與校正沒有改動來源資料', before == self.hashes(source), {'source_files': len(before)})
        self.pipeline_folder, self.pipeline_inventory = folder, inv
        self.source, self.source_hashes = source, before

    def closure_error(self):
        import h5py
        import numpy as np
        from mintpy.unwrap_error_phase_closure import calc_num_triplet_with_nonzero_integer_ambiguity
        from alosstack_helper import mintpy_diagnostics as diagnostics
        self.case('closure')
        folder = self.output / 'known-closure'; (folder / 'inputs').mkdir(parents=True); (folder / 'exports').mkdir()
        stack = folder / 'inputs/ifgramStack.h5'
        shutil.copy2(self.pipeline_folder / 'inputs/ifgramStack.h5', stack)
        region = np.zeros((16, 32), bool); region[5:11, 10:22] = True
        with h5py.File(stack, 'a') as f:
            phase = f['unwrapPhase'][0]; phase[region] += 2 * np.pi; f['unwrapPhase'][0] = phase
        target = folder / 'numTriNonzeroIntAmbiguity.h5'
        calc_num_triplet_with_nonzero_integer_ambiguity(str(stack), out_file=str(target), update_mode=False)
        with h5py.File(target) as f:
            a = next(f[k][:] for k in f if isinstance(f[k], h5py.Dataset) and f[k].ndim == 2)
        self.check('cycle_detected', '加入一整週錯誤的區域出現非零閉合', np.all(a[region] > 0), {'injected_radians': float(2*np.pi), 'affected_pixels': int(region.sum())})
        self.check('cycle_localized', '未加入錯誤的區域保持零閉合', np.all(a[~region] == 0))
        report = diagnostics.closure(folder, 10)
        self.check('closure_report', '工作台診斷呈現原生錯誤圖與統計', report['status'] == 'available' and sum(report['histogram']['counts']) == 512)

    def dem_error(self):
        import h5py
        import numpy as np
        from mintpy.utils import ptime
        from mintpy.cli.dem_error import main
        self.case('dem')
        folder = self.output / 'known-dem'; folder.mkdir(); os.chdir(folder)
        dates = ['20200101', '20200401', '20200701', '20201001', '20210101', '20210401', '20210701', '20211001']
        years = np.asarray(ptime.yyyymmdd2years(dates)); years -= years[0]
        baseline = np.asarray([0, 35, -60, 90, -15, 55, -45, 20], dtype='f4')
        y, x = np.mgrid[:12, :16]
        height_error = (x + 2*y).astype('f4') * 1.5
        velocity = .0004*x - .0007*y
        expected = years[:, None, None] * velocity
        disturbance = baseline[:, None, None] / (700000 * np.sin(np.deg2rad(35))) * height_error
        path = folder / 'timeseries.h5'
        with h5py.File(path, 'w') as f:
            f['timeseries'] = np.asarray(expected + disturbance, dtype='f4'); f['date'] = np.asarray(dates, dtype='S8'); f['bperp'] = baseline
            f.attrs.update(FILE_TYPE='timeseries', UNIT='m', LENGTH='12', WIDTH='16', REF_Y='0', REF_X='0', REF_DATE=dates[0], WAVELENGTH='.236')
        with h5py.File(folder / 'geometryRadar.h5', 'w') as f:
            f['incidenceAngle'] = np.full((12, 16), 35, dtype='f4'); f['slantRangeDistance'] = np.full((12, 16), 700000, dtype='f4')
            f.attrs.update(FILE_TYPE='geometry', LENGTH='12', WIDTH='16')
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        main([str(path), '-g', str(folder/'geometryRadar.h5'), '-p', '1', '-o', str(folder/'corrected.h5')])
        with h5py.File(folder / 'corrected.h5') as f: corrected = f['timeseries'][:]
        with h5py.File(folder / 'demErr.h5') as f: recovered = f['dem'][:]
        err = float(np.max(np.abs(corrected-expected)))
        dz = float(np.max(np.abs(recovered-height_error)))
        self.check('dem_recovered', '估計的 DEM 誤差符合已知注入值', dz < .02, {'max_height_error_m': dz, 'injected_max_height_m': float(height_error.max())})
        self.check('deformation_preserved', '去除 DEM 誤差後保留真實線性位移', err < 2e-7, {'max_displacement_error_mm': err*1000})
        self.check('dem_input_unchanged', 'DEM 校正保留原始時序檔', hashlib.sha256(path.read_bytes()).hexdigest() == before)

    def anomalous_date(self):
        import h5py
        import numpy as np
        from mintpy.cli.timeseries_rms import main
        from alosstack_helper import mintpy_diagnostics as diagnostics
        self.case('rms')
        folder = self.output / 'known-rms'; folder.mkdir(); os.chdir(folder)
        dates = ['20200101', '20200401', '20200701', '20201001', '20210101', '20210401', '20210701', '20211001']
        values = np.asarray([.001, .002, .0015, .0025, .12, .0018, .0021, .0012], dtype='f4')
        path = folder / 'timeseriesResidual.h5'
        with h5py.File(path, 'w') as f:
            f['timeseries'] = values[:, None, None] * np.ones((8, 12, 16), dtype='f4'); f['date'] = np.asarray(dates, dtype='S8')
            f.attrs.update(FILE_TYPE='timeseries', UNIT='m', LENGTH='12', WIDTH='16', REF_DATE=dates[0])
        with h5py.File(folder / 'maskTempCoh.h5', 'w') as f:
            f['mask'] = np.ones((12,16), dtype='u1'); f.attrs.update(FILE_TYPE='mask', LENGTH='12', WIDTH='16')
        cfg = folder / 'mintpy.cfg'
        cfg.write_text('mintpy.residualRMS.deramp = no\nmintpy.residualRMS.maskFile = maskTempCoh.h5\nmintpy.residualRMS.cutoff = 3\n')
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        main([str(path), '-t', str(cfg)])
        report = diagnostics.rms(folder, {'mintpy.residualRMS.deramp': False, 'mintpy.residualRMS.cutoff': 3}, dates)
        self.check('anomaly_detected', '120 mm 異常日期被建議檢查', report['status'] == 'available' and report['recommended_excluded_dates'] == [dates[4]], {'suggested_dates': report['recommended_excluded_dates']})
        self.check('rms_value', 'RMS 表保留正確單位與數值', abs(report['rows'][4]['rms_mm']-120) < .001)
        self.check('rms_readonly', '診斷只產生建議，不刪除日期或改動時序', hashlib.sha256(path.read_bytes()).hexdigest() == before)

    def geography(self):
        import h5py
        import numpy as np
        from osgeo import gdal, osr
        # geo_worker is also a standalone CLI; its imports are intentionally
        # resolved from the same bundled source directory.
        sys.path.insert(0, str(Path(__file__).parent))
        from alosstack_helper import geo_worker as geo
        self.case('geography')
        folder = self.pipeline_folder
        science_files = list((folder/'inputs').glob('*.h5')) + list(folder.glob('*.h5')) + [folder/'mintpy.cfg', folder/'analysis.json']
        protect = lambda: {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest() for p in science_files}
        protected = protect()
        spec = dict(folder=str(folder), out=str(folder/'exports'), mode='result', prefix='selftest-map')
        meta = geo.map_view(spec)
        self.check('map', '人工網格地圖包含速度、相干性與不確定性', meta['valid_pixels'] == 512 and all(k in meta['layers'] for k in ('velocity', 'coherence', 'uncertainty')))
        roi = geo.roi(dict(inventory=self.pipeline_inventory, bounds=[25.0078,25.0092,121.0012,121.0045]))
        self.check('roi', '框選範圍轉為較小的原生像素矩形', 0 < roi['selected_pixels'] < 512 and len(roi['subset']) == 4)
        with h5py.File(folder/'inputs/geometryRadar.h5') as f: coord = [float(f['latitude'][7,11]), float(f['longitude'][7,11])]
        picked = geo.pick(dict(spec, coordinate=coord, native=True, threshold=.7))
        self.check('point', '地圖取點對應正確原生像素及速度', [picked['y'],picked['x']] == [7,11] and abs(picked['velocity']-self.expected_velocity[7,11]*1000) < .0002)
        for layer, crs in [('velocity','EPSG:4326'), ('velocity','EPSG:3826'), ('coherence','EPSG:4326'), ('uncertainty','EPSG:4326')]:
            result = geo.export(dict(spec, layer=layer, crs=crs, resolution=30, threshold=.7))
            ds = gdal.Open(str(folder/'exports'/result['file'])); a = ds.ReadAsArray(); sr = osr.SpatialReference(wkt=ds.GetProjection())
            unit = ds.GetRasterBand(1).GetUnitType()
            self.check('export_'+layer+'_'+crs[-4:], 'GeoTIFF '+layer+' '+crs+' 的座標與單位正確', sr.GetAuthorityCode(None) == crs.split(':')[1] and unit == ('1' if layer=='coherence' else 'mm/year') and np.isfinite(a).any())
            ds = None
        figure = geo.figure(dict(spec, layer='velocity', threshold=.7))
        self.check('figure', '可輸出獨立圖版', (folder/'exports'/figure['file']).stat().st_size > 10000)
        self.check('geo_readonly', '地圖與匯出不修改來源或科學產物', protected == protect() and self.source_hashes == self.hashes(self.source))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    return 0 if Suite(args.output).execute() else 1


if __name__ == '__main__': sys.exit(main())
