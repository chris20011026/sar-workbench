"""Run with the scientific environment to verify geolocation, not just rendering."""
import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/alosstack_helper'))
try:
    import geo_worker as geo
    import numpy as np
    import h5py
except ImportError:
    geo=None

@unittest.skipIf(geo is None,'requires scientific environment: numpy, pyresample, pyproj, h5py')
class GeographyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name);(self.p/'inputs').mkdir();(self.p/'exports').mkdir()
        y,x=np.mgrid[:8,:9];self.lat=25-y*.001+x*.00013;self.lon=121+x*.001+y*.00011
        self.land=np.ones(y.shape,dtype=bool);self.land[0,:]=False
        self.mask=self.land.copy();self.mask[1,:]=False
        self.vel=(y*9+x).astype('float32')/1000
        with h5py.File(self.p/'inputs/geometryRadar.h5','w') as f:
            for k,v in dict(latitude=self.lat,longitude=self.lon,waterMask=self.land,height=y*30).items():f[k]=v
        with h5py.File(self.p/'velocity.h5','w') as f:f['velocity']=self.vel;f['velocityStd']=np.ones(y.shape)*.001
        with h5py.File(self.p/'maskTempCoh.h5','w') as f:f['mask']=self.mask
        with h5py.File(self.p/'temporalCoherence.h5','w') as f:f['temporalCoherence']=np.where(self.mask,.9,.3)
        with h5py.File(self.p/'timeseries.h5','w') as f:f['timeseries']=np.stack([self.vel*0,self.vel,self.vel*2]);f['date']=np.array(['20180101','20190101','20200101'],dtype='S8')
        (self.p/'analysis.json').write_text(json.dumps(dict(id='test',options=dict(temporal_coherence=.7,reference_date='190101'),timeseries_file=str(self.p/'timeseries.h5'))))
        (self.p/'results.json').write_text(json.dumps(dict(reference_pixel=[3,3])))
        self.spec=dict(folder=str(self.p),out=str(self.p/'exports'),mode='result',prefix='map-v4-test')
    def tearDown(self):self.tmp.cleanup()
    def test_rotated_geometry_overlay_pick_and_native_values_agree(self):
        m=geo.map_view(self.spec)
        with np.load(self.p/'exports/map-v4-test-index.npz') as f:ids=f['index'];shape=f['shape'];tr=f['transform']
        target=np.argwhere((ids>=0)&self.mask.ravel()[np.maximum(ids,0)])[5];gy,gx=map(int,target)
        y,x=divmod(int(ids[gy,gx]),int(shape[1]));lo,la=geo.Transformer.from_crs(3857,4326,always_xy=True).transform(tr[0]+(gx+.5)*tr[1],tr[3]+(gy+.5)*tr[5])
        d=geo.pick({**self.spec,'coordinate':[la,lo],'threshold':.7})
        shown=np.fromfile(self.p/'exports'/m['layers']['velocity']['file'],dtype='<f4').reshape(m['height'],m['width'])[gy,gx]
        self.assertEqual([d['y'],d['x']],[y,x]);self.assertAlmostEqual(d['velocity'],shown,places=4)
        self.assertAlmostEqual(d['latitude'],self.lat[y,x]);self.assertEqual(len(d['dates']),3)
    def test_nodata_and_stricter_display_threshold_are_rejected(self):
        geo.map_view(self.spec)
        with self.assertRaisesRegex(ValueError,'範圍外'):geo.pick({**self.spec,'coordinate':[20,110]})
        from science_worker import point_data
        with self.assertRaisesRegex(ValueError,'品質遮罩'):point_data(self.p/'analysis.json',0,2)
        with self.assertRaisesRegex(ValueError,'顯示門檻'):point_data(self.p/'analysis.json',3,3,.95)
    def test_export_crs_units_nodata_and_nearest_values(self):
        from osgeo import gdal,osr
        before=(self.p/'velocity.h5').read_bytes()
        r=geo.export({**self.spec,'crs':'EPSG:4326','resolution':100,'threshold':.7})
        ds=gdal.Open(str(self.p/'exports'/r['file']));a=ds.ReadAsArray();band=ds.GetRasterBand(1)
        self.assertEqual(band.GetUnitType(),'mm/year');self.assertTrue(np.isnan(band.GetNoDataValue()));self.assertTrue(np.isnan(a).any())
        self.assertTrue(set(np.round(a[np.isfinite(a)],3))<=set(np.round(self.vel[self.mask]*1000,3)))
        self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None),'4326');self.assertEqual(before,(self.p/'velocity.h5').read_bytes())
    def test_export_refuses_unsupported_or_huge_grids(self):
        with self.assertRaises(ValueError):geo.export({**self.spec,'crs':'EPSG:3857','resolution':100})
        with self.assertRaises(ValueError):geo.export({**self.spec,'resolution':0})
        with self.assertRaises(ValueError):geo.export({**self.spec,'threshold':float('nan')})
    def test_publication_figure_and_projected_export(self):
        from PIL import Image
        from osgeo import gdal
        geo.map_view(self.spec);r=geo.figure({**self.spec,'layer':'velocity','threshold':.7})
        with Image.open(self.p/'exports'/r['file']) as im:self.assertGreater(im.width,1000)
        r=geo.export({**self.spec,'crs':'EPSG:3826','resolution':100,'threshold':.7})
        ds=gdal.Open(str(self.p/'exports'/r['file']));self.assertEqual(ds.GetSpatialRef().GetAuthorityCode(None),'3826');self.assertEqual(ds.GetGeoTransform()[1],100)
    def test_coordinate_input_uses_exact_native_reference_not_preview_cell(self):
        j=json.loads((self.p/'analysis.json').read_text());j['options']['reference_coherence']=.85;(self.p/'analysis.json').write_text(json.dumps(j))
        with h5py.File(self.p/'inputs/ifgramStack.h5','w') as f:
            f['dropIfgram']=np.ones(2,dtype=bool);f['coherence']=np.ones((2,8,9))*.9;f['unwrapPhase']=np.ones((2,8,9));f['connectComponent']=np.ones((2,8,9),dtype='uint8')
        (self.p/'loaded.json').write_text(json.dumps(dict(height=8,width=9,candidate=dict(y=3,x=3,coherence=.9))))
        spec={**self.spec,'mode':'reference'};geo.map_view(spec)
        d=geo.pick({**spec,'coordinate':[self.lat[3,3],self.lon[3,3]],'native':True})
        self.assertEqual([d['y'],d['x']],[3,3])

if __name__=='__main__':unittest.main()
