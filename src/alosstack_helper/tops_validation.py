"""Read-only TOPS evidence inventory and separately stored human review.

No pickle loading, scientific execution, SAFE decompression, or raster rendering.
The dossier is a review aid, never a release/scientific certification switch.
"""
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import xml.etree.ElementTree as ET

from .core import atomic_json
from .tops import STEPS

MAX_SMALL = 2 * 1024 * 1024
MAX_LOG = 1024 * 1024
STATUSES = ('pending', 'reviewed', 'pass', 'fail')
REVIEW_ITEMS = [
    dict(id='inputs', title='影像、軌道與設定', help='核對兩期真實 IW SLC 產品、共同極化／相對軌道、DEM 高程基準、精密軌道及實際解析參數。保存的來源名稱不等於重新驗證大型影像內容。'),
    dict(id='bursts', title='共同 burst 與覆蓋', help='檢查每個選定子波束的共同 burst、處理範圍與 DEM 覆蓋；annotation 外框僅供初選，需採用實際 computeBaselines 紀錄。'),
    dict(id='esd', title='ESD 方位向對位', help='檢查 ESD 有效樣本數、偏移分布與 burst 接縫。ESD 的 Median 是估計修正量，並非修正後殘差；不以單一數字判定精度。'),
    dict(id='coregistration', title='距離向與精對位', help='檢查 range offset 統計、精重採樣後重疊與接縫。Range 修正量不等於修正後精度，應配合圖像與獨立結果。'),
    dict(id='coherence', title='相干性與遮罩', help='查看高低相干性的位置、低相干區與水體、有效樣本覆蓋。全圖平均或有效像素數不能替代空間判讀，不設定通用合格門檻。'),
    dict(id='unwrapping', title='解纏與連通區', help='檢查連通區邊界、孤立區與可能的 2π 跳變；跨不同連通區的相位基準不一定一致。解纏完成不代表解纏正確。'),
    dict(id='geocoding', title='地理位置與相位意義', help='與獨立地理參考核對海岸、地物或 DEM；座標系／網格一致僅是格式條件。相位是 rad，不能直接稱為速度或垂直下陷。'),
    dict(id='independent', title='獨立原生命令列對照', help='另建資料夾，以相同來源、軌道、DEM、ISCE2 版本及設定獨立處理。記錄網格、相位參考、遮罩、共同 burst、對位與相位差異。單純複製工作台輸出不算獨立對照。'),
]
REFERENCES = [
    dict(title='ISCE2 TOPS steps', url='https://github.com/isce-framework/isce2/blob/main/applications/topsApp.py'),
    dict(title='ISCE2 common bursts', url='https://github.com/isce-framework/isce2/blob/main/components/isceobj/TopsProc/runComputeBaseline.py'),
    dict(title='ISCE2 ESD statistics', url='https://github.com/isce-framework/isce2/blob/main/components/isceobj/TopsProc/runESD.py'),
    dict(title='ISCE2 range offset statistics', url='https://github.com/isce-framework/isce2/blob/main/components/isceobj/TopsProc/runRangeCoreg.py'),
]
RAW_PRODUCTS = [
    ('unwrapped', '解纏相位', 'merged/filt_topophase.unw.geo'),
    ('coherence', '相干性', 'merged/phsig.cor.geo'),
    ('components', '解纏連通區', 'merged/filt_topophase.unw.conncomp.geo'),
    ('wrapped', '包裹相位', 'merged/filt_topophase.flat.geo'),
]


def _stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _regular(base, relative):
    """Confine reads to plain files; even intermediate symlinks are not followed."""
    base = Path(base)
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in ('.', '..') for p in parts):
        raise ValueError('無效的檢查檔案路徑。')
    current = base
    if current.is_symlink():
        raise ValueError('檢查根目錄不可是符號連結。')
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ValueError('檢查檔案不可透過符號連結讀取。')
    info = current.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('檢查對象不是一般檔案。')
    return current, info


def _read(base, relative, limit=MAX_SMALL, tail=False):
    path, info = _regular(base, relative)
    if info.st_size > limit and not tail:
        raise ValueError('描述檔超過檢查大小上限。')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as stream:
        current = os.fstat(stream.fileno())
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('檢查期間檔案已變更。')
        if tail:
            stream.seek(max(0, current.st_size - limit))
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('描述檔在讀取期間變大。')
    return value, info


def _json(base, relative):
    data, _ = _read(base, relative)
    def invalid(_value):
        raise ValueError('JSON 含非有限數值。')
    value = json.loads(data, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError('描述檔必須為 JSON 物件。')
    return value


def _file(base, relative, content=False):
    row = dict(file=relative, status='missing')
    try:
        path, info = _regular(base, relative)
        row.update(status='present' if info.st_size else 'empty', bytes=info.st_size,
                   mtime_ns=info.st_mtime_ns)
        if content:
            value, _ = _read(base, relative)
            row['sha256'] = hashlib.sha256(value).hexdigest()
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        row.update(status='unavailable', reason=str(error))
    return row


def _descriptor(base, name):
    """Read VRT metadata as text only. Never open a GDAL driver/source path."""
    row = _file(base, name)
    row['descriptor'] = _file(base, name + '.vrt', content=True)
    if row['descriptor']['status'] != 'present':
        return row
    try:
        data, _ = _read(base, name + '.vrt')
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise ValueError('不支援 XML DTD 或實體。')
        root = ET.fromstring(data)
        if root.tag != 'VRTDataset':
            raise ValueError('不是 VRTDataset 描述檔。')
        width, height = int(root.get('rasterXSize', '0')), int(root.get('rasterYSize', '0'))
        transform = [float(n) for n in (root.findtext('GeoTransform') or '').split(',')]
        srs = (root.findtext('SRS') or '').strip()
        bands = [dict(band=int(b.get('band', '0')), data_type=b.get('dataType', ''))
                 for b in root.findall('VRTRasterBand')]
        if min(width, height) <= 0 or not bands or len(transform) != 6 or not all(math.isfinite(n) for n in transform):
            raise ValueError('尺寸、波段或地理轉換不完整。')
        if not srs or transform[1] * transform[5] - transform[2] * transform[4] == 0:
            raise ValueError('缺少座標系或網格不可逆。')
        row['grid'] = dict(width=width, height=height, transform=transform, srs=srs, bands=bands)
    except (OSError, ValueError, ET.ParseError) as error:
        row['descriptor']['reason'] = str(error)
    return row


def _log_statistics(base):
    result = dict(source='.helper/run.log', limit_bytes=MAX_LOG, truncated=False,
                  esd=None, range_coregistration=None)
    try:
        data, info = _read(base, '.helper/run.log', MAX_LOG, tail=True)
        result.update(truncated=info.st_size > MAX_LOG, bytes=info.st_size,
                      tail_sha256=hashlib.sha256(data).hexdigest())
        text = data.decode('utf-8', errors='replace')
        # runESD and runRangeCoreg both use the node name "esd" upstream.
        # Only accept a complete explicitly titled catalogue block, never the
        # final merged catalogue where range values can overwrite ESD values.
        for title, key in [('runESD', 'esd'), ('runRangeCoreg', 'range_coregistration')]:
            blocks = re.findall(r'(?m)^\s*' + title + r'\s*\n-{20,}\s*\n(.*?)^#{20,}\s*$', text, re.S | re.M)
            if not blocks:
                continue
            values = {}
            names = {'Median': 'median_pixels', 'Mean': 'mean_pixels', 'Std': 'std_pixels',
                     'number of coherent points': 'sample_count', 'coherence threshold': 'coherence_threshold',
                     'snr threshold': 'snr_threshold'}
            for line in blocks[-1].splitlines():
                match = re.search(r'(?:^|\.)esd\.([^=]+)\s*=\s*(\S+)\s*$', line)
                if not match or match[1].strip() not in names:
                    continue
                try:
                    value = float(match[2])
                    if math.isfinite(value):
                        values[names[match[1].strip()]] = value
                except ValueError:
                    pass
            if values:
                result[key] = dict(values=values, title=title,
                                   meaning='估計的修正偏移與篩選樣本統計，並非修正後殘差或精度。')
    except (OSError, ValueError) as error:
        result['reason'] = str(error)
    return result


def _text(value, label, maximum=4000):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(label + '格式或長度不正確。')
    return value.strip()


def _statistics_present(record):
    values = (record or {}).get('values', {})
    return (all(key in values for key in ('median_pixels', 'mean_pixels', 'std_pixels', 'sample_count'))
            and values['sample_count'] > 0 and values['std_pixels'] >= 0)


def _write_json(path, value):
    if path.is_symlink() or path.with_suffix('.tmp').is_symlink():
        raise ValueError('評閱與暫存檔不可使用符號連結。')
    atomic_json(path, value)


class TopsValidation:
    def __init__(self, engine):
        self.e = engine
        self.base = Path(engine.home) / 'research' / 'tops-validation'
        self.lock = threading.RLock()

    def _directory(self, project_id, create=False):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', project_id):
            raise ValueError('無效的專案識別碼。')
        path = self.base / project_id
        for p in (Path(self.e.home), Path(self.e.home) / 'research', self.base, path):
            if p.is_symlink():
                raise ValueError('評閱儲存位置不可使用符號連結。')
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _review(self, project_id):
        path = self._directory(project_id)
        try:
            return _json(path, 'review.json')
        except FileNotFoundError:
            return dict(dataset_kind='unknown', reviewer='', dataset_note='', items={}, independent={})

    def inspect(self, project_id):
        s = self.e.get(project_id)
        if s['config'].get('workflow') != 'topsApp':
            raise ValueError('這個驗證表僅適用 Sentinel-1 TOPS 雙日期專案。')
        root, proc, meta = self.e.paths(s)
        if s['status'] == 'running' or self.e.active == project_id:
            raise ValueError('請等待目前處理完成或停止，再整理一致的驗證紀錄。')
        c = s['config']
        inventory = s.get('tops_inventory', {})
        sources = []
        for key in ('reference', 'secondary'):
            source = inventory.get(key, {})
            sources.append(dict(role=key, **{k: source.get(k) for k in
                ('name', 'path', 'satellite', 'date', 'start', 'stop', 'relative_orbit', 'direction', 'bytes')}))
        steps = []
        complete = s.get('completed_steps', [])
        expected = [key for key, _ in STEPS]
        for key, label in STEPS:
            steps.append(dict(id=key, label=label, recorded=key in complete,
                              files=[_file(proc, 'PICKLE/' + key + suffix) for suffix in ('', '.xml')]))
        files = {name: _file(root, name, content=True) for name in
                 ('.helper/tops-geometry.json', '.helper/tops-resolved.json', '.helper/tops-results.json',
                  'proc/topsApp.xml', '.helper/orbit-sources.json')}
        parsed = {}
        for key in ('geometry', 'resolved', 'results'):
            try:
                parsed[key] = _json(meta, 'tops-' + key + '.json')
            except (OSError, ValueError) as error:
                parsed[key] = {}
                files['.helper/tops-' + key + '.json']['reason'] = str(error)
        products = [dict(id=key, label=label, **_descriptor(proc, name)) for key, label, name in RAW_PRODUCTS]
        stats = _log_statistics(root)
        channels = parsed['geometry'].get('channels', [])
        geometry_ok = (isinstance(channels, list) and bool(channels)
                       and {x.get('swath') for x in channels if isinstance(x, dict)} == set(c.get('swaths', []))
                       and all(isinstance(x, dict) and type(x.get('count')) is int and x['count'] >= 2 for x in channels))
        steps_ok = complete == expected and all(f['status'] == 'present' for step in steps for f in step['files'])
        grids = [p.get('grid') for p in products]
        grids_ok = all(g is not None for g in grids) and all(
            {k: g[k] for k in ('width', 'height', 'transform', 'srs')} ==
            {k: grids[0][k] for k in ('width', 'height', 'transform', 'srs')} for g in grids)
        outputs_ok = all(p['status'] == 'present' for p in products) and grids_ok
        results = parsed['results']
        valid_pixels = results.get('valid_pixels')
        checks = [
            dict(id='inputs', available=all(x.get('name') for x in sources), title='保存的兩期影像識別', detail='建立專案時保存的 SAFE inventory；此處不重新讀取或認證原始影像。'),
            dict(id='steps', available=steps_ok, title='21 個原生階段紀錄', detail=f'{sum(x["recorded"] for x in steps)} / {len(steps)} 步已登記；檢查 checkpoint 檔案存在與非空，未讀取 pickle 內容。'),
            dict(id='bursts', available=geometry_ok, title='實際共同 burst 報告', detail='由原生 computeBaselines 產生的工作台紀錄，每個所選子波束需至少兩個共同 burst。'),
            dict(id='esd', available=_statistics_present(stats['esd']), title='ESD 日誌統計', detail='只解析明確標示 runESD 的完整段落；需有限統計及非零樣本，缺少段落不推測數值。'),
            dict(id='coregistration', available=_statistics_present(stats['range_coregistration']), title='Range coregistration 日誌統計', detail='只解析明確標示 runRangeCoreg 的完整段落；需有限統計及非零樣本，與 ESD 統計分開。'),
            dict(id='products', available=outputs_ok, title='地理編碼檔案與描述網格', detail='檢查四種原生成果非空與 VRT 網格一致；不透過 VRT 載入任何來源，不檢查每個像素。'),
            dict(id='results', available=type(valid_pixels) is int and valid_pixels > 0, title='成果整理的有效像素紀錄', detail=f'保存的有效像素數：{valid_pixels if type(valid_pixels) is int else "尚無紀錄"}；其遮罩不是高品質或陸地遮罩。'),
        ]
        evidence = dict(project_id=project_id, project_root=str(root), status=s['status'], sources=sources,
                        selection={k: c.get(k) for k in ('polarization', 'swaths', 'roi', 'properties', 'dem', 'orbit_mode')},
                        steps=steps, files=files, geometry=parsed['geometry'], resolved=parsed['resolved'],
                        results_summary=dict(valid_pixels=valid_pixels, versions=results.get('versions'), meaning=results.get('meaning')),
                        products=products, statistics=stats, checks=checks)
        evidence_id = _hash(evidence)
        review = self._review(project_id)
        stale = bool(review.get('evidence_id') and review['evidence_id'] != evidence_id)
        items = review.get('items', {})
        all_reviewed = all(items.get(x['id'], {}).get('status') == 'pass' for x in REVIEW_ITEMS)
        if stale:
            state, message = 'stale', '處理證據已變更，保留舊評閱供參考；請重新檢查並儲存。'
        elif any(x.get('status') == 'fail' for x in items.values()):
            state, message = 'needs_attention', '人工評閱發現問題，需先處理並重新驗證。'
        elif review.get('dataset_kind') == 'synthetic':
            state, message = 'synthetic_only', '合成資料檢查；不計入真實影像驗證。'
        elif review.get('dataset_kind') == 'real' and all_reviewed and all(x['available'] for x in checks):
            state, message = 'user_review_complete', '已保存本專案的人工真實影像評閱；尚未經維護者獨立驗證，功能仍屬試驗。'
        else:
            state, message = 'pending', '真實影像驗證尚未完成；補齊證據與人工評閱後再判斷科學品質。'
        return dict(schema=1, generated_at=_stamp(), project_id=project_id,
                    project_name=s.get('display_name') or c.get('name') or project_id,
                    evidence_id=evidence_id, evidence=evidence, review=review, review_stale=stale,
                    review_items=REVIEW_ITEMS, references=REFERENCES, status=state, message=message,
                    workflow_experimental=True, tops_stack_enabled=False,
                    notice='這是檢查資料與人工評閱紀錄，不是自動認證。軟體測試、安裝成功、檔案存在或人工勾選，都不會解除 TOPS 試驗標籤或開啟 topsStack。')

    def save(self, project_id, payload):
        if not isinstance(payload, dict):
            raise ValueError('評閱內容格式不正確。')
        with self.lock:
            report = self.inspect(project_id)
            if payload.get('evidence_id') != report['evidence_id']:
                raise ValueError('處理證據已更新，請重新讀取後再儲存評閱。')
            kind = payload.get('dataset_kind', 'unknown')
            if kind not in ('unknown', 'synthetic', 'real'):
                raise ValueError('請選擇資料性質。')
            reviewer = _text(payload.get('reviewer', ''), '評閱者', 120)
            dataset_note = _text(payload.get('dataset_note', ''), '資料來源說明')
            supplied = payload.get('items', {})
            if not isinstance(supplied, dict) or set(supplied) - {x['id'] for x in REVIEW_ITEMS}:
                raise ValueError('不支援的評閱項目。')
            items = {}
            for item in REVIEW_ITEMS:
                row = supplied.get(item['id'], {})
                if not isinstance(row, dict) or row.get('status', 'pending') not in STATUSES:
                    raise ValueError('評閱狀態不正確。')
                status = row.get('status', 'pending')
                note = _text(row.get('note', ''), item['title'] + '說明')
                if status != 'pending' and (not reviewer or not note):
                    raise ValueError('已評閱的項目需填寫評閱者與觀察說明。')
                items[item['id']] = dict(status=status, note=note)
            independent = payload.get('independent', {})
            allowed = ('record_location', 'software_version', 'same_inputs_settings', 'grid_reference_mask', 'comparison_results')
            if not isinstance(independent, dict) or set(independent) - set(allowed):
                raise ValueError('獨立對照資料格式不正確。')
            independent = {key: _text(independent.get(key, ''), '獨立對照說明') for key in allowed}
            if items['independent']['status'] == 'pass' and not all(independent.values()):
                raise ValueError('獨立對照通過前，請完整記錄執行位置、版本、來源／設定、網格／參考與差異結果。')
            if kind == 'real' and not dataset_note:
                raise ValueError('真實影像請填寫來源與產品取得說明。')
            review = dict(evidence_id=report['evidence_id'], saved_at=_stamp(), dataset_kind=kind,
                          reviewer=reviewer, dataset_note=dataset_note, items=items, independent=independent,
                          independent_evidence='user_reported_not_automatically_compared')
            directory = self._directory(project_id, create=True)
            if (directory / 'review.json').is_symlink():
                raise ValueError('評閱檔案不可使用符號連結。')
            _write_json(directory / 'review.json', review)
            return self.inspect(project_id)

    def artifact(self, project_id, name):
        if name not in ('validation.json', 'validation.html'):
            raise ValueError('不支援的驗證報告格式。')
        with self.lock:
            report = self.inspect(project_id)
            directory = self._directory(project_id, create=True)
            path = directory / name
            if path.is_symlink():
                raise ValueError('報告檔案不可使用符號連結。')
            if name.endswith('.json'):
                _write_json(path, report)
            else:
                value = render_html(report)
                temp = directory / 'validation.html.tmp'
                if temp.is_symlink():
                    raise ValueError('報告暫存檔不可使用符號連結。')
                temp.write_text(value, encoding='utf-8')
                temp.replace(path)
            return path


def render_html(report):
    """Self-contained, escaped, printable report with no network resources."""
    esc = lambda value: html.escape(str(value), quote=True)
    r = report['review']
    status = dict(pending='待檢查', reviewed='已看過，尚未判定', **{'pass': '人工判定符合', 'fail': '發現問題'})
    chunks = ['<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>Sentinel-1 驗證紀錄</title><style>body{font:16px/1.65 system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:1rem;color:#183d32}table{border-collapse:collapse;width:100%;margin:1rem 0}td,th{border:1px solid #bbb;padding:.5rem;text-align:left;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f3;padding:1rem}p{overflow-wrap:anywhere}.notice{border:1px solid #b18548;padding:1rem}@media print{body{max-width:none;font-size:10pt}a{color:inherit}h2,h3{break-after:avoid}tr{break-inside:avoid}}</style>',
              '<h1>Sentinel-1 TOPS 驗證紀錄</h1>', '<p>' + esc(report['project_name']) + '</p>',
              '<p class="notice">' + esc(report['message']) + ' ' + esc(report['notice']) + '</p>',
              '<p>產生時間：' + esc(report['generated_at']) + '<br>資料性質（人工聲明）：' + esc(r.get('dataset_kind', 'unknown')) +
              '<br>評閱者：' + esc(r.get('reviewer', '')) + '<br>評閱是否過期：' + ('是' if report['review_stale'] else '否') + '</p>',
              '<h2>自動整理的檔案與紀錄</h2><table><tr><th>項目</th><th>證據</th><th>說明</th></tr>']
    for row in report['evidence']['checks']:
        chunks.append('<tr><td>' + esc(row['title']) + '</td><td>' + ('已找到' if row['available'] else '缺少或不完整') + '</td><td>' + esc(row['detail']) + '</td></tr>')
    chunks.append('</table><h2>人工科學評閱</h2>')
    for row in REVIEW_ITEMS:
        item = r.get('items', {}).get(row['id'], {})
        chunks += ['<h3>' + esc(row['title']) + ' · ' + esc(status.get(item.get('status'), '待檢查')) + '</h3>',
                   '<p>' + esc(row['help']) + '</p><pre>' + esc(item.get('note', '')) + '</pre>']
    chunks += ['<h2>人工提供的獨立對照證據</h2><p>此處由評閱者提供，工作台未自動執行或驗證獨立處理，也未自動計算差異。</p><pre>' +
               esc(json.dumps(r.get('independent', {}), ensure_ascii=False, indent=2)) + '</pre>',
               '<h2>檔案、步驟、網格與來源詳情</h2><pre>' + esc(json.dumps(report['evidence'], ensure_ascii=False, indent=2)) + '</pre>',
               '<p>處理證據指紋：' + esc(report['evidence_id']) + '</p><h2>上游實作參考</h2><ul>']
    for row in REFERENCES:
        chunks.append('<li><a href="' + esc(row['url']) + '">' + esc(row['title']) + '</a></li>')
    return ''.join(chunks) + '</ul></html>'
