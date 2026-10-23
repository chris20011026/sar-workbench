import json, tempfile, unittest
import contextlib, hashlib, importlib.util, io, os, sys
from pathlib import Path
from unittest.mock import patch
from alosstack_helper import mintpy_diagnostics as d

try:
    import h5py
    import numpy as np
    from alosstack_helper import science_worker as worker
except ImportError:
    worker=None


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name);(self.p/'exports').mkdir()
        (self.p/'mintpy.cfg').write_text('sample=yes')
    def tearDown(self):self.t.cleanup()
    def report(self,stage='unwrap'):
        return d.seal(self.p,dict(stage=stage,dates=['20200101','20200201','20200301','20200401']))
    def test_receipt_binds_report_and_scientific_evidence(self):
        r=self.report();self.assertEqual(d.verify(self.p,r['report_id'])['stage'],'unwrap')
        (self.p/'mintpy.cfg').write_text('sample=no')
        with self.assertRaisesRegex(ValueError,'來源或設定'):d.verify(self.p,r['report_id'])
    def test_new_output_invalidates_previous_receipt(self):
        r=self.report();(self.p/'timeseriesResidual.h5').write_bytes(b'changed')
        with self.assertRaises(ValueError):d.verify(self.p,r['report_id'])
    def test_edited_report_and_wrong_token_are_rejected(self):
        r=self.report()
        with self.assertRaises(ValueError):d.verify(self.p,'old')
        r['dates']=[];d.save(self.p/'diagnostics.json',r)
        with self.assertRaises(ValueError):d.verify(self.p,r['report_id'])
    def test_residual_decision_is_explicit_and_reference_is_retained(self):
        r=self.report('residual');data=dict(reviewed=True,notes='已檢查空間分布',reference_date='20200101')
        with self.assertRaisesRegex(ValueError,'明確'):d.decision(r,data)
        choice=d.decision(r,dict(data,excluded_dates=[]));self.assertEqual(choice['excluded_dates'],[])
        for dates in [['20200101'],['20210101'],['20200201','20200301']]:
            with self.assertRaises(ValueError):d.decision(r,dict(data,excluded_dates=dates))
        with self.assertRaises(ValueError):d.decision(r,dict(data,reviewed='yes',excluded_dates=[]))
        with self.assertRaises(ValueError):d.decision(r,dict(data,notes='',excluded_dates=[]))
    def test_receipt_export_remains_available(self):
        self.report();self.assertTrue((self.p/'exports/diagnostics-unwrap.json').is_file())
    def test_imported_source_receipt_is_revalidated_at_new_checkpoints(self):
        import threading
        from types import SimpleNamespace
        from alosstack_helper.analysis import Analyses
        engine=SimpleNamespace(lock=threading.RLock(),require_processing_location=lambda *a,**k:{'import_only':True})
        analyses=Analyses(engine)
        with patch.object(analyses,'reserve'),patch('alosstack_helper.legacy.require_legacy_source',side_effect=ValueError('來源已更換')) as check:
            with self.assertRaisesRegex(ValueError,'來源已更換'):analyses.review_continue('project','run',{})
        check.assert_called_once()


@unittest.skipIf(worker is None,'requires selected scientific environment')
class ScientificDiagnosticsTests(unittest.TestCase):
    dates=['20200101','20200201','20200301','20200401']
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name);(self.p/'inputs').mkdir();(self.p/'exports').mkdir()
        self.job=self.p/'analysis.json';self.config={'mintpy.timeFunc.excludeDate':False,'mintpy.residualRMS.deramp':False,'mintpy.residualRMS.cutoff':3}
        self.data=dict(status='residual_review',options={'reference_date':'200101'},reference_pixel=[0,0],history=[],parameter_report={'corrections':[]})
        d.save(self.job,self.data);(self.p/'mintpy.cfg').write_text('mintpy.timeFunc.excludeDate = no\n')
        pairs=[[self.dates[0],self.dates[1]],[self.dates[1],self.dates[2]],[self.dates[0],self.dates[2]],[self.dates[2],self.dates[3]]]
        with h5py.File(self.p/'inputs/ifgramStack.h5','w') as f:
            f['date']=np.asarray(pairs,dtype='S8');f['dropIfgram']=[True]*4;f['bperp']=[10,20,30,-5]
            f['coherence']=np.ones((4,3,4),dtype='f4')*.7;f['connectComponent']=np.ones((4,3,4),dtype='i2');f['unwrapPhase']=np.ones((4,3,4),dtype='f4')
        with h5py.File(self.p/'inputs/geometryRadar.h5','w') as f:f['waterMask']=np.ones((3,4),dtype='u1')
        with h5py.File(self.p/'maskTempCoh.h5','w') as f:f['mask']=np.ones((3,4),dtype='u1')
    def tearDown(self):self.t.cleanup()
    def timeseries(self,name='timeseries.h5',offset=0):
        with h5py.File(self.p/name,'w') as f:
            f['date']=np.asarray(self.dates,dtype='S8')
            f['timeseries']=np.arange(48,dtype='f4').reshape(4,3,4)*.001+offset
            f.attrs.update(REF_Y='0',REF_X='0',REF_DATE='20200101',UNIT='m',FILE_TYPE='timeseries',LENGTH='3',WIDTH='4')
    def test_network_uses_actual_kept_flags_and_baselines(self):
        network,cc=d.network(self.p,{})
        self.assertTrue(network['connected']);self.assertEqual(network['triangles'],1)
        self.assertAlmostEqual(network['dates'][-1]['bperp'],25)
        self.assertTrue(cc['sampled']);self.assertEqual(len(cc['pairs']),4)
        with h5py.File(self.p/'inputs/ifgramStack.h5','a') as f:f['dropIfgram'][-1]=False
        network,_=d.network(self.p,{})
        self.assertTrue(network['connected']);self.assertFalse(network['pairs'][-1]['kept'])
        self.assertEqual(network['retained_dates'],self.dates[:3])
        self.assertIsNone(network['dates'][-1]['bperp']);self.assertFalse(network['dates'][-1]['retained'])
    def test_no_triangles_is_not_zero_errors(self):
        self.assertEqual(d.closure(self.p,0)['status'],'skipped')
        self.assertEqual(d.closure(self.p,1)['status'],'unavailable')
        with h5py.File(self.p/'numTriNonzeroIntAmbiguity.h5','w') as f:f['mask']=np.array([[0,np.nan],[1,2]],dtype='f4')
        result=d.closure(self.p,3)
        self.assertEqual(result['status'],'available');self.assertEqual(sum(result['histogram']['counts']),3)
    def test_missing_residual_is_skipped_and_partial_rms_is_unavailable(self):
        self.assertEqual(d.rms(self.p,self.config,self.dates)['status'],'skipped')
        self.timeseries('timeseriesResidual.h5')
        self.assertEqual(d.rms(self.p,self.config,self.dates)['status'],'unavailable')
        (self.p/'rms_timeseriesResidual.txt').write_text('\n'.join(f'{date} {value}' for date,value in zip(self.dates,[.001,.002,.003,.1])))
        (self.p/'exclude_date.txt').write_text('20200401\n');(self.p/'reference_date.txt').write_text('20200101\n')
        result=d.rms(self.p,self.config,self.dates)
        self.assertEqual(result['status'],'available');self.assertEqual(result['rows'][-1]['rms_mm'],100)
        self.assertEqual(result['recommended_excluded_dates'],['20200401'])
    @unittest.skipUnless(importlib.util.find_spec('mintpy') is not None,'requires native MintPy')
    def test_native_rms_table_and_suggestions_are_read_without_applying(self):
        from mintpy.cli.timeseries_rms import main
        self.timeseries('timeseriesResidual.h5')
        with h5py.File(self.p/'timeseriesResidual.h5','a') as f:
            f['timeseries'][:]=np.asarray([.001,.002,.003,.2],dtype='f4')[:,None,None]*np.ones((4,3,4),dtype='f4')
        (self.p/'mintpy.cfg').write_text('mintpy.residualRMS.deramp = no\nmintpy.residualRMS.maskFile = maskTempCoh.h5\nmintpy.residualRMS.cutoff = 3\n')
        old=Path.cwd()
        try:
            os.chdir(self.p)
            with contextlib.redirect_stdout(io.StringIO()):main(['timeseriesResidual.h5','-t','mintpy.cfg'])
        finally:os.chdir(old)
        result=d.rms(self.p,self.config,self.dates)
        self.assertEqual(result['status'],'available');self.assertEqual(result['source'],'rms_timeseriesResidual.txt')
        self.assertEqual(result['recommended_excluded_dates'],['20200401'])
        self.assertEqual(result['recommended_reference_date'],'20200101')
        self.assertAlmostEqual(result['rows'][-1]['rms_mm'],200,places=3)
    def test_recommendations_do_not_become_current_decisions(self):
        self.timeseries()
        (self.p/'exclude_date.txt').write_text('20200401\n')
        self.data['review_initial_excluded_dates']=['20200201'];d.save(self.job,self.data)
        with patch.object(worker.runtime,'resolve_file',return_value=self.config):report=d.build(self.job,'residual')
        self.assertEqual(report['current_excluded_dates'],['20200201'])
        self.assertEqual(report['rms']['status'],'skipped')
        self.assertEqual(d.verify(self.p,report['report_id'])['dates'],self.dates)
    def test_spatial_comparison_is_bounded_same_reference_and_read_only(self):
        self.timeseries();self.timeseries('timeseries_trop.h5',offset=.1)
        self.data['parameter_report']['corrections']=[dict(step='correct_troposphere',state='applied',artifact='timeseries_trop.h5',label='對流層')];d.save(self.job,self.data)
        before=(self.p/'timeseries.h5').read_bytes();report=d.spatial(self.job,'20200301')
        self.assertEqual(len(report['stages']),2);self.assertEqual(report['unit'],'mm')
        self.assertEqual(report['stages'][0]['preview']['min'],report['stages'][1]['preview']['min'])
        self.assertEqual((self.p/'timeseries.h5').read_bytes(),before)
        with self.assertRaises(ValueError):d.spatial(self.job,'../../bad')
        with self.assertRaises(ValueError):d.spatial(self.job,'20210101')
    def test_invert_stops_before_unwrap_and_corrections_before_velocity(self):
        d.save(self.job,dict(self.data,parameter_report={}))
        calls=[]
        with patch.object(worker,'validate_reference',return_value=[0,0]),patch.object(worker.runtime,'validate_external_inputs'),patch.object(worker,'mintpy_steps',side_effect=lambda p,n:calls.extend(n)),patch.object(worker.diagnostics,'build'):
            worker.invert(self.job)
        self.assertEqual(calls,['reference_point','quick_overview'])
        data=json.loads(self.job.read_text());data['review_pending']={'report_id':'receipt'};d.save(self.job,data);calls=[]
        with patch.object(worker.runtime,'validate_external_inputs'),patch.object(worker.diagnostics,'verify'),patch.object(worker.diagnostics,'build'),patch.object(worker,'mintpy_steps',side_effect=lambda p,n:calls.extend(n)):
            worker.corrections(self.job)
        self.assertEqual(calls[-1],'residual_RMS');self.assertNotIn('velocity',calls);self.assertNotIn('reference_date',calls)

    def test_finalization_uses_explicit_choice_not_rms_suggestion_file(self):
        self.timeseries();(self.p/'exclude_date.txt').write_text('20200401\n')
        data=dict(self.data,parameter_report={})
        report=d.seal(self.p,dict(stage='residual',dates=self.dates))
        choice=d.decision(report,dict(reviewed=True,notes='檢查後保留所有日期',excluded_dates=[],reference_date='20200201'))
        data['review_pending']=choice;d.save(self.job,data)
        calls=[]
        def run_steps(path,names):
            calls.extend(names)
            cfg=worker.runtime.resolve_file(self.p/'mintpy.cfg')
            self.assertFalse(cfg['mintpy.timeFunc.excludeDate'])
            self.assertEqual(cfg['mintpy.reference.date'],'20200201')
            with h5py.File(self.p/'velocity.h5','w') as f:
                f['velocity']=np.ones((3,4),dtype='f4')*.001;f.attrs['REF_DATE']='20200201'
            with h5py.File(self.p/'temporalCoherence.h5','w') as f:f['temporalCoherence']=np.ones((3,4),dtype='f4')
        with patch.object(worker,'mintpy_steps',side_effect=run_steps),patch('mintpy.smallbaselineApp.TimeSeriesAnalysis') as cls:
            cls.return_value.get_timeseries_filename.return_value={'velocity':{'input':str(self.p/'timeseries.h5')}}
            worker.finalize(self.job)
        self.assertEqual(calls,['reference_date','velocity'])
        final=json.loads(self.job.read_text());self.assertEqual(final['review_velocity_excluded_dates'],[])
        self.assertEqual(final['options']['reference_date'],'200201')
        self.assertEqual((self.p/'exclude_date.txt').read_text(),'20200401\n')
        self.assertTrue((self.p/'results.json').is_file())

    def test_residual_report_dates_follow_actual_timeseries_after_network_drop(self):
        with h5py.File(self.p/'inputs/ifgramStack.h5','a') as f:f['dropIfgram'][-1]=False
        with h5py.File(self.p/'timeseries.h5','w') as f:
            f['date']=np.asarray(self.dates[:3],dtype='S8');f['timeseries']=np.zeros((3,3,4),dtype='f4')
        with patch.object(worker.runtime,'resolve_file',return_value=self.config):report=d.build(self.job,'residual')
        self.assertEqual(report['dates'],self.dates[:3]);self.assertTrue(report['network']['connected'])


@unittest.skipUnless(all(importlib.util.find_spec(name) is not None for name in ('isce','mintpy','h5py','numpy')),'requires full scientific environment')
class NativeCheckpointTests(unittest.TestCase):
    def test_real_mintpy_native_steps_pause_twice_and_preserve_sources(self):
        from test_legacy import dataset
        from alosstack_helper.legacy import create_wrapper,inspect_legacy
        from alosstack_helper.workflows import inventory,options
        from mintpy.utils import ptime
        old=Path.cwd()
        with tempfile.TemporaryDirectory(prefix='mintpy-checkpoints-') as tmp:
            p=Path(tmp)
            try:
                os.chdir(p);request,pairs=dataset(p,Path(sys.prefix));source=Path(request['source'])
                # Nonzero spatially varying phase with a known temporal model;
                # constant phase would become entirely invalid after referencing.
                y,x=np.mgrid[:16,:32]
                for pair in pairs:
                    # Match the explicit decimal-year coordinate used by the
                    # native time model; the known spatial coefficient remains
                    # independent of the inversion/velocity implementation.
                    a,b=ptime.yyyymmdd2years(['20'+v for v in pair.split('-')])
                    raw=np.ones((16,2,32),dtype='<f4');raw[:,1,:]=1+(b-a)*(x+y*.2)*.01
                    for f in (source/'pairs'/pair/'insar').glob('*.unw'):f.write_bytes(raw.tobytes())
                hashes=lambda:{str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in source.rglob('*') if f.is_file()}
                before=hashes();state=create_wrapper(request,inspect_legacy(request),'aabbccddeeff');inv=inventory(state)
                dest=Path(state['config']['root'])/'analyses/001';dest.mkdir(parents=True);path=dest/'analysis.json'
                job=dict(id='001',kind='mintpy',status='running',history=[],inventory=inv,options=options({},inv),environment=request['environment'],source_ion=state['ion'],source_ion_excluded=[])
                d.save(path,job)
                with patch.dict(os.environ,{'MPLCONFIGDIR':str(p/'mpl'),'XDG_CACHE_HOME':str(p/'cache')}),contextlib.redirect_stdout(io.StringIO()):
                    worker.load(path);worker.invert(path)
                    first=json.loads((dest/'diagnostics.json').read_text())
                    self.assertEqual(first['stage'],'unwrap');self.assertFalse((dest/'timeseries.h5').exists())
                    self.assertEqual(first['closure']['status'],'available')
                    choice=d.decision(first,dict(reviewed=True,notes='合成資料確認連通與閉合'))
                    job=json.loads(path.read_text());job['review_pending']=choice;d.save(path,job)
                    worker.corrections(path)
                    second=json.loads((dest/'diagnostics.json').read_text())
                    self.assertEqual(second['stage'],'residual');self.assertFalse((dest/'velocity.h5').exists())
                    self.assertEqual(second['rms']['status'],'skipped')
                    choice=d.decision(second,dict(reviewed=True,notes='合成資料保留所有日期',excluded_dates=[],reference_date=second['dates'][0]))
                    job=json.loads(path.read_text());job['review_pending']=choice;d.save(path,job)
                    worker.finalize(path)
                self.assertTrue((dest/'velocity.h5').is_file());self.assertTrue((dest/'results.json').is_file())
                with h5py.File(dest/'velocity.h5','r') as f:
                    np.testing.assert_allclose(f['velocity'][:],-.236/(4*np.pi)*(x+y*.2)*.01,atol=2e-7)
                self.assertEqual(before,hashes())
                final=json.loads(path.read_text());self.assertEqual(final['parameter_report']['time_model']['dates'],second['dates'])
            finally:os.chdir(old)


if __name__=='__main__':unittest.main()
