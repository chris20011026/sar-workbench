"""Mission adapters: isolation, scientific input guards and lossless sample bytes."""
from pathlib import Path
import hashlib
import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.nisar_jobs import NisarJobs, normalize, stamps
from alosstack_helper.alos_compatibility import compatibility
from alosstack_helper.alos4_conversion import convert_img, inspect_img, product_files, verify_tool


class MissionParameters(unittest.TestCase):
    def test_coupled_parameters_and_strict_booleans(self):
        self.assertEqual(normalize({})['range_looks'], 11)
        self.assertEqual(normalize({'epsg':32652,'posting':30})['posting'],30)
        for bad in ({'threads':0}, {'threads':True}, {'range_looks':'nan'}, {'posting':30}, {'epsg':99999}, {'ionosphere':'false'}, {'ionosphere':True,'frequency':'B'}, {'polarization':'HV'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): normalize(bad)

    def test_output_is_not_created_by_check_and_inputs_cannot_be_changed(self):
        with tempfile.TemporaryDirectory() as d:
            base=Path(d); env=base/'env';(env/'bin').mkdir(parents=True);(env/'bin/python').write_bytes(b'python')
            paths=[]
            for k in ('reference','secondary','dem'):
                p=base/k;p.write_bytes(k.encode());paths.append(p)
            m=NisarJobs(base/'state')
            with patch('alosstack_helper.nisar_jobs.find_conda',return_value='/test/conda'),patch.object(m,'_launch'):
                s=m.create(dict(environment=str(env),root=str(base/'output'),reference=str(paths[0]),secondary=str(paths[1]),dem=str(paths[2])))
            self.assertFalse((base/'output').exists())
            cfg=m.folder(s['id'])/'insar.yaml';cfg.write_text('generated')
            s['config_sha256']=hashlib.sha256(cfg.read_bytes()).hexdigest()
            m.verify(s)
            paths[0].write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'輸入檔已改變'):m.verify(s)
            with self.assertRaisesRegex(ValueError,'先完成'):m.start(s['id'],True)

    def test_restart_never_marks_incomplete_work_complete(self):
        with tempfile.TemporaryDirectory() as d:
            key='a'*32;p=Path(d)/'nisar'/key;p.mkdir(parents=True)
            (p/'state.json').write_text(json.dumps(dict(id=key,created=0,status='running',config=dict(name='test',root='/tmp/never'))))
            m=NisarJobs(d);self.assertEqual(m.get(key)['status'],'interrupted')
            self.assertIsNone(m.active)

    def test_orphaned_process_blocks_new_work_without_killing_unrelated_pid(self):
        with tempfile.TemporaryDirectory() as d:
            key='b'*32;p=Path(d)/'nisar'/key;p.mkdir(parents=True)
            (p/'state.json').write_text(json.dumps(dict(id=key,pid=99999,created=0,status='running',config=dict(name='test',root='/tmp/never'))))
            with patch.object(NisarJobs,'_alive',return_value=True):
                m=NisarJobs(d);self.assertEqual(m.get(key)['status'],'orphaned');self.assertEqual(m.active,key)
                with patch.object(m,'_owns_process',return_value=False),patch('os.killpg') as kill:
                    with self.assertRaisesRegex(ValueError,'身分'):m.stop()
                    kill.assert_not_called()
            with patch.object(m,'_alive',return_value=False):
                m.reconcile();self.assertIsNone(m.active);self.assertEqual(m.get(key)['status'],'interrupted')

    def test_cross_satellite_frame_mismatch_and_rates_visible(self):
        products=[]
        for date,sensor,count,rate in [('20241017','ALOS-2',2,34.9305319),('20250425','ALOS-4',1,32.7473956)]:
            for _ in range(count):products.append(dict(type='CEOS',sensor=sensor,date=date,images=2,polarizations=['HH','HV'],metadata=dict(level='1.1',range_sampling_mhz=rate)))
        r=compatibility(dict(id='scan',products=products,configs=[],errors=[]))
        self.assertTrue(any(c['status']=='error' and c['title']=='影格對應' for c in r['checks']))
        self.assertTrue(any('Ampcor' in c['detail'] for c in r['checks']))
        self.assertEqual([d['count'] for d in r['dates']],[2,1])


def make_img(path,lines=3,pixels=8):
    record=800+pixels*8;desc=bytearray(b' '*720);desc[180:186]=str(lines).rjust(6).encode();desc[186:192]=str(record).rjust(6).encode()
    payload=[]
    with path.open('wb') as f:
        f.write(desc)
        for i in range(lines):
            prefix=bytearray(800);struct.pack_into('>I',prefix,8,record);struct.pack_into('>I',prefix,24,pixels)
            struct.pack_into('>I',prefix,116,85000123)
            data=struct.pack('>'+('f'*pixels*2),*[i+j/8 for j in range(pixels*2)])
            payload.append(data);f.write(prefix);f.write(data)
    return payload


class ConversionBytes(unittest.TestCase):
    def test_all_complex_samples_preserved_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'input';dst=Path(d)/'output';data=make_img(src);before=src.read_bytes()
            convert_img(src,dst)
            with dst.open('rb') as f:
                self.assertEqual(len(f.read(720)),720)
                for expected in data:
                    pre=f.read(544);self.assertEqual(struct.unpack_from('>I',pre,116)[0],850001)
                    self.assertEqual(f.read(64),expected)
                self.assertEqual(f.read(),b'')
            self.assertEqual(src.read_bytes(),before)
            with self.assertRaises(FileExistsError):convert_img(src,dst)

    def test_truncated_or_variable_record_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'input';make_img(src);src.write_bytes(src.read_bytes()[:-1])
            with self.assertRaisesRegex(ValueError,'截斷'):inspect_img(src)
            make_img(src)
            with src.open('r+b') as f:f.seek(720+864+8);f.write(struct.pack('>I',865))
            with self.assertRaisesRegex(ValueError,'第 2 行'):convert_img(src,Path(d)/'output')

    def test_unreviewed_tools_and_multiple_products_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError,'版本不符'):verify_tool(d)
            with self.assertRaisesRegex(ValueError,'一份'):product_files(d)


try:
    import h5py
    import numpy as np
    from osgeo import gdal, osr
    from alosstack_helper.nisar_worker import check_inputs
    SCIENCE=True
except ImportError: SCIENCE=False


@unittest.skipUnless(SCIENCE, 'science libraries required')
class NisarInputChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        for name,date in [('reference','2026-07-21T20:29:00'),('secondary','2026-08-14T20:28:58')]:
            with h5py.File(self.base/(name+'.h5'),'w') as h:
                group=h.create_group('science/LSAR/identification')
                for k,v in dict(productType='RSLC',orbitPassDirection='Ascending',trackNumber=24,frameNumber=18,zeroDopplerStartTime=date,boundingPolygon='POLYGON ((130 32,131 32,131 33,130 33,130 32))').items():group[k]=v
                for f in ('A','B'):h[f'science/LSAR/RSLC/swaths/frequency{f}/listOfPolarizations']=np.array(['HH'],dtype='S2')
        self.raster(self.base/'dem.tif')
        self.c=dict(reference=str(self.base/'reference.h5'),secondary=str(self.base/'secondary.h5'),dem=str(self.base/'dem.tif'),water='',root=str(self.base/'out'),options=normalize({}))

    def tearDown(self):self.temp.cleanup()

    def raster(self,path,north=34,water=False):
        ds=gdal.GetDriverByName('GTiff').Create(str(path),40,40,1,gdal.GDT_Float32)
        ds.SetGeoTransform((129,.1,0,north,0,-.1));s=osr.SpatialReference();s.ImportFromEPSG(4326);ds.SetProjection(s.ExportToWkt());ds.GetRasterBand(1).Fill(1 if water else 200);ds=None

    def test_common_footprint_and_dem_guard(self):
        report=check_inputs(self.c);self.assertEqual(report['overlap_bounds'],[130,32,131,33])
        self.raster(self.base/'small.tif',north=32.5);self.c['dem']=str(self.base/'small.tif')
        with self.assertRaisesRegex(ValueError,'未涵蓋'):check_inputs(self.c)

    def test_wrong_track_binary_mask_and_geographic_posting_rejected(self):
        self.raster(self.base/'water.tif',water=True);self.c['water']=str(self.base/'water.tif')
        with self.assertRaisesRegex(ValueError,'二元'):check_inputs(self.c)
        self.c['water']='';self.c['options']=normalize({'epsg':4326,'posting':30})
        with self.assertRaisesRegex(ValueError,'公尺'):check_inputs(self.c)
        self.c['options']=normalize({})
        with h5py.File(self.base/'secondary.h5','a') as h:h['science/LSAR/identification/trackNumber'][...]=25
        with self.assertRaisesRegex(ValueError,'相對軌道不同'):check_inputs(self.c)



class MissionAPI(unittest.TestCase):
    def setUp(self):
        import re, threading, urllib.request
        from alosstack_helper.engine import Engine
        from alosstack_helper.server import make_server
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'SAR_WORKBENCH_HOME':str(self.base/'workbench')});self.env.start()
        self.engine=Engine(str(self.base/'state'));self.server=make_server(self.engine,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.token=re.search(r'name="api-token" content="([^"]+)"',urllib.request.urlopen(self.url).read().decode()).group(1)
    def tearDown(self):
        self.server.nisar_jobs.stop();self.server.conversion_jobs.stop();self.server.shutdown();self.server.server_close();self.thread.join();self.env.stop();self.temp.cleanup()
    def request(self,path,data=None,headers=None):
        import urllib.request
        h={'X-Token':self.token,'Content-Type':'application/json'};h.update(headers or {})
        with urllib.request.urlopen(urllib.request.Request(self.url+path,headers=h,data=None if data is None else json.dumps(data).encode())) as response:return json.load(response)
    def test_authenticated_catalogue_and_shared_resource_gate(self):
        import urllib.error
        self.assertEqual(self.request('/api/nisar')['version'],'0.25.21')
        self.assertEqual(self.request('/api/alos4-conversion')['jobs'],[])
        for path in ('/api/nisar','/api/alos4-conversion'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(path,headers={'X-Token':''})
            self.assertEqual(e.exception.code,403)
        self.server.nisar_jobs.active='test'
        for path in ('/api/action','/api/setup-start','/api/analysis-create','/api/alos4-conversion-create'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(path,{})
            self.assertEqual(e.exception.code,400)
        self.server.nisar_jobs.active=None;self.engine.active='isce2'
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/nisar-create',{})
        self.engine.active=None
    def test_cross_origin_cannot_create_or_run_mission(self):
        import urllib.error
        for path in ('/api/nisar-create','/api/nisar-run','/api/alos4-conversion-run'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(path,{},headers={'Origin':'https://example.org'})
            self.assertEqual(e.exception.code,403)
        self.assertFalse((self.base/'state/nisar').exists())

    def test_workspace_endpoints_auth_and_environment_busy_guard(self):
        import urllib.error
        r=self.request('/api/workspace')
        self.assertEqual(r['records'],[])
        self.assertEqual(len(r['workflows']),8)
        self.assertIn('environments',self.request('/api/environment-status'))
        for path in ('/api/workspace','/api/environment-status'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(path,headers={'X-Token':''})
            self.assertEqual(e.exception.code,403)
        for path in ('/api/workspace-identify','/api/environment-check'):
            with self.assertRaises(urllib.error.HTTPError) as e:self.request(path,{},headers={'Origin':'https://example.org'})
            self.assertEqual(e.exception.code,403)
        self.engine.active='isce2'
        with patch('alosstack_helper.server.EnvironmentChecks.run') as check:
            with self.assertRaises(urllib.error.HTTPError):self.request('/api/environment-check',dict(path='/env'))
            check.assert_not_called()
        self.engine.active=None

    def test_project_removal_and_restore_are_authenticated_and_persistent(self):
        import urllib.error
        root = self.base / 'already-deleted'
        self.engine.register('project-id', root)
        for path in ('/api/project-remove', '/api/project-restore'):
            for headers in ({'X-Token':''}, {'Origin':'https://example.org'}):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    self.request(path, {'id':'project-id'}, headers=headers)
                self.assertEqual(e.exception.code,403)
        self.engine.active='project-id:analysis'
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request('/api/project-remove', {'id':'project-id'})
        self.assertEqual(e.exception.code,400)
        self.assertTrue(self.request('/api/workspace')['records'][0]['registration']['busy'])
        self.engine.active=None
        record=self.request('/api/project-remove', {'id':'project-id'})
        state=self.request('/api/workspace')
        self.assertEqual(state['records'],[])
        self.assertEqual(state['removed_projects'],[record])
        self.request('/api/project-restore', {'token':record['token']})
        state=self.request('/api/workspace')
        self.assertEqual(state['removed_projects'],[])
        self.assertEqual(state['records'][0]['status'],'unavailable')
        self.assertFalse(root.exists())


if __name__=='__main__':unittest.main()
