"""Portable downstream inputs must stay independent of the old computer."""
from pathlib import Path
import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.core import atomic_json
from alosstack_helper.legacy import create_wrapper, inspect_legacy
from alosstack_helper.portable import (FORMAT, MANIFEST, INCOMPLETE, plan_export,
    export_package, inspect_package, import_package, require_portable_source, read_provenance)
from alosstack_helper.workflows import inventory
from test_legacy import dataset


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.request,self.pairs=dataset(self.base);self.source=Path(self.request['source'])
        self.state=dict(id='abcdef123456',status='complete',config=dict(root=str(self.source.parent),
            workflow='alosStack',name='合成成果',reference='181103',env=self.request['environment'],properties={'polarization':'HH'}),
            expected_pairs=self.pairs,history=[{'action':'finish'}],ion={'estimate':True,'apply':True},excluded=['170826-181103'])
        (self.source.parent/'.helper').mkdir();atomic_json(self.source.parent/'.helper/state.json',self.state)
        self.destination=self.base/'package'

    def tearDown(self):self.temp.cleanup()

    def export(self):
        preview=plan_export(self.state,str(self.destination))
        return export_package(self.state,preview)

    def request_import(self,source=None):
        return dict(source=str(source or self.destination),root=str(self.base/'imported'),environment=self.request['environment'],name='新分析')

    def hashes(self,root):
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}

    def test_export_has_independent_bytes_relative_descriptors_and_full_manifest(self):
        before=self.hashes(self.source);report=self.export()
        self.assertEqual(before,self.hashes(self.source));self.assertEqual(report['status'],'complete')
        manifest=json.loads((self.destination/MANIFEST).read_text());self.assertEqual(manifest['format'],FORMAT)
        for row in manifest['files']:
            p=self.destination/row['path'];self.assertFalse(p.is_symlink());self.assertEqual(p.stat().st_nlink,1)
            self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['sha256'])
        for p in (self.destination/'proc').rglob('*.xml'):
            self.assertNotIn(str(self.source),p.read_text());self.assertNotIn(str(self.destination),p.read_text())
        self.assertFalse((self.destination/INCOMPLETE).exists());self.assertFalse((self.destination/'.helper').exists())

    def test_move_package_hide_all_sources_import_is_standalone_and_review_reset(self):
        self.export();moved=self.base/'moved';self.destination.rename(moved)
        self.source.parent.rename(self.base/'old-source-unavailable')
        req=self.request_import(moved);preview=inspect_package(req)
        state=import_package(req,preview,'112233aabbcc')
        moved.rename(self.base/'old-package-unavailable')
        require_portable_source(state);inv=inventory(state)
        self.assertEqual(inv['network']['dates'],['170826','181103','190518'])
        self.assertTrue(state['import_only']);self.assertEqual(state['status'],'imported');self.assertEqual(state['history'],[])
        self.assertEqual(state['excluded'],[]);self.assertIsNone(state['ion']['apply'])
        self.assertFalse(state['recovery_enabled']);self.assertFalse((Path(state['config']['root'])/'analyses').exists())
        for p in Path(state['config']['root']).rglob('*'):self.assertFalse(p.is_symlink())
        record=json.loads((Path(state['config']['root'])/'.helper/portable-provenance.json').read_text())
        self.assertTrue(record['ion']['apply']);self.assertEqual(record['history'],self.state['history'])

    def test_original_and_existing_destination_not_overwritten(self):
        self.destination.mkdir();(self.destination/'keep').write_text('mine')
        with self.assertRaisesRegex(ValueError,'存在'):plan_export(self.state,str(self.destination))
        self.assertEqual((self.destination/'keep').read_text(),'mine')
        with self.assertRaisesRegex(ValueError,'包含'):plan_export(self.state,str(self.source/'pack'))

    def test_partial_and_unsupported_projects_rejected(self):
        for change in ({'status':'sampling_review'},{'config':dict(self.state['config'],workflow='topsApp')}):
            with self.assertRaises(ValueError):plan_export(dict(self.state,**change),str(self.destination))
        with self.assertRaisesRegex(ValueError,'配對'):plan_export(dict(self.state,expected_pairs=self.pairs[:2]),str(self.destination))
        self.assertFalse(self.destination.exists())

    def test_stale_export_preview_including_unsampled_byte_rejected(self):
        preview=plan_export(self.state,str(self.destination));f=next(self.source.rglob('*.unw'))
        st=f.stat();raw=bytearray(f.read_bytes());raw[-1]^=1;f.write_bytes(raw);os.utime(f,ns=(st.st_atime_ns,st.st_mtime_ns))
        with self.assertRaises(ValueError):export_package(self.state,preview)
        self.assertFalse(self.destination.exists())

    def test_change_project_provenance_rejected(self):
        preview=plan_export(self.state,str(self.destination));changed=copy.deepcopy(self.state);changed['ion']['apply']=False
        with self.assertRaisesRegex(ValueError,'紀錄'):export_package(changed,preview)
        self.assertFalse(self.destination.exists())

    def test_external_links_in_processing_outputs_not_silently_followed(self):
        f=next(self.source.rglob('*.cor'));other=self.base/'external.cor';f.rename(other);f.symlink_to(other)
        with self.assertRaisesRegex(ValueError,'以外'):plan_export(self.state,str(self.destination))

    def test_export_legacy_wrapper_copies_original_without_changing_it(self):
        report=inspect_legacy(self.request);state=create_wrapper(self.request,report,'aabbccddeeff')
        before=self.hashes(self.source);preview=plan_export(state,str(self.destination));export_package(state,preview)
        self.assertEqual(before,self.hashes(self.source));self.source.parent.rename(self.base/'away')
        req=self.request_import();new=import_package(req,inspect_package(req),'112233aabbcc');require_portable_source(new)

    def test_reexport_imported_portable_project(self):
        self.export();req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        destination=self.base/'second-package';export_package(state,plan_export(state,str(destination)))
        self.assertTrue((destination/MANIFEST).is_file())

    def test_missing_or_extra_payload_fails(self):
        self.export();extra=self.destination/'unexpected.py';extra.write_text('raise Exception()')
        with self.assertRaisesRegex(ValueError,'清單'):inspect_package(self.request_import())
        extra.unlink();next((self.destination/'proc').rglob('*.cor')).unlink()
        with self.assertRaisesRegex(ValueError,'清單'):inspect_package(self.request_import())

    def test_payload_tamper_fails_before_new_project(self):
        self.export();f=next((self.destination/'proc').rglob('*.unw'));f.write_bytes(b'x'*f.stat().st_size)
        with self.assertRaisesRegex(ValueError,'驗證失敗'):inspect_package(self.request_import())
        self.assertFalse((self.base/'imported').exists())

    def test_manifest_traversal_duplicate_and_size_errors(self):
        self.export();path=self.destination/MANIFEST;original=json.loads(path.read_text())
        changes=[lambda m:m['files'][0].update(path='../outside'),lambda m:m['files'].append(m['files'][0]),lambda m:m.update(bytes=-1)]
        for change in changes:
            record=copy.deepcopy(original);change(record);atomic_json(path,record)
            with self.assertRaises(ValueError):inspect_package(self.request_import())

    def test_package_symlink_and_hardlink_rejected(self):
        self.export();f=next((self.destination/'proc').rglob('*.cor'));backup=self.base/'backup';f.rename(backup);f.symlink_to(backup)
        with self.assertRaisesRegex(ValueError,'連結'):inspect_package(self.request_import())
        f.unlink();os.link(backup,f)
        with self.assertRaisesRegex(ValueError,'硬連結'):inspect_package(self.request_import())

    def test_forged_manifest_cannot_authorize_executable_xml(self):
        self.export();f=next((self.destination/'proc').rglob('*.cor.vrt'))
        f.write_text(f.read_text().replace('VRTRawRasterBand','VRTDerivedRasterBand'))
        manifest=json.loads((self.destination/MANIFEST).read_text());relative=str(f.relative_to(self.destination))
        row=next(r for r in manifest['files'] if r['path']==relative);old=row['size'];row.update(size=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest());manifest['bytes']+=row['size']-old;atomic_json(self.destination/MANIFEST,manifest)
        with self.assertRaisesRegex(ValueError,'VRT'):inspect_package(self.request_import())

    def test_changed_import_setting_or_preview_content_rejected(self):
        self.export();req=self.request_import();preview=inspect_package(req)
        with self.assertRaisesRegex(ValueError,'設定'):import_package(dict(req,name='changed'),preview,'112233aabbcc')
        self.assertFalse((self.base/'imported').exists())

    def test_insufficient_space_prevents_copy(self):
        preview=plan_export(self.state,str(self.destination))
        usage=shutil._ntuple_diskusage(total=10,used=10,free=0)
        with patch('alosstack_helper.portable.shutil.disk_usage',return_value=usage):
            with self.assertRaisesRegex(ValueError,'空間'):export_package(self.state,preview)
        self.assertFalse(self.destination.exists())

    def test_export_interruption_never_looks_complete(self):
        preview=plan_export(self.state,str(self.destination))
        def stop(_):raise RuntimeError('stopped')
        with self.assertRaisesRegex(RuntimeError,'stopped'):export_package(self.state,preview,stop)
        self.assertFalse((self.destination/MANIFEST).exists())
        with self.assertRaises((ValueError,FileNotFoundError)):inspect_package(self.request_import())

    def test_import_interruption_has_no_state(self):
        self.export();req=self.request_import();preview=inspect_package(req)
        def stop(_):raise RuntimeError('stopped')
        with self.assertRaisesRegex(RuntimeError,'stopped'):import_package(req,preview,'112233aabbcc',stop)
        self.assertFalse((self.base/'imported/.helper/state.json').exists())

    def test_import_receipt_and_payload_change_block_new_analysis(self):
        self.export();req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        f=next((self.base/'imported/proc').rglob('*.cor'));f.write_bytes(b'x'*f.stat().st_size)
        with self.assertRaisesRegex(ValueError,'變更|驗證'):require_portable_source(state)

    def test_finder_metadata_is_ignored_but_link_is_rejected(self):
        self.export();(self.destination/'.DS_Store').write_bytes(b'finder')
        (self.destination/'proc/.DS_Store').write_bytes(b'finder')
        req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        self.assertFalse((Path(state['config']['root'])/'proc/.DS_Store').exists())
        require_portable_source(state)
        (self.destination/'.DS_Store').unlink();(self.destination/'.DS_Store').symlink_to(self.base/'missing')
        with self.assertRaisesRegex(ValueError,'連結'):inspect_package(dict(req,root=str(self.base/'another')))

    def test_publish_does_not_replace_even_empty_destination_created_mid_copy(self):
        preview=plan_export(self.state,str(self.destination))
        def intervene(_):self.destination.mkdir(exist_ok=True)
        with self.assertRaises(OSError):export_package(self.state,preview,intervene)
        self.assertTrue(self.destination.is_dir());self.assertEqual(list(self.destination.iterdir()),[])
        self.assertTrue(list(self.base.glob('.sar-portable-*')))

    def test_source_change_during_copy_rejects_publication(self):
        preview=plan_export(self.state,str(self.destination));changed=[False]
        def mutate(_):
            if not changed[0]:
                changed[0]=True;f=next(self.source.rglob('*.cor'));f.write_bytes(b'x'*f.stat().st_size)
        with self.assertRaisesRegex(ValueError,'來源|內容'):export_package(self.state,preview,mutate)
        self.assertFalse(self.destination.exists())

    def test_runtime_full_metadata_guard_and_receipt_guard(self):
        self.export();req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        f=next((self.base/'imported/proc').rglob('*.cor.xml'));before=f.read_bytes();f.write_bytes(before+b' ')
        with self.assertRaisesRegex(ValueError,'變更'):require_portable_source(state)
        receipt=self.base/'imported/.helper/portable-source.json';receipt.write_text('{}')
        with self.assertRaisesRegex(ValueError,'紀錄'):require_portable_source(state)

    def test_combined_provenance_size_is_capped_before_creation(self):
        with patch('alosstack_helper.portable.MAX_JSON',64):
            with self.assertRaisesRegex(ValueError,'歷史紀錄過大'):plan_export(self.state,str(self.destination))
        self.assertFalse(self.destination.exists());self.assertEqual(list(self.base.glob('.sar-portable-*')),[])

    def test_complete_source_config_is_inert_provenance(self):
        self.state['config'].update(dem='/old/external.dem',subsequent=9,properties={'polarization':'HH','custom':'retained'})
        self.export();record=json.loads((self.destination/'provenance.json').read_text())
        self.assertEqual(record['source_config'],self.state['config'])
        req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        self.assertNotIn('dem',state['config']);self.assertNotIn('subsequent',state['config'])
        self.assertNotIn('custom',state['config']['properties'])

    def test_staging_path_reported_before_copy_and_preserved_when_interrupted(self):
        preview=plan_export(self.state,str(self.destination));updates=[]
        def stop(value):
            updates.append(value)
            if value.get('staging_path'):raise RuntimeError('stop after stage creation')
        with self.assertRaisesRegex(RuntimeError,'stage creation'):export_package(self.state,preview,stop)
        path=Path(updates[0]['staging_path']);self.assertTrue(path.is_dir())
        self.assertTrue((path/INCOMPLETE).is_file());self.assertFalse(self.destination.exists())

    def test_explicit_environment_override_allows_removed_old_environment(self):
        old=Path(self.state['config']['env']);old.rename(self.base/'old-environment-unavailable')
        replacement=self.base/'replacement-environment';(replacement/'bin').mkdir(parents=True)
        (replacement/'bin/python').touch();(replacement/'bin/conda').touch()
        preview=plan_export(self.state,str(self.destination),environment=str(replacement))
        self.assertEqual(preview['environment'],str(replacement.resolve()))
        export_package(self.state,preview)
        record=json.loads((self.destination/'provenance.json').read_text())
        self.assertEqual(record['source_config']['env'],str(old))
        self.assertEqual(record['processing_environment'],str(old))

    def test_inert_provenance_reader_rejects_changed_or_nonportable_record(self):
        with self.assertRaisesRegex(ValueError,'不是可攜'):read_provenance(self.state)
        self.export();req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        value=read_provenance(state);self.assertEqual(value['source_config'],self.state['config'])
        self.assertEqual(value['history'],self.state['history'])
        path=Path(state['config']['root'])/'.helper/portable-provenance.json'
        value['history']=[{'action':'forged approval'}];atomic_json(path,value)
        with self.assertRaisesRegex(ValueError,'紀錄已改變'):read_provenance(state)

    def test_inert_provenance_reader_rejects_link_and_malformed_structure(self):
        self.export();req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        path=Path(state['config']['root'])/'.helper/portable-provenance.json'
        path.write_text('{}');state['portable_import']['provenance_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError,'格式'):read_provenance(state)
        backup=self.base/'provenance';path.rename(backup);path.symlink_to(backup)
        with self.assertRaises(OSError):read_provenance(state)

    def test_package_metadata_is_not_old_project_approvals_or_scripts(self):
        (self.source/'cmd1').write_text('do not execute');(self.source/'anything.pkl').write_bytes(b'badpickle')
        self.export();files=self.hashes(self.destination)
        self.assertFalse(any('cmd1' in p or '.pkl' in p for p in files))
        req=self.request_import();state=import_package(req,inspect_package(req),'112233aabbcc')
        self.assertFalse(state.get('sampling_approved'));self.assertFalse(state.get('matching_approved'))


if __name__=='__main__':unittest.main()
