import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from alosstack_helper.metadata_worker import inspect, summarize
from alosstack_helper.onboarding import scan_source
from alosstack_helper.alos4_quality import verify_conversion_metadata
from test_alos4_quality import fixture, SCHEMA

@unittest.skipUnless(SCHEMA, 'Requires ISCE2 schema')
class ConvertedMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/private/tmp' if Path('/private/tmp').exists() else None)
        self.source,self.output,_=fixture(Path(self.tmp.name))
    def tearDown(self):self.tmp.cleanup()
    def manifest(self):
        (self.output/'conversion.json').write_text(json.dumps(dict(source_sensor='ALOS-4',output_format='PALSAR-2 CEOS compatibility',source=str(self.source))))
    def test_bounded_provenance_reads_no_pixel_stream(self):
        with patch('alosstack_helper.alos4_quality._image',side_effect=AssertionError('must not read pixels')):
            result=verify_conversion_metadata(self.source,self.output)
        self.assertEqual(result['status'],'passed')
        self.assertIn('not checked',result['scope'])
    def test_real_ceos_orbit_exception_requires_validated_original(self):
        entries=scan_source(self.output)['entries']
        self.assertTrue(any('軌道圈次' in e for e in inspect(entries)['errors']))
        self.manifest()
        result=inspect(entries,True)
        self.assertFalse(any('軌道圈次' in e for e in result['errors']))
        self.assertEqual(result['products'][0]['source_sensor'],'ALOS-4')
        self.assertTrue(result['warnings'])
    def test_changed_orbit_is_not_excused_by_manifest(self):
        self.manifest()
        p=next(self.output.glob('LED-*'));data=bytearray(p.read_bytes());data[720+444:720+452]=b'   99999';p.write_bytes(data)
        self.assertTrue(inspect(scan_source(self.output)['entries'],True)['errors'])
    def test_missing_original_is_blocked(self):
        self.manifest();next(self.source.glob('LED-*')).unlink()
        self.assertTrue(inspect(scan_source(self.output)['entries'],True)['errors'])

class GroupingTests(unittest.TestCase):
    def product(self,key,mission,sensor='ALOS2 -L -0315-'):
        return dict(key=key,date=key,frame='0480',direction='ascending',look='right',sensor_mode='03',product_mode='FBDR1.1__A',sensor_id=sensor,source_sensor=mission,orbit='1',errors=[],warnings=[],polarizations=['HH'],footprint=None)
    def test_cross_sensor_requires_explicit_sampling_gate(self):
        rows=[self.product('240822','ALOS-2'),self.product('250425','ALOS-4','ALOS2 -L -0315')]
        self.assertEqual(len(summarize(rows)['groups']),2)
        self.assertEqual(len(summarize(rows,True)['groups']),1)
        rows[1]['direction']='descending'
        self.assertEqual(len(summarize(rows,True)['groups']),2)
