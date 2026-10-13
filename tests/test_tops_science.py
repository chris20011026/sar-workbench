"""Small native GDAL checks; not real Sentinel-1 validation."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/alosstack_helper'))
try:
    import tops_worker as worker
    import numpy as np
    from osgeo import gdal,osr
    from PIL import Image
except ImportError:worker=None

@unittest.skipIf(worker is None,'requires scientific environment')
class TopsScienceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.old=Path.cwd();os.chdir(self.root);Path('merged').mkdir();self.meta=self.root/'.helper';self.meta.mkdir();Path('topsApp.xml').write_text('<test/>')
        sr=osr.SpatialReference();sr.ImportFromEPSG(4326);self.wkt=sr.ExportToWkt();self.gt=[121,.001,0,25,0,-.001]
        phase=np.arange(120,dtype='float32').reshape(10,12);cor=np.ones_like(phase)*.7;comp=np.ones_like(phase,dtype='uint8');comp[-1]=0
        self.raw('merged/filt_topophase.unw.geo',[np.ones_like(phase),phase],'Float32');self.raw('merged/phsig.cor.geo',[cor],'Float32');self.raw('merged/filt_topophase.unw.conncomp.geo',[comp],'Byte');self.raw('merged/filt_topophase.flat.geo',[np.exp(1j*phase).astype('complex64')],'CFloat32');self.raw('dem',[phase+100],'Float32')
        self.state={'prepared_inputs':{'dem':str(self.root/'dem')}}
    def raw(self,name,bands,kind):
        a=np.stack(bands);Path(name).write_bytes(a.tobytes());n=a.dtype.itemsize;w,h=a.shape[2],a.shape[1]
        text=f'<VRTDataset rasterXSize="{w}" rasterYSize="{h}"><SRS>{self.wkt}</SRS><GeoTransform>'+','.join(map(str,self.gt))+'</GeoTransform>'
        for i,b in enumerate(bands):text+=f'<VRTRasterBand dataType="{kind}" band="{i+1}" subClass="VRTRawRasterBand"><SourceFilename relativeToVRT="0">{self.root/name}</SourceFilename><ImageOffset>{i*w*h*n}</ImageOffset><PixelOffset>{n}</PixelOffset><LineOffset>{w*n}</LineOffset><ByteOrder>LSB</ByteOrder></VRTRasterBand>'
        Path(name+'.vrt').write_text(text+'</VRTDataset>')
    def tearDown(self):os.chdir(self.old);self.temp.cleanup()
    def test_outputs_preserve_zero_phase_mask_components_and_crs(self):
        before=Path('merged/filt_topophase.unw.geo').read_bytes();worker.results(self.state,self.meta)
        report=json.loads((self.meta/'tops-results.json').read_text());self.assertEqual(report['valid_pixels'],108)
        ds=gdal.Open(str(self.meta/'tops-exports/unwrapped-phase.tif'));a=ds.ReadAsArray();self.assertEqual(a[0,0],0);self.assertTrue(np.isnan(a[-1]).all());self.assertEqual(ds.GetRasterBand(1).GetUnitType(),'rad');self.assertIn('4326',ds.GetProjection());ds=None
        self.assertEqual(Path('merged/filt_topophase.unw.geo').read_bytes(),before)
        layer=report['layers'][0];self.assertAlmostEqual(layer['bounds'][0][1],121,places=4)
        with Image.open(self.meta/'tops-exports'/layer['file']) as im:self.assertLessEqual(max(im.size),850);self.assertTrue((np.asarray(im)[:,:,3]==0).any())
    def test_truncated_or_misaligned_results_rejected(self):
        p=Path('merged/phsig.cor.geo');p.write_bytes(p.read_bytes()[:-1])
        with self.assertRaises(ValueError):worker.results(self.state,self.meta)
    def test_no_common_bursts_cannot_continue(self):
        from types import SimpleNamespace
        obj=SimpleNamespace(_insar=SimpleNamespace(numberOfCommonBursts=[0,0,0],commonBurstStartReferenceIndex=[-1]*3,commonBurstStartSecondaryIndex=[-1]*3),loadPickleObj=lambda _:None)
        with patch.object(worker,'app',return_value=obj),self.assertRaisesRegex(ValueError,'共同 burst'):worker.geometry({'config':{'swaths':[1]}},self.meta)

if __name__=='__main__':unittest.main()
