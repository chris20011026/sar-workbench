import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from alosstack_helper.analysis import Analyses
from alosstack_helper.analysis_recovery import AnalysisRecovery, controls
from alosstack_helper.core import atomic_json
from alosstack_helper import mintpy_diagnostics as diagnostics
import test_analysis as analysis_tests


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        analysis_tests.AnalysisTests.setUp(self)
        (self.env/'conda-meta').mkdir();(self.env/'conda-meta/mintpy.json').write_text('{"version":"test"}')
        with patch('threading.Thread.start'):self.job=self.a.create('test','mintpy',{})
        self.e.active=None;self.path=self.a.folder('test',self.job['id'])
        self.recovery=AnalysisRecovery(self.a,'test',self.job['id'])

    def tearDown(self):analysis_tests.AnalysisTests.tearDown(self)

    def failed(self,mode='invert'):
        (self.path/'exports').mkdir(exist_ok=True)
        (self.path/'inputs').mkdir(exist_ok=True)
        (self.path/'inputs/ifgramStack.h5').write_bytes(b'complete-hdf5-placeholder')
        (self.path/'mintpy.cfg').write_text('mintpy.load.metaFile = '+str(self.path/'source/track.xml')+'\n')
        (self.path/'source').mkdir(exist_ok=True)
        (self.path/'source/input.unw').symlink_to(self.job['inventory']['files'][0])
        if mode in ('corrections','finalize'):
            report=diagnostics.seal(self.path,dict(stage='unwrap' if mode=='corrections' else 'residual',dates=['20200101','20200201','20200301']))
            choice=diagnostics.decision(report,dict(reviewed=True,notes='已判讀合成資料',excluded_dates=[],reference_date='20200101'))
            self.job['review_pending']=choice;self.job['review_decisions']=[choice]
        atomic_json(self.path/'analysis.json',self.job)
        self.job=self.recovery.capture(self.job,mode)
        (self.path/'inputs/ifgramStack.h5').write_bytes(b'partial-and-invalid')
        (self.path/'partial.h5').write_bytes(b'not a valid hdf5')
        self.job.update(status='failed',error='interrupted')
        atomic_json(self.path/'analysis.json',self.job)
        return self.job['recovery_checkpoint']['id']

    def test_preserved_checkpoint_copies_not_failed_partial(self):
        ident=self.failed();before={str(p):p.read_bytes() for p in self.path.rglob('*') if p.is_file() and not p.is_symlink()}
        result=self.recovery.report();self.assertTrue(result['can_recover']);self.assertEqual(result['status'],'loaded')
        destination=self.path.parent/'111111111111'
        job,_=self.recovery.restore(ident,destination,'111111111111')
        self.assertEqual(job['status'],'loaded');self.assertEqual(job['recovery_origin']['source_run'],self.job['id'])
        self.assertEqual((destination/'inputs/ifgramStack.h5').read_bytes(),b'complete-hdf5-placeholder')
        self.assertFalse((destination/'partial.h5').exists())
        self.assertNotEqual((destination/'inputs/ifgramStack.h5').stat().st_ino,(self.recovery.checkpoint(ident)/'data/inputs/ifgramStack.h5').stat().st_ino)
        self.assertIn(str(destination),(destination/'mintpy.cfg').read_text())
        self.assertTrue((destination/'source/input.unw').is_symlink())
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.path.rglob('*') if p.is_file() and not p.is_symlink()})

    def test_restore_keeps_gate_and_prior_notes_without_auto_approval(self):
        ident=self.failed('finalize');destination=self.path.parent/'111111111111'
        job,_=self.recovery.restore(ident,destination,'111111111111')
        self.assertEqual(job['status'],'residual_review');self.assertNotIn('review_pending',job)
        self.assertEqual(job['review_decisions'],[])
        self.assertEqual(job['recovery_origin']['previous_decision']['notes'],'已判讀合成資料')
        self.assertTrue(job['recovery_origin']['science_approval_required'])

    def test_actual_finalize_time_reference_edit_is_allowed(self):
        self.failed('finalize')
        self.job['options']['reference_date']='200101';atomic_json(self.path/'analysis.json',self.job)
        self.assertTrue(self.recovery.report()['can_recover'])
        self.job['options']['reference_date']='200201';atomic_json(self.path/'analysis.json',self.job)
        self.assertIn('參數已改變',self.recovery.report()['reason'])

    def test_corrupt_copy_or_receipt_rejected(self):
        ident=self.failed();checkpoint=self.recovery.checkpoint(ident)
        (checkpoint/'data/inputs/ifgramStack.h5').write_bytes(b'edited')
        self.assertIn('完整性',self.recovery.report()['reason'])

    def test_modified_source_and_environment_rejected(self):
        self.failed();source=Path(self.job['inventory']['files'][0]);source.write_bytes(source.read_bytes()+b'x')
        self.assertIn('來源',self.recovery.report()['reason'])
        source.write_bytes(source.read_bytes()[:-1])
        # The mtime also belongs to input identity; restoring just content does
        # not waive the invalidation. Start a separate unchanged fixture.
        self.assertFalse(self.recovery.report()['can_recover'])

    def test_environment_change_rejected(self):
        self.failed();(self.env/'conda-meta/mintpy.json').write_text('{"version":"changed"}')
        self.assertIn('環境',self.recovery.report()['reason'])

    def test_controls_and_project_change_rejected(self):
        self.failed();self.job['options']['memory']=99;atomic_json(self.path/'analysis.json',self.job)
        self.assertIn('參數',self.recovery.report()['reason'])
        self.s['config']['reference']='190518';self.e.save(self.s)
        self.assertIn('專案設定',self.recovery.report()['reason'])

    def test_old_run_and_wrong_receipt_cannot_recover(self):
        self.job['status']='failed';atomic_json(self.path/'analysis.json',self.job)
        self.assertIn('沒有完整',self.recovery.report()['reason'])
        ident=self.failed()
        with self.assertRaisesRegex(ValueError,'恢復點已變更'):self.recovery.validate('0'*32)
        with self.assertRaisesRegex(ValueError,'先確認'):self.a.recover('test',self.job['id'],dict(checkpoint_id=ident))

    def test_live_worker_blocks_recovery(self):
        self.failed();(self.path/'worker.pid').write_text(str(os.getpid()))
        self.assertIn('背景程序',self.recovery.report()['reason'])

    def test_disk_shortage_and_cancel_leave_failed_run(self):
        ident=self.failed();destination=self.path.parent/'111111111111'
        with patch('alosstack_helper.analysis_recovery.shutil.disk_usage',return_value=type('D',(),{'free':0})()):
            with self.assertRaisesRegex(ValueError,'空間不足'):self.recovery.restore(ident,destination,'111111111111')
        self.assertFalse(destination.exists())
        self.e.cancel.set()
        with self.assertRaisesRegex(RuntimeError,'已停止'):self.recovery.capture(self.job,'invert')
        self.assertEqual(self.a.get('test',self.job['id'])['status'],'failed')

    def test_foreign_link_rejected_before_processing(self):
        (self.path/'outside').symlink_to('/etc/hosts')
        with self.assertRaisesRegex(ValueError,'連結'):self.recovery.capture(self.job,'load')

    def test_restore_copy_cancellation_cleans_only_new_destination(self):
        ident=self.failed();destination=self.path.parent/'111111111111'
        def interrupted(source,target,*args,**kwargs):
            (target/'partial').write_text('ours');raise RuntimeError('已停止複製')
        with patch('alosstack_helper.analysis_recovery.copy_tree',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'已停止'):self.recovery.restore(ident,destination,'111111111111')
        self.assertFalse(destination.exists());self.assertTrue((self.path/'partial.h5').is_file())
        destination.mkdir();(destination/'unrelated').write_text('keep')
        with self.assertRaises(FileExistsError):self.recovery.restore(ident,destination,'111111111111')
        self.assertEqual((destination/'unrelated').read_text(),'keep')

    def test_failed_loading_retries_in_new_empty_version_only(self):
        # Run an actual failing child that leaves partial load output behind.
        script='from pathlib import Path; Path("source").mkdir(); Path("source/partial").write_text("broken"); raise SystemExit(9)'
        with patch.object(self.a,'command',return_value=[sys.executable,'-c',script]):
            self.a.worker('test',self.job['id'],'load')
        failed=self.a.get('test',self.job['id']);self.assertEqual(failed['status'],'failed')
        report=self.a.recovery_report('test',self.job['id']);self.assertEqual(report['status'],'ready');self.assertTrue(report['can_recover'])
        with patch('threading.Thread.start'):
            recovered=self.a.recover('test',self.job['id'],dict(reviewed=True,checkpoint_id=report['checkpoint_id']))
        self.assertNotEqual(recovered['id'],self.job['id']);self.assertEqual(recovered['status'],'running')
        self.assertFalse((self.a.folder('test',recovered['id'])/'source').exists())
        self.assertEqual((self.path/'source/partial').read_text(),'broken')
        self.e.active=None

    def test_unpublished_staging_is_not_listed_and_live_operation_blocks_new_work(self):
        self.failed();container=self.path.parent/'.recovery-staging/111111111111'
        (container/'data').mkdir(parents=True)
        atomic_json(container/'data/analysis.json',dict(self.job,id='111111111111',status='loaded'))
        atomic_json(container/'operation.json',dict(server_pid=os.getpid()))
        from alosstack_helper.engine import Engine
        restarted=Analyses(Engine(self.p/'registry'))
        self.assertEqual([j['id'] for j in restarted.listing('test')],[self.job['id']])
        with self.assertRaises(FileNotFoundError):restarted.get('test','111111111111')
        with self.assertRaisesRegex(ValueError,'恢復程序'):restarted.reserve()
        self.assertFalse(restarted.recovery_report('test',self.job['id'])['can_recover'])

    def test_latest_checkpoint_replaces_old_copy_only_after_success(self):
        one=self.recovery.capture(self.job,'load');first=one['recovery_checkpoint']['id']
        two=self.recovery.capture(one,'load');second=two['recovery_checkpoint']['id']
        self.assertNotEqual(first,second);self.assertFalse(self.recovery.checkpoint(first).exists())
        self.assertTrue(self.recovery.checkpoint(second).exists())


@unittest.skipUnless(all(importlib.util.find_spec(name) is not None for name in ('isce','mintpy','h5py','numpy')), 'requires full scientific environment')
class NativeInterruptionTests(unittest.TestCase):
    def test_killed_recovery_server_never_publishes_half_rebased_version(self):
        from test_legacy import dataset
        from alosstack_helper.legacy import create_wrapper,inspect_legacy
        from alosstack_helper.engine import Engine
        import numpy as np
        import h5py
        with tempfile.TemporaryDirectory(prefix='mintpy-restore-publication-') as temp:
            p=Path(temp);request,pairs=dataset(p,Path(sys.prefix));source=Path(request['source'])
            y,x=np.mgrid[:16,:32]
            for i,pair in enumerate(pairs):
                raw=np.ones((16,2,32),dtype='<f4');raw[:,1,:]=1+(i+1)*(x+y*.2)*.01
                for f in (source/'pairs'/pair/'insar').glob('*.unw'):f.write_bytes(raw.tobytes())
            before={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()}
            state=create_wrapper(request,inspect_legacy(request),'aabbccddeeff')
            engine=Engine(p/'registry');engine.register(state['id'],state['config']['root']);analyses=Analyses(engine)
            job=analyses.create(state['id'],'mintpy',{})
            def wait(status):
                deadline=time.monotonic()+120
                while engine.active and time.monotonic()<deadline:time.sleep(.05)
                result=analyses.get(state['id'],job['id']);self.assertEqual(result['status'],status,result)
                return result
            wait('loaded');analyses.invert(state['id'],job['id'],dict(reviewed=True));job=wait('diagnostic_review')
            folder=analyses.folder(state['id'],job['id']);job['status']='failed';atomic_json(folder/'analysis.json',job)
            report=analyses.recovery_report(state['id'],job['id']);self.assertTrue(report['can_recover'],report)
            helper=p/'paused_rebase.py'
            helper.write_text('import sys,time\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[1]/'src'))+')\nfrom alosstack_helper.analysis_recovery_worker import rebase\nrebase(*sys.argv[1:])\n(Path(sys.argv[1]).parent/"helper-ready").write_text("rebased, not yet published")\ntime.sleep(120)\n')
            server=p/'recovery_server.py'
            server.write_text('''import sys,json\nfrom unittest.mock import patch\nfrom alosstack_helper.engine import Engine\nfrom alosstack_helper.analysis import Analyses\nfrom alosstack_helper import analysis\na=Analyses(Engine(sys.argv[1]));original=analysis.subprocess.Popen\ndef launch(command,*args,**kwargs):\n    if any(str(x).endswith("analysis_recovery_worker.py") for x in command):\n        command=[sys.executable,sys.argv[4],*command[-3:]]\n    return original(command,*args,**kwargs)\nwith patch.object(analysis.subprocess,"Popen",side_effect=launch):\n    a.recover(sys.argv[2],sys.argv[3],dict(reviewed=True,checkpoint_id=sys.argv[5]))\n''')
            env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
            parent=None;worker_pid=None
            try:
                with (p/'restore.log').open('w') as log:
                    parent=subprocess.Popen([sys.executable,str(server),str(p/'registry'),state['id'],job['id'],str(helper),report['checkpoint_id']],env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    staging=folder.parent/'.recovery-staging';deadline=time.monotonic()+60;markers=[]
                    while not markers and parent.poll() is None and time.monotonic()<deadline:
                        markers=list(staging.glob('*/helper-ready'));time.sleep(.05)
                    self.assertTrue(markers,(p/'restore.log').read_text())
                    container=markers[0].parent;operation=json.loads((container/'operation.json').read_text());worker_pid=operation['worker_pid']
                    self.assertFalse((folder.parent/operation['new_id']).exists())
                    self.assertEqual([v['id'] for v in analyses.listing(state['id'])],[job['id']])
                    os.killpg(parent.pid,signal.SIGTERM);parent.wait(timeout=10)
                    restarted=Analyses(Engine(p/'registry'))
                    with self.assertRaisesRegex(ValueError,'恢復程序'):restarted.reserve()
                    with self.assertRaises(FileNotFoundError):restarted.get(state['id'],operation['new_id'])
                    self.assertFalse(restarted.recovery_report(state['id'],job['id'])['can_recover'])
                    os.killpg(worker_pid,signal.SIGTERM)
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        try:os.kill(worker_pid,0)
                        except ProcessLookupError:break
                        time.sleep(.05)
                    ready=restarted.recovery_report(state['id'],job['id']);self.assertTrue(ready['can_recover'],ready)
                    restored=restarted.recover(state['id'],job['id'],dict(reviewed=True,checkpoint_id=ready['checkpoint_id']))
                    dest=restarted.folder(state['id'],restored['id']);self.assertEqual(restored['status'],'loaded')
                    self.assertNotEqual(restored['id'],operation['new_id'])
                    with h5py.File(dest/'inputs/ifgramStack.h5','r') as f:self.assertEqual(f['unwrapPhase'].shape,(3,16,32))
                    self.assertNotIn(str(folder),(dest/'mintpy.cfg').read_text())
                    self.assertEqual(before,{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()})
            finally:
                if parent and parent.poll() is None:os.killpg(parent.pid,signal.SIGKILL);parent.wait()
                if worker_pid:
                    try:os.killpg(worker_pid,signal.SIGKILL)
                    except ProcessLookupError:pass

    def test_controller_captures_all_native_stages_and_recovery_reseals_residual_gate(self):
        from test_legacy import dataset
        from alosstack_helper.legacy import create_wrapper,inspect_legacy
        from alosstack_helper.engine import Engine
        from mintpy.utils import ptime
        import h5py
        import numpy as np
        with tempfile.TemporaryDirectory(prefix='mintpy-recovery-controller-') as temp:
            p=Path(temp);request,pairs=dataset(p,Path(sys.prefix));source=Path(request['source'])
            y,x=np.mgrid[:16,:32]
            for pair in pairs:
                begin,end=ptime.yyyymmdd2years(['20'+v for v in pair.split('-')])
                raw=np.ones((16,2,32),dtype='<f4');raw[:,1,:]=1+(end-begin)*(x+y*.2)*.01
                for f in (source/'pairs'/pair/'insar').glob('*.unw'):f.write_bytes(raw.tobytes())
            before={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()}
            state=create_wrapper(request,inspect_legacy(request),'aabbccddeeff')
            e=Engine(p/'registry');e.register(state['id'],state['config']['root']);a=Analyses(e)
            job=a.create(state['id'],'mintpy',{})
            def wait(run,status):
                deadline=time.monotonic()+120
                while e.active and time.monotonic()<deadline:time.sleep(.05)
                self.assertFalse(e.active,'native stage did not finish')
                result=a.details(state['id'],run)
                self.assertEqual(result['status'],status,result.get('error','')+'\n'+result.get('log','')[-4000:])
                return result
            job=wait(job['id'],'loaded');self.assertEqual(job['recovery_checkpoint']['status'],'ready')
            a.invert(state['id'],job['id'],dict(reviewed=True));job=wait(job['id'],'diagnostic_review')
            self.assertEqual(job['recovery_checkpoint']['status'],'loaded')
            a.review_continue(state['id'],job['id'],dict(reviewed=True,notes='合成控制器原生流程測試',report_id=job['diagnostics']['report_id']))
            job=wait(job['id'],'residual_review');self.assertEqual(job['recovery_checkpoint']['status'],'diagnostic_review')
            dates=job['diagnostics']['dates'];data=dict(reviewed=True,notes='合成控制器日期判讀測試',report_id=job['diagnostics']['report_id'],excluded_dates=[],reference_date=dates[1])
            a.review_continue(state['id'],job['id'],data);job=wait(job['id'],'complete')
            self.assertEqual(job['recovery_checkpoint']['status'],'residual_review')
            folder=a.folder(state['id'],job['id'])
            with h5py.File(folder/'velocity.h5','r') as f:expected=f['velocity'][:]
            # Treat output completion with a missing final service acknowledgement
            # as interrupted. The recovery must still roll back to the verified
            # residual gate, not reuse the reference-date-modified HDF5.
            saved=a.get(state['id'],job['id']);saved.update(status='failed',error='simulated missing final acknowledgement');atomic_json(folder/'analysis.json',saved)
            report=a.recovery_report(state['id'],job['id']);self.assertTrue(report['can_recover'],report)
            recovered=a.recover(state['id'],job['id'],dict(reviewed=True,checkpoint_id=report['checkpoint_id']))
            dest=a.folder(state['id'],recovered['id']);self.assertFalse((dest/'velocity.h5').exists())
            self.assertEqual(recovered['status'],'residual_review')
            new_report=json.loads((dest/'diagnostics.json').read_text())
            self.assertNotEqual(new_report['report_id'],data['report_id'])
            diagnostics.verify(dest,new_report['report_id'])
            data['report_id']=new_report['report_id'];a.review_continue(state['id'],recovered['id'],data)
            wait(recovered['id'],'complete')
            with h5py.File(dest/'velocity.h5','r') as f:np.testing.assert_allclose(f['velocity'][:],expected,rtol=0,atol=1e-10)
            self.assertEqual(before,{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()})

    def test_native_process_is_killed_then_new_version_repeats_only_unfinished_stage(self):
        """A real subprocess loads MintPy, mutates its HDF5, and is SIGTERM'd.

        The controlled pause is inside native inversion setup; the restored
        version must have the exact pre-stage HDF5 and complete native stages.
        """
        from test_legacy import dataset
        from alosstack_helper.legacy import create_wrapper,inspect_legacy
        from alosstack_helper.engine import Engine
        from alosstack_helper import science_worker as worker
        from alosstack_helper.analysis import fingerprint
        from alosstack_helper.workflows import inventory,options
        import h5py
        import numpy as np
        import contextlib,io
        old=Path.cwd()
        with tempfile.TemporaryDirectory(prefix='mintpy-recovery-native-') as temp:
            p=Path(temp);process=None
            try:
                os.chdir(p);request,pairs=dataset(p,Path(sys.prefix));source=Path(request['source'])
                y,x=np.mgrid[:16,:32]
                for i,pair in enumerate(pairs):
                    raw=np.ones((16,2,32),dtype='<f4');raw[:,1,:]=1+(i+1)*(x+y*.2)*.01
                    for f in (source/'pairs'/pair/'insar').glob('*.unw'):f.write_bytes(raw.tobytes())
                hashes=lambda:{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()}
                before=hashes();state=create_wrapper(request,inspect_legacy(request),'aabbccddeeff')
                e=Engine(p/'registry');e.register(state['id'],state['config']['root']);a=Analyses(e)
                run='111111111111';folder=a.folder(state['id'],run);folder.mkdir(parents=True)
                inv=inventory(state);job=dict(id=run,kind='mintpy',project_id=state['id'],status='running',history=[],inventory=inv,options=options({},inv),environment=str(sys.prefix),source_fingerprint=fingerprint(inv['files']),source_ion=state['ion'],source_ion_excluded=[])
                atomic_json(folder/'analysis.json',job)
                with contextlib.redirect_stdout(io.StringIO()):worker.load(folder/'analysis.json')
                job=json.loads((folder/'analysis.json').read_text());job.update(status='running',reference_pixel=[0,0])
                atomic_json(folder/'analysis.json',job);recovery=AnalysisRecovery(a,state['id'],run)
                job=recovery.capture(job,'invert');checkpoint=job['recovery_checkpoint']['id']
                checkpoint_h5=recovery.checkpoint(checkpoint)/'data/inputs/ifgramStack.h5'
                before_input=hashlib.sha256(checkpoint_h5.read_bytes()).hexdigest()
                script=p/'interrupt.py'
                script.write_text('''import sys,time\nfrom pathlib import Path\nimport h5py\nfrom alosstack_helper import science_worker as w\np=Path(sys.argv[1])\noriginal=w.runtime.after_step\ndef paused(path,name,error=None):\n    original(path,name,error=error)\n    if name=="reference_point" and error is None:\n        with h5py.File(p.parent/"inputs/ifgramStack.h5","r+") as f:\n            f["unwrapPhase"][0,0,0]=99999\n        (p.parent/"interruption-ready").write_text("native reference step completed, interrupted before next step")\n        time.sleep(120)\nw.runtime.after_step=paused\nw.invert(p)\n''')
                env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src'),'MPLCONFIGDIR':str(p/'mpl')}
                with (p/'child.log').open('w') as log:
                    process=subprocess.Popen([sys.executable,str(script),str(folder/'analysis.json')],cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    (folder/'worker.pid').write_text(str(process.pid))
                    deadline=time.monotonic()+60
                    while not (folder/'interruption-ready').exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.05)
                    self.assertTrue((folder/'interruption-ready').is_file(),(p/'child.log').read_text())
                    os.killpg(process.pid,signal.SIGTERM);self.assertNotEqual(process.wait(timeout=10),0)
                # Simulate a service restart instead of asking the old engine
                # to finish the interrupted thread.
                reloaded=Analyses(Engine(p/'registry'));reloaded.reconcile()
                self.assertEqual(reloaded.get(state['id'],run)['status'],'failed')
                report=reloaded.recovery_report(state['id'],run);self.assertTrue(report['can_recover'],report)
                failed_h5=(folder/'inputs/ifgramStack.h5').read_bytes()
                recovered=reloaded.recover(state['id'],run,dict(reviewed=True,checkpoint_id=report['checkpoint_id']))
                dest=reloaded.folder(state['id'],recovered['id'])
                self.assertEqual(recovered['status'],'loaded');self.assertNotEqual(recovered['id'],run)
                with h5py.File(dest/'inputs/ifgramStack.h5','r') as f:self.assertNotEqual(f['unwrapPhase'][0,0,0],99999)
                self.assertFalse((dest/'interruption-ready').exists())
                self.assertEqual(failed_h5,(folder/'inputs/ifgramStack.h5').read_bytes())
                with contextlib.redirect_stdout(io.StringIO()):
                    worker.invert(dest/'analysis.json')
                    first=json.loads((dest/'diagnostics.json').read_text());choice=diagnostics.decision(first,dict(reviewed=True,notes='合成恢復測試確認'))
                    recovered=json.loads((dest/'analysis.json').read_text());recovered['review_pending']=choice;atomic_json(dest/'analysis.json',recovered)
                    worker.corrections(dest/'analysis.json')
                    second=json.loads((dest/'diagnostics.json').read_text());choice=diagnostics.decision(second,dict(reviewed=True,notes='合成恢復測試確認',excluded_dates=[],reference_date=second['dates'][0]))
                    recovered=json.loads((dest/'analysis.json').read_text());recovered['review_pending']=choice;atomic_json(dest/'analysis.json',recovered)
                    worker.finalize(dest/'analysis.json')
                self.assertTrue((dest/'velocity.h5').is_file());self.assertEqual(before,hashes())
                self.assertEqual(before_input,hashlib.sha256(checkpoint_h5.read_bytes()).hexdigest())
            finally:
                if process and process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait()
                os.chdir(old)


if __name__=='__main__':unittest.main()
