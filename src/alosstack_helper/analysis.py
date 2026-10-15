"""Independent, immutable analysis runs; research processing state stays untouched."""
from pathlib import Path
import json, os, re, signal, subprocess, threading, time, uuid, shutil, hashlib
from .core import atomic_json, digest, find_conda
from .environment import isolated_env
from .workflows import inventory, options
from .storage import atomic_existing_json

WORKER=Path(__file__).with_name('science_worker.py')

def fingerprint(files):
    out=[]
    for value in sorted(set(files)):
        p=Path(value);st=p.stat()
        row=dict(path=str(p),size=st.st_size,mtime_ns=st.st_mtime_ns)
        if p.suffix in ('.xml','.vrt','.txt'):row['sha256']=digest(p)
        out.append(row)
    return out

def clean_env(cache):
    return isolated_env(cache)

def failure_detail(log_path):
    """Keep the actionable scientific validation error visible next to the run."""
    try:
        with Path(log_path).open('rb') as f:
            f.seek(max(0,Path(log_path).stat().st_size-18000))
            lines=f.read().decode(errors='replace').splitlines()
        for line in reversed(lines):
            for marker in ('ValueError: ', 'RuntimeError: ', '分析失敗：'):
                if marker in line:return line.split(marker,1)[1].strip()[:900]
    except OSError:pass
    return ''

class Analyses:
    def __init__(self, engine):self.engine=engine;self.geo_lock=threading.Lock()
    def base(self,key):
        return Path(self.engine.get(key)['config']['root'])/'analyses'
    def folder(self,key,run):
        if not re.fullmatch(r'[0-9a-f]{12}',run):raise ValueError('無效分析代號。')
        base=self.base(key);p=base/run
        if p.is_symlink() or base.is_symlink():raise ValueError('分析資料夾不能是符號連結。')
        return p
    def interruption(self,key,run):
        path=self.engine.home/'interrupted-analyses'/(key+'-'+run+'.json')
        return json.loads(path.read_text()) if path.exists() else {}
    def get(self,key,run):
        job=json.loads((self.folder(key,run)/'analysis.json').read_text())
        job.update(self.interruption(key,run));return job
    def listing(self,key):
        base=self.base(key)
        if not base.exists():return []
        result=[]
        for f in sorted(base.glob('*/analysis.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            try:
                d=json.loads(f.read_text());d.update(self.interruption(key,f.parent.name));result.append({k:d.get(k) for k in ['id','kind','status','stage','created','error','options','environment','history']})
            except (ValueError,OSError):continue
        return result
    def reconcile(self):
        if self.engine.active:return
        for key in self.engine.projects:
            try:
                if self.engine.get(key).get('storage',{}).get('relocated'): continue
                items = self.listing(key)
            except (OSError, ValueError, KeyError): continue
            for item in items:
                if item['status']!='running':continue
                p=self.folder(key,item['id']);pid=p/'worker.pid'
                if pid.exists():
                    try:os.kill(int(pid.read_text()),0)
                    except ProcessLookupError:pass
                    except (ValueError,PermissionError):return
                    else:return  # Existing process is left alone; reserve rejects a second job.
                job=self.get(key,item['id']);job.update(status='failed',stage='服務曾中斷',error='保留現有輸出。請檢查分析日誌後建立新版本，不會自動接續半成品。');atomic_existing_json(p/'analysis.json',job)
    def reserve(self):
        self.reconcile()
        if any(r['status']=='running' for s in self.engine.list() for r in self.listing(s['id'])):
            raise ValueError('仍有背景分析，請確認原本的工作已結束。')
        if self.engine.active:raise ValueError('已有工作執行中，請等待完成。')
    def create(self,key,kind,data):
        if kind not in ('quality','mintpy'):raise ValueError('未知分析種類。')
        with self.engine.lock:
            self.reserve();s=self.engine.require_processing_location(key,check_inputs=False);inv=inventory(s)
            env=Path(data.get('environment') or s['config']['env']).expanduser().resolve()
            if not (env/'bin/python').is_file():raise ValueError('找不到分析環境的 Python。')
            conda = find_conda(env)
            if not conda:raise ValueError('所選分析環境找不到 Conda。')
            opts=options(data,inv) if kind=='mintpy' else {}
            if opts.get('subset_lalo'):
                resolved=self.geo_call(s,env,'roi',dict(inventory=inv,bounds=opts['subset_lalo']),self.base(key)/'.map-cache')
                opts['subset']=resolved['subset'];opts['region']=resolved
            if kind=='mintpy':
                fraction=1
                if opts.get('subset'):
                    y0,y1,x0,x1=opts['subset'];fraction=(y1-y0)*(x1-x0)/(inv['width']*inv['height'])
                required=sum(Path(r[k]).stat().st_size for r in inv['pairs'] for k in ('unw','coherence','components'))*3*fraction
                if shutil.disk_usage(Path(s['config']['root'])).free < required + 512*1024**2:
                    raise ValueError('可用磁碟空間不足，請預留影像載入與時序結果所需空間。')
            run=uuid.uuid4().hex[:12];p=self.folder(key,run);p.mkdir(parents=True,exist_ok=False)
            try:
                job=dict(id=run,kind=kind,project_id=key,status='running',stage='準備分析',created=time.strftime('%Y-%m-%d %H:%M:%S'),environment=str(env),conda=conda,inventory=inv,options=opts,source_fingerprint=fingerprint(inv['files']),history=[],error='',source_ion=s.get('ion',{}),source_ion_excluded=s.get('excluded',[]))
                atomic_json(p/'analysis.json',job)
            except Exception:
                # Only this newly created empty directory is ours.
                if not any(p.iterdir()):p.rmdir()
                raise
            self.engine.active=key+':'+run;self.engine.cancel.clear()
            threading.Thread(target=self.worker,args=(key,run,'quality' if kind=='quality' else 'load'),daemon=True).start()
            return job
    def invert(self,key,run,data):
        with self.engine.lock:
            self.reserve();self.engine.require_processing_location(key,check_inputs=False);p=self.folder(key,run);job=self.get(key,run)
            if job['kind']!='mintpy' or job['status']!='loaded':raise ValueError('請先完成載入。失敗分析請建立新版本，不覆寫半成品。')
            if not data.get('reviewed'):raise ValueError('請先確認配對、覆蓋與參考設定。')
            if fingerprint(job['inventory']['files'])!=job['source_fingerprint']:raise ValueError('来源影像或 metadata 已改變，請建立新分析版本。')
            ref=data.get('reference_pixel')
            if ref is not None and (not isinstance(ref,list) or len(ref)!=2 or any(type(x)!=int for x in ref)):raise ValueError('參考點需為行、列整數。')
            # Reject invalid points while keeping the loaded checkpoint editable.
            result=subprocess.run(self.command(job,['reference',str(p/'analysis.json'),json.dumps(ref)]),cwd=p,env=clean_env(p/'cache'),capture_output=True,text=True,timeout=30)
            if result.returncode:
                lines=result.stderr.splitlines()+result.stdout.splitlines()
                message=next((x.partition('ValueError: ')[2] for x in lines if 'ValueError: ' in x),'參考點檢查失敗，請選擇共同有效陸地。')
                raise ValueError(message)
            job['reference_pixel']=ref;job.update(status='running',stage='準備反演',error='')
            atomic_json(p/'analysis.json',job);self.engine.active=key+':'+run;self.engine.cancel.clear()
            threading.Thread(target=self.worker,args=(key,run,'invert'),daemon=True).start()
            return job
    def command(self,job,args):
        conda = find_conda(job['environment'])
        if not conda:raise ValueError('所選分析環境已移除，請重新安裝或選擇其他環境。')
        return [conda,'run','--no-capture-output','-p',job['environment'],'python',str(WORKER),*args]
    def worker(self,key,run,mode):
        p=None;job=None;process=None
        try:
            p=self.folder(key,run);job=self.get(key,run)
            cache=p/'cache';cache.mkdir(exist_ok=True)
            with (p/'run.log').open('a') as log:
                command=self.command(job,[mode,str(p/'analysis.json')])
                process=subprocess.Popen(command,cwd=p,env=clean_env(cache),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                self.engine.process=process
                (p/'worker.pid').write_text(str(process.pid))
                if self.engine.cancel.is_set():os.killpg(process.pid,signal.SIGTERM)
                code=process.wait()
            job=self.get(key,run)
            if code or self.engine.cancel.is_set():
                detail=failure_detail(p/'run.log') if code and not self.engine.cancel.is_set() else ''
                raise RuntimeError((detail+'。' if detail else '')+f'分析停止（結束碼 {code}），已保留輸出與日誌，可另建版本。')
            if fingerprint(job['inventory']['files'])!=job['source_fingerprint']:raise RuntimeError('分析期間來源檔案改變，結果不能標示完成。')
            job.update(status='loaded' if mode=='load' else 'complete',stage={'load':'資料已載入，等待確認參考點','invert':'時序分析完成，請檢查品質','quality':'品質檢查完成'}[mode])
        except Exception as e:
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()
                except ProcessLookupError: pass
            if job: job.update(status='failed',stage='需要檢查',error=str(e))
        finally:
            try:
                if p is None or job is None: raise ValueError('分析開始前儲存位置已中斷。')
                self.engine.get(key)  # Check identity before writing to a reconnected path.
                atomic_existing_json(p/'analysis.json',job)
                (p/'worker.pid').unlink(missing_ok=True)
            except (OSError, ValueError) as error:
                atomic_json(self.engine.home/'interrupted-analyses'/(key+'-'+run+'.json'),
                            dict(status='failed',stage='分析儲存中斷，需要檢查',
                                 error='分析狀態無法寫回，已在本機保留紀錄。接回磁碟後可查看產物並另建分析版本；不會自動續跑。 '+str(error)))
            finally:
                with self.engine.lock:self.engine.active=None;self.engine.process=None
    def details(self,key,run):
        p=self.folder(key,run);job=self.get(key,run)
        for name in ('quality','loaded','results'):
            f=p/(name+'.json')
            if f.exists():job[name]=json.loads(f.read_text())
        f=p/'run.log'
        if f.exists():
            with f.open('rb') as h:h.seek(max(0,f.stat().st_size-18000));job['log']=h.read().decode(errors='replace')
        return job
    def artifact(self,key,run,name):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',name):raise ValueError('無效成果名稱。')
        p=self.folder(key,run)/'exports'/name
        if not p.is_file() or p.is_symlink():raise ValueError('找不到成果。')
        return p
    def point(self,key,run,y,x):
        p=self.folder(key,run);job=self.get(key,run)
        if job['status']!='complete' or job['kind']!='mintpy':raise ValueError('請先完成 MintPy。')
        result=subprocess.run(self.command(job,['point',str(p/'analysis.json'),str(int(y)),str(int(x))]),env=clean_env(p/'cache'),cwd=p,capture_output=True,text=True,timeout=30)
        if result.returncode:raise ValueError('此像素無有效時間序列或位置超出範圍。')
        lines=[x[7:] for x in result.stdout.splitlines() if x.startswith('RESULT:')]
        if not lines:raise ValueError('無法讀取時間序列。')
        return json.loads(lines[-1])

    def geo_call(self,s,env,action,spec,cache):
        env=Path(env).expanduser().resolve()
        if not (env/'bin/python').is_file():raise ValueError('找不到所選分析環境。')
        cache.mkdir(parents=True,exist_ok=True)
        conda=find_conda(env)
        if not conda:raise ValueError('所選地圖分析環境找不到 Conda。')
        command=[conda,'run','--no-capture-output','-p',str(env),'python',str(WORKER.with_name('geo_worker.py')),action,json.dumps(spec)]
        result=subprocess.run(command,env=clean_env(cache),capture_output=True,text=True,timeout=240)
        if result.returncode:
            lines=result.stderr.splitlines()+result.stdout.splitlines()
            msg=next((v.split('ValueError: ',1)[1] for v in lines if 'ValueError: ' in v),None)
            raise ValueError(msg or '地圖處理失敗：'+(result.stderr or result.stdout)[-1400:])
        values=[v[7:] for v in result.stdout.splitlines() if v.startswith('RESULT:')]
        if not values:raise ValueError('地圖工具未傳回結果。')
        return json.loads(values[-1])

    def survey(self,key,data):
        s=self.engine.get(key);inv=inventory(s);out=self.base(key)/'.map-cache'
        if out.is_symlink():raise ValueError('地圖快取不可是符號連結。')
        env=data.get('environment') or s['config']['env']
        if data.get('bounds'):
            o=options({'subset_lalo':data['bounds']},inv)
            return self.geo_call(s,env,'roi',dict(inventory=inv,bounds=o['subset_lalo']),out)
        signature=hashlib.sha256(json.dumps(fingerprint(list(inv['geometry'].values()))).encode()).hexdigest()[:12]
        prefix='survey-v4-'+signature;path=out/(prefix+'.json')
        with self.geo_lock:
            if path.exists():result=json.loads(path.read_text())
            else:
                spec=dict(mode='survey',folder=str(out),inventory=inv,out=str(out),prefix=prefix)
                result=self.geo_call(s,env,'map_view',spec,out/'cache')
        # Per-pair baseline differences and quality stay available beside date selection.
        baselines={inv['reference']:0.0}
        for line in Path(inv['baseline']).read_text().splitlines():
            cols=line.split()
            if len(cols)==4 and cols[0].isdigit() and cols[1].isdigit():
                try:baselines[cols[1]]=float(cols[3])
                except ValueError:pass
        quality={}
        for r in self.listing(key):
            if r['kind']=='quality' and r['status']=='complete':
                quality={v['pair']:v for v in self.details(key,r['id']).get('quality',{}).get('rows',[])};break
        result['pair_stats']=[dict(pair=v['pair'],baseline=abs(baselines[v['pair'][:6]]-baselines[v['pair'][7:]]) if all(d in baselines for d in v['pair'].split('-')) else None,coherence=quality.get(v['pair'],{}).get('coherence_median')) for v in inv['pairs']]
        atomic_json(path,result);return result

    def geo_spec(self,key,run):
        p=self.folder(key,run);j=self.get(key,run)
        if j['kind']!='mintpy' or j['status'] not in ('loaded','complete'):raise ValueError('請先載入或完成 MintPy 分析。')
        mode='result' if j['status']=='complete' else 'reference';out=p/'exports'
        names=['inputs/geometryRadar.h5','velocity.h5','temporalCoherence.h5','maskTempCoh.h5'] if mode=='result' else ['inputs/geometryRadar.h5','inputs/ifgramStack.h5']
        stamps=[(n,(p/n).stat().st_size,(p/n).stat().st_mtime_ns) for n in names]
        signature=hashlib.sha256(json.dumps(stamps).encode()).hexdigest()[:12]
        return j,dict(mode=mode,folder=str(p),out=str(out),prefix='map-v5-'+signature)

    def geo_view(self,key,run):
        j,spec=self.geo_spec(key,run);p=Path(spec['out'])/(spec['prefix']+'.json')
        with self.geo_lock:
            if p.exists():return json.loads(p.read_text())
            return self.geo_call(self.engine.get(key),j['environment'],'map_view',spec,p.parent/'map-cache')

    def geo_point(self,key,run,data):
        j,spec=self.geo_spec(key,run)
        if not (Path(spec['out'])/(spec['prefix']+'.json')).exists():raise ValueError('請先載入地圖。')
        spec.update(coordinate=data['coordinate'],threshold=data.get('threshold',j['options']['temporal_coherence']),native=data.get('native') is True)
        return self.geo_call(self.engine.get(key),j['environment'],'pick',spec,Path(spec['out'])/'map-cache')

    def geo_export(self,key,run,data):
        j,spec=self.geo_spec(key,run)
        if j['status']!='complete':raise ValueError('請先完成反演。')
        spec.update({k:data[k] for k in ('layer','resolution','crs','threshold') if k in data})
        with self.geo_lock:return self.geo_call(self.engine.get(key),j['environment'],'export',spec,Path(spec['out'])/'map-cache')

    def geo_figure(self,key,run,data):
        j,spec=self.geo_spec(key,run)
        if j['status']!='complete':raise ValueError('請先完成反演。')
        spec.update({k:data[k] for k in ('layer','vmin','vmax','threshold') if k in data})
        with self.geo_lock:return self.geo_call(self.engine.get(key),j['environment'],'figure',spec,Path(spec['out'])/'map-cache')

    def survey_artifact(self,key,name):
        if not re.fullmatch(r'survey-v4-[a-f0-9]{12}-(height|status)\.bin',name):raise ValueError('無效地圖名稱。')
        p=self.base(key)/'.map-cache'/name
        if p.is_symlink() or not p.is_file():raise ValueError('找不到地圖圖層。')
        return p

    def rename(self,key,run,name):
        name=str(name).strip()
        if not name or len(name)>100:raise ValueError('名稱需為 1–100 個字。')
        with self.engine.lock:
            j=self.get(key,run)
            if j['status']=='running':raise ValueError('請等分析結束後命名。')
            j.setdefault('options',{})['name']=name;atomic_json(self.folder(key,run)/'analysis.json',j)
        return j
