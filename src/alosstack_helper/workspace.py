"""Read-only views across processors; original project states remain authoritative."""
from pathlib import Path
import datetime as dt
import os
import zipfile

from .workflows import WORKFLOWS
from .sentinel import inspect_safe


def workflow_catalogue():
    rows = []
    for item in WORKFLOWS:
        row = dict(item)
        row['family'] = 'ALOS-2' if row['id'].startswith('alos') else 'Sentinel-1' if row['id'].startswith('tops') else 'Stripmap'
        row['maturity'] = 'planned' if not row['available'] else 'experimental' if row.get('experimental') or row['id'] == 'alos2App' else 'implemented'
        row['engine'] = 'ISCE2'
        rows.append(row)
    rows += [dict(id='nisar', name='NISAR · 雙日期', family='NISAR', engine='ISCE3', available=True,
                  maturity='experimental', description='RSLC → GUNW；尚未完成真實影像全流程驗證'),
             dict(id='alos4', name='ALOS-2／ALOS-4 · 資料準備', family='ALOS-4', engine='ISCE2', available=True,
                  maturity='preparation', description='相容性檢查與格式副本；混合雙日期／多日期仍保留檢查關卡')]
    return rows


STATUS = {
    'new': ('ready', '等待準備'), 'ready': ('ready', '等待開始'),
    'checking': ('running', '檢查中'), 'running': ('running', '執行中'), 'stopping': ('running', '停止中'),
    'review': ('attention', '等待人工判讀'), 'loaded': ('attention', '等待分析設定'),
    'pair_ready': ('attention', '等待確認配對'), 'tops_ready': ('attention', '等待確認 burst'),
    'processed': ('attention', '等待整理成果'), 'failed': ('attention', '需要處理'),
    'stopped': ('attention', '已停止'), 'cancelled': ('attention', '已停止'),
    'interrupted': ('attention', '服務曾中斷'), 'orphaned': ('attention', '前次程序仍在執行'),
    'complete': ('complete', '已完成'), 'unavailable': ('unavailable', '位置無法讀取'),
}


def timestamp(value):
    if isinstance(value, (int, float)): return value
    try: return dt.datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError): return 0


def overview(engine, analyses, nisar_jobs, conversions, onboarding):
    rows, warnings = [], []
    names = {w['id']: w['name'] for w in workflow_catalogue()}
    def add(kind, key, name, status, stage='', path='', environment='', created=0, error='', action=None, scientific=False):
        group, label = STATUS.get(status, ('attention', '請查看紀錄'))
        rows.append(dict(key=kind+':'+key, kind=kind, name=name, status=status, group=group,
                         status_label='運算結束，待品質檢查' if scientific and status == 'complete' else label,
                         stage=stage, path=path, environment=environment, updated=timestamp(created),
                         error=error, action=action, scientific=scientific))
        return rows[-1]
    for s in engine.list():
        c = s['config']; key = s['id']; history = s.get('history', [])
        name = c.get('name') or '未命名專案'
        project = add('project', key, name, s['status'], s.get('stage', ''), c['root'], c.get('env', ''),
            history[-1].get('time', '') if history else s.get('created', 0), s.get('error', ''),
            dict(type='project', id=key), scientific=True)
        rows[-1]['workflow'] = names.get(c.get('workflow', 'alosStack'), c.get('workflow'))
        active = getattr(engine, 'active', None) or ''
        project['registration'] = dict(id=key, busy=project['group']=='running' or active==key or active.startswith(key+':'))
        try:
            for a in analyses.listing(key):
                if a['status'] in ('running', 'checking', 'stopping', 'orphaned'):
                    project['registration']['busy'] = True
                label = 'MintPy 時間序列' if a['kind'] == 'mintpy' else '成果品質報告'
                add('analysis', key+':'+a['id'], name+' · '+((a.get('options') or {}).get('name') or label),
                    a['status'], a.get('stage', ''), str(Path(c['root'])/'analyses'/a['id']), a.get('environment', ''),
                    a.get('created'), a.get('error', ''), dict(type='analysis', id=key, run=a['id']), scientific=a['kind']=='mintpy')
                rows[-1]['workflow'] = label
        except (OSError, ValueError, KeyError) as e:
            warnings.append(name+' 的分析紀錄暫時無法讀取：'+str(e))
    for s in engine.unavailable():
        row = add('project', s['id'], Path(s['root']).name, 'unavailable', path=s['root'], error=s['reason'])
        active = getattr(engine, 'active', None) or ''
        row['registration'] = dict(id=s['id'], busy=active==s['id'] or active.startswith(s['id']+':'))
    for kind, manager in [('nisar', nisar_jobs), ('conversion', conversions)]:
        manager.reconcile()
        with manager.lock:
            states = list(manager.jobs.values())
            for s in states:
                c = s['config']
                add(kind, s['id'], c['name'], s['status'], s.get('stage', ''), c['root'], c.get('environment', ''),
                    s.get('created', 0), s.get('error', ''), dict(type=kind, id=s['id']), scientific=kind=='nisar')
                if s['status']=='ready':
                    rows[-1].update(group='attention', status_label='檢查完成，等待確認')
                rows[-1]['workflow'] = 'NISAR 雙日期' if kind=='nisar' else 'ALOS-4 格式轉換'
    for s in onboarding.recent():
        label = {'import':'資料整理', 'terrain':'地形準備', 'demo':'合成教學'}.get(s['kind'], s['kind'])
        add('preparation', s['id'], label, s['status'], s.get('stage', ''), created=s.get('created', 0),
            error=s.get('error', ''), action=dict(type='preparation', id=s['id']))
        rows[-1]['workflow'] = label
    rank = {'running':0, 'attention':1, 'ready':2, 'complete':3, 'unavailable':4}
    rows.sort(key=lambda r: (rank[r['group']], -r['updated'], r['name'], r['key']))
    return dict(records=rows, warnings=warnings, counts={g:sum(r['group']==g for r in rows) for g in rank},
                removed_projects=engine.removed_projects())


def source_candidates(value):
    """Bound file traversal; SAFE products are treated as a unit, never opened as pixels."""
    root = Path(value).expanduser().resolve()
    if not root.exists(): raise ValueError('找不到來源，請確認路徑與磁碟連線。')
    if root.is_file() or root.name.endswith('.SAFE'): return root, [root]
    found, visited = [], 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not Path(folder,d).is_symlink())
        visited += len(dirs)+len(files)
        if visited > 5000: raise ValueError('資料夾內容過多，請選到影像所在的子資料夾。')
        for name in list(dirs):
            if name.endswith('.SAFE'):
                found.append(Path(folder,name)); dirs.remove(name)
        if len(Path(folder).relative_to(root).parts)>=4: dirs[:]=[]
        for name in sorted(files):
            p=Path(folder,name)
            if name.startswith('.') or p.is_symlink(): continue
            if name.startswith(('LED-ALOS2','LED-ALOS4')) or p.suffix.lower() in ('.h5','.zip'):
                found.append(p)
        if len(found)>64: raise ValueError('候選產品超過 64 份，請縮小資料範圍。')
    return root, found


def identify(value, environment, library, onboarding):
    root, candidates = source_candidates(value)
    def is_safe(p):
        if p.name.endswith('.SAFE'): return True
        if p.suffix.lower() != '.zip': return False
        if p.name.startswith('S1'): return True
        try:
            with zipfile.ZipFile(p) as z:
                return any(n.endswith('.SAFE/manifest.safe') for n in z.namelist())
        except (OSError, zipfile.BadZipFile): return False
    sentinels = [p for p in candidates if is_safe(p)]
    other = [p for p in candidates if p not in sentinels]
    products, warnings, families, eligible = [], [], set(), set()
    if sentinels:
        families.add('Sentinel-1')
        for p in sentinels:
            try:
                s=inspect_safe(p)
                products.append(dict(sensor='Sentinel-1', type='IW SLC', path=str(p), date=s['date']))
            except (OSError, ValueError, zipfile.BadZipFile) as e:
                warnings.append(p.name+'：'+str(e))
        if any(p['type']=='IW SLC' for p in products): eligible.add('topsApp')
    alos_zip = [p for p in other if p.suffix.lower()=='.zip']
    loose = [p for p in other if p.suffix.lower()!='.zip']
    if loose:
        report=library.scan(str(root) if root.is_dir() else str(root.parent) if root.name.startswith('LED-') else str(root),environment)
        warnings.extend(e['message'] for e in report.get('errors', []))
        for p in report['products']:
            sensor=p['sensor'];families.add(sensor)
            products.append(dict(sensor=sensor,type=p['type'],path=p['path'],date=p.get('date') or p.get('metadata',{}).get('zeroDopplerStartTime','')))
    if alos_zip:
        if loose or sentinels: warnings.append('同時包含 ZIP 與展開產品。請分開辨識，避免把同一份影像重複使用。')
        else:
            report=onboarding.scan(str(root),environment)
            families.add('ALOS-2')
            products.extend(dict(sensor='ALOS-2',type='CEOS ZIP',path=str(root),date=d['date']) for d in report['dates'])
            warnings.extend(report.get('problems',[]));warnings.extend(report.get('warnings',[]))
    if families=={'ALOS-2'}: eligible.update(('alosStack','alos2App'))
    if 'ALOS-4' in families:
        eligible={'alos4'}
        warnings.append('含 ALOS-4，先完成格式與影格相容性檢查。混合雙日期／多日期不自動接續。')
    elif len(families)>1:
        eligible.clear();warnings.append('包含不同衛星資料，請先選擇同一類產品的資料夾。')
    elif families=={'NISAR'} and any(p['type']=='RSLC' for p in products): eligible.add('nisar')
    if any(p['type']=='GUNW' for p in products): warnings.append('已有 GUNW 可到「資料與成果」查看；建立雙日期工作仍需兩份 RSLC。')
    if not products: warnings.append('尚未辨識到支援的產品。可縮小資料範圍，或直接選擇流程後完成專案健檢。')
    # A selected CEOS leader is a file; ISCE2's source input needs its directory.
    source = root.parent if root.is_file() and root.name.startswith('LED-') else root
    return dict(source=str(source), families=sorted(families), products=products, eligible=sorted(eligible),
                warnings=warnings, note='辨識僅供流程選擇；共同軌道、極化、覆蓋與完整性仍須通過各流程健檢。')
