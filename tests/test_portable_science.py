"""Native MintPy consumes a moved portable package without its old sources.

Every image/orbit/geographic value is artificial. This regression establishes
file portability and known-answer software behavior, not real SAR validity.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest

from alosstack_helper.core import atomic_json, find_conda
from alosstack_helper.environment import isolated_env
from alosstack_helper.portable import (
    plan_export, export_package, inspect_package, import_package,
    require_portable_source,
)
from alosstack_helper.selftest_fixture import dataset
from alosstack_helper.workflows import inventory, options

NATIVE = all(importlib.util.find_spec(name) is not None for name in ('isce', 'mintpy', 'numpy', 'h5py'))


@unittest.skipUnless(NATIVE, 'requires native ISCE2 / MintPy scientific Python')
class PortableScientificTests(unittest.TestCase):
    def hashes(self, root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}

    def run_stage(self, stage, job):
        from alosstack_helper import science_worker
        result = subprocess.run(
            [self.conda, 'run', '--no-capture-output', '-p', str(self.prefix),
             'python', str(Path(science_worker.__file__).resolve()), stage, str(job)],
            cwd=job.parent, env=isolated_env(job.parent/'cache'),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=150,
        )
        self.assertEqual(result.returncode, 0, stage + '\n' + result.stdout[-18000:])

    def test_moved_package_runs_known_phase_pipeline_without_old_sources(self):
        import h5py
        import numpy as np
        from mintpy.utils import ptime
        from alosstack_helper import mintpy_diagnostics as diagnostics

        self.prefix = Path(sys.prefix)
        self.conda = find_conda(self.prefix)
        if not self.conda:
            self.skipTest('scientific environment has no associated Conda executable')
        with tempfile.TemporaryDirectory(prefix='portable-native-') as tmp:
            base = Path(tmp).resolve()
            request, pairs = dataset(base, self.prefix)
            source = Path(request['source'])
            y, x = np.mgrid[:16, :32]
            spatial_slope = (x + .2*y) * .01
            expected = {}
            for pair in pairs:
                first, second = ptime.yyyymmdd2years(['20'+day for day in pair.split('-')])
                phase = np.asarray(1 + (second-first)*spatial_slope, dtype='<f4')
                expected[pair] = phase.copy()
                raw = np.ones((16, 2, 32), dtype='<f4')
                raw[:, 1, :] = phase
                for raster in (source/'pairs'/pair/'insar').glob('*.unw'):
                    raster.write_bytes(raw.tobytes())
            state = dict(id='abcdef123456', status='complete',
                config=dict(root=str(source.parent), workflow='alosStack',
                    name='人工合成可攜性測試', reference='181103', env=str(self.prefix),
                    properties={'polarization':'HH'}), expected_pairs=pairs,
                history=[dict(action='synthetic fixture')], excluded=[],
                ion=dict(estimate=True, apply=True))
            (source.parent/'.helper').mkdir()
            atomic_json(source.parent/'.helper/state.json', state)
            original_hashes = self.hashes(source.parent)

            package = base/'package'
            export_package(state, plan_export(state, str(package)))
            self.assertEqual(original_hashes, self.hashes(source.parent))
            package_hashes = self.hashes(package)
            # Break all previously valid source/package paths before consumption.
            hidden_source = base/'original-unavailable'
            source.parent.rename(hidden_source)
            moved_package = base/'different-disk-package'
            package.rename(moved_package)
            self.assertFalse(source.exists())
            self.assertFalse(package.exists())
            import_request = dict(source=str(moved_package), root=str(base/'new-analysis-project'),
                                  environment=str(self.prefix), name='重新匯入的合成案例')
            imported = import_package(import_request, inspect_package(import_request), '112233aabbcc')
            hidden_package = base/'package-now-disconnected'
            moved_package.rename(hidden_package)
            self.assertFalse(moved_package.exists())
            self.assertTrue(imported['import_only'])
            self.assertEqual(imported['status'], 'imported')
            self.assertEqual(imported['history'], [])
            require_portable_source(imported)
            inv = inventory(imported)
            root = Path(imported['config']['root'])
            independent_inputs = self.hashes(root/'proc')
            for path in inv['files']:
                self.assertTrue(Path(path).is_relative_to(root))
                self.assertFalse(Path(path).is_symlink())

            analysis = root/'analyses/abcdefabcdef'
            analysis.mkdir(parents=True)
            job_path = analysis/'analysis.json'
            atomic_json(job_path, dict(id='abcdefabcdef', kind='mintpy', status='running',
                project_id=imported['id'], history=[], inventory=inv,
                options=options({}, inv), environment=str(self.prefix),
                source_ion=imported['ion'], source_ion_excluded=[], reference_pixel=[0, 0]))
            self.run_stage('load', job_path)
            loaded = json.loads((analysis/'loaded.json').read_text())
            self.assertEqual((loaded['height'], loaded['width']), (16, 32))
            with h5py.File(analysis/'inputs/ifgramStack.h5') as h5:
                self.assertEqual(h5['unwrapPhase'].shape, (3, 16, 32))
                self.assertTrue(h5['dropIfgram'][:].all())
                for index, row in enumerate(h5['date'][:]):
                    pair = '-'.join(value.decode()[2:] for value in row)
                    np.testing.assert_allclose(h5['unwrapPhase'][index], expected[pair], atol=1e-7, rtol=0)
            self.run_stage('invert', job_path)
            report = json.loads((analysis/'diagnostics.json').read_text())
            self.assertEqual(report['stage'], 'unwrap')
            self.assertTrue(report['network']['connected'])
            self.assertFalse((analysis/'timeseries.h5').exists(), 'first scientific review gate remains intact')
            self.assertNotIn('review_pending', json.loads(job_path.read_text()))

            # Explicit decisions apply only to this generated fixture.
            job = json.loads(job_path.read_text())
            job['review_pending'] = diagnostics.decision(report,
                dict(reviewed=True, notes='人工合成已知答案測試；不核准任何真實專案。'))
            atomic_json(job_path, job)
            self.run_stage('corrections', job_path)
            report = json.loads((analysis/'diagnostics.json').read_text())
            self.assertEqual(report['stage'], 'residual')
            self.assertFalse((analysis/'velocity.h5').exists(), 'residual gate precedes velocity estimation')
            job = json.loads(job_path.read_text())
            job['review_pending'] = diagnostics.decision(report,
                dict(reviewed=True, notes='已知答案合成案例，明確保留全部日期。',
                     excluded_dates=[], reference_date='20170826'))
            atomic_json(job_path, job)
            self.run_stage('finalize', job_path)
            expected_velocity = -.236/(4*np.pi)*spatial_slope
            with h5py.File(analysis/'velocity.h5') as h5:
                velocity = h5['velocity'][:]
                self.assertTrue(np.isfinite(velocity).all())
                np.testing.assert_allclose(velocity, expected_velocity, atol=2e-7, rtol=0)
            final = json.loads(job_path.read_text())
            with h5py.File(final['timeseries_file']) as h5:
                years = np.asarray(ptime.yyyymmdd2years([d.decode() for d in h5['date'][:]]))
                ts = h5['timeseries'][:]
                expected_ts = (years-years[0])[:,None,None] * expected_velocity
                np.testing.assert_allclose(ts, expected_ts, atol=2e-7, rtol=0)

            require_portable_source(imported)
            self.assertEqual(independent_inputs, self.hashes(root/'proc'))
            self.assertEqual(original_hashes, self.hashes(hidden_source))
            self.assertEqual(package_hashes, self.hashes(hidden_package))


if __name__ == '__main__':
    unittest.main()
