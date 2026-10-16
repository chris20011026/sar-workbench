"""Small complex SLCs exercise the actual ISCE2 resampler, not a mock kernel."""
import copy
import datetime
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from alosstack_helper import sampling_worker as worker
try:
    import isce
    import isceobj
    import numpy as np
    import isceobj.Sensor.MultiMode as MultiMode
except ImportError:
    isce = None


@unittest.skipUnless(isce is not None and os.environ.get('ALOSSTACK_TEST_ENV'), 'requires installed ISCE2')
class SamplingScienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.old=Path.cwd();os.chdir(self.root)
        self.source=self.root/'data';self.source.mkdir();self.dates=self.root/'dates';self.dates.mkdir()
        self.request=dict(argv=[os.environ['ALOSSTACK_TEST_ENV']+'/share/isce2/alosStack/read_data.py'],
                          source=str(self.source), dates=['240822','250425'],reference='250425',frames=['0480'])
        _,_,self.save_track,_=worker.libraries(self.request)

    def tearDown(self): os.chdir(self.old);self.tmp.cleanup()

    def make_date(self,date,rsr=30e6,prf=1200.,virtual=False):
        width,length=128,128
        folder=self.dates/date/'f1_0480/s1';folder.mkdir(parents=True)
        track=MultiMode.createTrack();track.configure();track.operationMode='FBD';track.radarWavelength=.24
        frame=MultiMode.createFrame();frame.configure();frame.frameNumber='0480'
        sw=MultiMode.createSwath();sw.configure();sw.swathNumber=1;sw.polarization='HH'
        sw.numberOfSamples=width;sw.numberOfLines=length;sw.startingRange=800000.
        sw.rangeSamplingRate=rsr;sw.rangePixelSize=299792458/(2*rsr);sw.rangeBandwidth=20e6
        sw.prf=prf;sw.azimuthPixelSize=7000/prf;sw.azimuthLineInterval=1/prf
        sw.sensingStart=datetime.datetime(2025,4,25)
        sw.dopplerVsPixel=[0.,0.,0.,0.];sw.azimuthFmrateVsPixel=[-2000.,.01,1e-5,1e-9]
        frame.swaths.append(sw);track.frames=[frame]
        with worker.at(self.dates/date):self.save_track(track,date)
        slc=folder/(date+'.slc')
        y,x=np.mgrid[:length,:width];values=np.exp(1j*(.04*x+.06*y)).astype('complex64')
        if virtual:
            raw=self.source/('IMG-'+date)
            with raw.open('wb') as f:
                f.write(b'\0'*720)
                for row in values:
                    f.write(b'\0'*544);f.write(row.astype('>c8').tobytes())
            worker.output_headers(slc,width,length)
            vrt=Path(str(slc)+'.vrt');tree=ET.parse(vrt);band=tree.getroot().find('VRTRasterBand')
            band.find('SourceFilename').text=str(raw);band.find('SourceFilename').set('relativeToVRT','0')
            band.find('ByteOrder').text='MSB';band.find('ImageOffset').text='1264';band.find('LineOffset').text=str(width*8+544)
            tree.write(vrt)
        else:
            values.tofile(slc);worker.output_headers(slc,width,length)
        return slc

    def prepare(self,secondary_virtual=True,rsr=60e6,prf=1800.):
        ref=self.make_date('250425',virtual=True)
        sec=self.make_date('240822',rsr=rsr,prf=prf,virtual=secondary_virtual)
        plan,_=worker.inspect(self.request)
        worker.write_json('helper_sampling_plan.json',plan)
        worker.write_json('helper_sampling_approval.json',dict(plan_id=plan['plan_id'],frames_verified=True,notes='synthetic fixture'))
        return plan,ref,sec

    def test_actual_resamp_accepts_ceos_padded_msb_virtual_and_preserves_phase(self):
        plan,ref,sec=self.prepare()
        before={p:worker.file_stamp(p,True) for p in self.source.iterdir()}
        self.assertFalse(sec.exists());self.assertEqual(plan['changed'],1)
        worker.apply(self.request);worker.verify(self.request)
        self.assertFalse(ref.exists());self.assertTrue(sec.is_file())
        output=np.fromfile(sec,dtype='complex64').reshape(85,64)
        y,x=np.mgrid[:85,:64];expected=np.exp(1j*(.04*2*x+.06*1.5*y))
        np.testing.assert_allclose(output[10:-10,10:-10],expected[10:-10,10:-10],atol=.015)
        self.assertEqual(before,{p:worker.file_stamp(p,True) for p in self.source.iterdir()})
        _,tracks=worker.inspect(self.request);sw=tracks['240822'].frames[0].swaths[0]
        self.assertAlmostEqual(sw.rangeSamplingRate,30e6);self.assertAlmostEqual(sw.prf,1200.)
        self.assertEqual(sw.azimuthFmrateVsPixel,[-2000.,.02,4e-5,8e-9])
        self.assertIsNone(sw.prfFraction)
        stamp=worker.file_stamp(sec);worker.apply(self.request);self.assertEqual(stamp,worker.file_stamp(sec))
        self.assertTrue(Path('helper_sampling/before/240822/f1_0480/s1/240822.slc.vrt').is_file())

    def test_physical_input_upsampling_and_backups(self):
        _,_,sec=self.prepare(secondary_virtual=False,rsr=20e6,prf=1000.)
        original=sec.read_bytes();worker.apply(self.request)
        self.assertEqual(sec.stat().st_size,192*154*8)
        self.assertEqual(Path('helper_sampling/before/240822/f1_0480/s1/240822.slc').read_bytes(),original)
        worker.verify(self.request)

    def test_equal_rates_virtual_remains_virtual(self):
        plan,ref,sec=self.prepare(rsr=30e6,prf=1200.)
        self.assertEqual(plan['changed'],0);worker.apply(self.request);worker.verify(self.request)
        self.assertFalse(sec.exists());self.assertFalse(ref.exists())

    def test_truncated_virtual_source_wrong_stride_and_changed_consent_block(self):
        _,_,sec=self.prepare()
        source=self.source/'IMG-240822';source.write_bytes(source.read_bytes()[:-500])
        with self.assertRaisesRegex(ValueError,'不完整'):worker.apply(self.request)
        self.assertFalse(Path('helper_sampling').exists())

    def test_metadata_change_after_review_and_after_success_blocks(self):
        _,_,sec=self.prepare()
        xml=Path(str(sec)+'.xml');xml.write_text(xml.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'已改變'):worker.apply(self.request)
        # Renew the explicit approval only for this test fixture.
        plan,_=worker.inspect(self.request);worker.write_json('helper_sampling_plan.json',plan)
        worker.write_json('helper_sampling_approval.json',dict(plan_id=plan['plan_id'],frames_verified=True,notes='new fixture'))
        worker.apply(self.request)
        xml.write_text(xml.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'已變更'):worker.verify(self.request)

    def test_interrupted_publish_never_allows_matching(self):
        _,_,sec=self.prepare(secondary_virtual=False)
        original=sec.read_bytes();rename=Path.replace
        def interrupted(path,dest):
            if 'staged' in str(path) and str(dest).endswith('.slc'): raise OSError('simulated unplug')
            return rename(path,dest)
        with patch.object(Path,'replace',interrupted),self.assertRaises(OSError):worker.apply(self.request)
        with self.assertRaisesRegex(ValueError,'尚未完整'):worker.verify(self.request)
        with self.assertRaisesRegex(ValueError,'未完成'):worker.apply(self.request)
        self.assertEqual(Path('helper_sampling/before/240822/f1_0480/s1/240822.slc').read_bytes(),original)

    def test_recovery_restores_sampling_checkpoint_before_rerunning(self):
        from alosstack_helper.engine import Engine
        from alosstack_helper.recovery import Recovery
        from alosstack_helper.sampling import APPLY_PHASE
        self.prepare()
        project=self.root/'project';proc=project/'proc';meta=project/'.helper'
        proc.mkdir(parents=True);meta.mkdir()
        self.dates.rename(proc/'dates');self.dates=proc/'dates'
        for name in ('helper_sampling_plan.json','helper_sampling_approval.json'):(self.root/name).rename(proc/name)
        os.chdir(proc)
        engine=Engine(self.root/'registry')
        state=dict(id='recover-sampling',config=dict(root=str(project),env=os.environ['ALOSSTACK_TEST_ENV'],
                   source=str(self.source),data=str(self.source)),status='running',stage=APPLY_PHASE,
                   history=[],hashes={},recovery_enabled=True)
        engine.projects[state['id']]=str(project);engine.save(state)
        recovery=Recovery(engine,state);recovery.capture(APPLY_PHASE,'run')
        original=Path.replace
        def interrupted(path,dest):
            if 'staged' in str(path) and str(dest).endswith('.slc.xml'): raise OSError('simulated unplug')
            return original(path,dest)
        with patch.object(Path,'replace',interrupted),self.assertRaises(OSError):worker.apply(self.request)
        state.update(status='failed',failed_action='run',failed_phase=APPLY_PHASE,error='simulated unplug')
        engine.save(state)
        self.assertEqual(recovery.restore(state['recovery']['id']),('run',APPLY_PHASE))
        os.chdir(proc)  # Engine launches a new subprocess in the restored proc.
        worker.apply(self.request);worker.verify(self.request)
        self.assertTrue(Path(state['recovery_attempt'],'failed-proc/helper_sampling/journal.json').is_file())

    def test_output_validation_rejects_nans_and_all_zero(self):
        p=self.root/'bad.slc'
        for value in (0j,complex(float('nan'),0)):
            np.full(64,value,dtype='complex64').tofile(p)
            with self.assertRaises(ValueError):worker.validate_pixels(p)

    def test_reader_fallback_is_process_local_and_retains_known_rates(self):
        from types import SimpleNamespace
        from isceobj.Sensor.MultiMode.ALOS2 import ALOS2
        table=dict(ALOS2.fsampConst)
        fake=lambda self:(None,SimpleNamespace(metadata={'Range sampling rate in MHz':self.test_rate}))
        with patch.object(ALOS2,'readLeader',fake):
            cls,original=worker.compatible_reader()
            try:
                a=MultiMode.createALOS2();a.test_rate=98.2421869;a.readLeader()
                self.assertAlmostEqual(a.fsampConst[98],98242186.9)
                b=MultiMode.createALOS2();b.test_rate=next(iter(table))+.1;b.readLeader()
                self.assertEqual(b.fsampConst,table)
                self.assertEqual(ALOS2.fsampConst,table)
            finally:cls.readLeader=original

    def test_virtual_stride_source_and_reference_frame_mismatch_fail_closed(self):
        _,_,sec=self.prepare()
        vrt=Path(str(sec)+'.vrt');original=vrt.read_bytes();tree=ET.parse(vrt)
        tree.getroot().find('.//PixelOffset').text='16';tree.write(vrt)
        with self.assertRaisesRegex(ValueError,'偏移'):worker.inspect(self.request)
        vrt.write_bytes(original)
        (self.dates/'240822/f1_0480').rename(self.dates/'240822/f1_0490')
        with self.assertRaisesRegex(ValueError,'影格順序'):worker.inspect(self.request)

    def test_polynomial_transform_is_exact_and_not_a_fit(self):
        old=[4.,3.,2.,1.];x=np.linspace(0,9999,301)
        for ratio in (.5,1.5):
            np.testing.assert_allclose(np.polyval(worker.scale_polynomial(old,ratio)[::-1],x),np.polyval(old[::-1],x*ratio),rtol=1e-12)


if __name__=='__main__':unittest.main()
