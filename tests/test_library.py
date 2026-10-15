import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from alosstack_helper.library import Library
from alosstack_helper.engine import Engine
from alosstack_helper.server import make_server
from alosstack_helper import library_worker as worker

try:
    import h5py
    import numpy as np
    import pyproj
    from PIL import Image
    import matplotlib
    SCIENCE=True
except ImportError:
    SCIENCE=False


@unittest.skipUnless(SCIENCE,'science environment required')
class LibraryScienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.path=self.root/'product.h5'
        self.layer='/science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/unwrappedPhase'
        with h5py.File(self.path,'w') as h:
            g=h.create_group('science/LSAR/identification')
            for k,v in {'productType':b'GUNW','referenceZeroDopplerStartTime':b'2026-07-21','secondaryZeroDopplerStartTime':b'2026-08-14'}.items():g[k]=v
            f=h.create_group('science/LSAR/GUNW/grids/frequencyA');f['listOfPolarizations']=[b'HH']
            p=f.create_group('unwrappedInterferogram/HH')
            p['xCoordinates']=np.arange(6)*100+500000.;p['yCoordinates']=3600000.-np.arange(4)*100
            p['projection']=np.uint32(32652)
            p['unwrappedPhase']=np.arange(24,dtype='float32').reshape(4,6)
            cc=np.ones((4,6),dtype='uint16');cc[0,0]=0;cc[0,1]=65535
            p['connectedComponents']=cc;p['connectedComponents'].attrs['_FillValue']=65535
            coh=np.full((4,6),.8,dtype='float32');coh[1,0]=.1;p['coherenceMagnitude']=coh
            w=f.create_group('wrappedInterferogram/HH')
            for name in ('xCoordinates','yCoordinates','projection'):w[name]=p[name]
            w['wrappedInterferogram']=np.full((4,6),1j,dtype='complex64')
        self.req=dict(path=str(self.path),stamp=worker.stamp(self.path),layer=self.layer,threshold=0)
    def tearDown(self):self.tmp.cleanup()
    def test_catalog_reads_metadata_and_layers(self):
        out=worker.nisar(self.path);self.assertEqual(out['type'],'GUNW');self.assertEqual(len(out['layers']),4)
    def test_preview_is_bounded_geographic_and_source_is_unchanged(self):
        before=self.path.read_bytes();out=worker.preview(self.req,self.root/'p.png')
        self.assertEqual(out['epsg'],32652);self.assertLessEqual(out['width'],900)
        self.assertTrue(31<out['bounds'][0][0]<34);self.assertTrue(128<out['bounds'][0][1]<131)
        self.assertEqual(self.path.read_bytes(),before)
        with Image.open(self.root/'p.png') as im:self.assertEqual(im.mode,'RGBA');self.assertIn(0,im.getchannel('A').getdata())
    def test_point_uses_original_grid_and_masks_components_and_coherence(self):
        from pyproj import Transformer
        transform=lambda src,dst,x,y:Transformer.from_crs(src,dst,always_xy=True).transform(x,y)
        def query(row,col,threshold=0):
            lon,lat=transform('EPSG:32652','EPSG:4326',[500000.+col*100],[3600000.-row*100])
            return worker.point(dict(self.req,lon=lon[0],lat=lat[0],threshold=threshold))
        self.assertEqual(query(2,3)['value'],15)
        self.assertIsNone(query(0,0)['value']);self.assertIsNone(query(0,1)['value'])
        self.assertIsNone(query(1,0,.5)['value']);self.assertEqual(query(1,0,0)['value'],6)
    def test_outside_has_no_stale_value(self):
        self.assertIsNone(worker.point(dict(self.req,lat=0,lon=0))['value'])
    def test_wrapped_complex_is_phase_not_amplitude(self):
        req=dict(self.req,layer=self.layer.replace('unwrappedInterferogram','wrappedInterferogram').replace('unwrappedPhase','wrappedInterferogram'))
        from pyproj import Transformer
        transform=lambda src,dst,x,y:Transformer.from_crs(src,dst,always_xy=True).transform(x,y)
        lon,lat=transform('EPSG:32652','EPSG:4326',[500200],[3599800])
        self.assertAlmostEqual(worker.point(dict(req,lon=lon[0],lat=lat[0]))['value'],np.pi/2,places=5)
    def test_missing_matching_coherence_rejects_threshold(self):
        req=dict(self.req,threshold=.5,layer=self.layer.replace('unwrappedInterferogram','wrappedInterferogram').replace('unwrappedPhase','wrappedInterferogram'))
        with self.assertRaisesRegex(ValueError,'相同網格'):worker.preview(req,self.root/'p.png')
    def test_changed_source_and_unknown_layer_rejected(self):
        with self.assertRaisesRegex(ValueError,'變動'):worker.preview(dict(self.req,stamp={}),self.root/'p.png')
        with self.assertRaisesRegex(ValueError,'圖層'):worker.preview(dict(self.req,layer='/science/LSAR/identification'),self.root/'p.png')
    def test_nonfinite_threshold_and_empty_mask_rejected(self):
        for value in (float('nan'),-1,2):
            with self.subTest(value=value),self.assertRaises(ValueError):worker.preview(dict(self.req,threshold=value),self.root/'p.png')
        with self.assertRaisesRegex(ValueError,'沒有有效'):worker.preview(dict(self.req,threshold=1),self.root/'p.png')
    def test_bad_grid_is_not_guessed(self):
        with h5py.File(self.path,'r+') as h:h[self.layer].parent['yCoordinates'][2]=1
        with self.assertRaisesRegex(ValueError,'等距'):worker.preview(dict(self.req,stamp=worker.stamp(self.path)),self.root/'p.png')
    def test_scan_keeps_valid_products_and_reports_broken_h5(self):
        (self.root/'broken.h5').write_bytes(b'broken');(self.root/'loop').symlink_to(self.root,target_is_directory=True)
        out=worker.scan(self.root);self.assertEqual(len(out['products']),1);self.assertEqual(len(out['errors']),1)


class LibraryTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.lib=Library(self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_bad_ids_and_arbitrary_paths_rejected(self):
        for key in ('../projects','/etc/passwd','abc'):
            with self.assertRaises(ValueError):self.lib.get(key)
        with self.assertRaises(ValueError):self.lib.scan(str(self.root),str(self.root/'noenv'))
    def test_saved_scan_preserves_selected_environment(self):
        with patch.object(self.lib,'call',return_value=dict(root='/data',products=[],configs=[],warnings=[],errors=[])):
            v=self.lib.scan('/data',str(self.root/'env'))
        self.assertEqual(self.lib.get(v['id'])['environment'],str((self.root/'env').resolve()))
        self.assertEqual(len(self.lib.listing()),1)
        with self.assertRaises(ValueError):self.lib.request(dict(scan=v['id'],product=-1,layer='fake'))
    def test_configs_detect_missing_reference_and_adjacent_strings(self):
        proc=self.root/'proc';proc.mkdir();(self.root/'data').mkdir()
        p=proc/'alosStack.xml';p.write_text('''<stack><property name="reference date of the stack">200423</property><property name="geocode file list">['one.unw'
        'two.unw']</property><property name="water body">/nonexistent/wbd</property></stack>''')
        v=worker.config_report(p);self.assertEqual(len(v['warnings']),3)
    def test_pair_metadata_is_not_a_processing_certificate(self):
        p=dict(type='RSLC',metadata=dict(trackNumber=24,frameNumber=18,orbitPassDirection='Ascending',zeroDopplerStartTime='a',polarizations={'A':['HH','HV']}))
        q=dict(type='RSLC',metadata=dict(p['metadata'],zeroDopplerStartTime='b',polarizations={'A':['HH']}))
        r=worker.pair_checks([p,q])[0];self.assertEqual(r['common'],{'A':['HH']});self.assertEqual(r['issues'],[]);self.assertIn('尚待驗證',r['note'])
        q['metadata']['trackNumber']=25;self.assertIn('相對軌道不同',worker.pair_checks([p,q])[0]['issues'])
    def test_offline_project_does_not_break_library_or_delete_registration(self):
        home=self.root/'registry';home.mkdir();registry=home/'projects.json'
        registry.write_text(json.dumps({'offline':str(self.root/'offline')}))
        before=registry.read_bytes();engine=Engine(home)
        self.assertEqual(engine.list(),[]);self.assertEqual(engine.unavailable()[0]['id'],'offline')
        self.assertEqual(registry.read_bytes(),before)

    def test_http_auth_and_static_entry(self):
        server=make_server(Engine(self.root/'home'),0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            page=urllib.request.urlopen(base).read().decode();import re
            token=re.search('name="api-token" content="([^"]+)"',page)[1]
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/library')
            req=urllib.request.Request(base+'/api/library',headers={'X-Token':token})
            self.assertEqual(json.load(urllib.request.urlopen(req)),{'scans':[]})
            self.assertIn(b'libViewer',urllib.request.urlopen(base+'/library.js').read())
            bad=urllib.request.Request(base+'/api/library-preview',data=json.dumps(dict(scan='../escape')).encode(),headers={'X-Token':token,'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(bad)
        finally:server.shutdown();thread.join();server.server_close()
