"""Experimental topsApp pair adapter; kept separate from ALOS and MintPy."""
import json
import math
from pathlib import Path
import shutil
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from .core import atomic_json,digest,find_conda,safe_path
from .inputs import prepare_raster
from .onboarding import inspect_terrain
from .pair import fingerprint as basic_fingerprint
from .sentinel import inspect_pair,overlap,union
from .sentinel_orbits import download_orbit,validate_orbit
from .runtime import workbench_home

STEPS=[('startup','初始化'),('preprocess','讀取 SAFE 與 burst'),('computeBaselines','確認共同 burst 與基線'),('verifyDEM','核對 DEM'),('topo','雷達地理幾何'),('subsetoverlaps','提取 burst 重疊'),('coarseoffsets','粗對位偏移'),('coarseresamp','粗重採樣'),('overlapifg','重疊區干涉'),('prepesd','準備 ESD'),('esd','ESD 精細方位對位'),('rangecoreg','距離向對位'),('fineoffsets','精對位偏移'),('fineresamp','精重採樣'),('ion','電離層步驟（本版關閉）'),('burstifg','逐 burst 干涉'),('mergebursts','合併 burst'),('filter','濾波及相干性'),('unwrap','SNAPHU 解纏'),('unwrap2stage','二階解纏（本版關閉）'),('geocode','地理編碼')]
FIELDS=[
 dict(key='range looks',label='距離向 looks',value=19,min=1,max=128,help='沿用所選工作台基準 19；增加會降低解析度並減少輸出量。'),
 dict(key='azimuth looks',label='方位向 looks',value=7,min=1,max=128,help='工作台基準 7；與距離向共同決定多視解析度。'),
 dict(key='filter strength',label='干涉圖濾波強度',value=.5,min=0,max=1,help='Goldstein 濾波；加強平滑可能犧牲細節，並非越高越準確。'),
 dict(key='ESD coherence threshold',label='ESD 相干性門檻',value=.85,min=0,max=1,help='僅以較可靠的重疊像素估計方位位移；太高可能沒有足夠樣本。')]
GEOCODE=['merged/filt_topophase.flat','merged/phsig.cor','merged/filt_topophase.unw','merged/filt_topophase.unw.conncomp']


def fingerprint(paths):
    result=basic_fingerprint(paths)
    for row in result:
        if Path(row['path']).suffix in ('.safe','.py','.json','.EOF'):row['sha256']=digest(row['path'])
    return result


def app_path(env):
    paths=sorted({p.resolve() for p in (Path(env)/'lib').glob('python*/site-packages/isce/applications/topsApp.py')})
    if len(paths)!=1:raise ValueError('所選環境找不到唯一的 topsApp.py。')
    return paths[0]


def normalize(data):
    c={k:data.get(k) for k in ('name','root','env','reference_safe','secondary_safe','dem','orbit_mode','reference_orbit','secondary_orbit','roi','swaths','polarization','experimental_ack')}
    c['workflow']='topsApp'
    for key in ('root','env','reference_safe','secondary_safe','dem'):c[key]=str(safe_path(c[key] or '')) if c[key] else ''
    c['name']=str(c['name'] or 'Sentinel-1 雙日期').strip()[:100]
    c['polarization']=str(c['polarization'] or 'VV').upper()
    if c['polarization'] not in ('VV','VH','HH','HV'):raise ValueError('請選擇共同極化。')
    if not isinstance(c['swaths'],list) or not c['swaths'] or any(type(v)!=int or v not in (1,2,3) for v in c['swaths']) or len(set(c['swaths']))!=len(c['swaths']):raise ValueError('請至少選擇一個 IW 子波束。')
    c['swaths']=sorted(c['swaths'])
    if c['swaths']!=list(range(min(c['swaths']),max(c['swaths'])+1)):raise ValueError('多個子波束請選相鄰範圍。')
    if c['roi']:
        box=c['roi']
        if not isinstance(box,list) or len(box)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in box) or not -85<box[0]<box[1]<85 or not -180<box[2]<box[3]<180:raise ValueError('範圍需為有效的南、北、西、東界。')
    if c['orbit_mode'] not in ('download','local'):raise ValueError('請選擇取得精密軌道的方式。')
    props=data.get('properties',{})
    if not isinstance(props,dict) or set(props)-{f['key'] for f in FIELDS}:raise ValueError('不支援的 TOPS 處理參數。')
    c['properties']={}
    for f in FIELDS:
        n=float(props.get(f['key'],f['value']))
        if not math.isfinite(n) or not f['min']<=n<=f['max'] or ('looks' in f['key'] and not n.is_integer()):raise ValueError(f['label']+'超出範圍。')
        c['properties'][f['key']]=int(n) if 'looks' in f['key'] else n
    return c


def preflight(data,existing=False):
    c=normalize(data);checks=[];result=dict(config=c,checks=checks,can_proceed=False)
    def add(status,title,detail):checks.append(dict(status=status,title=title,detail=str(detail)))
    try:
        if not all(c[k] for k in ('root','env','reference_safe','secondary_safe','dem')):raise ValueError('請選影像、專案、環境與 DEM。')
        root=Path(c['root']);runtime=(workbench_home()/'runtime').resolve()
        if root==runtime or runtime in root.parents or root in runtime.parents:raise ValueError('專案請放在工具 runtime 以外。')
        inputs=[Path(c[k]) for k in ('env','reference_safe','secondary_safe','dem')]
        for p in inputs:
            if p==root or p in root.parents or root in p.parents:raise ValueError('專案不可與來源／環境互相包含。')
        if root.exists() and (not root.is_dir() or not existing and any(root.iterdir())):raise ValueError('請選新的空專案資料夾。')
        parent=next(p for p in (root,*root.parents) if p.exists())
        import os
        if not os.access(parent,os.W_OK|os.X_OK):raise ValueError('專案位置不可寫入。')
        app=app_path(c['env']);c['app']=str(app)
        if not Path(c['env'],'bin/python').is_file() or not find_conda(c['env']):raise ValueError('找不到環境的 Python／Conda。')
        subdirs={json.loads(p.read_text()).get('subdir') for p in Path(c['env'],'conda-meta').glob('*.json')}-{None,'noarch',''}
        if len(subdirs)>1:raise ValueError('所選環境的 Conda 套件架構混用，請選单一架構環境。')
        add('pass' if subdirs else 'warning','ISCE2 環境',str(app)+'；套件標記：'+(', '.join(sorted(subdirs)) or '未確認'))
        pair=inspect_pair(c['reference_safe'],c['secondary_safe']);result['pair']=pair
        channels=[ch for ch in pair['common'] if ch['polarization']==c['polarization'] and ch['swath'] in c['swaths']]
        if {ch['swath'] for ch in channels}!=set(c['swaths']):raise ValueError('所選子波束／極化並非兩期共同具有。')
        selected=[]
        for ch in channels:
            selection={k:[b for b in ch[k+'_bursts'] if not c['roi'] or overlap(b['bounds'],c['roi'])] for k in ('reference','secondary')}
            if any(len(v)<2 for v in selection.values()):raise ValueError(f"IW{ch['swath']} 選區不足兩個候選 burst；本版保留 ESD，請擴大範圍。")
            selected.extend(b['bounds'] for v in selection.values() for b in v)
        result['dem_bounds']=union(selected)
        dem=inspect_terrain(c['dem'],'dem');b=dem['bounds'];a=result['dem_bounds']
        if not (b[0]<=a[0] and b[1]>=a[1] and b[2]<=a[2] and b[3]>=a[3]):raise ValueError('DEM 未涵蓋選定 burst 的完整概略範圍；請加緩衝或換 DEM。')
        add('pass','影像與地形',f"{pair['days']} 天 · 相對軌道 {pair['reference']['relative_orbit']} · {pair['reference']['direction']} · {c['polarization']} · IW {c['swaths']}；DEM 已涵蓋候選 burst。")
        if c['orbit_mode']=='local':
            for key in ('reference','secondary'):
                c[key+'_orbit']=str(safe_path(c[key+'_orbit'] or ''))
                validate_orbit(c[key+'_orbit'],pair[key])
            add('pass','精密軌道','两期 EOF 的衛星、有效期與狀態向量已核對。')
        else:add('warning','精密軌道','準備時從 ESA 官方 POEORB 下載到專案內；不可用就停止，不退回 annotation 軌道。')
        size=sum(pair[k]['bytes'] for k in ('reference','secondary'));free=shutil.disk_usage(parent).free
        if free<2*1024**3:raise ValueError('可用空間低於基本 2 GB 緩衝。')
        add('warning' if free<size*6 else 'pass','磁碟空間',f'可用 {free/1024**3:.1f} GB；粗估至少預留輸入量 6 倍 {size*6/1024**3:.1f} GB，實際與選區和 looks 有關，並非完成保證。')
        add('warning','真實影像驗證狀態','試驗功能：尚未以完整兩期 SLC 跑通；僅完成格式、上游設定及小型測試。階段失敗保留輸出，不自動重跑。')
        if c['experimental_ack'] is not True:raise ValueError('請先確認試驗功能的驗證限制。')
        result['can_proceed']=True
    except Exception as error:add('error','需修正',error)
    return result


def write_xml(c,path):
    root=ET.Element('topsApp');comp=ET.SubElement(root,'component',name='topsinsar')
    def props(parent,values):
        for k,v in values.items():ET.SubElement(parent,'property',name=k).text=str(v)
    props(comp,{'sensor name':'SENTINEL1','demFilename':c['dem'],'geocode demfilename':c['dem'],'swaths':repr(c['swaths']),
                'do ESD':'True','do unwrap':'True','unwrapper name':'snaphu_mcf','do unwrap 2 stage':'False',
                'do dense offsets':'False','use GPU':'False','use virtual files':'True','do interferogram':'True',
                'do ionosphere correction':'False','apply ionosphere correction':'False','renderer':'xml',
                'pickle dump directory':'PICKLE','pickle load directory':'PICKLE','geocode list':repr(GEOCODE),**c['properties']})
    if c.get('roi'):props(comp,{'region of interest':repr(c['roi']),'geocode bounding box':repr(c['roi'])})
    for key in ('reference','secondary'):
        sensor=ET.SubElement(comp,'component',name=key)
        props(sensor,{'safe':repr([c[key+'_safe']]),'polarization':c['polarization'].lower(),'orbit file':c[key+'_orbit'],'output directory':key})
    ET.indent(root);ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)


class TopsWorkflow:
    def __init__(self,engine):self.e=engine
    def create(self,data):
        r=preflight(data)
        if not r['can_proceed']:raise ValueError('；'.join(x['detail'] for x in r['checks'] if x['status']=='error'))
        c=r['config'];root=Path(c['root']);files=[]
        for key in ('reference','secondary'):files+=r['pair'][key]['files']
        files += [c['dem'],c['dem']+'.xml',c['app']]
        if Path(c['dem']+'.vrt').exists():files.append(c['dem']+'.vrt')
        files+=list(Path(c['env'],'conda-meta').glob('*.json'))
        if c['orbit_mode']=='local':files += [c['reference_orbit'],c['secondary_orbit']]
        snapshot=fingerprint(files)
        c.update(reference=r['pair']['reference']['date'][2:],secondary=r['pair']['secondary']['date'][2:],source=c['reference_safe'],conda=find_conda(c['env']))
        root.mkdir(parents=True,exist_ok=True);(root/'proc').mkdir();(root/'.helper').mkdir()
        s=dict(id=uuid.uuid4().hex[:12],config=c,dates=[dict(date=c[k]) for k in ('reference','secondary')],tops_inventory=r['pair'],status='new',stage='TOPS 試驗專案已建立',history=[],excluded=[],completed_steps=[],tops_steps=[k for k,_ in STEPS],source_fingerprint=snapshot)
        atomic_json(root/'.helper/tops-preflight.json',r);self.e.save(s);self.e.register(s['id'],root)
        return s
    def verify(self,s):
        if fingerprint(p['path'] for p in s['source_fingerprint'])!=s['source_fingerprint']:raise ValueError('來源、DEM 或環境紀錄已改變，請建立新專案。')
        if s.get('hashes'):self.e.verify_commands(s)
    def can_retry(self,s):return s['status']=='failed' and s.get('failed_action')=='prepare' and not (self.e.paths(s)[1]/'PICKLE').exists()
    def validate_action(self,s,action,options):
        expected={'prepare':'new','inspect':'ready','run':'tops_ready','export':'processed'}
        if not (action=='export' and s['status']=='failed' and len(s.get('completed_steps',[]))==len(STEPS)) and s['status']!=expected.get(action) and not (action=='prepare' and self.can_retry(s)):raise ValueError('目前階段不允許此 TOPS 操作；失敗步驟不可直接重跑。')
        if action in ('inspect','run') and options.get('confirmed') is not True:raise ValueError('請確認這是尚未完成真實影像驗證的試驗執行。')
    def science(self,s,mode):self.e.run(s,['python',str(Path(__file__).with_name('tops_worker.py')),mode,str(self.e.paths(s)[2]/'state.json')],conda=True)
    def step(self,s,key,label):
        self.verify(s);proc=self.e.paths(s)[1];done=s['completed_steps'];order=[k for k,_ in STEPS]
        if done!=order[:len(done)] or key!=order[len(done)]:raise ValueError('TOPS 階段紀錄不連續。')
        if (proc/'PICKLE'/key).exists() or (proc/'PICKLE'/(key+'.xml')).exists():raise ValueError('此步驟已有輸出，停止以避免覆寫。')
        if done:
            for name,sha in s['checkpoint_hashes'].items():
                if digest(proc/name)!=sha:raise ValueError('前一步檢查點已改變。')
        if shutil.disk_usage(proc).free<2*1024**3:raise ValueError('剩餘磁碟空間不足，已保留目前結果。')
        self.e.checkpoint(s,f'{len(done)+1}/{len(STEPS)} · '+label,lambda:self.e.run(s,['python',s['config']['app'],'topsApp.xml','--dostep='+key],conda=True))
        paths=[proc/'PICKLE'/key,proc/'PICKLE'/(key+'.xml')]
        if any(not p.is_file() or p.stat().st_size==0 for p in paths):raise ValueError('缺少 TOPS 完整階段檢查點。')
        s['checkpoint_hashes']={str(p.relative_to(proc)):digest(p) for p in paths};done.append(key);self.e.save(s)
    def worker(self,s,action):
        e=self.e;root,proc,meta=e.paths(s);c=s['config'];self.verify(s)
        if action=='prepare':
            staged=dict(c);orbits=[]
            for key in ('reference','secondary'):
                if c['orbit_mode']=='download':
                    if e.cancel.is_set():raise ValueError('已停止準備。')
                    result=download_orbit(s['tops_inventory'][key],meta/'orbits');staged[key+'_orbit']=result['path'];orbits.append(result)
            staged['dem']=prepare_raster(c['dem'],meta/'inputs/dem')
            write_xml(staged,proc/'topsApp.xml');s['prepared_inputs']=staged;e.save(s)
            self.science(s,'resolve');s['resolved']=json.loads((meta/'tops-resolved.json').read_text())
            tracked=[proc/'topsApp.xml',meta/'tops-resolved.json',Path(c['app'])]+[p for p in (meta/'inputs').rglob('*') if p.suffix in ('.xml','.vrt')]+[Path(staged[k+'_orbit']) for k in ('reference','secondary')]
            s['hashes']={str(p.resolve()):digest(p) for p in tracked};atomic_json(meta/'orbit-sources.json',orbits)
            s.update(status='ready',stage='設定與精密軌道已確認，可試驗讀取影像')
        elif action=='inspect':
            for key,label in STEPS[:3]:self.step(s,key,label)
            self.science(s,'geometry');s['tops_geometry']=json.loads((meta/'tops-geometry.json').read_text())
            s.update(status='tops_ready',stage='ISCE2 已確認共同 burst；請核對後繼續')
        elif action=='run':
            for key,label in STEPS[3:]:self.step(s,key,label)
            s['status']='processed';e.save(s)
            self.science(s,'results');s['tops_results']=json.loads((meta/'tops-results.json').read_text())
            s.update(status='complete',stage='TOPS 處理完成，請獨立驗證科學品質')
        elif action=='export':
            self.science(s,'results');s['tops_results']=json.loads((meta/'tops-results.json').read_text());s.update(status='complete')
        self.verify(s)
    def artifact(self,s,name):
        if name not in s.get('tops_results',{}).get('artifacts',[]) or Path(name).name!=name:raise ValueError('成果不存在。')
        base=self.e.paths(s)[2]/'tops-exports';p=base/name
        if base.is_symlink() or p.is_symlink() or not p.is_file():raise ValueError('無效成果。')
        return p
