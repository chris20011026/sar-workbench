"""ALOS-2 pair adapter. Only project-owned outputs are ever written."""
from pathlib import Path
import datetime
import json
import math
import re
import shutil
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from .core import atomic_json, digest, inspect_data, safe_path
from .inputs import prepare_raster

WORKER = Path(__file__).with_name('pair_worker.py')
# Keep the sequence identical to alos2App; disabled ion steps still save checkpoints.
STEPS = [
 ('startup','初始化'), ('preprocess','讀取兩期影像'), ('baseline','計算基線與幾何'),
 ('download_dem','載入本機 DEM 與水體'), ('prep_slc','準備共同覆蓋影像'),
 ('slc_offset','估計影像對位偏移'), ('form_int','形成干涉圖'),
 ('swath_offset','估計子測幅偏移'), ('swath_mosaic','拼接子測幅'),
 ('frame_offset','估計相鄰 frame 偏移'), ('frame_mosaic','拼接 frame'),
 ('rdr2geo','計算經緯度與高程'), ('geo2rdr','地理位置轉雷達座標'),
 ('rdrdem_offset','雷達影像與地形對位'), ('rect_rgoffset','修正距離偏移'),
 ('diff_int','移除地形相位'), ('look','多視處理'), ('coherence','估計相干性'),
 ('ion_subband','建立電離層子頻帶'), ('ion_unwrap','子頻帶解纏'),
 ('ion_filt','估計與濾波電離層'), ('ion_correct','處理電離層校正'),
 ('filt','干涉圖濾波'), ('unwrap','SNAPHU 解纏'), ('geocode','地理編碼')]
FIELDS = [
 dict(key='polarization',label='極化方式',type='choice',choices=['HH','HV','VH','VV'],value='HH',help='兩個日期使用相同極化，必須都有對應影像。'),
 dict(key='do ionospheric phase estimation',label='估計電離層',type='bool',value='True',help='啟用時在估計完成後停下，等你判讀。'),
 dict(key='apply ionospheric phase correction',label='套用電離層校正',type='bool',value='True',help='估計關閉時不執行；檢查點仍可決定保留或不套用。'),
 *[dict(key='number of '+axis+' looks '+stage,label=label,type='int',min=1,max=256,value='',help='留白由安裝的 ISCE2 依影像模式決定。') for stage,title in [('1','第一階段'),('2','第二階段'),('ion','電離層')] for axis,label in [('range','Range looks · '+title),('azimuth','Azimuth looks · '+title)]],
 dict(key='interferogram filter strength',label='干涉圖濾波強度',type='float',min=0,max=1,value='',help='留白用 ISCE2 預設；值越高不代表成果越準確。'),
 dict(key='interferogram filter window size',label='干涉圖濾波視窗',type='int',min=4,max=512,value='',help='像素數；須為 2 的次方。'),
 dict(key='interferogram filter step size',label='干涉圖濾波步距',type='int',min=1,max=512,value='',help='像素數；不可大於濾波視窗。'),
 dict(key='water body mask starting step',label='水體遮罩開始階段',type='choice',choices=['unwrap','filt','None'],value='unwrap',help='unwrap：解纏時遮罩；filt：濾波時即遮罩；None：不套用。'),
 dict(key='crop slc',label='裁至兩期共同覆蓋',type='bool',value='False',help='這是兩期影像的重疊範圍，不是自訂研究區裁切。'),
 dict(key='geocode interpolation method',label='地理編碼插值',type='choice',choices=['','bilinear','nearest','bicubic','sinc'],value='',help='留白沿用 ISCE2 依資料類型選擇的方式。'),
 dict(key='maximum window size for filtering ionosphere phase',label='電離層濾波最大視窗',type='int',min=3,max=1001,value='',help='奇數；留白使用安裝版本預設。'),
 dict(key='minimum window size for filtering ionosphere phase',label='電離層濾波最小視窗',type='int',min=3,max=1001,value='',help='奇數；不能大於最大視窗。'),
]
# Public names are checked against the installed processor during prepare.
MANAGED = {'reference directory','secondary directory','reference frames','secondary frames','reference polarization','secondary polarization','dem for coregistration','dem for geocoding','water body','geocode bounding box','use GPU','do InSAR','do dense offset','use virtual file','renderer','pickle dump directory','pickle load directory'}
MODES = {'UBS','UBD','HBS','HBD','HBQ','FBS','FBD','FBQ'}

def normalize_options(raw):
    known={f['key']:f for f in FIELDS}
    if not isinstance(raw,dict) or set(raw)-set(known):
        raise ValueError('此版雙日期流程不支援這些參數：'+', '.join(sorted(set(raw)-set(known))) if isinstance(raw,dict) else '處理設定格式不正確。')
    result={}
    for key,f in known.items():
        v=str(raw.get(key,f['value'])).strip()
        if not v:
            continue
        if f['type']=='bool':
            if v.lower() not in ('true','false'):raise ValueError(f['label']+'請選啟用或關閉。')
            v=v.title()
        elif f['type']=='choice':
            if v not in f['choices']:raise ValueError(f['label']+'選項不合法。')
        else:
            try:n=float(v)
            except ValueError:raise ValueError(f['label']+'必須是數字。')
            if not math.isfinite(n) or not f['min']<=n<=f['max'] or (f['type']=='int' and not n.is_integer()):raise ValueError(f['label']+'數值超出範圍。')
            v=str(int(n)) if f['type']=='int' else str(n)
        result[key]=v
    win=int(result.get('interferogram filter window size',32));step=int(result.get('interferogram filter step size',4))
    if win&(win-1) or step>win:raise ValueError('濾波視窗須為 2 的次方，步距不可大於視窗。')
    lo=int(result.get('minimum window size for filtering ionosphere phase',11));hi=int(result.get('maximum window size for filtering ionosphere phase',301))
    if lo>hi or lo%2==0 or hi%2==0:raise ValueError('電離層視窗須為奇數，最小值不可大於最大值。')
    return result

def bounds(value):
    if not value:return None
    if not isinstance(value,list) or len(value)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in value):raise ValueError('範圍需為南、北、西、東四個數字。')
    s,n,w,e=value
    if not -85<s<n<85 or not -180<w<e<180:raise ValueError('地理編碼範圍無效。')
    return value

def write_pair_xml(c,path,apply=None):
    values={k:v for k,v in c['properties'].items() if k!='polarization'}
    pol=c['properties'].get('polarization','HH')
    values.update({'reference directory':str(Path(c['data'])/c['reference']), 'secondary directory':str(Path(c['data'])/c['secondary']),
      'reference polarization':pol,'secondary polarization':pol,'reference frames':repr(c['frames']),'secondary frames':repr(c['frames']),
      'dem for coregistration':c['dem'],'dem for geocoding':c['dem_geo'],'water body':c['wbd'],
      'do InSAR':'True','do dense offset':'False','use GPU':'False','use virtual file':'True',
      'renderer':'xml','pickle dump directory':'PICKLE','pickle load directory':'PICKLE'})
    if c.get('bbox'):values['geocode bounding box']=repr(c['bbox'])
    if apply is not None:values['apply ionospheric phase correction']=str(apply)
    root=ET.Element('alos2App');comp=ET.SubElement(root,'component',name='alos2insar')
    for k,v in values.items():ET.SubElement(comp,'property',name=k).text=str(v)
    ET.indent(root);ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)

def import_pair_xml(path):
    root=ET.parse(path).getroot();comp=root.find(".//component[@name='alos2insar']")
    if comp is None:raise ValueError('請選 alos2App 的 alos2insar 設定；alosStack 範本不能直接套用。')
    props={}
    for p in comp.findall('property'):
        key=p.get('name');value=p.findtext('value') if p.find('value') is not None else p.text
        if key in props:raise ValueError('XML 含重複參數：'+key)
        props[key]=(value or '').strip()
    import ast
    settings={k:v for k,v in props.items() if k not in MANAGED}
    pol=props.get('reference polarization','HH')
    if props.get('secondary polarization',pol)!=pol:raise ValueError('此版只處理相同極化的兩期影像。')
    settings['polarization']=pol
    settings=normalize_options(settings)
    bbox=bounds(ast.literal_eval(props['geocode bounding box'])) if props.get('geocode bounding box') else None
    return dict(properties=settings,bbox=bbox,notice='已匯入處理參數與地理編碼範圍。資料、環境與 frame 請在表單另選；輸出及執行方式由工作台管理。')

def app_path(env):
    files=sorted({p.resolve() for p in (Path(env)/'lib').glob('python*/site-packages/isce/applications/alos2App.py')})
    if len(files)!=1:raise ValueError('所選環境無法唯一找到 alos2App.py。')
    return files[0]

def fingerprint(paths):
    result=[]
    for f in sorted(set(map(str,paths))):
        p=Path(f);st=p.stat();row=dict(path=f,size=st.st_size,mtime_ns=st.st_mtime_ns)
        if p.suffix in ('.xml','.vrt'):row['sha256']=digest(p)
        result.append(row)
    return result

class PairWorkflow:
    def __init__(self,engine):self.e=engine
    def create(self,data):
        c=dict(data);c['workflow']='alos2App';c['properties']=normalize_options(c.get('properties',{}));c['bbox']=bounds(c.get('bbox'))
        root=safe_path(c['root'])
        if root.exists() and (not root.is_dir() or any(root.iterdir())):raise ValueError('雙日期處理請使用新的空專案資料夾。')
        for key in ('source','env','dem','dem_geo','wbd'):c[key]=str(safe_path(c[key]))
        for key in ('source','env','dem','dem_geo','wbd'):
            p=Path(c[key])
            if p==root or p in root.parents or root in p.parents:raise ValueError('專案不可與輸入或環境資料夾互相包含。')
        if not Path(c['env'],'bin/python').is_file():raise ValueError('環境缺少 Python。')
        c['app']=str(app_path(c['env']))
        dates={x['date']:x for x in inspect_data(c['source'])}
        ref=c.get('reference');sec=c.get('secondary')
        if ref not in dates or sec not in dates or ref>=sec:raise ValueError('請選擇兩個不同日期；參考日期必須早於比較日期。')
        selected=[dates[ref],dates[sec]];pol=c['properties'].get('polarization','HH')
        for row in selected:
            if row['leaders']!=len(row['frames']) or not row['leaders'] or len(row['modes'])!=1:raise ValueError(row['date']+' 的 LED 或觀測模式不唯一。')
            mode=row['modes'][0]
            if mode[:3] not in MODES or '1.1' not in mode:raise ValueError('此版 alos2App 先支援 ALOS-2 Level 1.1 Stripmap；ScanSAR、Spotlight 或其他產品尚未驗證。')
            if pol not in row['polarizations']:raise ValueError(row['date']+' 缺少 '+pol+' 極化。')
        if selected[0]['modes']!=selected[1]['modes']:raise ValueError('此版需兩期使用相同觀測模式與方向。')
        frames=c.get('frames') or selected[0]['frames']
        if not isinstance(frames,list) or not frames or len(frames)!=len(set(frames)) or any(not isinstance(x,str) or not re.fullmatch(r'\d{4}',x) for x in frames):raise ValueError('請選至少一個有效 frame。')
        if not c.get('frames') and selected[0]['frames']!=selected[1]['frames']:raise ValueError('兩期 frame 不同，請勾選共同的 frame。')
        if any(set(frames)-set(x['frames']) for x in selected):raise ValueError('選擇的 frame 必須在兩個日期都存在。')
        c['frames']=sorted(frames);files=[]
        for row in selected:
            folder=Path(c['source'])/row['date']
            for frame in c['frames']:
                for pattern in ('LED-ALOS2*'+frame+'-'+row['date']+'-*','IMG-'+pol+'-ALOS2*'+frame+'-'+row['date']+'-*'):
                    found=list(folder.glob(pattern))
                    if len(found)!=1 or not found[0].stat().st_size:raise ValueError('缺少或重複的影像：'+row['date']+' frame '+frame)
                    files.append(found[0])
        for key in ('dem','dem_geo','wbd'):
            f=Path(c[key])
            if not f.is_file() or not Path(str(f)+'.xml').is_file():raise ValueError(key+' 需有 raster 本體與同名 XML。')
            files.extend([f,Path(str(f)+'.xml')])
            if Path(str(f)+'.vrt').exists():files.append(Path(str(f)+'.vrt'))
        from .core import find_conda
        conda=find_conda(c['env'])
        if not conda:raise ValueError('找不到 Conda。')
        c['name']=str(c.get('name','ALOS-2 雙日期')).strip()[:100]
        c.update(root=str(root),data=str(root/'data') if c.get('copy') else c['source'],conda=conda)
        with tempfile.TemporaryDirectory() as td:write_pair_xml(c,Path(td)/'check.xml')
        snapshot=fingerprint(files)
        root.mkdir(parents=True,exist_ok=True);(root/'proc').mkdir();(root/'.helper').mkdir()
        write_pair_xml(c,root/'proc/alos2App.xml')
        s=dict(id=uuid.uuid4().hex[:12],config=c,dates=selected,status='new',stage='雙日期專案已建立，等待準備',history=[],excluded=[],completed_steps=[],source_fingerprint=snapshot)
        self.e.save(s);self.e.register(s['id'],root)
        return s
    def verify(self,s):
        if fingerprint(x['path'] for x in s['source_fingerprint'])!=s['source_fingerprint']:raise ValueError('來源資料或描述檔已變更，請重新建立專案。')
        if s.get('copied_fingerprint') and fingerprint(x['path'] for x in s['copied_fingerprint'])!=s['copied_fingerprint']:
            raise ValueError('專案內複製的影像已改變。')
        if s.get('hashes'):self.e.verify_commands(s)
    def can_retry(self,s):
        proc=self.e.paths(s)[1]
        return s['status']=='failed' and s.get('failed_action')=='prepare' and not s.get('completed_steps') and not (proc/'PICKLE').exists()
    def validate_action(self,s,action,data):
        expected={'prepare':'new','inspect':'ready','run':'pair_ready','continue':'review'}
        if s['status']!=expected.get(action) and not (action=='prepare' and self.can_retry(s)):raise ValueError('目前階段不允許這個雙日期操作。')
        if action=='continue':
            if data.get('reviewed') is not True or type(data.get('apply_ion')) is not bool:raise ValueError('請先確認電離層圖片並選擇是否套用校正。')
            if not s.get('ion',{}).get('estimate'):raise ValueError('沒有電離層估計可供判讀。')
            s['review_decision']=dict(apply=data['apply_ion'],time=time.strftime('%Y-%m-%d %H:%M:%S'))
    def science(self,s,mode):
        self.e.run(s,['python',str(WORKER),mode,str(self.e.paths(s)[2]/'state.json')],conda=True)
    def step(self,s,key,label):
        self.verify(s);proc=self.e.paths(s)[1];done=s['completed_steps'];order=[x[0] for x in STEPS]
        if done!=order[:len(done)] or key!=order[len(done)]:raise ValueError('階段紀錄不連續，已停止。')
        if (proc/'PICKLE'/key).exists() or (proc/'PICKLE'/(key+'.xml')).exists():raise ValueError('此步驟已有輸出，請診斷後建立新專案，不自動覆寫。')
        if done:
            for p in (proc/'PICKLE'/done[-1],proc/'PICKLE'/(done[-1]+'.xml')):
                if digest(p)!=s['checkpoint_hashes'].get(str(p.relative_to(proc))):raise ValueError('前一步檢查點已改變。')
        config='alos2App.review.xml' if s.get('review_decision') else 'alos2App.xml'
        self.e.checkpoint(s,f'{len(done)+1}/{len(STEPS)} · '+label,lambda:self.e.run(s,['python',s['config']['app'],config,'--dostep='+key],conda=True))
        saved=[proc/'PICKLE'/key,proc/'PICKLE'/(key+'.xml')]
        if any(not f.is_file() or not f.stat().st_size for f in saved):raise ValueError('ISCE2 未留下完整階段檢查點：'+key)
        s['checkpoint_hashes']={str(f.relative_to(proc)):digest(f) for f in saved}
        done.append(key);self.e.save(s)
    def worker(self,s,action):
        e=self.e;root,proc,meta=e.paths(s);c=s['config']
        if action=='prepare':
            self.verify(s)
            staged=dict(c)
            for key in ('dem','dem_geo','wbd'):staged[key]=prepare_raster(c[key],meta/'inputs'/key)
            if c.get('copy'):
                raw=[Path(x['path']) for x in s['source_fingerprint'] if Path(x['path']).parent.parent==Path(c['source'])]
                if shutil.disk_usage(root).free<sum(p.stat().st_size for p in raw)+1024**3:raise ValueError('磁碟空間不足以複製選定影像。')
                for f in raw:
                    target=Path(c['data'])/f.relative_to(c['source']);target.parent.mkdir(parents=True,exist_ok=True)
                    e.run(s,['rsync','-a','--partial',str(f),str(target)])
                    if digest(f)!=digest(target):raise ValueError('資料複製驗證失敗：'+f.name)
                s['copied_fingerprint']=fingerprint(Path(c['data'])/p.relative_to(c['source']) for p in raw)
            write_pair_xml(staged,proc/'alos2App.xml');s['prepared_inputs']={k:staged[k] for k in ('dem','dem_geo','wbd')};e.save(s)
            self.science(s,'resolve');s['resolved']=json.loads((meta/'pair-resolved.json').read_text());s['ion']=s['resolved']['ion']
            tracked=[proc/'alos2App.xml',meta/'pair-resolved.json',Path(c['app'])]+[p for p in (meta/'inputs').rglob('*') if p.suffix in ('.xml','.vrt')]
            s['hashes']={str(p.relative_to(proc)) if p.is_relative_to(proc) else str(p):digest(p) for p in tracked}
            s.update(status='ready',stage='設定已驗證，可讀取影像與基線')
        elif action=='inspect':
            for key,label in STEPS[:3]:self.step(s,key,label)
            self.science(s,'metadata');s['pair_metadata']=json.loads((meta/'pair-metadata.json').read_text())
            s.update(status='pair_ready',stage='影像與基線檢查完成，請確認範圍後開始')
        else:
            self.verify(s)
            if action=='continue':
                staged={**c,**s['prepared_inputs']};write_pair_xml(staged,proc/'alos2App.review.xml',s['review_decision']['apply'])
                s['hashes']['alos2App.review.xml']=digest(proc/'alos2App.review.xml')
                atomic_json(meta/'pair-review-decision.json',s['review_decision']);s['ion']['apply']=s['review_decision']['apply'];e.save(s)
            stop=20 if action=='run' and s['ion']['estimate'] else len(STEPS)-1
            for key,label in STEPS[len(s['completed_steps']):stop+1]:self.step(s,key,label)
            if stop==20:
                self.science(s,'ion');s['pair_review']=json.loads((meta/'pair-ion.json').read_text())
                s.update(status='review',stage='電離層估計完成，等待你檢查')
            else:
                self.science(s,'results');s['pair_results']=json.loads((meta/'pair-results.json').read_text())
                self.verify(s);s.update(status='complete',stage='雙日期干涉處理完成，請檢查成果品質')
        self.verify(s)
    def artifact(self,s,name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('無效成果名稱。')
        names=set()
        for report in ('pair_metadata','pair_review','pair_results'):
            names.update(s.get(report,{}).get('artifacts',[]))
        if name not in names:raise ValueError('成果不存在或尚未完成。')
        p=self.e.paths(s)[2]/'pair-exports'/name
        if not p.is_file() or p.is_symlink():raise ValueError('無效成果檔案。')
        return p
