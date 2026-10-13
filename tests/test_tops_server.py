"""TOPS routes remain separate from ALOS preflight and analysis routes."""
import json
import re
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import test_tops as fixtures
from alosstack_helper.server import make_server

class TopsAPI(unittest.TestCase):
    setUp=fixtures.TopsTests.setUp
    tearDown=fixtures.TopsTests.tearDown
    def test_api_tops_create_dispatch_and_confirmation(self):
        server=make_server(self.e,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
        try:
            html=urllib.request.urlopen(base).read().decode();token=re.search('name="api-token" content="([^"]+)"',html)[1]
            def request(path,data=None,auth=True):
                headers={'Content-Type':'application/json'}
                if auth:headers['X-Token']=token
                return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers,data=json.dumps(data).encode() if data is not None else None))
            self.assertIn('tops.js',html)
            with self.assertRaises(urllib.error.HTTPError) as err:request('/api/tops-schema',auth=False)
            self.assertEqual(err.exception.code,403)
            r=json.load(request('/api/tops-scan',dict(reference_safe=str(self.a),secondary_safe=str(self.b))));self.assertEqual(r['days'],12)
            with patch('alosstack_helper.server.require_preflight',side_effect=AssertionError('ALOS preflight called')):
                s=json.load(request('/api/create',dict(self.c,workflow='topsApp')));self.assertEqual(s['config']['workflow'],'topsApp')
            s['status']='ready';self.e.save(s)
            with self.assertRaises(urllib.error.HTTPError):request('/api/action',dict(id=s['id'],action='inspect'))
            self.assertIsNone(self.e.active)
            r=json.load(request('/api/project-check',{'id':s['id']}));self.assertTrue(r['can_proceed'])
            with self.assertRaises(urllib.error.HTTPError):request('/api/tops-artifact?id='+s['id']+'&file=state.json')
        finally:server.shutdown();thread.join();server.server_close()
