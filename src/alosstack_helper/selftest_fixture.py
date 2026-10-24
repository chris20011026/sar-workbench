"""Small public, artificial ISCE raster fixture for the installed self-test.

All metadata, orbit vectors and geographic grids are invented test values.
This is not an ALOS observation or a geolocation accuracy reference.
"""
from pathlib import Path
import struct
import xml.etree.ElementTree as ET

def prop(node, name, value):
    child = ET.SubElement(node, 'property', name=name)
    ET.SubElement(child, 'value').text = str(value)


def component(parent, name, module, factory):
    out = ET.SubElement(parent, 'component', name=name)
    ET.SubElement(out, 'factorymodule').text = module
    ET.SubElement(out, 'factoryname').text = factory
    return out


def metadata(path, kind):
    root = ET.Element('productmanager_name')
    instance = component(root, 'instance', 'isceobj.Sensor.MultiMode.' + kind, kind)
    for key, value in dict(numberofsamples=256, numberoflines=256, startingrange=700000., rangesamplingrate=32000000.,
                           rangepixelsize=2., pulserepetitionfrequency=2000., azimuthpixelsize=3., azimuthlineinterval=.002,
                           sensingstart='2018-11-03 12:00:00').items(): prop(instance, key, value)
    if kind == 'Track':
        for key, value in dict(passdirection='ascending', pointingdirection='right', operationmode='SM3', radarwavelength=.236).items(): prop(instance, key, value)
        orbit = component(instance, 'orbit', 'isceobj.Orbit.Orbit', 'createOrbit')
        seq = component(orbit, 'state_vectors', 'iscesys.Component', 'createTraitSeq')
        for i, t in enumerate(['11:59:20','11:59:40','12:00:00','12:00:20','12:00:40'], 1):
            row = component(seq, 'statevector' + str(i), 'isceobj.Orbit.Orbit', 'StateVector')
            for key, value in dict(position=[7000000., (i-3)*140000., 10000.], velocity=[0.,7000.,0.],time='2018-11-03 '+t).items(): prop(row,key,value)
    else:
        prop(instance, 'framenumber', '0480')
        seq = component(instance, 'swaths', 'iscesys.Component', 'createTraitSeq')
        row = component(seq, 'swath1', 'isceobj.Sensor.MultiMode.Swath', 'Swath')
        for key, value in dict(swathnumber=1, polarization='HH', rangebandwidth=28000000.).items(): prop(row,key,value)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path)


def raster(path, kind='Float32', bands=1, width=32, height=16, value=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    size, fmt, isce = {'Float32': (4,'<f','FLOAT'), 'Float64': (8,'<d','DOUBLE'), 'Byte':(1,'B','BYTE')}[kind]
    path.write_bytes(struct.pack(fmt,value)*(width*height*bands))
    x=ET.Element('imageFile')
    for key,val in dict(width=width,length=height,number_bands=bands,data_type=isce,scheme='BIL',byte_order='l',file_name=str(path)).items():prop(x,key,val)
    ET.ElementTree(x).write(str(path)+'.xml')
    v=ET.Element('VRTDataset',rasterXSize=str(width),rasterYSize=str(height))
    for i in range(bands):
        b=ET.SubElement(v,'VRTRasterBand',band=str(i+1),dataType=kind,subClass='VRTRawRasterBand')
        ET.SubElement(b,'SourceFilename',relativeToVRT='1').text=path.name
        for key,val in dict(ImageOffset=i*width*size,PixelOffset=size,LineOffset=width*size*bands,ByteOrder='LSB').items():ET.SubElement(b,key).text=str(val)
    ET.ElementTree(v).write(str(path)+'.vrt')


def dataset(base, environment=None):
    """Public synthetic fixture, also used by the installed-environment check."""
    source=base/'manual'/'proc';source.mkdir(parents=True)
    pairs=['170826-181103','170826-190518','181103-190518'];suffix='_8rlks_16alks'
    for pair in pairs:
        p=source/'pairs'/pair/'insar'
        raster(p/f'filt_{pair}{suffix}.unw',bands=2)
        raster(p/f'filt_{pair}{suffix}_msk.unw',bands=2)
        raster(p/f'{pair}{suffix}.cor',bands=2,value=.9)
        raster(p/f'filt_{pair}{suffix}.unw.conncomp',kind='Byte')
    geom=source/'dates_resampled/181103/insar'
    for ext in ('hgt','lat','lon','los','wbd'):
        kind='Float64' if ext in ('lat','lon') else 'Byte' if ext=='wbd' else 'Float32'
        value={'hgt':100.,'lat':25.,'lon':121.,'los':35.,'wbd':0}[ext]
        raster(geom/f'181103{suffix}.{ext}',kind=kind,bands=2 if ext=='los' else 1,value=value)
    metadata(geom.parent/'181103.track.xml','Track');metadata(geom.parent/'f1_0480/181103.frame.xml','Frame')
    (source/'baseline').mkdir();(source/'baseline/baseline_center.txt').write_text('reference secondary parallel perpendicular\nYYMMDD YYMMDD meters meters\n181103 170826 2.0 12.0\n181103 190518 3.0 24.0\n')
    env=environment or base/'environment'
    if not environment:
        (env/'bin').mkdir(parents=True);(env/'bin/python').touch();(env/'bin/conda').touch()
    return dict(source=str(source),root=str(base/'adopted'),name='既有成果測試',environment=str(env)),pairs
