"""Portable, editable research reports from explicit, completed analysis snapshots.

The web server uses only the standard library. All output is derived and stored
in the workbench registry, including for projects on read-only external disks.
"""
from pathlib import Path
from xml.sax.saxutils import escape
import base64
import copy
import hashlib
import html
import json
import math
import os
import re
import signal
import struct
import subprocess
import threading
import time
import uuid
import zipfile

from .core import atomic_json
from . import DISPLAY_VERSION
from .analysis import clean_env
from .core import find_conda

ALLOWED = {'report.html', 'report.docx', 'report.json', 'research-report.zip'}
MEANING = '位移與速度為相對空間參考點及時間參考日期的 LOS 值。正值朝向衛星，負值遠離衛星；不能直接等同垂直沉降。'


def read_json(path):
    p = Path(path)
    if p.is_symlink() or p.stat().st_size > 16 * 1024**2:
        raise ValueError('報告來源不是一般 JSON 檔或超過大小限制。')
    return json.loads(p.read_text())


def label(value):
    if value is None:
        return '未記錄'
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, float):
        return f'{value:.6g}' if math.isfinite(value) else '無效數值'
    if isinstance(value, list):
        if not value: return '無'
        result = '、'.join(label(v) for v in value[:30])
        if len(value) > 30: result += f' …（共 {len(value)} 項，完整清單見 JSON）'
    elif isinstance(value, dict):
        result = '；'.join(str(k) + '：' + label(v) for k,v in value.items()) or '無'
    else:
        result = str(value)
    return result if len(result) <= 1000 else result[:1000] + ' …（完整內容見 JSON）'


def option_rows(options, prefix=''):
    """Keep a large override dictionary in separate, editable table rows."""
    names = dict(name='分析名稱', reference_date='時間參考日期',
                 temporal_coherence='時間相干性門檻', reference_coherence='參考點相干性門檻',
                 deramp='空間斜坡移除', dem_error='DEM 誤差校正', memory='記憶體上限 GiB',
                 excluded='排除配對', subset='像素範圍', subset_lalo='經緯度範圍')
    rows=[]
    for key,value in options.items():
        raw=prefix+key
        if isinstance(value,dict): rows.extend(option_rows(value,raw+'.'))
        else: rows.append([names.get(raw,raw),label(value)])
    return rows


def scrub_paths(value):
    """Mask local paths in structured fields, keys and free-form text.

    Keep web/remote URLs as provenance. Bare paths containing spaces are
    conservatively consumed through the next clear delimiter; only the final
    component is retained. This avoids leaking an intermediate folder just
    because an upstream diagnostic did not quote its pathname.
    """
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            masked = scrub_paths(key) if isinstance(key, str) else key
            unique, index = masked, 2
            while unique in cleaned:
                unique = str(masked) + f' [{index}]'
                index += 1
            cleaned[unique] = scrub_paths(item)
        return cleaned
    if isinstance(value, list):
        return [scrub_paths(v) for v in value]
    if isinstance(value, str):
        file_url = r'(?i:file)://[^\r\n\t<>"\'\[\]{}(),;|]*?(?=\s+[A-Za-z][A-Za-z0-9+.-]*://|[\r\n\t<>"\'\[\]{}(),;|]|$)'
        url = re.compile(r'(' + file_url + r'|[A-Za-z][A-Za-z0-9+.-]*://[^\s<>"\']+)')
        local = re.compile(r'(?<![\w/\\.])(?://(?!/)|/(?!/)|[A-Za-z]:[\\/]|\\\\)[^\r\n\t<>"\'\[\]{}(),;|]*?(?=\s+(?://(?!/)|/(?!/)|[A-Za-z]:[\\/]|\\\\)|[\r\n\t<>"\'\[\]{}(),;|]|$)')
        def mask(match):
            raw = match.group(0)
            tail = raw[len(raw.rstrip()):]
            name = re.split(r'[\\/]', raw.rstrip().rstrip('/\\'))[-1]
            return '[本機路徑]' + ('/' + name if name else '') + tail
        pieces = url.split(value)
        def clean_piece(part, is_url):
            if is_url and part.lower().startswith('file://'):
                # file://localhost, UNC hosts and drive letters are local
                # provenance too, not a public citation URL.
                payload=part.split('://', 1)[1]
                separator=re.search(r'[/\\]',payload)
                if not separator:return '[本機路徑]'
                return local.sub(mask, '/' + payload[separator.start():].lstrip('/\\'))
            return part if is_url else local.sub(mask, part)
        return ''.join(clean_piece(part, i % 2) for i, part in enumerate(pieces))
    return value


def png_size(data):
    if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('報告圖片必須是有效 PNG。')
    width, height = struct.unpack('>II', data[16:24])
    if not width or not height or width * height > 40_000_000:
        raise ValueError('報告圖片尺寸不合法。')
    return width, height


def source_path(folder, path):
    """An ordinary file inside the source tree, with no owned symlink parents.

    Outer OS aliases such as macOS /var -> /private/var remain allowed. Links
    at or below the supplied source root are rejected before reading content.
    """
    folder = Path(folder).absolute()
    path = Path(path).absolute()
    if '..' in path.parts or folder.is_symlink():
        raise ValueError('報告來源路徑不能跳出資料夾或包含符號連結。')
    root = folder.resolve(strict=True)
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError('報告來源路徑不能包含符號連結。')
        if component.resolve() == root:
            break
    else:
        raise ValueError('報告來源必須位於指定資料夾內。')
    if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
        raise ValueError('報告來源不是資料夾內的一般檔案。')
    return path


def source_identities(folder, job):
    """Track exact source file identities while a report is generated, without reading a full cube."""
    folder=Path(folder);selected=Path(job['timeseries_file'])
    state=folder.parents[1]/'.helper/state.json'
    if state.is_file():
        source_path(folder.parents[1],state)
        original=Path(read_json(state)['config']['root'])/'analyses'/folder.name
        if selected.is_absolute() and selected.is_relative_to(original):selected=folder/selected.relative_to(original)
    if not selected.is_absolute():selected=folder/selected
    if not selected.resolve().is_relative_to(folder.resolve()):raise ValueError('時間序列必須位於所選分析內。')
    names=[folder/'velocity.h5',folder/'maskTempCoh.h5',folder/'temporalCoherence.h5',folder/'inputs/geometryRadar.h5',selected]
    result={}
    for p in names:
        source_path(folder,p)
        stat=p.stat();result[str(p)]=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns,ctime_ns=stat.st_ctime_ns,device=stat.st_dev,inode=stat.st_ino)
    return result


def report_sections(snapshot):
    """A shared semantic document tree for HTML and editable Word output."""
    sections = []
    def p(text): sections.append(('p', text))
    def heading(text): sections.append(('h', text))
    def table(headers, rows): sections.append(('table', headers, rows))
    p('本報告整理已完成分析的設定、品質資訊與成果，供研究檢查及後續編輯。運算完成不等同科學品質已通過驗證。')
    table(['項目', '內容'], [
        ['專案', snapshot['project']['name']], ['流程', snapshot['project']['workflow']],
        ['整理時間', snapshot['created']], ['作者', snapshot['author'] or '未填寫'],
        ['工作台版本', snapshot['version']], ['來源類型', snapshot['project']['origin']],
    ])
    p(MEANING)
    if snapshot['notes']:
        heading('研究者說明')
        for paragraph in snapshot['notes'].splitlines():
            if paragraph.strip(): p(paragraph)
    heading('原始處理紀錄')
    table(['項目', '紀錄'], [
        ['原始參考日期', label(snapshot['project'].get('reference'))],
        ['原始資料日期', label(snapshot['project'].get('dates'))],
        ['一般干涉配對', label(snapshot['project'].get('pairs'))],
        ['電離層排除配對', label(snapshot['project'].get('ion_excluded'))],
        ['電離層處理紀錄', label({{'estimate':'估計電離層','apply':'套用校正','subsequent':'一般後續配對數','subsequent_ion':'電離層後續配對數'}.get(k,k):v for k,v in (snapshot['project'].get('ion') or {}).items()}) if snapshot['project'].get('ion') is not None else '未記錄'],
    ])
    for run in snapshot['analyses']:
        result, job = run['results'], run['job']
        heading('分析 ' + run['name'])
        p('分析代號：' + run['id'] + '。' + result.get('velocity_meaning', MEANING))
        table(['項目', '紀錄'], [
            ['觀測日期', label(result.get('dates'))],
            ['時間參考日期', label(result.get('reference_date'))],
            ['空間參考像素 行／列', label(result.get('reference_pixel'))],
            ['品質遮罩後像素', label(result.get('valid_pixels'))],
            ['總像素', label(result.get('total_pixels'))],
            ['成果單位', label(result.get('unit'))],
            ['軟體版本', label(result.get('versions'))],
        ])
        for figure in run.get('figures', []):
            sections.append(('image', figure['file'], figure['caption']))
        param = result.get('parameter_report') or job.get('parameter_report') or {}
        corrections = param.get('corrections', {})
        if corrections:
            heading('校正執行紀錄')
            table(['校正項目', '紀錄'], [[k, label(v)] for k, v in corrections.items()] if isinstance(corrections, dict) else [['校正', label(corrections)]])
        heading('分析設定')
        table(['設定', '值'], option_rows(job.get('options', {})))
    comp = snapshot.get('comparison')
    if comp:
        heading('兩次分析的比較')
        compatibility = comp.get('compatibility', {})
        p('可直接計算 B − A 差值：' + label(compatibility.get('allow_difference', False)))
        for reason in compatibility.get('reasons', []): p(label(reason))
        p('差值代表兩次處理的結果差異，不代表任一結果的真實誤差或科學準確率。')
        if comp.get('coverage'):table(['共同有效範圍', '像素數'], [[{'shared':'A、B 皆有效','left_only':'只有 A 有效','right_only':'只有 B 有效','neither':'皆無效'}.get(k,k), label(v)] for k,v in comp['coverage'].items()])
        if comp.get('difference',{}).get('statistics'):table(['B − A 差值統計', '數值（速度為 mm/year）'], [[{'count':'共同有效像素數','mean':'平均','std':'標準差','rms':'均方根','min':'最小','max':'最大'}.get(k,k),label(v)] for k,v in comp['difference']['statistics'].items()])
        if comp.get('options_diff'):table(['設定', 'A → B'], [[r['key'],label(r.get('left'))+' → '+label(r.get('right'))] for r in comp['options_diff']])
        for fig in snapshot.get('comparison_figures', []): sections.append(('image', fig['file'], fig['caption']))
    point = snapshot.get('point')
    if point:
        heading('所選點位的時間序列')
        p(f"分析 {point['analysis_id']}；原始像素行 {point['y']}、列 {point['x']}；緯度 {point['latitude']:.6f}、經度 {point['longitude']:.6f}。")
        p('數值由報告產生時重新讀取所選分析；沒有以畫面降採樣值取代原始數值。')
        if snapshot.get('point_figure'): sections.append(('image', snapshot['point_figure'], '圖  點位相對 LOS 位移，單位 mm；日期軸採實際時間間隔。'))
        table(['觀測日期', '相對 LOS 位移 mm'], [[str(d), f'{v:.3f}' if isinstance(v, (float, int)) and math.isfinite(v) else '無效'] for d, v in zip(point['dates'], point['displacement_mm'])])
        paired=snapshot.get('comparison_point',{})
        for side in ('left','right'):
            other=paired.get(side,{})
            if not other.get('available') or other.get('analysis_id')==point['analysis_id']:continue
            p(('A' if side=='left' else 'B')+' 版本同位置的曲線；參考基準：'+label(other.get('reference')))
            table(['觀測日期', '相對 LOS 位移 mm'], [[str(d),f'{v:.3f}'] for d,v in zip(other['dates'],other['displacement_mm'])])
        for reason in paired.get('reasons',[]):p(reason)
    heading('品質判讀與重現資訊')
    p('請另外檢查配對網路、相干性、解纏連通性、參考點穩定性及校正前後的空間特徵。報告不自動判定最佳參數，也不代替地面觀測或獨立處理驗證。')
    p('本次輸出的 JSON 保存設定快照及來源中繼資料 SHA256，可用於核對報告對應的分析版本；這不是全部 SAR 影像的完整內容驗證。')
    p('長日期、配對與設定清單在本文節錄；完整內容保留於 report.json。')
    verification=snapshot.get('source_verification',{})
    if verification.get('point_analysis'):
        p('重新抽取點位前已檢查所選分析的 HDF5 不含間接連結或外部／虛擬資料集，並核對抽點前後的來源檔案身分。這不是整幅影像的數值或科學品質驗證。')
    elif verification:
        p('本報告整理既有中繼資料與成果圖；HDF5 僅核對容器檔案身分，未重新讀取像素或檢查其內部資料連結。')
    if snapshot['project']['origin'] == '外部手動成果':
        p('此專案由外部手動成果匯入。未提供的原始指令、電離層校正歷程及人工檢查紀錄均保持未知，不以工作台的預設值補寫。')
    if not snapshot.get('include_paths'): p('本文件的本機絕對路徑已縮減為檔名；重現時需另行指定資料位置。')
    table(['來源紀錄', 'SHA256'], [[x['name'], x['sha256']] for x in snapshot.get('source_records', [])])
    return sections


def html_report(snapshot, out):
    parts = ['<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(snapshot['title']) + '</title>',
             '<style>body{font-family:-apple-system,"Noto Sans CJK TC",sans-serif;max-width:900px;margin:40px auto;padding:0 22px;color:#1c302b;line-height:1.65}h1{font-size:30px;color:#000}h2{font-size:21px;margin-top:32px;color:#000;break-after:avoid}table{width:100%;border-collapse:collapse;font-size:13px;margin:18px 0 26px;table-layout:fixed}td,th{border:1px solid #d9d9d9;padding:9px 12px;overflow-wrap:anywhere;vertical-align:middle}th{background:#e6eef1;color:#000;text-align:left}th:first-child,td:first-child{width:27%}tr{break-inside:avoid}thead{display:table-header-group}img{max-width:100%;max-height:650px;object-fit:contain}figure{margin:24px 0;break-inside:avoid}figcaption{font-size:12px;color:#52635e}p{overflow-wrap:anywhere}@page{size:A4;margin:20mm}@media print{body{max-width:none;margin:0;padding:0;font-size:10pt}h1{font-size:22pt}table{font-size:9pt}img{max-height:180mm}}</style><body><h1>' + html.escape(snapshot['title']) + '</h1>']
    for item in report_sections(snapshot):
        if item[0] in ('p', 'h'):
            tag = 'p' if item[0] == 'p' else 'h2'
            parts.append(f'<{tag}>' + html.escape(item[1]) + f'</{tag}>')
        elif item[0] == 'table':
            parts.append('<table><thead><tr>' + ''.join('<th scope="col">'+html.escape(str(x))+'</th>' for x in item[1]) + '</tr></thead><tbody>')
            for row in item[2]: parts.append('<tr>' + ''.join('<td>'+html.escape(str(x))+'</td>' for x in row) + '</tr>')
            parts.append('</tbody></table>')
        elif item[0] == 'image':
            data = base64.b64encode((out/item[1]).read_bytes()).decode()
            parts.append('<figure><img alt="'+html.escape(item[2], quote=True)+'" src="data:image/png;base64,'+data+'"><figcaption>'+html.escape(item[2])+'</figcaption></figure>')
    (out/'report.html').write_text('\n'.join(parts) + '</body></html>')


def docx_report(snapshot, out):
    """Standard OOXML so users do not need a document library installed."""
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    def text(t):
        # Source names and native metadata bypass the report form's input
        # checks. Remove every XML 1.0-invalid code point, not only NUL.
        valid = (c for c in str(t) if c in '\t\n\r' or
                 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or
                 0x10000 <= ord(c) <= 0x10FFFF)
        return escape(''.join(valid))
    def para(t, style=None):
        pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
        return '<w:p>'+pr+'<w:r><w:t xml:space="preserve">'+text(t)+'</w:t></w:r></w:p>'
    parts = [para(snapshot['title'], 'Title')]
    pictures = []
    for item in report_sections(snapshot):
        if item[0] == 'p': parts.append(para(item[1]))
        elif item[0] == 'h': parts.append(para(item[1], 'Heading1'))
        elif item[0] == 'table':
            parts.append('<w:tbl><w:tblPr><w:tblW w:w="9360" w:type="dxa"/><w:tblBorders>'+''.join(f'<w:{edge} w:val="single" w:sz="4" w:color="D9D9D9"/>' for edge in ('top','left','bottom','right','insideH','insideV'))+'</w:tblBorders><w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="100" w:type="dxa"/><w:left w:w="130" w:type="dxa"/><w:bottom w:w="100" w:type="dxa"/><w:right w:w="130" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid><w:gridCol w:w="2500"/><w:gridCol w:w="6860"/></w:tblGrid>')
            for index, row in enumerate([item[1], *item[2]]):
                parts.append('<w:tr><w:trPr><w:cantSplit/>'+('<w:tblHeader/>' if index == 0 else '')+'</w:trPr>')
                for col, value in enumerate(row):
                    parts.append(f'<w:tc><w:tcPr><w:tcW w:w="{2500 if col == 0 else 6860}" w:type="dxa"/>'+('<w:shd w:fill="E6EEF1"/>' if index == 0 else '')+'<w:vAlign w:val="center"/></w:tcPr>'+para(value, 'TableText')+'</w:tc>')
                parts.append('</w:tr>')
            parts.append('</w:tbl>'+para(''))
        elif item[0] == 'image':
            data=(out/item[1]).read_bytes(); w,h=png_size(data)
            scale=min(5.9*914400/w,5.7*914400/h);cx,cy=int(w*scale),int(h*scale)
            number=len(pictures)+1; pictures.append((item[1],data))
            parts.append(f'<w:p><w:pPr><w:keepNext/></w:pPr><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{number}" name="Figure {number}" descr="{text(item[2]).replace(chr(34), "&quot;")}"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="{number}" name="Figure {number}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdImage{number}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')
            parts.append(para(item[2], 'Caption'))
    parts.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1273" w:bottom="1134" w:left="1273"/></w:sectPr>')
    document=f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'+''.join(parts)+'</w:body></w:document>'
    # A referenced custom style needs a display name. Without it LibreOffice
    # misimports cell paragraphs, placing their text after the table.
    styles=f'''<w:styles xmlns:w="{ns}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="PingFang TC"/><w:sz w:val="22"/><w:color w:val="000000"/><w:lang w:val="en-US" w:eastAsia="zh-TW"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="150" w:line="300" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="180"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="40" w:line="260" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="19"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="19"/></w:rPr></w:style></w:styles>'''
    relbase='http://schemas.openxmlformats.org/package/2006/relationships'
    rels=f'<Relationships xmlns="{relbase}"><Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'+''.join(f'<Relationship Id="rIdImage{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image{i}.png"/>' for i in range(1,len(pictures)+1))+'</Relationships>'
    types='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    with zipfile.ZipFile(out/'report.docx','w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml',types)
        z.writestr('_rels/.rels',f'<Relationships xmlns="{relbase}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr('word/document.xml',document);z.writestr('word/styles.xml',styles);z.writestr('word/_rels/document.xml.rels',rels)
        for i,(_,data) in enumerate(pictures,1): z.writestr(f'word/media/image{i}.png',data)


class Reports:
    def __init__(self, analyses, comparisons):
        self.analyses=analyses;self.engine=analyses.engine;self.comparisons=comparisons
        self.home=self.engine.home/'research'/'reports';self.lock=threading.RLock();self.active=None
        self.cancelled=threading.Event();self.thread=None;self.process=None

    def folder(self,key,report):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',key) or not re.fullmatch(r'[a-f0-9]{16}',report):
            raise ValueError('無效報告代號。')
        p=self.home/key/report
        for item in (self.home.parent,self.home,p.parent,p):
            if item.is_symlink():raise ValueError('報告資料夾不能是符號連結。')
        return p

    def get(self,key,report):
        self.engine.get(key)
        j=read_json(self.folder(key,report)/'job.json')
        if j['status']=='running' and self.active!=(key,report):
            j.update(status='failed',error='報告產生曾中斷，請重新建立。')
        return j

    def listing(self,key):
        self.engine.get(key);result=[]
        p=self.home/key
        if p.is_symlink():raise ValueError('報告資料夾不能是符號連結。')
        if p.exists():
            for f in sorted(p.glob('*/job.json'),reverse=True):
                try:result.append(self.get(key,f.parent.name))
                except (OSError,ValueError):continue
        return sorted(result,key=lambda x:x['created'],reverse=True)[:30]

    def start(self,key,data):
        with self.lock,self.engine.lock:
            if self.active or self.engine.active:raise ValueError('請等待目前工作完成後再產生報告。')
            s=self.engine.get(key);run=str(data.get('run',''))
            job=self.analyses.get(key,run)
            if job.get('status')!='complete' or job.get('kind')!='mintpy':raise ValueError('請選擇已完成的 MintPy 分析。')
            title=str(data.get('title') or s['config'].get('name') or 'SAR 研究成果報告').strip()
            author=str(data.get('author','')).strip();notes=str(data.get('notes','')).strip()
            if not title or len(title)>160 or len(author)>120 or len(notes)>12000:raise ValueError('標題、作者或說明超過長度限制。')
            if any(ord(c)<32 and c not in '\n\t\r' for c in title+author+notes):raise ValueError('文字包含不支援的控制字元。')
            point=data.get('point')
            if point is not None and (not isinstance(point,dict) or any(type(point.get(k)) is not int or point[k]<0 for k in ('y','x'))):raise ValueError('點位必須是原始行、列的非負整數。')
            comp=data.get('comparison')
            if comp:
                c=self.comparisons.get(key,str(comp))
                if c.get('status')!='complete':raise ValueError('請先完成成果比較。')
            report=uuid.uuid4().hex[:16];out=self.folder(key,report);out.mkdir(parents=True,exist_ok=False)
            record=dict(id=report,project_id=key,run=run,title=title,author=author,notes=notes,point=point,comparison=comp,include_paths=data.get('include_paths') is True,status='running',created=time.strftime('%Y-%m-%d %H:%M:%S'),error='')
            atomic_json(out/'job.json',record);self.active=(key,report);self.engine.active=key+':report:'+report;self.cancelled.clear()
            self.thread=threading.Thread(target=self.worker,args=(key,report),daemon=True);self.thread.start();return record

    def worker(self,key,report):
        out=self.folder(key,report);record=read_json(out/'job.json')
        try:
            s=self.engine.get(key);cfg=s['config'];source_records=[];source_bytes={};source_roots={};runs=[]
            def read_source(path,name,folder):
                p=source_path(folder,path)
                if p.stat().st_size>16*1024**2:raise ValueError('來源描述檔不合法。')
                raw=p.read_bytes();source_bytes[p]=raw;source_roots[p]=folder
                source_records.append(dict(name=name,sha256=hashlib.sha256(raw).hexdigest()))
                return json.loads(raw)
            ids=[record['run']];comparison=None;comparison_figures=[];scientific_stamps={}
            if record.get('comparison'):
                self.comparisons.verify(key,record['comparison'])
                comparison=self.comparisons.get(key,record['comparison'])
                for side in ('left','right'):
                    v=comparison.get(side,{})
                    rid=v.get('id') or v.get('run') if isinstance(v,dict) else v
                    if rid and rid not in ids:ids.append(rid)
                if record['run'] not in [((comparison.get(side) or {}).get('id') or (comparison.get(side) or {}).get('run')) if isinstance(comparison.get(side),dict) else comparison.get(side) for side in ('left','right')]:
                    raise ValueError('比較紀錄不包含所選分析，請重新選擇。')
                for side in ('left','right','difference'):
                    image=comparison.get(side,{}).get('map')
                    if not image:continue
                    path=self.comparisons.artifact(key,record['comparison'],image['file'])
                    boundary=self.comparisons.folder(key,record['comparison'])
                    path=source_path(boundary,path)
                    if path.stat().st_size>20*1024**2:raise ValueError('報告圖片過大。')
                    raw=path.read_bytes();png_size(raw);source_bytes[path]=raw;source_roots[path]=boundary
                    name='comparison-'+side+'.png';(out/name).write_bytes(raw)
                    caption=('A' if side=='left' else 'B' if side=='right' else 'B − A')+f' 地理預覽；色階 {image["min"]:.3f} 至 {image["max"]:.3f} '+str(image.get('unit') or comparison.get(side,{}).get('unit') or 'mm/year')+'。'
                    caption+='地圖僅供降採樣展示；差值統計使用原生共同有效像素。' if image.get('coordinate_system')=='EPSG:3857' else '此圖為原生雷達座標預覽，未套用底圖。'
                    comparison_figures.append(dict(file=name,caption=caption))
            for run in ids:
                folder=self.analyses.folder(key,run)
                job=read_source(folder/'analysis.json',run+'/analysis.json',folder);result=read_source(folder/'results.json',run+'/results.json',folder)
                if job.get('status')!='complete' or job.get('kind')!='mintpy':raise ValueError('來源分析狀態已改變。')
                scientific_stamps[run]=source_identities(folder,job)
                figures=[]
                for field,caption in [('preview','LOS 速度'),('coherence_preview','時間相干性')]:
                    preview=result.get(field)
                    if not preview:continue
                    name=preview.get('file','');path=self.analyses.artifact(key,run,name)
                    path=source_path(folder,path)
                    if path.stat().st_size>20*1024**2:raise ValueError('報告圖片過大。')
                    raw=path.read_bytes();png_size(raw);source_bytes[path]=raw;source_roots[path]=folder
                    filename=run+'-'+field+'.png';(out/filename).write_bytes(raw)
                    figures.append(dict(file=filename,caption=f'{caption}，雷達座標預覽。色階 {preview.get("min", "未記錄")} 至 {preview.get("max", "未記錄")}；'+('mm/year。' if field=='preview' else '無單位。')+'灰色為遮罩或無資料。'))
                runs.append(dict(id=run,name=job.get('options',{}).get('name') or run,job=job,results=result,figures=figures))
            snapshot=dict(id=report,title=record['title'],author=record['author'],notes=record['notes'],created=record['created'],version=DISPLAY_VERSION,include_paths=record['include_paths'],project=dict(id=key,name=cfg.get('name',key),workflow=cfg.get('workflow','alosStack'),reference=cfg.get('reference'),dates=[d.get('date') if isinstance(d,dict) else d for d in s.get('dates',[])],pairs=s.get('expected_pairs'),ion_excluded=None if s.get('import_only') else s.get('excluded'),ion=s.get('ion'),origin='外部手動成果' if s.get('import_only') or s.get('legacy_import') else '工作台處理紀錄'),analyses=runs,comparison=comparison,source_records=source_records)
            snapshot['comparison_figures']=comparison_figures
            snapshot['source_identities']=scientific_stamps
            snapshot['source_verification']=dict(method='description SHA256 and container size/mtime/ctime/device/inode before and after',point_analysis=None,full_pixel_validation=False)
            if self.cancelled.is_set():raise InterruptedError('報告已停止。')
            if not record['include_paths']:snapshot=scrub_paths(snapshot)
            atomic_json(out/'report.json',snapshot)
            if record.get('point'):
                env=runs[0]['job']['environment'];conda=find_conda(env)
                if not conda:raise ValueError('找不到所選分析的 Conda，無法繪製報告曲線。')
                cache=out/'plot-cache';cache.mkdir()
                command=[conda,'run','--no-capture-output','-p',env,'python',str(Path(__file__).with_name('report_plot.py')),str(self.analyses.folder(key,record['run'])/'analysis.json'),str(record['point']['y']),str(record['point']['x']),str(out/'point-data.json'),str(out/'point.png')]
                if record.get('comparison'):command.append(str(self.comparisons.artifact(key,record['comparison'],'result.json')))
                self.process=subprocess.Popen(command,cwd=self.analyses.folder(key,record['run']),env=clean_env(cache),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
                if self.cancelled.is_set():os.killpg(self.process.pid,signal.SIGTERM)
                try:stdout,stderr=self.process.communicate(timeout=120)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid,signal.SIGKILL);self.process.communicate();raise ValueError('報告曲線繪製逾時。')
                if self.process.returncode:raise ValueError('報告曲線繪製失敗：'+(stderr or stdout)[-800:])
                values=read_json(out/'point-data.json');point=values['point']
                if point.get('analysis_id')!=record['run']:raise ValueError('點位與所選分析不一致。')
                if values.get('comparison_point'):snapshot['comparison_point']=values['comparison_point']
                png_size((out/'point.png').read_bytes());snapshot['point']=point;snapshot['point_figure']='point.png'
                snapshot['source_verification']['point_analysis']=record['run']
                if not record['include_paths']:snapshot=scrub_paths(snapshot)
                atomic_json(out/'report.json',snapshot)
            html_report(snapshot,out);docx_report(snapshot,out)
            with zipfile.ZipFile(out/'research-report.zip','w',zipfile.ZIP_DEFLATED) as z:
                for name in ('report.json','report.html','report.docx'):z.write(out/name,name)
                for f in out.glob('*.png'):z.write(f,f.name)
            for path,raw in source_bytes.items():
                source_path(source_roots[path],path)
                if path.stat().st_size!=len(raw) or path.read_bytes()!=raw:raise ValueError('來源分析在報告產生期間改變，請重新產生。')
            for run in runs:
                if source_identities(self.analyses.folder(key,run['id']),run['job'])!=scientific_stamps[run['id']]:raise ValueError('科學成果在報告產生期間改變，請重新產生。')
            if record.get('comparison'):self.comparisons.verify(key,record['comparison'])
            if self.cancelled.is_set():raise InterruptedError('報告已停止。')
            record.update(status='complete',files=sorted(ALLOWED))
        except Exception as e:record.update(status='cancelled' if self.cancelled.is_set() else 'failed',error=str(e))
        finally:
            atomic_json(out/'job.json',record)
            with self.lock,self.engine.lock:
                self.active=None
                self.process=None
                if self.engine.active==key+':report:'+report:self.engine.active=None

    def stop(self,key,report):
        with self.lock:
            if self.active!=(key,report):raise ValueError('這份報告目前沒有執行中工作。')
            self.cancelled.set();process=self.process
        if process and process.poll() is None:
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:pass

    def shutdown(self):
        active=self.active
        if active:
            try:self.stop(*active)
            except ValueError:pass
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=4)
        if self.process and self.process.poll() is None:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass

    def artifact(self,key,report,name):
        if name not in ALLOWED:raise ValueError('無效報告檔名。')
        if self.get(key,report)['status']!='complete':raise ValueError('報告尚未完成。')
        p=self.folder(key,report)/name
        if p.is_symlink() or not p.is_file():raise ValueError('找不到報告。')
        return p
