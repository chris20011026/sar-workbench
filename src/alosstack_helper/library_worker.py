"""Read-only product inspection and bounded GUNW previews in a science worker."""
from pathlib import Path
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

LAYERS = {
 'unwrappedPhase': ('解纏相位', 'rad', 'phase'),
 'wrappedInterferogram': ('纏繞相位', 'rad', 'wrapped'),
 'coherenceMagnitude': ('相干性', '0–1', 'coherence'),
 'connectedComponents': ('解纏連通區', '區域編號', 'category'),
 'ionospherePhaseScreen': ('電離層相位屏', 'rad', 'phase'),
 'ionospherePhaseScreenUncertainty': ('電離層相位屏不確定度', 'rad', 'positive'),
}


def scalar(v):
    if isinstance(v, bytes): return v.decode('utf-8', errors='replace')
    if hasattr(v, 'tolist'): return scalar(v.tolist())
    if isinstance(v, list): return [scalar(x) for x in v]
    return v


def stamp(path):
    s=Path(path).stat();return dict(size=s.st_size,mtime_ns=s.st_mtime_ns)


def gunw_layers(h):
    result=[];base=h.get('science/LSAR/GUNW/grids')
    if base is None:return result
    for frequency in ('frequencyA','frequencyB'):
        for group in ('unwrappedInterferogram','wrappedInterferogram'):
            for pol in ('HH','HV','VH','VV'):
                folder=base.get(f'{frequency}/{group}/{pol}')
                if folder is None:continue
                for key,(label,unit,kind) in LAYERS.items():
                    if key not in folder or len(folder[key].shape)!=2:continue
                    ds=folder[key]
                    suffix='（纏繞網格）' if key=='coherenceMagnitude' and group=='wrappedInterferogram' else '（解纏網格）' if key=='coherenceMagnitude' else ''
                    result.append(dict(key=ds.name,label=f'{frequency[-1]} · {pol} · {label}{suffix}',unit=unit,kind=kind,shape=list(ds.shape)))
    return result


def nisar(path):
    import h5py
    before=stamp(path)
    with h5py.File(path,'r') as h:
        g=h.get('science/LSAR/identification')
        if g is None:raise ValueError('不是支援的 NISAR L-band HDF5 產品')
        fields=['productType','orbitPassDirection','trackNumber','frameNumber','listOfFrequencies','zeroDopplerStartTime','referenceZeroDopplerStartTime','secondaryZeroDopplerStartTime']
        meta={k:scalar(g[k][()]) for k in fields if k in g};product=meta.get('productType')
        if product not in ('RSLC','GUNW'):raise ValueError('目前僅辨識 NISAR RSLC／GUNW')
        meta['polarizations']={}
        for f in ('A','B'):
            key=f'science/LSAR/{product}/'+('swaths' if product=='RSLC' else 'grids')+'/frequency'+f+'/listOfPolarizations'
            if key in h:meta['polarizations'][f]=scalar(h[key][()])
        result=dict(sensor='NISAR',path=str(path),type=product,metadata=meta,layers=gunw_layers(h),stamp=before)
    if stamp(path)!=before:raise ValueError('檔案正在變動，請待複製或處理完成後再讀取')
    return result


def alos(path):
    # Separate from existing ALOS-2 importer: this does not certify processing compatibility.
    images=sorted(path.parent.glob('IMG-??-'+path.name[4:]))
    out=dict(sensor='ALOS-4' if path.name.startswith('LED-ALOS4') else 'ALOS-2',type='CEOS',path=str(path),images=len(images),stamp=stamp(path),polarizations=sorted({p.name[4:6] for p in images}),metadata={},warnings=[])
    provenance = path.parent/'conversion.json'
    if provenance.is_file() and provenance.stat().st_size < 1024*1024:
        try:
            converted = json.loads(provenance.read_text())
            if converted.get('source_sensor') == 'ALOS-4' and converted.get('output_format') == 'PALSAR-2 CEOS compatibility':
                out.update(sensor='ALOS-4', converted=True)
                out['warnings'].append('轉換紀錄顯示來源為 ALOS-4；ALOS2 檔名是相容格式，frame 編號不代表跨衛星對應。')
        except (OSError, ValueError): pass
    try:
        import isce
        try:
            from .metadata_worker import record
        except ImportError:
            from metadata_worker import record
        with path.open('rb') as stream:
            record(stream,'leader_file.xml');scene,_=record(stream,'scene_record.xml')
        for key,source in [('acquisition','Scene centre time'),('range_sampling_mhz','Range sampling rate in MHz'),('direction','Time direction indicator along line direction'),('level','Product level code'),('sensor_mode','Sensor ID and mode of operation for this channel')]:out['metadata'][key]=scalar(scene.get(source))
        acquisition=str(out['metadata'].get('acquisition','')).strip()
        out['date']=acquisition[:8] if re.match(r'^\d{8}',acquisition) else None
        if not str(out['metadata'].get('direction','')).strip():out['warnings'].append('此 reader 未取得升降軌欄位，不能由資料夾名稱推定可配對。')
        if str(out['metadata'].get('level','')).strip()!='1.1':out['warnings'].append('不是已確認的 Level 1.1')
    except Exception as e:out['warnings'].append('LED 中繼資料尚未確認：'+str(e))
    if not images:out['warnings'].append('缺少同場景 IMG 檔')
    if stamp(path)!=out['stamp']:raise ValueError('LED 檔正在變動，請稍後重試')
    out['warnings'].append('僅檢查 LED 與 IMG 檔名；尚未驗證影像完整性、共同覆蓋或可干涉性。')
    return out


def config_report(path):
    if path.stat().st_size>2*1024*1024:raise ValueError('設定檔過大')
    if path.suffix in ('.yaml','.yml'):
        import yaml
        doc=yaml.safe_load(path.read_text())
        groups=doc['runconfig']['groups'];props={}
        for name in ('input_file_group','dynamic_ancillary_file_group'):
            for k,v in groups.get(name,{}).items():
                if k in ('reference_rslc_file','secondary_rslc_file','dem_file','water_mask_file') and v:props[k]=str(v)
        warnings=[k+'：路徑不存在，需重新選取' for k,v in props.items() if not (Path(v) if Path(v).is_absolute() else path.parent/v).exists()]
        return dict(path=str(path),properties=props,warnings=warnings)
    props={e.get('name'):''.join(e.itertext()).strip() for e in ET.parse(path).iter('property')};warnings=[]
    for k in ('data directory','reference directory','secondary directory','dem for coregistration','dem for geocoding','water body'):
        if props.get(k):
            p=Path(props[k]).expanduser()
            if not p.is_absolute():p=path.parent/p
            if not p.exists():warnings.append(k+'：路徑不存在，需重新選取')
    ref=props.get('reference date of the stack');local=path.parent.parent/'data'
    if ref and local.is_dir() and not (local/ref).is_dir():warnings.append('參考日期 '+ref+' 不在旁邊的 data 資料夾')
    if re.search(r"['\"]\s*\n\s*['\"]",props.get('geocode file list','')):warnings.append('geocode file list 可能少逗號，兩個檔名會被接在一起')
    return dict(path=str(path),properties=props,warnings=warnings)


def scan(path):
    root=Path(path).expanduser().resolve()
    if not root.exists():raise ValueError('找不到選取的檔案或資料夾')
    files=[];count=0
    if root.is_file():
        if root.suffix.lower()!='.h5' and not root.name.startswith(('LED-ALOS2','LED-ALOS4')) and root.name not in ('alos2App.xml','alosStack.xml','insar.yaml'):raise ValueError('請選資料夾、NISAR .h5、ALOS LED 或支援的設定檔')
        files=[root]
    else:
        for directory,dirs,names in os.walk(root,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and d!='__pycache__' and not Path(directory,d).is_symlink())
            for name in sorted(names):
                count+=1
                if count>20000:raise ValueError('超過 20,000 個檔案，請選較小的資料夾')
                p=Path(directory,name)
                if not p.is_symlink() and (p.suffix.lower()=='.h5' or p.name.startswith(('LED-ALOS2','LED-ALOS4')) or p.name in ('alos2App.xml','alosStack.xml','insar.yaml')):files.append(p)
                if len(files)>256:raise ValueError('產品過多，請分批選取（最多 256 個候選檔）')
    products=[];errors=[];configs=[]
    import contextlib,io
    for p in files:
        if p.suffix in ('.xml','.yaml','.yml'):continue
        try:
            with contextlib.redirect_stdout(io.StringIO()):item=nisar(p) if p.suffix.lower()=='.h5' else alos(p)
            products.append(item)
        except Exception as e:errors.append(dict(path=str(p),message=str(e)))
    for p in files:
        if p.suffix in ('.xml','.yaml','.yml'):
            try:configs.append(config_report(p))
            except Exception as e:errors.append(dict(path=str(p),message=str(e)))
    warnings=[]
    if any(p['sensor']=='ALOS-4' for p in products):warnings.append('已辨識 ALOS-4；跨衛星處理尚未開放，不會自動套用 ALOS-2 流程或覆寫 reader。')
    groups={}
    for p in products:
        if p['type']=='CEOS':
            folder=str(Path(p['path']).parent);groups[folder]=groups.get(folder,0)+1
    if len(set(groups.values()))>1:warnings.append('不同資料夾的 LED 數量不一致；需確認影格與覆蓋對應，不能直接套用取樣統一腳本。')
    if not products:warnings.append('未找到可辨識的 NISAR HDF5 或 ALOS LED；目前不展開 ZIP。')
    return dict(root=str(root),products=products,configs=configs,errors=errors,warnings=warnings,nisar_pairs=pair_checks(products))


def pair_checks(products):
    rows=[];rslc=[(i,p) for i,p in enumerate(products) if p['type']=='RSLC']
    for n,(i,a) in enumerate(rslc):
        for j,b in rslc[n+1:]:
            if len(rows)>=100:return rows
            am=a['metadata'];bm=b['metadata'];issues=[]
            for key,label in [('trackNumber','相對軌道'),('frameNumber','影格'),('orbitPassDirection','升降軌')]:
                if am.get(key) is None or bm.get(key) is None:issues.append(label+'尚未確認')
                elif am[key]!=bm[key]:issues.append(label+'不同')
            ad=am.get('zeroDopplerStartTime');bd=bm.get('zeroDopplerStartTime')
            if not ad or not bd:issues.append('觀測日期尚未確認')
            elif ad==bd:issues.append('觀測時間相同')
            common={f:sorted(set(ps)&set(bm.get('polarizations',{}).get(f,[]))) for f,ps in am.get('polarizations',{}).items()}
            common={f:ps for f,ps in common.items() if ps}
            if not common:issues.append('沒有已確認的共同頻段／極化')
            rows.append(dict(products=[i,j],common=common,issues=issues,note='僅比較中繼資料；共同有效覆蓋、基線與實際可處理性尚待驗證。'))
    return rows


def grid(ds):
    import numpy as np
    g=ds.parent
    def field(k):
        if k in g:return g[k]
        if k in g.parent:return g.parent[k]
        raise ValueError('缺少地理定位欄位：'+k)
    x=np.asarray(field('xCoordinates')[()]);y=np.asarray(field('yCoordinates')[()])
    if len(x)!=ds.shape[1] or len(y)!=ds.shape[0] or min(len(x),len(y))<2:raise ValueError('影像尺寸與座標軸不符')
    dx=float(x[1]-x[0]);dy=float(y[1]-y[0])
    if not (dx>0 and dy<0 and np.allclose(np.diff(x),dx) and np.allclose(np.diff(y),dy)):raise ValueError('目前僅支援等距、北向上的 GUNW 網格')
    projection=field('projection');epsg=int(projection.attrs.get('epsg_code',projection[()]))
    return x,y,dx,dy,epsg


def selected(h,key):
    layers={v['key']:v for v in gunw_layers(h)}
    if key not in layers:raise ValueError('不存在或不支援的 GUNW 圖層')
    return h[key],layers[key]


def read_values(ds,kind,selection,threshold):
    import numpy as np
    a=ds[selection];good=np.isfinite(a);fill=ds.attrs.get('_FillValue')
    if fill is not None:good &= a!=fill
    if kind=='wrapped':good &= np.abs(a)>0;a=np.angle(a)
    if kind=='category':good &= a>0
    if ds.name.endswith('/unwrappedPhase') and 'connectedComponents' in ds.parent:
        cc=ds.parent['connectedComponents'];c=cc[selection];good &= (c>0)&(c!=cc.attrs.get('_FillValue',65535))
    if threshold>0:
        coh=ds.parent.get('coherenceMagnitude')
        if coh is None or coh.shape!=ds.shape:raise ValueError('此圖層沒有相同網格的相干性，無法套用門檻')
        c=coh[selection];good &= np.isfinite(c)&(c>=threshold)&(c<=1)&(c!=coh.attrs.get('_FillValue',float('nan')))
    if kind=='coherence':good &= (a>=0)&(a<=1)
    return np.where(good,a,np.nan).astype('float32')


def preview(request,output):
    import h5py
    import numpy as np
    from pyproj import Transformer
    from PIL import Image
    from matplotlib import colormaps
    threshold=float(request.get('threshold',0))
    if not math.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('相干性門檻必須介於 0 與 1')
    before=stamp(request['path'])
    if before!=request['stamp']:raise ValueError('來源檔已變動，請重新掃描')
    with h5py.File(request['path'],'r') as h:
        ds,info=selected(h,request['layer']);x,y,dx,dy,epsg=grid(ds)
        step=max(1,math.ceil(max(ds.shape)/768));a=read_values(ds,info['kind'],(slice(None,None,step),slice(None,None,step)),threshold)
        valid=a[np.isfinite(a)]
        if not valid.size:raise ValueError('此圖層在目前門檻下沒有有效預覽像素；請降低門檻或改選圖層')
        bounds=(x[0]-dx/2,y[-1]+dy/2,x[-1]+dx/2,y[0]-dy/2)
        west,south,east,north=Transformer.from_crs(epsg,4326,always_xy=True).transform_bounds(*bounds,densify_pts=21)
        if not (-180<=west<east<=180 and -85<south<north<85 and east-west<180):raise ValueError('地圖範圍不支援或跨越換日線')
        mb=Transformer.from_crs(epsg,3857,always_xy=True).transform_bounds(*bounds,densify_pts=21)
        ratio=(mb[2]-mb[0])/(mb[3]-mb[1]);w=min(900,max(1,round(900*ratio)));ht=min(900,max(1,round(900/ratio)))
        # Inverse-map Web Mercator pixel centres to the sampled source grid.
        # Clip to the ORIGINAL pixel-edge footprint, never extend edge samples.
        tx,ty=np.meshgrid(mb[0]+(np.arange(w)+.5)*(mb[2]-mb[0])/w,mb[3]-(np.arange(ht)+.5)*(mb[3]-mb[1])/ht)
        sx,sy=Transformer.from_crs(3857,epsg,always_xy=True).transform(tx,ty)
        inside=np.isfinite(sx)&np.isfinite(sy)&(sx>=bounds[0])&(sx<=bounds[2])&(sy>=bounds[1])&(sy<=bounds[3])
        cols=np.floor(np.nan_to_num((sx-x[0])/(dx*step))+.5).astype('int64')
        rows=np.floor(np.nan_to_num((sy-y[0])/(dy*step))+.5).astype('int64')
        dest=np.where(inside,a[np.clip(rows,0,a.shape[0]-1),np.clip(cols,0,a.shape[1]-1)],np.nan)
        if info['kind']=='wrapped':lo,hi=-math.pi,math.pi;cm='twilight'
        elif info['kind']=='coherence':lo,hi=0,1;cm='viridis'
        elif info['kind']=='category':lo,hi=0,19;cm='tab20'
        else:
            lo,hi=np.percentile(valid,[2,98]);cm='viridis' if info['kind']=='positive' else 'RdBu_r'
            if hi<=lo:lo-=0.5;hi+=0.5
        mapped=np.mod(dest-1,20)/19 if info['kind']=='category' else np.clip((dest-lo)/(hi-lo),0,1)
        rgba=(colormaps[cm](np.nan_to_num(mapped))*255).astype('uint8');rgba[:,:,3]=np.where(np.isfinite(dest),255,0)
        Image.fromarray(rgba).save(output)
        colors=['#%02x%02x%02x'%tuple(round(c*255) for c in colormaps[cm](float(t))[:3]) for t in np.linspace(0,1,20 if info['kind']=='category' else 9)]
        result=dict(layer=info,bounds=[[south,west],[north,east]],epsg=epsg,threshold=threshold,range=[float(lo),float(hi)],colors=colors,step=step,valid_fraction=float(valid.size/a.size),width=w,height=ht,note='抽樣預覽；透明區為無效或被篩選像素。數值為產品原始相位／品質值，未換算位移。')
    if stamp(request['path'])!=before:raise ValueError('來源檔正在變動，請稍後再讀取')
    return result


def point(request):
    import h5py
    import numpy as np
    from pyproj import Transformer
    lon=float(request['lon']);lat=float(request['lat']);threshold=float(request.get('threshold',0))
    if not all(math.isfinite(v) for v in (lon,lat,threshold)) or not (-180<=lon<=180 and -85<=lat<=85 and 0<=threshold<=1):raise ValueError('座標或門檻無效')
    if stamp(request['path'])!=request['stamp']:raise ValueError('來源檔已變動，請重新掃描')
    with h5py.File(request['path'],'r') as h:
        ds,info=selected(h,request['layer']);x,y,dx,dy,epsg=grid(ds)
        xx,yy=Transformer.from_crs(4326,epsg,always_xy=True).transform([lon],[lat]);col=math.floor((xx[0]-x[0])/dx+0.5);row=math.floor((yy[0]-y[0])/dy+0.5)
        if not (0<=row<len(y) and 0<=col<len(x)):return dict(value=None,reason='超出影像範圍')
        value=float(read_values(ds,info['kind'],(row,col),threshold));result=dict(value=value if np.isfinite(value) else None,reason='' if np.isfinite(value) else '無效或被門檻遮罩',row=row,col=col,unit=info['unit'])
    if stamp(request['path'])!=request['stamp']:raise ValueError('來源檔正在變動')
    return result


if __name__=='__main__':
    request=json.loads(Path(sys.argv[1]).read_text())
    try:
        mode=request['mode'];result=scan(request['path']) if mode=='scan' else preview(request,sys.argv[3]) if mode=='preview' else point(request) if mode=='point' else None
        if result is None:raise ValueError('不支援的操作')
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    except Exception as e:
        Path(sys.argv[2]).write_text(json.dumps({'error':str(e)},ensure_ascii=False));sys.exit(1)
