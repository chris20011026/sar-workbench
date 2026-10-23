"""Metadata arithmetic/layout tests, not real cross-sensor InSAR validation."""
import copy
import json
import unittest

from alosstack_helper.compatibility import C, compatibility_report, radar_metadata


class RadarFields(unittest.TestCase):
    def record(self, layout='PALSAR-2'):
        raw = bytearray(b' ' * (4096 if layout == 'PALSAR-2' else 4680))
        for start, value in [(500, .24), (550, -1e12), (742, 40), (710, 52.3957978),
                             (934, 2500000), (1254, 40000), (1238, 1800)]:
            raw[start:start + 16] = str(value).encode().ljust(16)
        raw[518:534] = b'LINEAR FM CHIRP'.ljust(16)
        start = 1734 if layout == 'PALSAR-2' else 2330
        raw[start:start + 16] = b'-22.5'.ljust(16)
        raw[start + 16:start + 32] = b'0.031'.ljust(16)
        return raw

    def test_p2_p3_use_distinct_doppler_offsets_and_explicit_units(self):
        for layout in ('PALSAR-2', 'PALSAR-3'):
            raw = self.record(layout)
            wrong = 2330 if layout == 'PALSAR-2' else 1734
            raw[wrong:wrong + 16] = b'123456789'.ljust(16)
            before = bytes(raw)
            result = radar_metadata(raw, layout)
            self.assertEqual(result['wavelength_m'], .24)
            self.assertEqual(result['doppler_constant_hz'], -22.5)
            self.assertEqual(result['doppler_linear_hz_per_km'], .031)
            self.assertEqual(result['field_sources']['doppler_linear_hz_per_km']['unit'], 'Hz/km')
            self.assertEqual(result['field_sources']['doppler_constant_hz']['bytes'][0], 1735 if layout == 'PALSAR-2' else 2331)
            self.assertEqual(bytes(raw), before)

    def test_missing_negative_and_non_finite_do_not_become_zero_or_pass(self):
        for value in ('', 'NaN', 'Inf', '-0.24', '0', '1e999', '0.24junk'):
            raw = self.record(); raw[500:516] = value.encode().ljust(16)
            result = radar_metadata(raw, 'PALSAR-2')
            with self.subTest(value=value):
                self.assertIsNone(result['wavelength_m'])
                json.dumps(result, allow_nan=False)
        raw = self.record(); raw[1734:1750] = b'0'.ljust(16)
        self.assertEqual(radar_metadata(raw, 'PALSAR-2')['doppler_constant_hz'], 0)

    def test_unknown_or_truncated_layout_is_explicitly_unknown(self):
        for layout, raw in [('PALSAR-3', self.record()), ('unknown', self.record()), ('PALSAR-2', b'')]:
            result = radar_metadata(raw, layout)
            self.assertTrue(result['issues'])
            self.assertNotIn('wavelength_m', result)


class CompatibilityReview(unittest.TestCase):
    record = RadarFields.record
    def product(self, identifier, date='240822', selected=True, wavelength=.24, bandwidth=40e6):
        radar = radar_metadata(self.record(), 'PALSAR-2')
        radar['wavelength_m'] = wavelength
        radar['chirp_rate_hz_per_s'] = bandwidth / (40e-6)
        return dict(id=identifier, date='20' + date, selected=selected, sensor='ALOS-2',
                    metadata=dict(frame='0480', layout='PALSAR-2', level='1.1',
                                  sensor_mode='ALOS2 -L -0315-', radar=radar), errors=[])

    def test_selected_only_report_has_no_scientific_pass_or_automatic_approval(self):
        a, b = self.product('a'), self.product('b', '250425')
        candidate = self.product('c', '240822', selected=False, wavelength=.3)
        before = copy.deepcopy([a, b, candidate])
        out = compatibility_report([a, b, candidate], {('240822', '0480'), ('250425', '0480')})
        self.assertEqual(len(out['rows']), 2)
        self.assertEqual(out['status'], 'review_required')
        self.assertIs(out['processing_unlocked'], False)
        self.assertEqual(out['actual_filtering'], 'not_verified')
        self.assertEqual(out['scientific_validation'], 'not_performed')
        self.assertTrue(out['complete'])
        self.assertAlmostEqual(out['range_overlap']['bandwidth_hz'], 40e6)
        self.assertEqual([a, b, candidate], before)
        json.dumps(out, allow_nan=False)

    def test_units_nominal_bandwidth_and_doppler_equation(self):
        a = self.product('a')
        row = compatibility_report([a])['rows'][0]
        self.assertAlmostEqual(row['carrier_frequency_hz'], C / .24)
        self.assertEqual(row['processor_range_bandwidth_hz'], 40e6)
        self.assertAlmostEqual(row['range_bandwidth_hz'], 40e6)
        self.assertEqual(row['prf_hz'], 2500)
        self.assertAlmostEqual(row['range_sampling_hz'], 52.3957978e6)
        self.assertEqual(row['doppler']['linear_hz_per_km'], .031)
        self.assertIn('R_km', row['doppler']['equation'])
        self.assertIn('不是影像中心', row['doppler']['note'])

    def test_nominal_overlap_intersection_is_not_minimum_bandwidth(self):
        a, b = self.product('a'), self.product('b', '250425', wavelength=C / (C / .24 + 10e6))
        out = compatibility_report([a, b])
        self.assertAlmostEqual(out['range_overlap']['bandwidth_hz'], 30e6)
        self.assertAlmostEqual(out['range_overlap']['fractions']['a'], .75)
        self.assertEqual(out['range_overlap']['status'], 'nominal_overlap')
        b['metadata']['radar']['wavelength_m'] = .3
        out = compatibility_report([a, b])
        self.assertEqual(out['range_overlap']['bandwidth_hz'], 0)
        self.assertIsNone(out['range_overlap']['lower_hz'])
        self.assertEqual(out['range_overlap']['status'], 'no_nominal_overlap')
        self.assertTrue(any('沒有交集' in warning for warning in out['warnings']))

    def test_missing_selected_product_cannot_use_only_known_subset(self):
        out = compatibility_report([self.product('a'), self.product('b', '250425')],
                                   {('240822', '0480'), ('250425', '0480'), ('240822', '0490')})
        self.assertFalse(out['complete'])
        self.assertEqual(out['range_overlap']['status'], 'unknown')
        self.assertTrue(all(c['status'] == 'unknown' for c in out['checks']))

    def test_duplicate_selected_date_frame_cannot_be_hidden_by_set_comparison(self):
        a, duplicate, b = self.product('a'), self.product('another-file'), self.product('b', '250425')
        out = compatibility_report([a, duplicate, b], {('240822', '0480'), ('250425', '0480')})
        self.assertFalse(out['complete'])
        self.assertEqual(out['range_overlap']['status'], 'unknown')
        self.assertIsNone(out['range_overlap']['bandwidth_hz'])
        self.assertTrue(any('重複日期／影格' in warning for warning in out['warnings']))
        self.assertTrue(all(c['status'] == 'unknown' for c in out['checks']))

    def test_bad_product_missing_wavelength_non_l11_or_bad_chirp_keep_overlap_unknown(self):
        for mutate in (
            lambda p: p.update(errors=['corrupt metadata']),
            lambda p: p['metadata'].update(level='1.5'),
            lambda p: p['metadata']['radar'].update(wavelength_m=None),
            lambda p: p['metadata']['radar'].update(chirp_rate_hz_per_s=None),
            lambda p: p['metadata']['radar'].update(pulse_code='UNKNOWN'),
            lambda p: p['metadata'].update(sensor_mode='ALOS2 -L -0815-'),
            lambda p: p['metadata'].update(sensor_mode=None),
        ):
            a, b = self.product('a'), self.product('b', '250425')
            mutate(b)
            out = compatibility_report([a, b])
            self.assertEqual(out['range_overlap']['status'], 'unknown')
            self.assertIsNone(out['range_overlap']['bandwidth_hz'])

    def test_old_dossier_without_radar_and_no_products_remain_usable(self):
        product = self.product('old'); product['metadata'].pop('radar')
        report = compatibility_report([product])
        self.assertIsNone(report['rows'][0]['wavelength_m'])
        self.assertEqual(report['range_overlap']['status'], 'unknown')
        self.assertFalse(compatibility_report([])['complete'])

    def test_equal_sampling_cannot_mask_different_frequency_or_missing_doppler(self):
        a, b = self.product('a'), self.product('b', '250425', wavelength=.3)
        b['metadata']['radar']['doppler_constant_hz'] = None
        out = compatibility_report([a, b])
        self.assertEqual(next(c for c in out['checks'] if c['key'] == 'range_sampling_hz')['status'], 'same')
        self.assertEqual(out['range_overlap']['status'], 'no_nominal_overlap')
        self.assertIsNone(out['rows'][1]['doppler']['constant_hz'])
        self.assertIs(out['processing_unlocked'], False)


if __name__ == '__main__':
    unittest.main()
