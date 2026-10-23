"""Synthetic offsets and isolated state; no SAR processing or environment writes."""
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper import matching_quality as quality, sampling
from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine


class MatchingQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc = self.root / 'proc'; self.proc.mkdir()
        (self.root / '.helper').mkdir()
        (self.root / 'data').mkdir()
        self.folder = self.proc / 'dates/240822/f1_0480/s1'; self.folder.mkdir(parents=True)
        self.raw = self.folder / '240822.slc'; self.raw.write_bytes(b'synthetic complex samples')
        xml = Path(str(self.raw) + '.xml'); xml.write_text('<image/>')
        vrt = Path(str(self.raw) + '.vrt'); vrt.write_text('<VRTDataset/>')
        self.plan = dict(plan_id='plan', reference='250425', rows=[
            dict(date='240822', frame='0480', swath=1, relative='240822/f1_0480/s1/240822.slc'),
            dict(date='250425', frame='0480', swath=1, relative='250425/f1_0480/s1/250425.slc',width=100,length=100)])
        self.state = dict(id='quality', config=dict(root=str(self.root), data=str(self.root / 'data'),
                         reference='250425', workflow='alosStack', sampling_equalization=True),
                          sampling=self.plan, status='matching_review', history=[], hashes={})
        atomic_json(self.proc / 'helper_sampling_plan.json', self.plan)
        sampling.approve(self.state, dict(plan_id='plan', frames_verified=True, frame_notes='人工確認影格對應'), self.proc)
        post = dict(changed=0, rows=[dict(**self.plan['rows'][0], raw=str(self.raw),
                    raw_stamp=quality.file_evidence(self.raw, False), xml=quality.file_evidence(xml),
                    vrt=quality.file_evidence(vrt))], metadata={})
        atomic_json(self.proc / 'helper_sampling/receipt.json', dict(plan_id='plan', frames=self.state['sampling_approval'], post=post))
        self.points = [(x, 1 + .01*x + .03*y, y, -2 + .02*x - .04*y, 10., .1, .2, 0.)
                       for x in (10, 20, 30) for y in (20, 40, 60)]
        self.write('ampcor.off', self.points)
        self.write('cull.off', self.points[:-1])
        self.engine = Engine(self.root / 'registry')
        self.engine.projects['quality'] = str(self.root)
        self.engine.save(self.state)

    def write(self, name, points):
        (self.folder / name).write_text(''.join(' '.join(f'{x:.6f}' for x in p) + '\n' for p in points))

    def approve(self):
        report = quality.publish(self.state, self.proc)
        quality.approve(self.state, dict(report_id=report['report_id'], matching_reviewed=True,
                                        matching_notes='檢查點位與日誌'), self.proc)
        return report

    def test_counts_offsets_and_affine_residual_use_known_columns(self):
        report = quality.collect(self.state, self.proc)
        row = report['rows'][0]
        self.assertTrue(report['can_continue'])
        self.assertEqual((row['raw_count'], row['retained_count']), (9, 8))
        self.assertAlmostEqual(row['rejected_fraction'], 1/9)
        self.assertAlmostEqual(row['range']['min'], 1.7)
        self.assertAlmostEqual(row['azimuth']['max'], -2.2)
        self.assertLess(row['diagnostic_fit']['range_rms_px'], 1e-10)
        self.assertLess(row['diagnostic_fit']['azimuth_rms_px'], 1e-10)
        self.assertEqual(row['kind'], 'correlation')
        self.assertEqual(report['summary']['correlation'], 1)

    def test_affine_diagnostic_detects_non_affine_offsets_and_collinearity(self):
        curved = [tuple([p[0], p[1] + .01*p[0]**2, *p[2:]]) for p in self.points]
        self.assertGreater(quality.diagnostic_fit(curved)['range_rms_px'], .1)
        straight = [(i, i, i, i, 1, 0, 0, 0) for i in range(10)]
        self.assertIsNone(quality.diagnostic_fit(straight)['range_rms_px'])
        self.assertIn('共線', quality.diagnostic_fit(straight)['reason'])
        self.assertIn('少於', quality.diagnostic_fit(self.points[:3])['reason'])

    def test_geometry_only_is_unknown_and_never_measured_correlation(self):
        (self.folder / 'ampcor.off').unlink()
        row = quality.collect(self.state, self.proc)['rows'][0]
        self.assertEqual(row['kind'], 'geometry_or_unknown')
        self.assertIsNone(row['raw_count'])
        self.assertIsNone(row['rejected_fraction'])
        self.assertIsNone(row['snr_median'])
        self.assertTrue(row['warnings'])

    def test_missing_empty_malformed_nonfinite_offsets_block_approval(self):
        for content in (None, '', '1 2 3\n', '1 nan 2 3 4 5 6 7\n', '-1 2 3 4 5 6 7 8\n', 'garbage'):
            with self.subTest(content=content):
                file = self.folder / 'cull.off'
                file.unlink(missing_ok=True)
                if content is not None: file.write_text(content)
                report = quality.publish(self.state, self.proc)
                self.assertFalse(report['can_continue'])
                with self.assertRaises(ValueError):
                    quality.approve(self.state, dict(report_id=report['report_id'], matching_reviewed=True,
                                                    matching_notes='人工說明'), self.proc)

    def test_retained_points_must_be_multiset_subset_of_raw_points(self):
        self.write('cull.off', [self.points[0]] * 2)
        report = quality.collect(self.state, self.proc)
        self.assertFalse(report['can_continue'])
        self.assertIn('子集', report['rows'][0]['errors'][0])

    def test_offsets_outside_reference_swath_block_continuation(self):
        self.points[0] = (101, 1, 20, 0, 10, 0, 0, 0)
        self.write('ampcor.off',self.points); self.write('cull.off',self.points)
        report=quality.collect(self.state,self.proc)
        self.assertFalse(report['can_continue'])
        self.assertTrue(any('超出參考' in message for message in report['rows'][0]['errors']))

    def test_preview_point_count_and_report_size_are_bounded(self):
        many=[(i % 100 + 1, 0, i // 100 + 1, 0, 10, 0, 0, 0) for i in range(10000)]
        self.write('ampcor.off',many); self.write('cull.off',many)
        report=quality.collect(self.state,self.proc)
        self.assertLessEqual(len(report['rows'][0]['points']),100)
        with patch.object(quality,'MAX_REPORT',2000), self.assertRaises(ValueError):
            quality.collect(self.state,self.proc)

    def test_report_change_new_offset_or_source_change_invalidates_approval(self):
        for what in ('offset', 'source', 'report', 'missing'):
            with self.subTest(what=what):
                if what == 'missing': (self.folder / 'ampcor.off').unlink()
                self.approve()
                target = {'offset': self.folder / 'cull.off', 'source': self.raw,
                          'report': self.proc / 'helper_matching_quality.json', 'missing': self.folder / 'ampcor.off'}[what]
                original = target.read_bytes() if target.exists() else None
                stat = target.stat() if target.exists() else None
                if what == 'report':
                    document=json.loads(original); document['can_continue']=False
                    target.write_text(json.dumps(document))
                else: target.write_bytes((original or b'') + b'\n')
                with self.assertRaises(ValueError): quality.verified_approval(self.state, self.proc)
                if original is None: target.unlink()
                else:
                    target.write_bytes(original)
                    import os
                    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                if what == 'missing': self.write('ampcor.off', self.points)

    def test_refresh_resets_approval_and_never_runs_science(self):
        self.approve(); self.engine.save(self.state)
        with patch.object(self.engine, 'run') as run, patch.object(self.engine, 'shell') as shell:
            report = self.engine.refresh_matching_quality('quality')
        run.assert_not_called(); shell.assert_not_called()
        self.assertTrue(report['can_continue'])
        self.assertNotIn('matching_approval', self.engine.get('quality'))
        self.assertFalse((self.proc / 'helper_matching_approval.json').exists())

    def test_launch_requires_gate_report_and_explicit_notes(self):
        report = quality.publish(self.state, self.proc); self.engine.save(self.state)
        valid = dict(report_id=report['report_id'], matching_reviewed=True, matching_notes='檢查資料與日誌')
        for changes in ({'report_id':'stale'}, {'matching_reviewed':False}, {'matching_notes':'a'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.engine.launch('quality', 'matching_continue', options={**valid, **changes})
        for status in ('ready', 'sampling_review', 'sampling_ready', 'complete', 'failed'):
            self.state['status'] = status; self.engine.save(self.state)
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.engine.launch('quality', 'matching_continue', options=valid)

    def test_busy_and_wrong_stage_refresh_preserve_previous_report(self):
        self.approve(); self.engine.save(self.state)
        original = (self.proc / 'helper_matching_quality.json').read_bytes()
        with patch.object(self.engine, 'active', 'other'), self.assertRaises(ValueError):
            self.engine.refresh_matching_quality('quality')
        self.state['status'] = 'sampling_ready'; self.engine.save(self.state)
        with self.assertRaises(ValueError): self.engine.refresh_matching_quality('quality')
        self.assertEqual((self.proc / 'helper_matching_quality.json').read_bytes(), original)

    def test_command_and_offset_changes_after_launch_stop_before_cmd2(self):
        report = quality.publish(self.state, self.proc); self.engine.save(self.state)
        with patch('alosstack_helper.engine.threading.Thread'):
            self.engine.launch('quality', 'matching_continue', options=dict(report_id=report['report_id'],
                               matching_reviewed=True, matching_notes='檢查資料與日誌'))
        (self.folder / 'cull.off').write_text('changed during launch')
        state = self.engine.get('quality')
        with patch.object(self.engine, 'shell') as shell:
            self.engine.worker(state, 'run')
        shell.assert_not_called()
        self.assertEqual(state['status'], 'failed')

    def test_mosaic_metadata_is_bound_after_cmd1_not_rejected_against_pre_match(self):
        metadata = self.proc / 'dates/240822/240822.track.xml'
        metadata.write_text('pre matching')
        receipt = self.proc / 'helper_sampling/receipt.json'
        value = json.loads(receipt.read_text())
        value['post']['metadata'] = {'240822/240822.track.xml': quality.file_evidence(metadata)}
        atomic_json(receipt, value)
        metadata.write_text('new mosaicked metadata')
        self.approve()
        metadata.write_text('changed again')
        with self.assertRaises(ValueError): quality.verified_approval(self.state, self.proc)

    def test_symlink_and_path_traversal_do_not_read_external_evidence(self):
        (self.folder / 'cull.off').unlink()
        outside = self.root / 'external.off'; outside.write_text('secret')
        (self.folder / 'cull.off').symlink_to(outside)
        report = quality.collect(self.state, self.proc)
        self.assertFalse(report['can_continue'])
        self.assertNotIn('secret', json.dumps(report))
        with self.assertRaises(ValueError): quality.local_file(self.proc, '../external.off')
        with self.assertRaises(ValueError): quality.local_file(self.proc, str(outside))


if __name__ == '__main__':
    unittest.main()
