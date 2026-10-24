"""Isolation and authorization checks for self-tests and new analysis recovery."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from alosstack_helper.engine import Engine
from alosstack_helper.server import make_server


class OperationsAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, SAR_WORKBENCH_HOME=str(self.root/'managed'))
        env.start(); self.addCleanup(env.stop)
        self.engine = Engine(self.root/'registry')
        self.server = make_server(self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        with urlopen(self.base) as r: html = r.read().decode()
        self.token = re.search(r'name="api-token" content="([^"]+)"', html)[1]

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)

    def request(self, path, data=None, headers=None):
        return urlopen(Request(self.base+path,
            headers={'X-Token':self.token,'Content-Type':'application/json',**(headers or {})},
            data=None if data is None else json.dumps(data).encode()), timeout=5)

    def reject(self, path, data=None, headers=None, code=400):
        with self.assertRaises(HTTPError) as error: self.request(path, data, headers)
        self.assertEqual(error.exception.code, code); error.exception.close()

    def test_auth_covers_status_report_recovery_and_launch(self):
        routes = [('/api/selftest',None),('/api/selftest-report',None),
                  ('/api/analysis-recovery?id=missing&run=missing',None),
                  ('/api/selftest-start',{}),('/api/selftest-stop',{}),
                  ('/api/analysis-recover',{'id':'missing','run':'missing','reviewed':True})]
        for path, body in routes:
            for headers in ({'X-Token':''},{'Origin':'https://outside.example'},{'Host':'outside.example'}):
                self.reject(path,body,headers,403)
        self.assertFalse((self.root/'registry/selftests').exists())

    def test_reading_idle_state_never_starts_or_registers_science(self):
        with self.request('/api/selftest') as r: state=json.load(r)
        self.assertEqual(state['status'],'idle'); self.assertTrue(state['synthetic'])
        self.assertIsNone(self.engine.active); self.assertEqual(self.engine.projects,{})
        self.reject('/api/selftest-report')
        for name in ('operations.js','operations.css'):
            with self.request('/'+name) as r: self.assertGreater(len(r.read()),100)

    def test_selftest_respects_all_busy_services(self):
        blockers=[lambda:patch.object(self.engine,'active','science'),
                  lambda:patch.object(self.server.nisar_jobs,'active','science'),
                  lambda:patch.object(self.server.conversion_jobs,'active','science'),
                  lambda:patch.object(self.server.alos_batches,'active','science'),
                  lambda:patch.object(self.server.migrations,'busy',return_value='copy'),
                  lambda:patch.object(self.server.onboarding.runtime,'status',return_value={'status':'running'}),
                  lambda:patch.dict(self.server.onboarding.jobs,{'demo':{'status':'running'}})]
        with patch.object(self.server.selftests,'start') as start:
            for blocker in blockers:
                with blocker():self.reject('/api/selftest-start',{'environment':'/unused'})
            start.assert_not_called()

    def test_running_selftest_blocks_new_work_but_can_be_stopped(self):
        with patch.object(self.server.selftests,'status',return_value={'status':'running'}), \
             patch.object(self.server.selftests,'stop') as stop, \
             patch.object(self.server.analyses,'recover') as recover:
            for path in ('/api/analysis-recover','/api/setup-start','/api/create','/api/migration-plan'):
                self.reject(path,{'id':'missing','run':'missing'})
            with self.request('/api/selftest-stop',{}) as r:self.assertEqual(r.status,200)
            stop.assert_called_once(); recover.assert_not_called()

    def test_recovery_keeps_explicit_review_and_checkpoint_parameters(self):
        payload={'id':'fixture','run':'old','checkpoint_id':'chosen','reviewed':False}
        with patch.object(self.server.analyses,'recover',return_value={'id':'new','status':'loaded'}) as recover:
            with self.request('/api/analysis-recover',payload) as r:self.assertEqual(json.load(r)['id'],'new')
            # No server shortcut can turn a missing human decision into consent.
            recover.assert_called_once_with('fixture','old',payload)


if __name__=='__main__':unittest.main()
