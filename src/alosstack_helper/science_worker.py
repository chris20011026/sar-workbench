"""Runs only in the explicitly selected scientific Conda environment."""
import os,sys,json,shutil,time,math,importlib.metadata
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np


def save(path,data):
    p=Path(path);t=p.with_suffix('.tmp');t.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False));t.replace(p)

def step(job_path,label):
    job=json.loads(job_path.read_text());job['stage']=label;job['history'].append(dict(stage=label,time=time.strftime('%Y-%m-%d %H:%M:%S')));save(job_path,job);print('\n=== '+label+' ===',flush=True)

def read_raster(path):
    path=Path(path);v=ET.parse(str(path)+'.vrt').getroot();w=int(v.get('rasterXSize'));h=int(v.get('rasterYSize'))
    kinds={'Float32':'<f4','Float64':'<f8','Byte':'u1','CFloat32':'<c8','Int16':'<i2','UInt16':'<u2'};arrays=[]
    for b in v.findall('VRTRasterBand'):
        raw=np.memmap(path,mode='r',dtype='u1');a=np.ndarray((h,w),dtype=kinds[b.get('dataType')],buffer=raw,offset=int(b.findtext('ImageOffset')),strides=(int(b.findtext('LineOffset')),int(b.findtext('PixelOffset'))))
        arrays.append(a)
    return arrays

def preview(a,mask,out,name,limits=None,cmap='viridis'):
    from PIL import Image
    from matplotlib import colormaps
    h,w=a.shape;stride=max(1,math.ceil(max(h,w)/650));a=np.array(a[::stride,::stride]);valid=mask[::stride,::stride]&np.isfinite(a)
    values=a[valid]
    lo,hi=limits or (np.percentile(values,[2,98]).tolist() if len(values) else [0,1])
    if hi<=lo:hi=lo+1
    norm=np.clip(np.nan_to_num((a-lo)/(hi-lo)),0,1)
    rgba=(colormaps[cmap](norm)*255).astype('uint8');rgba[~valid]=[224,230,223,255]
    Image.fromarray(rgba).save(out/name)
    return dict(file=name,min=float(lo),max=float(hi),width=w,height=h,preview_width=a.shape[1],preview_height=a.shape[0],stride=stride)

def versions():
    import platform
    d={'python':sys.version.split()[0],'architecture':platform.machine(),'prefix':sys.prefix}
    for name in ('mintpy','numpy','scipy','h5py','GDAL'):
        try:d[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:d[name]='unknown'
    return d

def quality(job_path):
    job=json.loads(job_path.read_text());inv=job['inventory'];out=job_path.parent/'exports';out.mkdir(exist_ok=True)
    step(job_path,'檢查影像數值、陸地覆蓋與配對網路')
    wbd=read_raster(inv['geometry']['wbd'])[0];land=wbd==0;nland=int(land.sum())
    if not nland:raise ValueError('水體遮罩沒有陸地像素，無法計算陸地品質。')
    allmask=land.copy();coverage=np.zeros(land.shape,dtype='uint16');rows=[];issues=[]
    for row in inv['pairs']:
        pair=row['pair'];coh=read_raster(row['coherence'])[-1];phase=read_raster(row['masked'])[-1];cc=read_raster(row['components'])[0]
        finite=bool(np.isfinite(coh).all() and np.isfinite(phase).all())
        if not finite:issues.append(pair+' 含非有限數值')
        outside=np.isfinite(coh)&((coh < -1e-5)|(coh > 1.00001))
        if outside.any():issues.append(f'{pair}：{int(outside.sum())} 個相干性像素超出 0–1，其中 {int((outside&land).sum())} 個在陸地；來源保持不變。')
        valid=land&(phase!=0)&np.isfinite(phase)&(cc>0);allmask&=valid;coverage+=valid
        vals=coh[land&np.isfinite(coh)]
        retained=100*float(valid.sum())/nland
        stats=dict(pair=pair,retained=retained,coherence_median=float(np.median(vals)) if vals.size else None,finite=finite,components=int(np.unique(cc[cc>0]).size))
        stats['coherence_image']=preview(coh,land,out,pair+'-coherence.png',[0,1])
        stats['phase_image']=preview(phase,valid,out,pair+'-phase.png',cmap='turbo')
        rows.append(stats);print(pair,round(retained,2),'% retained',flush=True)
    common=preview(coverage.astype('f4'),land,out,'coverage.png',[0,len(rows)])
    report=dict(rows=rows,network=inv['network'],issues=issues,land_pixels=nland,common_retained_percent=float(100*allmask.sum()/nland),coverage_image=common,versions=versions(),ion_excluded=job.get('source_ion_excluded',[]),meaning='保留率＝陸地上非零遮罩解纏、且連通區有效的比例，不是準確率。各相位預覽獨立使用 2–98 百分位色階。')
    save(job_path.parent/'quality.json',report)
    import csv
    with (out/'quality.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['pair','retained','coherence_median','finite','components']);writer.writeheader();writer.writerows({k:r[k] for k in writer.fieldnames} for r in rows)
    save(out/'quality.json',report)

def stage_raster(source,dest):
    source=Path(source);dest=Path(dest);dest.parent.mkdir(parents=True,exist_ok=True);dest.symlink_to(source.resolve())
    v=ET.parse(str(source)+'.vrt')
    for node in v.findall('.//SourceFilename'):node.text=dest.name;node.set('relativeToVRT','1')
    v.write(str(dest)+'.vrt',encoding='utf-8',xml_declaration=True)
    v=ET.parse(str(source)+'.xml')
    for node in v.findall('.//property'):
        if node.get('name','').lower() in ('file_name','filename'):
            if node.find('value') is not None:node.find('value').text=str(dest)
            else:node.text=str(dest)
    v.write(str(dest)+'.xml',encoding='utf-8',xml_declaration=True)

def stage_input(job_path):
    job=json.loads(job_path.read_text());inv=job['inventory'];p=job_path.parent;src=p/'source';src.mkdir(exist_ok=False)
    for row in inv['pairs']:
        for key in ('unw','coherence','components'):
            f=Path(row[key]);stage_raster(f,src/'pairs'/row['pair']/f.name)
    for f in inv['geometry'].values():stage_raster(f,src/'geometry'/Path(f).name)
    metadata=src/'metadata';metadata.mkdir()
    shutil.copy2(inv['track'],metadata/Path(inv['track']).name)
    for f in inv['frames']:
        f=Path(f);target=metadata/f.parent.name;target.mkdir(exist_ok=True);shutil.copy2(f,target/f.name)
    (src/'baseline').mkdir();shutil.copy2(inv['baseline'],src/'baseline/baseline_center.txt')
    # All .rsc writes performed by MintPy are confined to this copied metadata tree.
    from mintpy.cli.prep_isce import main
    main(['-f',str(src/'pairs/*/filt_*.unw'),str(src/'pairs/*/*.cor'),str(src/'pairs/*/*.conncomp'),'-m',str(metadata/Path(inv['track']).name),'-b',str(src/'baseline'),'-g',str(src/'geometry'),'--geom-files',*[Path(f).name for f in inv['geometry'].values()]])
    return src

def make_template(job_path,src):
    job=json.loads(job_path.read_text());o=job['options'];inv=job['inventory'];p=job_path.parent
    # Explicitly disable optional corrections requiring services or scientific assumptions.
    config={'mintpy.load.processor':'isce','mintpy.load.autoPath':'no','mintpy.load.metaFile':str(src/'metadata'/Path(inv['track']).name),'mintpy.load.baselineDir':str(src/'baseline'),'mintpy.load.unwFile':str(src/'pairs/*/filt_*.unw'),'mintpy.load.corFile':str(src/'pairs/*/*.cor'),'mintpy.load.connCompFile':str(src/'pairs/*/*.conncomp'),'mintpy.load.compression':'lzf','mintpy.load.ionUnwFile':'auto','mintpy.load.ionCorFile':'auto','mintpy.network.excludeDate12':','.join(date8(a)+'_'+date8(b) for a,b in (v.split('-') for v in o['excluded'])) or 'no','mintpy.network.coherenceBased':'no','mintpy.network.areaRatioBased':'no','mintpy.reference.minCoherence':o['reference_coherence'],'mintpy.reference.date':date8(o['reference_date']),'mintpy.compute.maxMemory':o['memory'],'mintpy.compute.cluster':'no','mintpy.plot':'no','mintpy.troposphericDelay.method':'no','mintpy.ionosphericDelay.method':'no','mintpy.solidEarthTides':'no','mintpy.deramp':o['deramp'],'mintpy.topographicResidual':'yes' if o['dem_error'] else 'no','mintpy.topographicResidual.polyOrder':1,'mintpy.networkInversion.maskDataset':'connectComponent','mintpy.networkInversion.minTempCoh':o['temporal_coherence'],'mintpy.networkInversion.minNumPixel':100,'mintpy.unwrapError.method':'no','mintpy.geocode':'no','mintpy.save.kmz':'no','mintpy.save.hdfEos5':'no'}
    config.update({'mintpy.networkInversion.weightFunc':o.get('weight','var'),'mintpy.networkInversion.minRedundancy':o.get('redundancy',1),'mintpy.topographicResidual.polyOrder':o.get('dem_order',1),'mintpy.timeFunc.polynomial':1,'mintpy.timeFunc.uncertaintyQuantification':'residue'})
    for key,ext in [('demFile','hgt'),('lookupYFile','lat'),('lookupXFile','lon'),('incAngleFile','los'),('azAngleFile','los'),('waterMaskFile','wbd')]:config['mintpy.load.'+key]=str(src/'geometry'/Path(inv['geometry'][ext]).name)
    if o['subset']:
        y0,y1,x0,x1=o['subset'];config['mintpy.subset.yx']=f'{y0}:{y1},{x0}:{x1}'
    (p/'mintpy.cfg').write_text('\n'.join(f'{k} = {v}' for k,v in config.items())+'\n')

def date8(s):
    import datetime
    return datetime.datetime.strptime(s,'%y%m%d').strftime('%Y%m%d')

def mintpy_steps(job_path,names):
    from mintpy.cli.smallbaselineApp import main
    p=job_path.parent;os.chdir(p)
    for name in names:
        step(job_path,'MintPy · '+name);main([str(p/'mintpy.cfg'),'--dostep',name])

def load(job_path):
    import h5py
    step(job_path,'建立独立 metadata 與影像連結');src=stage_input(job_path);make_template(job_path,src)
    mintpy_steps(job_path,['load_data','modify_network'])
    p=job_path.parent;out=p/'exports';out.mkdir(exist_ok=True)
    with h5py.File(p/'inputs/ifgramStack.h5','r') as f:
        dates=[[v.decode() for v in row] for row in f['date'][:]];keep=f['dropIfgram'][:].astype(bool);shape=f['unwrapPhase'].shape[1:];avg=np.zeros(shape);common=np.ones(shape,dtype=bool)
        for i in np.flatnonzero(keep):
            avg+=f['coherence'][i];common&=(f['connectComponent'][i]>0)&(f['unwrapPhase'][i]!=0)
        avg/=int(keep.sum())
    with h5py.File(p/'inputs/geometryRadar.h5','r') as f:
        land=f['waterMask'][:].astype(bool);common&=land
    usable=common&np.isfinite(avg);job=json.loads(job_path.read_text());mincoh=job['options']['reference_coherence']
    if usable.any():
        index=np.argmax(np.where(usable,avg,-1));y,x=np.unravel_index(index,avg.shape);candidate=dict(y=int(y),x=int(x),coherence=float(avg[y,x]))
    else:candidate=None
    save(p/'loaded.json',dict(width=shape[1],height=shape[0],dates=dates,kept=keep.tolist(),common_land_percent=100*float(common.sum()/max(1,land.sum())),candidate=candidate,reference_threshold=mincoh,preview=preview(avg,land,out,'reference.png',[0,1]),versions=versions(),warning='參考點需位於每組保留配對的有效連通區，且具穩定地物；高相干性不等於零變形。',run_path=str(p)))

def validate_reference(job_path,ref=None):
    import h5py
    p=job_path.parent;job=json.loads(job_path.read_text());loaded=json.loads((p/'loaded.json').read_text())
    if ref is None:
        c=loaded['candidate']
        if not c or c['coherence']<job['options']['reference_coherence']:raise ValueError('找不到達到門檻的共同參考點，請另建分析調整配對或門檻，不能強行反演。')
        ref=[c['y'],c['x']]
    y,x=ref
    if not 0<=y<loaded['height'] or not 0<=x<loaded['width']:raise ValueError('參考點超出載入影像。')
    with h5py.File(p/'inputs/geometryRadar.h5','r') as g:
        if not g['waterMask'][y,x]:raise ValueError('參考點位於水域或無資料區。')
    with h5py.File(p/'inputs/ifgramStack.h5','r') as f:
        keep=f['dropIfgram'][:].astype(bool);phase=f['unwrapPhase'][:,y,x];cc=f['connectComponent'][:,y,x]
        if np.any(~np.isfinite(phase[keep])) or np.any(phase[keep]==0) or np.any(cc[keep]<=0):raise ValueError('參考點不在全部保留配對的有效解纏範圍。')
        if np.mean(f['coherence'][:,y,x][keep])<job['options']['reference_coherence']:raise ValueError('參考點平均相干性低於所設門檻。')
    return ref


def invert(job_path):
    import h5py
    p=job_path.parent;job=json.loads(job_path.read_text())
    ref=validate_reference(job_path,job.get("reference_pixel"));y,x=ref
    with (p/'mintpy.cfg').open('a') as f:f.write(f'mintpy.reference.yx = {y},{x}\n')
    mintpy_steps(job_path,['reference_point','quick_overview','correct_unwrap_error','invert_network','correct_LOD','correct_SET','correct_ionosphere','correct_troposphere','deramp','correct_topography','residual_RMS','reference_date','velocity'])
    # Reference-date correction edits the selected time series; use MintPy's own output selection.
    from mintpy.smallbaselineApp import TimeSeriesAnalysis
    ts=TimeSeriesAnalysis(str(p/'mintpy.cfg'),workDir=str(p));ts.open();files=ts.get_timeseries_filename(ts.template, work_dir=str(p));ts.close()
    final_ts=files[-1] if isinstance(files,(list,tuple)) else None
    # get_timeseries_filename returns a dict in supported MintPy versions.
    if isinstance(files,dict):final_ts=files['velocity']['input']
    if not final_ts or not Path(final_ts).is_file():raise ValueError('無法確認最終時序檔，保留輸出供檢查。')
    final_ts=Path(final_ts).resolve();
    if p.resolve() not in final_ts.parents:raise ValueError('最終時序檔超出分析目錄。')
    job=json.loads(job_path.read_text());job['timeseries_file']=str(final_ts);save(job_path,job)
    out=p/'exports'
    with h5py.File(p/'velocity.h5','r') as f:
        vel=f['velocity'][:]*1000;attrs={k:str(v) for k,v in f.attrs.items()};std=f['velocityStd'][:]*1000 if 'velocityStd' in f else None
    with h5py.File(p/'maskTempCoh.h5','r') as f:mask=f['mask'][:].astype(bool)
    mask&=np.isfinite(vel)
    with h5py.File(p/'temporalCoherence.h5','r') as f:coh=f['temporalCoherence'][:]
    with h5py.File(final_ts,'r') as f:dates=[d.decode() for d in f['date'][:]]
    finite=vel[mask]
    if not finite.size:raise ValueError('時間相干性遮罩後沒有有效速度像素。')
    lim=float(np.percentile(np.abs(finite),98)) or 1
    results=dict(preview=preview(vel,mask,out,'velocity.png',[-lim,lim],'RdBu_r'),coherence_preview=preview(coh,np.isfinite(coh),out,'temporal-coherence.png',[0,1]),dates=dates,reference_pixel=ref,reference_date=attrs.get('REF_DATE'),unit='mm/year',direction='正值＝朝向衛星；負值＝遠離衛星。這是相對參考點的 LOS 位移，不是垂直下陷。',valid_pixels=int(mask.sum()),total_pixels=int(mask.size),versions=versions(),settings=job['options'])
    save(p/'results.json',results);save(out/'results.json',results)
    # Exports are owned by this analysis; originals remain inside the analysis folder.
    shutil.copy2(p/'mintpy.cfg',out/'mintpy.cfg');shutil.copy2(p/'velocity.h5',out/'velocity.h5')

def point_data(job_path,y,x,threshold=0):
    import h5py
    job=json.loads(job_path.read_text());p=job_path.parent
    with h5py.File(p/'maskTempCoh.h5','r') as f:
        a=f['mask'];
        if not 0<=y<a.shape[0] or not 0<=x<a.shape[1]:raise ValueError('位置超出影像範圍。')
        if not a[y,x]:raise ValueError('此位置未通過本次分析的品質遮罩。')
    with h5py.File(job['timeseries_file'],'r') as f:
        dates=[d.decode() for d in f['date'][:]];v=f['timeseries'][:,y,x]*1000
    if not np.isfinite(v).all():raise ValueError('非有限時間序列')
    with h5py.File(p/'inputs/geometryRadar.h5','r') as f:lat=float(f['latitude'][y,x]);lon=float(f['longitude'][y,x])
    with h5py.File(p/'temporalCoherence.h5','r') as f:coh=float(f['temporalCoherence'][y,x])
    if not np.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('顯示門檻需介於 0–1。')
    if coh<threshold:raise ValueError('此位置低於目前的顯示門檻。')
    with h5py.File(p/'velocity.h5','r') as f:
        velocity=float(f['velocity'][y,x])*1000;std=float(f['velocityStd'][y,x])*1000 if 'velocityStd' in f else None
    return dict(y=y,x=x,latitude=lat,longitude=lon,dates=dates,displacement_mm=v.tolist(),unit='mm',velocity=velocity,velocity_std=std if std is not None and np.isfinite(std) else None,temporal_coherence=coh,reference_date=job['options']['reference_date'],analysis_id=job['id'])

def point(job_path,y,x):
    print('RESULT:'+json.dumps(point_data(job_path,y,x),allow_nan=False))

if __name__=='__main__':
    mode=sys.argv[1];path=Path(sys.argv[2]).resolve()
    if mode=='reference':
        print('RESULT:'+json.dumps(validate_reference(path,json.loads(sys.argv[3]))))
    elif mode=='point':point(path,int(sys.argv[3]),int(sys.argv[4]))
    else:
        try:globals()[mode](path)
        except Exception as e:
            import traceback
            traceback.print_exc();print('分析失敗：'+str(e),flush=True);raise
