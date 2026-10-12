"""Read-only, bounded checks before a project writes or processes data."""
from pathlib import Path
import datetime
import json
import math
import os
import shutil

from .core import safe_path, find_conda
from .onboarding import PRODUCT, inspect_terrain
from .runtime import workbench_home


def check_project(c, existing=False):
    checks, paths, terrains = [], {}, {}
    def add(key, status, title, detail):
        checks.append(dict(key=key, status=status, title=title, detail=str(detail)))
    for key in ('root', 'source', 'env', 'dem', 'dem_geo', 'wbd'):
        try:
            if not c.get(key): raise ValueError('請選擇路徑。')
            paths[key] = safe_path(c[key])
        except (ValueError, OSError) as e: add(key, 'error', key+' 路徑', e)
    root, source, env = (paths.get(k) for k in ('root', 'source', 'env'))
    if root:
        try:
            runtime = (workbench_home()/'runtime').resolve()
            if root == runtime or runtime in root.parents or root in runtime.parents:
                raise ValueError('專案請放在 runtime 外，避免解除安裝工具時移動研究成果。')
            for key, p in paths.items():
                if key != 'root' and (root == p or root in p.parents or p in root.parents):
                    raise ValueError('專案與來源／環境／地形路徑不能互相包含。')
            if root.exists() and (not root.is_dir() or (not existing and any(root.iterdir()))):
                raise ValueError('請選新的空專案資料夾。')
            parent = next(p for p in (root, *root.parents) if p.exists())
            if not os.access(parent, os.W_OK | os.X_OK): raise ValueError('專案位置無寫入權限。')
            add('destination', 'pass', '專案位置', '可寫入，與來源及運算工具分開。')
        except (OSError, ValueError) as e: add('destination', 'error', '專案位置', e)
    workflow = c.get('workflow', 'alosStack')
    if workflow not in ('alosStack', 'alos2App'):
        add('workflow', 'error', '處理方式', '此版開放 ALOS-2 多日期與雙日期流程。')
    if env:
        if not (env/'bin/python').is_file() or not find_conda(env):
            add('environment', 'error', '運算環境', '找不到所選環境的 Python 或 Conda。若已移除專用工具，請先重新安裝。')
        else:
            add('environment', 'pass', '環境選擇', str(env)+'；Conda：'+str(find_conda(env)))
        if workflow == 'alosStack' and not (env/'share/isce2/alosStack/create_cmds.py').is_file():
            add('processor', 'error', 'ISCE2 程式', '缺少 alosStack/create_cmds.py。')
        subdirs = set()
        for p in (env/'conda-meta').glob('*.json'):
            try:
                value = json.loads(p.read_text()).get('subdir', '')
                if value and value != 'noarch': subdirs.add(value)
            except (OSError, ValueError): pass
        add('architecture', 'error' if len(subdirs) > 1 else 'warning' if not subdirs else 'pass', '套件架構',
            ('偵測到混用 '+'、'.join(sorted(subdirs))+'。請選擇單一架構的環境，例如 insar_arm 或工作台專用環境；不會自動修改既有環境。') if len(subdirs) > 1 else
            ('、'.join(sorted(subdirs))+'。套件標記不等於每個二進位都已驗證；可使用首頁的環境驗證。' if subdirs else '缺少套件架構紀錄，無法自動確認。'))
    files, dates, modes, frames_by_date = [], [], set(), {}
    try:
        if not source or not source.is_dir(): raise ValueError('找不到來源資料夾，請確認 NAS 已掛載。')
        all_dates = sorted(p for p in source.iterdir() if p.is_dir() and len(p.name) == 6 and p.name.isdigit())
        dates = [p for p in all_dates if workflow != 'alos2App' or p.name in (c.get('reference'), c.get('secondary'))]
        if len(dates) < 2: raise ValueError('需至少兩個不同的 YYMMDD 日期資料夾。')
        pol = c.get('properties', {}).get('polarization', 'HH').upper() or 'HH'
        if pol not in ('HH', 'HV', 'VH', 'VV'): raise ValueError('請選 HH、HV、VH 或 VV。')
        for folder in dates:
            datetime.datetime.strptime(folder.name, '%y%m%d')
            leaders, images, frames = {}, {}, set()
            for p in sorted(folder.iterdir()):
                match = PRODUCT.fullmatch(p.name)
                if not match: continue
                identity, date, mode = match.groups()
                frame = identity[-4:]
                if workflow == 'alos2App' and c.get('frames') and frame not in c['frames']: continue
                if date != folder.name: raise ValueError(p.name+' 的日期與資料夾不符。')
                if '1.1' not in mode: raise ValueError(p.name+' 不是 Level 1.1。')
                if not p.name.startswith('LED-') and not p.name.startswith('IMG-'+pol+'-'): continue
                target = leaders if p.name.startswith('LED-') else images
                if frame in target: raise ValueError(date+' / '+frame+' 有重複產品。')
                target[frame] = (identity, mode)
                size = p.stat().st_size
                with p.open('rb') as stream: header = stream.read(12)
                if len(header) != 12 or not 12 <= int.from_bytes(header[8:12], 'big') <= size:
                    raise ValueError(p.name+' 的 CEOS 首筆紀錄長度無效；可能不是原始產品或尚未下載完整。')
                files.append(p); frames.add(frame); modes.add(mode)
            if not leaders or leaders != images:
                raise ValueError(folder.name+' 的每個 frame 都必須有對應的 LED 與 '+pol+' IMG（同產品／版本）。')
            frames_by_date[folder.name] = frames
        if len(modes) != 1: raise ValueError('日期之間的觀測模式／版本不一致，請先分组。')
        if any(x != next(iter(frames_by_date.values())) for x in frames_by_date.values()):
            raise ValueError('日期之間的 frame 組合不同。雙日期可只選共同 frame。')
        if c.get('reference') not in frames_by_date: raise ValueError('參考日期不在本次資料中。')
        add('products', 'pass', '日期、frame、極化與讀取', f'{len(dates)} 個日期，{len(files)} 個 LED／IMG；{pol}。首筆 CEOS 紀錄可讀取。')
        add('orbit', 'warning', '軌道與完整性仍需確認', '檔名與首筆紀錄無法證明整幅影像完整或同軌。請核對資料提供者的軌道、方向與覆蓋；ISCE2 準備階段還會讀取詳細 metadata。')
    except (OSError, ValueError) as e: add('products', 'error', '影像資料', e)
    for key in ('dem', 'dem_geo', 'wbd'):
        if key not in paths: continue
        try:
            terrains[key] = inspect_terrain(paths[key], 'wbd' if key == 'wbd' else 'dem')
            add(key, 'pass', {'dem':'對位 DEM', 'dem_geo':'地理編碼 DEM', 'wbd':'水體遮罩'}[key],
                '尺寸與座標描述有效；南／北／西／東：'+', '.join(f'{x:.5f}' for x in terrains[key]['bounds']))
        except Exception as e:
            add(key, 'error', key+' 描述檔', e)
    bounds = c.get('image_bounds')
    if bounds:
        try:
            if not isinstance(bounds, list) or len(bounds) != 4: raise ValueError('需填南／北／西／東四個數字。')
            s,n,w,e = map(float, bounds)
            if not all(math.isfinite(v) for v in (s,n,w,e)) or not (-90 <= s < n <= 90 and -180 <= w < e <= 180):
                raise ValueError('完整影像範圍無效；跨日期線資料尚未支援。')
            for key, value in terrains.items():
                a,b,x,y = value['bounds']
                ok = a <= s and b >= n and x <= w and y >= e
                add(key+'_coverage', 'pass' if ok else 'error', key+' 涵蓋輸入範圍',
                    '已涵蓋你提供的完整影像範圍。' if ok else '未涵蓋你提供的完整影像範圍，請更換地形檔。')
        except (TypeError, ValueError) as e: add('coverage', 'error', '地形涵蓋', e)
    else:
        add('coverage', 'warning', '尚未確認地形完整覆蓋', '可填寫資料提供者的完整影像南／北／西／東界；這不是 MintPy 裁切範圍。未填時不會宣稱覆蓋已通過。')
    if root:
        try:
            base = next(p for p in (root, *root.parents) if p.exists())
            free = shutil.disk_usage(base).free
            size = sum(p.stat().st_size for p in files)
            minimum = (size if c.get('copy') and not existing else 0) + 1024**3
            suggestion = minimum + size*4
            add('space', 'error' if free < minimum else 'warning' if free < suggestion else 'pass', '磁碟空間',
                f'可用 {free/1024**3:.1f} GB；複製與基本緩衝需 {minimum/1024**3:.1f} GB。粗略建議保留 {suggestion/1024**3:.1f} GB；以輸入量 4 倍估算中間檔，實際還取決於配對與 looks，並非完成保證。')
        except OSError as e: add('space', 'error', '磁碟空間', e)
    return dict(checks=checks, can_proceed=not any(x['status']=='error' for x in checks),
                needs_review=any(x['status']=='warning' for x in checks))


def require_preflight(config, existing=False):
    report = check_project(config, existing)
    if not report['can_proceed']:
        raise ValueError('資料健檢未通過：'+'；'.join(x['detail'] for x in report['checks'] if x['status']=='error'))
    if not existing and report['needs_review'] and config.get('preflight_reviewed') is not True:
        raise ValueError('請先檢查健檢摘要並確認尚待人工核對的事項。')
    return report
