"""HTTP wiring for independent human review gates. No scientific runs."""
from contextlib import ExitStack
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
from alosstack_helper.server import make_server


class AcceptanceAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(os.environ, SAR_WORKBENCH_HOME=str(self.base/'managed'))
        env.start(); self.addCleanup(env.stop)
        self.engine = Engine(self.base/'registry')
        self.server = make_server(self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        with urllib.request.urlopen(self.url, timeout=5) as response:
            self.html = response.read().decode()
        self.token = re.search(r'name="api-token" content="([^"]+)"', self.html)[1]
        self.body = dict(id='fixture', run='a'*12, kind='ion', report_id='report', reviewed=True, notes='人工判讀紀錄')

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)

    def request(self, path, body=None, headers=None):
        return urllib.request.urlopen(urllib.request.Request(
            self.url+path, data=None if body is None else json.dumps(body).encode(),
            headers={'X-Token': self.token, 'Content-Type': 'application/json', **(headers or {})}), timeout=5)

    def reject(self, path, headers=None, code=400):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(path, self.body, headers)
        self.assertEqual(error.exception.code, code); error.exception.close()

    def test_review_endpoints_require_page_token_and_local_origin(self):
        with patch.object(self.engine, 'refresh_alos_review') as alos, patch.object(self.server.analyses, 'review_continue') as mint:
            for endpoint in ('/api/alos-review', '/api/analysis-review', '/api/alos-review-preview', '/api/analysis-spatial'):
                for headers in ({'X-Token':''}, {'Origin':'https://foreign.example'}, {'Host':'foreign.example'}):
                    self.reject(endpoint, headers, 403)
            alos.assert_not_called(); mint.assert_not_called()

    def test_routes_do_not_fall_back_to_previous_science_actions(self):
        with patch.object(self.engine, 'launch') as launch:
            with patch.object(self.engine, 'refresh_alos_review', return_value={'report_id':'new'}) as call:
                with self.request('/api/alos-review', self.body) as response:
                    self.assertEqual(json.load(response), {'report_id':'new'})
                call.assert_called_once_with('fixture','ion')
            with patch.object(self.server.analyses, 'review_continue', return_value={'status':'running'}) as call:
                with self.request('/api/analysis-review', self.body) as response:
                    self.assertEqual(json.load(response), {'status':'running'})
                call.assert_called_once_with('fixture','a'*12,self.body)
            launch.assert_not_called()

    def test_preview_routes_return_bounded_reports_without_launching(self):
        body = {**self.body, 'pair':'150206-151113', 'date':'20200101'}
        with patch.object(self.engine, 'launch') as launch:
            with patch.object(self.engine, 'alos_review_preview', return_value={'row': {}}) as call:
                with self.request('/api/alos-review-preview', body) as response:
                    self.assertEqual(json.load(response), {'row': {}})
                call.assert_called_once_with('fixture', 'ion', '150206-151113', 'report')
            with patch.object(self.server.analyses, 'spatial', return_value={'stages': []}) as call:
                with self.request('/api/analysis-spatial', body) as response:
                    self.assertEqual(json.load(response), {'stages': []})
                call.assert_called_once_with('fixture', 'a'*12, body)
            launch.assert_not_called()

    def test_review_blocked_while_other_managers_are_busy(self):
        for endpoint, target, method in [('/api/alos-review', self.engine, 'refresh_alos_review'),
                                         ('/api/analysis-review', self.server.analyses, 'review_continue')]:
            for manager in (self.server.nisar_jobs, self.server.conversion_jobs, self.server.alos_batches):
                with patch.object(manager, 'active', 'busy'), patch.object(target, method) as call:
                    self.reject(endpoint); call.assert_not_called()
            with patch.object(self.server.migrations, 'busy', return_value=True), patch.object(target, method) as call:
                self.reject(endpoint); call.assert_not_called()
            with patch.object(self.server.onboarding.runtime, 'status', return_value={'status':'running'}), patch.object(target, method) as call:
                self.reject(endpoint); call.assert_not_called()

    def test_loaded_page_serves_new_assets(self):
        for asset in ('alos-acceptance.js','mintpy-diagnostics.js','mintpy-diagnostics.css'):
            self.assertIn('/'+asset, self.html)
            with self.request('/'+asset) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 100)


if __name__ == '__main__': unittest.main()
