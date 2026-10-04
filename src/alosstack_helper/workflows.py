"""Workflow capabilities and pure validation shared by the API and workers."""
from pathlib import Path
import datetime
import math
import re
import xml.etree.ElementTree as ET
from .core import validate_network

WORKFLOWS = [
    dict(id='alosStack', name='ALOS-2 · 多日期', tool='alosStack', available=True, description='資料準備 → 電離層判讀 → 解纏 → MintPy'),
    dict(id='alos2App', name='ALOS-2 · 雙日期', tool='alos2App.py', available=True, description='試用：Level 1.1 Stripmap · 兩期干涉與電離層檢查'),
    dict(id='topsStack', name='Sentinel-1 TOPS · 多日期', tool='topsStack', available=False, description='規劃中：TOPS stack 與時序分析'),
    dict(id='topsApp', name='Sentinel-1 TOPS · 雙日期', tool='topsApp.py', available=False, description='規劃中：TOPS 雙日期干涉'),
    dict(id='stripmapStack', name='Stripmap · 多日期', tool='stripmapStack', available=False, description='規劃中：支援感測器的 stripmap stack'),
    dict(id='stripmapApp', name='Stripmap · 雙日期', tool='stripmapApp.py', available=False, description='規劃中：支援感測器的 stripmap 雙日期'),
]

def date8(value):
    return datetime.datetime.strptime(value, '%y%m%d').strftime('%Y%m%d')

def network(pairs):
    dates=sorted({d for p in pairs for d in p.split('-')}); adj={d:set() for d in dates}
    for p in pairs:
        a,b=p.split('-');adj[a].add(b);adj[b].add(a)
    groups=[];remaining=set(dates)
    while remaining:
        todo=[min(remaining)];seen=set()
        while todo:
            d=todo.pop()
            if d in seen:continue
            seen.add(d);todo.extend(adj[d]-seen)
        remaining-=seen;groups.append(sorted(seen))
    return dict(dates=dates,pairs=pairs,groups=groups,connected=len(groups)==1,degrees={d:len(adj[d]) for d in dates})

def raster_size(path):
    f=Path(path)
    if not f.is_file() or not f.stat().st_size:raise ValueError('缺少影像：'+str(f))
    for ext in ('.xml','.vrt'):
        if not Path(str(f)+ext).is_file():raise ValueError('缺少描述檔：'+str(f)+ext)
    v=ET.parse(str(f)+'.vrt').getroot();w=int(v.attrib['rasterXSize']);h=int(v.attrib['rasterYSize']);end=0
    widths={'Float32':4,'Float64':8,'CFloat32':8,'CFloat64':16,'Byte':1,'Int16':2,'UInt16':2,'Int32':4}
    for b in v.findall('VRTRasterBand'):
        if b.attrib.get('subClass')!='VRTRawRasterBand':raise ValueError('此版本僅接受 ISCE 原始影像 VRT。')
        source=Path(b.findtext('SourceFilename'))
        if b.find('SourceFilename').get('relativeToVRT')=='1':source=f.parent/source
        if source.resolve()!=f.resolve():raise ValueError('VRT 指向不同影像：'+str(f))
        n=int(b.findtext('ImageOffset'))+(h-1)*int(b.findtext('LineOffset'))+(w-1)*int(b.findtext('PixelOffset'))+widths[b.get('dataType')]
        end=max(end,n)
    if end!=f.stat().st_size:raise ValueError('影像大小與 VRT 不符：'+str(f))
    return w,h

def inventory(s):
    if s['config'].get('workflow','alosStack')!='alosStack':raise ValueError('MintPy 時序目前需使用已完成的 alosStack 多日期專案。')
    if s['status']!='complete':raise ValueError('請先完成 alosStack，再進行成果檢查與 MintPy。')
    p=Path(s['config']['root'])/'proc';ref=s['config']['reference'];pairs=s.get('expected_pairs',[])
    if not pairs:raise ValueError('專案缺少預期配對清單。')
    files=[];rows=[];shape=None;looks=None
    def add(f):
        nonlocal shape
        wh=raster_size(f)
        if shape is None:shape=wh
        if wh!=shape:raise ValueError('影像尺寸不一致：'+str(f))
        files.extend([str(f),str(f)+'.xml',str(f)+'.vrt']);return str(f)
    for pair in pairs:
        folder=p/'pairs'/pair/'insar'
        cand=list(folder.glob('filt_'+pair+'_*rlks_*alks.unw'))
        if len(cand)!=1:raise ValueError('無法唯一辨識解纏版本：'+pair)
        m=re.fullmatch(r'filt_'+pair+r'_(\d+)rlks_(\d+)alks.unw',cand[0].name)
        if not m:raise ValueError('不支援的解纏檔名。')
        suffix=f'_{m[1]}rlks_{m[2]}alks'
        if looks is not None and suffix!=looks:raise ValueError('配對之間 looks 不一致。')
        looks=suffix
        rows.append(dict(pair=pair,unw=add(cand[0]),masked=add(folder/f'filt_{pair}{suffix}_msk.unw'),coherence=add(folder/f'{pair}{suffix}.cor'),components=add(Path(str(cand[0])+'.conncomp'))))
    geom=p/'dates_resampled'/ref/'insar'
    geometry={ext:add(geom/f'{ref}{looks}.{ext}') for ext in ['hgt','lat','lon','los','wbd']}
    track=p/'dates_resampled'/ref/f'{ref}.track.xml';frames=sorted(track.parent.glob(f'f*_*/{ref}.frame.xml'));baseline=p/'baseline/baseline_center.txt'
    if not track.is_file() or not frames or not baseline.is_file():raise ValueError('缺少 track、frame 或 baseline metadata，不能載入 MintPy。')
    files += [str(track),str(baseline),*[str(f) for f in frames]]
    return dict(pairs=rows,geometry=geometry,track=str(track),frames=[str(f) for f in frames],baseline=str(baseline),looks=looks,width=shape[0],height=shape[1],reference=ref,files=files,network=network(pairs))

def options(data, inv):
    pairs=[p['pair'] for p in inv['pairs']];dates=inv['network']['dates'];excluded=data.get('excluded',[])
    if not isinstance(excluded,list):raise ValueError('排除配對需為清單。')
    excluded_dates=data.get('excluded_dates',[])
    if not isinstance(excluded_dates,list) or set(excluded_dates)-set(dates):raise ValueError('排除日期不在資料內。')
    start=data.get('start_date') or min(dates);end=data.get('end_date') or max(dates)
    if start not in dates or end not in dates or start>end:raise ValueError('日期範圍不合法。')
    retained=[d for d in dates if start<=d<=end and d not in excluded_dates]
    if len(retained)<3:raise ValueError('時序分析至少需保留三個日期。')
    manual=sorted(set(excluded))
    excluded=sorted(set(excluded)|{p for p in pairs if any(d not in retained for d in p.split('-'))})
    used=validate_network(pairs,excluded,retained)
    ref=data.get('reference_date',inv['reference'])
    if ref not in retained:raise ValueError('參考日期必須在保留的日期內。')
    memory=float(data.get('memory',2));threshold=float(data.get('temporal_coherence',.7));minref=float(data.get('reference_coherence',.85))
    if not .5<=memory<=32 or not 0<=threshold<=1 or not 0<=minref<=1:raise ValueError('記憶體或相干性設定超出範圍。')
    deramp=data.get('deramp','no')
    if deramp not in ['no','linear','quadratic']:raise ValueError('不支援的軌道坡度設定。')
    subset=data.get('subset') or None
    lalo=data.get('subset_lalo') or None
    if lalo:
        if len(lalo)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in lalo):raise ValueError('經緯度需填南、北、西、東四個數字。')
        a,b,c,d=lalo
        if not -85<a<b<85 or not -180<c<d<180 or d-c>180:raise ValueError('經緯度範圍不合法。')
        if subset:raise ValueError('請選擇一種裁切方式。')
    if subset:
        if len(subset)!=4 or any(type(v)!=int for v in subset):raise ValueError('範圍需為 y0,y1,x0,x1 整數。')
        y0,y1,x0,x1=subset
        if not 0<=y0<y1<=inv['height'] or not 0<=x0<x1<=inv['width']:raise ValueError('裁切範圍超出影像。')
    weight=data.get('weight','var');redundancy=float(data.get('redundancy',1));order=int(data.get('dem_order',1))
    if weight not in ('var','fim','coh','no') or not math.isfinite(redundancy) or not 1<=redundancy<=10:raise ValueError('反演加權或最低冗餘設定不合法。')
    if order not in (1,2) or (data.get('dem_error') and len(retained)<order+3):raise ValueError('DEM 誤差模型所需日期數不足。')
    name=str(data.get('name','')).strip()
    if len(name)>100:raise ValueError('分析名稱請限制在 100 字內。')
    return dict(name=name,excluded=excluded,manual_excluded=manual,excluded_dates=excluded_dates,start_date=start,end_date=end,retained_dates=retained,used=used,reference_date=ref,memory=memory,temporal_coherence=threshold,reference_coherence=minref,deramp=deramp,dem_error=bool(data.get('dem_error',False)),dem_order=order,weight=weight,redundancy=redundancy,subset=subset,subset_lalo=lalo)
