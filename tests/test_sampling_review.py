"""Review manager guards use synthetic inputs and never start ISCE processing."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from alosstack_helper.core import atomic_json
from alosstack_helper.sampling_review import SamplingReviews, stamp


PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lp0AAAAASUVORK5CYII=')


class ReviewEngine:
    def __init__(self,state):
        self.state=state
        self.active=None
        self.launch=Mock()
        self.run=Mock()
        self.shell=Mock()
        self.save=Mock()

    def require_processing_location(self,key):
        if key!='project':raise ValueError('unknown project')
        return copy.deepcopy(self.state)

    def paths(self,state):
        root=Path(state['config']['root'])
        return root,root/'proc',root/'.helper'


class SamplingReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'source';self.source.mkdir()
        self.raw=self.source/'sample.slc';self.raw.write_bytes(b'original input')
        self.project=self.root/'project'
        self.proc=self.project/'proc';self.proc.mkdir(parents=True)
        self.meta=self.project/'.helper';self.meta.mkdir()
        self.folder=self.meta/'sampling-review'
        self.plan=dict(plan_id='plan-A',dates=['240822','250425'],reference='250425',rows=[])
        atomic_json(self.proc/'helper_sampling_plan.json',self.plan)
        self.state=dict(id='project',status='sampling_review',sampling=self.plan,
                        config=dict(root=str(self.project),data=str(self.source),env='/env',
                                    workflow='alosStack',sampling_equalization=True,properties={'polarization':'HH'}))
        self.engine=ReviewEngine(self.state)
        self.reviews=SamplingReviews(self.engine)
        self.image_id='a'*32
        self.calls=[]
        self.reviews.worker=self.worker

    def tearDown(self):
        self.tmp.cleanup()

    def worker(self,state,folder,module,request):
        self.calls.append((module,copy.deepcopy(request)))
        if module=='sampling_review_worker':
            return dict(plan_id=self.plan['plan_id'],reference=self.plan['reference'],products=[],
                        source_fingerprint='source-fingerprint',coverage=dict(complete=False,common=None),
                        _files=[stamp(self.raw)],_image_specs={self.image_id:dict(
                            path=str(self.raw),width=1,length=1,image_offset=0,line_offset=8,byte_order='LSB')})
        if module=='sampling_preview_worker':
            Path(request['output']).write_bytes(PNG)
            return dict(status='complete',output=request['output'],width=1,height=1,source_unchanged=True,
                        source_stamp={k:v for k,v in stamp(self.raw).items() if k!='path'})
        raise AssertionError(module)

    def load(self):
        return self.reviews.review('project')

    def preview(self):
        return self.reviews.preview('project',self.image_id,self.plan['plan_id'])

    def post(self):
        self.state['status']='sampling_ready'
        self.state['sampling_result']=dict(plan_id='post-A',changed=0,rows=[])
        approval=dict(plan_id='plan-A',frames_verified=True,notes='人工檢查測試',time='synthetic')
        self.state['sampling_approval']=approval
        atomic_json(self.proc/'helper_sampling_approval.json',approval)
        atomic_json(self.proc/'helper_sampling/receipt.json',
                    dict(plan_id='plan-A',frames=approval,post=self.state['sampling_result']))

    def test_catalogue_and_previews_are_read_only_for_processing_state_and_sources(self):
        before=copy.deepcopy(self.state)
        original=self.raw.read_bytes();identity=stamp(self.raw)
        report=self.load()
        result=self.preview()
        self.assertEqual(self.reviews.image('project',self.image_id),PNG)
        self.assertTrue(result['url'].startswith('/api/sampling-preview-image?'))
        self.assertFalse(any(k.startswith('_') for k in report))
        self.assertEqual(self.state,before)
        self.assertEqual(self.raw.read_bytes(),original)
        self.assertEqual(stamp(self.raw),identity)
        for action in (self.engine.launch,self.engine.run,self.engine.shell,self.engine.save):
            action.assert_not_called()
        self.assertFalse((self.proc/'helper_sampling_approval.json').exists())
        self.assertFalse((self.proc/'helper_sampling/receipt.json').exists())
        self.assertFalse((self.proc/'dates').exists())

    def test_processing_or_wrong_states_cannot_be_reviewed(self):
        self.engine.active='another'
        with self.assertRaisesRegex(ValueError,'運算'):self.load()
        self.engine.active=None
        for status in ('new','ready','running','failed','review','complete'):
            self.state['status']=status
            with self.subTest(status=status),self.assertRaises(ValueError):self.load()
        self.assertEqual(self.calls,[])

    def test_stale_source_blocks_preview_and_image_cache_reuse(self):
        self.load();self.preview()
        self.raw.write_bytes(b'changed input')
        with self.assertRaisesRegex(ValueError,'來源'):self.preview()
        with self.assertRaisesRegex(ValueError,'來源'):self.reviews.image('project',self.image_id)
        self.raw.unlink()
        with self.assertRaises(ValueError):self.preview()
        self.assertEqual(len(self.calls),2)

    def test_plan_edit_or_new_plan_invalidates_catalogue(self):
        self.load()
        atomic_json(self.proc/'helper_sampling_plan.json',{**self.plan,'reference':'240822'})
        with self.assertRaisesRegex(ValueError,'取樣計畫'):self.preview()
        self.state['sampling']={**self.plan,'plan_id':'plan-B'}
        atomic_json(self.proc/'helper_sampling_plan.json',self.state['sampling'])
        with self.assertRaisesRegex(ValueError,'重新載入'):self.preview()

    def test_source_change_during_worker_never_publishes_catalogue(self):
        original=self.worker
        def change(*args):
            result=original(*args);self.raw.write_bytes(b'changed during scan');return result
        self.reviews.worker=change
        with self.assertRaisesRegex(ValueError,'來源'):self.load()
        self.assertFalse((self.folder/'catalogue.json').exists())

    def test_plan_change_during_worker_never_publishes_catalogue(self):
        original=self.worker
        def change(*args):
            result=original(*args)
            self.state['sampling']={**self.plan,'plan_id':'plan-B'}
            atomic_json(self.proc/'helper_sampling_plan.json',self.state['sampling'])
            return result
        self.reviews.worker=change
        with self.assertRaisesRegex(ValueError,'計畫已改變'):self.load()
        self.assertFalse((self.folder/'catalogue.json').exists())

    def test_image_ids_are_opaque_and_must_belong_to_current_plan(self):
        self.load()
        for value in ('../sample','a'*31,'g'*32,str(self.raw)):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.reviews.preview('project',value,self.plan['plan_id'])
            with self.subTest(image=value),self.assertRaises(ValueError):
                self.reviews.image('project',value)
        with self.assertRaisesRegex(ValueError,'影像或計畫'):
            self.reviews.preview('project','b'*32,self.plan['plan_id'])
        with self.assertRaisesRegex(ValueError,'影像或計畫'):
            self.reviews.preview('project',self.image_id,'different-plan')
        self.assertEqual(len(self.calls),1)

    def test_post_result_is_bound_to_receipt_and_manual_approval(self):
        self.post();self.load()
        self.assertEqual(self.calls[-1][1]['post'],self.state['sampling_result'])
        receipt=self.proc/'helper_sampling/receipt.json'
        original=json.loads(receipt.read_text())
        for change in ({'plan_id':'other'},{'post':{}},{'frames':{}}):
            atomic_json(receipt,{**original,**change})
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.reviews.context('project')
        atomic_json(receipt,original)
        atomic_json(self.proc/'helper_sampling_approval.json',
                    {**self.state['sampling_approval'],'frames_verified':False})
        with self.assertRaises(ValueError):self.reviews.context('project')
        (self.proc/'helper_sampling_approval.json').unlink()
        with self.assertRaises(ValueError):self.reviews.context('project')

    def test_post_stage_change_requires_fresh_catalogue(self):
        self.load();self.post()
        with self.assertRaisesRegex(ValueError,'重新載入'):self.preview()

    def test_inconsistent_review_and_ready_results_are_not_displayed(self):
        self.state['status']='sampling_ready'
        with self.assertRaisesRegex(ValueError,'缺少取樣成果'):self.load()
        self.post();self.state['status']='sampling_review'
        with self.assertRaisesRegex(ValueError,'舊取樣成果'):self.load()
        self.assertEqual(self.calls,[])

    def test_symlink_catalogue_plan_and_preview_are_rejected(self):
        self.load();self.preview()
        png=self.folder/(self.image_id+'.png')
        png.unlink();png.symlink_to(self.raw)
        with self.assertRaises(ValueError):self.preview()
        with self.assertRaises(ValueError):self.reviews.image('project',self.image_id)
        plan=self.proc/'helper_sampling_plan.json';target=self.root/'external-plan.json'
        target.write_bytes(plan.read_bytes());plan.unlink();plan.symlink_to(target)
        with self.assertRaises(ValueError):self.reviews.context('project')
        plan.unlink();plan.write_bytes(target.read_bytes())
        catalogue=self.folder/'catalogue.json';target=self.root/'external-catalogue.json'
        target.write_bytes(catalogue.read_bytes());catalogue.unlink();catalogue.symlink_to(target)
        with self.assertRaises(ValueError):self.load()

    def test_cached_sidecar_mismatch_regenerates_and_corrupt_image_get_rejects(self):
        self.load();self.preview()
        sidecar=self.folder/(self.image_id+'.json')
        result=json.loads(sidecar.read_text())
        for field,bad in [('image_id','b'*32),('source_fingerprint','other'),('status','failed')]:
            atomic_json(sidecar,{**result,field:bad})
            before=len(self.calls)
            self.preview()
            self.assertEqual(len(self.calls),before+1,field)
            result=json.loads(sidecar.read_text())
        png=self.folder/(self.image_id+'.png');png.write_bytes(b'corrupted cached PNG')
        with self.assertRaises(ValueError):self.reviews.image('project',self.image_id)
        before=len(self.calls);self.preview()
        self.assertEqual(len(self.calls),before+1)
        self.assertEqual(self.reviews.image('project',self.image_id),PNG)

    def test_candidate_source_is_explicitly_retained_and_can_be_cleared(self):
        self.reviews.review('project',' /comparison ')
        self.assertEqual(self.calls[-1][1]['extra_source'],'/comparison')
        self.load()
        self.assertEqual(self.calls[-1][1]['extra_source'],'/comparison')
        self.reviews.review('project','')
        self.assertEqual(self.calls[-1][1]['extra_source'],'')

    def test_review_lock_blocks_duplicate_work_without_touching_source(self):
        self.reviews.lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError,'另一個'):self.load()
            with self.assertRaisesRegex(ValueError,'另一個'):self.preview()
            self.assertEqual(self.calls,[])
        finally:self.reviews.lock.release()
        self.load()


if __name__=='__main__':
    unittest.main()
