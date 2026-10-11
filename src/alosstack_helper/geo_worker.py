"""Derived geographic views. Native scientific rasters are always opened read-only.

pyresample performs nearest-neighbour geolocation; the same source-index grid
is used for display, picking and exports. No PNG is stretched in radar coordinates.
"""
import hashlib,json,math,sys
from pathlib import Path
import numpy as np
import h5py
from pyproj import Transformer
from pyresample import geometry,kd_tree
from science_worker import read_raster,save

VERSION=5

def model_metadata(folder):
    """Preserve the scientific meaning of a velocity coefficient in derived outputs."""
    job=json.loads((Path(folder)/'analysis.json').read_text())
    cfg=job.get('parameter_report',{}).get('resolved',{})
    cfg={**job.get('options',{}).get('mintpy_overrides',{}),**cfg}
    time_settings={k:v for k,v in cfg.items() if k.startswith('mintpy.timeFunc.')}
    inactive=lambda v: v is None or v is False or str(v).lower() in ('','no','none','auto','false','[]')
    nonlinear=str(cfg.get('mintpy.timeFunc.polynomial',1)) not in ('1','1.0','auto') or any(not inactive(cfg.get('mintpy.timeFunc.'+k)) for k in ('periodic','stepDate','exp','log'))
    return dict(velocity_label='LOS 模型線性項' if nonlinear else 'LOS 平均速度',
                velocity_meaning='linear coefficient of selected time model' if nonlinear else 'linear trend over selected dates',
                time_function_settings=time_settings,
                uncertainty_method=str(cfg.get('mintpy.timeFunc.uncertaintyQuantification','residue')))

def valid_geometry(lat,lon):
    return np.isfinite(lat)&np.isfinite(lon)&(np.abs(lat)<85)&(np.abs(lon)<=180)&((lat!=0)|(lon!=0))

def perimeter(lat,lon,box=None):
    h,w=lat.shape;y0,y1,x0,x1=box or (0,h,0,w)
    xs=np.unique(np.linspace(x0,x1-1,min(64,x1-x0)).astype(int));ys=np.unique(np.linspace(y0,y1-1,min(64,y1-y0)).astype(int))
    points=[(y0,x) for x in xs]+[(y,x1-1) for y in ys]+[(y1-1,x) for x in xs[::-1]]+[(y,x0) for y in ys[::-1]]
    return [[float(lat[y,x]),float(lon[y,x])] for y,x in points if valid_geometry(lat[y:y+1,x:x+1],lon[y:y+1,x:x+1])[0,0]]

def spacing(lat,lon):
    # Use adjacent native samples (not the map display resolution) for the search radius.
    stride=max(1,int(math.sqrt(lat.size/50000)))
    a=lat[::stride,::stride];b=lon[::stride,::stride];dist=[]
    for axis in (0,1):
        if a.shape[axis]<2:continue
        dl=np.diff(a,axis=axis)*111195;dn=np.diff(b,axis=axis)*111195*np.cos(np.deg2rad(np.nanmedian(a)))
        d=np.hypot(dl,dn)/stride;d=d[np.isfinite(d)&(d>0)&(d<10000)]
        if d.size:dist.append(float(np.median(d)))
    if not dist:raise ValueError('經緯度查找表沒有有效像素間距。')
    return max(dist)

def grid(lat,lon,crs='EPSG:3857',resolution=None):
    valid=valid_geometry(lat,lon)
    if not valid.any():raise ValueError('缺少有效地理座標。')
    if np.ptp(lon[valid])>180:raise ValueError('此版本尚未支援跨換日線資料。')
    forward=Transformer.from_crs(4326,crs,always_xy=True);back=Transformer.from_crs(crs,4326,always_xy=True)
    x,y=forward.transform(lon[valid],lat[valid]);west,east=float(x.min()),float(x.max());south,north=float(y.min()),float(y.max())
    native=spacing(lat,lon)
    if resolution:
        dx=dy=resolution
        if crs=='EPSG:4326':dy=resolution/111195;dx=dy/max(.1,math.cos(math.radians(float(np.median(lat[valid])))))
    else:dx=dy=max(max(east-west,north-south)/850,native/max(.1,math.cos(math.radians(float(np.median(lat[valid]))))))
    width=math.ceil((east-west)/dx)+1;height=math.ceil((north-south)/dy)+1
    if width*height>12000000:raise ValueError('輸出超過 1,200 萬像素，請提高輸出像素大小。')
    west-=dx/2;north+=dy/2;east=west+width*dx;south=north-height*dy
    tx,ty=np.meshgrid(west+(np.arange(width)+.5)*dx,north-(np.arange(height)+.5)*dy)
    t_lon,t_lat=back.transform(tx,ty)
    source=geometry.SwathDefinition(lons=np.where(valid,lon,np.nan),lats=np.where(valid,lat,np.nan))
    target=geometry.SwathDefinition(lons=t_lon,lats=t_lat)
    ids=np.arange(lat.size,dtype=np.float64).reshape(lat.shape)+1
    index=kd_tree.resample_nearest(source,ids,target,radius_of_influence=native*1.8,fill_value=0,nprocs=1).astype('int64')-1
    bounds=[[float(back.transform(west,south)[1]),float(back.transform(west,south)[0])],[float(back.transform(east,north)[1]),float(back.transform(east,north)[0])]]
    return index,dict(width=width,height=height,bounds=bounds,transform=[west,dx,0,north,0,-dy],crs=crs,native_spacing_m=native)

def read_inputs(spec):
    p=Path(spec['folder']);mode=spec['mode']
    if mode=='survey':
        inv=spec['inventory'];g=inv['geometry'];lat=read_raster(g['lat'])[0];lon=read_raster(g['lon'])[0]
        land=read_raster(g['wbd'])[0]==0;data={'height':read_raster(g['hgt'])[0]};mask=land
        ref=None
    else:
        with h5py.File(p/'inputs/geometryRadar.h5','r') as f:lat=f['latitude'][:];lon=f['longitude'][:];land=f['waterMask'][:].astype(bool)
        job=json.loads((p/'analysis.json').read_text());data={};ref=None
        with h5py.File(p/'inputs/geometryRadar.h5','r') as f:
            if 'height' in f:data['height']=f['height'][:]
        if mode=='result':
            with h5py.File(p/'velocity.h5','r') as f:
                data['velocity']=f['velocity'][:]*1000
                if 'velocityStd' in f:data['uncertainty']=f['velocityStd'][:]*1000
            with h5py.File(p/'temporalCoherence.h5','r') as f:data['coherence']=f['temporalCoherence'][:]
            with h5py.File(p/'maskTempCoh.h5','r') as f:mask=f['mask'][:].astype(bool)
            r=json.loads((p/'results.json').read_text());ref=r['reference_pixel']
        else:
            with h5py.File(p/'inputs/ifgramStack.h5','r') as f:
                kept=np.flatnonzero(f['dropIfgram'][:]);coh=np.zeros(lat.shape);mask=land.copy()
                for i in kept:
                    ph=f['unwrapPhase'][i];coh+=f['coherence'][i];mask&=(f['connectComponent'][i]>0)&np.isfinite(ph)&(ph!=0)
                data['coherence']=coh/len(kept)
            c=json.loads((p/'loaded.json').read_text()).get('candidate');ref=[c['y'],c['x']] if c else None
    mask=mask&land&valid_geometry(lat,lon)
    if mode=='result':mask&=np.isfinite(data['velocity'])
    return lat,lon,land,data,mask,ref

def roi(spec):
    lat=read_raster(spec['inventory']['geometry']['lat'])[0];lon=read_raster(spec['inventory']['geometry']['lon'])[0]
    s,n,w,e=spec['bounds'];hit=valid_geometry(lat,lon)&(lat>=s)&(lat<=n)&(lon>=w)&(lon<=e)
    yy,xx=np.nonzero(hit)
    if not yy.size:raise ValueError('框選範圍沒有涵蓋資料像素。')
    box=[int(yy.min()),int(yy.max())+1,int(xx.min()),int(xx.max())+1];y0,y1,x0,x1=box
    fraction=(y1-y0)*(x1-x0)/lat.size
    estimate=sum(Path(row[k]).stat().st_size for row in spec['inventory']['pairs'] for k in ('unw','coherence','components'))*3*fraction
    return dict(subset=box,pixels=(y1-y0)*(x1-x0),selected_pixels=int(hit.sum()),footprint=perimeter(lat,lon,box),fraction=fraction,estimated_bytes=round(estimate+512*1024**2),meaning='MintPy 以涵蓋所選區域的雷達矩形裁切；藍色邊界為實際運算範圍。')

def layer_values(data,mask,land,mode,threshold):
    shape=land.shape;status=np.full(shape,2,dtype='float32');status[~land]=1
    if mode=='result':
        coh=data['coherence'];status[land&np.isfinite(coh)&(coh<threshold)]=3
    status[mask]=4
    return {**data,'status':status}

def map_view(spec):
    lat,lon,land,data,mask,ref=read_inputs(spec);out=Path(spec['out']);out.mkdir(parents=True,exist_ok=True)
    idx,meta=grid(lat,lon);valid=idx>=0;flat=np.maximum(idx,0)
    job=json.loads((Path(spec['folder'])/'analysis.json').read_text()) if spec['mode']!='survey' else {}
    threshold=job.get('options',{}).get('temporal_coherence',.7);data=layer_values(data,mask,land,spec['mode'],threshold)
    from matplotlib import colormaps
    palettes={k:(colormaps[c](np.linspace(0,1,256))*255).astype('uint8').tolist() for k,c in [('diverging','RdBu_r'),('sequential','viridis'),('terrain','Greys')]}
    layers={}
    for name,a in data.items():
        values=np.where(valid,np.asarray(a).ravel()[flat],np.nan).astype('<f4');fname=spec['prefix']+'-'+name+'.bin';values.tofile(out/fname)
        selected=mask&np.isfinite(a) if name in ('velocity','uncertainty') else land&np.isfinite(a)
        vals=np.asarray(a)[selected]
        lo,hi=(np.percentile(vals,[2,98]).tolist() if vals.size else [0,1]);palette='sequential'
        if name=='velocity':hi=float(np.percentile(np.abs(vals),98)) if vals.size else 1;lo=-hi;palette='diverging'
        if name=='coherence':lo,hi=0,1
        if name=='height':palette='terrain'
        layers[name]=dict(file=fname,min=float(lo),max=float(max(hi,lo+1e-5)),palette=palette)
    np.savez_compressed(out/(spec['prefix']+'-index.npz'),index=idx,shape=np.array(lat.shape),transform=np.array(meta['transform']))
    meta.update(prefix=spec['prefix'],layers=layers,palettes=palettes,mode=spec['mode'],footprint=perimeter(lat,lon),native_shape=list(lat.shape),threshold=threshold,valid_pixels=int(mask.sum()),land_pixels=int(land.sum()),total_pixels=int(mask.size),reference=None)
    if spec['mode']=='result':meta.update(model_metadata(spec['folder']))
    if mask.any():
        vlat=lat[mask];vlon=lon[mask];meta['valid_bounds']=[[float(vlat.min()),float(vlon.min())],[float(vlat.max()),float(vlon.max())]]
    if ref:
        y,x=ref;meta['reference']=dict(y=y,x=x,lat=float(lat[y,x]),lon=float(lon[y,x]))
    save(out/(spec['prefix']+'.json'),meta)
    return meta

def pick(spec):
    p=Path(spec['folder']);out=Path(spec['out']);meta=json.loads((out/(spec['prefix']+'.json')).read_text())
    lat0,lon0=map(float,spec['coordinate'])
    if not math.isfinite(lat0) or not math.isfinite(lon0) or not -85<lat0<85 or not -180<=lon0<=180:raise ValueError('座標不合法。')
    if spec.get('native') is not True:
        with np.load(out/(spec['prefix']+'-index.npz')) as z:
            tr=z['transform'];gx,gy=Transformer.from_crs(4326,3857,always_xy=True).transform(lon0,lat0);x=math.floor((gx-tr[0])/tr[1]);y=math.floor((gy-tr[3])/tr[5]);idx=z['index']
            if not 0<=y<idx.shape[0] or not 0<=x<idx.shape[1] or idx[y,x]<0:raise ValueError('此位置在資料涵蓋範圍外。')
            native=int(idx[y,x]);ny,nx=divmod(native,int(z['shape'][1]))
    with h5py.File(p/'inputs/geometryRadar.h5','r') as f:
        if spec.get('native') is True:
            lat=f['latitude'][:];lon=f['longitude'][:];project=Transformer.from_crs(4326,3857,always_xy=True);sx,sy=project.transform(lon,lat);gx,gy=project.transform(lon0,lat0)
            dist=np.where(valid_geometry(lat,lon),(sx-gx)**2+(sy-gy)**2,np.inf);ny,nx=map(int,np.unravel_index(np.argmin(dist),dist.shape))
            if math.sqrt(float(dist[ny,nx]))*math.cos(math.radians(lat0))>meta['native_spacing_m']*1.8:raise ValueError('此位置在資料涵蓋範圍外。')
        latitude=float(f['latitude'][ny,nx]);longitude=float(f['longitude'][ny,nx])
        if not f['waterMask'][ny,nx]:raise ValueError('此位置為水域遮罩，沒有可用的陸地時間序列。')
    if spec['mode']=='reference':
        from science_worker import validate_reference
        validate_reference(p/'analysis.json',[ny,nx])
        return dict(y=ny,x=nx,latitude=latitude,longitude=longitude)
    from science_worker import point_data
    return point_data(p/'analysis.json',ny,nx,float(spec.get('threshold',meta['threshold'])))

def export(spec):
    from osgeo import gdal,osr
    gdal.UseExceptions()
    lat,lon,land,data,mask,ref=read_inputs(spec)
    crs=spec.get('crs','EPSG:4326');resolution=float(spec.get('resolution',100));name=spec.get('layer','velocity')
    if crs not in ('EPSG:4326','EPSG:3826') or not math.isfinite(resolution) or not 20<=resolution<=2000:raise ValueError('請選擇支援的座標系與 20–2000 公尺的像素大小。')
    if crs=='EPSG:3826' and not (np.nanmin(lon)>118 and np.nanmax(lon)<124 and np.nanmin(lat)>20 and np.nanmax(lat)<28):raise ValueError('TWD97 / TM2 121 僅適用本工具的臺灣區域輸出。')
    if name not in ('velocity','uncertainty','coherence') or name not in data:raise ValueError('無此輸出圖層。')
    index,meta=grid(lat,lon,crs,resolution);ok=index>=0;native=np.maximum(index,0);a=data[name]
    threshold=float(spec.get('threshold',0))
    if not math.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('顯示門檻需介於 0–1。')
    mask&=np.isfinite(a)&(data['coherence']>=threshold)
    values=np.where(ok&mask.ravel()[native],a.ravel()[native],np.nan).astype('float32')
    if not np.isfinite(values).any():raise ValueError('目前顯示門檻下沒有可匯出的像素。')
    filename=f"{spec['prefix']}-{name}-{crs.split(':')[1]}-{resolution:g}m-{threshold:g}.tif";dest=Path(spec['out'])/filename
    ds=gdal.GetDriverByName('GTiff').Create(str(dest),meta['width'],meta['height'],1,gdal.GDT_Float32,options=['COMPRESS=DEFLATE','TILED=YES'])
    ds.SetGeoTransform(meta['transform']);sr=osr.SpatialReference();sr.ImportFromEPSG(int(crs.split(':')[1]));ds.SetProjection(sr.ExportToWkt())
    band=ds.GetRasterBand(1);band.SetNoDataValue(float('nan'));band.WriteArray(values);band.SetUnitType('1' if name=='coherence' else 'mm/year')
    model=model_metadata(spec['folder'])
    ds.SetMetadata({'SOURCE_ANALYSIS':Path(spec['folder']).name,'RESAMPLING':'nearest source pixel','DISPLAY_TEMPORAL_COHERENCE':str(threshold),'REFERENCE':'same spatial and temporal reference as source velocity.h5','UNIT':'1' if name=='coherence' else 'mm/year','VELOCITY_MEANING':model['velocity_meaning'],'TIME_FUNCTION_SETTINGS':json.dumps(model['time_function_settings'],ensure_ascii=True),'UNCERTAINTY_METHOD':model['uncertainty_method']});ds.FlushCache();ds=None
    return dict(file=filename,width=meta['width'],height=meta['height'],crs=crs,resolution=resolution)

def figure(spec):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter,MaxNLocator
    from matplotlib.colors import ListedColormap,BoundaryNorm
    out=Path(spec['out']);meta=json.loads((out/(spec['prefix']+'.json')).read_text());name=spec.get('layer','velocity')
    if name not in meta['layers']:raise ValueError('找不到圖層。')
    arrays={k:np.fromfile(out/v['file'],dtype='<f4').reshape(meta['height'],meta['width']) for k,v in meta['layers'].items()}
    low=float(spec.get('vmin',meta['layers'][name]['min']));high=float(spec.get('vmax',meta['layers'][name]['max']));threshold=float(spec.get('threshold',meta['threshold']))
    if not all(math.isfinite(v) for v in (low,high,threshold)) or high<=low or not 0<=threshold<=1:raise ValueError('圖例或門檻數值不合法。')
    tr=meta['transform'];extent=[tr[0],tr[0]+tr[1]*meta['width'],tr[3]+tr[5]*meta['height'],tr[3]]
    fig,ax=plt.subplots(figsize=(8,7));status=arrays['status'].copy();status[(status==4)&(arrays['coherence']<threshold)]=3
    if 'height' in arrays:ax.imshow(np.ma.masked_where(~np.isfinite(status)|(status==1),arrays['height']),extent=extent,cmap='Greys',alpha=.25,interpolation='nearest')
    titles={'velocity':'LOS velocity (mm/year)','uncertainty':'Velocity standard deviation (mm/year)','coherence':'Temporal coherence','status':'Quality mask'}
    if meta.get('velocity_meaning')=='linear coefficient of selected time model':titles['velocity']='LOS model linear coefficient (mm/year)'
    if name=='status':
        cmap=ListedColormap(['#4289b9','#a5a09b','#e7ab3d','#277450']);im=ax.imshow(np.ma.masked_invalid(status),extent=extent,cmap=cmap,norm=BoundaryNorm([.5,1.5,2.5,3.5,4.5],4),interpolation='nearest');bar=fig.colorbar(im,ax=ax,shrink=.7,ticks=[1,2,3,4]);bar.ax.set_yticklabels(['Water','Other mask / no solution','Low temporal coherence','Valid'])
    else:
        invalid=~np.isfinite(status)|(status==1)
        if name in ('velocity','uncertainty'):invalid|=status!=4
        im=ax.imshow(np.ma.masked_where(invalid,arrays[name]),extent=extent,cmap='RdBu_r' if name=='velocity' else 'viridis',vmin=low,vmax=high,interpolation='nearest');fig.colorbar(im,ax=ax,shrink=.7,label=titles[name])
    project=Transformer.from_crs(4326,3857,always_xy=True);inverse=Transformer.from_crs(3857,4326,always_xy=True)
    if meta.get('reference'):
        r=meta['reference'];rx,ry=project.transform(r['lon'],r['lat']);ax.plot(rx,ry,'*',color='#af742a',markersize=10,label='Spatial reference');ax.legend(loc='best',fontsize=8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x,pos:f'{inverse.transform(x,0)[0]:.3f}'));ax.yaxis.set_major_formatter(FuncFormatter(lambda y,pos:f'{inverse.transform(0,y)[1]:.3f}'));ax.xaxis.set_major_locator(MaxNLocator(5));ax.yaxis.set_major_locator(MaxNLocator(5));ax.set_xlabel('Longitude (WGS84)');ax.set_ylabel('Latitude (WGS84)');ax.grid(alpha=.15);ax.set_title(titles[name]+'\nAnalysis '+Path(spec['folder']).name)
    job=json.loads((Path(spec['folder'])/'analysis.json').read_text());fig.text(.08,.02,f"Time reference: {job['options']['reference_date']} | displayed temporal coherence >= {threshold:g}\nNearest source pixel; geographic ticks on Web Mercator grid. Positive LOS = toward satellite.",fontsize=8)
    fig.tight_layout(rect=[0,.07,1,1]);signature=hashlib.sha256(json.dumps([spec['prefix'],name,low,high,threshold]).encode()).hexdigest()[:12];filename='map-figure-'+signature+'.png';fig.savefig(out/filename,dpi=220);plt.close(fig)
    return dict(file=filename)

if __name__=='__main__':
    spec=json.loads(sys.argv[2]);result=globals()[sys.argv[1]](spec);print('RESULT:'+json.dumps(result,ensure_ascii=False,allow_nan=False))
