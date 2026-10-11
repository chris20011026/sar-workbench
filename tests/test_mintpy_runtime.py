"""Scientific contracts against native MintPy; all outputs are temporary fixtures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/alosstack_helper'))
try:
    import numpy as np
    import h5py
    import mintpy_runtime as runtime
    import mintpy
except ImportError:
    runtime=None


@unittest.skipIf(runtime is None,'requires selected scientific environment with MintPy')
class RuntimeTests(unittest.TestCase):
    dates=['20151205','20170826','20181103','20190518','20200516','20220514']
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=Path(self.temp.name)
        self.job=self.p/'analysis.json'
        self.job.write_text(json.dumps({'options':{'retained_dates':self.dates,'mintpy_overrides':{}},'history':[]}))
        self.config={'mintpy.troposphericDelay.method':'no','mintpy.ionosphericDelay.method':'no',
                     'mintpy.solidEarthTides':'no','mintpy.topographicResidual':'no',
                     'mintpy.compute.cluster':'no','mintpy.timeFunc.polynomial':'1'}
    def tearDown(self):self.temp.cleanup()
    def resolved(self,**changes):
        return runtime.resolve_template({**self.config,**changes})
    def test_installed_auto_resolves_while_empty_baseline_stays_disabled(self):
        self.assertFalse(self.resolved()['mintpy.troposphericDelay.method'])
        self.assertEqual(self.resolved(**{'mintpy.troposphericDelay.method':'auto'})['mintpy.troposphericDelay.method'],'pyaps')
        runtime.preflight(self.job,self.config.copy())
        report=json.loads(self.job.read_text())['parameter_report']
        self.assertTrue(all(x['state']=='disabled' for x in report['corrections']))
        self.assertEqual(report['time_model']['residual_dof'],4)
    def test_network_does_not_silently_drop_a_requested_date(self):
        edges=[[self.dates[i],self.dates[i+1]] for i in range(5)]
        self.assertTrue(runtime.verify_network(edges,[True]*5,self.dates)['connected'])
        with self.assertRaisesRegex(ValueError,'孤立|不連通'):
            runtime.verify_network(edges,[True,True,True,True,False],self.dates)
        with self.assertRaisesRegex(ValueError,'不連通'):
            runtime.verify_network(edges,[True,True,False,True,True],self.dates)
    def test_nonlinear_design_and_degrees_are_checked_with_native_matrix(self):
        cfg=self.resolved(**{'mintpy.timeFunc.periodic':'1'})
        model=runtime.time_model(cfg,self.dates,self.p)
        self.assertTrue(model['nonlinear']);self.assertEqual(model['parameters'],4)
        self.assertIn('一次項係數',model['velocity_meaning'])
        with self.assertRaisesRegex(ValueError,'自由度'):
            runtime.time_model(cfg,self.dates[:4],self.p)
        with self.assertRaisesRegex(ValueError,'秩不足'):
            runtime.time_model(self.resolved(**{'mintpy.timeFunc.periodic':'1,1'}),self.dates,self.p)
        with self.assertRaisesRegex(ValueError,'bootstrap'):
            runtime.time_model(self.resolved(**{'mintpy.timeFunc.periodic':'1','mintpy.timeFunc.uncertaintyQuantification':'bootstrap'}),self.dates,self.p)
    def test_event_dates_and_dem_degrees_are_checked(self):
        with self.assertRaises(ValueError):
            runtime.time_model(self.resolved(**{'mintpy.timeFunc.stepDate':'20300101'}),self.dates,self.p)
        cfg=self.resolved(**{'mintpy.topographicResidual.polyOrder':'2'})
        with self.assertRaisesRegex(ValueError,'自由度'):
            runtime.time_model(cfg,self.dates[:4],self.p,dem=True)
    def test_external_mask_is_copied_and_fingerprint_change_is_detected(self):
        with tempfile.TemporaryDirectory() as external:
            source=Path(external)/'mask.h5'
            with h5py.File(source,'w') as f:
                f['mask']=np.ones((2,3),dtype='uint8');f.attrs.update(FILE_TYPE='mask',WIDTH='3',LENGTH='2')
            before=source.read_bytes()
            cfg=runtime.preflight(self.job,{**self.config,'mintpy.reference.maskFile':str(source)})
            copied=Path(cfg['mintpy.reference.maskFile'])
            self.assertNotEqual(copied,source);self.assertTrue(copied.is_file());self.assertEqual(source.read_bytes(),before)
            self.assertTrue(runtime.read_mask(str(copied),(2,3),self.p).all())
            with self.assertRaisesRegex(ValueError,'尺寸'):
                runtime.read_mask(str(copied),(3,3),self.p)
            source.write_bytes(before+b' ')
            with self.assertRaisesRegex(ValueError,'改變'):
                runtime.validate_external_inputs(self.job)
    def test_missing_weather_or_covariance_fails_without_silent_fallback(self):
        with self.assertRaisesRegex(ValueError,'共變異數'):
            runtime.validate_dependencies(self.resolved(**{'mintpy.timeFunc.uncertaintyQuantification':'covariance'}),self.dates,self.p)
        with patch.object(runtime,'dependency'):
            with self.assertRaisesRegex(ValueError,'GACOS.*缺少'):
                runtime.validate_dependencies(self.resolved(**{'mintpy.troposphericDelay.method':'gacos','mintpy.troposphericDelay.gacosDir':str(self.p)}),self.dates,self.p)
            with self.assertRaisesRegex(ValueError,'ERA5'):
                runtime.validate_dependencies(self.resolved(**{'mintpy.troposphericDelay.method':'pyaps','mintpy.troposphericDelay.weatherDir':str(self.p)}),self.dates,self.p)
    def test_requested_correction_not_claimed_applied(self):
        cfg={**self.config,'mintpy.solidEarthTides':'yes'}
        with patch.object(runtime,'dependency'):
            runtime.preflight(self.job,cfg)
        report=json.loads(self.job.read_text())['parameter_report']
        correction=next(c for c in report['corrections'] if c['step']=='correct_SET')
        self.assertEqual(correction['state'],'pending')
        (self.p/'mintpy.cfg').write_text('\n'.join(f'{k} = {v}' for k,v in cfg.items()))
        runtime.before_step(self.job,'correct_SET')
        runtime.after_step(self.job,'correct_SET',error=RuntimeError('fixture'))
        correction=next(c for c in json.loads(self.job.read_text())['parameter_report']['corrections'] if c['step']=='correct_SET')
        self.assertEqual(correction['state'],'failed')
    def test_dem_baseline_degeneracy_is_rejected_before_correction(self):
        cfg={**self.config,'mintpy.topographicResidual':'yes','mintpy.topographicResidual.polyOrder':'1'}
        runtime.preflight(self.job,cfg)
        (self.p/'mintpy.cfg').write_text('\n'.join(f'{k} = {v}' for k,v in cfg.items()))
        with h5py.File(self.p/'timeseries.h5','w') as f:
            f['date']=np.asarray(self.dates,dtype='S8');f['bperp']=np.zeros(len(self.dates))
        with self.assertRaisesRegex(ValueError,'秩不足'):
            runtime.before_step(self.job,'correct_topography')
    def test_covariance_dates_units_variance_and_reader_limit_are_validated(self):
        (self.p/'inputs').mkdir()
        shape=(2,3);cov=self.p/'cov.h5'
        with h5py.File(self.p/'inputs/geometryRadar.h5','w') as f:
            f['height']=np.ones(shape);f.attrs.update(FILE_TYPE='geometry',WIDTH='3',LENGTH='2')
        with h5py.File(cov,'w') as f:
            f['date']=np.asarray(self.dates,dtype='S8')
            f['timeseriesCov']=np.ones((len(self.dates),*shape))
            f.attrs.update(FILE_TYPE='timeseriesCov',WIDTH='3',LENGTH='2',UNIT='m^2')
        cfg={**self.config,'mintpy.timeFunc.uncertaintyQuantification':'covariance','mintpy.timeFunc.timeSeriesCovFile':str(cov)}
        runtime.preflight(self.job,cfg)
        (self.p/'mintpy.cfg').write_text('\n'.join(f'{k} = {v}' for k,v in cfg.items()))
        edges=[[self.dates[i],self.dates[i+1]] for i in range(5)]
        runtime.validate_loaded(self.job,edges,[True]*5,shape)
        with h5py.File(cov,'a') as f:f['timeseriesCov'][0,0,0]=-1
        with self.assertRaisesRegex(ValueError,'負值'):
            runtime.validate_loaded(self.job,edges,[True]*5,shape)
        with h5py.File(cov,'a') as f:
            del f['timeseriesCov'];f['timeseriesCov']=np.tile(np.eye(len(self.dates))[:,:,None,None],(1,1,*shape))
        with self.assertRaisesRegex(ValueError,'4D'):
            runtime.validate_loaded(self.job,edges,[True]*5,shape)
    def test_native_velocity_recovers_linear_plus_annual_and_exports_coefficient(self):
        from mintpy.cli.timeseries2velocity import main
        from mintpy.utils import time_func
        dates=['20190103','20190321','20190717','20191022','20200104','20200416','20200803','20201127','20210206','20210519','20210911','20211223']
        model={'polynomial':1,'periodic':[1.],'stepDate':[],'polyline':[],'exp':{},'log':{}}
        design=time_func.get_design_matrix4time_func(dates,model)
        truth=np.array([.01,.004,.003,-.002])
        series=(design@truth + np.sin(np.arange(len(dates))*1.7)*.000001).astype('float32')
        source=self.p/'synthetic.h5';output=self.p/'velocity.h5'
        with h5py.File(source,'w') as f:
            f['date']=np.asarray(dates,dtype='S8');f['timeseries']=np.tile(series[:,None,None],(1,2,2))
            f.attrs.update(FILE_TYPE='timeseries',LENGTH='2',WIDTH='2',UNIT='m',REF_DATE=dates[0],REF_Y='0',REF_X='0')
        main([str(source),'-o',str(output),'--poly','1','--periodic','1','--uq','residue'])
        with h5py.File(output,'r') as f:
            self.assertAlmostEqual(float(f['velocity'][1,1]),.004,places=5)
            self.assertTrue(np.isfinite(f['velocityStd'][:]).all())
            self.assertIn('annualAmplitude',f)
        covariance=self.p/'variance.h5'
        with h5py.File(covariance,'w') as f:
            f['variance']=np.ones((len(dates),2,2),dtype='float32')*.000004
            f['date']=np.asarray(dates,dtype='S8');f.attrs.update(FILE_TYPE='variance',WIDTH='2',LENGTH='2',UNIT='m^2')
        main([str(source),'-o',str(self.p/'velocity-cov.h5'),'--poly','1','--periodic','1','--uq','covariance','--ts-cov',str(covariance)])
        with h5py.File(self.p/'velocity-cov.h5','r') as f:
            self.assertAlmostEqual(float(f['velocity'][1,1]),.004,places=5)
            self.assertTrue((f['velocityStd'][:]>0).all())


if __name__=='__main__':unittest.main()
