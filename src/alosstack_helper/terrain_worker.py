"""Download public terrain, convert its vertical datum, and write ISCE rasters.

Copernicus DSM is NOT bare-earth terrain and its WBM is NOT SRTM SWBD.
We retain source URLs, hashes, the vertical operation, and the requested bounds.
Missing public tiles are refused rather than guessed to be ocean.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.error
import urllib.request

try:
    from .terrain import plan_terrain, tile_urls
except ImportError:  # Invoked by file path inside the selected science environment.
    from terrain import plan_terrain, tile_urls

GEOID_URL = 'https://cdn.proj.org/us_nga_egm08_25.tif'
GEOID_SHA256 = '4191d471eefebf24091b56dbc604353cb3b8cf8cc70e448bb9ae56a272bef17a'
ATTRIBUTION = ('produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and '
               '© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS '
               'by the European Union and ESA; all rights reserved')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def download(url, cache, expected=None):
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / url.rsplit('/', 1)[-1]
    marker = Path(str(path)+'.json')
    if path.is_file() and marker.is_file():
        try:
            old = json.loads(marker.read_text())
        except (OSError, ValueError):
            old = {}
        checksum = digest(path)
        if old.get('url') == url and old.get('sha256') == checksum and (expected is None or checksum == expected):
            return path, old
    print('Downloading ' + path.name, flush=True)
    partial = Path(str(path)+'.part')
    try:
        with urllib.request.urlopen(url, timeout=45) as response, partial.open('wb') as out:
            size = 0; sha = hashlib.sha256()
            for chunk in iter(lambda: response.read(1024*1024), b''):
                size += len(chunk)
                if size > 300*1024**2: raise RuntimeError('下載檔案大小異常，已停止。')
                sha.update(chunk); out.write(chunk)
            declared = response.headers.get('Content-Length')
            if declared and int(declared) != size: raise RuntimeError('下載未完成，請重試。')
            checksum = sha.hexdigest()
            if expected and checksum != expected: raise RuntimeError('高程基準格網的 SHA256 校驗未通過。')
            info = dict(url=url, sha256=checksum, bytes=size, etag=response.headers.get('ETag'))
        partial.replace(path); marker.write_text(json.dumps(info, indent=2))
        return path, info
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError('此範圍有尚未提供的公開地形／水體圖幅。請改用既有 ISCE 地形檔；工作台不會自行把缺圖區填成海洋。') from None
        raise RuntimeError(f'地形資料服務回應 {exc.code}，稍後可重試。') from None


def vertical_transform(grid):
    from pyproj import Transformer, network
    network.set_network_enabled(False)
    # PROJ documents multiplier=+1 as z_target = z_source + gridvalue.
    # The NGA grid stores geoid undulation N: ellipsoid h = EGM2008 H + N.
    return Transformer.from_pipeline(
        '+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad '
        f'+step +proj=vgridshift +grids={grid} +multiplier=1 '
        '+step +proj=unitconvert +xy_in=rad +xy_out=deg')


def header(path, width, height, transform, dtype):
    import isce
    import isceobj
    image = isceobj.createDemImage()
    image.initImage(str(path), 'read', width, dtype)
    image.length = height
    image.firstLongitude = transform[0] + transform[1] / 2
    image.firstLatitude = transform[3] + transform[5] / 2
    image.deltaLongitude = transform[1]; image.deltaLatitude = transform[5]
    image.reference = 'WGS84'
    image.renderHdr(); image.renderVRT()


def convert(dems, masks, grid, bounds, output):
    import numpy as np
    from osgeo import gdal
    gdal.UseExceptions()
    s,n,w,e = bounds
    options = dict(format='GTiff', outputBounds=[w,s,e,n], dstSRS='EPSG:4326',
                   xRes=1/3600, yRes=1/3600, multithread=False,
                   creationOptions=['TILED=YES', 'COMPRESS=DEFLATE'])
    dem = gdal.Warp(str(output/'surface-egm2008.tif'), list(map(str,dems)),
                    resampleAlg='bilinear', outputType=gdal.GDT_Float32, dstNodata=-9999, **options)
    water = gdal.Warp(str(output/'water-classes.tif'), list(map(str,masks)),
                     resampleAlg='near', outputType=gdal.GDT_Byte, dstNodata=255, **options)
    width, height = dem.RasterXSize, dem.RasterYSize
    gt = dem.GetGeoTransform()
    if (water.RasterXSize,water.RasterYSize) != (width,height) or water.GetGeoTransform() != gt:
        raise RuntimeError('地形與水體網格未對齊，已停止。')
    transformer = vertical_transform(grid)
    dem_file, wbd_file = output/'copernicus.dem.wgs84', output/'copernicus.wbd'
    cols = np.arange(width)
    print('Converting EGM2008 heights to WGS84 ellipsoid and WBM classes to ISCE water mask.', flush=True)
    with dem_file.open('wb') as fd, wbd_file.open('wb') as fw:
        for y in range(0, height, 128):
            count = min(128, height-y)
            heights = dem.ReadAsArray(0,y,width,count)
            classes = water.ReadAsArray(0,y,width,count)
            if not np.all(np.isfinite(heights)) or np.any(heights == -9999) or not np.all(np.isin(classes,[0,1,2,3])):
                raise RuntimeError('地形／水體包含缺值或未支援的類別，未啟用此地形。請縮小到有完整圖幅的涵蓋範圍或使用既有地形檔。')
            lon,lat = np.meshgrid(gt[0]+(cols+.5)*gt[1], gt[3]+(np.arange(y,y+count)+.5)*gt[5])
            _,_,ellipsoid = transformer.transform(lon,lat,heights,errcheck=True)
            if not np.all(np.isfinite(ellipsoid)): raise RuntimeError('高程基準轉換產生無效數值。')
            np.asarray(ellipsoid,dtype='<f4').tofile(fd)
            np.where(classes == 0, 0, -1).astype(np.int8).tofile(fw)
    dem = water = None
    header(dem_file,width,height,gt,'FLOAT'); header(wbd_file,width,height,gt,'BYTE')
    return dict(dem=str(dem_file),wbd=str(wbd_file),width=width,height=height,
                bounds=bounds,vertical_operation=transformer.definition,
                horizontal_reference='WGS84',vertical_reference='WGS84 ellipsoid',
                water_classes='Copernicus WBM 0 -> land 0; 1/2/3 -> water -1; missing classes rejected')


def run(output, cache, bounds):
    plan = plan_terrain(bounds)
    output.mkdir(parents=True, exist_ok=False)
    if shutil.disk_usage(output).free < plan['estimated_disk_gb']*1024**3:
        raise RuntimeError('剩餘磁碟空間不足。')
    dems,masks,sources = [],[],[]
    for lat,lon in plan['tiles']:
        d,w = tile_urls(lat,lon)
        for url,files in [(d,dems),(w,masks)]:
            p,info = download(url,cache); files.append(p);sources.append(info)
    grid,info = download(GEOID_URL,cache,expected=GEOID_SHA256); sources.append(info)
    result = convert(dems,masks,grid,plan['bounds'],output)
    result.update(provider=plan['provider'],meaning=plan['note'],sources=sources,
                  attribution=ATTRIBUTION,
                  geoid_source='NGA EGM2008 grid distributed by PROJ-data',
                  license_url='https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM')
    (output/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    (output/'SOURCE.txt').write_text(ATTRIBUTION+'\n\n'+plan['note']+'\n'+result['license_url']+'\n')
    print('Terrain preparation complete. Sources and transformations saved.',flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--bounds',type=float,nargs=4,required=True)
    a = p.parse_args();run(a.output.resolve(),a.cache.resolve(),a.bounds)
