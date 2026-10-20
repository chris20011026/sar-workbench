"""Read-only ALOS-2 CEOS inspection in the selected ISCE2 environment.

Uses upstream CEOS XML schemas. No SLC extraction, resampling or source writes.
Image record coordinates: JAXA CEOS Rev G Table 3.3-14, microdegrees.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import sys
import zipfile


def record(fp, schema, prefix_only=False):
    from isceobj.Sensor import CEOS, xmlPrefix
    start = fp.tell()
    header = fp.read(12)
    if len(header) != 12:
        raise ValueError('CEOS 記錄不完整')
    size = int.from_bytes(header[8:12], 'big')
    if size < 12 or size > 16 * 1024 * 1024:
        raise ValueError('CEOS 記錄長度異常')
    # The upstream decoder substitutes zero for invalid ASCII numbers. Validate
    # fields we rely on separately below; never treat scene-centre zero as a fix.
    need = 544 if prefix_only else size
    data = header + fp.read(need - 12)
    if len(data) != need:
        raise ValueError('CEOS 描述或影像前綴不完整')
    parser = CEOS.CEOSDB(xml=str(Path(xmlPrefix)/'alos2_slc'/schema), dataFile=io.BytesIO(data))
    try:
        parser.parse()
        return parser.metadata, start + size
    finally:
        parser.finalizeParser()


@contextlib.contextmanager
def open_entry(entry):
    if entry.get('member'):
        with zipfile.ZipFile(entry['source']) as archive, archive.open(entry['member']) as stream:
            yield stream
    else:
        with open(entry['source'], 'rb') as stream:
            yield stream


def coordinate(value):
    value = int(value)
    return (value - 2**32 if value >= 2**31 else value) / 1e6


def line_date(meta):
    year, day = meta['Sensor acquisition year'], meta['Sensor acquisition day of year']
    if not 2000 <= year <= 2100 or not 1 <= day <= 366:
        raise ValueError('影像觀測日期不合法')
    date = dt.datetime(year, 1, 1) + dt.timedelta(days=day-1)
    if date.year != year:
        raise ValueError('影像年內日期不合法')
    return date.strftime('%y%m%d')


def image_metadata(entry):
    with open_entry(entry) as stream:
        header, offset = record(stream, 'image_file.xml')
        count, stride = header['Number of SAR DATA records'], header['SAR DATA record length']
        width = header['Number of pixels per line per SAR channel']
        if not 2 <= count <= 10000000 or width <= 0 or stride < 544:
            raise ValueError('影像尺寸或記錄長度不合法')
        if offset + count * stride != entry['size']:
            raise ValueError('影像大小與 CEOS 記錄不符，可能未下載完整')
        first, _ = record(stream, 'image_record.xml', True)
        if first['Record Length'] != stride:
            raise ValueError('影像記錄長度與檔頭不同')
        pol = {0:'H', 1:'V'}
        polarization = pol[first['Transmitted polarization']] + pol[first['Received polarization']]
        out = dict(polarization=polarization, date=line_date(first), width=width, lines=count, footprint=None)
        if entry.get('member'):
            out['warning'] = 'ZIP 尚未展開：已核對首筆記錄，覆蓋圖待整理後補讀。'
            return out
        # Sample edges, not raster pixels. This is a metadata outline, not a DEM
        # geolocation or a valid-data mask. Sparse reads avoid GB-sized I/O.
        left, right = [], []
        for index in sorted({round(i*(count-1)/8) for i in range(9)}):
            stream.seek(offset + index * stride)
            line, _ = record(stream, 'image_record.xml', True)
            if line['Record Length'] != stride:
                raise ValueError('影像邊界記錄長度異常')
            for side, pixel in ((left, 'first'), (right, 'last')):
                lat = coordinate(line['Latitude of '+pixel+' pixel'])
                lon = coordinate(line['Longitude of '+pixel+' pixel'])
                if not (-85 < lat < 85 and -180 <= lon <= 180):
                    out['warning'] = '影像座標超出可顯示範圍，未繪製覆蓋圖。'
                    return out
                side.append([lon, lat])
        points = left + right[::-1]
        if max(p[0] for p in points)-min(p[0] for p in points)>180:
            out['warning'] = '影像跨越換日線，這版不自動計算覆蓋範圍。'
            return out
        from shapely.geometry import Polygon, mapping
        polygon = Polygon(points)
        if not polygon.is_valid or polygon.area < 1e-8:
            out['warning'] = '影像邊界座標缺漏或無效，未用零值猜測地點。'
            return out
        out['footprint'] = mapping(polygon)
        return out


def converted_provenance(leader):
    if leader.get('member'):
        return None
    folder = Path(leader['source']).parent
    manifest = folder / 'conversion.json'
    if not manifest.exists():
        return None
    if manifest.is_symlink() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError('轉換來源紀錄不合法')
    before = manifest.stat()
    document = json.loads(manifest.read_text())
    if (document.get('source_sensor') != 'ALOS-4' or
            document.get('output_format') != 'PALSAR-2 CEOS compatibility'):
        raise ValueError('轉換來源紀錄不受支援')
    try:
        from .alos4_quality import verify_conversion_metadata
    except ImportError:
        from alos4_quality import verify_conversion_metadata
    result = verify_conversion_metadata(document['source'], folder)
    if result['leader'] != leader['name'] or result['date'] != leader['date']:
        raise ValueError('轉換來源日期或產品不一致')
    after = manifest.stat()
    if (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
        raise ValueError('轉換紀錄在檢查期間改變')
    return result


def product_metadata(entries):
    leader = next((e for e in entries if e['name'].startswith('LED-')), entries[0])
    out = dict(key='|'.join([leader['identity'],leader['date'],leader['mode']]),
               date=leader['date'], frame=leader['identity'][-4:], identity=leader['identity'],
               product_mode=leader['mode'], direction=None, look=None, orbit=None, sensor_mode=None,
               acquisition=None, sensor_id=None, polarizations=[], footprint=None, errors=[], warnings=[])
    try:
        if not leader['name'].startswith('LED-'):
            raise ValueError('缺少 LED 描述檔')
        with open_entry(leader) as stream:
            _, end = record(stream, 'leader_file.xml')
            scene, _ = record(stream, 'scene_record.xml')
        out['direction'] = {'ASCEND':'ascending','DESCEND':'descending'}.get(scene['Time direction indicator along line direction'])
        out['look'] = {90.0:'right',-90.0:'left'}.get(scene['Sensor clock angle'])
        out['orbit'] = str(scene['Orbit number']).strip() or None
        sensor = str(scene['Sensor ID and mode of operation for this channel'])
        out['sensor_id'] = sensor
        out['sensor_mode'] = sensor[10:12] if len(sensor)>=12 else None
        provenance = converted_provenance(leader)
        out['source_sensor'] = 'ALOS-4' if provenance else 'ALOS-2'
        if provenance:
            out['conversion_metadata'] = provenance
            out['warnings'].append('已核對 ALOS-4 原始軌道與轉換描述；檔名為合成相容標籤，仍須人工確認影格對應。')
        if out['orbit'] and out['orbit'].lstrip('0') != leader['identity'][5:-4].lstrip('0') and not provenance:
            out['errors'].append('LED 軌道圈次與檔名不同')
        timestamp = str(scene['Scene centre time']).strip()
        center = dt.datetime.strptime(timestamp[:14], '%Y%m%d%H%M%S')
        out['acquisition'] = center.isoformat()+'Z'
        if center.strftime('%y%m%d') != leader['date']:
            out['errors'].append('LED 觀測日期與檔名不同')
        # Scene identifier spans two fields in the existing upstream schema.
        scene_id = (str(scene['Scene id']) + str(scene['Scene reference number'])).replace(' ', '')
        if not scene_id.startswith(leader['identity']+'-'+leader['date']):
            out['errors'].append('LED 場景編號與檔名不同')
        if str(scene['Product level code']).strip() != '1.1':
            out['errors'].append('LED 不是 Level 1.1')
        if not out['direction'] or not out['look'] or not out['sensor_mode']:
            out['warnings'].append('LED 觀測方向或成像模式未確認')
    except Exception as exc:
        out['errors'].append('無法核對 LED：'+str(exc))
    footprints = []
    for entry in entries:
        if not entry['name'].startswith('IMG-'): continue
        try:
            meta = image_metadata(entry)
            pol = entry['name'].split('-')[1]
            if meta['polarization'] != pol:
                out['errors'].append('IMG 極化與檔名不同：'+entry['name'])
            if meta['date'] != entry['date']:
                out['errors'].append('IMG 觀測日期與檔名不同：'+entry['name'])
            out['polarizations'].append(meta['polarization'])
            if meta['footprint']: footprints.append(meta['footprint'])
            if meta.get('warning'): out['warnings'].append(meta['warning'])
        except Exception as exc:
            out['errors'].append('無法核對 IMG：'+entry['name']+'：'+str(exc))
    if footprints and len(footprints) == sum(e['name'].startswith('IMG-') for e in entries):
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
        out['footprint'] = mapping(unary_union([shape(p) for p in footprints]))
    out['polarizations'] = sorted(set(out['polarizations']))
    out['warnings'] = sorted(set(out['warnings']))
    return out


def summarize(products, allow_cross_sensor=False):
    errors, warnings = [], []
    groups = {}
    for product in products:
        sensor = (product.get('sensor_id') or '').rstrip('- ')
        mission = product.get('source_sensor', 'ALOS-2')
        # Only the explicitly gated sampling workflow can combine validated
        # ALOS-4 compatibility products with native ALOS-2 observations.
        if allow_cross_sensor and mission in ('ALOS-2', 'ALOS-4'):
            mission = 'ALOS-2/4 manual review'
        key = (product['direction'], product['look'], product['sensor_mode'], product['product_mode'], sensor, mission)
        warnings += product.get('warnings', [])
        groups.setdefault(key, []).append(product['key'])
        errors += [product['date']+' / '+product['frame']+'：'+e for e in product['errors']]
    if len(groups)>1:
        errors.append('不同升降軌、觀測方向或模式不可直接合成同一組；請選擇下方一個候選組。')
    days = {}
    for p in products: days.setdefault(p['date'], []).append(p)
    if days:
        pols = [set(p['polarizations']) for p in products]
        if pols and not set.intersection(*pols): errors.append('各影像沒有共同極化，不能使用同一極化處理。')
        for date, rows in days.items():
            orbits = {p['orbit'] for p in rows if p['orbit']}
            if len(orbits)>1: errors.append(date+' 的 frame 來自不同軌道圈次，不能只依日期合併。')
    complete = bool(products) and all(p['footprint'] and not p['errors'] for p in products)
    union, common, bounds = None, None, None
    if complete:
        from shapely.geometry import shape, mapping
        from shapely.ops import unary_union
        per_day = [unary_union([shape(p['footprint']) for p in rows]) for rows in days.values()]
        all_area = unary_union(per_day)
        union = mapping(all_area)
        west,south,east,north = all_area.bounds
        bounds = [south,north,west,east]
        overlap = per_day[0]
        for area in per_day[1:]: overlap = overlap.intersection(area)
        if overlap.is_empty or overlap.area < 1e-8:
            errors.append('日期之間沒有共同的概略覆蓋區，請重新分組。')
        else: common = mapping(overlap)
    else:
        warnings.append('部分影像尚無可信邊界：不自動帶入 DEM 範圍，也不宣稱已核對共同覆蓋。')
    return dict(products=products, groups=[dict(direction=k[0],look=k[1],sensor_mode=k[2],mode=k[3],sensor_id=k[4],source_sensor=k[5],keys=v) for k,v in groups.items()],
                errors=errors, warnings=warnings, bounds=bounds, coverage=union, common=common,
                complete=complete, note='影像記錄座標的概略邊界；共同覆蓋不代表相干性或有效形變成果。相同升降軌不等於相同相對軌道。')


def inspect(entries, allow_cross_sensor=False):
    import isce  # Configure upstream namespace before CEOS.
    groups = {}
    for entry in entries:
        source = Path(entry['source'])
        if [source.stat().st_size,source.stat().st_mtime_ns] != entry['stamp']:
            raise ValueError('來源在掃描後改變，請重新掃描。')
        groups.setdefault((entry['identity'],entry['date'],entry['mode']), []).append(entry)
    result = summarize([product_metadata(rows) for rows in groups.values()], allow_cross_sensor)
    for entry in entries:
        source = Path(entry['source'])
        if [source.stat().st_size,source.stat().st_mtime_ns] != entry['stamp']:
            raise ValueError('來源在辨識過程中改變，請重新掃描。')
    return result


if __name__ == '__main__':
    # ISCE diagnostics go to worker log; structured output uses a separate file.
    request = json.loads(Path(sys.argv[1]).read_text())
    result = inspect(request['entries'], request.get('allow_cross_sensor') is True) if isinstance(request, dict) else inspect(request)
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
