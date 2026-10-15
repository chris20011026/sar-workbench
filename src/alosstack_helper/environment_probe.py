"""Run inside the selected Conda prefix; import/entrypoint checks, no SAR processing."""
from pathlib import Path
import importlib
import json
import platform
import sys


def probe(capabilities):
    rows=[]
    def check(name, fn, required=True):
        try:
            detail=fn();rows.append(dict(name=name,ok=True,required=required,detail=str(detail or '通過')))
        except Exception as e:rows.append(dict(name=name,ok=False,required=required,detail=str(e)))
    check('Python',lambda:sys.version.split()[0]+' · '+platform.machine())
    def architecture():
        subdirs=set()
        for p in (Path(sys.prefix)/'conda-meta').glob('*.json'):
            value=json.loads(p.read_text()).get('subdir')
            if value and value!='noarch':subdirs.add(value)
        if len(subdirs)>1:raise ValueError('套件包含多個架構：'+', '.join(sorted(subdirs)))
        if not subdirs:raise ValueError('缺少 Conda 架構紀錄')
        return ', '.join(sorted(subdirs))+'；這是套件標記檢查'
    check('套件架構',architecture)
    if 'isce' in capabilities:
        def isce2():
            isce=importlib.import_module('isce')
            importlib.import_module('isceobj');importlib.import_module('stdproc')
            stack=Path(sys.prefix)/'share/isce2/alosStack/create_cmds.py'
            if not stack.is_file():raise ValueError('缺少 alosStack 入口')
            return str(isce.__file__)
        check('ISCE2 匯入與 alosStack 入口',isce2)
    if 'mintpy' in capabilities:
        check('MintPy 時序入口',lambda:importlib.import_module('mintpy.smallbaselineApp').__file__)
    if 'isce3' in capabilities:
        def isce3():
            module=importlib.import_module('isce3');importlib.import_module('nisar.workflows.insar')
            if module.__version__!='0.25.21':raise ValueError('目前 NISAR 介面核對版本為 0.25.21；此環境是 '+module.__version__)
            return module.__version__+' · 完整 NISAR workflow 匯入通過'
        check('ISCE3／NISAR 入口與版本',isce3)
    def gdal():
        from osgeo import gdal
        if 'isce3' in capabilities and not gdal.GetDriverByName('HDF5'):
            raise ValueError('NISAR 需要 HDF5 driver，此環境未提供')
        return gdal.VersionInfo()+(' · HDF5' if 'isce3' in capabilities else '')
    check('GDAL 與 NISAR HDF5' if 'isce3' in capabilities else 'GDAL',gdal)
    check('資料讀寫函式庫',lambda:[importlib.import_module(n).__name__ for n in ('numpy','h5py')])
    check('成果繪圖函式庫',lambda:[importlib.import_module(n).__name__ for n in ('pyproj','matplotlib','PIL')],
          required=bool({'isce','mintpy'} & set(capabilities)))
    return dict(ok=all(r['ok'] for r in rows if r['required']),checks=rows,executable=sys.executable,
                note='基本匯入與入口檢查，不代表所有處理模式或真實衛星影像已驗證；建立專案時仍需健檢。ISCE3 的成果繪圖套件為選用，可在資料瀏覽器另選繪圖完整的環境。')


if __name__=='__main__':
    report=probe(json.loads(sys.argv[2]));Path(sys.argv[1]).write_text(json.dumps(report,ensure_ascii=False))
    sys.exit(0 if report['ok'] else 1)
