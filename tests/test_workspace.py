"""Routing is advisory; scientific review gates and original state remain authoritative."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import copy
import json
import tempfile
import sys
import threading
import unittest
import zipfile

from alosstack_helper.core import atomic_json
from alosstack_helper.environment_checks import EnvironmentChecks, signature, CHECK_VERSION
from alosstack_helper.environment_probe import probe
from alosstack_helper.workspace import identify, overview, source_candidates, workflow_catalogue
from test_tops import safe


class WorkspaceOverview(unittest.TestCase):
    def test_all_job_types_namespaced_and_science_completion_qualified(self):
        p=dict(id='same', status='complete', stage='cmd4', config=dict(name='研究', root='/data/project', env='/env/isce2'))
        a=dict(id='same', kind='mintpy', status='loaded', stage='選擇參考點', environment='/env/isce2', created=10)
        n=dict(id='same', status='ready', config=dict(name='NISAR', root='/data/nisar', environment='/env/isce3'), created=20)
        c=dict(id='same', status='running', config=dict(name='格式副本', root='/data/copy'), created=30)
        before=copy.deepcopy([p,a,n,c])
        engine=SimpleNamespace(list=lambda:[p], unavailable=lambda:[dict(id='missing',root='/offline/data',reason='offline')], removed_projects=lambda:[])
        analyses=SimpleNamespace(listing=lambda _:[a])
        def manager(state):return SimpleNamespace(reconcile=lambda:None, lock=threading.Lock(), jobs={'same':state})
        prep=SimpleNamespace(recent=lambda:[dict(id='same',status='failed',kind='terrain',created=40,error='network')])
        r=overview(engine,analyses,manager(n),manager(c),prep)
        self.assertEqual(len(r['records']),6)
        self.assertEqual(len({x['key'] for x in r['records']}),6)
        self.assertEqual(r['records'][0]['kind'],'conversion')
        self.assertEqual(r['counts'],dict(running=1,attention=3,ready=0,complete=1,unavailable=1))
        rows={x['kind']:x for x in r['records'] if x['status']!='unavailable'}
        self.assertIn('待品質檢查',rows['project']['status_label'])
        self.assertEqual(rows['nisar']['group'],'attention')
        self.assertEqual(rows['analysis']['action'],dict(type='analysis',id='same',run='same'))
        self.assertIsNone(r['records'][-1]['action'])
        self.assertEqual(r['records'][-1]['registration'],dict(id='missing',busy=False))
        self.assertEqual(rows['project']['registration'],dict(id='same',busy=False))
        self.assertEqual(r['removed_projects'],[])
        self.assertEqual([p,a,n,c],before)

    def test_missing_analysis_does_not_hide_other_projects(self):
        engine=SimpleNamespace(list=lambda:[dict(id='p',status='new',config=dict(name='Example',root='/data'))],unavailable=lambda:[],removed_projects=lambda:[])
        analyses=SimpleNamespace(listing=Mock(side_effect=ValueError('invalid json')))
        empty=SimpleNamespace(reconcile=lambda:None,lock=threading.Lock(),jobs={})
        r=overview(engine,analyses,empty,empty,SimpleNamespace(recent=lambda:[]))
        self.assertEqual(len(r['records']),1)
        self.assertIn('invalid json',r['warnings'][0])

    def test_catalogue_does_not_enable_unimplemented_or_mixed_science(self):
        rows={r['id']:r for r in workflow_catalogue()}
        for key in ('topsStack','stripmapApp','stripmapStack'):
            self.assertFalse(rows[key]['available'])
            self.assertEqual(rows[key]['maturity'],'planned')
        self.assertEqual(rows['alos4']['maturity'],'preparation')
        self.assertEqual(rows['nisar']['engine'],'ISCE3')
        self.assertEqual(rows['alosStack']['engine'],'ISCE2')


class WorkspaceIdentification(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.library=Mock();self.library.scan.return_value=dict(products=[],errors=[])
        self.onboarding=Mock()
    def tearDown(self):self.temp.cleanup()
    def run_scan(self,path=None):return identify(str(path or self.base),'/env/selected',self.library,self.onboarding)

    def test_iw_slc_metadata_and_generic_zip_are_recognized_without_processing(self):
        a=safe(self.base,'20220801');b=safe(self.base,'20220813')
        r=self.run_scan();self.assertEqual(r['eligible'],['topsApp']);self.assertEqual(len(r['products']),2)
        z=self.base/'download.zip'
        with zipfile.ZipFile(z,'w') as archive:
            for p in a.rglob('*'):
                if p.is_file():archive.write(p,p.relative_to(self.base))
        r=self.run_scan(z);self.assertEqual(r['products'][0]['date'],'20220801')
        self.library.scan.assert_not_called();self.onboarding.scan.assert_not_called()
        self.assertTrue(b.exists())

    def test_grd_and_broken_zip_never_offer_tops(self):
        a=safe(self.base,'20220801');p=a/'manifest.safe'
        p.write_text(p.read_text().replace('<productType>SLC','<productType>GRD'))
        r=self.run_scan(a);self.assertEqual(r['eligible'],[]);self.assertTrue(r['warnings'])
        p=self.base/'S1A_broken.zip';p.write_text('broken')
        self.assertEqual(self.run_scan(p)['eligible'],[])

    def test_mixed_alos_preserves_manual_gate(self):
        (self.base/'LED-ALOS2-fixture').touch();(self.base/'LED-ALOS4-fixture').touch()
        self.library.scan.return_value=dict(products=[dict(sensor=s,type='CEOS',path=s) for s in ('ALOS-2','ALOS-4')],errors=[])
        r=self.run_scan();self.assertEqual(r['eligible'],['alos4'])
        self.assertTrue(any('不自動接續' in w for w in r['warnings']))

    def test_gunw_is_viewable_but_not_new_rslc_work(self):
        p=self.base/'gunw.h5';p.touch()
        self.library.scan.return_value=dict(products=[dict(sensor='NISAR',type='GUNW',path=str(p))],errors=[])
        r=self.run_scan(p);self.assertEqual(r['eligible'],[])
        self.library.scan.return_value['products'].append(dict(sensor='NISAR',type='RSLC',path='rslc',metadata=dict(zeroDopplerStartTime='2026-07-21')))
        r=self.run_scan();self.assertEqual(r['eligible'],['nisar']);self.assertEqual(r['products'][1]['date'],'2026-07-21')

    def test_mixed_sensors_do_not_offer_a_scientific_run(self):
        safe(self.base,'20220801');(self.base/'nisar.h5').touch()
        self.library.scan.return_value=dict(products=[dict(sensor='NISAR',type='RSLC',path='nisar.h5')],errors=[])
        self.assertEqual(self.run_scan()['eligible'],[])

    def test_ceos_leader_resolves_to_directory(self):
        p=self.base/'LED-ALOS2-fixture';p.touch()
        self.library.scan.return_value=dict(products=[dict(sensor='ALOS-2',type='CEOS',path=str(p))],errors=[])
        r=self.run_scan(p);self.assertEqual(r['source'],str(self.base))
        self.assertEqual(set(r['eligible']),{'alosStack','alos2App'})

    def test_traversal_is_bounded_and_does_not_follow_symlinks(self):
        deep=self.base/'1/2/3/4/5';deep.mkdir(parents=True);(deep/'not-seen.h5').touch()
        (self.base/'loop').symlink_to(self.base,target_is_directory=True)
        self.assertEqual(source_candidates(str(self.base))[1],[])
        for i in range(65):(self.base/f'{i}.h5').touch()
        with self.assertRaisesRegex(ValueError,'64'):self.run_scan()


class EnvironmentStatus(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.env=self.base/'env';(self.env/'bin').mkdir(parents=True);(self.env/'bin/python').write_text('python')
        (self.env/'conda-meta').mkdir();self.package=self.env/'conda-meta/isce2-1.json';self.package.write_text('{}')
        self.checks=EnvironmentChecks(self.base/'state');self.candidates=[dict(path=str(self.env),name='test',isce=True)]
    def tearDown(self):self.temp.cleanup()
    def test_detection_is_not_validation_and_updates_invalidate_cache(self):
        self.assertEqual(self.checks.catalogue(self.candidates)[0]['validation'],'detected')
        report=dict(ok=True,signature=signature(self.env),check_version=CHECK_VERSION,checked_at=0,checks=[])
        atomic_json(self.checks.path(self.env),report)
        self.assertEqual(self.checks.catalogue(self.candidates)[0]['validation'],'verified')
        self.package.write_text('{"new":"version"}')
        row=self.checks.catalogue(self.candidates)[0]
        self.assertEqual(row['validation'],'changed');self.assertIsNone(row['report'])
    def test_failed_check_not_ready_and_unknown_prefix_not_launched(self):
        atomic_json(self.checks.path(self.env),dict(ok=False,signature=signature(self.env),check_version=CHECK_VERSION))
        self.assertEqual(self.checks.catalogue(self.candidates)[0]['validation'],'failed')
        with patch('alosstack_helper.environment_checks.subprocess.Popen') as launch:
            with self.assertRaisesRegex(ValueError,'重新偵測'):self.checks.run('/unknown/env',self.candidates)
            launch.assert_not_called()

    def test_nisar_specific_driver_is_not_required_by_isce2(self):
        fake=SimpleNamespace(gdal=SimpleNamespace(GetDriverByName=lambda _:None,VersionInfo=lambda:'fixture'))
        with patch.dict(sys.modules,{'osgeo':fake}),patch('alosstack_helper.environment_probe.importlib.import_module',side_effect=ImportError('fixture')):
            standard=probe([])
            nisar=probe(['isce3'])
        self.assertTrue(next(c for c in standard['checks'] if c['name']=='GDAL')['ok'])
        self.assertFalse(next(c for c in nisar['checks'] if c['name']=='GDAL 與 NISAR HDF5')['ok'])

    def test_optional_plotting_does_not_misreport_a_working_nisar_processor(self):
        self.package.write_text('{"subdir":"osx-arm64"}')
        fake=SimpleNamespace(gdal=SimpleNamespace(GetDriverByName=lambda _:object(),VersionInfo=lambda:'fixture'))
        def imported(name):
            if name=='matplotlib':raise ImportError('No module named matplotlib')
            return SimpleNamespace(__name__=name,__version__='0.25.21')
        with patch.dict(sys.modules,{'osgeo':fake}),patch.object(sys,'prefix',str(self.env)),patch('alosstack_helper.environment_probe.importlib.import_module',side_effect=imported):
            r=probe(['isce3'])
        self.assertTrue(r['ok'])
        plotting=next(c for c in r['checks'] if c['name']=='成果繪圖函式庫')
        self.assertFalse(plotting['ok']);self.assertFalse(plotting['required'])


if __name__=='__main__':unittest.main()
