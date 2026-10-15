import json,struct,tempfile,unittest,threading,urllib.request,urllib.error,re
from pathlib import Path
from unittest.mock import patch
from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.workflows import inventory,options,network,raster_size
from alosstack_helper.analysis import Analyses,fingerprint
from alosstack_helper.server import make_server


def raster(path,bands=1,byte=False):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);size=1 if byte else 4
    p.write_bytes((b'\x01' if byte else struct.pack('<f',1))*6*bands)
    Path(str(p)+'.xml').write_text('<imageFile/>')
    Path(str(p)+'.vrt').write_text('<VRTDataset rasterXSize="3" rasterYSize="2">'+''.join(f'<VRTRasterBand dataType="{"Byte" if byte else "Float32"}" subClass="VRTRawRasterBand"><SourceFilename relativeToVRT="1">{p.name}</SourceFilename><ImageOffset>{i*3*size}</ImageOffset><PixelOffset>{size}</PixelOffset><LineOffset>{3*size*bands}</LineOffset></VRTRasterBand>' for i in range(bands))+'</VRTDataset>')

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.root=self.p/'project';self.root.mkdir();self.proc=self.root/'proc';self.proc.mkdir();self.env=self.p/'env';(self.env/'bin').mkdir(parents=True);(self.env/'bin/python').touch();(self.env/'bin/conda').touch()
        self.s=dict(id='test',config=dict(root=str(self.root),env=str(self.env),conda=str(self.env/'bin/conda'),reference='181103'),status='complete',expected_pairs=['170826-181103','181103-190518','170826-190518'])
        suffix='_8rlks_16alks'
        for pair in self.s['expected_pairs']:
            base=self.proc/'pairs'/pair/'insar'
            for name,b in [('filt_'+pair+suffix+'.unw',2),('filt_'+pair+suffix+'_msk.unw',2),(pair+suffix+'.cor',2),('filt_'+pair+suffix+'.unw.conncomp',1)]:raster(base/name,b,byte=name.endswith('conncomp'))
        geom=self.proc/'dates_resampled/181103/insar'
        for ext in ['hgt','lat','lon','los','wbd']:raster(geom/('181103'+suffix+'.'+ext))
        (geom.parent/'181103.track.xml').write_text('<track/>');(geom.parent/'f1_0480').mkdir();(geom.parent/'f1_0480/181103.frame.xml').write_text('<frame/>');(self.proc/'baseline').mkdir();(self.proc/'baseline/baseline_center.txt').write_text('baseline')
        self.e=Engine(self.p/'registry');self.e.save(self.s);self.e.register('test',self.root);self.a=Analyses(self.e)
    def tearDown(self):self.tmp.cleanup()
    def test_inventory_and_source_dimensions(self):
        inv=inventory(self.s);self.assertEqual((inv['width'],inv['height']),(3,2));self.assertEqual(len(inv['pairs']),3)
    def test_truncation_rejected_before_launch(self):
        f=next(self.proc.glob('pairs/*/insar/*.cor'));f.write_bytes(b'x')
        with self.assertRaises(ValueError):inventory(self.s)
    def test_missing_metadata_is_not_silently_ignored(self):
        next(self.proc.glob('pairs/*/insar/*.unw.xml')).unlink()
        with self.assertRaises(ValueError):inventory(self.s)
    def test_different_looks_are_not_mixed(self):
        f=next(f for f in self.proc.glob('pairs/*/insar/filt_*.unw') if f.name.endswith('alks.unw'));raster(f.with_name(f.name.replace('_8rlks','_4rlks')),2)
        with self.assertRaises(ValueError):inventory(self.s)
    def test_science_network_exclusions_are_independent(self):
        self.s['excluded']=['170826-181103'];inv=inventory(self.s);o=options({},inv);self.assertEqual(o['excluded'],[])
        with self.assertRaises(ValueError):options({'excluded':['170826-181103','170826-190518']},inv)
    def test_out_of_bounds_subset_and_nan_rejected(self):
        for d in [{'subset':[0,9,0,3]},{'memory':float('nan')},{'reference_date':'000000'},{'deramp':'shell;evil'}]:
            with self.subTest(d=d),self.assertRaises(ValueError):options(d,inventory(self.s))
    def test_pending_original_processing_rejected(self):
        self.s['status']='running'
        with self.assertRaises(ValueError):inventory(self.s)
    def test_geographic_bounds_and_advanced_settings_validation(self):
        inv=inventory(self.s)
        for d in [{'subset_lalo':[25,24,121,122]},{'subset_lalo':[24,25,121,float('nan')]},{'subset_lalo':[24,25,121,122],'subset':[0,1,0,1]},{'weight':'unknown'},{'redundancy':float('inf')},{'dem_error':True,'dem_order':2},{'excluded_dates':['181103']},{'name':'x'*101}]:
            with self.subTest(d=d),self.assertRaises(ValueError):options(d,inv)
        o=options({'subset_lalo':[24,25,121,122],'weight':'coh','redundancy':1.5},inv)
        self.assertEqual(o['weight'],'coh');self.assertEqual(o['subset_lalo'],[24,25,121,122])
    def test_mintpy_override_settings_are_persisted_per_run(self):
        raw={'mintpy.unwrapError.method':'phase_closure','mintpy.networkInversion.minNumPixel':12}
        with patch('threading.Thread.start'):
            job=self.a.create('test','mintpy',{'mintpy_overrides':raw})
        saved=self.a.get('test',job['id'])['options']['mintpy_overrides']
        self.assertEqual(saved['mintpy.unwrapError.method'],'phase_closure')
        self.assertEqual(str(saved['mintpy.networkInversion.minNumPixel']),'12')
        raw['mintpy.unwrapError.method']='no'
        self.assertEqual(self.a.get('test',job['id'])['options']['mintpy_overrides']['mintpy.unwrapError.method'],'phase_closure')
        self.assertEqual(self.e.get('test')['status'],'complete')
    def test_protected_or_injected_mintpy_overrides_fail_before_creating_run(self):
        for raw in [{'mintpy.load.processor':'snap'}, {'mintpy.reference.date':'20200101'},
                    {'mintpy.timeFunc.periodic':'1\nmintpy.load.processor = snap'},
                    {'mintpy.unknown':'auto'}, {'mintpy.networkInversion.minNumPixel':float('nan')}]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                self.a.create('test','mintpy',{'mintpy_overrides':raw})
        self.assertFalse(self.a.base('test').exists())
        self.assertIsNone(self.e.active)
    def test_missing_overrides_keep_existing_defaults(self):
        result=options({},inventory(self.s))
        self.assertEqual(result['mintpy_overrides'],{})
        self.assertEqual(result['deramp'],'no');self.assertFalse(result['dem_error'])
    def test_scientific_error_is_visible_without_reading_full_traceback(self):
        from alosstack_helper.analysis import failure_detail
        p=self.p/'science.log';p.write_text('Traceback\nValueError: 日期數不足以估計季節模型\nERROR conda failed\n')
        self.assertEqual(failure_detail(p),'日期數不足以估計季節模型')
    def test_date_exclusion_recomputes_required_network_and_reference(self):
        inv=inventory(self.s);inv['pairs'].extend([{'pair':'190518-200516'},{'pair':'181103-200516'}]);inv['network']=network([p['pair'] for p in inv['pairs']])
        o=options({'excluded_dates':['170826']},inv)
        self.assertEqual(o['retained_dates'],['181103','190518','200516']);self.assertTrue(all('170826' not in p for p in o['used']))
        with self.assertRaisesRegex(ValueError,'參考日期'):options({'excluded_dates':['181103']},inv)
        with self.assertRaisesRegex(ValueError,'不連通'):options({'excluded_dates':['170826'],'excluded':['190518-200516','181103-200516']},inv)
    def test_geographic_path_and_rename_guards(self):
        with self.assertRaises(ValueError):self.a.survey_artifact('test','../../state.json')
        with patch('threading.Thread.start'):j=self.a.create('test','mintpy',{})
        with self.assertRaises(ValueError):self.a.rename('test',j['id'],'執行中不改名')
        j['status']='complete';atomic_json(self.a.folder('test',j['id'])/'analysis.json',j)
        before=(self.root/'.helper/state.json').read_bytes()
        self.a.rename('test',j['id'],'新的分析名稱')
        self.assertEqual(self.a.get('test',j['id'])['options']['name'],'新的分析名稱');self.assertEqual(before,(self.root/'.helper/state.json').read_bytes())
    def test_analysis_is_independent_and_cannot_overlap_jobs(self):
        before=(self.root/'.helper/state.json').read_bytes()
        with patch('threading.Thread.start'):
            job=self.a.create('test','mintpy',{})
        self.assertEqual(before,(self.root/'.helper/state.json').read_bytes());self.assertTrue(self.e.active)
        with self.assertRaises(ValueError):self.a.create('test','quality',{})
        self.assertNotEqual(job['id'],'test')
    def test_changed_sources_block_inversion(self):
        with patch('threading.Thread.start'):j=self.a.create('test','mintpy',{})
        self.e.active=None;j['status']='loaded';atomic_json(self.a.folder('test',j['id'])/'analysis.json',j)
        (self.proc/'baseline/baseline_center.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError,'改變'):self.a.invert('test',j['id'],{'reviewed':True})
    def test_no_confirmation_or_wrong_stage_blocks_inversion(self):
        with patch('threading.Thread.start'):j=self.a.create('test','mintpy',{})
        self.e.active=None
        with self.assertRaises(ValueError):self.a.invert('test',j['id'],{'reviewed':True})
        j['status']='loaded';atomic_json(self.a.folder('test',j['id'])/'analysis.json',j)
        with self.assertRaises(ValueError):self.a.invert('test',j['id'],{'reviewed':False})
    def test_invalid_reference_preserves_loaded_checkpoint(self):
        import subprocess
        with patch('threading.Thread.start'):j=self.a.create('test','mintpy',{})
        self.e.active=None;j['status']='loaded';atomic_json(self.a.folder('test',j['id'])/'analysis.json',j)
        with patch('alosstack_helper.analysis.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','ValueError: 參考點位於水域')):
            with self.assertRaisesRegex(ValueError,'水域'):self.a.invert('test',j['id'],{'reviewed':True,'reference_pixel':[0,0]})
        self.assertEqual(self.a.get('test',j['id'])['status'],'loaded');self.assertIsNone(self.e.active)
    def test_restart_marks_abandoned_run_without_touching_original(self):
        with patch('threading.Thread.start'):j=self.a.create('test','quality',{})
        self.e.active=None;self.a.reconcile()
        self.assertEqual(self.a.get('test',j['id'])['status'],'failed');self.assertEqual(self.e.get('test')['status'],'complete')
    def test_traversal_and_symlink_exports_rejected(self):
        with self.assertRaises(ValueError):self.a.folder('test','../proc')
        with patch('threading.Thread.start'):j=self.a.create('test','quality',{})
        d=self.a.folder('test',j['id'])/'exports';d.mkdir();(d/'secret.png').symlink_to(self.proc/'baseline/baseline_center.txt')
        with self.assertRaises(ValueError):self.a.artifact('test',j['id'],'secret.png')
        with self.assertRaises(ValueError):self.a.artifact('test',j['id'],'../analysis.json')
    def test_failed_subprocess_preserves_project_and_run(self):
        with patch('threading.Thread.start'):j=self.a.create('test','quality',{})
        class Failed:
            pid=123
            def wait(self):return 9
            def poll(self):return 9
        with patch('alosstack_helper.analysis.subprocess.Popen',return_value=Failed()):self.a.worker('test',j['id'],'quality')
        self.assertEqual(self.a.get('test',j['id'])['status'],'failed');self.assertEqual(self.e.get('test')['status'],'complete');self.assertIsNone(self.e.active)
    def test_unsupported_workflow_rejected(self):
        with self.assertRaisesRegex(ValueError,'尚未開放'):self.e.create({'workflow':'topsStack'})
    def test_disconnected_analysis_preserves_original_completion_and_releases_lock(self):
        with patch('threading.Thread.start'):j=self.a.create('test','quality',{})
        root=self.root;away=self.p/'disconnected'
        class Disconnected:
            pid=123
            def wait(self):
                root.rename(away)
                raise OSError('device disconnected')
            def poll(self):return 1
        with patch('alosstack_helper.analysis.subprocess.Popen',return_value=Disconnected()):self.a.worker('test',j['id'],'quality')
        self.assertIsNone(self.e.active);self.assertFalse(root.exists())
        away.rename(root)
        self.assertEqual(self.e.get('test')['status'],'complete')
        self.assertEqual(self.a.get('test',j['id'])['status'],'failed')
        self.assertEqual(self.a.listing('test')[0]['status'],'failed')
        self.assertIn('不會自動續跑',self.a.get('test',j['id'])['error'])
    def test_project_output_alias_keeps_log_authentication_and_content(self):
        (self.root/'.helper/run.log').write_text('已完成，原成果保留。\n')
        server=make_server(self.e,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            page=urllib.request.urlopen(base).read().decode();token=re.search('name="api-token" content="([^"]+)"',page)[1]
            for route in ('/api/project-output','/api/log'):
                with self.subTest(route=route):
                    with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(base+route+'?id=test')
                    self.assertEqual(denied.exception.code,403)
                    request=urllib.request.Request(base+route+'?id=test',headers={'X-Token':token})
                    self.assertEqual(json.load(urllib.request.urlopen(request)),{'text':'已完成，原成果保留。\n'})
        finally:server.shutdown();server.server_close();thread.join()
    def test_api_requires_token_before_accessing_results(self):
        server=make_server(self.e,0);t=threading.Thread(target=server.serve_forever,daemon=True);t.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(base+'/api/analyses?id=test')
            self.assertEqual(caught.exception.code,403)
            page=urllib.request.urlopen(base).read().decode();token=re.search('name="api-token" content="([^"]+)"',page)[1]
            req=urllib.request.Request(base+'/api/analyses?id=test',headers={'X-Token':token});self.assertEqual(json.load(urllib.request.urlopen(req)),{'runs':[]})
            with self.assertRaises(urllib.error.HTTPError) as denied:urllib.request.urlopen(base+'/api/mintpy-schema')
            self.assertEqual(denied.exception.code,403)
            req=urllib.request.Request(base+'/api/mintpy-schema',headers={'X-Token':token})
            schema=json.load(urllib.request.urlopen(req));self.assertEqual(len(schema['options']),121)
            self.assertEqual(len({r['key'] for r in schema['options']}),121)
        finally:server.shutdown();server.server_close();t.join()

if __name__=='__main__':unittest.main()
