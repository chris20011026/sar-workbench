from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from alosstack_helper.inputs import prepare_raster

class RasterInputTests(unittest.TestCase):
    def test_relocated_xml_vrt_are_fixed_without_changing_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'height.dem';source.write_bytes(b'original raster')
            xml=Path(str(source)+'.xml');vrt=Path(str(source)+'.vrt')
            xml.write_text('<imageFile><property name="file_name"><value>/old/nas/height.dem</value></property><property name="width"><value>42</value></property></imageFile>')
            vrt.write_text('<VRTDataset><VRTRasterBand><SourceFilename relativeToVRT="1">height.dem</SourceFilename></VRTRasterBand></VRTDataset>')
            before=[p.read_bytes() for p in (source,xml,vrt)]
            target=prepare_raster(source,root/'project/inputs')
            self.assertTrue(Path(target).is_symlink())
            tree=ET.parse(target+'.xml');self.assertEqual(tree.findtext(".//property[@name='file_name']/value"),target)
            self.assertEqual(tree.findtext(".//property[@name='width']/value"),'42')
            node=ET.parse(target+'.vrt').find('.//SourceFilename')
            self.assertEqual(node.text,target);self.assertEqual(node.get('relativeToVRT'),'0')
            self.assertEqual(before,[p.read_bytes() for p in (source,xml,vrt)])
            self.assertEqual(prepare_raster(source,root/'project/inputs'),target)

    def test_missing_or_external_descriptor_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'a.dem';source.touch()
            Path(str(source)+'.xml').write_text('<imageFile/>')
            with self.assertRaises(ValueError):prepare_raster(source,Path(d)/'out')
