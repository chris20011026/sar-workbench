"""Terrain retrieval guards and independent numeric conversion checks."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from alosstack_helper.terrain import plan_terrain, tile_urls
from alosstack_helper.terrain_worker import convert, download, vertical_transform
from alosstack_helper.onboarding import inspect_terrain

try:
    import numpy as np
    from osgeo import gdal, osr
    import isce
    import pyproj
except ImportError:
    np = None


class TerrainPlanning(unittest.TestCase):
    def test_tiles_include_edges_in_both_hemispheres(self):
        p = plan_terrain([-1.2, .2, -1.2, .2])
        self.assertEqual(len(p['tiles']), 9)
        self.assertIn((-2, -2), p['tiles'])
        self.assertIn('S02_00_W002_00', tile_urls(-2, -2)[0])
        self.assertIn('/AUXFILES/', tile_urls(24, 121)[1])

    def test_bounds_limits_reject_unbounded_or_ambiguous_requests(self):
        for bounds in ([0, 60, 0, 1], [0, 1, 170, -170], [25, 25, 121, 122],
                       [0, float('nan'), 0, 1], [-10, 10, -10, 10], [0, 1e-8, 0, 1e-8]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                plan_terrain(bounds)

    def test_missing_public_tile_is_not_assumed_ocean(self):
        with tempfile.TemporaryDirectory() as d, patch('urllib.request.urlopen', side_effect=urllib.error.HTTPError('https://example.org/tile.tif', 404, 'missing', {}, None)):
            with self.assertRaisesRegex(RuntimeError, '缺圖區'):
                download('https://example.org/tile.tif', Path(d))
            self.assertFalse((Path(d) / 'tile.tif').exists())

    def test_checksum_failure_never_publishes_cache(self):
        import io
        class Response(io.BytesIO):
            headers = {'Content-Length': '3'}
        with tempfile.TemporaryDirectory() as d, patch('urllib.request.urlopen', return_value=Response(b'bad')):
            with self.assertRaisesRegex(RuntimeError, 'SHA256'):
                download('https://example.org/grid.tif', Path(d), expected='0' * 64)
            self.assertFalse((Path(d) / 'grid.tif.json').exists())


@unittest.skipIf(np is None, 'requires the scientific runtime: GDAL, ISCE2, numpy and pyproj')
class TerrainScience(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        gdal.UseExceptions()
        sr = osr.SpatialReference(); sr.ImportFromEPSG(4326); self.wkt = sr.ExportToWkt()
        self.gt = [121, 1/3600, 0, 25, 0, -1/3600]
        self.dem = self.raster('dem.tif', np.ones((4,4), dtype='float32')*100)
        self.mask = self.raster('water.tif', np.tile(np.arange(4, dtype='uint8'), (4,1)))
        self.grid = self.raster('constant-geoid.tif', np.ones((10,10), dtype='float32')*20,
                                [120, .2, 0, 26, 0, -.2])
        self.bounds = [25-4/3600, 25, 121, 121+4/3600]
        self.out = self.root/'out'; self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def raster(self, name, data, gt=None):
        p = self.root/name
        dtype = gdal.GDT_Byte if data.dtype == np.uint8 else gdal.GDT_Float32
        ds = gdal.GetDriverByName('GTiff').Create(str(p), data.shape[1], data.shape[0], 1, dtype)
        ds.SetGeoTransform(gt or self.gt); ds.SetProjection(self.wkt)
        ds.GetRasterBand(1).WriteArray(data); ds = None
        return p

    def test_geoid_is_added_and_water_classes_keep_isce_convention(self):
        # Independent expected answer: 100 m orthometric + 20 m geoid = 120 m.
        result = convert([self.dem], [self.mask], self.grid, self.bounds, self.out)
        np.testing.assert_allclose(np.fromfile(result['dem'], '<f4'), 120, atol=1e-5)
        np.testing.assert_array_equal(np.fromfile(result['wbd'], 'i1').reshape(4,4)[0], [0,-1,-1,-1])
        dem = inspect_terrain(result['dem'], 'dem'); water = inspect_terrain(result['wbd'], 'wbd')
        self.assertEqual(dem['bounds'], water['bounds'])
        self.assertAlmostEqual(dem['bounds'][2], 121+.5/3600)
        self.assertEqual(dem['reference'], 'WGS84')
        self.assertTrue(Path(result['dem']+'.vrt').is_file())

    def test_invalid_water_pixels_are_not_silently_land(self):
        invalid = self.raster('invalid.tif', np.full((4,4), 255, dtype='uint8'))
        with self.assertRaisesRegex(RuntimeError, '缺值'):
            convert([self.dem], [invalid], self.grid, self.bounds, self.out)
        self.assertFalse((self.out/'result.json').exists())

    def test_vertical_operation_fails_if_grid_is_missing(self):
        with self.assertRaises(Exception):
            vertical_transform(self.root/'missing-grid.tif')


if __name__ == '__main__': unittest.main()
