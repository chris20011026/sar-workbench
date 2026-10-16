import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper import sampling
from alosstack_helper.core import atomic_json, inspect_data
from alosstack_helper.engine import Engine
from alosstack_helper.preflight import check_project


def command(config):
    return f'''#!/bin/bash
dates=(240822 250425)
dates2=(240822)
# read data
{config['env']}/share/isce2/alosStack/read_data.py -idir {config['data']} -odir dates -ref_date 250425 -sec_date 240822 250425 -pol HH -frames 0480 -virtual
# compute baseline
/env/compute_baseline.py -idir dates
for i in ${{!dates2[@]}}; do
    /env/estimate_slc_offset.py -sec_date ${{dates2[i]}}
done
'''


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.proc = self.root/'proc'; self.proc.mkdir()
        (self.root/'.helper').mkdir()
        (self.root/'data').mkdir()
        self.config = dict(env='/env', root=str(self.root), data=str(self.root/'data'), reference='250425',
                           workflow='alosStack', sampling_equalization=True, properties={'frames':'["0480"]'})
        self.engine = Engine(self.root/'registry')
        self.state = dict(id='sampling', config=self.config, status='ready', stage='', history=[],
                          hashes={}, ion={'estimate':False}, expected_pairs=['240822-250425'])
        self.engine.projects['sampling'] = str(self.root); self.engine.save(self.state)

    def tearDown(self): self.tmp.cleanup()

    def test_split_keeps_arrays_and_removes_only_read_command(self):
        request, rest = sampling.split_read_command(command(self.config), self.config)
        self.assertIn('-virtual', request['argv'])
        self.assertEqual(request['dates'], ['240822','250425'])
        self.assertNotIn('read_data.py', rest)
        self.assertIn('dates2=(240822)', rest)
        self.assertIn('estimate_slc_offset.py', rest)

    def test_split_blocks_unsafe_or_unknown_order(self):
        good = command(self.config)
        for text in (good+good, good.replace('# read data','evil.py'),
                     good.replace(' -virtual',' -virtual; bad'), good.replace('-odir dates','-odir elsewhere'),
                     good.replace('-pol HH','-pol HH -pol VV'), good.replace('-frames 0480','-frames 0490'),
                     good.replace('-sec_date 240822 250425','-sec_date 240822')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                sampling.split_read_command(text,self.config)

    def test_selected_frame_filter_handles_two_vs_one_without_guessing(self):
        source=self.root/'data'
        for d,frames in [('240822',['0480','0490']),('250425',['0480'])]:
            folder=source/d; folder.mkdir(parents=True)
            for frame in frames:
                for p in ('LED','IMG-HH'):
                    (folder/f'{p}-ALOS200001{frame}-{d}-FBDR1.1__A').touch()
        self.assertTrue(inspect_data(source)[1]['warnings'])
        self.assertTrue(all(not row['warnings'] for row in inspect_data(source,['0480'])))
        self.assertTrue(inspect_data(source,['0490'])[1]['warnings'])
        for frames in ("[480]", "['0480','0480']", '__import__("os")', "['../x']"):
            with self.assertRaises(ValueError): sampling.selected_frames({'properties':{'frames':frames}})

    def plan(self):
        report={'plan_id':'bound-to-inputs','rows':[]}
        atomic_json(self.proc/'helper_sampling_plan.json', report)
        return report

    def test_engine_stops_after_read_before_any_matching_then_continues_once(self):
        calls=[]
        def invoke(e,s,mode):
            calls.append(mode)
            if mode=='plan': self.plan()
        self.engine.shell=lambda s,p:calls.append(p.name)
        product=self.proc/'pairs/240822-250425/out.unw.geo'; product.parent.mkdir(parents=True);product.write_bytes(b'ok')
        with patch('alosstack_helper.sampling.invoke',side_effect=invoke):
            self.engine.worker(self.state,'run')
            self.assertEqual(calls,['read','plan'])
            self.assertEqual(self.state['status'],'sampling_review')
            with self.assertRaises(ValueError): self.engine.launch('sampling','run')
            with self.assertRaises(ValueError): self.engine.launch('sampling','sampling_continue',options={})
            opts=dict(plan_id='bound-to-inputs', frames_verified=True, frame_notes='已人工核對影像外框')
            with patch('alosstack_helper.engine.threading.Thread'):
                self.engine.launch('sampling','sampling_continue',options=opts)
            self.state=self.engine.get('sampling')
            self.engine.worker(self.state,'run')
        self.assertEqual(calls,['read','plan','apply','verify','helper_cmd1_after_read.sh','cmd_2.sh','cmd_4.sh'])
        self.assertEqual(self.state['status'],'complete')

    def test_sampling_failure_blocks_matching_and_preserves_consent(self):
        self.state['sampling']=self.plan()
        sampling.approve(self.state,dict(plan_id='bound-to-inputs',frames_verified=True,frame_notes='確認測試'),self.proc)
        self.engine.resume_phase=sampling.APPLY_PHASE
        with patch('alosstack_helper.sampling.invoke',side_effect=RuntimeError('interrupted')), patch.object(self.engine,'shell') as shell:
            self.engine.worker(self.state,'run');shell.assert_not_called()
        self.assertEqual(self.state['status'],'failed')
        self.assertEqual(self.state['failed_phase'],sampling.APPLY_PHASE)

    def test_changed_report_and_missing_manual_review_are_rejected(self):
        self.state['sampling']=self.plan()
        for opts in ({}, dict(plan_id='bound-to-inputs',frames_verified=False,frame_notes='依據'),
                     dict(plan_id='different',frames_verified=True,frame_notes='確認範圍')):
            with self.assertRaises(ValueError):sampling.approve(self.state,opts,self.proc)
        atomic_json(self.proc/'helper_sampling_plan.json',{'plan_id':'changed'})
        with self.assertRaises(ValueError): sampling.approve(self.state,dict(plan_id='bound-to-inputs',frames_verified=True,frame_notes='確認範圍'),self.proc)

    def test_recovery_after_matching_does_not_repeat_sampling(self):
        self.state['sampling']=self.plan();self.state['sampling_approval']={'frames_verified':True}
        self.engine.resume_phase='cmd2 · 干涉配對'
        calls=[];self.engine.shell=lambda s,p:calls.append(p.name)
        product=self.proc/'pairs/240822-250425/out.unw.geo';product.parent.mkdir(parents=True);product.write_bytes(b'ok')
        with patch('alosstack_helper.sampling.invoke') as worker:
            self.engine.worker(self.state,'run');worker.assert_not_called()
        self.assertEqual(calls,['cmd_2.sh','cmd_4.sh'])


if __name__=='__main__': unittest.main()
