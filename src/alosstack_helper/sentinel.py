"""Bounded, read-only Sentinel-1 IW SLC SAFE inventory (no pixel processing)."""
import datetime as dt
import math
from pathlib import Path, PurePosixPath
import re
import stat
import xml.etree.ElementTree as ET
import zipfile

NAME=re.compile(r'^(S1[AB])_IW_SLC__1S[A-Z]{2}_(\d{8}T\d{6})_(\d{8}T\d{6})_\d{6}_[A-Z0-9]{6}_[A-Z0-9]{4}\.SAFE$')
MAX_XML=24*1024*1024


def xml(data):
    if len(data)>MAX_XML or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('描述檔過大或含不支援的 XML 宣告。')
    root=ET.fromstring(data)
    for e in root.iter():e.tag=e.tag.split('}')[-1]
    return root


def text(root,name):
    nodes=root.findall('.//'+name)
    if not nodes:raise ValueError('描述檔缺少 '+name)
    return (nodes[0].text or '').strip()


def utc(value):
    value=dt.datetime.fromisoformat(value.removeprefix('UTC=').replace('Z','+00:00'))
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)


def extent(points):
    if not points:raise ValueError('沒有有效的影像經緯度。')
    lat=[float(p[0]) for p in points];lon=[float(p[1]) for p in points]
    if not all(math.isfinite(x) for x in lat+lon) or min(lat)<=-85 or max(lat)>=85 or min(lon)<-180 or max(lon)>180 or max(lon)-min(lon)>180:
        raise ValueError('此版不支援極區、跨換日線或無效座標。')
    return [min(lat),max(lat),min(lon),max(lon)]


def union(boxes):
    return [min(b[0] for b in boxes),max(b[1] for b in boxes),min(b[2] for b in boxes),max(b[3] for b in boxes)]


def overlap(a,b):
    c=[max(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),min(a[3],b[3])]
    return c if c[0]<c[1] and c[2]<c[3] else None


class SafeReader:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve();self.archive=None
        try:self._load()
        except Exception:
            self.close();raise
    def _load(self):
        if self.path.suffix.lower()=='.zip':
            self.archive=zipfile.ZipFile(self.path)
            info=self.archive.infolist()
            if len(info)>10000:raise ValueError('ZIP 內容過多。')
            names=[i.filename for i in info]
            if len(names)!=len(set(names)):raise ValueError('ZIP 含重複檔名。')
            for i in info:
                p=PurePosixPath(i.filename)
                if p.is_absolute() or '..' in p.parts or '\\' in i.filename or stat.S_ISLNK(i.external_attr>>16) or i.flag_bits&1:
                    raise ValueError('ZIP 含不安全路徑、連結或加密檔案。')
            manifests=[n for n in names if n.endswith('.SAFE/manifest.safe')]
            if len(manifests)!=1 or len(PurePosixPath(manifests[0]).parts)!=2:
                raise ValueError('ZIP 必須包含一份位於最上層的完整 .SAFE。')
            self.prefix=manifests[0].removesuffix('manifest.safe');self.name=self.prefix.rstrip('/')
            self.files={n[len(self.prefix):]:self.archive.getinfo(n).file_size for n in names if n.startswith(self.prefix) and not n.endswith('/')}
        else:
            if not self.path.is_dir() or not (self.path/'manifest.safe').is_file():raise ValueError('請選完整 .SAFE 資料夾或原始 ZIP；GRD 不適用。')
            self.name=self.path.name;self.files={}
            for folder in (self.path,self.path/'annotation',self.path/'measurement'):
                if folder.is_dir():
                    for p in folder.iterdir():
                        if p.is_file():
                            if not p.resolve().is_relative_to(self.path):raise ValueError('SAFE 檔案連結超出所選資料夾。')
                            self.files[str(p.relative_to(self.path))]=p.stat().st_size
    def read(self,name,limit=MAX_XML):
        if name not in self.files:raise ValueError('SAFE 缺少 '+name)
        if self.files[name]>limit:raise ValueError('描述檔過大：'+name)
        if self.archive:return self.archive.read(self.prefix+name)
        return (self.path/name).read_bytes()
    def header(self,name):
        if self.files.get(name,0)<8:raise ValueError('影像 TIFF 缺失或為空。')
        with (self.archive.open(self.prefix+name) if self.archive else (self.path/name).open('rb')) as stream:return stream.read(4)
    def close(self):
        if self.archive:self.archive.close()


def inspect_safe(path):
    reader=SafeReader(path)
    try:
        match=NAME.fullmatch(reader.name)
        if not match:raise ValueError('第一版支援 S1A／S1B IW SLC Level-1 SAFE；GRD、EW、Stripmap 與其他衛星不可套用。')
        satellite,start,stop=match.groups();manifest=xml(reader.read('manifest.safe'))
        if text(manifest,'productType')!='SLC' or text(manifest,'mode')!='IW':raise ValueError('正式描述檔不是 IW SLC。')
        platform=manifest.find('.//platform')
        if platform is None or text(platform,'number')!=satellite[-1]:raise ValueError('檔名與平台識別不符。')
        begin=utc(text(manifest,'startTime'));end=utc(text(manifest,'stopTime'))
        if begin.strftime('%Y%m%dT%H%M%S')!=start or end.strftime('%Y%m%dT%H%M%S')!=stop:raise ValueError('檔名與影像取得時間不符。')
        relative={int((e.text or '').strip()) for e in manifest.findall('.//relativeOrbitNumber')}
        if len(relative)!=1 or not 1<=next(iter(relative))<=175:raise ValueError('無法唯一確認相對軌道。')
        direction=text(manifest,'pass').upper()
        if direction not in ('ASCENDING','DESCENDING'):raise ValueError('升降軌資訊無效。')
        channels=[]
        for name in sorted(reader.files):
            if not re.fullmatch(r'annotation/s1[ab]-iw[123]-slc-(vv|vh|hh|hv)-[^/]+\.xml',name):continue
            ann=xml(reader.read(name));header=ann.find('adsHeader')
            if header is None:raise ValueError('缺少 annotation 標頭。')
            swath=text(header,'swath');pol=text(header,'polarisation').upper()
            if text(header,'missionId')!=satellite or text(header,'productType')!='SLC' or not name.startswith(f'annotation/{satellite.lower()}-{swath.lower()}-slc-{pol.lower()}-'):
                raise ValueError('annotation 與檔名的衛星／子波束／極化不一致。')
            if abs((utc(text(header,'startTime'))-begin).total_seconds())>5 or abs((utc(text(header,'stopTime'))-end).total_seconds())>5:raise ValueError('annotation 取得時間超出 SAFE 的時間範圍。')
            image='measurement/'+Path(name).stem+'.tiff'
            if reader.header(image) not in (b'II*\x00',b'MM\x00*',b'II+\x00',b'MM\x00+'):
                raise ValueError('影像不是有效 TIFF 標頭。')
            lines=int(text(ann,'linesPerBurst'));burstlist=ann.findall('.//swathTiming/burstList/burst')
            count=int(ann.find('.//swathTiming/burstList').get('count'))
            if not 0<len(burstlist)==count<=1000 or lines<=0:raise ValueError('burst 數量或大小不符。')
            grid=[(int(text(p,'line')),float(text(p,'latitude')),float(text(p,'longitude'))) for p in ann.findall('.//geolocationGridPoint')]
            boxes=[]
            for i,b in enumerate(burstlist):
                low=i*lines;high=(i+1)*lines-1
                levels=sorted({g[0] for g in grid})
                below=max((v for v in levels if v<=low),default=low)
                above=min((v for v in levels if v>=high),default=high)
                bb=extent([(g[1],g[2]) for g in grid if below<=g[0]<=above])
                boxes.append(dict(index=i+1,time=text(b,'azimuthTime'),bounds=bb))
            channels.append(dict(swath=int(swath[-1]),polarization=pol,bursts=boxes,bounds=union([b['bounds'] for b in boxes]),annotation=name,measurement=image))
        if not channels:raise ValueError('找不到配套的 IW annotation 與影像。')
        if len({(c['swath'],c['polarization']) for c in channels})!=len(channels):raise ValueError('子波束／極化產品重複。')
        return dict(path=str(reader.path),name=reader.name,satellite=satellite,start=begin.isoformat(),stop=end.isoformat(),date=begin.strftime('%Y%m%d'),relative_orbit=next(iter(relative)),direction=direction,channels=channels,bounds=union([c['bounds'] for c in channels]),bytes=sum(reader.files.values()),
                    files=[str(reader.path)] if reader.archive else [str(reader.path/n) for n in reader.files if n=='manifest.safe' or n.startswith(('annotation/','measurement/'))],
                    note='網格與 burst 外接矩形只供選區，不代表精確共同 burst 或有效像素；正式共用 burst 由 ISCE2 幾何計算確認。')
    finally:reader.close()


def inspect_pair(reference,secondary):
    a=inspect_safe(reference);b=inspect_safe(secondary)
    if a['start']>=b['start'] or a['date']==b['date']:raise ValueError('請選不同日期，參考影像需早於比較影像。')
    if a['relative_orbit']!=b['relative_orbit'] or a['direction']!=b['direction']:raise ValueError('兩期相對軌道或升降軌不一致。')
    common=[]
    for x in a['channels']:
        for y in b['channels']:
            box=overlap(x['bounds'],y['bounds'])
            if x['swath']==y['swath'] and x['polarization']==y['polarization'] and box:
                common.append(dict(swath=x['swath'],polarization=x['polarization'],bounds=box,reference_bursts=x['bursts'],secondary_bursts=y['bursts']))
    if not common:raise ValueError('沒有相同極化與子波束的概略重疊。')
    return dict(reference=a,secondary=b,common=common,bounds=union([x['bounds'] for x in common]),days=(utc(b['start'])-utc(a['start'])).days)
