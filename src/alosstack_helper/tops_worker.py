"""Scientific TOPS checks executed only inside the selected ISCE2 environment."""
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import numpy as np
from science_worker import save, versions


def app():
    import isce
    sys.path.insert(0,str(Path(isce.__file__).parent/'applications'))
    from topsApp import TopsInSAR
    obj=TopsInSAR(name='topsApp',cmdline=['topsApp.xml']);obj.configure()
    return obj


def resolve(s,meta):
    from osgeo import gdal
    obj=app();obj._steps()
    from contrib.Snaphu.Snaphu import Snaphu
    expected=s['tops_steps']
    if obj.step_list[:len(expected)]!=expected:raise ValueError('安裝版本的 TOPS 步驟已改變，需要更新工作台。')
    props=ET.parse('topsApp.xml').getroot().find("component[@name='topsinsar']")
    public={p.public_name:p for p in obj.parameter_list}
    unknown=[p.get('name') for p in props.findall('property') if p.get('name') not in public]
    if unknown:raise ValueError('安裝版本不認得設定：'+', '.join(unknown))
    for key in ('reference','secondary'):
        sensor=getattr(obj,key);names={p.public_name for p in sensor.parameter_list}
        if any(p.get('name') not in names for p in props.find(f"component[@name='{key}']")):raise ValueError('感測器參數不相容。')
        if not sensor.orbitFile:raise ValueError('精密軌道未成功設定。')
    values={p.get('name'):getattr(obj,public[p.get('name')].attrname) for p in props.findall('property')}
    save(meta/'tops-resolved.json',dict(properties=values,steps=expected,versions=versions()))


def geometry(s,meta):
    obj=app();obj.loadPickleObj('computeBaselines');ctx=obj._insar;channels=[]
    for swath in s['config']['swaths']:
        i=swath-1;n=int(ctx.numberOfCommonBursts[i]);a=int(ctx.commonBurstStartReferenceIndex[i]);b=int(ctx.commonBurstStartSecondaryIndex[i])
        if n<2 or min(a,b)<0:raise ValueError(f'IW{swath} 不足兩個真正共同 burst，無法繼續 ESD。')
        bounds=[];wavelengths=[]
        for prefix,start in ((ctx.referenceSlcProduct,a),(ctx.secondarySlcProduct,b)):
            prod=ctx.loadProduct(str(Path(prefix)/f'IW{swath}.xml'))
            if start+n>len(prod.bursts):raise ValueError('共同 burst 索引超出影像。')
            for burst in prod.bursts[start:start+n]:
                bounds.append(list(map(float,burst.getBbox()))[:4]);wavelengths.append(float(burst.radarWavelength))
        if max(wavelengths)-min(wavelengths)>1e-6:raise ValueError('雷達波長不一致。')
        channels.append(dict(swath=swath,count=n,reference_start=a+1,secondary_start=b+1,bounds=[min(x[0] for x in bounds),max(x[1] for x in bounds),min(x[2] for x in bounds),max(x[3] for x in bounds)],wavelength=wavelengths[0]))
    import isceobj
    dem=isceobj.createDemImage();dem.load(s['prepared_inputs']['dem']+'.xml')
    xs=[dem.firstLongitude,dem.firstLongitude+dem.deltaLongitude*(dem.width-1)];ys=[dem.firstLatitude,dem.firstLatitude+dem.deltaLatitude*(dem.length-1)]
    for channel in channels:
        b=channel['bounds']
        if min(ys)>b[0] or max(ys)<b[1] or min(xs)>b[2] or max(xs)<b[3]:raise ValueError('DEM 未涵蓋 ISCE2 計算的共同 burst 範圍，請準備更大的 DEM 並建立新專案。')
    save(meta/'tops-geometry.json',dict(channels=channels,warning='共同 burst 已由 ISCE2 軌道幾何確認；這不代表精對位或解纏品質已通過。'))


def geographic_layer(tif,out,name,label,unit,cmap,limits=None):
    from osgeo import gdal
    from PIL import Image
    from matplotlib import colormaps
    from pyproj import Transformer
    src=gdal.Open(str(tif));gt=src.GetGeoTransform()
    # Bound the preview before allocation, including its projection change.
    tmp=gdal.Warp('',src,format='VRT',dstSRS='EPSG:3857',resampleAlg='near',dstNodata=float('nan'))
    w,h=tmp.RasterXSize,tmp.RasterYSize;scale=min(1,850/max(w,h));w2=max(1,round(w*scale));h2=max(1,round(h*scale))
    ds=gdal.Translate('',tmp,format='MEM',width=w2,height=h2)
    a=ds.ReadAsArray();valid=np.isfinite(a);v=a[valid]
    lo,hi=limits or (np.percentile(v,[2,98]).tolist() if v.size else [0,1])
    if hi<=lo:hi=lo+1
    rgba=(colormaps[cmap](np.clip(np.nan_to_num((a-lo)/(hi-lo)),0,1))*255).astype('uint8');rgba[~valid]=0
    Image.fromarray(rgba).save(out/name)
    tr=Transformer.from_crs(3857,4326,always_xy=True);gt=ds.GetGeoTransform()
    west,north=tr.transform(gt[0],gt[3]);east,south=tr.transform(gt[0]+w2*gt[1],gt[3]+h2*gt[5])
    return dict(file=name,label=label,unit=unit,min=float(lo),max=float(hi),bounds=[[south,west],[north,east]],lut=(colormaps[cmap](np.linspace(0,1,256))*255).astype('uint8').tolist())


def results(s,meta):
    from osgeo import gdal
    from pair_worker import checked_raster,terrain_background
    gdal.UseExceptions();out=meta/'tops-exports';out.mkdir(exist_ok=True)
    names=['merged/filt_topophase.unw.geo','merged/phsig.cor.geo','merged/filt_topophase.unw.conncomp.geo','merged/filt_topophase.flat.geo']
    # Check descriptors and physical length, without loading a full raster into memory.
    sources=[]
    for p in names:
        if not Path(p).is_file() or not Path(p+'.vrt').is_file():raise ValueError('缺少地理編碼成果：'+p)
        checked_raster(p)
        ds=gdal.Open(p+'.vrt')
        if not ds.GetProjection():raise ValueError('成果缺少座標系。')
        sources.append(ds)
    shape={(d.RasterXSize,d.RasterYSize,d.GetGeoTransform(),d.GetProjection()) for d in sources}
    if len(shape)!=1:raise ValueError('地理編碼成果網格不一致。')
    unw,coh,cc,flat=sources
    if unw.RasterCount!=2:raise ValueError('解纏成果波段不符合預期。')
    specs=[('unwrapped-phase','解纏相位','rad','RdBu_r',None),('coherence','相干性','1','viridis',[0,1]),('wrapped-phase','包裹干涉相位','rad','twilight',[-np.pi,np.pi]),('connected-components','解纏連通區','id','tab20',None)]
    outputs=[]
    for name,label,unit,cmap,lim in specs:
        ds=gdal.GetDriverByName('GTiff').Create(str(out/(name+'.tif')),unw.RasterXSize,unw.RasterYSize,1,gdal.GDT_Float32,options=['TILED=YES','COMPRESS=DEFLATE'])
        ds.SetProjection(unw.GetProjection());ds.SetGeoTransform(unw.GetGeoTransform());ds.GetRasterBand(1).SetNoDataValue(float('nan'));ds.GetRasterBand(1).SetUnitType(unit);outputs.append(ds)
    count=0
    for y in range(0,unw.RasterYSize,128):
        h=min(128,unw.RasterYSize-y);args=(0,y,unw.RasterXSize,h)
        phase=unw.GetRasterBand(2).ReadAsArray(*args);cor=coh.GetRasterBand(coh.RasterCount).ReadAsArray(*args);comp=cc.GetRasterBand(1).ReadAsArray(*args);z=flat.GetRasterBand(1).ReadAsArray(*args)
        valid=(comp>0)&np.isfinite(phase)&np.isfinite(cor)&(cor>=0)&(cor<=1.0001)
        count+=int(valid.sum())
        # Zero is a valid phase/coherence value. Only explicit validity masks become NoData.
        for ds,arr in zip(outputs,[phase,cor,np.angle(z),comp]):ds.GetRasterBand(1).WriteArray(np.where(valid,arr,np.nan).astype('float32'),0,y)
    for ds in outputs:ds.FlushCache()
    outputs.clear();ds=None
    if not count:raise ValueError('成果沒有有效解纏像素，請檢查科學日誌。')
    layers=[geographic_layer(out/(name+'.tif'),out,name+'.png',label,unit,cmap,lim) for name,label,unit,cmap,lim in specs]
    terrain=terrain_background(s['prepared_inputs']['dem'],layers[0],out)
    artifacts=[name+ext for name,*_ in specs for ext in ('.tif','.png')]+['terrain.png','tops-results.json','topsApp.xml']
    import shutil
    shutil.copy2('topsApp.xml',out/'topsApp.xml')
    report=dict(layers=layers,terrain=terrain,artifacts=artifacts,valid_pixels=count,versions=versions(),meaning='相位單位為 rad。未選穩定空間參考點，不標示為位移或速度；仍需檢查大氣、軌道、地形及解纏誤差。有效遮罩為連通區 > 0 及有限相位／相干性，不等於高品質或陸地遮罩。電離層校正在本版關閉。')
    save(meta/'tops-results.json',report);save(out/'tops-results.json',report)


if __name__=='__main__':
    mode,state=sys.argv[1:3];state=Path(state);s=json.loads(state.read_text())
    os.environ.setdefault('MPLBACKEND','Agg')
    {'resolve':resolve,'geometry':geometry,'results':results}[mode](s,state.parent)
