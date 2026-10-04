import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from alosstack_helper.core import (parse_cmd3, split_pre_review, validate_network, write_xml,
                                   read_template, atomic_json, digest)
from alosstack_helper.engine import Engine
from alosstack_helper.server import make_server

CMD3 = '''#!/bin/bash
echo precheck
/env/alosStack/ion_check.py -idir pairs_ion -odir fig_ion
##################################################
# estimate ionospheric phase for each date
##################################################
#/env/alosStack/ion_ls.py -idir pairs_ion -odir dates_ion -ref_date_stack 151113 -nrlks1 2 -nalks1 3 -nrlks2 4 -nalks2 4 -nrlks_ion 32 -nalks_ion 32 -interp
# correct ionosphere
#/env/alosStack/ion_correct.py -ion_dir ../../dates_ion
'''

class CoreTests(unittest.TestCase):
    def test_all_nine_common_properties_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'alosStack.xml'
            prefix='/data/sar/example_stack'
            cfg=dict(data=prefix+'/data',dem=prefix+'/dem/a.dem.wgs84',dem_geo=prefix+'/dem/b.dem.wgs84',wbd=prefix+'/wbd/a.wbd',reference='181103',properties={'polarization':'HH','number of subsequent dates':'9','do ionospheric phase estimation':'True','apply ionospheric phase correction':'True'})
            write_xml(cfg,p);out=read_template(p)
            expected={'data directory':cfg['data'],'dem for coregistration':cfg['dem'],'dem for geocoding':cfg['dem_geo'],'water body':cfg['wbd'],'reference date of the stack':'181103',**cfg['properties']}
            self.assertEqual(len(expected),9)
            for key,value in expected.items():self.assertEqual(out[key],value)
            for pol in ['HH','HV','VH','VV']:
                cfg['properties']['polarization']=pol;write_xml(cfg,p)
                self.assertEqual(read_template(p)['polarization'],pol)
            cfg['properties']['polarization']='';write_xml(cfg,p)
            self.assertNotIn('polarization',read_template(p))
    def test_omitted_and_disabled_ion_settings_survive_xml(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'source.xml';dest=Path(d)/'dest.xml'
            source.write_text('''<stack><component name="stackinsar">
            <property name="number of subsequent dates">9</property>
            <!-- <property name="number of subsequent dates for estimating ionosphere">4</property> -->
            <property name="do ionospheric phase estimation">False</property>
            <property name="apply ionospheric phase correction">False</property>
            </component></stack>''')
            props=read_template(source)
            cfg=dict(data='/data',dem='/dem',dem_geo='/geo',wbd='/wbd',reference='151113',properties=props)
            write_xml(cfg,dest);out=read_template(dest)
            self.assertEqual(out['number of subsequent dates'],'9')
            self.assertNotIn('number of subsequent dates for estimating ionosphere',out)
            self.assertEqual(out['do ionospheric phase estimation'],'False')
            self.assertEqual(out['apply ionospheric phase correction'],'False')
            cfg['properties']={};write_xml(cfg,dest);out=read_template(dest)
            self.assertNotIn('do ionospheric phase estimation',out)
            self.assertNotIn('apply ionospheric phase correction',out)
    def test_read_actual_parameters_not_example_defaults(self):
        opts, flags = parse_cmd3(CMD3.replace('-nrlks1 2', '-nrlks1 7 \\\n'))
        self.assertEqual(opts['-nrlks1'], '7'); self.assertIn('-interp', flags)
        self.assertEqual(opts['-nalks_ion'], '32')

    def test_unknown_or_duplicate_commands_fail_closed(self):
        for text in [CMD3 + CMD3, CMD3.replace('-interp','-unknown 9'), CMD3.replace('-nrlks1 2','-nrlks1 $(id)')]:
            with self.assertRaises(ValueError): parse_cmd3(text)

    def test_split_retains_only_pre_review(self):
        pre = split_pre_review(CMD3)
        self.assertIn('ion_check.py', pre); self.assertNotIn('ion_ls.py',pre)
        with self.assertRaises(ValueError): split_pre_review(CMD3.replace('# estimate ionospheric phase for each date','# changed'))

    def test_network_detects_isolation_and_disconnection(self):
        pairs=['150206-151113','151113-160219','150206-160219']
        self.assertEqual(len(validate_network(pairs,['150206-160219'],['150206','151113','160219'])),2)
        with self.assertRaises(ValueError): validate_network(pairs,pairs[:2],['151113'])
        with self.assertRaises(ValueError): validate_network(['150206-151113','160219-170101'],[],[])
        with self.assertRaises(ValueError): validate_network(pairs,['000000-111111'],[])

    def test_template_roundtrip_and_injection_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'a.xml'
            cfg=dict(data='/data',dem='/dem',dem_geo='/geo',wbd='/wbd',reference='151113',properties={'frames':"['2800']"})
            write_xml(cfg,f); props=read_template(f)
            self.assertEqual(props['frames'],"['2800']")
            cfg['properties']['frames']='$(touch /tmp/bad)'
            with self.assertRaises(ValueError): write_xml(cfg,f)

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.e=Engine(self.base/'registry'); self.root=self.base/'project'
        (self.root/'proc').mkdir(parents=True); (self.root/'.helper').mkdir()
        self.s=dict(id='test',config={'root':str(self.root),'env':'/env','conda':'/conda'},status='ready',stage='',history=[],excluded=[],hashes={})
        self.e.projects['test']=str(self.root); self.e.save(self.s)
    def tearDown(self): self.tmp.cleanup()
    def make_review(self):
        proc=self.root/'proc'; (proc/'cmd_3.sh').write_text(CMD3)
        for pair in ['150206-151113','151113-160219','150206-160219']:
            (proc/'pairs'/pair).mkdir(parents=True)
            p=proc/'pairs_ion'/pair/'ion/ion_cal';p.mkdir(parents=True)
            for ext in ('ion','std','win'): (p/f'filt_ion_64rlks_96alks.{ext}').write_bytes(b'fake-data')
        (proc/'fig_ion').mkdir(); (proc/'fig_ion/150206-151113.png').write_bytes(b'fake-image')
    def test_process_nonzero_is_failure(self):
        with self.assertRaises(RuntimeError): self.e.run(self.s,['/bin/bash','-c','exit 7'])
    def test_retry_only_allows_preparation_without_science_outputs(self):
        self.s.update(status='failed',failed_action='prepare')
        self.assertTrue(self.e.can_retry_prepare(self.s))
        self.s['failed_action']='run'
        self.assertFalse(self.e.can_retry_prepare(self.s))
        self.s['failed_action']='prepare'
        (self.root/'proc/dates').mkdir()
        self.assertFalse(self.e.can_retry_prepare(self.s))
    def test_changed_command_blocked(self):
        p=self.root/'proc/cmd_1.sh';p.write_text('old')
        self.s['hashes']={'cmd_1.sh':digest(p)};p.write_text('new')
        with self.assertRaises(ValueError):self.e.verify_commands(self.s)
    def test_run_stops_for_review_never_runs_correction(self):
        self.make_review(); calls=[]
        self.e.shell=lambda s,p:calls.append(p.name)
        self.e.worker(self.s,'run')
        self.assertEqual(self.e.get('test')['status'],'review')
        self.assertEqual(calls,['cmd_1.sh','cmd_2.sh','helper_cmd3_before_review.sh'])
    def test_failure_does_not_start_next_phase(self):
        def fail(s,p): raise RuntimeError('synthetic failure')
        self.e.shell=fail;self.e.worker(self.s,'run')
        s=self.e.get('test'); self.assertEqual(s['status'],'failed');self.assertEqual(s['history'],[])
    def test_post_review_uses_generated_looks_and_exact_exclusions(self):
        self.make_review();self.s['excluded']=['150206-160219']; calls=[]
        self.s['hashes']={'cmd_3.sh':digest(self.root/'proc/cmd_3.sh')}
        def fake_run(s,args,**kwargs):
            calls.append(args)
            if str(args[1]).endswith('ion_ls.py'):
                folder=self.root/'proc/dates_ion';folder.mkdir()
                for date in ['150206','151113','160219']:(folder/f'filt_ion_{date}_8rlks_12alks.ion').write_bytes(b'data')
        def fake_shell(s,p):
            for d in (self.root/'proc/pairs').iterdir():(d/'filt.unw.geo').write_bytes(b'data')
        self.e.run=fake_run;self.e.shell=fake_shell;self.e.worker(self.s,'continue')
        self.assertEqual(self.e.get('test')['status'],'complete')
        args=calls[0];self.assertEqual(args[-2:],['-exc_pair','150206-160219'])
        self.assertEqual(args[args.index('-nrlks_ion')+1],'32')
        self.assertEqual(len(calls),4)
        self.assertTrue((self.root/'.helper/review.json').exists())
    def test_continue_without_review_is_rejected(self):
        with self.assertRaises(ValueError):self.e.launch('test','continue',[])
    def test_disabled_estimation_skips_all_ion_steps(self):
        for apply in (True,False):
            with self.subTest(apply=apply):
                self.s.update(ion={'estimate':False,'apply':apply},expected_pairs=['150206-151113'])
                calls=[];self.e.shell=lambda s,p:calls.append(p.name)
                p=self.root/'proc/pairs/150206-151113';p.mkdir(parents=True,exist_ok=True)
                (p/'filt.unw.geo').write_bytes(b'data')
                self.e.worker(self.s,'run')
                self.assertEqual(calls,['cmd_1.sh','cmd_2.sh','cmd_4.sh'])
                self.assertEqual(self.e.get('test')['status'],'complete')
    def test_estimation_only_never_applies_correction(self):
        self.make_review();self.s['ion']={'estimate':True,'apply':False}
        self.s['hashes']={'cmd_3.sh':digest(self.root/'proc/cmd_3.sh')}
        calls=[];self.e.run=lambda s,args,**kwargs:calls.append(args)
        def finish_script(s,p):
            calls.append([p.name])
            for d in (self.root/'proc/pairs').iterdir():(d/'filt.unw.geo').write_bytes(b'data')
        self.e.shell=finish_script;self.e.worker(self.s,'continue')
        self.assertEqual(self.e.get('test')['status'],'complete')
        self.assertTrue(calls[0][1].endswith('ion_ls.py'))
        self.assertEqual(calls[1],['cmd_4.sh'])
        self.assertEqual(len(calls),2)
    def test_create_from_form_saves_xml_without_running_commands(self):
        env=self.base/'env';(env/'bin').mkdir(parents=True);(env/'bin/python').touch()
        (env/'share/isce2/alosStack').mkdir(parents=True)
        (env/'share/isce2/alosStack/create_cmds.py').touch()
        source=self.base/'data'
        for date in ['150206','151113']:
            p=source/date;p.mkdir(parents=True)
            (p/f'LED-ALOS2000012800-{date}-FBDR1.1__A').touch()
            (p/f'IMG-HH-ALOS2000012800-{date}-FBDR1.1__A').touch()
        dem=self.base/'dem';dem.touch();Path(str(dem)+'.xml').write_text('<imageFile/>')
        c=dict(name='UI fixture',root=str(self.base/'created'),source=str(source),env=str(env),dem=str(dem),dem_geo=str(dem),wbd=str(dem),reference='151113',properties={},copy=False)
        with patch('alosstack_helper.engine.shutil.which',return_value='/test/conda'):
            s=self.e.create(c)
        self.assertEqual(s['status'],'new')
        self.assertEqual(read_template(Path(c['root'])/'proc/alosStack.xml')['reference date of the stack'],'151113')
        self.assertFalse((Path(c['root'])/'.helper/run.log').exists())
        with self.assertRaises(ValueError):self.e.create(c)
    def test_missing_expected_pair_cannot_be_silently_skipped(self):
        self.make_review();self.s['expected_pairs']=['150206-151113','151113-160219']
        with self.assertRaises(ValueError):self.e.review(self.s,[])
    def test_http_rejects_cross_origin_and_unauthenticated_mutation(self):
        server=make_server(self.e,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            page=urlopen(url).read().decode();self.assertIn('研究工作台',page)
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(url+'/api/stop',data=b'{}',headers={'Content-Type':'application/json'}))
            self.assertEqual(ctx.exception.code,403)
        finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
