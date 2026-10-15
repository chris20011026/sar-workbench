"""Report content, source consistency, editable OOXML and actual point extraction."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import xml.etree.ElementTree as ET

from alosstack_helper.analysis import Analyses
from alosstack_helper.core import atomic_json
from alosstack_helper.engine import Engine
from alosstack_helper.comparison import Comparisons
from alosstack_helper.reports import Reports, source_identities, docx_report
from alosstack_helper.report_plot import checked_point
import test_comparison as comparison_fixtures
worker=comparison_fixtures.worker

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


@unittest.skipIf(worker is None,'requires synthetic HDF5 scientific runtime')
class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.project=self.p/'project'
        self.left=comparison_fixtures.ComparisonTests.fixture(self,'aaaaaaaaaaaa',0)
        self.right=comparison_fixtures.ComparisonTests.fixture(self,'bbbbbbbbbbbb',.002)
        self.state=dict(id='test',config=dict(root=str(self.project),env=sys.prefix,workflow='alosStack',name='研究 <A&B>',reference='200101'),status='complete',history=[],expected_pairs=['200101-210101'],dates=[dict(date='200101'),dict(date='210101')],excluded=['200101-220101'],ion=dict(estimate=True,apply=True))
        self.e=Engine(self.p/'registry');self.e.save(self.state);self.e.register('test',self.project)
        self.a=Analyses(self.e);self.c=Comparisons(self.a);self.r=Reports(self.a,self.c)
        for folder in (self.left,self.right):
            (folder/'exports').mkdir()
            (folder/'exports/velocity.png').write_bytes(PNG)
            job=json.loads((folder/'analysis.json').read_text());job['options']['reference_date']='200101';atomic_json(folder/'analysis.json',job)
            result=json.loads((folder/'results.json').read_text());result.update(dates=['20200101','20210101','20220101','20230101'],reference_date='20200101',valid_pixels=63,total_pixels=72,unit='mm/year',versions={'MintPy':'test'},preview=dict(file='velocity.png',min=-10,max=10),velocity_meaning='相對 LOS 線性擬合速度')
            atomic_json(folder/'results.json',result)

    def tearDown(self):
        self.r.shutdown();self.c.shutdown();self.tmp.cleanup()

    def wait(self,job):
        deadline=time.monotonic()+60
        while self.r.active and time.monotonic()<deadline:time.sleep(.02)
        self.assertFalse(self.r.active,'report did not finish')
        return self.r.get('test',job['id'])

    def create(self,**data):return self.wait(self.r.start('test',dict(run='aaaaaaaaaaaa',**data)))

    def test_editable_word_html_bundle_and_provenance_without_source_changes(self):
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.project.rglob('*') if p.is_file()}
        job=self.create(title='台北研究成果',author='研究者',notes='<script>alert(1)</script>')
        self.assertEqual(job['status'],'complete',job.get('error'))
        html=self.r.artifact('test',job['id'],'report.html').read_text()
        self.assertIn('&lt;script&gt;',html);self.assertNotIn('<script>',html)
        self.assertIn('200101-220101',html);self.assertIn('品質遮罩後像素',html)
        with zipfile.ZipFile(self.r.artifact('test',job['id'],'report.docx')) as z:
            for name in z.namelist():
                if name.endswith('.xml') or name.endswith('.rels'):ET.fromstring(z.read(name))
            doc=ET.fromstring(z.read('word/document.xml'));self.assertIn('研究者',''.join(doc.itertext()))
            self.assertIn('word/media/image1.png',z.namelist())
        with zipfile.ZipFile(self.r.artifact('test',job['id'],'research-report.zip')) as z:
            self.assertTrue({'report.html','report.docx','report.json'}.issubset(z.namelist()))
        snapshot=json.loads(self.r.artifact('test',job['id'],'report.json').read_text())
        self.assertTrue(snapshot['source_records']);self.assertNotIn(str(self.project),json.dumps(snapshot))
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.project.rglob('*') if p.is_file()})

    def test_only_completed_mintpy_allowed_and_fields_bounded(self):
        for data in [dict(title='x'*161),dict(author='x'*121),dict(notes='x'*12001),dict(point={'y':True,'x':2})]:
            with self.subTest(data=list(data)),self.assertRaises(ValueError):self.r.start('test',dict(run='aaaaaaaaaaaa',**data))
        p=self.left/'analysis.json';job=json.loads(p.read_text());job['status']='loaded';atomic_json(p,job)
        with self.assertRaisesRegex(ValueError,'已完成'):self.r.start('test',dict(run='aaaaaaaaaaaa'))

    def test_report_reserves_project_against_unregister(self):
        with patch('threading.Thread.start'):
            job=self.r.start('test',dict(run='aaaaaaaaaaaa'))
        self.assertTrue(self.e.active.startswith('test:'))
        with self.assertRaises(ValueError):self.e.unregister('test')
        self.r.active=None;self.r.thread=None;self.e.active=None
        self.assertEqual(self.r.get('test',job['id'])['status'],'failed')

    def test_changed_h5_during_report_refuses_publication(self):
        def mutate(snapshot,out):
            docx_report(snapshot,out)
            p=self.left/'velocity.h5';p.write_bytes(p.read_bytes()+b'changed')
        with patch('alosstack_helper.reports.docx_report',side_effect=mutate):job=self.create()
        self.assertEqual(job['status'],'failed');self.assertIn('科學成果',job['error'])
        with self.assertRaises(ValueError):self.r.artifact('test',job['id'],'report.docx')

    def test_selected_corrected_timeseries_missing_has_no_fallback(self):
        (self.left/'timeseries_corrected.h5').unlink();job=self.create()
        self.assertEqual(job['status'],'failed')
        self.assertTrue((self.left/'timeseries.h5').exists())

    def test_report_point_uses_real_runtime_and_corrected_values(self):
        job=self.create(point=dict(y=3,x=3))
        self.assertEqual(job['status'],'complete',job['error'])
        snapshot=json.loads(self.r.artifact('test',job['id'],'report.json').read_text())
        self.assertEqual(snapshot['point']['analysis_id'],'aaaaaaaaaaaa')
        self.assertAlmostEqual(snapshot['point']['displacement_mm'][-1],9.9,places=4)
        self.assertEqual(snapshot['point_figure'],'point.png')

    def test_path_traversal_and_unpublished_artifacts_refused(self):
        job=self.create()
        for name in ('../job.json','job.json','point-data.json'):
            with self.assertRaises(ValueError):self.r.artifact('test',job['id'],name)
        with self.assertRaises(ValueError):self.r.folder('../escape','a'*16)

    def test_moved_source_identity_resolves_corrected_analysis(self):
        job=json.loads((self.left/'analysis.json').read_text());before=source_identities(self.left,job)
        old='/old-project/analyses/aaaaaaaaaaaa'
        job['timeseries_file']=old+'/timeseries_corrected.h5'
        state=json.loads((self.project/'.helper/state.json').read_text());state['config']['root']='/old-project';atomic_json(self.project/'.helper/state.json',state)
        self.assertEqual(before,source_identities(self.left,job))

    def test_imported_provenance_not_invented(self):
        state=dict(self.state,import_only=True,status='imported',excluded=[],ion=dict(estimate=None,apply=None,source='unknown'))
        self.e.save(state);job=self.create();self.assertEqual(job['status'],'complete',job['error'])
        html=self.r.artifact('test',job['id'],'report.html').read_text()
        self.assertIn('外部手動成果',html);self.assertIn('均保持未知',html)

    def test_comparison_report_includes_both_figures_statistics_and_curves(self):
        comp=self.c.start('test','aaaaaaaaaaaa','bbbbbbbbbbbb');deadline=time.monotonic()+60
        while self.e.active and time.monotonic()<deadline:time.sleep(.03)
        ready=self.c.get('test',comp['id']);self.assertEqual(ready['status'],'complete',ready.get('error'))
        report=self.create(comparison=comp['id'],point=dict(y=3,x=3))
        self.assertEqual(report['status'],'complete',report['error'])
        snapshot=json.loads(self.r.artifact('test',report['id'],'report.json').read_text())
        self.assertEqual(len(snapshot['analyses']),2);self.assertEqual(len(snapshot['comparison_figures']),3)
        self.assertAlmostEqual(snapshot['comparison']['difference']['statistics']['mean'],2)
        self.assertTrue(snapshot['comparison_point']['right']['available'])
        self.assertTrue(snapshot['comparison_point']['left']['available'])

    def test_old_comparison_cannot_be_mixed_with_changed_scientific_result(self):
        comp=self.c.start('test','aaaaaaaaaaaa','bbbbbbbbbbbb');deadline=time.monotonic()+60
        while self.e.active and time.monotonic()<deadline:time.sleep(.03)
        p=self.right/'velocity.h5';p.write_bytes(p.read_bytes()+b'change')
        report=self.create(comparison=comp['id'])
        self.assertEqual(report['status'],'failed');self.assertIn('改變',report['error'])

    def test_metadata_only_report_is_offline_and_labels_container_only_verification(self):
        with patch('alosstack_helper.reports.find_conda',side_effect=AssertionError('metadata export must remain offline')):
            report=self.create(notes='source=file:///Users/Private Person/project/velocity.h5')
        self.assertEqual(report['status'],'complete',report['error'])
        snapshot=json.loads(self.r.artifact('test',report['id'],'report.json').read_text())
        self.assertIsNone(snapshot['source_verification']['point_analysis'])
        self.assertFalse(snapshot['source_verification']['full_pixel_validation'])
        self.assertNotIn('Private Person',snapshot['notes'])
        html=self.r.artifact('test',report['id'],'report.html').read_text()
        self.assertIn('未重新讀取像素或檢查其內部資料連結',html)

    def test_metadata_only_reports_reject_owned_symlink_directories(self):
        for name in ('inputs','exports'):
            with self.subTest(directory=name):
                original=self.left/name;target=self.p/('foreign-'+name)
                original.rename(target);original.symlink_to(target,target_is_directory=True)
                try:
                    report=self.create()
                    self.assertEqual(report['status'],'failed')
                    self.assertIn('符號連結',report['error'])
                    with self.assertRaises(ValueError):self.r.artifact('test',report['id'],'report.html')
                finally:original.unlink();target.rename(original)

    def test_source_picture_replaced_by_symlink_during_report_is_rejected(self):
        def replace(snapshot,out):
            docx_report(snapshot,out)
            path=self.left/'exports/velocity.png';other=self.p/'same-picture.png'
            other.write_bytes(path.read_bytes());path.unlink();path.symlink_to(other)
        with patch('alosstack_helper.reports.docx_report',side_effect=replace):report=self.create()
        self.assertEqual(report['status'],'failed');self.assertIn('符號連結',report['error'])

    def test_point_reads_reject_indirect_hdf5_before_dereferencing_pixels(self):
        h5py=comparison_fixtures.h5py
        path=self.left/'timeseries_corrected.h5';original=path.read_bytes()
        for kind in ('external-link','soft-link','virtual','external-raw','nested-link'):
            with self.subTest(kind=kind):
                path.write_bytes(original)
                with h5py.File(path,'a') as f:
                    if kind=='nested-link':
                        f.create_group('extra')['link']=h5py.ExternalLink(str(self.p/'outside.h5'),'/data')
                    else:
                        del f['timeseries']
                        if kind=='external-link':f['timeseries']=h5py.ExternalLink(str(self.p/'outside.h5'),'/data')
                        elif kind=='soft-link':f['timeseries']=h5py.SoftLink('/date')
                        elif kind=='virtual':
                            layout=h5py.VirtualLayout(shape=(4,8,9),dtype='float64')
                            layout[:]=h5py.VirtualSource(str(self.p/'outside.h5'),'data',shape=(4,8,9))
                            f.create_virtual_dataset('timeseries',layout)
                        else:f.create_dataset('timeseries',shape=(4,8,9),dtype='float64',external=[(str(self.p/'raw.bin'),0,h5py.h5f.UNLIMITED)])
                with patch('alosstack_helper.science_worker.point_data') as read_pixels:
                    with self.assertRaisesRegex(ValueError,'間接連結|外部／虛擬'):
                        checked_point(self.left/'analysis.json',3,3)
                    read_pixels.assert_not_called()

    def test_hard_link_group_cycle_is_finite_and_valid_point_is_preserved(self):
        with comparison_fixtures.h5py.File(self.left/'timeseries_corrected.h5','a') as f:
            group=f.create_group('internal');group['self']=group
        point=checked_point(self.left/'analysis.json',3,3)
        self.assertAlmostEqual(point['displacement_mm'][-1],9.9,places=4)

    def test_point_worker_checks_source_identity_after_pixel_read(self):
        from alosstack_helper.science_worker import point_data
        def replace(*args):
            value=point_data(*args)
            p=self.left/'velocity.h5';p.write_bytes(p.read_bytes()+b'changed')
            return value
        with patch('alosstack_helper.science_worker.point_data',side_effect=replace):
            with self.assertRaisesRegex(ValueError,'來源已改變'):checked_point(self.left/'analysis.json',3,3)

    def test_relative_timeseries_is_read_from_selected_analysis_only(self):
        path=self.left/'analysis.json';job=json.loads(path.read_text())
        job['timeseries_file']='timeseries_corrected.h5';atomic_json(path,job)
        with self.assertRaisesRegex(ValueError,'相對時間序列'):
            checked_point(path,3,3)
        report=self.create(point=dict(y=3,x=3))
        self.assertEqual(report['status'],'complete',report['error'])
        snapshot=json.loads(self.r.artifact('test',report['id'],'report.json').read_text())
        self.assertAlmostEqual(snapshot['point']['displacement_mm'][-1],9.9,places=4)


if __name__=='__main__':unittest.main()
