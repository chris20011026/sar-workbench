"""Adopt external scientific results without executing or modifying the source."""
from pathlib import Path
import hashlib
import json
import os
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from alosstack_helper.engine import Engine
from alosstack_helper.legacy import LegacyImports, create_wrapper, inspect_legacy, require_legacy_source
from alosstack_helper.workflows import inventory


def prop(node, name, value):
    child = ET.SubElement(node, 'property', name=name)
    ET.SubElement(child, 'value').text = str(value)


def component(parent, name, module, factory):
    out = ET.SubElement(parent, 'component', name=name)
    ET.SubElement(out, 'factorymodule').text = module
    ET.SubElement(out, 'factoryname').text = factory
    return out


def metadata(path, kind):
    root = ET.Element('productmanager_name')
    instance = component(root, 'instance', 'isceobj.Sensor.MultiMode.' + kind, kind)
    for key, value in dict(numberofsamples=256, numberoflines=256, startingrange=700000., rangesamplingrate=32000000.,
                           rangepixelsize=2., pulserepetitionfrequency=2000., azimuthpixelsize=3., azimuthlineinterval=.002,
                           sensingstart='2018-11-03 12:00:00').items(): prop(instance, key, value)
    if kind == 'Track':
        for key, value in dict(passdirection='ascending', pointingdirection='right', operationmode='SM3', radarwavelength=.236).items(): prop(instance, key, value)
        orbit = component(instance, 'orbit', 'isceobj.Orbit.Orbit', 'createOrbit')
        seq = component(orbit, 'state_vectors', 'iscesys.Component', 'createTraitSeq')
        for i, t in enumerate(['11:59:20','11:59:40','12:00:00','12:00:20','12:00:40'], 1):
            row = component(seq, 'statevector' + str(i), 'isceobj.Orbit.Orbit', 'StateVector')
            for key, value in dict(position=[7000000., (i-3)*140000., 10000.], velocity=[0.,7000.,0.],time='2018-11-03 '+t).items(): prop(row,key,value)
    else:
        prop(instance, 'framenumber', '0480')
        seq = component(instance, 'swaths', 'iscesys.Component', 'createTraitSeq')
        row = component(seq, 'swath1', 'isceobj.Sensor.MultiMode.Swath', 'Swath')
        for key, value in dict(swathnumber=1, polarization='HH', rangebandwidth=28000000.).items(): prop(row,key,value)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path)


def raster(path, kind='Float32', bands=1, width=32, height=16, value=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    size, fmt, isce = {'Float32': (4,'<f','FLOAT'), 'Float64': (8,'<d','DOUBLE'), 'Byte':(1,'B','BYTE')}[kind]
    path.write_bytes(struct.pack(fmt,value)*(width*height*bands))
    x=ET.Element('imageFile')
    for key,val in dict(width=width,length=height,number_bands=bands,data_type=isce,scheme='BIL',byte_order='l',file_name=str(path)).items():prop(x,key,val)
    ET.ElementTree(x).write(str(path)+'.xml')
    v=ET.Element('VRTDataset',rasterXSize=str(width),rasterYSize=str(height))
    for i in range(bands):
        b=ET.SubElement(v,'VRTRasterBand',band=str(i+1),dataType=kind,subClass='VRTRawRasterBand')
        ET.SubElement(b,'SourceFilename',relativeToVRT='1').text=path.name
        for key,val in dict(ImageOffset=i*width*size,PixelOffset=size,LineOffset=width*size*bands,ByteOrder='LSB').items():ET.SubElement(b,key).text=str(val)
    ET.ElementTree(v).write(str(path)+'.vrt')


def dataset(base, environment=None):
    """Public synthetic fixture, also used by the installed-environment check."""
    source=base/'manual'/'proc';source.mkdir(parents=True)
    pairs=['170826-181103','170826-190518','181103-190518'];suffix='_8rlks_16alks'
    for pair in pairs:
        p=source/'pairs'/pair/'insar'
        raster(p/f'filt_{pair}{suffix}.unw',bands=2)
        raster(p/f'filt_{pair}{suffix}_msk.unw',bands=2)
        raster(p/f'{pair}{suffix}.cor',bands=2,value=.9)
        raster(p/f'filt_{pair}{suffix}.unw.conncomp',kind='Byte')
    geom=source/'dates_resampled/181103/insar'
    for ext in ('hgt','lat','lon','los','wbd'):
        kind='Float64' if ext in ('lat','lon') else 'Byte' if ext=='wbd' else 'Float32'
        value={'hgt':100.,'lat':25.,'lon':121.,'los':35.,'wbd':0}[ext]
        raster(geom/f'181103{suffix}.{ext}',kind=kind,bands=2 if ext=='los' else 1,value=value)
    metadata(geom.parent/'181103.track.xml','Track');metadata(geom.parent/'f1_0480/181103.frame.xml','Frame')
    (source/'baseline').mkdir();(source/'baseline/baseline_center.txt').write_text('reference secondary parallel perpendicular\nYYMMDD YYMMDD meters meters\n181103 170826 2.0 12.0\n181103 190518 3.0 24.0\n')
    env=environment or base/'environment'
    if not environment:
        (env/'bin').mkdir(parents=True);(env/'bin/python').touch();(env/'bin/conda').touch()
    return dict(source=str(source),root=str(base/'adopted'),name='既有成果測試',environment=str(env)),pairs


class LegacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.request,self.pairs=dataset(self.base)
        self.source=Path(self.request['source']);self.engine=Engine(self.base/'registry');self.service=LegacyImports(self.engine)

    def tearDown(self):
        self.service.shutdown();self.tmp.cleanup()

    def hashes(self):
        return {str(p.relative_to(self.source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.rglob('*') if p.is_file()}

    def wait(self,token):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            result=self.service.get(token)
            if result['status'] not in ('running','importing'):return result
            time.sleep(.03)
        self.fail('background import did not finish')

    def test_read_only_preview_and_independent_wrapper_inventory(self):
        before=self.hashes();report=inspect_legacy(self.request)
        self.assertEqual(report['pairs'],self.pairs);self.assertEqual(report['reference'],'181103')
        self.assertEqual((report['width'],report['height']),(32,16));self.assertFalse(Path(self.request['root']).exists())
        state=create_wrapper(self.request,report,'aabbccddeeff')
        self.assertEqual(state['status'],'imported');self.assertTrue(state['import_only']);self.assertEqual(state['history'],[])
        self.assertIsNone(state['ion']['apply']);self.assertEqual(before,self.hashes())
        require_legacy_source(state)
        # Existing inventory support accepts imported, not fabricated processing completion.
        inv=inventory(state)
        self.assertEqual(len(inv['pairs']),3);self.assertEqual(inv['width'],32)
        raw=Path(inv['pairs'][0]['unw']);self.assertTrue(raw.is_symlink());self.assertTrue(raw.resolve().is_relative_to(self.source.resolve()))
        self.assertFalse(Path(str(raw)+'.xml').is_symlink())

    def test_complete_service_requires_preview_and_explicit_consent(self):
        job=self.service.preview(self.request);token=job['preview_id'];ready=self.wait(token)
        self.assertEqual(ready['status'],'ready',ready)
        self.assertNotIn('metadata',ready['preview']);self.assertNotIn('manifest',ready['preview'])
        with self.assertRaisesRegex(ValueError,'確認'):self.service.create(token,False)
        self.assertFalse(self.engine.projects)
        self.service.create(token,True);result=self.wait(token)
        self.assertEqual(result['status'],'complete',result)
        self.assertEqual(self.engine.get(result['project_id'])['status'],'imported')

    def test_engine_busy_blocks_preview_and_create(self):
        self.engine.active='science'
        with self.assertRaisesRegex(ValueError,'已有工作'):self.service.preview(self.request)
        self.engine.active=None;token=self.service.preview(self.request)['preview_id'];self.wait(token);self.engine.active='science'
        with self.assertRaisesRegex(ValueError,'已有工作'):self.service.create(token,True)
        self.assertFalse(Path(self.request['root']).exists());self.engine.active=None

    def test_missing_geometry_is_actionable_without_writes(self):
        next(self.source.glob('dates_resampled/*/insar/*.wbd')).unlink()
        with self.assertRaisesRegex(ValueError,'參考日期|幾何'):inspect_legacy(self.request)
        self.assertFalse(Path(self.request['root']).exists())

    def test_duplicate_looks_and_disconnected_pairs_rejected(self):
        pair=self.pairs[0];p=self.source/'pairs'/pair/'insar'/f'filt_{pair}_4rlks_4alks.unw';raster(p,bands=2)
        with self.assertRaisesRegex(ValueError,'單一 looks'):inspect_legacy(self.request)
        p.unlink()
        (self.source/'pairs/200101-210101').mkdir()
        with self.assertRaisesRegex(ValueError,'不連通'):inspect_legacy(self.request)

    def test_nonempty_or_empty_existing_destination_never_overwritten(self):
        root=Path(self.request['root']);root.mkdir()
        for content in ('', 'user data'):
            if content:(root/'keep').write_text(content)
            with self.assertRaisesRegex(ValueError,'新資料夾'):inspect_legacy(self.request)
        self.assertEqual((root/'keep').read_text(),'user data')

    def test_source_root_selection_and_nested_destination_rejection(self):
        request=dict(self.request,source=str(self.source.parent));self.assertEqual(inspect_legacy(request)['source'],str(self.source.resolve()))
        with self.assertRaisesRegex(ValueError,'互相包含'):inspect_legacy(dict(self.request,root=str(self.source/'new')))

    def test_stale_preview_rejects_mutation_without_creating_wrapper(self):
        report=inspect_legacy(self.request);path=next(self.source.glob('pairs/*/insar/*.cor'))
        with path.open('r+b') as f:f.write(struct.pack('<f',.1))
        with self.assertRaisesRegex(ValueError,'變更'):create_wrapper(self.request,report,'aabbccddeeff')
        self.assertFalse(Path(self.request['root']).exists())

    def test_changed_destination_or_environment_requires_new_preview(self):
        report=inspect_legacy(self.request)
        with self.assertRaisesRegex(ValueError,'變更'):create_wrapper(dict(self.request,root=str(self.base/'different')),report,'aabbccddeeff')
        self.assertFalse((self.base/'different').exists())

    def test_xml_entity_and_malformed_xml_rejected(self):
        path=next(self.source.glob('pairs/*/insar/*.cor.xml'))
        for content in ('<imageFile>', '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///tmp/private">]><imageFile/>'):
            path.write_text(content)
            with self.assertRaisesRegex(ValueError,'XML'):inspect_legacy(self.request)

    def test_external_symlink_and_vrt_source_rejected(self):
        path=next(self.source.glob('pairs/*/insar/*.cor'));outside=self.base/'other.cor';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'以外'):inspect_legacy(self.request)
        path.unlink();path.write_bytes(outside.read_bytes());vrt=Path(str(path)+'.vrt');tree=ET.parse(vrt)
        for node in tree.findall('.//SourceFilename'):node.text='../../../../../../outside.cor'
        tree.write(vrt)
        with self.assertRaises(ValueError):inspect_legacy(self.request)

    def test_vrt_derived_bands_and_non_little_endian_rejected(self):
        vrt=next(self.source.glob('pairs/*/insar/*.cor.vrt'));original=vrt.read_text()
        vrt.write_text(original.replace('VRTRawRasterBand','VRTDerivedRasterBand'))
        with self.assertRaisesRegex(ValueError,'VRT'):inspect_legacy(self.request)
        vrt.write_text(original.replace('LSB','MSB'))
        with self.assertRaisesRegex(ValueError,'endian'):inspect_legacy(self.request)

    def test_source_metadata_factory_not_loaded_and_injections_not_copied(self):
        path=next(self.source.glob('dates_resampled/*/*.track.xml'));tree=ET.parse(path);instance=tree.getroot().find('component')
        marker=self.base/'executed';expression=f"__import__('pathlib').Path({str(marker)!r}).touch()"
        prop(instance,'ignored_expression',expression)
        # Even source nested factory strings are treated as inert input and discarded.
        orbit=instance.find("component[@name='orbit']");orbit.find('factorymodule').text='os';orbit.find('factoryname').text='system'
        ET.SubElement(orbit,'args').text='('+repr('touch '+str(marker))+',)';tree.write(path)
        report=inspect_legacy(self.request);state=create_wrapper(self.request,report,'aabbccddeeff')
        self.assertFalse(marker.exists());clean=Path(state['config']['root'])/'proc/dates_resampled/181103/181103.track.xml'
        self.assertNotIn(expression,clean.read_text());self.assertNotIn('system',clean.read_text())
        # Executable external catalogs are rejected entirely.
        ET.SubElement(orbit,'catalog').text='/outside/config.xml';tree.write(path)
        with self.assertRaisesRegex(ValueError,'非靜態'):inspect_legacy(dict(self.request,root=str(self.base/'another')))

    def test_scripts_pickle_and_shelve_never_read_or_copied(self):
        (self.source/'evil.pkl').write_bytes(b'not a pickle');(self.source/'cmd4').write_text('exit 42');(self.source/'reference.db').write_bytes(b'not a database')
        state=create_wrapper(self.request,inspect_legacy(self.request),'aabbccddeeff');root=Path(state['config']['root'])
        self.assertFalse((root/'proc/cmd4').exists());self.assertFalse((root/'proc/evil.pkl').exists())

    def test_import_receipt_checks_source_and_wrapper_tampering(self):
        state=create_wrapper(self.request,inspect_legacy(self.request),'aabbccddeeff');require_legacy_source(state)
        root=Path(state['config']['root']);path=next((root/'proc').glob('pairs/*/insar/*.cor.xml'));path.write_text('<imageFile/>')
        with self.assertRaisesRegex(ValueError,'描述資料'):require_legacy_source(state)

    def test_import_receipt_rejects_changed_raster_link(self):
        state=create_wrapper(self.request,inspect_legacy(self.request),'aabbccddeeff');root=Path(state['config']['root'])
        path=next((root/'proc').glob('pairs/*/insar/*.cor'));outside=self.base/'outside';outside.write_bytes(path.read_bytes());path.unlink();path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'連結'):require_legacy_source(state)

    def test_import_receipt_rejects_changed_source(self):
        state=create_wrapper(self.request,inspect_legacy(self.request),'aabbccddeeff');path=self.source/'baseline/baseline_center.txt';path.write_text(path.read_text()+'\n')
        with self.assertRaisesRegex(ValueError,'來源已變更'):require_legacy_source(state)

    def test_invalid_token_and_restart_do_not_adopt_partial_job(self):
        for token in ('../outside','x',None):
            with self.assertRaises(ValueError):self.service.get(token)
        with patch('threading.Thread.start'):
            token=self.service.preview(self.request)['preview_id']
        self.service.processes.clear();self.engine.active=None
        self.assertEqual(LegacyImports(self.engine).get(token)['status'],'interrupted')
        self.assertFalse(self.engine.projects)

    def test_stop_before_worker_launch_does_not_register_project(self):
        with patch('threading.Thread.start'):
            token=self.service.preview(self.request)['preview_id'];self.service.stop(token)
        self.service._worker(token,'preview')
        self.assertEqual(self.service.get(token)['status'],'stopped');self.assertFalse(self.engine.projects);self.assertIsNone(self.engine.active)

    def test_missing_baseline_date_and_conflicting_reference_rejected(self):
        path=self.source/'baseline/baseline_center.txt';path.write_text('one\ntwo\n181103 170826 2 12\n')
        with self.assertRaisesRegex(ValueError,'baseline'):inspect_legacy(self.request)


if __name__=='__main__':unittest.main()
