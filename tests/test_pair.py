import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
from alosstack_helper.engine import Engine
from alosstack_helper.core import atomic_json, digest
from alosstack_helper.pair import PairWorkflow, STEPS, normalize_options, write_pair_xml, import_pair_xml, bounds, fingerprint, app_path
from alosstack_helper.workflows import inventory

class PairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.e=Engine(self.base/'registry');self.p=PairWorkflow(self.e)
        env=self.base/'conda/envs/test';(env/'bin').mkdir(parents=True);(env/'bin/python').touch();(self.base/'conda/bin').mkdir();(self.base/'conda/bin/conda').touch()
        app=env/'lib/python3.12/site-packages/isce/applications/alos2App.py';app.parent.mkdir(parents=True);app.write_text('example')
        self.source=self.base/'raw';self.source.mkdir()
        for d in ('181103','190518','200516'):
            folder=self.source/d;folder.mkdir()
            for f in ('0480','0490'):
                for prefix in ('LED','IMG-HH'):(folder/f'{prefix}-ALOS200001{f}-{d}-FBDR1.1__A').write_bytes(b'source')
        self.dem=self.base/'a.dem';self.dem.write_bytes(b'00')
        Path(str(self.dem)+'.xml').write_text('<imageFile><property name="file_name"><value>old</value></property></imageFile>')
        self.c=dict(name='test',root=str(self.base/'project'),source=str(self.source),env=str(env),dem=str(self.dem),dem_geo=str(self.dem),wbd=str(self.dem),reference='181103',secondary='190518',workflow='alos2App',properties={'polarization':'HH'})
    def tearDown(self):self.tmp.cleanup()
    def test_create_two_dates_and_selected_frames_only(self):
        s=self.p.create(dict(self.c,frames=['0490']));self.assertEqual([d['date'] for d in s['dates']],['181103','190518']);self.assertEqual(s['config']['frames'],['0490'])
        raw=[x['path'] for x in s['source_fingerprint'] if '/raw/' in x['path']];self.assertEqual(len(raw),4);self.assertTrue(all('0490' in p for p in raw))
        props={p.get('name'):p.text for p in ET.parse(Path(s['config']['root'])/'proc/alos2App.xml').getroot().iter('property')}
        self.assertEqual(props['reference frames'],"['0490']");self.assertEqual(props['use GPU'],'False');self.assertNotIn('number of subsequent dates',props)
    def test_reject_same_reverse_dates_missing_pol_frame_and_mode(self):
        for changes in [dict(secondary='181103'),dict(reference='190518',secondary='181103'),dict(properties={'polarization':'VV'}),dict(frames=['9999']),dict(properties={'number of subsequent dates':'9'})]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):self.p.create(dict(self.c,**changes))
        self.assertFalse(Path(self.c['root']).exists())
        f=next((self.source/'181103').glob('LED*'));f.rename(f.with_name(f.name.replace('FBD','WBD')))
        with self.assertRaises(ValueError):self.p.create(self.c)
    def test_reject_duplicate_product_and_nonempty_destination(self):
        f=next((self.source/'181103').glob('LED*'));f.with_name(f.name.replace('00001','00002')).write_bytes(b'duplicate')
        with self.assertRaises(ValueError):self.p.create(self.c)
        root=Path(self.c['root']);root.mkdir();(root/'keep').write_text('keep')
        with self.assertRaises(ValueError):self.p.create(self.c)
        self.assertEqual((root/'keep').read_text(),'keep')
    def test_source_mutation_and_config_mutation_block_execution(self):
        s=self.p.create(self.c);self.p.verify(s)
        config=Path(s['config']['root'])/'proc/alos2App.xml';s['hashes']={'alos2App.xml':digest(config)};config.write_text('changed')
        with self.assertRaises(ValueError):self.p.verify(s)
        s['hashes']={};self.dem.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.p.verify(s)
    def test_invalid_numbers_including_nan_and_window_constraints(self):
        for props in [{'number of range looks 1':'1.5'},{'number of range looks ion':'0'},{'interferogram filter strength':'nan'},{'interferogram filter window size':'31'},{'interferogram filter step size':'100'},{'minimum window size for filtering ionosphere phase':'12'},{'apply ionospheric phase correction':'$(id)'}]:
            with self.subTest(props=props),self.assertRaises(ValueError):normalize_options(props)
        self.assertEqual(normalize_options({'number of range looks 1':'2'})['number of range looks 1'],'2')
        for b in ([25,24,121,122],[24,25,122,121],[24,25,121,float('nan')]):
            with self.assertRaises(ValueError):bounds(b)
    def test_environment_aliases_do_not_make_app_ambiguous(self):
        lib=Path(self.c['env'])/'lib';(lib/'python3.1').symlink_to('python3.12');self.assertTrue(app_path(self.c['env']).is_file())
    def test_ion_flag_import_and_review_config_preserve_original(self):
        s=self.p.create(self.c);c=s['config'];root=Path(c['root']);original=(root/'proc/alos2App.xml').read_bytes()
        write_pair_xml(c,root/'proc/review.xml',False)
        self.assertEqual((root/'proc/alos2App.xml').read_bytes(),original)
        d=import_pair_xml(root/'proc/review.xml');self.assertEqual(d['properties']['apply ionospheric phase correction'],'False')
        bad=root/'other.xml';bad.write_text('<stack><component name="stackinsar"/></stack>')
        with self.assertRaises(ValueError):import_pair_xml(bad)
    def test_manual_review_required_on_server(self):
        s=self.p.create(self.c);s.update(status='review',ion={'estimate':True,'apply':True})
        for data in ({},{'reviewed':True},{'reviewed':True,'apply_ion':'False'}):
            with self.assertRaises(ValueError):self.p.validate_action(s,'continue',data)
        self.p.validate_action(s,'continue',{'reviewed':True,'apply_ion':False});self.assertFalse(s['review_decision']['apply'])
    def mock_prepared(self,estimate=True):
        s=self.p.create(self.c);s.update(status='pair_ready',ion={'estimate':estimate,'apply':True},resolved={},prepared_inputs={k:s['config'][k] for k in ('dem','dem_geo','wbd')},pair_metadata={},completed_steps=[x[0] for x in STEPS[:3]])
        proc=Path(s['config']['root'])/'proc';(proc/'PICKLE').mkdir()
        for suf in ('','.xml'):(proc/'PICKLE'/('baseline'+suf)).write_bytes(b'checkpoint')
        s['checkpoint_hashes']={str(p.relative_to(proc)):digest(p) for p in (proc/'PICKLE').iterdir()};s['hashes']={'alos2App.xml':digest(proc/'alos2App.xml')};self.e.save(s)
        def fake_run(state,args,**kwargs):
            step=args[-1].removeprefix('--dostep=')
            for suf in ('','.xml'):(proc/'PICKLE'/(step+suf)).write_bytes(b'checkpoint')
        self.e.run=fake_run
        return s
    def test_stop_before_correction_then_honor_review_decision(self):
        s=self.mock_prepared();calls=[]
        def science(s,mode):
            calls.append(mode);meta=self.e.paths(s)[2];atomic_json(meta/('pair-ion.json' if mode=='ion' else 'pair-results.json'),{'artifacts':[]})
        self.p.science=science;self.p.worker(s,'run');self.assertEqual(s['status'],'review');self.assertEqual(s['completed_steps'][-1],'ion_filt');self.assertNotIn('ion_correct',s['completed_steps'])
        self.p.validate_action(s,'continue',{'reviewed':True,'apply_ion':False});self.p.worker(s,'continue')
        self.assertEqual(s['status'],'complete');self.assertFalse(s['ion']['apply']);self.assertEqual(calls,['ion','results'])
        d=import_pair_xml(self.e.paths(s)[1]/'alos2App.review.xml');self.assertEqual(d['properties']['apply ionospheric phase correction'],'False')
    def test_disabled_ion_completes_without_review(self):
        s=self.mock_prepared(False);calls=[]
        def science(s,mode):calls.append(mode);atomic_json(self.e.paths(s)[2]/'pair-results.json',{'artifacts':[]})
        self.p.science=science;self.p.worker(s,'run');self.assertEqual(s['status'],'complete');self.assertEqual(calls,['results'])
    def test_failed_step_never_advances_or_reruns(self):
        s=self.mock_prepared();before=list(s['completed_steps'])
        self.e.run=lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('crash'))
        with self.assertRaises(RuntimeError):self.p.step(s,*STEPS[3])
        self.assertEqual(s['completed_steps'],before)
        proc=self.e.paths(s)[1];(proc/'PICKLE/download_dem').write_text('partial')
        with self.assertRaises(ValueError):self.p.step(s,*STEPS[3])
    def test_artifact_whitelist_and_no_mintpy_for_pair(self):
        s=self.p.create(self.c);meta=self.e.paths(s)[2];out=meta/'pair-exports';out.mkdir();(out/'ok.png').write_bytes(b'PNG');s['pair_results']={'artifacts':['ok.png','link.png']};(out/'link.png').symlink_to(self.dem)
        self.assertEqual(self.p.artifact(s,'ok.png'),out/'ok.png')
        for n in ('../state.json','link.png','secret'):
            with self.assertRaises(ValueError):self.p.artifact(s,n)
        s['status']='complete'
        with self.assertRaisesRegex(ValueError,'多日期'):inventory(s)

if __name__=='__main__':unittest.main()
