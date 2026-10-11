"""Plan public Copernicus GLO-30 DSM / water mask retrieval."""
import math


def plan_terrain(bounds):
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        raise ValueError('請完整輸入南、北、西、東四個經緯度。')
    s, n, w, e = map(float, bounds)
    if not all(math.isfinite(x) for x in (s, n, w, e)) or not (-49 <= s < n <= 49 and -180 <= w < e <= 180):
        raise ValueError('此版自動取得支援南北緯 49° 內、未跨換日線的矩形範圍。')
    tiles = [(lat, lon) for lat in range(math.floor(s), math.ceil(n)) for lon in range(math.floor(w), math.ceil(e))]
    pixels = math.ceil((n-s)*3600) * math.ceil((e-w)*3600)
    if len(tiles) > 36 or pixels > 400_000_000:
        raise ValueError('這個範圍過大，請縮小至本次影像的完整涵蓋範圍（最多 36 個一度圖幅）。')
    if pixels < 4:
        raise ValueError('範圍太小，請選擇包含整幅 SAR 影像的地形範圍。')
    return dict(bounds=[s,n,w,e], tiles=tiles, pixels=pixels,
                estimated_disk_gb=round((pixels*14 + len(tiles)*80_000_000 + 160_000_000)/1024**3, 2),
                provider='Copernicus GLO-30 Public (2021 release) + WBM',
                note='地表模型包含建物與植被；原高程為 EGM2008，會使用 PROJ 正式格網轉成 WGS84 橢球高。水體遮罩來自同一產品的 WBM，不等同 SRTM SWBD。若圖幅缺少或有無效像素，會停止，不把缺檔當成海洋或陸地。')


def tile_urls(lat, lon):
    name = f'Copernicus_DSM_COG_10_{"N" if lat >= 0 else "S"}{abs(lat):02d}_00_{"E" if lon >= 0 else "W"}{abs(lon):03d}_00'
    base = 'https://copernicus-dem-30m.s3.amazonaws.com/' + name + '_DEM/'
    return base + name + '_DEM.tif', base + 'AUXFILES/' + name + '_WBM.tif'
