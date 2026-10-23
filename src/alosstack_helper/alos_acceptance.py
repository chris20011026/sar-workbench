"""Read-only raster evidence for ALOS gates. Metadata SHA256 and sampled pixels
bind reports; no whole-raster scientific validity is claimed or data changed."""
import hashlib
import json
import math
import mmap
from pathlib import Path
import re
import struct
import time
import xml.etree.ElementTree as ET
from .core import atomic_json, PAIR, parse_cmd3
from .matching_quality import local_file, signature

MAX_PAIRS=10000
MAX_METADATA=2*1024**2
MAX_REPORT=32*1024**2
SAMPLE_SIDE=48
KINDS={'ion':'ion_correction_review','outputs':'output_acceptance'}
TYPES={'Float32':('f',4),'Float64':('d',8),'CFloat32':('ff',8),'CFloat64':('dd',16),'Byte':('B',1),'Int16':('h',2),'UInt16':('H',2),'Int32':('i',4),'UInt32':('I',4)}


def stamp(path,metadata=False):
    if not path.is_file():return {'missing':True}
    st=path.stat();result=dict(size=st.st_size,mtime_ns=st.st_mtime_ns)
    if metadata:
        if st.st_size>MAX_METADATA:raise ValueError('描述檔超過檢查大小上限。')
        result['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def prop(tree,name):
    nodes=[n for n in tree.findall('property') if n.get('name','').lower()==name]
    return (nodes[0].findtext('value') or nodes[0].text or '').strip() if len(nodes)==1 else None


def raster(proc,relative,phase=False,band=None,geo=False,allow_zero=False):
    path=local_file(proc,relative);names=[str(relative),str(relative)+'.xml',str(relative)+'.vrt']
    evidence={n:stamp(local_file(proc,n),i>0) for i,n in enumerate(names)}
    if any(v.get('missing') or not v.get('size') for v in evidence.values()):raise ValueError('缺少非空影像或 XML／VRT 描述檔。')
    xml=ET.parse(str(path)+'.xml').getroot();vrt=ET.parse(str(path)+'.vrt').getroot()
    if xml.tag!='imageFile' or vrt.tag!='VRTDataset':raise ValueError('不是 ISCE 影像 XML／VRT。')
    w,h=int(vrt.get('rasterXSize')),int(vrt.get('rasterYSize'))
    if not 0<w<=1000000 or not 0<h<=1000000:raise ValueError('影像尺寸超出可檢查範圍。')
    c1=next((c for c in xml.findall('component') if c.get('name','').lower()=='coordinate1'),None)
    c2=next((c for c in xml.findall('component') if c.get('name','').lower()=='coordinate2'),None)
    xsize=prop(c1,'size') if c1 is not None else prop(xml,'width');ysize=prop(c2,'size') if c2 is not None else prop(xml,'length')
    if xsize is None or ysize is None or (int(xsize),int(ysize))!=(w,h):raise ValueError('XML 與 VRT 的影像尺寸不一致。')
    bands=vrt.findall('VRTRasterBand');nb=prop(xml,'number_bands') or prop(xml,'bands')
    if not bands or (nb is not None and int(nb)!=len(bands)):raise ValueError('XML 與 VRT 的波段數不一致。')
    layouts=[];end=0
    for b in bands:
        if b.get('subClass')!='VRTRawRasterBand' or b.get('dataType') not in TYPES:raise ValueError('僅支援專案內 ISCE 原始像素 VRT。')
        source=b.find('SourceFilename')
        if source is None or not source.text:raise ValueError('VRT 未指定像素來源。')
        target=Path(source.text)
        if source.get('relativeToVRT')=='1':target=path.parent/target
        if target.resolve()!=path.resolve():raise ValueError('VRT 指向不同影像，已停止讀取。')
        fmt,size=TYPES[b.get('dataType')];offset,pixel,line=[int(b.findtext(n)) for n in ('ImageOffset','PixelOffset','LineOffset')]
        if offset<0 or pixel<size or line<w*pixel:raise ValueError('VRT 像素位移或列間距無效。')
        order=b.findtext('ByteOrder','LSB')
        if order not in ('LSB','MSB'):raise ValueError('無效位元組順序。')
        layouts.append((offset,pixel,line,('<' if order=='LSB' else '>')+fmt,size));end=max(end,offset+(h-1)*line+(w-1)*pixel+size)
    if end!=path.stat().st_size:raise ValueError('像素檔案大小與 VRT 不符，可能未寫完。')
    grid=None
    if geo:
        text=vrt.findtext('GeoTransform')
        if text:grid=[float(v) for v in text.split(',')]
        elif c1 is not None and c2 is not None:
            vals=[prop(c1,'startingvalue'),prop(c1,'delta'),prop(c2,'startingvalue'),prop(c2,'delta')]
            if all(v is not None for v in vals):
                x,dx,y,dy=map(float,vals);grid=[x-dx/2,dx,0.,y-dy/2,0.,dy]
        if grid is None or len(grid)!=6 or any(not math.isfinite(v) for v in grid) or grid[1]*grid[5]-grid[2]*grid[4]==0:raise ValueError('缺少有效的地理網格資訊。')
    selected=len(bands)-1 if band is None else band
    if not 0<=selected<len(bands):raise ValueError('影像缺少預期波段。')
    if phase and bands[selected].get('dataType') not in ('CFloat32','CFloat64'):raise ValueError('校正前後應為複數干涉圖。')
    offset,pixel,line,fmt,size=layouts[selected]
    ys=sorted({round(i*(h-1)/max(1,min(h,SAMPLE_SIDE)-1)) for i in range(min(h,SAMPLE_SIDE))});xs=sorted({round(i*(w-1)/max(1,min(w,SAMPLE_SIDE)-1)) for i in range(min(w,SAMPLE_SIDE))})
    values=[];finite=0;nonzero=0;sha=hashlib.sha256()
    with path.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as raw:
        for y in ys:
            for x in xs:
                pos=offset+y*line+x*pixel;chunk=raw[pos:pos+size];sha.update(chunk);parts=struct.unpack(fmt,chunk)
                ok=all(math.isfinite(v) for v in parts);finite+=ok;nz=ok and any(v!=0 for v in parts);nonzero+=nz
                value=math.atan2(parts[1],parts[0]) if len(parts)==2 and phase and nz else (abs(complex(*parts)) if len(parts)==2 else parts[0])
                values.append(float(value) if ok and (allow_zero or nz) else None)
    for i,name in enumerate(names):
        if stamp(local_file(proc,name),i>0)!=evidence[name]:raise ValueError('檢查期間影像或描述檔改變，請重新檢查。')
    evidence[names[0]]['sample_sha256']=sha.hexdigest();good=[v for v in values if v is not None]
    preview=dict(width=len(xs),height=len(ys),values=values,min=min(good) if good else None,max=max(good) if good else None,unit='rad' if phase else None)
    return dict(path=str(relative),width=w,height=h,bands=len(bands),grid=grid,samples=len(values),finite_fraction=finite/len(values),nonzero_fraction=nonzero/len(values),preview=preview),evidence


def binding(state):
    return signature(dict(config=state['config'],hashes=state.get('hashes',{}),pairs=state.get('expected_pairs',[]),ion=state.get('ion',{}),excluded=state.get('excluded',[])))


def read_one(proc,relative,evidence,**kwargs):
    result,files=raster(proc,relative,**kwargs);evidence.update(files);return result


def expected_pairs(state):
    pairs=state.get('expected_pairs',[])
    if not pairs or len(pairs)>MAX_PAIRS or len(set(pairs))!=len(pairs) or any(not PAIR.fullmatch(p) for p in pairs):raise ValueError('預期配對清單缺失、重複或超過 10000 組上限。')
    return pairs


def collect(state,proc,kind):
    if kind not in KINDS:raise ValueError('未知的檢查種類。')
    pairs=expected_pairs(state);before=binding(state);rows=[];errors=[];warnings=[];evidence={}
    if kind=='ion':
        opts,_=parse_cmd3((proc/'cmd_3.sh').read_text());nr=int(opts['-nrlks1'])*int(opts['-nrlks2']);na=int(opts['-nalks1'])*int(opts['-nalks2']);suffix=f'_{nr}rlks_{na}alks'
        for pair in pairs:
            row=dict(pair=pair,errors=[],products={})
            try:
                folder=Path('pairs')/pair/'insar'
                for key,name in [('before',f'diff_{pair}{suffix}_ori.int'),('after',f'diff_{pair}{suffix}.int')]:row['products'][key]=read_one(proc,folder/name,evidence,phase=True)
                a,b=pair.split('-');ions=[read_one(proc,f'dates_ion/filt_ion_{d}{suffix}.ion',evidence,allow_zero=True) for d in (a,b)]
                items=[*row['products'].values(),*ions]
                if len({(r['width'],r['height']) for r in items})!=1:raise ValueError('校正前後與日期電離層影像尺寸不同。')
                diff=[u-v if u is not None and v is not None else None for u,v in zip(ions[0]['preview']['values'],ions[1]['preview']['values'])];valid=[v for v in diff if v is not None]
                row['correction']=dict(width=ions[0]['preview']['width'],height=ions[0]['preview']['height'],values=diff,min=min(valid) if valid else None,max=max(valid) if valid else None,unit='rad')
                bv=row['products']['before']['preview']['values'];av=row['products']['after']['preview']['values']
                residual=[abs(math.atan2(math.sin(y-x+d),math.cos(y-x+d))) for x,y,d in zip(bv,av,diff) if x is not None and y is not None and d is not None];row['phase_relation_max_rad']=max(residual) if residual else None
                if not residual:raise ValueError('抽樣位置沒有可核對的校正前後有效相位。')
                if max(residual)>0.01:raise ValueError('抽樣相位不符合原始相位減去日期電離層差值（容許 0.01 rad）。')
                if any(v['finite_fraction']<1 for v in items):raise ValueError('抽樣位置出現非有限像素，請檢查完整性。')
            except (ValueError,OSError,ET.ParseError,TypeError,KeyError,OverflowError) as exc:row['errors'].append(str(exc))
            if rows:
                for value in row['products'].values():value['preview'].pop('values',None)
                if row.get('correction'):row['correction'].pop('values',None)
            rows.append(row)
        warnings.append('並排圖是相同雷達網格的規則抽樣；相位核對只能檢查校正是否套用，不能證明電離層估計正確。')
    else:
        looks=set();grids=[]
        for pair in pairs:
            row=dict(pair=pair,errors=[],products={});folder=Path('pairs')/pair/'insar'
            try:
                candidates=list(local_file(proc,folder).glob(f'filt_{pair}_*rlks_*alks.unw.geo'))
                if len(candidates)!=1:raise ValueError('找不到唯一的未遮罩地理編碼解纏成果。')
                match=re.fullmatch(r'filt_'+re.escape(pair)+r'(_\d+rlks_\d+alks)\.unw\.geo',candidates[0].name)
                if not match:raise ValueError('無法辨識成果的 looks。')
                suffix=match[1];looks.add(suffix)
                for key,name in [('unwrapped',f'filt_{pair}{suffix}.unw.geo'),('masked',f'filt_{pair}{suffix}_msk.unw.geo'),('coherence',f'{pair}{suffix}.cor.geo')]:
                    product=read_one(proc,folder/name,evidence,geo=True,band=1 if key!='coherence' else None);row['products'][key]=product;grids.append((product['width'],product['height'],product['grid']))
                    if product['finite_fraction']<1:raise ValueError('抽樣位置含 NaN／Inf，請檢查成果。')
                    if product['nonzero_fraction']==0:row.setdefault('warnings',[]).append(key+' 抽樣位置皆為 0；可能沒有有效覆蓋，請檢查全解析度影像。')
                    if key=='coherence' and any(v is not None and not 0<=v<=1.0001 for v in product['preview']['values']):raise ValueError('相干性抽樣值超出 0–1 範圍。')
            except (ValueError,OSError,ET.ParseError,TypeError,KeyError,OverflowError) as exc:row['errors'].append(str(exc))
            if rows:
                for value in row['products'].values():value['preview'].pop('values',None)
                if row.get('correction'):row['correction'].pop('values',None)
            rows.append(row)
        geometry=None
        try:
            if len(looks)!=1:raise ValueError('各配對 looks 缺失或不一致。')
            ref=state['config'].get('reference','')
            if not re.fullmatch(r'\d{6}',ref):raise ValueError('缺少參考日期。')
            name=f'dates_resampled/{ref}/insar/{ref}{next(iter(looks))}.los.geo'
            geometry=read_one(proc,name,evidence,geo=True,band=0)
            if geometry['bands']!=2:raise ValueError('LOS 幾何應包含入射角與方位角兩個波段。')
            azimuth=read_one(proc,name,evidence,geo=True,band=1)
            geometry['azimuth_finite_fraction']=azimuth['finite_fraction']
            if geometry['finite_fraction']<1 or azimuth['finite_fraction']<1:raise ValueError('LOS 幾何角度的抽樣位置含 NaN／Inf。')
            grids.append((geometry['width'],geometry['height'],geometry['grid']))
        except (ValueError,OSError,ET.ParseError,TypeError,KeyError,OverflowError) as exc:errors.append('LOS 幾何：'+str(exc))
        if grids and any(g[:2]!=grids[0][:2] or any(abs(a-b)>1e-8 for a,b in zip(g[2],grids[0][2])) for g in grids[1:]):errors.append('地理編碼成果的尺寸或網格不一致。')
        warnings+=['.los.geo 是入射角／方位角幾何，不是 LOS 位移。解纏相位以 rad 顯示，尚未轉為位移。','灰色表示抽樣零值或無資料，抽樣比例不等於完整覆蓋率。']
    warnings.append('清單檢查所有配對；僅第一組的預覽隨報告保存，其他配對按需讀取。')
    warnings.append('原始像素不修改。描述檔完整 SHA256；大型像素檔以大小、修改時間與抽樣 SHA256 綁定，未進行全像素科學驗證。')
    report=dict(schema=1,kind=kind,rows=rows,errors=errors,warnings=warnings,_binding=before,_files=evidence)
    if kind=='outputs':report['geometry']=geometry
    report['summary']=dict(expected_pairs=len(pairs),readable_pairs=sum(not r['errors'] for r in rows),execution_complete=kind=='outputs');report['can_continue']=not errors and all(not r['errors'] for r in rows)
    if binding(state)!=before:raise ValueError('檢查期間專案設定改變。')
    for name,value in evidence.items():
        expected={k:v for k,v in value.items() if k!='sample_sha256'}
        if stamp(local_file(proc,name),name.endswith(('.xml','.vrt')))!=expected:raise ValueError('彙整期間成果已改變，請稍後重新檢查。')
    report['report_id']=signature(report)
    if len(json.dumps(report).encode())>MAX_REPORT:raise ValueError('成果報告超過上限，請減少本次專案配對數。')
    return report


def publish(state,proc,kind):
    report=collect(state,proc,kind);key=KINDS[kind];atomic_json(local_file(proc,'helper_'+key+'.json'),report);state[key]=report;state.pop(key+'_approval',None);local_file(proc,'helper_'+key+'_approval.json').unlink(missing_ok=True);return report


def current(state,proc,kind):
    key=KINDS[kind];report=state.get(key);path=local_file(proc,'helper_'+key+'.json')
    if not isinstance(report,dict) or not path.is_file() or path.stat().st_size>MAX_REPORT or json.loads(path.read_text())!=report:raise ValueError('檢查報告遺失或已變更，請更新檢查。')
    if report.get('report_id')!=signature({k:v for k,v in report.items() if k!='report_id'}) or binding(state)!=report['_binding']:raise ValueError('報告或處理設定已變更，請更新檢查。')
    fresh=collect(state,proc,kind)
    if fresh['report_id']!=report['report_id']:raise ValueError('成果或抽樣證據已變更，請更新後重新確認。')
    return report


def approve(state,proc,kind,options):
    report=current(state,proc,kind);notes=options.get('notes','')
    if options.get('report_id')!=report['report_id']:raise ValueError('請確認目前這份檢查報告。')
    if not report['can_continue']:raise ValueError('成果結構或校正證據仍有缺失，請先處理報告中的問題。')
    if options.get('reviewed') is not True or not isinstance(notes,str) or not 3<=len(notes.strip())<=2000:raise ValueError('請勾選確認並填寫至少 3 字的人工判讀紀錄。')
    key=KINDS[kind];approval=dict(report_id=report['report_id'],reviewed=True,notes=notes.strip(),time=time.strftime('%Y-%m-%d %H:%M:%S'));atomic_json(local_file(proc,'helper_'+key+'_approval.json'),approval);state[key+'_approval']=approval;return approval


def verified_approval(state,proc,kind):
    report=current(state,proc,kind);key=KINDS[kind];approval=state.get(key+'_approval');path=local_file(proc,'helper_'+key+'_approval.json')
    if not approval or not path.is_file() or path.stat().st_size>10000 or json.loads(path.read_text())!=approval or approval.get('reviewed') is not True or approval.get('report_id')!=report['report_id'] or not report['can_continue']:raise ValueError('缺少與目前成果相符的人工確認紀錄。')
    return approval


def preview_pair(state,proc,kind,pair,report_id):
    key=KINDS.get(kind);report=state.get(key) if key else None
    path=local_file(proc,'helper_'+key+'.json') if key else None
    if (not report or report.get('report_id')!=report_id or not path.is_file() or path.stat().st_size>MAX_REPORT
            or json.loads(path.read_text())!=report or report_id!=signature({k:v for k,v in report.items() if k!='report_id'})
            or binding(state)!=report['_binding']):raise ValueError('成果報告已改變，請更新後再載入預覽。')
    if pair not in state.get('expected_pairs',[]):raise ValueError('配對不在目前成果清單。')
    single=collect({**state,'expected_pairs':[pair]},proc,kind)
    dates=pair.split('-')
    prefixes=['pairs/'+pair+'/']+(['dates_ion/filt_ion_'+d+'_' for d in dates] if kind=='ion' else ['dates_resampled/'])
    expected={n:v for n,v in report['_files'].items() if any(n.startswith(prefix) for prefix in prefixes)}
    if single['_files']!=expected:raise ValueError('影像或描述檔已改變，請更新成果檢查。')
    return dict(report_id=report_id,pair=pair,row=single['rows'][0])
