"""Lossless project copying, interruption, destination safety and explicit switch."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.migration import Migrations, Transfer, MARKER, identity, directory, check_volume


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.root=self.base/'project';self.target=self.base/'drive';self.target.mkdir()
        self.key='abc123';self.e=Engine(self.base/'registry')
        self.state=dict(id=self.key,status='complete',stage='運算完成',history=[],config=dict(root=str(self.root),workflow='alosStack',name='研究'))
        atomic_json(self.root/'.helper/state.json',self.state);(self.root/'proc').mkdir()
        (self.root/'proc/output.unw').write_bytes(bytes(range(256))*100)
        (self.root/'proc/cmd.sh').write_text('original '+str(self.root))
        (self.root/'empty').mkdir()
        self.e.register(self.key,self.root);self.m=Migrations(self.e)

    def tearDown(self):self.m.stop();self.temp.cleanup()

    def wait(self):
        until=time.monotonic()+15
        while self.m.busy():
            if time.monotonic()>until:self.fail('worker timed out')
            time.sleep(.03)
        return self.m.get(self.key)['transfer']

    def plan(self):
        self.m.plan(self.key,str(self.target),'copy');s=self.wait()
        self.assertEqual(s['status'],'preview',s.get('error'))
        return s

    def copy(self):
        s=self.plan();self.m.start(self.key,s['transfer'],'copy',True);s=self.wait()
        self.assertEqual(s['status'],'ready',s.get('error'))
        return s

    def files(self,root):
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.is_symlink() and p.name!=MARKER}

    def test_verified_independent_copy_and_explicit_switch_preserve_original(self):
        before=self.files(self.root);s=self.copy();dest=Path(s['destination'])
        self.assertEqual(self.e.projects[self.key],str(self.root))
        self.assertEqual(before,self.files(dest));self.assertEqual(before,self.files(self.root))
        self.assertNotEqual((self.root/'proc/output.unw').stat().st_ino,(dest/'proc/output.unw').stat().st_ino)
        self.assertTrue((dest/'empty').is_dir());self.assertEqual(s['verified'],s['files'])
        self.m.start(self.key,s['transfer'],'switch',True);s=self.wait()
        self.assertEqual(s['status'],'complete',s.get('error'))
        self.assertEqual(self.e.projects[self.key],str(dest));self.assertEqual(before,self.files(self.root))
        self.assertTrue(self.e.get(self.key)['storage']['relocated'])
        with self.assertRaisesRegex(ValueError,'舊路徑'):self.e.require_processing_location(self.key)

    def test_preview_never_creates_destination_or_reads_external_contents(self):
        external=self.base/'outside';external.mkdir();(external/'secret').write_bytes(b'keep')
        self.state['config']['source']=str(external);atomic_json(self.root/'.helper/state.json',self.state)
        (self.root/'absolute-internal').symlink_to(self.root/'proc/output.unw')
        (self.root/'relative-internal').symlink_to('proc/output.unw')
        (self.root/'external').symlink_to('../outside')
        (self.root/'cycle').symlink_to('.')
        s=self.plan();self.assertFalse(Path(s['destination']).exists())
        self.assertEqual(s['external_links'],1);self.assertEqual(s['internal_links'],3)
        self.assertEqual(s['dependencies'][0]['path'],str(external))
        self.m.start(self.key,s['transfer'],'copy',True);s=self.wait();self.assertEqual(s['status'],'ready',s)
        d=Path(s['destination']);self.assertEqual((d/'absolute-internal').read_bytes(),(d/'proc/output.unw').read_bytes())
        self.assertEqual(os.readlink(d/'relative-internal'),'proc/output.unw')
        self.assertEqual((d/'external').resolve(),external)

    def test_existing_destination_and_source_overlaps_never_overwrite(self):
        dest=self.target/'copy';dest.mkdir();(dest/'mine').write_text('mine')
        self.m.plan(self.key,str(self.target),'copy');s=self.wait();self.assertEqual(s['status'],'failed')
        self.assertEqual((dest/'mine').read_text(),'mine')
        for parent,name in [(self.root,'nested'),(self.base,'project'),(self.e.home,'copy')]:
            with self.assertRaisesRegex(ValueError,'重疊'):self.m.plan(self.key,str(parent),name)
        for name in ['../copy','.','x/y','', '\x00']:
            with self.assertRaises(ValueError):self.m.plan(self.key,str(self.target),name)

    def test_source_change_after_preview_fails_before_copy(self):
        s=self.plan();(self.root/'proc/output.unw').write_bytes(b'changed')
        self.m.start(self.key,s['transfer'],'copy',True);s=self.wait()
        self.assertEqual(s['status'],'failed');self.assertIn('原檔案已變更',s['error']);self.assertFalse(Path(s['destination']).exists())

    def test_missing_source_or_added_file_is_not_silently_skipped(self):
        for change in ('add','remove'):
            with self.subTest(change=change):
                s=self.plan()
                if change=='add':(self.root/'new').write_text('new')
                else:(self.root/'new').unlink()
                self.m.start(self.key,s['transfer'],'copy',True);s=self.wait()
                self.assertEqual(s['status'],'failed');self.assertIn('清單',s['error'])

    def test_copy_resume_reuses_verified_files_and_recopies_owned_partial(self):
        s=self.plan();folder=self.m.folder/self.key
        t=Transfer(folder);real=t.hash_file;calls=[]
        def fail_after_publication(fd,row,source=False):
            calls.append(row['path'])
            if row['path']=='proc/output.unw' and not source:raise OSError('simulated interruption')
            return real(fd,row,source)
        with patch.object(t,'hash_file',side_effect=fail_after_publication):r=t.run('copy')
        self.assertEqual(r['status'],'failed');d=Path(r['destination']);before=(d/'proc/cmd.sh').stat().st_ino
        self.m.start(self.key,r['transfer'],'copy',True);r=self.wait();self.assertEqual(r['status'],'ready',r)
        self.assertEqual((d/'proc/cmd.sh').stat().st_ino,before)
        # Force an interruption during the streaming write, including its persisted identity.
        self.m.abandon(self.key,r['transfer']);self.m.plan(self.key,str(self.target),'another');r=self.wait()
        t=Transfer(folder);real=t.publish
        def interrupted(force=False,**kw):
            real(force,**kw)
            if kw.get('partial'):raise OSError('simulated mid-file interruption')
        with patch.object(t,'publish',side_effect=interrupted):r=t.run('copy')
        self.assertIsNotNone(r['partial']);self.m.start(self.key,r['transfer'],'copy',True);r=self.wait()
        self.assertEqual(r['status'],'ready',r);self.assertEqual(self.files(self.root),self.files(Path(r['destination'])))

    def test_tampered_destination_blocks_switch_even_when_size_is_equal(self):
        s=self.copy();dest=Path(s['destination']);p=dest/'proc/output.unw';p.write_bytes(b'X'*p.stat().st_size)
        self.m.start(self.key,s['transfer'],'switch',True);s=self.wait()
        self.assertEqual(s['status'],'failed');self.assertIn('驗證失敗',s['error']);self.assertEqual(self.e.projects[self.key],str(self.root))

    def test_symlink_or_hardlink_substitution_cannot_modify_source(self):
        s=self.copy();dest=Path(s['destination']);p=dest/'proc/output.unw';p.unlink();p.symlink_to(self.root/'proc/output.unw')
        before=self.files(self.root);self.m.start(self.key,s['transfer'],'switch',True);s=self.wait()
        self.assertEqual(s['status'],'failed');self.assertEqual(self.files(self.root),before)
        p.unlink();os.link(self.root/'proc/output.unw',p)
        # Direct verification isolates the destination hardlink rejection.
        t=Transfer(self.m.folder/self.key);r=t.run('switch');self.assertEqual(r['status'],'failed');self.assertIn('硬連結',r['error'])

    def test_replaced_destination_folder_is_not_touched(self):
        s=self.copy();dest=Path(s['destination']);dest.rename(self.target/'saved');dest.mkdir();(dest/'keep').write_text('mine')
        t=Transfer(self.m.folder/self.key);r=t.run('copy')
        self.assertEqual(r['status'],'failed');self.assertIn('替換',r['error']);self.assertEqual((dest/'keep').read_text(),'mine')

    def test_insufficient_space_stops_before_creating_destination(self):
        s=self.plan();t=Transfer(self.m.folder/self.key)
        class Empty: f_bavail=0;f_frsize=4096
        with patch('alosstack_helper.migration.os.statvfs',return_value=Empty()):r=t.run('copy')
        self.assertEqual(r['status'],'failed');self.assertIn('空間不足',r['error']);self.assertFalse(Path(s['destination']).exists())

    def test_unfinished_copy_cannot_be_attached_and_abandon_keeps_it(self):
        s=self.plan();d=Path(s['destination']);shutil.copytree(self.root,d)
        atomic_json(d/MARKER,dict(status='incomplete',id=self.key,transfer=s['transfer']))
        with self.assertRaisesRegex(ValueError,'尚未驗證'):self.e.attach_project(d,self.key)
        self.m.abandon(self.key,s['transfer']);self.assertTrue((d/'proc/output.unw').exists());self.assertIsNone(self.m.record(self.key))

    def test_processing_and_analysis_workers_block_planning(self):
        self.state['status']='ready';atomic_json(self.root/'.helper/state.json',self.state)
        with self.assertRaisesRegex(ValueError,'完成'):self.m.plan(self.key,str(self.target),'copy')
        self.state['status']='complete';atomic_json(self.root/'.helper/state.json',self.state)
        atomic_json(self.root/'analyses/aaaaaaaaaaaa/analysis.json',dict(status='running'))
        self.m.plan(self.key,str(self.target),'copy');s=self.wait();self.assertEqual(s['status'],'failed');self.assertIn('MintPy',s['error'])

    def test_changed_volume_and_symlink_ancestors_are_rejected(self):
        binding=dict(mount='/Volumes/Data',identity='uuid:expected')
        for mounts in [[],[dict(mount='/Volumes/Data',identity='uuid:wrong')]]:
            with patch('alosstack_helper.migration.mounted_volumes',return_value=mounts),self.assertRaisesRegex(ValueError,'身分'):check_volume(binding)
        (self.base/'link').symlink_to(self.root)
        with self.assertRaises(OSError):
            with directory(self.base/'link/proc'):pass

    def test_confirmation_and_stale_preview_are_required(self):
        s=self.plan()
        for token,consent in [('other',True),(s['transfer'],False)]:
            with self.assertRaises(ValueError):self.m.start(self.key,token,'copy',consent)
        with self.assertRaisesRegex(ValueError,'先完成'):self.m.start(self.key,s['transfer'],'switch',True)

    def test_interrupted_restart_never_automatically_switches(self):
        s=self.copy();s['status']='switch_ready';atomic_json(self.m.folder/self.key/'state.json',s)
        other=Migrations(self.e);self.assertEqual(other.get(self.key)['transfer']['status'],'paused')
        self.assertEqual(self.e.projects[self.key],str(self.root))

    def test_parent_lifetime_pipe_stops_worker_blocked_on_storage(self):
        self.plan();folder=self.m.folder/self.key
        reader,writer=os.pipe();env=os.environ.copy()
        env['PYTHONPATH']=str(Path(__file__).resolve().parents[1]/'src')
        code="import sys,time;from alosstack_helper import migration as m;m.Transfer.plan=lambda self: time.sleep(120);m.main()"
        process=subprocess.Popen([sys.executable,'-c',code,str(folder),'plan',str(reader)],env=env,pass_fds=(reader,))
        os.close(reader)
        try:
            os.close(writer);process.wait(timeout=5)
            self.assertNotEqual(process.returncode,0)
        finally:
            if process.poll() is None:process.kill();process.wait()

    def test_stop_is_bounded_and_keeps_destination(self):
        self.plan();self.m.process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
        self.m.active=self.key
        before=time.monotonic();self.m.stop(self.key)
        self.assertLess(time.monotonic()-before,3);self.assertIsNone(self.m.busy())
        self.assertEqual(self.m.record(self.key)['status'],'paused')
        self.assertEqual(self.e.projects[self.key],str(self.root))

    def test_orphan_lock_prevents_replacing_or_abandoning_its_journal(self):
        import fcntl
        s=self.plan();path=self.m.folder/self.key/'worker.lock'
        with path.open('a') as guard:
            fcntl.flock(guard,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'前一次'):self.m.plan(self.key,str(self.target),'another')
            with self.assertRaisesRegex(ValueError,'前一次'):self.m.abandon(self.key,s['transfer'])
        self.assertEqual(self.m.record(self.key)['transfer'],s['transfer'])


class MigrationAPI(unittest.TestCase):
    from test_missions import MissionAPI as BaseAPI
    request=BaseAPI.request
    def setUp(self):
        self.BaseAPI.setUp(self)
        self.key='migration123';self.root=self.base/'project';self.dest=self.base/'drive';self.dest.mkdir()
        atomic_json(self.root/'.helper/state.json',dict(id=self.key,status='complete',stage='完成',history=[],config=dict(root=str(self.root),workflow='alosStack',name='測試')))
        (self.root/'proc').mkdir();(self.root/'proc/output').write_bytes(b'original bytes')
        self.engine.register(self.key,self.root)
    def tearDown(self):self.BaseAPI.tearDown(self)
    def wait(self):
        until=time.monotonic()+10
        while True:
            r=self.request('/api/migration?id='+self.key)
            if not r['busy']:return r['transfer']
            if time.monotonic()>until:self.fail('API worker timed out')
            time.sleep(.03)
    def test_api_requires_auth_and_explicit_switch(self):
        from urllib.error import HTTPError
        d=dict(id=self.key,parent=str(self.dest),name='copy')
        with self.assertRaises(HTTPError) as error:self.request('/api/migration-plan',d,{'X-Token':'wrong'})
        self.assertEqual(error.exception.code,403)
        self.request('/api/migration-plan',d);s=self.wait();self.assertEqual(s['status'],'preview')
        d=dict(id=self.key,transfer=s['transfer'],consent=True)
        self.request('/api/migration-copy',d);s=self.wait();self.assertEqual(s['status'],'ready',s)
        self.assertEqual(self.engine.projects[self.key],str(self.root))
        self.request('/api/migration-switch',d);s=self.wait();self.assertEqual(s['status'],'complete')
        self.assertEqual((self.root/'proc/output').read_bytes(),b'original bytes')
    def test_concurrent_writes_and_other_science_jobs_are_gated(self):
        from urllib.error import HTTPError
        with patch.object(self.server.migrations,'busy',return_value=self.key):
            for path,d in [('/api/project-remove',dict(id=self.key)),('/api/analysis-rename',dict(id=self.key)),('/api/action',dict(id=self.key,action='run')),('/api/geo-export',dict(id=self.key))]:
                with self.subTest(path=path),self.assertRaises(HTTPError) as error:self.request(path,d)
                self.assertIn('安全搬移',json.loads(error.exception.read())['error'])
        self.engine.active='another'
        with self.assertRaises(HTTPError):self.request('/api/migration-plan',dict(id=self.key,parent=str(self.dest),name='copy'))
        self.engine.active=None


if __name__=='__main__':unittest.main()
