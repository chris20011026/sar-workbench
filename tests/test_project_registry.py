"""Unregistering projects must remain independent of scientific data deletion."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine


class ProjectRegistry(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.engine = Engine(self.base / 'state')
        self.root = self.base / 'project'
        self.key = 'aabbccddeeff'
        self.engine.register(self.key, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def project(self):
        atomic_json(self.root / '.helper/state.json', dict(id=self.key, status='complete',
                    config=dict(name='測試研究', root=str(self.root), env='/existing/insar_arm')))
        (self.root / 'proc').mkdir()
        (self.root / 'proc/result.unw').write_bytes(b'keep these scientific results')
        atomic_json(self.root / 'analyses/001/analysis.json', dict(id='001', status='complete'))

    def test_existing_files_are_unchanged_and_undo_restores_same_project(self):
        self.project()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        record = self.engine.unregister(self.key)
        self.assertEqual(record['name'], '測試研究')
        self.assertEqual(self.engine.list(), [])
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.engine.restore_registration(record['token'])
        self.assertEqual(self.engine.get(self.key)['config']['name'], '測試研究')
        self.assertEqual(self.engine.removed_projects(), [])

    def test_missing_project_can_be_removed_and_restored_after_restart(self):
        record = self.engine.unregister(self.key)
        restarted = Engine(self.engine.home)
        self.assertEqual(restarted.unavailable(), [])
        self.assertEqual(restarted.removed_projects(), [record])
        restarted.restore_registration(record['token'])
        self.assertEqual(restarted.unavailable()[0]['id'], self.key)
        self.assertFalse(self.root.exists())
        self.assertEqual(json.loads(restarted.registry.read_text()), {self.key: str(self.root)})

    def test_active_project_and_analysis_cannot_be_removed_even_when_folder_missing(self):
        for active in (self.key, self.key + ':analysis'):
            self.engine.active = active
            with self.assertRaisesRegex(ValueError, '仍在運算'):
                self.engine.unregister(self.key)
            self.assertIn(self.key, self.engine.projects)
        self.engine.active = 'unrelated-project'
        self.engine.unregister(self.key)
        self.assertEqual(self.engine.active, 'unrelated-project')

    def test_live_orphan_science_and_analysis_workers_block_removal(self):
        self.project()
        meta = self.root / '.helper/worker-process.json'
        atomic_json(meta, {'pgid': os.getpgrp()})
        with self.assertRaisesRegex(ValueError, '背景程序'):
            self.engine.unregister(self.key)
        meta.unlink()
        (self.root / 'analyses/001/worker.pid').write_text(str(os.getpid()))
        with self.assertRaisesRegex(ValueError, '背景程序'):
            self.engine.unregister(self.key)
        self.assertIn(self.key, self.engine.projects)

    def test_failed_registry_write_keeps_registration_and_hides_unused_backup(self):
        def write(path, value):
            if Path(path) == self.engine.registry: raise OSError('disk full')
            atomic_json(path, value)
        with patch('alosstack_helper.engine.atomic_json', side_effect=write):
            with self.assertRaisesRegex(OSError, 'disk full'): self.engine.unregister(self.key)
        self.assertIn(self.key, self.engine.projects)
        self.assertIn(self.key, json.loads(self.engine.registry.read_text()))
        self.assertEqual(self.engine.removed_projects(), [])

    def test_restore_does_not_attach_replacement_or_duplicate_project(self):
        record = self.engine.unregister(self.key)
        atomic_json(self.root / '.helper/state.json', dict(id='replacement'))
        with self.assertRaisesRegex(ValueError, '另一個專案'):
            self.engine.restore_registration(record['token'])
        (self.root / '.helper/state.json').unlink()
        self.engine.register('different-id', self.root)
        with self.assertRaisesRegex(ValueError, '已在清單'):
            self.engine.restore_registration(record['token'])

    def test_repeated_removal_only_lists_latest_record_and_rejects_unknown_ids(self):
        first = self.engine.unregister(self.key)
        with self.assertRaises(ValueError): self.engine.unregister(self.key)
        self.engine.restore_registration(first['token'])
        second = self.engine.unregister(self.key)
        self.assertEqual(self.engine.removed_projects(), [second])
        for token in ('../projects', '', '0' * 32, None):
            with self.assertRaises(ValueError): self.engine.restore_registration(token)

    def test_concurrent_registration_and_removal_do_not_lose_updates(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.engine.unregister, self.key),
                    pool.submit(self.engine.register, 'new-project', self.base / 'new')]
            for job in jobs: job.result()
        self.assertEqual(json.loads(self.engine.registry.read_text()), {'new-project': str(self.base / 'new')})
        self.assertEqual(self.engine.projects, json.loads(self.engine.registry.read_text()))


if __name__ == '__main__': unittest.main()
