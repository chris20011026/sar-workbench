"""Public ESA POEORB retrieval, with acquisition and EOF validation."""
import datetime as dt
from html.parser import HTMLParser
import io
import math
import urllib.error
from pathlib import Path, PurePosixPath
import re
import stat
import urllib.request
import zipfile
from .sentinel import xml,text,utc

BASE='https://step.esa.int/auxdata/orbits/Sentinel-1/POEORB/'
ORBIT=re.compile(r'^(S1[AB])_OPER_AUX_POEORB_OPOD_\d{8}T\d{6}_V(\d{8}T\d{6})_(\d{8}T\d{6})\.EOF(?:\.zip)?$')


def read_url(url,limit):
    if not url.startswith(BASE):raise ValueError('僅允許 ESA 官方 POEORB 來源。')
    with urllib.request.urlopen(url,timeout=45) as response:
        if not response.url.startswith(BASE):raise ValueError('軌道下載被導向非預期來源。')
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('軌道下載超過大小限制。')
    return data


def validate_orbit(path,scene):
    path=Path(path)
    if path.stat().st_size>16*1024*1024:raise ValueError('EOF 過大。')
    root=xml(path.read_bytes())
    if text(root,'Mission').replace('-','').upper()!=scene['satellite'].replace('S1','SENTINEL1'):
        raise ValueError('軌道衛星與影像不符。')
    if text(root,'File_Type')!='AUX_POEORB':raise ValueError('第一版要求精密軌道 POEORB。')
    start=utc(scene['start'])-dt.timedelta(seconds=60);stop=utc(scene['stop'])+dt.timedelta(seconds=60)
    if utc(text(root,'Validity_Start'))>start or utc(text(root,'Validity_Stop'))<stop:raise ValueError('精密軌道有效時間未涵蓋影像與緩衝。')
    states=root.findall('.//OSV');times=[utc(text(s,'UTC')) for s in states]
    if len(times)<4 or times!=sorted(set(times)) or times[0]>start or times[-1]<stop or max((b-a).total_seconds() for a,b in zip(times,times[1:]))>20:
        raise ValueError('軌道狀態向量缺少、排序錯誤或時間涵蓋不足。')
    for state in states:
        if any(not math.isfinite(float(text(state,k))) for k in ('X','Y','Z','VX','VY','VZ')):raise ValueError('軌道位置或速度含無效數值。')
    return str(path)


class Links(HTMLParser):
    def __init__(self):super().__init__();self.names=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':
            value=dict(attrs).get('href','')
            if ORBIT.fullmatch(value):self.names.append(value)


def download_orbit(scene,directory):
    begin=utc(scene['start']);end=utc(scene['stop']);found=[]
    months={ (begin+dt.timedelta(days=i)).strftime('%Y/%m') for i in (-1,0,1)}
    for month in sorted(months):
        url=f"{BASE}{scene['satellite']}/{month}/"
        parser=Links()
        try:parser.feed(read_url(url,2*1024*1024).decode())
        except urllib.error.HTTPError as error:
            if error.code==404:continue
            raise
        for name in parser.names:
            m=ORBIT.fullmatch(name)
            if m[1]==scene['satellite'] and utc(m[2])<=begin-dt.timedelta(seconds=60) and utc(m[3])>=end+dt.timedelta(seconds=60):found.append((name,url+name))
    if not found:raise ValueError('官方站點尚無覆蓋此日期的精密軌道；可稍後重試或選擇本機 EOF。')
    name,url=sorted(found)[-1];data=read_url(url,16*1024*1024)
    if name.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries=z.infolist()
            if not 1<=len(entries)<=4 or sum(i.file_size for i in entries)>32*1024*1024:raise ValueError('軌道 ZIP 內容不符。')
            payloads=[]
            for entry in entries:
                p=PurePosixPath(entry.filename)
                if p.name!=name[:-4] or p.is_absolute() or '..' in p.parts or '\\' in entry.filename or entry.file_size>16*1024*1024 or stat.S_ISLNK(entry.external_attr>>16):raise ValueError('軌道 ZIP 內容不符。')
                payloads.append(z.read(entry))
            if any(x!=payloads[0] for x in payloads):raise ValueError('軌道 ZIP 中的多份 EOF 內容不一致。')
            data=payloads[0]
        name=name[:-4]
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    target=directory/name;temp=directory/(name+'.partial');temp.write_bytes(data)
    try:validate_orbit(temp,scene);temp.replace(target)
    finally:temp.unlink(missing_ok=True)
    return dict(path=str(target),url=url)
