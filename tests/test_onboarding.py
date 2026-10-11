import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from alosstack_helper.core import find_conda
from alosstack_helper.onboarding import scan_source, organize, validate_archive, inspect_terrain
from alosstack_helper.runtime import RuntimeManager, MANIFEST, private_env, ready_environment, verified_lock, validate_report, REQUIRED_CHECKS


def products(root, dates=('200101', '200201'), frame='0480'):
    root.mkdir(parents=True, exist_ok=True)
    for date in dates:
        for head in ('LED', 'IMG-HH'):
            (root / f'{head}-ALOS200001{frame}-{date}-FBDR1.1__A').write_bytes(b'fixture')


class Imports(unittest.TestCase):
    def test_nested_frames_same_date_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for frame in ('0480', '0490'):
                folder = root / 'source' / frame
                products(folder, frame=frame)
                (folder / 'summary.txt').write_text(frame)
            plan = scan_source(root / 'source')
            self.assertTrue(plan['can_import'])
            result = organize(plan, root / 'organized')
            self.assertEqual([r['leaders'] for r in result['dates']], [2, 2])
            self.assertEqual(len(list((root / 'organized/200101').iterdir())), 4)
            self.assertEqual((root / 'source/0480/summary.txt').read_text(), '0480')

    def test_zip_is_extracted_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); products(root / 'source')
            archive = root / 'input.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for f in (root / 'source').iterdir(): z.write(f, 'nested/' + f.name)
            before = hashlib.sha256(archive.read_bytes()).hexdigest()
            result = organize(scan_source(archive), root / 'output')
            self.assertEqual(len(result['dates']), 2)
            self.assertEqual(before, hashlib.sha256(archive.read_bytes()).hexdigest())

    def test_bad_archives_never_write_outside_destination(self):
        for name, mode in [('../escape', 0), ('/absolute', 0), ('C:\\escape', 0), ('link', stat.S_IFLNK | 0o777)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                p = Path(d) / 'bad.zip'
                with zipfile.ZipFile(p, 'w') as z:
                    i = zipfile.ZipInfo(name); i.external_attr = mode << 16; z.writestr(i, 'bad')
                with self.assertRaises(ValueError): scan_source(p)

    def test_duplicate_products_refuse_to_guess(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); products(root / 'one'); products(root / 'two')
            with self.assertRaisesRegex(ValueError, '重複'): scan_source(root)

    def test_missing_leader_is_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); products(root)
            next(root.glob('LED-*')).unlink()
            self.assertFalse(scan_source(root)['can_import'])

    def test_changed_source_requires_rescan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); products(root / 'source')
            plan = scan_source(root / 'source')
            next((root / 'source').glob('IMG-*')).write_bytes(b'changed source bytes')
            with self.assertRaisesRegex(ValueError, '來源在掃描後改變'): organize(plan, root / 'output')

    def test_existing_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); products(root / 'source'); (root / 'output').mkdir()
            (root / 'output/keep').write_text('important')
            with self.assertRaisesRegex(ValueError, '覆蓋'): organize(scan_source(root / 'source'), root / 'output')
            self.assertEqual((root / 'output/keep').read_text(), 'important')

    def test_cancelled_import_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); products(root / 'source')
            with self.assertRaisesRegex(RuntimeError, '停止'): organize(scan_source(root / 'source'), root / 'output', cancelled=lambda: True)
            self.assertFalse((root / 'output/import-manifest.json').exists())


class Terrain(unittest.TestCase):
    def fixture(self, root, reference='WGS84', datatype='SHORT'):
        p = root / 'terrain'
        p.write_bytes(b'\0' * (8 if datatype == 'SHORT' else 4))
        tree = ET.Element('imageFile')
        def prop(parent, key, value): ET.SubElement(ET.SubElement(parent, 'property', name=key), 'value').text = value
        for k, v in [('width', '2'), ('length', '2'), ('data_type', datatype), ('reference', reference)]: prop(tree, k, v)
        for name, start, delta in [('coordinate1', '120', '0.1'), ('coordinate2', '25', '-0.1')]:
            c = ET.SubElement(tree, 'component', name=name)
            for k, v in [('startingValue', start), ('delta', delta), ('size', '2')]: prop(c, k, v)
        ET.ElementTree(tree).write(str(p) + '.xml')
        return p

    def test_geographic_extent_and_size(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(Path(d)); r = inspect_terrain(p, 'dem')
            self.assertEqual(r['bounds'], [24.9, 25.0, 120.0, 120.1])
            p.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, '大小'): inspect_terrain(p, 'dem')

    def test_geoid_height_cannot_be_renamed_to_ellipsoid(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, '橢球高'): inspect_terrain(self.fixture(Path(d), 'EGM96'), 'dem')


class PrivateRuntime(unittest.TestCase):
    def test_partial_success_or_duplicate_checks_cannot_activate_environment(self):
        complete = dict(passed=True, checks=[dict(name=n,status='PASS') for n in REQUIRED_CHECKS])
        self.assertEqual(len(validate_report(complete)), 15)
        for bad in (dict(complete, passed=False), dict(complete, checks=complete['checks'][:3]),
                    dict(complete, checks=[complete['checks'][0]]*15),
                    dict(complete, checks=complete['checks'][:-1]+[dict(name=complete['checks'][-1]['name'],status='FAIL')])):
            with self.assertRaisesRegex(RuntimeError, '15'):
                validate_report(bad)

    def test_manifest_and_package_lock(self):
        lock, count = verified_lock()
        self.assertEqual(count, 381)
        self.assertEqual(hashlib.sha256(lock.read_bytes()).hexdigest(), MANIFEST['lock']['sha256'])

    def test_host_environment_does_not_leak(self):
        with patch.dict(os.environ, {'PYTHONPATH': '/wrong', 'CONDA_PREFIX': '/other', 'ISCE_HOME': '/intel', 'DYLD_LIBRARY_PATH': '/wrong'}):
            result = private_env(Path('/tmp/private'))
        for key in ('PYTHONPATH', 'CONDA_PREFIX', 'ISCE_HOME', 'DYLD_LIBRARY_PATH'): self.assertNotIn(key, result)
        self.assertEqual(result['CONDA_SUBDIR'], 'osx-arm64')
        self.assertEqual(result['CONDA_PKGS_DIRS'], '/tmp/private/runtime/packages')
        self.assertEqual(result['CONDA_REGISTER_ENVS'], 'false')
        self.assertEqual(result['XDG_CACHE_HOME'], '/tmp/private/runtime/cache')

    def test_stale_running_status_becomes_interrupted(self):
        with tempfile.TemporaryDirectory() as d:
            m = RuntimeManager(Path(d)); m.status_file.parent.mkdir()
            m.status_file.write_text(json.dumps({'status': 'running'}))
            self.assertEqual(m.status()['status'], 'interrupted')

    def test_ready_requires_matching_lock_and_private_prefix(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); prefix = home / 'runtime/envs/test'
            for p in ('bin/python', 'bin/insar-check', 'share/isce2/alosStack/create_cmds.py'):
                f = prefix / p; f.parent.mkdir(parents=True, exist_ok=True); f.touch()
            ready = home / 'runtime/ready.json'
            ready.write_text(json.dumps(dict(prefix=str(prefix), lock_sha256=MANIFEST['lock']['sha256'])))
            self.assertEqual(ready_environment(home), prefix)
            ready.write_text(json.dumps(dict(prefix=str(prefix), lock_sha256='wrong')))
            self.assertIsNone(ready_environment(home))

    def test_private_conda_wins_over_active_shell(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d); conda = home / 'runtime/miniforge/bin/conda'; conda.parent.mkdir(parents=True); conda.touch()
            with patch.dict(os.environ, {'SAR_WORKBENCH_HOME': str(home)}), patch('shutil.which', return_value='/wrong/conda'):
                self.assertEqual(find_conda(home / 'runtime/envs/native'), str(conda))

    def test_no_consent_never_starts_install(self):
        with tempfile.TemporaryDirectory() as d:
            m = RuntimeManager(Path(d))
            with self.assertRaisesRegex(ValueError, '授權'): m.start()
            self.assertFalse((Path(d) / 'runtime').exists())

    def test_lock_blocks_second_installer(self):
        import fcntl
        with tempfile.TemporaryDirectory() as d:
            m = RuntimeManager(Path(d)); (Path(d) / 'runtime').mkdir()
            with (Path(d) / 'runtime/install.lock').open('a') as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(m._locked())
                with patch('alosstack_helper.runtime.supported_platform', return_value=(True, 'test')):
                    with self.assertRaisesRegex(ValueError, '另一個'): m.start(consent=True)


if __name__ == '__main__': unittest.main()
