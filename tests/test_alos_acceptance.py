import cmath
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper import alos_acceptance as review
from alosstack_helper.engine import Engine
from alosstack_helper.core import digest
from test_workflow import CMD3


def raster(path,values,complex_data=False,bands=1,geo=False,w=4,h=3):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    values=values if isinstance(values,list) else [values]*(w*h*bands)
    if complex_data:
        raw=b''.join(struct.pack('<ff',complex(v).real,complex(v).imag) for v in values);kind='CFloat32';size=8
    else:raw=struct.pack('<'+'f'*len(values),*values);kind='Float32';size=4
    path.write_bytes(raw)
    coord=''.join(f'<component name="coordinate{i}"><property name="size"><value>{v}</value></property></component>' for i,v in [(1,w),(2,h)])
    Path(str(path)+'.xml').write_text(f'<imageFile><property name="number_bands"><value>{bands}</value></property>{coord}</imageFile>')
    content=f'<VRTDataset rasterXSize="{w}" rasterYSize="{h}">'+('<GeoTransform>120,.01,0,25,0,-.01</GeoTransform>' if geo else '')
    # Band sequential layout, distinct band offset; read supports BIL/BIP too.
    for band in range(bands):content+=f'<VRTRasterBand dataType="{kind}" band="{band+1}" subClass="VRTRawRasterBand"><SourceFilename relativeToVRT="1">{path.name}</SourceFilename><ImageOffset>{band*w*h*size}</ImageOffset><PixelOffset>{size}</PixelOffset><LineOffset>{w*size}</LineOffset><ByteOrder>LSB</ByteOrder></VRTRasterBand>'
    Path(str(path)+'.vrt').write_text(content+'</VRTDataset>')
    return path


class AcceptanceFixture:
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.proc=self.root/'proc';self.proc.mkdir();self.pair='150206-151113';self.s=dict(config={'root':str(self.root),'reference':'151113'},expected_pairs=[self.pair],hashes={},ion={'estimate':True,'apply':True},excluded=[])
        (self.proc/'cmd_3.sh').write_text(CMD3)
    def tearDown(self):self.tmp.cleanup()
    def ion(self):
        folder=self.proc/'pairs'/self.pair/'insar';suffix='_8rlks_12alks'
        raster(folder/f'diff_{self.pair}{suffix}_ori.int',cmath.exp(.7j),True)
        raster(folder/f'diff_{self.pair}{suffix}.int',cmath.exp(.4j),True)
        raster(self.proc/f'dates_ion/filt_ion_150206{suffix}.ion',.3)
        raster(self.proc/f'dates_ion/filt_ion_151113{suffix}.ion',0)
    def outputs(self):
        f=self.proc/'pairs'/self.pair/'insar';suffix='_8rlks_12alks'
        for name,value,bands in [(f'filt_{self.pair}{suffix}.unw.geo',2,2),(f'filt_{self.pair}{suffix}_msk.unw.geo',2,2),(f'{self.pair}{suffix}.cor.geo',.8,2)]:raster(f/name,value,bands=bands,geo=True)
        raster(self.proc/f'dates_resampled/151113/insar/151113{suffix}.los.geo',30,bands=2,geo=True)
    def approval(self,r):return dict(report_id=r['report_id'],reviewed=True,notes='已逐項人工判讀')

class AcceptanceTests(AcceptanceFixture,unittest.TestCase):
    def test_ion_actual_phase_relation_and_reference_zero_are_valid(self):
        self.ion();r=review.publish(self.s,self.proc,'ion');self.assertTrue(r['can_continue']);self.assertLess(r['rows'][0]['phase_relation_max_rad'],1e-6)
        review.approve(self.s,self.proc,'ion',self.approval(r));review.verified_approval(self.s,self.proc,'ion')
    def test_missing_original_and_incorrect_applied_phase_block(self):
        self.ion();f=self.proc/'pairs'/self.pair/'insar'/f'diff_{self.pair}_8rlks_12alks.int';raster(f,cmath.exp(.7j),True)
        r=review.publish(self.s,self.proc,'ion');self.assertFalse(r['can_continue']);self.assertIn('0.01',r['rows'][0]['errors'][0])
        with self.assertRaises(ValueError):review.approve(self.s,self.proc,'ion',self.approval(r))
        f.with_name(f.stem+'_ori.int').unlink();self.assertFalse(review.collect(self.s,self.proc,'ion')['can_continue'])
    def test_stale_pixel_even_preserved_size_mtime_blocks(self):
        self.ion();r=review.publish(self.s,self.proc,'ion');f=self.proc/'pairs'/self.pair/'insar'/f'diff_{self.pair}_8rlks_12alks.int';st=f.stat();data=f.read_bytes();f.write_bytes(struct.pack('<ff',1,0)+data[8:]);os.utime(f,ns=(st.st_atime_ns,st.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'證據已變更'):review.approve(self.s,self.proc,'ion',self.approval(r))
    def test_refresh_revokes_prior_approval_and_requires_current_report(self):
        self.ion();r=review.publish(self.s,self.proc,'ion');review.approve(self.s,self.proc,'ion',self.approval(r));review.publish(self.s,self.proc,'ion')
        self.assertNotIn('ion_correction_review_approval',self.s)
        with self.assertRaises(ValueError):review.verified_approval(self.s,self.proc,'ion')
        with self.assertRaises(ValueError):review.approve(self.s,self.proc,'ion',dict(report_id='old',reviewed=True,notes='已核對'))
        with self.assertRaises(ValueError):review.approve(self.s,self.proc,'ion',dict(report_id=r['report_id'],reviewed=False,notes='已核對'))
    def test_all_actual_cmd4_products_and_geo_grid(self):
        self.outputs();r=review.publish(self.s,self.proc,'outputs');self.assertTrue(r['can_continue']);self.assertEqual(set(r['rows'][0]['products']),{'unwrapped','masked','coherence'});self.assertEqual(r['geometry']['bands'],2)
        review.approve(self.s,self.proc,'outputs',self.approval(r))
    def test_missing_coherence_and_invalid_sidecar_prevent_acceptance(self):
        self.outputs();f=self.proc/'pairs'/self.pair/'insar'/f'{self.pair}_8rlks_12alks.cor.geo';f.unlink();r=review.publish(self.s,self.proc,'outputs');self.assertFalse(r['can_continue'])
        with self.assertRaises(ValueError):review.approve(self.s,self.proc,'outputs',self.approval(r))
        self.outputs();vrt=Path(str(f)+'.vrt');vrt.write_text(vrt.read_text().replace('rasterXSize="4"','rasterXSize="5"'));self.assertFalse(review.collect(self.s,self.proc,'outputs')['can_continue'])
    def test_external_vrt_and_symlink_are_never_opened(self):
        self.outputs();f=self.proc/'pairs'/self.pair/'insar'/f'{self.pair}_8rlks_12alks.cor.geo';v=Path(str(f)+'.vrt');v.write_text(v.read_text().replace(f.name,'../../../../secret'))
        r=review.collect(self.s,self.proc,'outputs');self.assertFalse(r['can_continue']);self.assertIn('不同影像',r['rows'][0]['errors'][0])
        self.outputs();f.unlink();f.symlink_to('/etc/hosts');self.assertFalse(review.collect(self.s,self.proc,'outputs')['can_continue'])
    def test_other_pairs_load_lazily_and_stale_lazy_file_is_rejected(self):
        self.outputs();first=self.pair;self.pair='150206-160219';self.outputs();self.s['expected_pairs'].append(self.pair)
        report=review.publish(self.s,self.proc,'outputs')
        self.assertIn('values',report['rows'][0]['products']['unwrapped']['preview'])
        self.assertNotIn('values',report['rows'][1]['products']['unwrapped']['preview'])
        data=review.preview_pair(self.s,self.proc,'outputs',self.pair,report['report_id'])
        self.assertIn('values',data['row']['products']['unwrapped']['preview'])
        (self.proc/'pairs'/self.pair/'insar'/f'filt_{self.pair}_8rlks_12alks.unw.geo').unlink()
        with self.assertRaisesRegex(ValueError,'已改變'):review.preview_pair(self.s,self.proc,'outputs',self.pair,report['report_id'])
    def test_large_inventory_reports_missing_products_without_losing_checkpoint(self):
        import datetime,itertools
        dates=[(datetime.date(2020,1,1)+datetime.timedelta(days=i)).strftime('%y%m%d') for i in range(30)]
        self.s['expected_pairs']=['-'.join(p) for p in itertools.combinations(dates,2)]
        report=review.collect(self.s,self.proc,'outputs')
        self.assertEqual(report['summary']['expected_pairs'],435)
        self.assertEqual(len(report['rows']),435)
        self.assertFalse(report['can_continue'])

    def test_no_source_writes(self):
        self.ion();before={str(p):p.read_bytes() for p in self.proc.rglob('*') if p.is_file()};review.collect(self.s,self.proc,'ion');self.assertEqual(before,{str(p):p.read_bytes() for p in self.proc.rglob('*') if p.is_file()})

class AcceptanceEngineTests(AcceptanceFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();(self.root/'.helper').mkdir();self.e=Engine(self.root/'registry');self.s.update(id='test',status='ion_correction_review',stage='',history=[],config={**self.s['config'],'workflow':'alosStack','env':'/env'})
        self.e.projects['test']=str(self.root);self.e.save(self.s)
    def test_cmd4_only_after_verified_ion_approval_then_stops_for_acceptance(self):
        self.ion();r=review.publish(self.s,self.proc,'ion');review.approve(self.s,self.proc,'ion',self.approval(r));self.e.save(self.s);calls=[]
        def shell(s,p):calls.append(p.name);self.outputs()
        self.e.shell=shell;self.e.worker(self.s,'ion_correction_continue');s=self.e.get('test');self.assertEqual(calls,['cmd_4.sh']);self.assertEqual(s['status'],'output_review');self.assertTrue(s['output_acceptance']['can_continue'])
        self.e.launch('test','output_accept',options=self.approval(s['output_acceptance']));self.assertEqual(self.e.get('test')['status'],'complete');self.assertEqual(calls,['cmd_4.sh'])
    def test_no_approval_or_modified_ion_does_not_run_cmd4(self):
        self.ion();review.publish(self.s,self.proc,'ion');calls=[];self.e.shell=lambda s,p:calls.append(p.name);self.e.worker(self.s,'ion_correction_continue');self.assertEqual(calls,[]);self.assertEqual(self.e.get('test')['status'],'failed')
    def test_cmd4_success_with_missing_products_remains_review_not_complete(self):
        self.e.shell=lambda s,p:None;self.e.finish(self.s,[self.pair]);self.assertEqual(self.s['status'],'output_review');self.assertFalse(self.s['output_acceptance']['can_continue'])
    def test_cmd4_failure_is_failed_not_accepted(self):
        self.ion();r=review.publish(self.s,self.proc,'ion');review.approve(self.s,self.proc,'ion',self.approval(r))
        self.e.shell=lambda s,p:(_ for _ in ()).throw(RuntimeError('failed cmd4'));self.e.worker(self.s,'ion_correction_continue');self.assertEqual(self.e.get('test')['status'],'failed');self.assertNotIn('output_acceptance',self.e.get('test'))

if __name__=='__main__':unittest.main()
