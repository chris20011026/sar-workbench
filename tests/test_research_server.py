"""Authenticated research/import API checks with isolated synthetic projects.

These tests do not launch Conda, process SAR pixels, or touch the user's registry.
"""
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

from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.server import make_server


class ResearchAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.runtime = patch.dict(os.environ, {'SAR_WORKBENCH_HOME': str(self.base / 'managed')})
        self.runtime.start()
        self.addCleanup(self.runtime.stop)
        self.default = patch('alosstack_helper.server.defaults', return_value={
            'home': str(self.base), 'env': '', 'template': '', 'environments': []})
        self.default.start()
        self.addCleanup(self.default.stop)
        self.e = Engine(self.base / 'registry')
        self.imported = self.project('imported01', 'alosStack', imported=True)
        self.tops = self.project('tops01', 'topsApp')
        self.server = make_server(self.e, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        with urllib.request.urlopen(self.url, timeout=5) as response:
            self.html = response.read().decode()
        self.token = re.search(r'name="api-token" content="([^"]+)"', self.html)[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def project(self, key, workflow, imported=False):
        root = self.base / key
        (root / 'proc').mkdir(parents=True)
        (root / '.helper').mkdir()
        state = dict(id=key, config=dict(root=str(root), workflow=workflow, name='Synthetic ' + key,
                     reference='220801', env='/synthetic/not-executed', swaths=[1], polarization='VV'),
                     status='imported' if imported else 'new', stage='Synthetic fixture', history=[],
                     dates=[{'date': '220801'}, {'date': '220813'}, {'date': '220825'}],
                     completed_steps=[], expected_pairs=['220801-220813', '220813-220825'],
                     excluded=[], import_only=imported, recovery_enabled=False)
        self.e.save(state); self.e.register(key, root)
        return state

    def request(self, path, data=None, headers=None):
        merged = {'X-Token': self.token, 'Content-Type': 'application/json'}
        merged.update(headers or {})
        return urllib.request.urlopen(urllib.request.Request(
            self.url + path, headers=merged,
            data=None if data is None else json.dumps(data).encode()), timeout=5)

    def value(self, path, data=None):
        with self.request(path, data) as response:
            return json.load(response)

    def rejected(self, path, data=None, headers=None, code=400):
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.request(path, data, headers)
        self.assertEqual(failure.exception.code, code)
        error = json.loads(failure.exception.read())
        failure.exception.close()
        self.assertIn('error', error)
        return error['error']

    def test_new_static_assets_load_and_do_not_expose_data_without_token(self):
        for name in ('research.js', 'research.css', 'legacy-import.js', 'legacy-import.css',
                     'tops-validation.js', 'tops-validation.css'):
            self.assertIn('/' + name, self.html)
            with self.request('/' + name, headers={'X-Token': ''}) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
                self.assertGreater(len(response.read()), 50)
        self.assertLess(self.html.index('/tops.js'), self.html.index('/tops-validation.js'))
        self.rejected('/research.js', headers={'Host': 'other.invalid'}, code=403)

    def test_all_new_routes_require_token_and_same_origin(self):
        gets = [
            '/api/comparison-candidates?id=imported01', '/api/comparisons?id=imported01',
            '/api/comparison?id=imported01&comparison=aaaaaaaaaaaa',
            '/api/research-reports?id=imported01', '/api/research-report?id=imported01&report=' + 'a' * 16,
            '/api/research-report-artifact?id=imported01&report=' + 'a' * 16 + '&file=report.json',
            '/api/comparison-artifact?id=imported01&comparison=aaaaaaaaaaaa&file=left.png',
            '/api/legacy-import?preview=' + 'a' * 24, '/api/tops-validation?id=tops01',
            '/api/tops-validation-artifact?id=tops01&file=validation.json',
        ]
        posts = [
            '/api/comparison-start', '/api/comparison-stop', '/api/comparison-point',
            '/api/research-report-start', '/api/research-report-stop',
            '/api/legacy-preview', '/api/legacy-create', '/api/legacy-stop', '/api/tops-validation-review',
        ]
        before = (self.base / 'tops01/.helper/state.json').read_bytes()
        for path in gets:
            with self.subTest(path=path):
                self.rejected(path, headers={'X-Token': ''}, code=403)
                self.rejected(path, headers={'Origin': 'https://unrelated.example'}, code=403)
        for path in posts:
            with self.subTest(path=path):
                self.rejected(path, {}, {'X-Token': ''}, code=403)
                self.rejected(path, {}, {'Origin': 'https://unrelated.example'}, code=403)
        self.assertEqual(before, (self.base / 'tops01/.helper/state.json').read_bytes())
        self.assertFalse((self.e.home / 'research').exists())
        self.assertIsNone(self.e.active)

    def test_empty_list_wrappers_and_imported_workspace_state(self):
        self.assertEqual(self.value('/api/comparisons?id=imported01'), {'comparisons': []})
        self.assertEqual(self.value('/api/research-reports?id=imported01'), {'reports': []})
        self.assertEqual(self.value('/api/comparison-candidates?id=imported01'), {'project_id': 'imported01', 'runs': []})
        state = self.value('/api/state')
        imported = next(x for x in state['projects'] if x['id'] == 'imported01')
        self.assertEqual(imported['status'], 'imported'); self.assertTrue(imported['import_only'])
        workspace = self.value('/api/workspace')
        item = next(x for x in workspace['records'] if x['key'] == 'project:imported01')
        self.assertIn('匯入', item['status_label'])
        self.assertEqual(item['group'], 'attention')

    def test_imported_projects_cannot_run_recover_or_preflight_original_workflow(self):
        state_file = self.base / 'imported01/.helper/state.json'
        before = state_file.read_bytes()
        with (patch.object(self.e, 'launch') as launch, patch('alosstack_helper.server.require_preflight') as preflight,
              patch.object(self.e, 'recovery_enable') as recovery):
            for action in ('prepare', 'run', 'continue', 'resume', 'recover', 'export'):
                error = self.rejected('/api/action', {'id': 'imported01', 'action': action})
                self.assertIn('匯入', error)
            self.assertIn('匯入', self.rejected('/api/recovery-enable', {'id': 'imported01', 'enabled': True}))
            self.assertIn('匯入', self.rejected('/api/project-check', {'id': 'imported01'}))
            launch.assert_not_called(); preflight.assert_not_called(); recovery.assert_not_called()
        self.assertEqual(before, state_file.read_bytes())
        self.assertEqual(list((self.base / 'imported01/proc').iterdir()), [])

    def test_busy_and_migration_gates_block_new_research_launches(self):
        routes = ['/api/comparison-start', '/api/research-report-start', '/api/legacy-preview', '/api/legacy-create']
        with (patch.object(self.server.comparisons, 'start') as comp, patch.object(self.server.reports, 'start') as report,
              patch.object(self.server.legacy, 'preview') as preview, patch.object(self.server.legacy, 'create') as create):
            self.e.active = 'another:analysis:fixture'
            try:
                for path in routes: self.assertIn('等待', self.rejected(path, {}))
            finally: self.e.active = None
            with patch.object(self.server.migrations, 'busy', return_value='moving'):
                for path in routes: self.assertIn('搬移', self.rejected(path, {}))
            for method in (comp, report, preview, create): method.assert_not_called()

    def test_async_start_delegation_and_explicit_import_review_boolean(self):
        with patch.object(self.server.comparisons, 'start', return_value={'status': 'queued', 'id': 'a' * 12}) as method:
            result = self.value('/api/comparison-start', {'id': 'imported01', 'left': '1' * 12, 'right': '2' * 12})
            self.assertEqual(result['status'], 'queued')
            method.assert_called_once_with('imported01', '1' * 12, '2' * 12)
        body = {'id': 'imported01', 'run': '1' * 12, 'title': 'Synthetic report'}
        with patch.object(self.server.reports, 'start', return_value={'status': 'running', 'id': 'a' * 16}) as method:
            self.assertEqual(self.value('/api/research-report-start', body)['status'], 'running')
            method.assert_called_once_with('imported01', body)
        with patch.object(self.server.legacy, 'preview', return_value={'status': 'scanning', 'id': 'a' * 24}) as method:
            body = {'source': '/synthetic/path', 'name': 'Synthetic import'}
            self.assertEqual(self.value('/api/legacy-preview', body)['status'], 'scanning')
            method.assert_called_once_with(body)
        with patch.object(self.server.legacy, 'create', return_value={'status': 'importing'}) as method:
            self.value('/api/legacy-create', {'preview_id': 'a' * 24, 'reviewed': 'true'})
            method.assert_called_once_with('a' * 24, False)

    def test_report_artifact_allowlist_and_unfinished_download_guard(self):
        report = 'a' * 16
        folder = self.server.reports.folder('imported01', report); folder.mkdir(parents=True)
        job = {'id': report, 'project_id': 'imported01', 'status': 'running', 'created': 'test'}
        atomic_json(folder / 'job.json', job)
        (folder / 'report.json').write_text('{"synthetic": true}')
        url = f'/api/research-report-artifact?id=imported01&report={report}&file='
        self.rejected(url + 'report.json')
        job['status'] = 'complete'; atomic_json(folder / 'job.json', job)
        with self.request(url + 'report.json') as response:
            self.assertEqual(json.load(response), {'synthetic': True})
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.rejected(url + '../job.json')
        self.rejected(url + 'job.json')
        (folder / 'report.json').unlink()
        (folder / 'report.json').symlink_to(self.base / 'imported01/.helper/state.json')
        self.rejected(url + 'report.json')

    def test_tops_review_and_exports_write_only_registry_and_keep_validation_gate(self):
        state_file = self.base / 'tops01/.helper/state.json'
        before = state_file.read_bytes()
        report = self.value('/api/tops-validation?id=tops01')
        self.assertEqual(report['status'], 'pending')
        review = self.value('/api/tops-validation-review', {'id': 'tops01', 'evidence_id': report['evidence_id'],
                            'dataset_kind': 'synthetic', 'items': {}})
        self.assertEqual(review['status'], 'synthetic_only')
        self.assertTrue(review['workflow_experimental']); self.assertFalse(review['tops_stack_enabled'])
        exported = self.value('/api/tops-validation-artifact?id=tops01&file=validation.json')
        self.assertEqual(exported['review']['dataset_kind'], 'synthetic')
        self.assertTrue((self.e.home / 'research/tops-validation/tops01/review.json').is_file())
        self.assertEqual(before, state_file.read_bytes())
        self.assertEqual(sorted(x.name for x in state_file.parent.iterdir()), ['state.json'])
        self.rejected('/api/tops-validation-artifact?id=tops01&file=../state.json')


if __name__ == '__main__':
    unittest.main()
