"""Batch inventory, serial failure isolation, retained results and API boundaries."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from alosstack_helper.alos_preparation import AlosPreparation
from alosstack_helper import alos_preparation_worker as worker
from test_missions import make_img


NAME = 'ALOS41370480260327FWDPRA0107-1.1__-'


def product(folder, name=NAME):
    folder.mkdir(parents=True)
    for kind in ('LED-', 'VOL-', 'TRL-'):
        (folder / (kind+name)).write_bytes(kind.encode())
    for pol in ('HH', 'HV'):
        make_img(folder / ('IMG-'+pol+'-'+name))
    return folder / ('LED-'+name)


def read_fixture(path):
    return dict(path=str(path), sensor='ALOS-4', date='20260327', polarizations=['HH','HV'], metadata={},
                footprint=None, footprint_status='unknown', warnings=['Synthetic fixture'], errors=[])


def convert_fixture(config):
    output = Path(config['root']) / 'ALOS2_format'
    output.mkdir(parents=True)
    for path in Path(config['source']).iterdir():
        if path.name.startswith(('LED-', 'IMG-', 'VOL-', 'TRL-')):
            shutil.copyfile(path, output / path.name)
    (output / 'conversion.json').write_text(json.dumps(dict(source=config['source'])))
    return dict(output=str(output))


def quality_fixture(source, output, progress=None):
    """A complete audit-shaped fixture; actual CEOS reader tests live separately."""
    result = dict(status='passed', source=str(source), output=str(output),
                  scientific_validation=dict(status='not_performed'), fingerprints={})
    for phase in ('format', 'samples', 'metadata', 'reader', 'input_verification'):
        result[phase] = dict(status='passed')
    result['samples']['all_samples_equal'] = True
    for side, folder in (('source', source), ('output', output)):
        result['fingerprints'][side] = [dict(worker.file_stamp(p), name=p.name) for p in sorted(Path(folder).iterdir())
                                         if p.name.startswith(('LED-', 'IMG-', 'VOL-', 'TRL-'))]
    return result


def completed_fixture(source, destination, key):
    output = convert_fixture(dict(source=str(source), root=str(destination)))['output']
    quality = quality_fixture(source, output)
    return dict(id=key, status='complete', output=output, quality=quality,
                verified_directories={side:worker.directory_identity(quality[side]) for side in ('source', 'output')},
                conversion_manifest=worker.file_stamp(Path(output) / 'conversion.json'))


@unittest.skipUnless(sys.platform == 'darwin', 'macOS system aliases only')
class MacSystemAliases(unittest.TestCase):
    def test_default_tempfile_can_initialize_manager_without_resolve(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=AlosPreparation(Path(folder)/'state')
            self.assertEqual(manager.home,(Path(folder)/'state'/'alos-preparation').resolve())

    def test_var_and_tmp_aliases_are_normalized_without_resolving_children(self):
        for name in ('var','tmp'):
            with self.subTest(alias=name):
                self.assertEqual(worker.plain_path('/'+name+'/not-yet-created'),Path('/private')/name/'not-yet-created')
        with tempfile.TemporaryDirectory(dir='/tmp') as folder:
            target=Path(folder)/'real';target.mkdir()
            alias=Path(folder)/'custom-state';alias.symlink_to(target,target_is_directory=True)
            with self.assertRaisesRegex(ValueError,'連結'):worker.plain_path(alias/'child')
            with self.assertRaisesRegex(ValueError,'連結'):AlosPreparation(alias)

    def test_wrong_system_alias_target_is_not_whitelisted(self):
        with patch.object(worker.os,'readlink',return_value='/private/other'):
            with self.assertRaisesRegex(ValueError,'連結'):worker.plain_path('/tmp/work')

    def test_non_macos_does_not_whitelist_tmp_symlink(self):
        with patch.object(worker.sys,'platform','linux'):
            with self.assertRaisesRegex(ValueError,'連結'):worker.plain_path('/tmp/work')


class InventoryAndBatch(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve()
        self.root=self.base/'source'
        self.a=product(self.root/'a');self.b=product(self.root/'b')

    def scan(self):
        with patch.object(worker,'read_product',side_effect=read_fixture):
            return worker.scan(self.root)

    def request(self):
        report=self.scan()
        return dict(config=dict(source=str(self.root),root=str(self.base/'output'),tool='/verified-tool'),
                    report=report,selected=[p['id'] for p in report['products']],previous_items=[])

    def test_scan_keeps_dates_separate_and_does_not_create_output(self):
        events=[]
        with patch.object(worker,'read_product',side_effect=read_fixture):r=worker.scan(self.root,events.append)
        self.assertEqual(r['convertible_count'],2)
        self.assertEqual(len({p['id'] for p in r['products']}),2)
        self.assertFalse((self.base/'output').exists())
        self.assertTrue(all(p['manifest'] for p in r['products']))
        self.assertFalse(r['processing_unlocked'])
        self.assertEqual((events[-1]['completed'],events[-1]['total']),(2,2))

    def test_scan_detects_malformed_product_and_ignores_directory_links(self):
        (self.root/'linked').symlink_to(self.a.parent,target_is_directory=True)
        (self.b.parent/('IMG-HV-'+NAME)).unlink()
        r=self.scan();self.assertEqual(len(r['products']),2)
        self.assertEqual(r['convertible_count'],1)
        self.assertTrue(next(p for p in r['products'] if not p['convertible'])['reason'])

    def test_manifest_catches_metadata_changes_same_size_and_extra_polarization(self):
        p=self.scan()['products'][0]
        f=Path(p['path']);old=f.stat();f.write_bytes(b'BAD-');os.utime(f,ns=(old.st_atime_ns,old.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'變更'):worker.verify_manifest(p)
        f.write_bytes(b'LED-');os.utime(f,ns=(old.st_atime_ns,old.st_mtime_ns))
        make_img(f.parent/('IMG-VV-'+NAME))
        with self.assertRaisesRegex(ValueError,'變更'):worker.verify_manifest(p)

    def test_manifest_rejects_replaced_image_link(self):
        p=self.scan()['products'][0];f=Path(p['path']).parent/('IMG-HH-'+NAME)
        target=self.base/'elsewhere';target.write_bytes(f.read_bytes());f.unlink();f.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'連結'):worker.verify_manifest(p)

    def test_serial_queue_continues_after_one_product_fails(self):
        req=self.request();events=[];order=[]
        def convert(c):
            order.append(Path(c['source']).name)
            result=convert_fixture(c)
            if Path(c['source']).name=='a':raise ValueError('fixture conversion failed')
            return result
        with patch.object(worker,'verify_tool'),patch.object(worker,'run_conversion',side_effect=convert),patch.object(worker,'verify_conversion',side_effect=quality_fixture):
            result=worker.run_batch(req,events.append)
        self.assertEqual(order,['a','b'])
        self.assertEqual([i['status'] for i in result['items']],['failed','complete'])
        self.assertIn('fixture conversion failed',result['items'][0]['error'])
        self.assertFalse(result['processing_unlocked'])
        self.assertEqual(events[-1]['completed'],1)
        self.assertTrue((Path(req['config']['root'])/'batch-results.json').is_file())

    def test_quality_failure_never_marks_conversion_complete(self):
        req=self.request();req['selected']=req['selected'][:1]
        def convert(c):
            out=Path(c['root'])/'ALOS2_format';out.mkdir(parents=True);return dict(output=str(out))
        with patch.object(worker,'verify_tool'),patch.object(worker,'run_conversion',side_effect=convert),patch.object(worker,'verify_conversion',side_effect=ValueError('samples differ')):
            result=worker.run_batch(req,lambda v:None)
        self.assertEqual(result['items'][0]['status'],'failed')
        self.assertIn('samples differ',result['items'][0]['error'])

    def test_changed_source_and_existing_output_stop_before_conversion(self):
        req=self.request();self.a.write_bytes(b'changed')
        with patch.object(worker,'verify_tool'),patch.object(worker,'run_conversion') as convert:
            with self.assertRaises(ValueError):worker.run_batch(req,lambda v:None)
            convert.assert_not_called()
        self.assertFalse(Path(req['config']['root']).exists())

    def test_payload_change_with_restored_mtime_is_stale(self):
        p=self.scan()['products'][0]
        image=Path(p['path']).parent/('IMG-HH-'+NAME)
        saved=image.stat()
        with image.open('r+b') as stream:
            stream.seek(1521);stream.write(b'changed')
        os.utime(image,ns=(saved.st_atime_ns,saved.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'變更'):worker.verify_manifest(p)

    def test_ancestor_symlinks_and_output_overlap_are_rejected(self):
        alias=self.base/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'連結'):worker.scan(alias)
        req=self.request();req['config']['root']=str(alias/'output')
        with patch.object(worker,'verify_tool'),self.assertRaisesRegex(ValueError,'連結'):worker.run_batch(req,lambda v:None)
        req['config']['root']=str(self.root/'output')
        with patch.object(worker,'verify_tool'),self.assertRaisesRegex(ValueError,'包含'):worker.run_batch(req,lambda v:None)

    def test_incomplete_or_failed_quality_return_is_not_success(self):
        for index,quality in enumerate((dict(status='failed'),dict(status='passed'))):
            with self.subTest(quality=quality):
                req=self.request();req['selected']=req['selected'][:1];req['config']['root']=str(self.base/f'out-{index}')
                with patch.object(worker,'verify_tool'),patch.object(worker,'run_conversion',side_effect=convert_fixture),patch.object(worker,'verify_conversion',return_value=quality):
                    result=worker.run_batch(req,lambda v:None)
                self.assertEqual(result['items'][0]['status'],'failed')
                self.assertEqual(result['items'][0]['quality'],quality)

    def test_change_to_previous_output_during_later_conversion_invalidates_success(self):
        req=self.request();outputs=[]
        def convert(c):
            result=convert_fixture(c)
            if outputs:
                image=next(outputs[0].glob('IMG-*'));image.write_bytes(b'changed')
            outputs.append(Path(result['output']))
            return result
        with patch.object(worker,'verify_tool'),patch.object(worker,'run_conversion',side_effect=convert),patch.object(worker,'verify_conversion',side_effect=quality_fixture):
            result=worker.run_batch(req,lambda v:None)
        self.assertEqual([x['status'] for x in result['items']],['failed','complete'])
        self.assertEqual(result['items'][0]['quality']['status'],'passed')
        self.assertEqual(result['items'][0]['current_verification']['status'],'failed')

    def test_source_change_during_conversion_cannot_become_complete(self):
        req=self.request();req['selected']=req['selected'][:1]
        def convert(c):
            result=convert_fixture(c)
            leader=next(Path(c['source']).glob('LED-*'));leader.write_bytes(b'BAD-')
            return result
        with patch.object(worker,'verify_tool'),patch.object(worker,'run_conversion',side_effect=convert),patch.object(worker,'verify_conversion',side_effect=quality_fixture):
            result=worker.run_batch(req,lambda v:None)
        self.assertEqual(result['items'][0]['status'],'failed')
        self.assertIn('變更',result['items'][0]['error'])

    def test_coverage_rechecks_sources_after_calculation(self):
        req=self.request();req.update(mode='coverage',left_ids=req['selected'][:1],right_ids=req['selected'][1:],progress=None)
        def inspect(*args):
            self.a.write_bytes(b'BAD-')
            return dict(revision='stale',processing_unlocked=False)
        with patch.object(worker,'inspect_coverage',side_effect=inspect),self.assertRaisesRegex(ValueError,'變更'):worker.main(req)

    def test_scan_rejects_known_metadata_errors_as_convertible(self):
        def read(path):return dict(read_fixture(path),errors=['date disagrees'])
        with patch.object(worker,'read_product',side_effect=read):report=worker.scan(self.root)
        self.assertEqual(report['convertible_count'],0)

    def test_output_parent_replacement_and_old_attempt_do_not_create_output(self):
        req=self.request();req['config']['output_parent_identity']=dict(device=-1,inode=-1)
        with patch.object(worker,'verify_tool'),self.assertRaisesRegex(ValueError,'替換'):worker.run_batch(req,lambda v:None)
        req['config'].pop('output_parent_identity');req['attempted_roots']=[req['config']['root']]
        with patch.object(worker,'verify_tool'),self.assertRaisesRegex(ValueError,'重用'):worker.run_batch(req,lambda v:None)
        self.assertFalse(Path(req['config']['root']).exists())

    def test_malformed_selection_and_coverage_limit_fail_before_work(self):
        report=self.scan()
        with self.assertRaises(ValueError):worker.selected_products(report,[[]])
        with patch.object(worker,'MAX_PRODUCTS',1),self.assertRaisesRegex(ValueError,'最多'):worker.scan(self.root)

    def test_worker_exits_when_parent_pipe_closes(self):
        source=Path(worker.__file__).parent.parent
        code='from alosstack_helper.alos_preparation_worker import parent_watch; import time; parent_watch(0); time.sleep(15)'
        env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1')
        proc=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,env=env)
        try:
            proc.stdin.close()
            self.assertEqual(proc.wait(timeout=5),75)
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
            proc.stderr.close()


class PreparationManager(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.source=self.base/'source';self.source.mkdir()
        self.env=self.base/'env';(self.env/'bin').mkdir(parents=True);(self.env/'bin/python').touch()
        self.manager=AlosPreparation(self.base/'state')
        with patch('alosstack_helper.alos_preparation.find_conda',return_value='/fake/conda'),patch.object(self.manager,'_launch'):
            self.state=self.manager.create(dict(source=str(self.source),environment=str(self.env)))
        self.key=self.state['id'];self.s=self.manager.jobs[self.key]
        self.s.update(status='ready',report=dict(products=[dict(id='one',convertible=True),dict(id='two',convertible=True)]))

    def run(self, result=None):
        # Preserve unittest.TestCase.run; the helper below is called start_batch.
        return super().run(result)

    def start_batch(self,**overrides):
        d=dict(reviewed=True,product_ids=['one'],root=str(self.base/'out'),tool=str(self.base/'tool'))
        d.update(overrides)
        with patch('alosstack_helper.alos4_conversion.verify_tool'),patch.object(self.manager,'_launch'):
            return self.manager.start(self.key,d)

    def test_run_requires_review_and_known_convertible_ids(self):
        for value in (dict(reviewed=False),dict(product_ids=[]),dict(product_ids=['missing']),dict(product_ids=['one','one'])):
            with self.subTest(value=value),self.assertRaises(ValueError):self.start_batch(**value)

    def test_source_overlap_and_existing_directory_are_rejected(self):
        for root in (self.source/'out',self.base,self.env/'out',self.base/'tool'/'out'):
            with self.subTest(root=root),self.assertRaises(ValueError):self.start_batch(root=str(root))

    def test_retries_keep_passed_items_and_require_new_output(self):
        leader=product(self.source/'product')
        passed=completed_fixture(leader.parent,self.base/'earlier','one')
        (self.manager.folder(self.key)/'progress.json').write_text(json.dumps(dict(items=[passed,dict(id='two',status='failed',error='bad')])) )
        self.s['status']='partial'
        with self.assertRaisesRegex(ValueError,'已完成'):self.start_batch(product_ids=['one'])
        r=self.start_batch(product_ids=['two'])
        self.assertEqual(r['previous_items'][0]['status'],'complete')
        self.assertEqual(r['attempts'][0]['selected'],['two'])
        self.assertFalse((self.base/'out').exists())

    def test_modified_completed_output_becomes_retryable_at_a_new_location(self):
        leader=product(self.source/'product');passed=completed_fixture(leader.parent,self.base/'earlier','one')
        image=next(Path(passed['output']).glob('IMG-*'));image.write_bytes(b'changed')
        (self.manager.folder(self.key)/'progress.json').write_text(json.dumps(dict(items=[passed])))
        self.s['status']='complete'
        result=self.manager.get(self.key)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['progress']['items'][0]['current_verification']['status'],'failed')
        self.assertEqual(self.start_batch()['previous_items'][0]['status'],'failed')

    def test_retry_does_not_reuse_deleted_attempt_directory(self):
        self.s['attempts']=[dict(root=str(self.base/'out'))]
        with self.assertRaisesRegex(ValueError,'重用'):self.start_batch()

    def test_coverage_selection_cap_is_checked_before_worker(self):
        self.s['report']['products']=[dict(id=str(i)) for i in range(65)]
        with patch('alosstack_helper.alos_preparation.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'64'):self.manager.coverage(self.key,dict(left_ids=[str(i) for i in range(64)],right_ids=['64']))
        spawn.assert_not_called()

    def test_coverage_can_be_stopped_without_waiting_for_its_lock(self):
        started=threading.Event();released=threading.Event();errors=[]
        class Process:
            pid=99999998
            returncode=None
            def poll(self):return self.returncode
            def wait(self,timeout=None):
                started.set()
                if not released.wait(timeout or 2):raise subprocess.TimeoutExpired('fixture',timeout)
                self.returncode=-15;return self.returncode
        proc=Process()
        def run():
            try:self.manager.coverage(self.key,dict(left_ids=['one'],right_ids=['two']))
            except Exception as e:errors.append(str(e))
        self.s['review']=dict(revision='old',stale=False)
        with patch('alosstack_helper.alos_preparation.subprocess.Popen',return_value=proc),patch('alosstack_helper.nisar_jobs.os.killpg',side_effect=lambda *args:released.set()):
            thread=threading.Thread(target=run);thread.start()
            self.assertTrue(started.wait(2))
            before=time.monotonic();self.manager.stop()
            self.assertLess(time.monotonic()-before,1)
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertTrue(self.s['source_stale']);self.assertTrue(self.s['review']['stale'])
        self.assertIsNone(self.manager.active);self.assertIsNone(self.manager.process)

    def test_request_write_failure_releases_active_job(self):
        self.manager.active=self.key
        original=worker.write_json
        def write(path,value):
            if Path(path).name.endswith('-request.json'):raise OSError('fixture request denied')
            return original(path,value)
        with patch('alosstack_helper.alos_preparation.write_json',side_effect=write):self.manager._work(self.s,'check')
        self.assertEqual(self.s['status'],'failed')
        self.assertIn('fixture request denied',self.s['error'])
        self.assertIsNone(self.manager.active)

    def test_report_rejects_symlinked_job_folder(self):
        folder=self.manager.folder(self.key);other=self.base/'moved';folder.rename(other);folder.symlink_to(other,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'連結'):self.manager.get(self.key)

    def test_changed_coverage_source_invalidates_saved_human_review(self):
        product(self.source/'product')
        with patch.object(worker,'read_product',side_effect=read_fixture):report=worker.scan(self.source)
        self.s['report']=report
        self.s['review']=dict(selected_product_ids=[report['products'][0]['id']],stale=False,outcome='confirmed')
        self.assertFalse(self.manager.get(self.key)['review']['stale'])
        Path(report['products'][0]['path']).write_bytes(b'BAD-')
        current=self.manager.get(self.key)
        self.assertTrue(current['review']['stale']);self.assertTrue(current['source_stale'])
        self.assertFalse(current['processing_unlocked'])

    def test_restart_marks_partial_work_interrupted_and_preserves_inventory(self):
        self.s.update(status='running',pid=99999999)
        self.manager.save(self.s)
        (self.manager.folder(self.key)/'progress.json').write_text(json.dumps(dict(items=[dict(id='one',status='verifying')])))
        with patch.object(AlosPreparation,'_alive',return_value=False):restored=AlosPreparation(self.base/'state')
        state=restored.get(self.key)
        self.assertEqual(state['status'],'interrupted')
        self.assertEqual(state['progress']['items'][0]['status'],'stopped')
        self.assertEqual(len(state['report']['products']),2)

    def test_exports_escape_names_and_do_not_allow_arbitrary_paths(self):
        self.s['report']['products']=[dict(id='one',name='<script>alert(1)</script>',sensor='ALOS-4')]
        self.manager._export(self.key)
        text=self.manager.artifact(self.key,'report.html').read_text()
        self.assertIn('&lt;script&gt;',text);self.assertNotIn('<script>',text)
        with self.assertRaises(ValueError):self.manager.artifact(self.key,'../state.json')


if __name__=='__main__':unittest.main()
