"""Bounded metadata catalogue for the sampling review, in the science env.

No ingestion, resampling, matching, source writes, or approval decisions.
"""
import hashlib
import json
from pathlib import Path
import sys

try:
    from .alos_coverage import read_product, _known_geometry, _decode, _polygons
    from .sampling_worker import image_info
    from .compatibility import compatibility_report
except ImportError:
    from alos_coverage import read_product, _known_geometry, _decode, _polygons
    from sampling_worker import image_info
    from compatibility import compatibility_report


def stamp(path):
    p = Path(path)
    s = p.stat()
    return dict(path=str(p), size=s.st_size, mtime_ns=s.st_mtime_ns,
                ctime_ns=s.st_ctime_ns, inode=s.st_ino, device=s.st_dev)


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def leaders(source):
    """Only the chosen folder and its immediate date/product children."""
    root = Path(source).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('候選影格來源必須是資料夾。')
    folders = [root]
    children = sorted(root.iterdir())
    if len(children) > 2000:
        raise ValueError('資料夾項目過多，請選更小的資料目錄。')
    folders += [p for p in children if p.is_dir() and not p.is_symlink()]
    result = []
    for folder in folders:
        files = sorted(folder.iterdir())
        if len(files) > 2000:
            raise ValueError('單一產品資料夾項目過多。')
        result += [p for p in files if p.name.startswith(('LED-ALOS2', 'LED-ALOS4'))
                   and p.is_file() and not p.is_symlink()]
    if len(result) > 200:
        raise ValueError('一次最多比較 200 個影格，請分批選擇。')
    return result


def ceos_layout(path, layout, image):
    """Require the known Level 1.1 complex-float record layout."""
    prefix = {'PALSAR-2': 544, 'PALSAR-3': 800}[layout]
    with path.open('rb') as f:
        raw = f.read(720)
    if len(raw) != 720 or int.from_bytes(raw[8:12], 'big') != 720:
        raise ValueError('IMG descriptor 格式不支援振幅預覽。')
    fields = _decode(raw, 'image_file.xml', [
        'Number of bits per sample', 'Number of samples per data group',
        'Number of bytes per data group', 'Number of SAR channels in this file',
        'Number of bytes of prefix data per record', 'SAR DATA record length',
        'SAR Data format type identifier', 'SAR Data format type code',
        'Interleaving indicator', 'Number of physical records per line',
        'Number of bytes of suffix data per record',
        'Number of left border pixels per line', 'Number of right border pixels per line',
    ])
    expected = {'Number of bits per sample': 32, 'Number of samples per data group': 2,
                'Number of bytes per data group': 8, 'Number of SAR channels in this file': 1,
                'Number of bytes of prefix data per record': prefix,
                'SAR Data format type identifier': 'COMPLEX*8', 'SAR Data format type code': 'C*8',
                'Interleaving indicator': 'BSQ', 'Number of physical records per line': 1,
                'Number of bytes of suffix data per record': 0,
                'Number of left border pixels per line': 0, 'Number of right border pixels per line': 0}
    if any(fields[k] != v for k, v in expected.items()):
        raise ValueError('只預覽已核對的 complex-float32 Level 1.1 IMG。')
    stride = fields['SAR DATA record length']
    if stride != prefix + image['width'] * 8 or path.stat().st_size != 720 + stride * image['lines']:
        raise ValueError('IMG 尺寸或列配置不符。')
    return dict(path=str(path), width=image['width'], length=image['lines'],
                image_offset=720+prefix, line_offset=stride, byte_order='MSB')


def catalogue(request):
    config, plan = request['config'], request['plan']
    primary = leaders(config['data'])
    extra = leaders(request['extra_source']) if request.get('extra_source') else []
    rows = {(r['date'], r['frame']) for r in plan['rows']}
    selected_names = {Path(r['raw']).name for r in plan['rows']}
    products, specs, files, warnings, radar_products = [], {}, {}, [], []
    def remember(path, evidence=None):
        current = stamp(path)
        if evidence is not None and any(current[k] != evidence.get(k) for k in (
                'size', 'mtime_ns', 'ctime_ns', 'inode', 'device')):
            raise ValueError('來源在中繼資料檢查後改變，請重新掃描。')
        previous = files.get(str(path))
        if previous is not None and previous != current:
            raise ValueError('來源在影格比較期間改變，請重新掃描。')
        files[str(path)] = current
        return current
    seen_names = set()
    # Original ALOS-4 folders already represented by a converted product should
    # not appear a second time as though they were independent acquisitions.
    represented = set()
    for p in primary:
        manifest = p.parent/'conversion.json'
        if manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size < 1024**2:
            try:
                document = json.loads(manifest.read_text())
                represented.add(str(Path(document['source']).resolve()))
            except (ValueError, TypeError, KeyError):
                pass
    for leader in primary + extra:
        if leader.name in seen_names:
            continue
        if leader in extra and leader.name.startswith('LED-ALOS4') and str(leader.parent) in represented:
            continue
        seen_names.add(leader.name)
        dossier = read_product(leader)
        metadata = dossier['metadata']
        date = (dossier.get('date') or '')[-6:]
        frame = metadata.get('frame')
        selected = leader in primary and (date, frame) in rows
        radar_products.append({**dossier, 'selected': selected})
        geometry = _known_geometry(dossier)
        product = dict(id=dossier['id'], date=date, frame=frame, sensor=dossier['sensor'],
                       selected=selected, footprint=dossier.get('footprint') if geometry is not None else None, images=[],
                       direction=metadata.get('orbit_direction'), look=metadata.get('look_direction'),
                       frame_provenance=metadata.get('frame_provenance'), path=str(leader),
                       warnings=dossier['warnings']+dossier['errors'])
        if dossier.get('footprint') and geometry is None:
            product['warnings'].append('概略外框未通過完整地理檢查，未用於共同覆蓋計算。')
        for evidence in dossier['source_files']:
            path = Path(evidence['path'])
            remember(path, evidence)
        for im in dossier['images']:
            if im.get('polarization') != config.get('properties', {}).get('polarization', 'HH'):
                continue
            try:
                path = leader.parent/im['name']
                layout = ceos_layout(path, metadata['layout'], im)
                image_id = signature(dict(layout=layout, stamp=remember(path)))[:32]
                specs[image_id] = layout
                product['images'].append(dict(id=image_id, polarization=im['polarization'],
                    width=im['width'], length=im['lines'], kind='source', label='原始振幅（雷達座標）'))
                if selected and path.name not in selected_names:
                    product['warnings'].append('此 IMG 未包含在本次讀取計畫中，僅供比較。')
            except (ValueError, KeyError, OSError) as exc:
                product['warnings'].append('振幅預覽不可用：'+str(exc))
        products.append(product)
    # Show actual post-equalization rasters separately, never label a raw
    # thumbnail as a resampling result.
    post = request.get('post')
    if post:
        import isce  # noqa: F401
        dates = Path(config['root'])/'proc/dates'
        for row in post['rows']:
            product = next((p for p in products if p['selected'] and p['date'] == row['date']
                            and p['frame'] == row['frame']), None)
            if not product:
                continue
            relative = Path(row['relative'])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('SLC 相對路徑無效。')
            slc = dates/relative
            info = image_info(slc, row['width'], row['length'], Path(config['data']), dates)
            layout = dict(path=info['raw'], width=row['width'], length=row['length'],
                          image_offset=info['image_offset'], line_offset=info['line_offset'],
                          byte_order=info['byte_order'])
            for path in (Path(info['raw']), Path(str(slc)+'.xml'), Path(str(slc)+'.vrt')):
                remember(path)
            image_id = signature(dict(layout=layout, stamp=stamp(info['raw']), kind='equalized'))[:32]
            specs[image_id] = layout
            product['images'].append(dict(id=image_id, polarization=config['properties'].get('polarization', 'HH'),
                width=row['width'], length=row['length'], kind='equalized', label='統一取樣後／保留原樣'))
    # Intersection is across dates, after unioning selected frames per date.
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union
    selected = [p for p in products if p['selected']]
    complete = bool(selected) and all(p['footprint'] for p in selected)
    complete = complete and rows == {(p['date'], p['frame']) for p in selected}
    common = None
    if complete:
        groups = [unary_union([shape(p['footprint']) for p in selected if p['date'] == d])
                  for d in plan['dates']]
        intersection = groups[0]
        for group in groups[1:]:
            intersection = intersection.intersection(group)
        # A disjoint selected frame can merely touch another date's edge while
        # another frame overlaps in area. Shapely then returns a collection of
        # polygons and lines; retain its polygonal coverage, never drop it.
        parts = [p for p in _polygons(intersection) if p.area > 1e-12]
        if parts:
            common = mapping(unary_union(parts))
        else:
            warnings.append('選定日期沒有共同的概略影像外框，請重新核對。')
    else:
        warnings.append('部分選定影格缺少可靠外框，無法完整計算共同覆蓋。')
    fingerprints = sorted(files.values(), key=lambda f: f['path'])
    if any(stamp(item['path']) != item for item in fingerprints):
        raise ValueError('來源在影格比較期間改變，請重新掃描。')
    if primary != leaders(config['data']) or (request.get('extra_source') and extra != leaders(request['extra_source'])):
        raise ValueError('來源影格清單在檢查期間改變，請重新掃描。')
    return dict(plan_id=plan['plan_id'], reference=plan['reference'], products=products,
                compatibility=compatibility_report(radar_products, expected=rows),
                coverage=dict(common=common, complete=complete), warnings=warnings,
                source_fingerprint=signature(fingerprints), _files=fingerprints, _image_specs=specs,
                note='外框來自 CEOS 中繼資料，不是精密配準或有效像素範圍；候選影格不更改處理設定。')


if __name__ == '__main__':
    result = catalogue(json.loads(Path(sys.argv[1]).read_text()))
    Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
