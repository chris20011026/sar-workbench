"""Real filesystem accounting plus cancellation, offline cache and API guards."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.storage_usage import Inventory, StorageUsage, category


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.root = self.base/'project'; self.key = 'project123'
        self.state = dict(id=self.key, status='complete', config=dict(root=str(self.root), name='研究', workflow='alosStack'), history=[])
        atomic_json(self.root/'.helper/state.json', self.state)
        (self.root/'proc').mkdir()
        self.manager = None

    def tearDown(self):
        if self.manager: self.manager.stop(shutdown=True)
        self.temp.cleanup()

    def write(self, path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'a'*size)
        return path

    def scan(self, external=False, **changes):
        request = dict(id=self.key, root=str(self.root), include_external=external, **changes)
        self.updates = []
        with patch('alosstack_helper.storage_usage.mounted_volumes', return_value=[]):
            return Inventory(request, self.updates.append).run()

    def engine(self):
        e = Engine(self.base/'registry'); e.register(self.key, self.root)
        self.manager = StorageUsage(e)
        return e

    def test_categories_and_totals_do_not_change_any_files(self):
        entries = {'data/IMG':1234, 'proc/pair.unw':2345, 'analyses/run/velocity.h5':3456,
                   'analyses/run/cache/map.png':123, 'analyses/.map-cache/preview':45,
                   '.helper/recovery/snapshot/proc/a':567, 'dem/a.dem':789, 'notes.txt':12}
        for name, count in entries.items(): self.write(self.root/name, count)
        before = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = self.scan()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['project']['bytes'], sum(map(len,before.values())))
        self.assertEqual(report['project']['bytes'], sum(c['bytes'] for c in report['categories'].values()))
        self.assertEqual(report['categories']['cache']['bytes'],168)
        self.assertEqual(report['categories']['recovery']['bytes'],567)
        self.assertEqual(report['categories']['mintpy']['bytes'],3456)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertTrue(report['disks'][0]['free'] > 0)
        self.assertEqual(self.updates[0]['status'],'checking')

    def test_sparse_files_and_hardlinks_are_not_claimed_as_reclaimable_space(self):
        data = self.root/'data/sparse'; data.parent.mkdir()
        with data.open('wb') as f: f.truncate(32*1024**2)
        os.link(data, self.root/'proc/same-file')
        report = self.scan()
        self.assertEqual(report['project']['bytes'],32*1024**2+(self.root/'.helper/state.json').stat().st_size)
        self.assertEqual(report['project']['hardlinks'],1)
        self.assertLess(report['project']['allocated'],report['project']['bytes'])

    def test_symlink_cycles_and_external_links_are_not_traversed(self):
        self.write(self.base/'outside/large',4096)
        (self.root/'loop').symlink_to(self.root, target_is_directory=True)
        (self.root/'data').symlink_to(self.base/'outside', target_is_directory=True)
        report = self.scan()
        self.assertEqual(report['project']['bytes'],(self.root/'.helper/state.json').stat().st_size)
        self.assertEqual(report['project']['links'],2)

    def test_configured_external_parent_alias_and_nested_source_count_once(self):
        source = self.base/'images'; self.write(source/'part/IMG',777);self.write(source/'DEM',99)
        (self.base/'alias').symlink_to(source, target_is_directory=True)
        self.state['config'].update(source=str(source), data=str(self.base/'alias'), dem=str(source/'DEM'))
        atomic_json(self.root/'.helper/state.json',self.state)
        report = self.scan(True)
        self.assertEqual(report['external']['bytes'],876)
        self.assertEqual(sum(row['status']=='shared' for row in report['sources']),2)
        self.assertEqual(report['project']['bytes'],(self.root/'.helper/state.json').stat().st_size)
        report = self.scan(False)
        self.assertEqual(report['external']['bytes'],0)
        self.assertTrue(all(row['status']=='not_scanned' for row in report['sources']))

    def test_explicit_external_parent_never_recounts_project_subtree(self):
        self.write(self.base/'archive/input',123)
        self.state['config']['source']=str(self.base)
        atomic_json(self.root/'.helper/state.json',self.state)
        report = self.scan(True)
        self.assertEqual(report['external']['bytes'],123)

    def test_custom_input_paths_and_registered_symlink_source(self):
        self.write(self.root/'imagery/IMG',456)
        self.write(self.base/'terrain/dem',789)
        (self.root/'dem').symlink_to(self.base/'terrain',target_is_directory=True)
        self.state['config'].update(source=str(self.root/'imagery'),dem=str(self.root/'dem/dem'))
        atomic_json(self.root/'.helper/state.json',self.state)
        report = self.scan(True)
        self.assertEqual(report['categories']['raw']['bytes'],456)
        self.assertEqual(report['external']['bytes'],789)
        self.assertEqual(report['sources'][0]['status'],'in_project')

    def test_missing_input_is_partial_only_when_included(self):
        self.state['config']['source']=str(self.base/'not-here')
        atomic_json(self.root/'.helper/state.json',self.state)
        self.assertEqual(self.scan(False)['status'],'complete')
        report=self.scan(True)
        self.assertEqual(report['status'],'partial');self.assertEqual(report['error_count'],1)

    def test_permission_failure_is_not_complete_or_silently_zero(self):
        blocked=self.root/'proc/blocked';blocked.mkdir()
        real=os.scandir
        def scan(path):
            if isinstance(path,int) and os.fstat(path).st_ino==blocked.stat().st_ino:raise PermissionError('拒絕讀取')
            return real(path)
        with patch('alosstack_helper.storage_usage.os.scandir',side_effect=scan):report=self.scan()
        self.assertEqual(report['status'],'partial');self.assertIn('拒絕讀取',report['errors'][0]['message'])

    def test_wrong_disk_or_project_id_prevents_scan(self):
        binding=dict(mount=str(self.base),identity='uuid:original')
        report=self.scan(binding=binding)
        self.assertEqual(report['status'],'failed');self.assertEqual(report['visited'],0)
        request=dict(id='other',root=str(self.root),include_external=False)
        report=Inventory(request,lambda _:None).run()
        self.assertEqual(report['status'],'failed')

    def test_removed_during_walk_reports_partial_without_recreating_root(self):
        self.write(self.root/'proc/a',50)
        real=Inventory.walk
        away=self.base/'away'
        def move(scanner,*args,**kwargs):
            real(scanner,*args,**kwargs);self.root.rename(away)
        with patch.object(Inventory,'walk',move):report=self.scan()
        self.assertEqual(report['status'],'partial');self.assertFalse(self.root.exists())

    def test_background_result_stays_readable_offline_and_marks_relinked_path(self):
        e=self.engine();self.manager.start(self.key)
        for _ in range(150):
            data=self.manager.get(self.key)
            if not data['busy']:break
            time.sleep(.02)
        self.assertEqual(data['report']['status'],'complete')
        self.root.rename(self.base/'away')
        with patch('alosstack_helper.storage.read_project',side_effect=AssertionError('must not touch disk')):
            cached=self.manager.get(self.key)
        self.assertEqual(cached['report'],data['report'])
        e.projects[self.key]=str(self.base/'away')
        self.assertTrue(self.manager.get(self.key)['location_changed'])

    def stalled_worker(self, args, **kwargs):
        # The real scanner blocks before reading its request. Stop and the parent
        # lifetime pipe must still work without touching a research filesystem.
        args=list(args);fifo=self.base/'slow-request'
        os.mkfifo(fifo);args[-3]=str(fifo)
        return self.real_popen(args,**kwargs)

    def test_stop_and_restart_are_isolated_from_scientific_processes(self):
        e=self.engine();self.real_popen=subprocess.Popen
        with patch('alosstack_helper.storage_usage.subprocess.Popen',side_effect=self.stalled_worker):
            self.manager.start(self.key)
        child=self.manager.process
        with self.assertRaisesRegex(ValueError,'進行中'):self.manager.start(self.key)
        self.manager.stop(self.key)
        self.assertIsNotNone(child.poll())
        self.assertEqual(self.manager.get(self.key)['report']['status'],'cancelled')
        self.assertIsNone(e.active)
        self.assertTrue((self.root/'.helper/state.json').is_file())
        record=self.manager._record(self.key);record['status']='running'
        atomic_json(self.manager.path(self.key),record)
        again=StorageUsage(e)
        self.assertEqual(again.get(self.key)['report']['status'],'interrupted')

    def test_scanner_exits_when_owning_server_disappears(self):
        self.engine();self.real_popen=subprocess.Popen
        with patch('alosstack_helper.storage_usage.subprocess.Popen',side_effect=self.stalled_worker):self.manager.start(self.key)
        child=self.manager.process
        os.close(self.manager.lifetime);self.manager.lifetime=None
        child.wait(timeout=5)
        self.assertNotEqual(child.returncode,0)
        self.assertEqual(self.manager.get(self.key)['report']['status'],'failed')

    def test_unknown_project_and_nonboolean_options_rejected(self):
        self.engine()
        for key in ('missing','../outside'):
            with self.assertRaises(ValueError):self.manager.get(key)
        with self.assertRaises(ValueError):self.manager.start(self.key,'false')

    def test_failed_rescan_keeps_last_complete_inventory(self):
        self.engine()
        previous=self.scan();atomic_json(self.manager.path(self.key),previous)
        self.root.rename(self.base/'away')
        self.manager.start(self.key)
        for _ in range(150):
            result=self.manager.get(self.key)
            if not result['busy']:break
            time.sleep(.02)
        self.assertEqual(result['report']['status'],'failed')
        self.assertEqual(result['previous'],previous)
        self.manager.start(self.key);self.manager.stop(self.key)
        self.assertEqual(self.manager.get(self.key)['previous'],previous)


if __name__=='__main__':unittest.main()
