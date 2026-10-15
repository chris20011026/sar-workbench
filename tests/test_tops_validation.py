"""Synthetic dossier fixtures; never evidence of real Sentinel-1 validation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from alosstack_helper.tops import STEPS
from alosstack_helper.tops_validation import (TopsValidation, REVIEW_ITEMS, RAW_PRODUCTS,
                                             MAX_LOG, _log_statistics, render_html)


def catalogue(title, median, count=120):
    return ('\n' + '#' * 100 + '\n    ' + title + '\n' + '-' * 100 + '\n' +
            f'topsProc.esd.Median = {median}\ntopsProc.esd.Mean = {median}\n' +
            f'topsProc.esd.Std = 0.15\ntopsProc.esd.number of coherent points = {count}\n' +
            'topsProc.esd.coherence threshold = 0.85\n' + '#' * 100 + '\n')


class FakeEngine:
    def __init__(self, root):
        self.home = root / 'workbench'; self.home.mkdir()
        self.root = root / 'project'; (self.root / 'proc').mkdir(parents=True)
        (self.root / '.helper').mkdir()
        self.active = None
        self.s = dict(id='test01', status='new', completed_steps=[], config=dict(
            root=str(self.root), workflow='topsApp', name='Synthetic TOPS fixture',
            swaths=[1], polarization='VV', properties={'ESD coherence threshold': .85}),
            tops_inventory={key: dict(name='SYNTHETIC-' + key + '.SAFE', date=date, satellite='S1A',
                                    path=str(root / (key + '.SAFE')), relative_orbit=42, direction='ASCENDING')
                            for key, date in [('reference', '20220801'), ('secondary', '20220813')]})
    def get(self, key):
        if key != self.s['id']: raise KeyError(key)
        return copy.deepcopy(self.s)
    def paths(self, state):
        return self.root, self.root / 'proc', self.root / '.helper'


class TopsValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.e = FakeEngine(self.base)
        self.service = TopsValidation(self.e)
        self.root, self.proc, self.meta = self.e.paths(self.e.s)

    def tearDown(self):
        self.tmp.cleanup()

    def complete(self):
        self.e.s.update(status='complete', completed_steps=[key for key, _ in STEPS])
        (self.proc / 'PICKLE').mkdir()
        for key, _ in STEPS:
            for suffix in ('', '.xml'):
                (self.proc / 'PICKLE' / (key + suffix)).write_bytes(b'synthetic non-executable checkpoint')
        self.write_json('tops-geometry.json', {'channels': [{'swath': 1, 'count': 2,
                        'reference_start': 1, 'secondary_start': 1, 'wavelength': .056}]})
        self.write_json('tops-resolved.json', {'properties': {'do ESD': True}, 'versions': {'fixture': 'synthetic'}})
        self.write_json('tops-results.json', {'valid_pixels': 100, 'versions': {'fixture': 'synthetic'}})
        self.write_json('orbit-sources.json', {})
        (self.proc / 'topsApp.xml').write_text('<topsApp/>')
        (self.meta / 'run.log').write_text(catalogue('runESD', .023) + catalogue('runRangeCoreg', .42))
        (self.proc / 'merged').mkdir()
        for _key, _label, filename in RAW_PRODUCTS:
            (self.proc / filename).write_bytes(b'\0' * 40)
            (self.proc / (filename + '.vrt')).write_text(
                '<VRTDataset rasterXSize="10" rasterYSize="10"><SRS>EPSG:4326</SRS>'
                '<GeoTransform>121,0.01,0,25,0,-0.01</GeoTransform>'
                '<VRTRasterBand dataType="Float32" band="1"><SourceFilename>/never/open/this/file</SourceFilename>'
                '</VRTRasterBand></VRTDataset>')

    def write_json(self, name, data):
        (self.meta / name).write_text(json.dumps(data))

    def review(self, report, kind='synthetic'):
        return dict(evidence_id=report['evidence_id'], dataset_kind=kind, reviewer='Synthetic tester',
                    dataset_note='Synthetic fixture, no real image pixels processed',
                    items={x['id']: dict(status='pass', note='Synthetic test observation') for x in REVIEW_ITEMS},
                    independent={key: 'Synthetic test evidence' for key in (
                        'record_location', 'software_version', 'same_inputs_settings', 'grid_reference_mask', 'comparison_results')})

    def test_incomplete_inspection_writes_nothing_and_keeps_gate(self):
        r = self.service.inspect('test01')
        self.assertEqual(r['status'], 'pending')
        self.assertFalse(self.service.base.exists())
        self.assertFalse(next(x for x in r['evidence']['checks'] if x['id'] == 'steps')['available'])
        self.assertTrue(r['workflow_experimental'])
        self.assertFalse(r['tops_stack_enabled'])
        self.assertEqual(self.e.s['status'], 'new')

    def test_complete_native_evidence_does_not_pass_science(self):
        self.complete()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        r = self.service.inspect('test01')
        self.assertTrue(all(x['available'] for x in r['evidence']['checks']), r['evidence']['checks'])
        self.assertEqual(r['status'], 'pending')
        self.assertEqual(r['evidence']['statistics']['esd']['values']['median_pixels'], .023)
        self.assertEqual(r['evidence']['statistics']['range_coregistration']['values']['median_pixels'], .42)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_synthetic_review_cannot_count_as_real_validation(self):
        self.complete()
        r = self.service.inspect('test01')
        saved = self.service.save('test01', self.review(r))
        self.assertEqual(saved['status'], 'synthetic_only')
        self.assertEqual(saved['review']['independent_evidence'], 'user_reported_not_automatically_compared')
        self.assertTrue(saved['workflow_experimental'])
        self.assertFalse(saved['tops_stack_enabled'])
        self.assertFalse((self.meta / 'review.json').exists())

    def test_even_all_real_human_passes_do_not_unlock_workflow(self):
        self.complete()
        r = self.service.inspect('test01')
        saved = self.service.save('test01', self.review(r, kind='real'))
        self.assertEqual(saved['status'], 'user_review_complete')
        self.assertTrue(saved['workflow_experimental'])
        self.assertFalse(saved['tops_stack_enabled'])
        self.assertIn('人工', saved['message'])

    def test_review_is_bound_to_evidence_and_old_notes_retained(self):
        self.complete()
        r = self.service.inspect('test01')
        self.service.save('test01', self.review(r))
        self.write_json('tops-results.json', {'valid_pixels': 99})
        changed = self.service.inspect('test01')
        self.assertTrue(changed['review_stale'])
        self.assertEqual(changed['status'], 'stale')
        self.assertEqual(changed['review']['reviewer'], 'Synthetic tester')
        with self.assertRaisesRegex(ValueError, '證據已更新'):
            self.service.save('test01', self.review(r))

    def test_missing_checkpoint_and_grid_mismatch_stay_incomplete(self):
        self.complete()
        (self.proc / 'PICKLE/esd.xml').unlink()
        vrt = self.proc / (RAW_PRODUCTS[1][2] + '.vrt')
        vrt.write_text(vrt.read_text().replace('rasterXSize="10"', 'rasterXSize="11"'))
        r = self.service.inspect('test01')
        checks = {c['id']: c['available'] for c in r['evidence']['checks']}
        self.assertFalse(checks['steps'])
        self.assertFalse(checks['products'])
        saved = self.service.save('test01', self.review(r, kind='real'))
        self.assertEqual(saved['status'], 'pending')

    def test_failed_human_observation_is_visible(self):
        r = self.service.inspect('test01')
        payload = self.review(r)
        payload['items']['unwrapping'] = dict(status='fail', note='Synthetic phase jump found')
        saved = self.service.save('test01', payload)
        self.assertEqual(saved['status'], 'needs_attention')

    def test_reviewed_requires_name_and_notes_and_independent_details(self):
        r = self.service.inspect('test01')
        for modify in [lambda p: p.update(reviewer=''),
                       lambda p: p['items']['esd'].update(note=''),
                       lambda p: p['independent'].update(grid_reference_mask=''),
                       lambda p: p.update(dataset_kind='real', dataset_note=''),
                       lambda p: p['items']['esd'].update(status='automatic_pass')]:
            payload = self.review(r); modify(payload)
            with self.assertRaises(ValueError): self.service.save('test01', payload)
        self.assertFalse(self.service.base.exists())

    def test_pending_draft_can_be_saved(self):
        r = self.service.inspect('test01')
        saved = self.service.save('test01', {'evidence_id': r['evidence_id'], 'items': {
            'esd': {'status': 'pending', 'note': 'Need to inspect original log'}}})
        self.assertEqual(saved['review']['items']['esd']['status'], 'pending')
        self.assertEqual(saved['status'], 'pending')

    def test_reports_are_self_contained_escaped_and_confined(self):
        r = self.service.inspect('test01')
        payload = self.review(r)
        payload['items']['esd']['note'] = '<script>alert("test")</script>'
        self.service.save('test01', payload)
        page = self.service.artifact('test01', 'validation.html')
        self.assertTrue(page.is_relative_to(self.e.home))
        content = page.read_text()
        self.assertIn('&lt;script&gt;', content)
        self.assertNotIn('<script>', content)
        self.assertNotIn('<img', content)
        self.assertIn('不', content)
        data = json.loads(self.service.artifact('test01', 'validation.json').read_text())
        self.assertEqual(data['review']['dataset_kind'], 'synthetic')
        with self.assertRaises(ValueError): self.service.artifact('test01', '../state.json')
        self.assertFalse((self.meta / 'validation.html').exists())

    def test_unsafe_xml_and_symlink_metadata_are_not_read(self):
        self.complete()
        target = self.base / 'external.json'; target.write_text('{"valid_pixels": 999}')
        (self.meta / 'tops-results.json').unlink()
        (self.meta / 'tops-results.json').symlink_to(target)
        vrt = self.proc / (RAW_PRODUCTS[0][2] + '.vrt')
        vrt.write_text('<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><VRTDataset/>')
        r = self.service.inspect('test01')
        self.assertEqual(r['evidence']['files']['.helper/tops-results.json']['status'], 'unavailable')
        self.assertIsNone(r['evidence']['results_summary']['valid_pixels'])
        self.assertNotIn('grid', r['evidence']['products'][0])
        self.assertIn('DTD', r['evidence']['products'][0]['descriptor']['reason'])

    def test_writer_rejects_symlinked_temporaries(self):
        r = self.service.inspect('test01')
        directory = self.service._directory('test01', create=True)
        victim = self.base / 'victim'; victim.write_text('unchanged')
        (directory / 'review.tmp').symlink_to(victim)
        with self.assertRaises(ValueError): self.service.save('test01', self.review(r))
        self.assertEqual(victim.read_text(), 'unchanged')

    def test_log_requires_named_complete_blocks_not_merged_esd_fields(self):
        (self.meta / 'run.log').write_text('topsProc.esd.Median = .5\n' + catalogue('runRangeCoreg', .2))
        stats = _log_statistics(self.root)
        self.assertIsNone(stats['esd'])
        self.assertEqual(stats['range_coregistration']['values']['median_pixels'], .2)
        (self.meta / 'run.log').write_text(catalogue('runESD', .1) + 'x' * (MAX_LOG + 10))
        stats = _log_statistics(self.root)
        self.assertTrue(stats['truncated'])
        self.assertIsNone(stats['esd'])

    def test_nonfinite_statistics_and_zero_samples_are_incomplete(self):
        self.complete()
        (self.meta / 'run.log').write_text(catalogue('runESD', 'nan') + catalogue('runRangeCoreg', .2, count=0))
        r = self.service.inspect('test01')
        checks = {c['id']: c['available'] for c in r['evidence']['checks']}
        self.assertFalse(checks['esd'])
        self.assertFalse(checks['coregistration'])
        json.dumps(r, allow_nan=False)

    def test_running_other_workflows_and_bad_ids_are_rejected(self):
        self.e.s['status'] = 'running'
        with self.assertRaises(ValueError): self.service.inspect('test01')
        self.e.s.update(status='complete')
        self.e.s['config']['workflow'] = 'alosStack'
        with self.assertRaises(ValueError): self.service.inspect('test01')
        with self.assertRaises(ValueError): self.service._directory('../escape', create=True)


if __name__ == '__main__':
    unittest.main()
