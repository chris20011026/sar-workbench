"""Process and API boundaries for portable copies, independent of user data."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.portable_jobs import PortableJobs
from alosstack_helper.selftest_fixture import dataset
from test_operations_server import OperationsAPI


class PortableAPI(OperationsAPI):
    def test_portable_auth_and_idle(self):
        for path,body in [('/api/portable-provenance?id=missing',None),('/api/portable-latest',None),('/api/portable?job=bad',None),('/api/portable-export-preview',{}),
                          ('/api/portable-import-preview',{}),('/api/portable-start',{}),('/api/portable-stop',{})]:
            for headers in ({'X-Token':''},{'Origin':'https://outside.example'},{'Host':'outside.example'}):
                self.reject(path,body,headers,403)
        with self.request('/api/portable-latest') as response:self.assertEqual(json.load(response),{'job':None})
        self.assertFalse((self.engine.home/'portable-jobs').exists())

    def test_portable_busy_and_stop_routing(self):
        with patch.object(self.server.portable,'busy',return_value=True),patch.object(self.server.portable,'stop',return_value={'status':'stopped'}) as stop:
            for path in ('/api/create','/api/analysis-create','/api/setup-start','/api/migration-plan','/api/portable-start','/api/legacy-create'):
                with self.assertRaises(Exception) as error:self.request(path,{})
                text=error.exception.read().decode();error.exception.close()
                self.assertIn('可攜分析包正在',text);self.assertNotIn('NameError',text)
            with self.request('/api/portable-stop',{'job_id':'sample'}) as response:self.assertEqual(json.load(response)['status'],'stopped')
            stop.assert_called_once_with('sample')
            with self.request('/api/state') as response:self.assertTrue(json.load(response)['busy'])

    def test_portable_launch_respects_other_workers(self):
        blockers=[lambda:patch.object(self.engine,'active','science'),lambda:patch.object(self.server.nisar_jobs,'active','science'),
                  lambda:patch.object(self.server.conversion_jobs,'active','science'),lambda:patch.object(self.server.alos_batches,'active','science'),
                  lambda:patch.object(self.server.migrations,'busy',return_value='copy'),lambda:patch.object(self.server.selftests,'status',return_value={'status':'running'}),
                  lambda:patch.object(self.server.onboarding.runtime,'status',return_value={'status':'running'})]
        with patch.object(self.server.portable,'preview') as start:
            for blocker in blockers:
                with blocker():self.reject('/api/portable-export-preview',{'id':'missing'})
            start.assert_not_called()


class PortableJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        request,pairs=dataset(self.root)
        self.env=request['environment'];self.source=Path(request['source']).parent
        self.state=dict(id='completedtest',status='complete',stage='已完成',history=[],expected_pairs=pairs,
            config=dict(root=str(self.source),env=self.env,name='合成已完成成果',workflow='alosStack',reference='181103'))
        atomic_json(self.source/'.helper/state.json',self.state)
        self.engine=Engine(self.root/'registry');self.engine.register(self.state['id'],str(self.source))
        self.service=PortableJobs(self.engine)

    def tearDown(self):self.service.shutdown();self.tmp.cleanup()

    def wait(self,token):
        deadline=time.monotonic()+25
        while token in self.service.processes and time.monotonic()<deadline:time.sleep(.03)
        self.assertNotIn(token,self.service.processes)
        return self.service.get(token)

    def test_export_import_after_source_and_package_moved(self):
        start=self.service.preview('export',dict(id=self.state['id'],parent=str(self.root),name='portable'))
        token=start['job_id'];job=self.wait(token);self.assertEqual(job['status'],'ready',job)
        with self.assertRaisesRegex(ValueError,'確認'):self.service.start(token,False)
        self.service.start(token,True);job=self.wait(token);self.assertEqual(job['status'],'complete',job)
        self.source.rename(self.root/'old-source-offline')
        moved=self.root/'new-disk-package';(self.root/'portable').rename(moved)
        token=self.service.preview('import',dict(source=str(moved),parent=str(self.root),folder='new-analysis',name='新位置',environment=self.env))['job_id']
        job=self.wait(token);self.assertEqual(job['status'],'ready',job)
        self.service.start(token,True);job=self.wait(token);self.assertEqual(job['status'],'complete',job)
        imported=self.engine.get(job['project_id']);self.assertTrue(imported.get('portable_import'));self.assertEqual(imported['status'],'imported')
        moved.rename(self.root/'package-offline')
        from alosstack_helper.legacy import require_legacy_source
        from alosstack_helper.workflows import inventory
        require_legacy_source(imported);self.assertEqual(len(inventory(imported)['pairs']),3)
        self.assertFalse(imported['storage']['relocated']);self.assertIsNone(self.engine.active)
        self.assertEqual(job['progress']['current'],'獨立專案建立完成，等待品質檢查')

    def test_reject_bad_destination_and_concurrent_work(self):
        for name in ('..','../outside','x/y','x\\y',''):
            with self.assertRaises(ValueError):self.service.preview('export',dict(id=self.state['id'],parent=str(self.root),name=name))
        self.engine.active='science'
        with self.assertRaises(ValueError):self.service.preview('export',dict(id=self.state['id'],parent=str(self.root),name='blocked'))
        self.assertFalse((self.root/'blocked').exists())

    def test_orphan_lock_is_busy_and_does_not_claim_interruption(self):
        folder=self.service.home;folder.mkdir()
        token='f'*24;(folder/token).mkdir();atomic_json(folder/token/'job.json',dict(job_id=token,status='running',mode='export'))
        with (folder/'worker.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertTrue(self.service.busy());self.assertEqual(self.service.get(token)['status'],'running')
            with self.assertRaises(ValueError):self.service.preview('export',dict(id=self.state['id'],parent=str(self.root),name='blocked'))
        self.assertEqual(self.service.get(token)['status'],'interrupted')
        self.assertEqual(json.loads((folder/token/'job.json').read_text())['status'],'running')

    def test_stop_before_spawn_leaves_source_and_no_registration(self):
        with patch('alosstack_helper.portable_jobs.threading.Thread') as thread:
            job=self.service.preview('export',dict(id=self.state['id'],parent=str(self.root),name='never'))
        # Inject cancellation before the worker sees its Popen handle.
        self.service.stop(job['job_id'])
        self.assertIn(job['job_id'],self.service.cancelled)
        # Now let the reserved worker start and observe the earlier cancellation.
        self.service._worker(*thread.call_args.kwargs['args'])
        self.assertEqual(self.service.get(job['job_id'])['status'],'stopped')
        self.assertFalse(self.service.busy());self.assertIsNone(self.engine.active)
        self.assertEqual(len(self.engine.projects),1);self.assertFalse((self.root/'never').exists())


if __name__=='__main__':unittest.main()
