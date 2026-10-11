import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from alosstack_helper.engine import Engine
from alosstack_helper.onboarding import Onboarding
from alosstack_helper.server import make_server


class OnboardingAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'SAR_WORKBENCH_HOME': str(self.root/'workbench')}); self.env.start()
        self.server = make_server(Engine(str(self.root/'registry')), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.html = urllib.request.urlopen(self.url).read().decode()
        self.token = re.search(r'name="api-token" content="([^"]+)"', self.html).group(1)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.env.stop(); self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        h = {'X-Token': self.token, 'Content-Type':'application/json'}
        if headers: h.update(headers)
        return urllib.request.urlopen(urllib.request.Request(self.url+path, headers=h,
                                      data=None if data is None else json.dumps(data).encode()))

    def test_first_use_markup_and_auth_protected_runtime_status(self):
        self.assertIn('id="welcome"', self.html); self.assertNotIn('__ONBOARDING__', self.html)
        for path in ('/api/onboarding', '/api/onboarding-job?job=unknown'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, headers={'X-Token': ''})
            self.assertEqual(error.exception.code, 403)
        with self.request('/api/onboarding') as response:
            result = json.load(response)
        self.assertEqual(result['status'], 'not_installed'); self.assertEqual(result['jobs'], [])

    def test_cross_origin_cannot_start_installer(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/setup-start', {'consent':True}, {'Origin':'https://example.org'})
        self.assertEqual(error.exception.code, 403)
        self.assertFalse((self.root/'workbench/runtime').exists())

    def test_install_requires_explicit_local_action(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/setup-start', {'consent':False})
        self.assertEqual(error.exception.code, 400)
        self.assertFalse((self.root/'workbench/runtime').exists())


class PreparationRecovery(unittest.TestCase):
    def test_completed_results_survive_restart_and_incomplete_work_is_not_success(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'onboarding-jobs.json').write_text(json.dumps([
                dict(id='done', kind='import', status='complete', stage='已完成', result={'path':'/tmp/fixture'}),
                dict(id='partial', kind='terrain', status='running', stage='下載中')]))
            manager = Onboarding(root)
            self.assertEqual(manager.job('done')['result']['path'], '/tmp/fixture')
            self.assertEqual(manager.job('partial')['status'], 'failed')
            self.assertNotIn('result', manager.recent()[1])


if __name__ == '__main__': unittest.main()
