"""The installed advanced test is isolated from user projects and environments."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.selftest import SelfTests, CASES


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = SimpleNamespace(home=self.root/'registry', lock=threading.Lock(), active=None)
        self.service = SelfTests(self.engine)
        self.prefix = self.root/'environment'; (self.prefix/'bin').mkdir(parents=True)
        (self.prefix/'bin/python').touch(); (self.prefix/'bin/conda').touch()

    def tearDown(self):
        self.service.shutdown()
        self.tmp.cleanup()

    def test_initial_state_and_missing_report_are_explicit(self):
        state=self.service.status()
        self.assertEqual(state['status'],'idle'); self.assertTrue(state['synthetic'])
        self.assertEqual(len(state['cases']),5)
        with self.assertRaises(ValueError): self.service.report()

    def test_existing_scientific_job_cannot_be_interrupted_or_overlapped(self):
        self.engine.active='real:analysis'
        with self.assertRaisesRegex(ValueError,'已有工作'): self.service.start(str(self.prefix))
        self.service.stop(); self.service.shutdown()
        self.assertEqual(self.engine.active,'real:analysis')
        self.assertFalse(self.service.home.exists())

    def test_missing_environment_does_not_reserve_engine(self):
        with self.assertRaises((ValueError,FileNotFoundError)): self.service.start(str(self.root/'absent'))
        self.assertIsNone(self.engine.active)

    def test_surviving_project_worker_blocks_selftest_without_project_writes(self):
        project=self.root/'real-project';run=project/'analyses/0123456789ab'
        run.mkdir(parents=True);(run/'worker.pid').write_text(str(os.getpid()))
        state=run/'analysis.json';state.write_text('{"status":"running","note":"preserve"}')
        before=state.read_bytes();self.engine.projects={'project':str(project)}
        with self.assertRaisesRegex(ValueError,'背景程序存活'):
            self.service.start(str(self.prefix))
        self.assertEqual(state.read_bytes(),before)
        self.assertIsNone(self.engine.active)
        self.assertFalse(self.service.home.exists())

    def test_worker_probe_is_read_only_and_allows_ended_processes(self):
        self.engine.projects={'project':str(self.root/'project')}
        with patch('alosstack_helper.recovery.process_alive',return_value=False) as probe, \
             patch('alosstack_helper.selftest.subprocess.Popen',side_effect=OSError('sentinel: worker start')):
            state=self.service.start(str(self.prefix),synchronous=True)
        probe.assert_called_once_with(self.root/'project/.helper')
        self.assertIn('sentinel: worker start',state['error'])
        self.assertIsNone(self.engine.active)

    def test_surviving_private_restore_also_blocks_selftest(self):
        project=self.root/'real-project'
        marker=project/'analyses/.recovery-staging/0123456789ab/operation.json'
        atomic_json(marker,dict(server_pid=os.getpid()))
        before=marker.read_bytes();self.engine.projects={'project':str(project)}
        with self.assertRaisesRegex(ValueError,'背景程序存活'):
            self.service.start(str(self.prefix))
        self.assertEqual(marker.read_bytes(),before)
        self.assertIsNone(self.engine.active)
        self.assertFalse(self.service.home.exists())

    def test_orphaned_status_is_not_a_pass(self):
        run='0123456789ab';folder=self.service.home/run;folder.mkdir(parents=True)
        atomic_json(self.service.state_file,dict(run_id=run,status='running',stage='running'))
        atomic_json(folder/'report.json',dict(status='running',checks=[dict(status='passed')],stage='partial'))
        self.assertEqual(self.service.status()['status'],'interrupted')
        self.assertFalse(self.service.report()['passed'])

    def test_cancelled_or_failed_report_never_claims_completion(self):
        run='0123456789ab';folder=self.service.home/run;folder.mkdir(parents=True)
        atomic_json(folder/'report.json',dict(status='passed',checks=[dict(status='passed')],stage='case passed'))
        for status in ('cancelled','failed'):
            atomic_json(self.service.state_file,dict(run_id=run,status=status,stage='stopped',error='not finished'))
            self.assertEqual(self.service.status()['stage'],'stopped')
            self.assertFalse(self.service.report()['passed'])

    def test_running_lock_blocks_second_manager(self):
        self.service.home.mkdir(parents=True)
        with (self.service.home/'run.lock').open('a') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'另一個工作台'): self.service.start(str(self.prefix))
            with self.assertRaisesRegex(ValueError,'另一個工作台'): self.service.stop()
        self.assertIsNone(self.engine.active)

    def test_subprocess_failure_persists_and_releases_reservation(self):
        with patch('alosstack_helper.selftest.subprocess.Popen',side_effect=OSError('worker unavailable')):
            state=self.service.start(str(self.prefix),synchronous=True)
        self.assertEqual(state['status'],'failed');self.assertIn('unavailable',state['error'])
        self.assertIsNone(self.engine.active)
        self.assertTrue(Path(state['environment']).samefile(self.prefix))

    def test_real_owned_child_is_stopped_and_other_engine_state_is_preserved(self):
        # No Conda mutation or scientific import is involved in this cancellation test.
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
        self.service.process=child
        try:
            self.engine.active='real:analysis'
            self.service.stop()
            self.assertIsNotNone(child.poll());self.assertTrue(self.service.cancelled.is_set())
            self.assertEqual(self.engine.active,'real:analysis')
        finally:
            if child.poll() is None: child.kill();child.wait()
            self.service.process=None

    def test_invalid_saved_run_id_cannot_read_an_external_report(self):
        atomic_json(self.service.state_file,dict(run_id='../external',status='passed'))
        self.assertNotIn('report_path',self.service.status())

    def test_cancel_during_worker_execution_survives_manager_restart(self):
        popen = subprocess.Popen
        def slow_worker(args, **kwargs):
            return popen([sys.executable,'-c','import time; time.sleep(60)'], **kwargs)
        with patch('alosstack_helper.selftest.subprocess.Popen',side_effect=slow_worker):
            self.service.start(str(self.prefix))
            deadline=time.monotonic()+5
            while self.service.process is None and time.monotonic()<deadline: time.sleep(.01)
            self.assertIsNotNone(self.service.process)
            self.service.stop();self.service.shutdown()
        restored=SelfTests(self.engine)
        self.assertEqual(restored.status()['status'],'cancelled')
        self.assertIsNone(self.engine.active)
        self.assertFalse(restored._locked())


@unittest.skipUnless(os.environ.get('ALOSSTACK_ADVANCED_SELFTEST')=='1','opt-in full native science self-test')
class NativeSuiteTests(unittest.TestCase):
    def test_known_answers_through_installed_science_pipeline(self):
        with tempfile.TemporaryDirectory(prefix='native-selftest-') as tmp:
            engine=SimpleNamespace(home=Path(tmp),lock=threading.Lock(),active=None)
            service=SelfTests(engine)
            try:
                state=service.start(sys.prefix,synchronous=True)
                self.assertEqual(state['status'],'passed',state.get('log_tail'))
                report=service.report()
                self.assertTrue(all(c['status']=='passed' for c in report['cases']))
                self.assertGreaterEqual(len(report['checks']),25)
                self.assertIsNone(engine.active)
            finally:service.shutdown()


if __name__=='__main__':unittest.main()
