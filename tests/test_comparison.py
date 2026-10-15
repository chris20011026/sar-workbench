"""Scientific comparisons use synthetic HDF5, not mocked array arithmetic."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.comparison import Comparisons
from alosstack_helper.analysis import Analyses
from alosstack_helper.engine import Engine

try:
    import numpy as np
    import h5py
    from alosstack_helper import comparison_worker as worker
except ImportError:
    worker = None


@unittest.skipIf(worker is None, 'requires the scientific environment: numpy, h5py')
class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.project = self.home / 'project'
        self.left = self.fixture('aaaaaaaaaaaa', 0.)
        self.right = self.fixture('bbbbbbbbbbbb', .002)
        self.out = self.home / 'local-state/comparison'
        self.spec = dict(id='cccccccccccc', project_id='test', left=str(self.left),
                         right=str(self.right), out=str(self.out))

    def tearDown(self):
        self.tmp.cleanup()

    def fixture(self, run, offset, shape=(8, 9)):
        folder = self.project / 'analyses' / run
        (folder / 'inputs').mkdir(parents=True)
        y, x = np.mgrid[:shape[0], :shape[1]]
        lat = 25 - y * .001 + x * .00013
        lon = 121 + x * .001 + y * .00011
        mask = np.ones(y.shape, dtype='uint8')
        mask[0, :] = 0
        velocity = y * .001 + x * .0001 + offset
        dates = ['20200101', '20210101', '20220101', '20230101']
        attrs = dict(UNIT='m/year', REF_Y='2', REF_X='2', REF_DATE=dates[0],
                     START_DATE=dates[0], END_DATE=dates[-1], HEADING='-168.5',
                     WAVELENGTH='.236', ORBIT_DIRECTION='descending')
        attrs.update({'mintpy.timeFunc.' + k: v for k, v in
                      dict(polynomial='1', periodic='[]', stepDate='[]', exp='[]', log='[]').items()})
        with h5py.File(folder / 'velocity.h5', 'w') as f:
            f['velocity'] = velocity
            f['velocityStd'] = np.full(y.shape, .0005)
            f.attrs.update(attrs)
        with h5py.File(folder / 'inputs/geometryRadar.h5', 'w') as f:
            for name, data in dict(latitude=lat, longitude=lon, waterMask=np.ones(y.shape),
                                   incidenceAngle=np.full(y.shape, 35.), azimuthAngle=np.full(y.shape, 12.)).items():
                f[name] = data
        with h5py.File(folder / 'maskTempCoh.h5', 'w') as f:
            f['mask'] = mask
        with h5py.File(folder / 'temporalCoherence.h5', 'w') as f:
            f['temporalCoherence'] = np.full(y.shape, .85)
        with h5py.File(folder / 'timeseries_corrected.h5', 'w') as f:
            f['timeseries'] = np.array([velocity * i for i in range(4)])
            f['date'] = np.array(dates, dtype='S8')
            f.attrs.update({**attrs, 'UNIT': 'm'})
        with h5py.File(folder / 'timeseries.h5', 'w') as f:
            f['timeseries'] = np.zeros((4, *y.shape))
            f['date'] = np.array(dates, dtype='S8')
            f.attrs.update({**attrs, 'UNIT': 'm'})
        job = dict(id=run, project_id='test', kind='mintpy', status='complete', created='2026-10-15',
                   environment=sys.prefix, timeseries_file=str(folder / 'timeseries_corrected.h5'),
                   source_fingerprint=[dict(path='/unchanged-source/file', size=100, mtime_ns=123)],
                   parameter_report=dict(time_model=dict(model=dict(polynomial=1, periodic=[], stepDate=[], exp={}, log={}),
                                                          dates=dates, nonlinear=False)),
                   options=dict(name='Synthetic ' + run, deramp='no', temporal_coherence=.7))
        (folder / 'analysis.json').write_text(json.dumps(job))
        (folder / 'results.json').write_text(json.dumps(dict(reference_pixel=[2, 2], direction='positive towards satellite')))
        return folder

    def change_job(self, folder, **values):
        job = json.loads((folder / 'analysis.json').read_text())
        job.update(values)
        (folder / 'analysis.json').write_text(json.dumps(job))

    def compare(self):
        return worker.compare(self.spec)

    def test_shared_grid_difference_matches_full_resolution_and_sources_unchanged(self):
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.project.rglob('*') if p.is_file()}
        result = self.compare()
        self.assertTrue(result['compatibility']['allow_difference'], result['compatibility'])
        self.assertEqual(result['coverage']['shared'], 63)
        self.assertAlmostEqual(result['difference']['statistics']['mean'], 2., places=10)
        self.assertEqual(result['left']['statistics']['velocity']['count'], 63)
        self.assertTrue(result['colour_scale']['shared'])
        self.assertEqual(result['left']['map']['min'], result['right']['map']['min'])
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.project.rglob('*') if p.is_file()})
        self.assertTrue(result['input_verification']['unchanged'])
        json.dumps(result, allow_nan=False)

    def test_display_index_points_to_corrected_timeseries_native_pixel(self):
        result = self.compare()
        meta = result['left']['map']
        indices = np.fromfile(self.out / meta['native_index_file'], dtype='<i4').reshape(meta['height'], meta['width'])
        target = int(next(v for v in indices.ravel() if v >= 3 * 9 and v < 7 * 9))
        y, x = divmod(target, 9)
        point = worker.point(result, y, x)
        self.assertTrue(point['same_coordinate'])
        self.assertTrue(point['left']['available'])
        self.assertEqual(point['left']['selected_timeseries'], 'timeseries_corrected.h5')
        self.assertGreater(point['left']['displacement_mm'][-1], 0)
        self.assertAlmostEqual(point['difference']['displacement_mm'][-1], 6.)
        self.assertAlmostEqual(point['latitude'], 25 - y * .001 + x * .00013)

    def test_downsampled_display_keeps_full_native_statistics_and_exact_index(self):
        left = self.fixture('dddddddddddd', 0., (702, 9))
        right = self.fixture('eeeeeeeeeeee', .002, (702, 9))
        self.spec.update(left=str(left), right=str(right))
        result = self.compare()
        meta = result['left']['map']
        self.assertEqual(meta['preview_stride'], 2)
        self.assertEqual(result['coverage']['shared'], 701 * 9)
        self.assertAlmostEqual(result['difference']['statistics']['mean'], 2., places=10)
        ids = np.fromfile(self.out / meta['native_index_file'], dtype='<i4')
        ids = ids[ids >= 0]
        self.assertTrue(np.all((ids // 9) % 2 == 0))
        self.assertTrue(np.all((ids % 9) % 2 == 0))
        y, x = divmod(int(ids[len(ids) // 2]), 9)
        point = worker.point(result, y, x)
        self.assertAlmostEqual(point['left']['velocity_mm_year'], y + x * .1)
        self.assertAlmostEqual(point['difference']['velocity_mm_year'], 2.)

    def test_different_grids_keep_independent_maps_but_block_numeric_difference(self):
        with h5py.File(self.right / 'inputs/geometryRadar.h5', 'a') as f:
            f['longitude'][:] += .0001
        result = self.compare()
        self.assertFalse(result['compatibility']['allow_difference'])
        self.assertFalse(result['compatibility']['same_grid'])
        self.assertIsNone(result['coverage'])
        self.assertIsNone(result['difference']['map'])
        self.assertTrue((self.out / result['right']['map']['file']).is_file())
        point = worker.point(result, 3, 3)
        self.assertTrue(point['left']['available'])
        self.assertFalse(point['right']['available'])
        self.assertIsNone(point['difference'])

    def test_different_reference_point_and_date_are_not_rereferenced(self):
        for name in ('velocity.h5', 'timeseries_corrected.h5'):
            with h5py.File(self.right / name, 'a') as f:
                f.attrs['REF_X'] = '3'
                f.attrs['REF_DATE'] = '20210101'
        (self.right / 'results.json').write_text(json.dumps(dict(reference_pixel=[2, 3], direction='positive towards satellite')))
        result = self.compare()
        failed = {item['code'] for item in result['compatibility']['checks'] if not item['passed']}
        self.assertTrue({'reference_point', 'reference_date'} <= failed)
        point = worker.point(result, 3, 3)
        self.assertTrue(point['left']['available'] and point['right']['available'])
        self.assertIsNone(point['difference'])
        self.assertNotEqual(point['left']['reference'], point['right']['reference'])

    def test_different_model_blocks_same_named_velocity_coefficients(self):
        job = json.loads((self.right / 'analysis.json').read_text())
        job['parameter_report']['time_model']['model']['periodic'] = [1]
        self.change_job(self.right, parameter_report=job['parameter_report'])
        with h5py.File(self.right / 'velocity.h5', 'a') as f:
            f.attrs['mintpy.timeFunc.periodic'] = '[1.0]'
        result = self.compare()
        self.assertFalse(result['compatibility']['allow_difference'])
        self.assertIn('time_model', [v['code'] for v in result['compatibility']['checks'] if not v['passed']])

    def test_same_model_but_different_fit_dates_are_blocked(self):
        job = json.loads((self.right / 'analysis.json').read_text())
        job['parameter_report']['time_model']['dates'] = job['parameter_report']['time_model']['dates'][1:]
        self.change_job(self.right, parameter_report=job['parameter_report'])
        with h5py.File(self.right / 'velocity.h5', 'a') as f:
            f.attrs['START_DATE'] = '20210101'
        result = self.compare()
        self.assertFalse(next(v['passed'] for v in result['compatibility']['checks'] if v['code'] == 'fit_dates'))

    def test_saved_model_must_match_actual_velocity_metadata(self):
        for folder in (self.left, self.right):
            with h5py.File(folder / 'velocity.h5', 'a') as f:
                f.attrs['mintpy.timeFunc.polynomial'] = '2'
        result = self.compare()
        self.assertFalse(next(v['passed'] for v in result['compatibility']['checks'] if v['code'] == 'time_model'))

    def test_unknown_metadata_fails_closed_while_maps_are_available(self):
        self.change_job(self.left, parameter_report={}, source_fingerprint=[])
        with h5py.File(self.left / 'velocity.h5', 'a') as f:
            del f.attrs['HEADING']
        result = self.compare()
        self.assertFalse(result['compatibility']['allow_difference'])
        failed = {v['code'] for v in result['compatibility']['checks'] if not v['passed']}
        self.assertTrue({'provenance', 'los', 'time_model', 'fit_dates'} <= failed)
        self.assertTrue((self.out / 'left.png').exists())

    def test_different_masks_use_intersection_and_preserve_missing_curve(self):
        with h5py.File(self.right / 'maskTempCoh.h5', 'a') as f:
            f['mask'][2, 3] = 0
        result = self.compare()
        self.assertEqual(result['coverage']['shared'], 62)
        self.assertEqual(result['coverage']['left_only'], 1)
        self.assertEqual(result['difference']['statistics']['count'], 62)
        point = worker.point(result, 2, 3)
        self.assertTrue(point['left']['available'])
        self.assertFalse(point['right']['available'])
        self.assertIsNone(point['difference'])

    def test_no_common_valid_pixels_has_no_difference_map(self):
        with h5py.File(self.left / 'maskTempCoh.h5', 'a') as f:
            f['mask'][4:, :] = 0
        with h5py.File(self.right / 'maskTempCoh.h5', 'a') as f:
            f['mask'][:4, :] = 0
        result = self.compare()
        self.assertEqual(result['coverage']['shared'], 0)
        self.assertFalse(result['compatibility']['allow_difference'])
        self.assertIsNone(result['difference']['map'])

    def test_identical_missing_incidence_angles_do_not_establish_los_compatibility(self):
        for folder in (self.left, self.right):
            with h5py.File(folder / 'inputs/geometryRadar.h5', 'a') as f:
                f['incidenceAngle'][:] = np.nan
        result = self.compare()
        self.assertFalse(next(v['passed'] for v in result['compatibility']['checks'] if v['code'] == 'los'))

    def test_units_convert_explicitly_and_unknown_unit_disables_difference(self):
        with h5py.File(self.right / 'velocity.h5', 'a') as f:
            f['velocity'][:] *= 1000
            f['velocityStd'][:] *= 1000
            f.attrs['UNIT'] = 'mm/year'
        result = self.compare()
        self.assertAlmostEqual(result['difference']['statistics']['mean'], 2.)
        with h5py.File(self.right / 'velocity.h5', 'a') as f:
            f.attrs['UNIT'] = 'radians/year'
        result = self.compare()
        self.assertFalse(result['compatibility']['allow_difference'])
        self.assertFalse(result['colour_scale']['shared'])
        self.assertEqual(result['right']['unit'], 'radians/year')

    def test_read_corrected_timeseries_after_verified_project_relocation(self):
        (self.project / '.helper').mkdir()
        (self.project / '.helper/state.json').write_text(json.dumps(dict(config=dict(root=str(self.project)))))
        moved = self.home / 'moved'
        shutil.copytree(self.project, moved)
        shutil.rmtree(self.project)
        self.spec.update(left=str(moved / 'analyses' / self.left.name), right=str(moved / 'analyses' / self.right.name))
        result = self.compare()
        point = worker.point(result, 3, 3)
        self.assertGreater(point['left']['displacement_mm'][-1], 0)
        self.assertEqual(point['left']['selected_timeseries'], 'timeseries_corrected.h5')

    def test_missing_corrected_product_never_falls_back_to_uncorrected(self):
        (self.left / 'timeseries_corrected.h5').unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.compare()
        self.assertFalse((self.out / 'result.json').exists())

    def test_source_change_after_comparison_blocks_curves(self):
        result = self.compare()
        with h5py.File(self.right / 'timeseries_corrected.h5', 'a') as f:
            f['timeseries'][1, 3, 3] += 1
        with self.assertRaisesRegex(ValueError, '改變'):
            worker.point(result, 3, 3)

    def test_source_change_during_render_never_publishes_manifest(self):
        original = worker.image_preview
        changed = False
        def alter(*args, **kwargs):
            nonlocal changed
            value = original(*args, **kwargs)
            if not changed:
                changed = True
                # A JSON source remains closed while HDF5 files are read-only.
                self.change_job(self.left, created='changed during comparison')
            return value
        with patch.object(worker, 'image_preview', side_effect=alter):
            with self.assertRaisesRegex(ValueError, '改變'):
                self.compare()
        self.assertFalse((self.out / 'result.json').exists())

    def test_nonfinite_data_is_masked_and_json_stays_finite(self):
        with h5py.File(self.right / 'velocity.h5', 'a') as f:
            f['velocity'][3, 3] = np.nan
        result = self.compare()
        self.assertEqual(result['coverage']['shared'], 62)
        json.dumps(result, allow_nan=False)
        self.assertFalse(worker.point(result, 3, 3)['right']['available'])

    def test_outside_or_symlink_timeseries_is_rejected(self):
        external = self.home / 'external.h5'
        shutil.copy2(self.left / 'timeseries_corrected.h5', external)
        self.change_job(self.left, timeseries_file=str(external))
        with self.assertRaisesRegex(ValueError, '資料夾'):
            self.compare()
        self.change_job(self.left, timeseries_file=str(self.left / 'timeseries_corrected.h5'))
        (self.left / 'timeseries_corrected.h5').unlink()
        (self.left / 'timeseries_corrected.h5').symlink_to(external)
        with self.assertRaises(ValueError):
            self.compare()

    def test_external_hdf5_payload_cannot_bypass_source_fingerprint(self):
        path = self.left / 'inputs/geometryRadar.h5'
        external = self.home / 'external-geometry.h5'
        shutil.copy2(path, external)
        with h5py.File(path, 'a') as f:
            del f['latitude']
            f['latitude'] = h5py.ExternalLink(str(external), '/latitude')
        with self.assertRaisesRegex(ValueError, '間接連結'):
            self.compare()

    def test_service_starts_without_reading_scientific_arrays_and_artifact_paths_are_scoped(self):
        engine = Engine(self.home / 'state')
        state = dict(id='test', config=dict(root=str(self.project), env=sys.prefix), status='complete')
        engine.save(state)
        engine.register('test', self.project)
        service = Comparisons(Analyses(engine))
        candidates = service.candidates('test')
        self.assertEqual(len(candidates['runs']), 2)
        with patch('threading.Thread.start'):
            queued = service.start('test', self.left.name, self.right.name)
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(engine.active, 'test:comparison:' + queued['id'])
        with self.assertRaisesRegex(ValueError, '尚未完成'):
            service.artifact('test', queued['id'], '../../analysis.json')
        self.assertEqual(service.cancel('test', queued['id'])['status'], 'cancelling')
        with self.assertRaises(ValueError):
            service.folder('test', '../foo')

    def test_background_service_runs_selected_environment_and_publishes_only_final(self):
        # Exercise the actual Conda subprocess and parent-lifetime pipe.
        if not (Path(sys.prefix) / 'conda-meta').is_dir():
            self.skipTest('requires a Conda scientific environment')
        engine = Engine(self.home / 'state')
        engine.save(dict(id='test', config=dict(root=str(self.project), env=sys.prefix), status='complete'))
        engine.register('test', self.project)
        service = Comparisons(Analyses(engine))
        value = service.compare('test', self.left.name, self.right.name, timeout=60)
        self.assertEqual(value['status'], 'complete')
        self.assertIsNone(engine.active)
        self.assertTrue(service.artifact('test', value['id'], 'left.png').is_file())
        with self.assertRaises(ValueError):
            service.artifact('test', value['id'], 'request.json')
        result = service.point('test', value['id'], 3, 3)
        self.assertTrue(result['left']['available'])
        self.assertTrue(service.verify('test', value['id'])['verified'])
        self.change_job(self.right, created='modified after completed comparison')
        with self.assertRaisesRegex(ValueError, '改變'):
            service.verify('test', value['id'])

    def test_immediate_cancel_and_shutdown_leave_originals_and_no_published_artifact(self):
        if not (Path(sys.prefix) / 'conda-meta').is_dir():
            self.skipTest('requires a Conda scientific environment')
        engine = Engine(self.home / 'state')
        engine.save(dict(id='test', config=dict(root=str(self.project), env=sys.prefix), status='complete'))
        engine.register('test', self.project)
        service = Comparisons(Analyses(engine))
        before = (self.left / 'analysis.json').read_bytes()
        queued = service.start('test', self.left.name, self.right.name)
        service.cancel('test', queued['id'])
        service.shutdown()
        value = service.get('test', queued['id'])
        self.assertEqual(value['status'], 'cancelled', value)
        self.assertIsNone(engine.active)
        self.assertEqual(before, (self.left / 'analysis.json').read_bytes())
        with self.assertRaises(ValueError):
            service.artifact('test', queued['id'], 'left.png')


if __name__ == '__main__':
    unittest.main()
