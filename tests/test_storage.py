"""Offline media, identity collisions and project moves must never change science files."""
import copy
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.storage import mounted_volumes, network_identity, read_project, atomic_existing_json


class ExternalProjects(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.mount=self.base/'Disk'; self.root=self.mount/'research/project'; self.key='aabb00112233'
        self.volumes=[dict(mount=str(self.mount),label='Research disk',identity='uuid:original',kind='disk',filesystem='apfs')]
        self.mock=patch('alosstack_helper.storage.mounted_volumes',side_effect=lambda:copy.deepcopy(self.volumes)); self.mock.start()
        self.e=Engine(self.base/'registry')
        self.state=dict(id=self.key,status='ready',stage='已產生指令',history=[],config=dict(name='原研究名稱',root=str(self.root),workflow='alosStack'))
        atomic_json(self.root/'.helper/state.json',self.state)
        (self.root/'proc').mkdir();(self.root/'proc/cmd_1.sh').write_text('original science commands\n')
        self.e.register(self.key,self.root)

    def tearDown(self):self.mock.stop();self.temp.cleanup()

    def refresh(self):self.e.storage.mounts(force=True)

    def test_unplug_and_reconnect_same_location_recovers_without_import(self):
        self.e.edit_project(self.key,'台北盆地','外接硬碟上的研究')
        self.volumes=[];self.refresh()
        self.assertEqual(self.e.list(),[])
        row=self.e.unavailable()[0]
        self.assertEqual(row['status'],'offline');self.assertEqual(row['name'],'台北盆地')
        self.assertEqual(row['last_stage'],'已產生指令');self.assertIn(self.key,self.e.projects)
        self.volumes=[dict(mount=str(self.mount),label='Research disk',identity='uuid:original',kind='disk')];self.refresh()
        self.assertEqual(self.e.unavailable(),[])
        self.assertEqual(self.e.get(self.key)['display_name'],'台北盆地')
        self.assertEqual(self.e.get(self.key)['status'],'ready')
        self.assertIsNone(self.e.active)

    def test_changed_mount_is_found_by_uuid_and_project_id_without_rewriting_science(self):
        moved=self.base/'Disk 1';self.mount.rename(moved);new=moved/'research/project'
        self.volumes[0]['mount']=str(moved);self.refresh()
        row=self.e.unavailable()[0]
        self.assertEqual(row['status'],'relocated');self.assertEqual(row['candidates'],[str(new)])
        self.assertEqual(self.e.projects[self.key],str(self.root))
        before={str(p.relative_to(new)):p.read_bytes() for p in new.rglob('*') if p.is_file()}
        preview=self.e.preview_project(new,self.key);self.assertTrue(preview['relocated'])
        self.e.attach_project(new,self.key)
        self.assertEqual(self.e.projects[self.key],str(new));self.assertTrue(self.e.get(self.key)['storage']['relocated'])
        self.assertEqual(before,{str(p.relative_to(new)):p.read_bytes() for p in new.rglob('*') if p.is_file()})
        with self.assertRaisesRegex(ValueError,'舊路徑'):self.e.require_processing_location(self.key)
        self.assertFalse(self.mount.exists())

    def test_same_label_and_project_copy_on_wrong_disk_are_not_opened(self):
        self.volumes[0]['identity']='uuid:different';self.refresh()
        self.assertEqual(self.e.unavailable()[0]['status'],'wrong_disk')
        with self.assertRaisesRegex(ValueError,'另一個磁碟'):self.e.get(self.key)
        with self.assertRaises(ValueError):self.e.save(self.state)

    def test_invalid_project_metadata_stays_visible_without_breaking_listing(self):
        for change in ({'config':{'root':None}}, {'history':None}, {'config':{'root':str(self.root),'workflow':[]}}):
            with self.subTest(change=change):
                atomic_json(self.root/'.helper/state.json',{**self.state,**change})
                self.assertEqual(self.e.list(),[])
                self.assertEqual(self.e.unavailable()[0]['status'],'unavailable')

    def test_multiple_matching_copies_need_explicit_selection(self):
        choices=[]
        for name in ('CopyA','CopyB'):
            mount=self.base/name;shutil.copytree(self.mount,mount)
            choices.append(dict(self.volumes[0],mount=str(mount)))
        self.volumes=choices;self.refresh()
        row=self.e.unavailable()[0]
        self.assertEqual(len(row['candidates']),2);self.assertEqual(row['status'],'relocated')
        self.assertEqual(self.e.projects[self.key],str(self.root))

    def test_matching_volume_with_wrong_project_id_is_not_a_candidate(self):
        moved=self.base/'Moved';self.mount.rename(moved);self.volumes[0]['mount']=str(moved)
        state={**self.state,'id':'other'};atomic_json(moved/'research/project/.helper/state.json',state);self.refresh()
        self.assertEqual(self.e.unavailable()[0]['candidates'],[])
        with self.assertRaisesRegex(ValueError,'另一個專案'):self.e.attach_project(moved/'research/project',self.key)

    def test_missing_stable_volume_identity_never_guesses_by_label(self):
        self.volumes[0]['identity']=None;self.refresh()
        # A new registration can use a volume without UUID, but cannot auto-match it later.
        self.e.storage.entries={};self.e.register(self.key,self.root)
        moved=self.base/'Renamed';self.mount.rename(moved);self.volumes[0]['mount']=str(moved);self.refresh()
        self.assertEqual(self.e.unavailable()[0]['candidates'],[])
        self.e.attach_project(moved/'research/project',self.key)
        self.assertTrue(self.e.get(self.key)['storage']['relocated'])

    def test_metadata_edit_is_available_offline_and_does_not_change_scientific_config(self):
        before=(self.root/'.helper/state.json').read_bytes()
        self.volumes=[];self.refresh();self.e.edit_project(self.key,'新的名稱','研究備註')
        again=Engine(self.e.home)
        self.assertEqual(again.unavailable()[0]['name'],'新的名稱')
        self.assertEqual(again.unavailable()[0]['notes'],'研究備註')
        self.assertEqual((self.root/'.helper/state.json').read_bytes(),before)
        for name,notes in [('', ''),('a'*101,''),('ok','n'*2001)]:
            with self.assertRaises(ValueError):self.e.edit_project(self.key,name,notes)

    def test_existing_project_import_is_deduplicated_and_does_not_run_or_copy(self):
        other=Engine(self.base/'other-registry')
        before=(self.root/'.helper/state.json').read_bytes()
        preview=other.preview_project(self.root)
        other.attach_project(self.root,preview['id']);other.attach_project(self.root,preview['id'])
        self.assertEqual(len(other.projects),1);self.assertIsNone(other.active)
        self.assertEqual((self.root/'.helper/state.json').read_bytes(),before)
        with self.assertRaises((ValueError,OSError)):other.preview_project(self.root/'proc')

    def test_running_project_and_orphan_worker_block_relink(self):
        self.e.active=self.key+':analysis'
        with self.assertRaisesRegex(ValueError,'正在執行'):self.e.attach_project(self.root,self.key)
        self.e.active=None
        atomic_json(self.root/'.helper/worker-process.json',dict(pgid=os.getpgrp()))
        with self.assertRaisesRegex(ValueError,'背景程序'):self.e.attach_project(self.root,self.key)

    def test_input_disk_offline_does_not_hide_project_but_blocks_processing(self):
        inputs=self.base/'Inputs';(inputs/'data').mkdir(parents=True)
        self.volumes.append(dict(mount=str(inputs),label='Image disk',identity='uuid:images',kind='disk'))
        self.state['config']['source']=str(inputs/'data');atomic_json(self.root/'.helper/state.json',self.state)
        self.refresh();self.e.get(self.key)
        self.volumes.pop();self.refresh()
        self.assertEqual(len(self.e.list()),1)
        self.assertEqual(self.e.project_details(self.key)['paths'][0]['status'],'offline')
        with self.assertRaisesRegex(ValueError,'原始資料'):self.e.require_processing_location(self.key)

    def test_readable_but_replaced_state_is_rejected(self):
        atomic_json(self.root/'.helper/state.json',{**self.state,'id':'replacement'})
        with self.assertRaisesRegex(ValueError,'另一個專案'):self.e.get(self.key)
        with self.assertRaises(ValueError):self.e.save(self.state)

    def test_worker_disconnect_preserves_local_incident_and_never_recreates_project(self):
        self.state['config']['workflow']='alos2App';atomic_json(self.root/'.helper/state.json',self.state)
        state=self.e.get(self.key);self.e.active=self.key
        away=self.base/'Disconnected'
        def disconnect(*_):
            self.mount.rename(away);self.volumes=[];self.refresh();raise OSError('device disconnected')
        with patch('alosstack_helper.pair.PairWorkflow.worker',side_effect=disconnect):self.e.worker(state,'run')
        self.assertIsNone(self.e.active);self.assertFalse(self.mount.exists())
        self.assertTrue((self.e.home/'interrupted-projects'/(self.key+'.json')).is_file())
        away.rename(self.mount);self.volumes=[dict(mount=str(self.mount),label='disk',identity='uuid:original',kind='disk')];self.refresh()
        self.assertEqual(self.e.get(self.key)['status'],'failed')
        self.assertIn('不會自動續跑',self.e.get(self.key)['error'])


class MountIdentification(unittest.TestCase):
    def test_mac_uses_filesystem_uuid_and_network_identity(self):
        listing='/dev/disk3 on / (apfs, local)\n/dev/disk8s1 on /Volumes/Research Disk (apfs, local)\n//user:secret@nas/share on /Volumes/NAS (smbfs, nodev)\n'
        def command(argv,**_):
            return subprocess.CompletedProcess(argv,0,stdout=listing if argv==['/sbin/mount'] else plistlib.dumps(dict(VolumeUUID='ABC-DEF',VolumeName='Research Disk')))
        with patch('alosstack_helper.storage.sys.platform','darwin'),patch('alosstack_helper.storage.subprocess.run',side_effect=command):rows=mounted_volumes()
        self.assertEqual(len(rows),2);self.assertEqual(rows[0]['identity'],'uuid:abc-def')
        self.assertEqual(rows[1]['kind'],'network');self.assertNotIn('secret',json.dumps(rows))
        self.assertEqual(network_identity('//one@nas/share','smbfs'),network_identity('//two@nas/share','smbfs'))

    def test_diskutil_failure_leaves_identity_unknown_not_fabricated(self):
        def command(argv,**_):
            if argv==['/sbin/mount']:return subprocess.CompletedProcess(argv,0,stdout='/dev/disk8 on /Volumes/Data (exfat, local)\n')
            raise subprocess.TimeoutExpired(argv,2)
        with patch('alosstack_helper.storage.sys.platform','darwin'),patch('alosstack_helper.storage.subprocess.run',side_effect=command):rows=mounted_volumes()
        self.assertIsNone(rows[0]['identity'])

    def test_atomic_write_never_creates_a_disconnected_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'disconnected/project/.helper/state.json'
            with self.assertRaises(OSError):atomic_existing_json(path,{'test':True})
            self.assertFalse(path.parent.exists())

    def test_project_identity_reader_rejects_state_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.helper').mkdir();(root/'data').write_text('{}')
            (root/'.helper/state.json').symlink_to(root/'data')
            with self.assertRaisesRegex(ValueError,'符號連結'):read_project(root)


if __name__=='__main__':unittest.main()
