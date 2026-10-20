"""HTTP checks use isolated state and mocked review workers; never process SAR."""
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


class SamplingAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        env = patch.dict(os.environ, SAR_WORKBENCH_HOME=str(self.base/'managed'))
        env.start()
        self.addCleanup(env.stop)
        self.engine = Engine(self.base/'registry')
        self.server = make_server(self.engine, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        with urllib.request.urlopen(self.url, timeout=5) as response:
            self.token = re.search(r'name="api-token" content="([^"]+)"', response.read().decode())[1]
        self.manager = self.server.sampling_reviews
        self.body = dict(id='fixture', image_id='a'*32, plan_id='plan', extra_source='/candidate')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, path, body=None, headers=None):
        return urllib.request.urlopen(urllib.request.Request(
            self.url+path, data=None if body is None else json.dumps(body).encode(),
            headers={'X-Token': self.token, 'Content-Type': 'application/json', **(headers or {})}), timeout=5)

    def reject(self, path, body=None, headers=None, code=400):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(path, body, headers)
        self.assertEqual(error.exception.code, code)
        error.exception.close()

    def test_review_preview_and_image_require_same_origin_header_token(self):
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch.object(self.manager, method)) for method in ('review', 'preview', 'image')]
            for path, body in [('/api/sampling-review', self.body), ('/api/sampling-preview', self.body),
                               ('/api/sampling-preview-image?id=fixture&image='+'a'*32, None)]:
                for headers in ({'X-Token': ''}, {'X-Token': 'bad'}, {'Origin': 'https://foreign.example'},
                                {'Host': 'foreign.example'}):
                    self.reject(path, body, headers, 403)
            for mock in mocks:
                mock.assert_not_called()

    def test_endpoints_delegate_without_launch_or_approval(self):
        with patch.object(self.engine, 'launch') as launch:
            with patch.object(self.manager, 'review', return_value={'plan_id': 'plan'}) as review:
                with self.request('/api/sampling-review', self.body) as response:
                    self.assertEqual(json.load(response), {'plan_id': 'plan'})
                review.assert_called_once_with('fixture', '/candidate')
            with patch.object(self.manager, 'preview', return_value={'image_id': 'a'*32}) as preview:
                with self.request('/api/sampling-preview', self.body) as response:
                    self.assertEqual(json.load(response), {'image_id': 'a'*32})
                preview.assert_called_once_with('fixture', 'a'*32, 'plan')
            with patch.object(self.manager, 'image', return_value=b'png') as image:
                with self.request('/api/sampling-preview-image?id=fixture&image='+'a'*32) as response:
                    self.assertEqual(response.read(), b'png')
                    self.assertEqual(response.headers['Content-Type'], 'image/png')
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                image.assert_called_once_with('fixture', 'a'*32)
            launch.assert_not_called()
        self.assertFalse(list(self.base.rglob('*approval*')))

    def test_busy_operations_block_before_review_delegation(self):
        blockers = [
            lambda: patch.object(self.engine, 'active', 'busy'),
            lambda: patch.object(self.server.nisar_jobs, 'active', 'busy'),
            lambda: patch.object(self.server.conversion_jobs, 'active', 'busy'),
            lambda: patch.object(self.server.alos_batches, 'active', 'busy'),
            lambda: patch.object(self.server.migrations, 'busy', return_value='busy'),
            lambda: patch.object(self.server.onboarding.runtime, 'status', return_value={'status': 'running'}),
            lambda: patch.dict(self.server.onboarding.jobs, {'busy': {'status': 'running'}}),
        ]
        with patch.object(self.manager, 'review') as review, patch.object(self.manager, 'preview') as preview:
            for blocker in blockers:
                with blocker():
                    for path in ('/api/sampling-review', '/api/sampling-preview'):
                        self.reject(path, self.body)
            review.assert_not_called()
            preview.assert_not_called()

    def test_stop_remains_available_while_review_worker_is_waiting(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        failures = []
        def blocked(*args):
            entered.set()
            if not release.wait(4):
                raise RuntimeError('Test was not released')
            return {'plan_id': 'plan'}
        def read():
            try:
                with self.request('/api/sampling-review', self.body) as response:
                    response.read()
            except Exception as exc:
                failures.append(exc)
        with patch.object(self.manager, 'review', side_effect=blocked), patch.object(self.engine, 'stop') as stop:
            thread = threading.Thread(target=read)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.request('/api/stop', {}) as response:
                    self.assertTrue(json.load(response)['ok'])
                stop.assert_called_once()
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(failures)

    def test_matching_action_receives_same_preflight_and_source_validation_as_sampling(self):
        state = {'id': 'fixture', 'config': {'workflow': 'alosStack'}}
        with patch.object(self.engine, 'get', return_value=state), \
             patch.object(self.engine, 'require_processing_location', return_value=state), \
             patch.object(self.engine, 'paths', return_value=(self.base, self.base, self.base)), \
             patch.object(self.engine, 'launch') as launch, \
             patch('alosstack_helper.server.require_preflight', return_value={}) as preflight, \
             patch('alosstack_helper.server.verify_saved_inputs') as inputs:
            body = dict(id='fixture', action='sampling_match', sampling_reviewed=True)
            with self.request('/api/action', body) as response:
                self.assertTrue(json.load(response)['ok'])
            preflight.assert_called_once_with(state['config'], existing=True)
            inputs.assert_called_once_with(state, self.base)
            launch.assert_called_once_with('fixture', 'sampling_match', None, body)


if __name__ == '__main__':
    unittest.main()
