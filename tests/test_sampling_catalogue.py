"""Read-only sampling review catalogue; synthetic geometry and CEOS headers."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper import sampling_review_worker as worker


SCIENCE = bool(os.environ.get('ALOSSTACK_TEST_ENV'))


@unittest.skipUnless(SCIENCE, 'Requires installed ISCE2 scalar CEOS decoder')
class CeosPreviewLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'IMG-HH-fixture'

    def tearDown(self):
        self.tmp.cleanup()

    def image(self, prefix=544):
        width,length=2,3
        raw=bytearray(b' '*720)
        raw[8:12]=struct.pack('>I',720)
        # Explicit CEOS table offsets, independent of the installed XML parser.
        values=[(186,6,prefix+width*8),(216,4,32),(220,4,2),(224,4,8),
                (232,4,1),(244,4,0),(256,4,0),(268,4,'BSQ'),(272,2,1),
                (276,4,prefix),(288,4,0),(400,28,'COMPLEX*8'),(428,4,'C*8')]
        for offset,size,value in values:
            raw[offset:offset+size]=str(value).encode('ascii').ljust(size)
        self.path.write_bytes(raw+b'\0'*((prefix+width*8)*length))
        return dict(width=width,lines=length)

    def test_only_known_float_complex_p2_and_p3_layouts(self):
        for label,prefix in [('PALSAR-2',544),('PALSAR-3',800)]:
            image=self.image(prefix)
            result=worker.ceos_layout(self.path,label,image)
            self.assertEqual(result['byte_order'],'MSB')
            self.assertEqual(result['image_offset'],720+prefix)
            self.assertEqual(result['line_offset'],prefix+16)

    def test_little_endian_descriptor_wrong_prefix_and_non_float_types_rejected(self):
        image=self.image()
        original=self.path.read_bytes()
        for offset,values in [(8,struct.pack('<I',720)),(276,b' 800'),
                              (400,b'COMPLEX INTEGER'.ljust(28)),(428,b'C*4 '),
                              (268,b'BIL '),(272,b' 2'),(288,b'  16'),
                              (244,b'   1'),(256,b'   1')]:
            raw=bytearray(original);raw[offset:offset+len(values)]=values;self.path.write_bytes(raw)
            with self.subTest(offset=offset),self.assertRaises(ValueError):
                worker.ceos_layout(self.path,'PALSAR-2',image)

    def test_truncated_or_padded_source_rejected(self):
        image=self.image();original=self.path.read_bytes()
        for data in (original[:-1],original+b'padding'):
            self.path.write_bytes(data)
            with self.assertRaisesRegex(ValueError,'尺寸'):
                worker.ceos_layout(self.path,'PALSAR-2',image)


@unittest.skipUnless(SCIENCE, 'Requires selected science environment (Shapely/ISCE2)')
class SamplingCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'data';self.source.mkdir()
        self.extra=self.root/'extra';self.extra.mkdir()
        self.project=self.root/'project';(self.project/'proc/dates').mkdir(parents=True)
        self.request=dict(config=dict(data=str(self.source),root=str(self.project),
                                     properties={'polarization':'HH'}),
                          plan=dict(plan_id='manual-plan',reference='250425',dates=['240822','250425'],rows=[]))
        self.dossiers={}

    def tearDown(self):
        self.tmp.cleanup()

    def product(self,date,frame,x=121,y=24,selected=True,extra=False):
        folder=(self.extra if extra else self.source)/date;folder.mkdir(exist_ok=True)
        leader=folder/f'LED-ALOS2{date}-{frame}'
        leader.write_bytes(b'synthetic leader')
        image=folder/f'IMG-HH-ALOS2{date}-{frame}';image.write_bytes(b'\0'*32)
        dossier=dict(id=f'{date}-{frame}',date='20'+date,sensor='ALOS-2',
            metadata=dict(frame=frame,layout='PALSAR-2',orbit_direction='ascending',look_direction='right'),
            footprint_status='metadata_outline',
            footprint=dict(type='Polygon',coordinates=[[[x,y],[x+1,y],[x+1,y+1],[x,y+1],[x,y]]]),
            images=[dict(name=image.name,polarization='HH',width=2,lines=2)],warnings=[],errors=[],
            source_files=[worker.stamp(leader),worker.stamp(image)])
        self.dossiers[leader]=dossier
        if selected:
            self.request['plan']['rows'].append(dict(date=date,frame=frame,raw=str(image)))
        return leader,dossier

    def catalogue(self):
        def image(path,layout,im):
            return dict(path=str(path),width=im['width'],length=im['lines'],
                        image_offset=0,line_offset=im['width']*8,byte_order='MSB')
        with patch.object(worker,'read_product',side_effect=lambda p:copy.deepcopy(self.dossiers[p])), \
             patch.object(worker,'ceos_layout',side_effect=image):
            return worker.catalogue(self.request)

    def test_candidates_are_display_only_and_never_change_plan_or_approve(self):
        self.product('240822','0480')
        self.product('250425','0480',x=121.2)
        self.product('240822','0490',x=121.8,selected=False,extra=True)
        self.request['extra_source']=str(self.extra)
        original=copy.deepcopy(self.request)
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file()}
        result=self.catalogue()
        self.assertEqual(self.request,original)
        self.assertEqual(len(result['products']),3)
        candidate=next(p for p in result['products'] if p['frame']=='0490')
        self.assertFalse(candidate['selected'])
        self.assertEqual(sum(p['selected'] for p in result['products']),2)
        self.assertNotIn('frames_verified',result)
        self.assertNotIn('sampling_approval',result)
        self.assertFalse((self.project/'proc/helper_sampling_approval.json').exists())
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in self.root.rglob('*') if p.is_file()})

    def test_common_coverage_unions_each_dates_frames_before_intersection(self):
        from shapely.geometry import shape
        self.product('240822','0480',x=121)
        self.product('240822','0490',x=122)
        self.product('250425','0480',x=121.5)
        self.product('250425','0999',x=135,selected=False)
        result=self.catalogue()
        self.assertTrue(result['coverage']['complete'])
        common=shape(result['coverage']['common'])
        self.assertEqual(common.bounds,(121.5,24,122.5,25))
        self.assertAlmostEqual(common.area,1)

    def test_incomplete_or_invalid_geometry_cannot_claim_complete_overlap(self):
        self.product('240822','0480')
        _,item=self.product('250425','0480')
        original=copy.deepcopy(item['footprint'])
        cases=[None,{'type':'Polygon','coordinates':[[[121,24],[122,25],[122,24],[121,25],[121,24]]]},
               {'type':'Polygon','coordinates':[[[500,24],[501,24],[501,25],[500,25],[500,24]]]}]
        for geometry in cases:
            item['footprint']=geometry
            with self.subTest(geometry=geometry):
                result=self.catalogue()
                self.assertFalse(result['coverage']['complete'])
                self.assertIsNone(result['coverage']['common'])
                self.assertTrue(result['warnings'])
        item['footprint']=original
        item['errors']=['metadata mismatch']
        self.assertFalse(self.catalogue()['coverage']['complete'])

    def test_missing_selected_product_remains_incomplete(self):
        self.product('240822','0480')
        self.product('250425','0480')
        self.request['plan']['rows'].append(dict(date='240822',frame='0490',raw='/missing'))
        self.assertFalse(self.catalogue()['coverage']['complete'])

    def test_disjoint_selected_frames_are_complete_but_have_no_common_area(self):
        self.product('240822','0480')
        self.product('250425','0480',x=135)
        result=self.catalogue()
        self.assertTrue(result['coverage']['complete'])
        self.assertIsNone(result['coverage']['common'])
        self.assertTrue(any('沒有共同' in w for w in result['warnings']))

    def test_common_coverage_preserves_polygons_when_another_frame_only_touches(self):
        from shapely.geometry import box, mapping, shape
        _,a=self.product('240822','0480')
        _,b=self.product('240822','0490')
        _,c=self.product('250425','0480')
        a['footprint']=mapping(box(120,23,122,25))
        b['footprint']=mapping(box(123,23,124,24))
        c['footprint']=mapping(box(121,23,123,25))
        # The full intersection contains both a polygon and an isolated line.
        result=self.catalogue()
        self.assertTrue(result['coverage']['complete'])
        common=shape(result['coverage']['common'])
        self.assertEqual(common.geom_type,'Polygon')
        self.assertEqual(common.bounds,(121,23,122,25))
        self.assertAlmostEqual(common.area,2)
        self.assertFalse(any('沒有共同' in w for w in result['warnings']))

    def test_touching_selected_frames_do_not_claim_area_overlap(self):
        self.product('240822','0480',x=121)
        self.product('250425','0480',x=122)
        result=self.catalogue()
        self.assertTrue(result['coverage']['complete'])
        self.assertIsNone(result['coverage']['common'])
        self.assertTrue(any('沒有共同' in w for w in result['warnings']))

    def test_post_preview_uses_actual_physical_output_and_distinct_kind(self):
        self.product('240822','0480')
        self.product('250425','0480')
        relative='240822/f1_0480/s1/240822.slc'
        slc=self.project/'proc/dates'/relative;slc.parent.mkdir(parents=True);slc.write_bytes(b'\0'*96)
        for suffix in ('.xml','.vrt'):Path(str(slc)+suffix).write_text('descriptor')
        self.request['post']=dict(rows=[dict(date='240822',frame='0480',relative=relative,width=3,length=4)])
        info=dict(raw=str(slc),image_offset=0,line_offset=24,byte_order='LSB')
        with patch.object(worker,'image_info',return_value=info) as read:
            result=self.catalogue()
        read.assert_called_once_with(slc,3,4,self.source,self.project/'proc/dates')
        product=next(p for p in result['products'] if p['date']=='240822')
        self.assertEqual([i['kind'] for i in product['images']],['source','equalized'])
        equalized=product['images'][1]
        self.assertEqual((equalized['width'],equalized['length']),(3,4))
        self.assertNotEqual(product['images'][0]['id'],equalized['id'])
        self.assertEqual(result['_image_specs'][equalized['id']]['path'],str(slc))
        self.assertEqual(result['_image_specs'][equalized['id']]['byte_order'],'LSB')

    def test_post_relative_escape_rejected(self):
        self.product('240822','0480')
        self.product('250425','0480')
        for relative in ('../outside.slc','/outside.slc'):
            self.request['post']=dict(rows=[dict(date='240822',frame='0480',relative=relative,width=3,length=4)])
            with self.subTest(relative=relative),self.assertRaisesRegex(ValueError,'相對路徑'):
                self.catalogue()

    def test_metadata_changes_after_read_are_rejected(self):
        leader,_=self.product('240822','0480')
        self.product('250425','0480')
        leader.write_bytes(b'changed after metadata snapshot')
        with self.assertRaisesRegex(ValueError,'中繼資料檢查後改變'):
            self.catalogue()

    def test_changed_source_late_in_catalogue_is_rejected(self):
        leader,_=self.product('240822','0480')
        self.product('250425','0480')
        original=worker.stamp
        calls=0
        def changing(path):
            nonlocal calls
            calls+=1
            # Two products have been read; change the first before final audit.
            if calls==7:leader.write_bytes(b'changed near end')
            return original(path)
        with patch.object(worker,'stamp',side_effect=changing),self.assertRaisesRegex(ValueError,'比較期間改變'):
            self.catalogue()


if __name__=='__main__':
    unittest.main()
