import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from alosstack_helper.confirmation import (network, Confirmations, compare_generated,
    review, resolve, verify_saved_inputs, saved_review)


class ConfirmationControls(unittest.TestCase):
    def test_disconnected_components_and_isolated_dates(self):
        r=network(['200101-200201','200301-200401'],['200101','200201','200301','200401','200501'])
        self.assertFalse(r['connected']);self.assertEqual(len(r['components']),3)
        self.assertEqual(r['isolated'],['200501'])
        self.assertFalse(network([],[])['connected'])

    def test_snapshot_rejects_changed_config_source_and_failed_report(self):
        c={'name':'test'}
        with patch('alosstack_helper.confirmation.review',return_value=dict(config=c,can_proceed=True,fingerprint=['a'])), patch('alosstack_helper.confirmation.fingerprint',return_value=['a']) as fingerprint:
            service=Confirmations();r=service.preview(c)
            request={**c,'confirmation_id':r['confirmation_id'],'preflight_reviewed':True}
            self.assertTrue(service.validate(request)['can_proceed'])
            with self.assertRaises(ValueError):service.validate({**request,'name':'changed'})
            fingerprint.return_value=['changed']
            with self.assertRaises(ValueError):service.validate(request)
            with self.assertRaises(ValueError):service.validate({**request,'confirmation_id':'missing'})

    def test_generated_pair_mismatch_stops_before_science(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);proc=root/'proc';meta=root/'meta';proc.mkdir();meta.mkdir()
            ion=dict(estimate=False,apply=True,subsequent=1,subsequent_ion=4)
            (meta/'confirmation.json').write_text(json.dumps(dict(pairs=['200101-200201'],ion_pairs=[],resolved=ion)))
            (proc/'cmd_2.sh').write_text('insarpair=(200101-200201)\n')
            compare_generated({'ion':ion},proc,meta)
            self.assertTrue(json.loads((meta/'confirmation-generated.json').read_text())['matched'])
            (proc/'cmd_2.sh').write_text('insarpair=(200101-200301)\n')
            with self.assertRaisesRegex(ValueError,'不一致'):compare_generated({'ion':ion},proc,meta)
            self.assertFalse(json.loads((meta/'confirmation-generated.json').read_text())['matched'])

    def test_saved_input_check_and_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);state={'config':{}}
            verify_saved_inputs(state,root)
            (root/'confirmation.json').write_text('{"fingerprint":["before"]}')
            with patch('alosstack_helper.confirmation.fingerprint',return_value=['after']):
                with self.assertRaisesRegex(ValueError,'已改變'):verify_saved_inputs(state,root)

    def test_bad_preflight_never_invokes_science(self):
        with patch('alosstack_helper.confirmation.check_project',return_value={'can_proceed':False,'checks':[]}),patch('alosstack_helper.confirmation.resolve') as worker:
            r=review({'workflow':'alosStack'})
            self.assertFalse(r['can_proceed']);worker.assert_not_called()

    def test_server_saves_report_and_rechecks_before_prepare(self):
        import re
        import threading
        import urllib.request
        import urllib.error
        from test_preflight import PreflightTests
        from alosstack_helper.engine import Engine
        from alosstack_helper.server import make_server
        fixture=PreflightTests();fixture.setUp()
        engine=Engine(fixture.base/'registry');server=make_server(engine,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            with urllib.request.urlopen(base) as response:html=response.read().decode()
            token=re.search('name="api-token" content="([^"]+)"',html).group(1)
            def post(path,data):
                req=urllib.request.Request(base+path,data=json.dumps(data).encode(),headers={'X-Token':token,'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:return json.load(response)
            report=dict(config=copy.deepcopy(fixture.config),can_proceed=True,needs_review=True,checks=[],fingerprint=['before'],pairs=['200101-200201'],ion_pairs=[],resolved={})
            with patch('alosstack_helper.confirmation.review',return_value=report),patch('alosstack_helper.confirmation.fingerprint',return_value=['before']) as fingerprint:
                preview=post('/api/confirmation',fixture.config)
                request={**fixture.config,'preflight_reviewed':True,'confirmation_id':preview['confirmation_id']}
                with self.assertRaises(urllib.error.HTTPError):post('/api/create',{**request,'reference':'200201'})
                self.assertFalse(Path(fixture.config['root']).exists())
                state=post('/api/create',request)
                saved=post('/api/confirmation-saved',{'id':state['id']})
                self.assertEqual(saved['network']['pairs'],['200101-200201'])
                self.assertTrue((Path(fixture.config['root'])/'.helper/confirmation.json').is_file())
                fingerprint.return_value=['changed']
                with self.assertRaises(urllib.error.HTTPError):post('/api/action',{'id':state['id'],'action':'prepare'})
                self.assertIsNone(engine.active)
                self.assertEqual(engine.get(state['id'])['status'],'new')
        finally:
            server.shutdown();server.server_close();thread.join();fixture.tearDown()


@unittest.skipUnless(os.environ.get('ALOSSTACK_TEST_ENV'),'Requires installed ISCE2')
class InstalledPairRules(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        data=self.root/'data';data.mkdir()
        for date in ('200101','200201','200301','200401'):(data/date).mkdir()
        self.config=dict(source=str(data),env=os.environ['ALOSSTACK_TEST_ENV'],reference='200101',dem='/tmp/dem',dem_geo='/tmp/dem',wbd='/tmp/wbd',properties={})
    def tearDown(self): self.tmp.cleanup()

    def test_selected_defaults_explicit_nine_and_ion_off(self):
        self.config['properties']={'number of subsequent dates':'9','do ionospheric phase estimation':'False','apply ionospheric phase correction':'False'}
        r=resolve(self.config)
        self.assertEqual(len(r['pairs']),6);self.assertEqual(r['ion_pairs'],[])
        self.assertEqual(r['resolved']['subsequent'],9)
        self.assertEqual(r['resolved']['subsequent_ion'],4)

    def test_upstream_date_exclusion_and_time_filter(self):
        self.config['properties']={'number of subsequent dates':'1','dates to be excluded':"['200201']",'do ionospheric phase estimation':'False'}
        r=resolve(self.config)
        self.assertEqual(r['pairs'],['200301-200401'])
        self.config['properties']={'number of subsequent dates':'9','pair time span maximum in years':'0.10','do ionospheric phase estimation':'False'}
        r=resolve(self.config)
        self.assertEqual(len(r['pairs']),3)
