"""Scientific checks and derived views for alos2App, run in the selected environment."""
import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import numpy as np
from science_worker import read_raster, preview, save, versions


def app(config='alos2App.xml'):
    import isce
    sys.path.insert(0,str(Path(isce.__file__).parent/'applications'))
    from alos2App import Alos2InSAR
    obj=Alos2InSAR(name='alos2App',cmdline=[config]);obj.configure()
    return obj


def resolve(s,meta):
    from osgeo import gdal
    from PIL import Image
    import matplotlib
    import scipy
    from contrib.Snaphu.Snaphu import Snaphu
    obj=app();obj._steps()
    public={p.public_name:p for p in obj.parameter_list}
    props=ET.parse('alos2App.xml').getroot().find("component[@name='alos2insar']")
    unknown=[p.get('name') for p in props if p.get('name') not in public]
    if unknown:raise ValueError('安裝的 alos2App 不認得設定：'+', '.join(unknown))
    steps=[x for x in obj.step_list[:obj.step_list.index('geocode')+1]]
    # Same sequence as pair.py; fail on upstream changes before computing anything.
    expected=['startup','preprocess','baseline','download_dem','prep_slc','slc_offset','form_int','swath_offset','swath_mosaic','frame_offset','frame_mosaic','rdr2geo','geo2rdr','rdrdem_offset','rect_rgoffset','diff_int','look','coherence','ion_subband','ion_unwrap','ion_filt','ion_correct','filt','unwrap','geocode']
    if steps!=expected:raise ValueError('安裝版本的 alos2App 步驟已改變，需要更新工作台。')
    import shutil
    if not shutil.which('imageMath.py'):raise ValueError('環境缺少 imageMath.py。')
    values={k:getattr(obj,p.attrname) for k,p in public.items() if k in [x.get('name') for x in props]}
    if not obj.dem or not obj.demGeo or not obj.wbd:raise ValueError('必須提供本機 DEM/WBD。')
    report=dict(ion=dict(estimate=bool(obj.doIon),apply=bool(obj.applyIon)),steps=steps,properties=values,versions=versions())
    save(meta/'pair-resolved.json',report)


def geometry(s,meta):
    from isceobj.Alos2Proc.Alos2ProcPublic import getBboxGeo
    obj=app();obj.loadPickleObj('baseline');p=obj._insar
    ref=p.loadTrack(reference=True);sec=p.loadTrack(reference=False)
    if p.modeCombination!=1:raise ValueError('此版僅支援 Stripmap / Stripmap。')
    if ref.pointingDirection!=sec.pointingDirection or ref.passDirection!=sec.passDirection:raise ValueError('兩期影像的升降軌或左右視方向不同。')
    a=list(map(float,getBboxGeo(ref)));b=list(map(float,getBboxGeo(sec)))
    overlap=[max(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),min(a[3],b[3])]
    if overlap[0]>=overlap[1] or overlap[2]>=overlap[3]:raise ValueError('兩期影像沒有共同地理範圍。')
    for attr in ('dem','demGeo','wbd'):
        import isceobj
        im=isceobj.createDemImage();im.load(str(getattr(obj,attr))+'.xml')
        xs=[im.firstLongitude,im.firstLongitude+im.deltaLongitude*(im.width-1)];ys=[im.firstLatitude,im.firstLatitude+im.deltaLatitude*(im.length-1)]
        if min(ys)>a[0] or max(ys)<a[1] or min(xs)>a[2] or max(xs)<a[3]:raise ValueError(attr+' 的涵蓋範圍不足以包住參考影像。')
    bbox=s['config'].get('bbox')
    if bbox and (bbox[1]<=overlap[0] or bbox[0]>=overlap[1] or bbox[3]<=overlap[2] or bbox[2]>=overlap[3]):raise ValueError('地理編碼範圍與影像不相交。')
    look_names=['numberRangeLooks1','numberAzimuthLooks1','numberRangeLooks2','numberAzimuthLooks2','numberRangeLooksIon','numberAzimuthLooksIon']
    base=None
    f=Path('alos2Proc.xml')
    if f.exists():
        for n in ET.parse(f).getroot().iter():
            if ''.join(n.get('name',n.tag).lower().replace('_','').split())=='perpendicularbaselineatcenterofreferencetrack':
                try:base=float(n.findtext('value') or n.text)
                except (ValueError,TypeError):pass
    terrain=terrain_background(obj.demGeo,{'bounds':[[a[0],a[2]],[a[1],a[3]]]},meta/'pair-exports',name='footprint-terrain.png')
    report=dict(terrain=terrain,reference_bounds=a,secondary_bounds=b,overlap_bounds=overlap,wavelength=float(ref.radarWavelength),pass_direction=ref.passDirection,pointing_direction=ref.pointingDirection,looks={k:int(getattr(p,k)) for k in look_names},baseline_center=base,artifacts=['pair-metadata.json','footprint-terrain.png'],warning='外框交集是初步檢查；真實有效覆蓋仍由對位及相干性決定。')
    save(meta/'pair-metadata.json',report);save(meta/'pair-exports/pair-metadata.json',report)


def checked_raster(path):
    path=Path(path)
    if not path.is_file() or not path.stat().st_size:raise ValueError('缺少成果：'+str(path))
    root=ET.parse(str(path)+'.vrt').getroot();w=int(root.get('rasterXSize'));h=int(root.get('rasterYSize'));end=0
    widths={'Float32':4,'Float64':8,'Int16':2,'Byte':1,'CFloat32':8}
    for b in root.findall('VRTRasterBand'):
        if b.get('subClass')!='VRTRawRasterBand':raise ValueError('預期原始 ISCE2 VRT。')
        source=Path(b.findtext('SourceFilename'))
        if b.find('SourceFilename').get('relativeToVRT')=='1':source=path.parent/source
        if source.resolve()!=path.resolve():raise ValueError('VRT 與 raster 本體不符。')
        end=max(end,int(b.findtext('ImageOffset'))+(h-1)*int(b.findtext('LineOffset'))+(w-1)*int(b.findtext('PixelOffset'))+widths[b.get('dataType')])
    if end!=path.stat().st_size:raise ValueError('成果大小與描述檔不符：'+str(path))
    return read_raster(path)


def ion(s,meta):
    looks=s['pair_metadata']['looks'];r=looks['numberRangeLooks1']*looks['numberRangeLooksIon'];a=looks['numberAzimuthLooks1']*looks['numberAzimuthLooksIon']
    out=meta/'pair-exports';folder=Path('ion/ion_cal');layers=[];shape=None
    land=checked_raster(folder/f'wbd_{r}rlks_{a}alks.wbd')[0]==0
    cor=checked_raster(folder/f'diff_{r}rlks_{a}alks.cor')[-1]
    support=land&np.isfinite(cor)&(cor>=0.3)
    for band in ('lower','upper'):
        support &= checked_raster(folder/f'{band}_{r}rlks_{a}alks.unw.conncomp')[0]>0
    for ext,label,unit,cmap in [('ion','估計電離層相位','rad','RdBu_r'),('std','估計標準差','rad','viridis'),('win','自適應濾波視窗','pixels','viridis')]:
        arr=checked_raster(folder/f'filt_ion_{r}rlks_{a}alks.{ext}')[-1]
        if shape is not None and arr.shape!=shape:raise ValueError('電離層成果尺寸不一致。')
        shape=arr.shape;valid=np.isfinite(arr)&(arr!=0)
        if not valid.any():raise ValueError('電離層產物沒有可判讀的有效像素：'+ext)
        p=preview(arr,valid,out,'ion-'+ext+'.png',cmap=cmap);layers.append(dict(**p,label=label,unit=unit))
    arr=checked_raster(folder/f'filt_ion_{r}rlks_{a}alks.ion')[-1]
    layers.insert(0,dict(**preview(arr,support,out,'ion-supported.png',cmap='RdBu_r'),label='有效陸地電離層相位（相干性 ≥ 0.3）',unit='rad'))
    layers.append(dict(**preview(cor,land&np.isfinite(cor),out,'ion-coherence.png',[0,1]),label='子頻帶差分相干性（陸地）',unit='0–1'))
    # Lower/upper unwrapped bands and their coherence aid scientific review.
    for band in ('lower','upper'):
        candidates=list(folder.glob(band+f'_{r}rlks_{a}alks.unw'))
        if candidates:
            arr=checked_raster(candidates[0])[-1];layers.append(dict(**preview(arr,np.isfinite(arr)&(arr!=0),out,'ion-'+band+'.png',cmap='turbo'),label=band+' 子頻帶解纏相位',unit='rad'))
    report=dict(supported_pixels=int(support.sum()),land_pixels=int(land.sum()),layers=layers,artifacts=[p['file'] for p in layers]+['pair-ion.json'],warning='雷達座標預覽。有效陸地圖只顯示水體遮罩為陸地、相干性 ≥ 0.3 且兩子頻帶連通區有效的像素；此顯示遮罩不改變估計或校正。需檢查異常條紋、解纏跳變與平滑是否合理。雙日期沒有 ion_ls 配對網路可供排除。')
    save(meta/'pair-ion.json',report);save(out/'pair-ion.json',report)


def geotiff(path,out,name,band,unit="rad"):
    """Copy official geocoded raster to a portable, north-up WGS84 GeoTIFF."""
    from osgeo import gdal
    gdal.UseExceptions()
    ds=gdal.Open(str(path)+'.vrt')
    if ds is None:raise ValueError('無法開啟地理編碼結果。')
    if not ds.GetProjection():raise ValueError('地理編碼成果缺少座標系。')
    target=out/name
    result=gdal.Translate(str(target),ds,format='GTiff',bandList=[band],creationOptions=['COMPRESS=DEFLATE','TILED=YES'])
    result.GetRasterBand(1).SetUnitType(unit);result.GetRasterBand(1).SetNoDataValue(0);result.FlushCache();result=None
    return target


def geographic_layer(tif,out,name,label,unit,cmap,limits=None):
    from osgeo import gdal
    from PIL import Image
    from matplotlib import colormaps
    gdal.UseExceptions();src=gdal.Open(str(tif));tmp=gdal.Warp('',src,format='MEM',dstSRS='EPSG:3857',resampleAlg='near',srcNodata=0,dstNodata=float('nan'))
    # Square projection pixels avoid stretching a geographic grid in Leaflet.
    w,h=tmp.RasterXSize,tmp.RasterYSize;scale=min(1,850/max(w,h));w2=max(1,round(w*scale));h2=max(1,round(h*scale))
    ds=gdal.Translate('',tmp,format='MEM',width=w2,height=h2);a=ds.ReadAsArray();valid=np.isfinite(a);v=a[valid]
    lo,hi=limits or (np.percentile(v,[2,98]).tolist() if v.size else [0,1])
    if hi<=lo:hi=lo+1
    rgba=(colormaps[cmap](np.clip(np.nan_to_num((a-lo)/(hi-lo)),0,1))*255).astype('uint8');rgba[~valid]=0
    Image.fromarray(rgba).save(out/name)
    from pyproj import Transformer
    tr=Transformer.from_crs(3857,4326,always_xy=True);gt=ds.GetGeoTransform()
    west,north=tr.transform(gt[0],gt[3]);east,south=tr.transform(gt[0]+w2*gt[1],gt[3]+h2*gt[5])
    lut=(colormaps[cmap](np.linspace(0,1,256))*255).astype('uint8').tolist()
    valid_bounds=None
    if valid.any():
        yy,xx=np.where(valid);vw,vn=tr.transform(gt[0]+int(xx.min())*gt[1],gt[3]+int(yy.min())*gt[5]);ve,vs=tr.transform(gt[0]+(int(xx.max())+1)*gt[1],gt[3]+(int(yy.max())+1)*gt[5]);valid_bounds=[[vs,vw],[vn,ve]]
    return dict(file=name,label=label,unit=unit,min=float(lo),max=float(hi),bounds=[[south,west],[north,east]],valid_bounds=valid_bounds,lut=lut)



def terrain_background(source,layer,out,name='terrain.png'):
    from osgeo import gdal
    from pyproj import Transformer
    from PIL import Image
    gdal.UseExceptions();south,west=layer['bounds'][0];north,east=layer['bounds'][1]
    tr=Transformer.from_crs(4326,3857,always_xy=True);xmin,ymin=tr.transform(west,south);xmax,ymax=tr.transform(east,north)
    if layer.get('file'):
        with Image.open(out/layer['file']) as image:shape=image.size
    else:
        ratio=(ymax-ymin)/(xmax-xmin);shape=(max(1,round(850/max(1,ratio))),max(1,round(850*min(1,ratio))))
    ds=gdal.Warp('',str(source)+'.vrt',format='MEM',dstSRS='EPSG:3857',outputBounds=[xmin,ymin,xmax,ymax],width=shape[0],height=shape[1],outputType=gdal.GDT_Float32,resampleAlg='bilinear',srcNodata=-32768,dstNodata=float('nan'))
    a=ds.ReadAsArray();valid=np.isfinite(a);v=a[valid]
    low,high=np.percentile(v,[2,98]) if v.size else (0,1)
    gray=(245-100*np.clip(np.nan_to_num((a-low)/max(1,high-low)),0,1)).astype('uint8')
    rgba=np.stack([gray,gray,gray,np.where(valid,255,0).astype('uint8')],axis=-1)
    Image.fromarray(rgba).save(out/name)
    return dict(file=name,bounds=layer['bounds'])


def results(s,meta):
    p=app('alos2App.review.xml' if s.get('review_decision') else 'alos2App.xml');p.loadPickleObj('geocode');ctx=p._insar
    out=meta/'pair-exports';folder=Path('insar');unw=folder/ctx.unwrappedInterferogram;masked=folder/ctx.unwrappedMaskedInterferogram;coh=folder/ctx.multilookCoherence
    phase=checked_raster(unw)[-1];masked_phase=checked_raster(masked)[-1];cor=checked_raster(coh)[-1]
    components=checked_raster(Path(str(unw)+'.conncomp'))[0];wbd=checked_raster(folder/ctx.multilookWbdOut)[0]
    if len({a.shape for a in (phase,masked_phase,cor,components,wbd)})!=1:raise ValueError('解纏、相干性與幾何尺寸不一致。')
    land=wbd==0;valid=land&(components>0)&np.isfinite(phase)&(masked_phase!=0)
    if not valid.any():raise ValueError('成果沒有共同有效陸地像素。')
    finite_cor=cor[land&np.isfinite(cor)];issues=[]
    if np.any(~np.isfinite(cor[land])) or np.any(~np.isfinite(phase[land])):issues.append('陸地成果含非有限數值，請檢查資料品質。')
    if np.any((finite_cor<0)|(finite_cor>1.0001)):issues.append('部分相干性超出 0–1，請檢查數值。')
    layers=[];artifacts=[]
    for path,name,label,unit,cmap,lim,band in [
      (Path(str(masked)+'.geo'),'unwrapped-phase','遮罩解纏相位','rad','RdBu_r',None,2),
      (Path(str(coh)+'.geo'),'coherence','相干性','0–1','viridis',[0,1],1)]:
        checked_raster(path)
        # Coherence may be a one-band image; get the actual upstream band count.
        if name=='coherence':band=len(read_raster(path))
        tif=geotiff(path,out,name+'.tif',band,'1' if name=='coherence' else 'rad')
        layers.append(geographic_layer(tif,out,name+'.png',label,unit,cmap,lim));artifacts.extend([name+'.tif',name+'.png'])
    if s['ion']['estimate']:
        path=Path(str(folder/ctx.multilookIon)+'.geo');checked_raster(path)
        tif=geotiff(path,out,'ionosphere.tif',1);layers.append(geographic_layer(tif,out,'ionosphere.png','估計電離層相位','rad','RdBu_r'));artifacts.extend(['ionosphere.tif','ionosphere.png'])
    terrain=terrain_background(p._insar.demGeo,layers[0],out);artifacts.append('terrain.png')
    # Portable raw phase/coherence products. No unreferenced phase is labelled displacement.
    report=dict(layers=layers,terrain=terrain,artifacts=artifacts+['pair-results.json','alos2App.xml'],reference=s['config']['reference'],secondary=s['config']['secondary'],ion_applied=bool(s['ion']['estimate'] and s['ion']['apply']),land_pixels=int(land.sum()),valid_pixels=int(valid.sum()),retained_percent=float(valid.sum()/max(1,land.sum())*100),coherence_median=float(np.median(finite_cor)),components=int(np.unique(components[valid]).size),issues=issues,wavelength=s['pair_metadata']['wavelength'],versions=versions(),meaning='目前顯示解纏相位（rad）。相位仍可能含大氣、軌道、地形與解纏誤差；尚未選穩定空間參考點，不標為絕對位移或速度。單一配對不提供 MintPy 時序。')
    import shutil
    shutil.copy2('alos2App.review.xml' if s.get('review_decision') else 'alos2App.xml',out/'alos2App.xml')
    save(meta/'pair-results.json',report);save(out/'pair-results.json',report)


if __name__=='__main__':
    mode,state=sys.argv[1:3];state=Path(state);s=json.loads(state.read_text());meta=state.parent
    os.environ.setdefault('MPLBACKEND','Agg');os.environ.setdefault('MPLCONFIGDIR',str(meta/'cache/matplotlib'))
    (meta/'pair-exports').mkdir(exist_ok=True)
    {'resolve':resolve,'metadata':geometry,'ion':ion,'results':results}[mode](s,meta)
