"""Project-local raster descriptors; source DEM/WBD files remain untouched."""
from pathlib import Path
import xml.etree.ElementTree as ET

def prepare_raster(source, folder):
    source = Path(source).resolve()
    descriptor = Path(str(source)+'.xml')
    if not source.is_file() or not descriptor.is_file():
        raise ValueError(f'缺少地形資料或描述檔：{source}')
    tree = ET.parse(descriptor)
    properties = [p for p in tree.getroot().iter('property')
                  if p.get('name','').lower() == 'file_name']
    if len(properties) != 1:
        raise ValueError(f'描述檔需包含唯一的 file_name：{descriptor}')
    folder = Path(folder).absolute(); folder.mkdir(parents=True,exist_ok=True)
    target = folder/source.name
    if target.is_symlink():
        if target.resolve() != source:
            raise ValueError(f'既有地形資料連結不符：{target}')
    elif target.exists():
        raise ValueError(f'不覆蓋既有檔案：{target}')
    else:
        target.symlink_to(source)
    prop = properties[0]
    value = prop.find('value')
    if value is None: prop.text = str(target)
    else: value.text = str(target)
    vrt = Path(str(source)+'.vrt')
    if vrt.exists():
        vt = ET.parse(vrt)
        for node in vt.getroot().iter('SourceFilename'):
            if Path(node.text or '').name != source.name:
                raise ValueError(f'VRT 引用了其他資料，需要另行確認：{vrt}')
            node.text = str(target); node.set('relativeToVRT','0')
        vt.write(str(target)+'.vrt',encoding='utf-8',xml_declaration=True)
        for extra in tree.getroot().iter('property'):
            if extra.get('name','').lower() == 'extra_file_name':
                val=extra.find('value')
                if val is None: extra.text=str(target)+'.vrt'
                else: val.text=str(target)+'.vrt'
    tree.write(str(target)+'.xml',encoding='utf-8',xml_declaration=True)
    return str(target)
