import json
import tempfile
import unittest
from pathlib import Path

from alosstack_helper.mintpy_parameters import (
    BASELINES, EDITABLE_KEYS, PROTECTED_KEYS, catalogue,
    normalize_overrides, validate_combination,
)


class MintPyParameterTests(unittest.TestCase):
    def norm(self, suffix, value, **kwargs):
        key = 'mintpy.' + suffix
        return normalize_overrides({key: value}, **kwargs).get(key)

    def test_catalogue_complete_portable_explained(self):
        data = catalogue()
        self.assertEqual(len(data['options']), 121)
        self.assertEqual(len({row['key'] for row in data['options']}), 121)
        self.assertEqual(len(data['metadata']['groups']), 18)
        self.assertEqual(len(EDITABLE_KEYS), 54)
        self.assertFalse(data['metadata']['available'])
        self.assertNotIn('/Users/', json.dumps(data))
        for row in data['options']:
            self.assertTrue(row['help'])
            self.assertTrue(row['tradeoff'])
            self.assertTrue(row['sources'])
            self.assertEqual(row['editable'], row['status'] == 'editable')
            if not row['editable']:
                self.assertTrue(row['disabled_reason'])
            if row['status'] == 'basic':
                self.assertTrue(row['basic_control'])

    def test_catalogue_is_independent_each_call(self):
        first = catalogue()
        first['options'][0]['label'] = 'changed'
        self.assertNotEqual(catalogue()['options'][0]['label'], 'changed')

    def test_selected_environment_defaults_and_version_without_importing(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory) / 'lib/python3.12/site-packages'
            defaults = site / 'mintpy/defaults'
            defaults.mkdir(parents=True)
            (defaults / 'smallbaselineApp.cfg').write_text('mintpy.solidEarthTides = auto\nmintpy.new.option = yes\n')
            (defaults / 'smallbaselineApp_auto.cfg').write_text('mintpy.solidEarthTides = yes\n')
            metadata = site / 'mintpy-9.9.dist-info/METADATA'
            metadata.parent.mkdir()
            metadata.write_text('Name: mintpy\nVersion: 9.9\n')
            (Path(directory) / 'lib/python3.1').symlink_to('python3.12', target_is_directory=True)
            (site / 'mintpy/__init__.py').write_text('raise RuntimeError("must not import")')
            data = catalogue(directory)
            self.assertTrue(data['metadata']['available'])
            self.assertEqual(data['metadata']['environment_version'], '9.9')
            self.assertEqual(data['metadata']['additional_keys'], ['mintpy.new.option'])
            rows = {r['key']: r for r in data['options']}
            self.assertEqual(rows['mintpy.solidEarthTides']['default'], 'yes')
            self.assertTrue(rows['mintpy.solidEarthTides']['editable'])
            self.assertFalse(rows['mintpy.timeFunc.periodic']['editable'])
            self.assertTrue(data['metadata']['warnings'])

    def test_missing_environment_is_explainable(self):
        data = catalogue('/nonexistent-mintpy-test-environment')
        self.assertFalse(data['metadata']['available'])
        self.assertEqual(len(data['options']), 121)
        self.assertTrue(data['metadata']['warnings'])

    def test_empty_values_keep_baseline_and_do_not_enable_corrections(self):
        self.assertEqual(normalize_overrides(None), {})
        self.assertEqual(normalize_overrides({}), {})
        self.assertIsNone(self.norm('solidEarthTides', ''))
        self.assertIsNone(self.norm('troposphericDelay.method', None))
        self.assertEqual(BASELINES['mintpy.troposphericDelay.method'], 'no')
        self.assertEqual(self.norm('troposphericDelay.method', 'AUTO'), 'auto')

    def test_protected_fields_cannot_be_overridden(self):
        for key in PROTECTED_KEYS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                normalize_overrides({key: 'auto'})
        for raw in [{'mintpy.unknown': 'no'}, [], 'key=value', {1: 'no'}]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_overrides(raw)

    def test_injection_and_non_scalar_values_fail(self):
        values = ['yes\nmintpy.load.processor = snap', 'yes\rno', 'no # comment', 'key=value', 'yes\x00', {}, [], 'x' * 4097]
        for value in values:
            with self.subTest(value=str(value)[:60]), self.assertRaises(ValueError):
                self.norm('solidEarthTides', value)

    def test_boolean_and_enum_domains(self):
        self.assertEqual(self.norm('solidEarthTides', True), 'yes')
        self.assertEqual(self.norm('solidEarthTides', 'False'), 'no')
        self.assertEqual(self.norm('troposphericDelay.weatherModel', 'era5'), 'ERA5')
        self.assertEqual(self.norm('load.compression', 'none'), 'none')
        for suffix, value in [('solidEarthTides', 1), ('troposphericDelay.method', 'opera'), ('troposphericDelay.weatherModel', 'MERRA'), ('networkInversion.maskDataset', 'rangeOffsetStd')]:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.norm(suffix, value)

    def test_numbers_are_finite_in_range_and_integral_when_required(self):
        self.assertEqual(self.norm('unwrapError.connCompMinArea', '2.5e3'), '2500')
        self.assertEqual(self.norm('timeFunc.bootstrapCount', '400.0'), '400')
        self.assertEqual(self.norm('network.minCoherence', 0.75), '0.75')
        self.assertEqual(self.norm('network.tempBaseMax', 'no'), 'no')
        self.assertEqual(self.norm('network.perpBaseMax', 500), '500')
        values = [('network.tempBaseMax', 0), ('network.perpBaseMax', -10), ('network.minCoherence', 1.1), ('network.minCoherence', -0.1), ('unwrapError.connCompMinArea', 0), ('timeFunc.polynomial', 0), ('timeFunc.bootstrapCount', 1), ('timeFunc.bootstrapCount', 2.5), ('networkInversion.minNumPixel', 1), ('network.connNumMax', float('nan')), ('residualRMS.cutoff', 'inf')]
        for suffix, value in values:
            with self.subTest(suffix=suffix, value=value), self.assertRaises(ValueError):
                self.norm(suffix, value)

    def test_paths_are_explicit_readable_and_type_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '含 空白.h5'
            path.write_bytes(b'not parsed until science preflight')
            self.assertEqual(self.norm('residualRMS.maskFile', str(path)), str(path.resolve()))
            self.assertEqual(self.norm('troposphericDelay.gacosDir', directory), str(Path(directory).resolve()))
            self.assertEqual(self.norm('networkInversion.waterMaskFile', 'no'), 'no')
            for suffix, value in [('network.maskFile', 'no'), ('reference.coherenceFile', 'no'), ('residualRMS.maskFile', directory), ('troposphericDelay.gacosDir', str(path)), ('network.maskFile', 'mask.h5'), ('network.maskFile', str(Path(directory)/'missing.h5')), ('network.maskFile', '/path/${HOME}/mask.h5'), ('network.maskFile', '/path/*.h5')]:
                with self.subTest(suffix=suffix, value=value), self.assertRaises(ValueError):
                    self.norm(suffix, value)

    def test_aoi_validates_format_bounds_and_mutual_exclusion(self):
        self.assertEqual(self.norm('network.aoiYX', ' 0:50, 10:90 '), '0:50,10:90')
        self.assertEqual(self.norm('network.aoiLALO', '24:25,121:122'), '24:25,121:122')
        for suffix, value in [('network.aoiYX', '0.5:50,0:90'), ('network.aoiYX', '-1:50,0:90'), ('network.aoiLALO', '25:24,121:122'), ('network.aoiLALO', '-91:10,121:122'), ('network.aoiYX', '0:5:7,0:9')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.norm(suffix, value)
        with self.assertRaises(ValueError):
            normalize_overrides({'mintpy.network.aoiYX': '0:10,0:10', 'mintpy.network.aoiLALO': '24:25,121:122'})

    def test_real_dates_and_selected_dates(self):
        retained = ['220101', '220815', '230101']
        self.assertEqual(self.norm('timeFunc.excludeDate', '20220815', retained_dates=retained), '20220815')
        self.assertEqual(self.norm('timeFunc.stepDate', '20220815T1200', retained_dates=retained), '20220815T1200')
        for suffix, value in [('timeFunc.startDate', '20220230'), ('timeFunc.startDate', '220101'), ('timeFunc.stepDate', '20220815T2460'), ('timeFunc.excludeDate', '20220815,20220815'), ('timeFunc.excludeDate', '/path/dates.txt'), ('timeFunc.excludeDate', '20220816'), ('timeFunc.stepDate', '20230101')]:
            with self.subTest(suffix=suffix, value=value), self.assertRaises(ValueError):
                self.norm(suffix, value, retained_dates=retained)

    def test_model_grammars_and_duplicates(self):
        self.assertEqual(self.norm('timeFunc.periodic', '1;0.5'), '1,0.5')
        self.assertEqual(self.norm('timeFunc.exp', '20220101,60,120;20220815T1200,90'), '20220101,60,120;20220815T1200,90')
        for suffix, value in [('timeFunc.periodic', '1,1.0'), ('timeFunc.periodic', '0'), ('timeFunc.periodic', 'nan'), ('timeFunc.periodic', '1,'), ('timeFunc.exp', '20220101'), ('timeFunc.exp', '20220101,0'), ('timeFunc.exp', '20220101,2.5'), ('timeFunc.exp', '20220101,60,60'), ('timeFunc.exp', '20220101,60;20220101,90')]:
            with self.subTest(suffix=suffix, value=value), self.assertRaises(ValueError):
                self.norm(suffix, value)
        with self.assertRaises(ValueError):
            self.norm('timeFunc.log', '20230101,60', retained_dates=['220101', '230101'])

    def test_explicit_cross_parameter_constraints(self):
        cases = [
            {'mintpy.timeFunc.startDate': '20230101', 'mintpy.timeFunc.endDate': '20220101'},
            {'mintpy.timeFunc.uncertaintyQuantification': 'bootstrap', 'mintpy.timeFunc.polynomial': 2},
            {'mintpy.timeFunc.uncertaintyQuantification': 'bootstrap', 'mintpy.timeFunc.periodic': 1},
            {'mintpy.networkInversion.maskDataset': 'coherence', 'mintpy.networkInversion.maskThreshold': 2},
        ]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_overrides(raw)
        valid = {'mintpy.timeFunc.uncertaintyQuantification': 'bootstrap', 'mintpy.timeFunc.polynomial': '1'}
        self.assertEqual(normalize_overrides(valid), valid)


if __name__ == '__main__':
    unittest.main()
