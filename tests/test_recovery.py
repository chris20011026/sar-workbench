import json
import os
from pathlib import Path
import signal
import subprocess
import unittest
from unittest.mock import patch

import test_workflow
from alosstack_helper.core import atomic_json, digest
from alosstack_helper.recovery import Recovery, process_alive


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_workflow.EngineTests();self.fixture.setUp()
        self.e=self.fixture.e;self.s=self.fixture.s;self.root=self.fixture.root
        self.fixture.make_review()
        env=self.fixture.base/'env';(env/'bin').mkdir(parents=True)
        (env/'bin/python').write_text('python')
        (env/'conda-meta').mkdir();(env/'conda-meta/test.json').write_text('{"subdir":"osx-arm64"}')
        source=self.fixture.base/'data';source.mkdir();(source/'source.bin').write_bytes(b'original source')
        self.s['config'].update(env=str(env),source=str(source),data=str(source))
        self.s.update(recovery_enabled=True,ion={'estimate':True,'apply':True})
        for name in ['cmd_1.sh','cmd_2.sh','cmd_4.sh','helper_cmd3_before_review.sh']:
            (self.root/'proc'/name).write_text('#!/bin/bash\ntrue\n')
        self.s['hashes']={p.name:digest(p) for p in (self.root/'proc').glob('*.sh')}
        self.e.save(self.s)
        self.rec=Recovery(self.e,self.s)

    def tearDown(self): self.fixture.tearDown()

    def resume(self):
        self.s=self.e.get('test');self.e.active='test';self.e.cancel.clear()
        self.e.recover_worker(self.s,self.s['recovery']['id'])
        self.s=self.e.get('test')

    def checkpoint(self,phase='cmd2 · 干涉配對',action='run'):
        self.rec.capture(phase,action);self.s.update(status='failed',failed_phase=phase)
        self.e.save(self.s)
        return self.s['recovery']['id']

    def test_cmd2_failure_restores_baseline_and_skips_cmd1(self):
        calls=[]
        def run(s,p):
            calls.append(p.name)
            if p.name=='cmd_1.sh':(self.root/'proc/completed-one').write_text('preserved')
            if p.name=='cmd_2.sh':
                (self.root/'proc/partial').write_text('broken')
                raise RuntimeError('synthetic failure')
        self.e.shell=run;self.e.worker(self.s,'run')
        failed=self.e.get('test');self.assertEqual(failed['status'],'failed')
        self.assertEqual(len(failed['history']),1)
        def resumed(s,p):
            calls.append(p.name)
            self.assertFalse((self.root/'proc/partial').exists())
            self.assertEqual((self.root/'proc/completed-one').read_text(),'preserved')
        self.e.shell=resumed;self.resume()
        self.assertEqual(self.s['status'],'review')
        self.assertEqual(calls,['cmd_1.sh','cmd_2.sh','cmd_2.sh','helper_cmd3_before_review.sh'])
        archive=Path(self.s['recovery_attempt'])/'failed-proc'
        self.assertEqual((archive/'partial').read_text(),'broken')
        self.assertEqual(len([h for h in self.s['history'] if h['stage']=='cmd1 · 對位與幾何']),1)

    def test_ion_correction_is_not_applied_twice(self):
        phase=self.root/'proc/phase.txt';phase.write_text('0');calls=[];fail=True
        def run(s,args,**kw):
            nonlocal fail
            if str(args[1]).endswith('ion_ls.py'):
                d=self.root/'proc/dates_ion';d.mkdir(exist_ok=True)
                for date in ('150206','151113','160219'):(d/f'filt_ion_{date}_8rlks_12alks.ion').write_bytes(b'ion')
            if str(args[1]).endswith('ion_correct.py'):
                pair=args[args.index('-ref_date')+1]+'-'+args[args.index('-sec_date')+1]
                calls.append(pair)
                phase.write_text(str(int(phase.read_text())+1))
                if fail: fail=False;raise RuntimeError('correction interrupted')
        self.e.run=run
        def final(s,p):
            for pair in (self.root/'proc/pairs').iterdir():(pair/'filt.unw.geo').write_bytes(b'out')
        self.e.shell=final;self.e.worker(self.s,'continue')
        self.assertEqual(phase.read_text(),'1')
        self.resume()
        self.assertEqual(self.s['status'],'complete')
        self.assertEqual(phase.read_text(),'3') # three pairs, never four corrections
        self.assertEqual((Path(self.s['recovery_attempt'])/'failed-proc/phase.txt').read_text(),'1')
        self.assertEqual(len([h for h in self.s['history'] if h['stage']=='估計各日期電離層']),1)

    def test_failed_final_validation_restores_before_cmd4(self):
        self.s['ion']['estimate']=False;self.s['expected_pairs']=['150206-151113']
        self.e.shell=lambda s,p:None
        self.e.worker(self.s,'run')
        self.assertEqual(self.e.get('test')['status'],'failed')
        calls=[]
        def final(s,p):
            calls.append(p.name)
            (self.root/'proc/pairs/150206-151113/out.unw.geo').write_bytes(b'valid')
        self.e.shell=final;self.resume()
        self.assertEqual(calls,['cmd_4.sh']);self.assertEqual(self.s['status'],'complete')

    def test_snapshot_tampering_never_moves_failed_files(self):
        ident=self.checkpoint();p=self.rec.checkpoint_path(ident)/'proc/cmd_1.sh';p.write_text('corrupt')
        (self.root/'proc/partial').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'內容已變動'):self.rec.restore(ident)
        self.assertTrue((self.root/'proc/partial').exists())

    def test_changed_input_environment_commands_and_exclusions_block(self):
        ident=self.checkpoint()
        source=Path(self.s['config']['source'])/'source.bin';source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'輸入或環境'):self.rec.restore(ident)
        self.rec.capture('cmd2 · 干涉配對','run');ident=self.s['recovery']['id']
        (Path(self.s['config']['env'])/'conda-meta/test.json').write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError,'輸入或環境'):self.rec.restore(ident)
        self.rec.capture('cmd2 · 干涉配對','run');ident=self.s['recovery']['id']
        (self.root/'proc/cmd_2.sh').write_text('edited')
        with self.assertRaisesRegex(ValueError,'指令.*變更'):self.rec.restore(ident)
        self.s['excluded']=['150206-160219']
        self.assertFalse(self.rec.report()['can_recover'])

    def test_legacy_project_no_snapshot_and_wrong_workflow_block(self):
        self.s['status']='failed';self.assertFalse(self.rec.report()['can_recover'])
        self.checkpoint();self.s['config']['workflow']='alos2App'
        self.assertFalse(self.rec.report()['can_recover'])

    def test_live_process_group_blocks_even_after_service_restart(self):
        self.checkpoint()
        p=subprocess.Popen(['/bin/sleep','30'],start_new_session=True)
        try:
            atomic_json(self.root/'.helper/worker-process.json',dict(pgid=p.pid))
            self.assertTrue(process_alive(self.root/'.helper'))
            self.assertFalse(self.rec.report()['can_recover'])
            with self.assertRaises(ValueError):self.e.launch('test','recover',options={'confirmed':True})
        finally:os.killpg(p.pid,signal.SIGTERM);p.wait()
        self.assertFalse(process_alive(self.root/'.helper'))

    def test_cancel_snapshot_and_low_space_preserve_processing(self):
        self.e.cancel.set()
        with self.assertRaises(RuntimeError):self.rec.capture('cmd2 · 干涉配對','run')
        self.e.cancel.clear()
        with patch('alosstack_helper.recovery.shutil.disk_usage') as disk:
            disk.return_value.free=0
            with self.assertRaisesRegex(ValueError,'空間不足'):self.rec.capture('cmd2 · 干涉配對','run')
        self.assertNotIn('recovery',self.s);self.assertTrue((self.root/'proc/cmd_1.sh').exists())

    def test_interrupted_restore_directory_swap_can_finish(self):
        ident=self.checkpoint();(self.root/'proc/partial').write_text('broken')
        rename=Path.rename
        def fail(src,dst):
            if src.name=='restored':raise OSError('synthetic service interruption')
            return rename(src,dst)
        with patch.object(Path,'rename',fail):
            with self.assertRaises(OSError):self.rec.restore(ident)
        self.assertFalse((self.root/'proc').exists())
        self.assertTrue((self.root/'.helper/recovery/restore.json').exists())
        self.rec.restore(ident)
        self.assertTrue((self.root/'proc/cmd_1.sh').exists())
        self.assertFalse((self.root/'proc/partial').exists())
        self.assertEqual((Path(self.s['recovery_attempt'])/'failed-proc/partial').read_text(),'broken')

    def test_copies_are_independent_and_external_links_protected(self):
        p=self.root/'proc/value';p.write_text('before');ident=self.checkpoint()
        p.write_text('after')
        self.assertEqual((self.rec.checkpoint_path(ident)/'proc/value').read_text(),'before')
        self.assertNotEqual(p.stat().st_ino,(self.rec.checkpoint_path(ident)/'proc/value').stat().st_ino)
        (self.root/'proc/unsafe').symlink_to('/etc')
        with self.assertRaisesRegex(ValueError,'外部連結'):self.rec.capture('cmd2 · 干涉配對','run')

    def test_truncated_raster_is_not_a_valid_baseline(self):
        p=self.root/'proc/result.int';p.write_bytes(b'1234')
        Path(str(p)+'.xml').write_text('<imageFile><property name="width"><value>2</value></property><property name="length"><value>2</value></property><property name="data_type"><value>CFLOAT</value></property></imageFile>')
        with self.assertRaisesRegex(ValueError,'影像大小'):self.rec.capture('cmd2 · 干涉配對','run')

    def test_number_bands_and_internal_raster_links(self):
        p=self.root/'proc/result.unw';p.write_bytes(b'\0'*32)
        Path(str(p)+'.xml').write_text('<imageFile><property name="width"><value>2</value></property><property name="length"><value>2</value></property><property name="number_bands"><value>2</value></property><property name="data_type"><value>FLOAT</value></property></imageFile>')
        link=self.root/'proc/linked.unw';link.symlink_to(p)
        Path(str(link)+'.xml').write_bytes(Path(str(p)+'.xml').read_bytes())
        ident=self.checkpoint();p.write_bytes(b'broken')
        self.rec.restore(ident)
        self.assertEqual(p.stat().st_size,32)
        self.assertEqual(self.rec.check_rasters(self.root/'proc'),2)

    def test_first_snapshot_failure_allows_opt_out_before_science(self):
        calls=[];self.e.shell=lambda *a:calls.append(a)
        with patch('alosstack_helper.recovery.shutil.disk_usage') as disk:
            disk.return_value.free=0;self.e.worker(self.s,'run')
        self.s=self.e.get('test');self.assertEqual(self.s['status'],'ready');self.assertEqual(calls,[])
        self.e.recovery_enable('test',False)
        self.assertFalse(self.e.get('test')['recovery_enabled'])

    def test_source_directory_symlink_changes_invalidate_snapshot(self):
        target=self.fixture.base/'external-date';target.mkdir();(target/'data').write_bytes(b'original')
        (Path(self.s['config']['source'])/'date-link').symlink_to(target,target_is_directory=True)
        ident=self.checkpoint();(target/'data').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'輸入或環境'):self.rec.restore(ident)

    def test_metadata_tampering_and_no_confirmation_block(self):
        ident=self.checkpoint()
        with self.assertRaises(ValueError):self.e.launch('test','recover',options={'snapshot':ident})
        p=self.rec.checkpoint_path(ident)/'checkpoint.json'
        data=json.loads(p.read_text());data['phase']='cmd4 · 解纏與地理編碼';p.write_text(json.dumps(data))
        self.assertFalse(self.rec.report()['can_recover'])

    def test_process_is_stopped_if_tracking_cannot_be_saved(self):
        with patch('alosstack_helper.engine.atomic_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.e.run(self.s,['/bin/sleep','30'])
        self.assertIsNone(self.e.process)

if __name__=='__main__':unittest.main()
