"""Synthetic CEOS fixtures exercise the actual installed upstream reader.
No satellite imagery or private paths are committed to the repository.
"""
import datetime as dt
import os
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from alosstack_helper.onboarding import scan_source, organize, Onboarding, inventory_plan
from alosstack_helper.metadata import enrich
from alosstack_helper.metadata_worker import coordinate, line_date


class MetadataBoundary(unittest.TestCase):
    def test_missing_environment_is_explicit_not_a_verified_map(self):
        p=enrich(dict(entries=[],can_import=True),None)
        self.assertFalse(p['metadata']['complete'])
        self.assertIsNone(p['metadata']['bounds'])
        self.assertTrue(p['metadata']['warnings'])

    def test_signed_coordinate_and_invalid_day(self):
        self.assertEqual(coordinate((-33000000) % 2**32),-33)
        self.assertEqual(coordinate(121500000),121.5)
        with self.assertRaises(ValueError): line_date({'Sensor acquisition year':2021,'Sensor acquisition day of year':366})

    def test_folder_date_conflict_is_visible(self):
        from test_onboarding import products
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)/'190101';products(folder)
            plan=scan_source(folder)
            self.assertTrue(any('資料夾日期' in message for message in plan['warnings']))

    def test_selection_cannot_inject_unscanned_products(self):
        with tempfile.TemporaryDirectory() as d:
            m=Onboarding(Path(d))
            m.plans['known']={'metadata':{'products':[{'key':'a'}]}}
            for keys in (['other'], [], 'a'):
                with self.assertRaises(ValueError):m.select('known',keys)


SCIENCE = bool(os.environ.get('ALOSSTACK_TEST_ENV'))
@unittest.skipUnless(SCIENCE,'Requires installed ISCE2 test environment')
class CEOSInspection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import isce
        from isceobj.Sensor import xmlPrefix
        cls.schema=Path(xmlPrefix)/'alos2_slc'

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def record(self,name,values,size=None):
        data=bytearray()
        for r in ET.parse(self.schema/name).getroot():
            self.assertEqual(r.tag,'rec')
            kind=int(r.get('type'));n=int(r.get('num'));value=values.get(r.get('name'),0)
            if kind==0:data.extend(b' '*n)
            elif kind in (1,2,5):data.extend(str(value).encode().ljust(n,b' ')[:n])
            else:
                code={3:'B',4:'I',6:'H',9:'Q'}[kind]
                data.extend(struct.pack('>'+code,int(value) % (2**(8*struct.calcsize(code)))))
        length=size or len(data)
        data[8:12]=struct.pack('>I',length)
        data.extend(b'\0'*(length-len(data)))
        return bytes(data)

    def product(self,date='200101',frame='0480',direction='ASCEND',orbit='00001',x=121,y=24,pol='HH',stamp_date=None):
        identity=f'ALOS2{orbit}{frame}'
        suffix=f'{identity}-{date}-FBDR1.1__A'
        actual=stamp_date or date
        scene_id=identity+'-'+date
        scene=self.record('scene_record.xml',{'Scene id':scene_id[:16],'Scene reference number':scene_id[16:],
            'Scene centre time':'20'+actual+'120000000','Time direction indicator along line direction':direction,
            'Sensor clock angle':90,'Orbit number':orbit,'Sensor ID and mode of operation for this channel':'ALOS2 -L -0315-',
            'Product level code':'1.1'})
        (self.root/('LED-'+suffix)).write_bytes(self.record('leader_file.xml',{})+scene)
        header=self.record('image_file.xml',{'Number of SAR DATA records':3,'SAR DATA record length':560,
            'Number of pixels per line per SAR channel':2})
        lines=[]
        for row in range(3):
            values={'Sensor acquisition year':int('20'+actual[:2]),'Sensor acquisition day of year':dt.datetime.strptime(actual,'%y%m%d').timetuple().tm_yday,
                'Transmitted polarization':0,'Received polarization':0 if pol=='HH' else 1}
            for side,lon in [('first',x),('center',x+.5),('last',x+1)]:
                values['Latitude of '+side+' pixel']=round((y+row*.5)*1e6)
                values['Longitude of '+side+' pixel']=round(lon*1e6)
            lines.append(self.record('image_record.xml',values,560))
        (self.root/('IMG-'+pol+'-'+suffix)).write_bytes(header+b''.join(lines))

    def inspect(self):
        from alosstack_helper.metadata_worker import inspect
        return inspect(scan_source(self.root)['entries'])

    def test_real_decoder_coverage_common_and_metadata(self):
        self.product();self.product('200201',orbit='00002',x=121.2)
        result=self.inspect()
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['bounds'],[24,25,121,122.2])
        self.assertTrue(result['complete'])
        self.assertEqual(result['products'][0]['direction'],'ascending')
        from shapely.geometry import shape
        self.assertAlmostEqual(shape(result['common']).area,.8)

    def test_scene_date_polarization_and_identity_mismatch_are_blocked(self):
        self.product(stamp_date='200102');self.product('200201')
        self.assertTrue(any('日期' in e for e in self.inspect()['errors']))
        image=next(self.root.glob('IMG-HH*'));image.rename(image.with_name(image.name.replace('IMG-HH','IMG-HV')))
        self.assertTrue(any('極化' in e for e in self.inspect()['errors']))

    def test_mixed_direction_suggests_groups(self):
        self.product();self.product('200201',direction='DESCEND')
        result=self.inspect();self.assertEqual(len(result['groups']),2)
        self.assertTrue(any('候選組' in e for e in result['errors']))

    def test_no_spatial_overlap_and_same_day_different_orbits(self):
        self.product();self.product('200201',x=130)
        self.assertTrue(any('共同' in e for e in self.inspect()['errors']))
        self.product(frame='0490',orbit='00003')
        self.assertTrue(any('圈次' in e for e in self.inspect()['errors']))

    def test_zip_is_bounded_then_footprint_available_after_extract(self):
        self.product();self.product('200201')
        archive=self.root/'data.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for f in list(self.root.iterdir()):
                if f!=archive:z.write(f,f.name)
        from alosstack_helper.metadata_worker import inspect
        plan=scan_source(archive);result=inspect(plan['entries'])
        self.assertFalse(result['complete']);self.assertIsNone(result['bounds']);self.assertEqual(result['errors'],[])
        organized=organize(plan,self.root/'extracted')
        result=inspect(scan_source(organized['path'])['entries'])
        self.assertTrue(result['complete'])

    def test_signed_southern_western_coordinates(self):
        self.product(x=-70,y=-33);self.product('200201',x=-69.9,y=-32.9)
        self.assertEqual(self.inspect()['bounds'],[-33,-31.9,-70,-68.9])

    def test_truncated_image_and_source_change(self):
        self.product();self.product('200201')
        from alosstack_helper.metadata_worker import inspect
        plan=scan_source(self.root);f=next(self.root.glob('IMG-*'));f.write_bytes(f.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError,'來源'):inspect(plan['entries'])
        self.assertTrue(any('大小' in e for e in self.inspect()['errors']))

    def test_dateline_and_degenerate_boundary_never_get_auto_bounds(self):
        self.product(x=179.5);self.product('200201')
        result=self.inspect();self.assertFalse(result['complete']);self.assertIsNone(result['bounds'])

    def test_adapter_and_group_selection_revalidate_source(self):
        self.product();self.product('200201');self.product('200301',direction='DESCEND')
        manager=Onboarding(self.root/'home')
        result=manager.scan(str(self.root),os.environ['ALOSSTACK_TEST_ENV'])
        self.assertFalse(result['can_import'])
        group=next(g for g in result['metadata']['groups'] if g['direction']=='ascending')
        selected=manager.select(result['id'],group['keys'])
        self.assertTrue(selected['can_import']);self.assertEqual(len(selected['dates']),2)
        self.assertNotIn('entries',selected)
