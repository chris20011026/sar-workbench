"""Read-only project design review and reproducible input manifest."""
import copy
import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import uuid

from .core import digest, find_conda, write_xml, atomic_json, declared_pairs
from .environment import isolated_env
from .metadata import enrich
from .onboarding import scan_source, inventory_plan
from .preflight import check_project
from .sampling import selected_frames


def network(pairs, dates):
    adjacency = {d: set() for d in dates}
    for pair in pairs:
        a, b = pair.split('-')
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    remaining = set(adjacency)
    components = []
    while remaining:
        todo, seen = [min(remaining)], set()
        while todo:
            date = todo.pop()
            if date not in seen:
                seen.add(date)
                todo.extend(adjacency[date] - seen)
        remaining -= seen
        components.append(sorted(seen))
    return dict(pairs=sorted(pairs), dates=sorted(adjacency), components=components,
                isolated=sorted(d for d in adjacency if not adjacency[d]),
                connected=bool(pairs) and len(components) == 1)


def versions(prefix):
    rows = []
    for p in sorted((Path(prefix)/'conda-meta').glob('*.json')):
        data = json.loads(p.read_text())
        rows.append({k: data.get(k) for k in ('name','version','build','subdir')})
    return rows


def fingerprint(config):
    """Size/mtime inventory, plus hashes of small descriptors and processor source.

    Deliberately not a full multi-GB image checksum; the limitation is shown in UI.
    """
    source = Path(config['source'])
    paths = [p for d in sorted(source.iterdir()) if d.is_dir() and d.name.isdigit()
             for p in sorted(d.iterdir()) if p.is_file() and p.name.startswith(('LED-', 'IMG-'))]
    small = list(source.glob('*/conversion.json'))
    for key in ('dem','dem_geo','wbd'):
        p = Path(config[key]); paths.append(p)
        small += [Path(str(p)+suffix) for suffix in ('.xml','.vrt') if Path(str(p)+suffix).exists()]
    env = Path(config['env'])
    small += list((env/'conda-meta').glob('*.json'))
    small += [env/'share/isce2/alosStack'/name for name in ('create_cmds.py','StackPulic.py','Stack.py')]
    rows=[]
    for p in sorted(set(paths+small)):
        st=p.stat()
        rows.append(dict(path=str(p),size=st.st_size,mtime_ns=st.st_mtime_ns,
                         **({'sha256':digest(p)} if p in small else {})))
    return rows


def resolve(config):
    with tempfile.TemporaryDirectory(prefix='sar-confirm-') as directory:
        folder=Path(directory); xml=folder/'alosStack.xml'; output=folder/'pairs.json'
        write_xml({**config, 'data':config['source']},xml)
        command=[find_conda(config['env']), 'run','--no-capture-output','-p',config['env'],
                 'python',str(Path(__file__).with_name('confirmation_worker.py')),
                 str(xml),str(Path(config['env'])/'share/isce2/alosStack'),str(output)]
        with (folder/'worker.log').open('w+') as log:
            p=subprocess.Popen(command,cwd=folder,env=isolated_env(folder/'cache'),
                               stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code=p.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);p.wait()
                raise ValueError('配對預覽逾時，請確認所選環境及 NAS 連線。') from None
            if code:
                log.seek(0)
                raise ValueError('所選 ISCE2 無法解析這份設定；尚未執行運算。'+log.read()[-1800:])
        return json.loads(output.read_text())


def review(config):
    if config.get('workflow','alosStack') != 'alosStack':
        raise ValueError('此確認頁目前適用 ALOS-2 多日期；雙日期沿用專用健檢。')
    report=check_project(config)
    report.update(config=copy.deepcopy(config), created=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if not report['can_proceed']: return report
    before=fingerprint(config)
    try:
        prediction=resolve(config)
        report.update(prediction)
        # Check only the polarization that will actually be processed.
        plan=scan_source(config['source'])
        pol=prediction['resolved']['polarization'].upper()
        entries=[e for e in plan['entries'] if e['name'].startswith(('LED-','IMG-'+pol+'-')) and not e['member']
                 and Path(e['source']).parent == Path(config['source'])/e['date']]
        frames = selected_frames(config)
        if frames:
            entries = [e for e in entries if e['name'].split('-')[-3][-4:] in frames]
        plan=enrich(inventory_plan(config['source'],entries),config['env'],
                    allow_cross_sensor=config.get('sampling_equalization') is True)
        report['inventory']=plan
        dates=[d['date'] for d in plan['dates']]
        report['network']=network(prediction['pairs'],dates)
        report['ion_network']=network(prediction['ion_pairs'],dates)
        def add(key,status,title,detail):
            report['checks'].append(dict(key=key,status=status,title=title,detail=detail))
        for error in plan['problems']: add('metadata','error','影像描述核對',error)
        for warning in plan['warnings']: add('metadata','warning','影像描述提醒',warning)
        for row in report['checks']:
            if row['key']=='orbit':
                row['detail']='已讀取正式描述檔中的升降軌、視向與概略邊界；同方向仍不等於同相對軌道，也不代表像素內容完整或科學品質已驗證。'
        for key, title in [('network','干涉配對'),('ion_network','電離層配對')]:
            if key=='ion_network' and not prediction['resolved']['estimate']: continue
            n=report[key]
            # Explicitly excluded dates can be intentional: do not block disconnected designs.
            add(key,'pass' if n['connected'] else 'warning',title,
                f"{len(n['pairs'])} 組；{len(n['components'])} 個連通群；未配對日期："+('、'.join(n['isolated']) or '無')+
                ('。請核對日期排除設定；分離群無法共同決定相對位移。' if not n['connected'] else '。'))
        if not prediction['pairs']: add('pairs','error','沒有一般干涉配對','請調整後續日期數或日期／配對篩選。')
        used={d for pair in prediction['pairs']+prediction['ion_pairs'] for d in pair.split('-')}
        if config['reference'] not in used: add('reference','error','參考日期未參與配對','請返回選擇有參與本次網路的參考日期。')
        if prediction['resolved']['estimate'] and not prediction['ion_pairs']:
            add('ion_pairs','error','沒有電離層配對','請調整設定或關閉電離層估計。')
        bounds=plan['metadata'].get('bounds')
        if bounds and plan['metadata'].get('complete'):
            coverage=check_project({**config,'image_bounds':bounds})
            report['checks']=[r for r in report['checks'] if r['key']!='coverage' and not r['key'].endswith('_coverage')]
            for row in coverage['checks']:
                if row['key'].endswith('_coverage'):
                    row['detail']=row['detail'].replace('你提供的完整影像範圍','描述檔讀出的全部影像外框')
                    report['checks'].append(row)
        add('recovery','warning','階段恢復保護','新建多日期專案預設啟用。保存獨立階段副本需要額外磁碟空間；每階段會再檢查，空間不足會停止。')
        report['packages']=versions(config['env'])
        report['fingerprint']=before
        if before!=fingerprint(config): raise ValueError('檢查期間輸入或環境已改變，請重新預覽。')
    except Exception as e:
        report['checks'].append(dict(key='preview',status='error',title='確認頁未完成',detail=str(e)))
    report['can_proceed']=not any(r['status']=='error' for r in report['checks'])
    report['needs_review']=any(r['status']=='warning' for r in report['checks'])
    return report


class Confirmations:
    def __init__(self):
        self.reports={}
        self.lock=threading.Lock()

    def preview(self,config):
        result=review(config)
        with self.lock:
            key=uuid.uuid4().hex
            result['confirmation_id']=key
            self.reports[key]=copy.deepcopy(result)
            while len(self.reports)>12: self.reports.pop(next(iter(self.reports)))
        return result

    def validate(self,config):
        with self.lock: report=copy.deepcopy(self.reports.get(config.get('confirmation_id')))
        if not report or not report['can_proceed']: raise ValueError('確認頁已失效或未通過，請重新預覽。')
        clean={k:v for k,v in config.items() if k not in ('confirmation_id','preflight_reviewed')}
        if clean!=report['config'] or fingerprint(clean)!=report['fingerprint']:
            raise ValueError('設定、輸入檔案或環境已改變，請返回重新預覽。')
        return report


def compare_generated(state,proc,meta):
    path=meta/'confirmation.json'
    if not path.exists(): return  # Legacy projects retain their existing workflow.
    report=json.loads(path.read_text())
    actual=dict(pairs=declared_pairs((proc/'cmd_2.sh').read_text(),'insarpair'),
                ion_pairs=declared_pairs((proc/'cmd_3.sh').read_text(),'ionpair') if state['ion']['estimate'] else [])
    matched=all(sorted(report[k])==sorted(actual[k]) for k in actual)
    matched=matched and all(report['resolved'][k]==state['ion'][k] for k in ('estimate','apply','subsequent','subsequent_ion'))
    atomic_json(meta/'confirmation-generated.json',dict(matched=matched,**actual,resolved=state['ion']))
    if not matched: raise ValueError('產生的指令與確認頁不一致，已在科學運算前停止；請檢查 confirmation-generated.json。')


def verify_saved_inputs(state,meta):
    path=meta/'confirmation.json'
    if path.exists():
        report=json.loads(path.read_text())
        if fingerprint(state['config'])!=report['fingerprint']:
            raise ValueError('確認後輸入或環境已改變，已停止；請以目前設定建立新專案並重新確認。')


def saved_review(engine,key):
    state=engine.get(key); _,proc,meta=engine.paths(state)
    p=meta/'confirmation.json'
    report=json.loads(p.read_text()) if p.exists() else dict(config=state['config'],checks=[],legacy=True)
    dates=[d['date'] for d in state['dates']]
    report.update(network=network(state.get('expected_pairs',report.get('pairs',[])),dates),
                  ion_network=network(state.get('expected_ion_pairs',report.get('ion_pairs',[])),dates),
                  resolved={**report.get('resolved',{}),**state.get('ion',{})},status=state['status'])
    p=meta/'confirmation-generated.json'
    report['generated']=json.loads(p.read_text()) if p.exists() else None
    return report
