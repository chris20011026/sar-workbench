import copy
import datetime as dt
import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile
from alosstack_helper.sentinel import inspect_safe,inspect_pair,utc
from alosstack_helper.sentinel_orbits import validate_orbit,download_orbit,BASE
from alosstack_helper.tops import normalize,preflight,TopsWorkflow,STEPS,write_xml,app_path
from alosstack_helper.engine import Engine
from alosstack_helper.core import atomic_json


def safe(base,date,orbit=42,polarization='VV'):
    name=f'S1A_IW_SLC__1SDV_{date}T010000_{date}T010030_000001_000001_0001.SAFE';p=base/name;p.mkdir()
    (p/'manifest.safe').write_text(f'<root><productType>SLC</productType><mode>IW</mode><platform><number>A</number></platform><startTime>{date}T010000</startTime><stopTime>{date}T010030</stopTime><relativeOrbitNumber>{orbit}</relativeOrbitNumber><pass>ASCENDING</pass></root>')
    (p/'annotation').mkdir();(p/'measurement').mkdir();name=f's1a-iw1-slc-{polarization.lower()}-{date}.xml'
    grid=''.join(f'<geolocationGridPoint><line>{line}</line><latitude>{lat}</latitude><longitude>{lon}</longitude></geolocationGridPoint>' for line,lat in [(0,24),(100,24.2),(199,24.4)] for lon in (121,121.4))
    (p/'annotation'/name).write_text(f'<product><adsHeader><missionId>S1A</missionId><productType>SLC</productType><swath>IW1</swath><polarisation>{polarization}</polarisation><startTime>{date}T010000</startTime><stopTime>{date}T010030</stopTime></adsHeader><swathTiming><linesPerBurst>100</linesPerBurst><burstList count="2"><burst><azimuthTime>{date}T010000</azimuthTime></burst><burst><azimuthTime>{date}T010010</azimuthTime></burst></burstList></swathTiming><geolocationGrid>{grid}</geolocationGrid></product>')
    (p/'measurement'/(Path(name).stem+'.tiff')).write_bytes(b'II*\x00'+b'\0'*100)
    return p


def orbit(base,scene):
    start=utc(scene['start'])-dt.timedelta(seconds=120);stop=utc(scene['stop'])+dt.timedelta(seconds=120)
    states=''.join('<OSV><UTC>'+ (start+dt.timedelta(seconds=n)).isoformat()+'</UTC>'+''.join(f'<{k}>1</{k}>' for k in ['X','Y','Z','VX','VY','VZ'])+'</OSV>' for n in range(0,int((stop-start).total_seconds())+1,10))
    p=base/(scene['date']+'.EOF');p.write_text(f'<root><Mission>Sentinel-1A</Mission><File_Type>AUX_POEORB</File_Type><Validity_Start>{start.isoformat()}</Validity_Start><Validity_Stop>{stop.isoformat()}</Validity_Stop>{states}</root>');return p


class TopsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.a=safe(self.base,'20220801');self.b=safe(self.base,'20220813');self.e=Engine(self.base/'registry');self.w=TopsWorkflow(self.e)
        env=self.base/'conda/envs/test';(env/'bin').mkdir(parents=True);(env/'bin/python').touch();(self.base/'conda/bin').mkdir();(self.base/'conda/bin/conda').touch()
        app=env/'lib/python3.12/site-packages/isce/applications/topsApp.py';app.parent.mkdir(parents=True);app.write_text('test')
        dem=self.base/'terrain.dem';dem.write_bytes(b'\0'*32)
        Path(str(dem)+'.xml').write_text('<imageFile><property name="file_name">terrain.dem</property><property name="width">4</property><property name="length">4</property><property name="data_type">SHORT</property><property name="reference">WGS84</property><component name="coordinate1"><property name="startingvalue">120</property><property name="delta">1</property></component><component name="coordinate2"><property name="startingvalue">26</property><property name="delta">-1</property></component></imageFile>')
        pair=inspect_pair(self.a,self.b)
        self.c=dict(name='fixture',root=str(self.base/'project'),env=str(env),reference_safe=str(self.a),secondary_safe=str(self.b),dem=str(dem),swaths=[1],polarization='VV',orbit_mode='local',reference_orbit=str(orbit(self.base,pair['reference'])),secondary_orbit=str(orbit(self.base,pair['secondary'])),experimental_ack=True)
    def tearDown(self):self.tmp.cleanup()
    def test_inventory_directory_zip_and_pair(self):
        d=inspect_pair(self.a,self.b);self.assertEqual(d['days'],12);self.assertEqual(d['common'][0]['reference_bursts'][1]['index'],2)
        z=self.base/'data.zip'
        with zipfile.ZipFile(z,'w') as archive:
            for p in self.a.rglob('*'):
                if p.is_file():archive.write(p,str(p.relative_to(self.base)))
        self.assertEqual(inspect_safe(z)['date'],'20220801')
        with zipfile.ZipFile(z,'a') as archive:archive.writestr('../evil','bad')
        with self.assertRaises(ValueError):inspect_safe(z)
    def test_mismatched_orbit_dates_and_grd_rejected(self):
        with self.assertRaises(ValueError):inspect_pair(self.b,self.a)
        p=self.b/'manifest.safe';p.write_text(p.read_text().replace('42','43'))
        with self.assertRaises(ValueError):inspect_pair(self.a,self.b)
        p=self.a/'manifest.safe';p.write_text(p.read_text().replace('SLC','GRD'))
        with self.assertRaises(ValueError):inspect_safe(self.a)
    def test_missing_measurement_entity_and_channel_inconsistency(self):
        p=next((self.a/'measurement').iterdir());p.unlink()
        with self.assertRaises(ValueError):inspect_safe(self.a)
        (self.b/'manifest.safe').write_text('<!DOCTYPE x><x/>')
        with self.assertRaises(ValueError):inspect_safe(self.b)
    def test_orbit_satellite_coverage_and_vectors(self):
        scene=inspect_safe(self.a);p=Path(self.c['reference_orbit']);validate_orbit(p,scene)
        p.write_text(p.read_text().replace('Sentinel-1A','Sentinel-1B'))
        with self.assertRaises(ValueError):validate_orbit(p,scene)
    def test_official_orbit_zip_layout_and_conflicting_duplicates(self):
        scene=inspect_safe(self.a);body=Path(self.c['reference_orbit']).read_bytes()
        name='S1A_OPER_AUX_POEORB_OPOD_20220822T000000_V20220731T225942_20220802T005942.EOF'
        def archive(other):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                z.writestr(name,body);z.writestr('var/www/auxdata/'+name,other)
            return stream.getvalue()
        def request(url,limit):return archive(body) if url.endswith('.zip') else ('<a href="'+name+'.zip">orbit</a>').encode()
        with patch('alosstack_helper.sentinel_orbits.read_url',side_effect=request):
            result=download_orbit(scene,self.base/'orbits');self.assertEqual(Path(result['path']).read_bytes(),body)
        def conflict(url,limit):return archive(b'different') if url.endswith('.zip') else ('<a href="'+name+'.zip">orbit</a>').encode()
        with patch('alosstack_helper.sentinel_orbits.read_url',side_effect=conflict),self.assertRaises(ValueError):download_orbit(scene,self.base/'orbits')
    def test_preflight_creates_no_files_and_requires_ack(self):
        r=preflight(self.c);self.assertTrue(r['can_proceed'],r);self.assertFalse(Path(self.c['root']).exists())
        for changes in [dict(experimental_ack=False),dict(swaths=[2]),dict(roi=[24.01,24.05,121.1,121.2]),dict(orbit_mode='bad')]:
            with self.subTest(changes=changes):
                try:self.assertFalse(preflight(dict(self.c,**changes))['can_proceed'])
                except ValueError:pass
    def test_invalid_roi_and_parameters(self):
        for changes in [dict(roi=[24,25,121,float('nan')]),dict(swaths=[1,3]),dict(properties={'range looks':1.5}),dict(properties={'alien':1})]:
            with self.assertRaises(ValueError):normalize(dict(self.c,**changes))
    def test_create_stage_guards_source_change_and_artifacts(self):
        s=self.e.create(dict(self.c,workflow='topsApp'));self.assertEqual(s['config']['workflow'],'topsApp');self.w.verify(s)
        with self.assertRaises(ValueError):self.w.validate_action(s,'run',{'confirmed':True})
        s['status']='ready'
        with self.assertRaises(ValueError):self.w.validate_action(s,'inspect',{})
        self.w.validate_action(s,'inspect',{'confirmed':True})
        (self.a/'manifest.safe').write_text('changed')
        with self.assertRaises(ValueError):self.w.verify(s)
        with self.assertRaises(ValueError):self.w.artifact(s,'../state.json')
    def test_failed_step_does_not_advance_or_overwrite(self):
        s=self.w.create(self.c);proc=self.e.paths(s)[1];(proc/'PICKLE').mkdir();(proc/'PICKLE/startup').write_text('partial')
        with self.assertRaises(ValueError):self.w.step(s,*STEPS[0])
        self.assertEqual(s['completed_steps'],[])
    def test_xml_uses_tops_and_no_alos_properties(self):
        c=normalize(self.c);p=self.base/'tops.xml';write_xml(c,p);root=ET.parse(p).getroot();self.assertEqual(root.tag,'topsApp')
        names={x.get('name') for x in root.iter('property')};self.assertIn('safe',names);self.assertNotIn('number of subsequent dates',names)
    @unittest.skipUnless(os.environ.get('ALOSSTACK_TEST_ENV'),'requires installed ISCE2')
    def test_installed_prepare_and_startup_without_processing_pixels(self):
        c=dict(self.c,env=os.environ['ALOSSTACK_TEST_ENV'])
        s=self.w.create(c);self.w.worker(s,'prepare');self.assertEqual(s['status'],'ready')
        self.w.step(s,*STEPS[0]);self.assertEqual(s['completed_steps'],['startup'])
        self.w.verify(s)
    @unittest.skipUnless(os.environ.get('ALOSSTACK_TEST_ENV'),'requires installed ISCE2')
    def test_installed_isce_resolves_generated_xml(self):
        c=normalize(self.c);c['env']=os.environ['ALOSSTACK_TEST_ENV'];app_path(c['env']);write_xml(c,self.base/'topsApp.xml');meta=self.base/'.helper';meta.mkdir();atomic_json(meta/'state.json',dict(config=c,tops_steps=[k for k,_ in STEPS]))
        env=dict(os.environ);env['MPLCONFIGDIR']=str(self.base/'mpl');env['XDG_CACHE_HOME']=str(self.base/'cache');env['HOME']=str(self.base)
        worker=Path(__file__).resolve().parents[1]/'src/alosstack_helper/tops_worker.py'
        result=subprocess.run([c['env']+'/bin/python',str(worker),'resolve',str(meta/'state.json')],cwd=self.base,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        r=json.loads((meta/'tops-resolved.json').read_text());self.assertEqual(r['properties']['swaths'],[1]);self.assertFalse(r['properties']['do ionosphere correction'])

if __name__=='__main__':unittest.main()
