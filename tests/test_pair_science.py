"""Scientific format/coordinate checks, using small synthetic GDAL/ISCE grids."""
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/alosstack_helper'))
try:
    import pair_worker as worker
    import numpy as np
    from osgeo import gdal,osr
    from PIL import Image
except ImportError:
    worker=None

@unittest.skipIf(worker is None,'requires numpy, GDAL, matplotlib and Pillow')
class PairScienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);self.out=self.p/'out';self.out.mkdir()
        sr=osr.SpatialReference();sr.ImportFromEPSG(4326);self.wkt=sr.ExportToWkt()
        self.a=np.arange(120,dtype='float32').reshape(10,12)/10;self.a[0]=0
        ds=gdal.GetDriverByName('GTiff').Create(str(self.p/'source.tif'),12,10,2,gdal.GDT_Float32)
        ds.SetGeoTransform([121,.001,0,25,0,-.001]);ds.SetProjection(self.wkt);ds.GetRasterBand(1).WriteArray(self.a+10);ds.GetRasterBand(2).WriteArray(self.a);ds=None
        gdal.Translate(str(self.p/'source.vrt'),str(self.p/'source.tif'),format='VRT')
    def tearDown(self):self.tmp.cleanup()
    def test_geotiff_exports_phase_band_units_nodata_without_modifying_input(self):
        before=(self.p/'source.tif').read_bytes()
        p=worker.geotiff(self.p/'source',self.out,'phase.tif',2)
        ds=gdal.Open(str(p));np.testing.assert_array_equal(ds.ReadAsArray(),self.a);self.assertEqual(ds.GetRasterBand(1).GetUnitType(),'rad');self.assertEqual(ds.GetRasterBand(1).GetNoDataValue(),0);self.assertIn('4326',ds.GetProjection());ds=None
        self.assertEqual((self.p/'source.tif').read_bytes(),before)
    def test_geographic_preview_bounds_transparency_and_real_colour_limits(self):
        p=worker.geotiff(self.p/'source',self.out,'phase.tif',2)
        layer=worker.geographic_layer(p,self.out,'map.png','phase','rad','RdBu_r',[-12,12])
        self.assertAlmostEqual(layer['bounds'][0][1],121,places=4);self.assertAlmostEqual(layer['bounds'][1][0],25,places=4)
        self.assertEqual(layer['min'],-12);self.assertEqual(layer['max'],12);self.assertEqual(len(layer['lut']),256)
        
        with Image.open(self.out/'map.png') as im:rgba=np.array(im)
        self.assertTrue((rgba[:,:,3]==0).any());self.assertTrue((rgba[:,:,3]==255).any())
    def test_offline_dem_background_matches_map_dimensions(self):
        p=worker.geotiff(self.p/'source',self.out,'phase.tif',2);layer=worker.geographic_layer(p,self.out,'map.png','phase','rad','RdBu_r')
        # A DEM is a single-band image.
        gdal.Translate(str(self.p/'dem.vrt'),str(p),format='VRT')
        bg=worker.terrain_background(self.p/'dem',layer,self.out)
        self.assertEqual(bg['bounds'],layer['bounds'])
        with Image.open(self.out/'terrain.png') as a, Image.open(self.out/'map.png') as b:self.assertEqual(a.size,b.size)
    def test_corrupt_raw_raster_length_is_rejected(self):
        f=self.p/'small';f.write_bytes(b'\x00'*16)
        Path(str(f)+'.vrt').write_text(f'''<VRTDataset rasterXSize="2" rasterYSize="2"><VRTRasterBand dataType="Float32" subClass="VRTRawRasterBand"><SourceFilename relativeToVRT="0">{f}</SourceFilename><ImageOffset>0</ImageOffset><PixelOffset>4</PixelOffset><LineOffset>8</LineOffset></VRTRasterBand></VRTDataset>''')
        self.assertEqual(worker.checked_raster(f)[0].shape,(2,2));f.write_bytes(b'\x00'*12)
        with self.assertRaises(ValueError):worker.checked_raster(f)

if __name__=='__main__':unittest.main()
