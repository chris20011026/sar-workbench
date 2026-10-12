"""First-use checks and reversible data organization. Standard library only."""
from __future__ import annotations

import datetime
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import threading
import signal
import contextlib
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .core import atomic_json, inspect_data, safe_path
from .runtime import RuntimeManager, ready_environment, private_env
from .terrain import plan_terrain
from .metadata import enrich

PRODUCT = re.compile(r'^(?:LED|IMG-(?:HH|HV|VV|VH))-(ALOS2\d+)-(\d{6})-([A-Z0-9_.]+)$')
MAX_ENTRIES = 50000


def validate_archive(archive):
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ValueError('壓縮檔包含過多檔案，請分批匯入。')
    seen = set()
    for info in infos:
        name = info.filename
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts or '\\' in name or '\x00' in name or ':' in name:
            raise ValueError('ZIP 包含不安全路徑，未解壓縮。')
        if stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
            raise ValueError('不支援包含符號連結或加密項目的 ZIP。')
        if name in seen:
            raise ValueError('ZIP 包含重複路徑，請重新確認來源。')
        seen.add(name)
        if info.file_size > 150 * 1024**3 or info.file_size > max(info.compress_size, 1) * 1000:
            raise ValueError('ZIP 的展開大小異常，請先檢查壓縮檔。')
    return infos


def scan_source(value):
    path = Path(value).expanduser().absolute()
    if not path.exists():
        raise ValueError('找不到資料，請確認資料夾與 NAS 連線。')
    entries, unsupported, visited = [], set(), 0

    def add(name, size, source, member=None):
        nonlocal visited
        visited += 1
        if visited > MAX_ENTRIES:
            raise ValueError('資料夾包含過多檔案；請選擇只含本次影像的資料夾。')
        match = PRODUCT.fullmatch(name)
        if not match:
            if name.startswith(('S1A_', 'S1B_', 'S1C_', 'S1D_')):
                unsupported.add('偵測到 Sentinel-1。此版匯入精靈處理 ALOS-2 Level 1.1；Sentinel-1 工作流尚未開放。')
            return
        identity, date, mode = match.groups()
        datetime.datetime.strptime(date, '%y%m%d')
        if '1.1' not in mode:
            unsupported.add('有 ALOS-2 產品不是 Level 1.1，未納入本次匯入。')
            return
        entries.append(dict(name=name, date=date, identity=identity, mode=mode, size=size,
                            source=str(source), member=member,
                            stamp=[source.stat().st_size, source.stat().st_mtime_ns]))

    def visit(file):
        if file.name.startswith(('S1A_', 'S1B_', 'S1C_', 'S1D_')):
            unsupported.add('偵測到 Sentinel-1。此版匯入精靈處理 ALOS-2 Level 1.1；Sentinel-1 工作流尚未開放。')
        if file.suffix.lower() == '.zip':
            with zipfile.ZipFile(file) as archive:
                for info in validate_archive(archive):
                    if not info.is_dir():
                        add(PurePosixPath(info.filename).name, info.file_size, file, info.filename)
        else:
            add(file.name, file.stat().st_size, file)

    if path.is_file():
        visit(path)
    else:
        for base, dirs, files in os.walk(path, followlinks=False):
            if len(Path(base).relative_to(path).parts) > 12:
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('proc', 'analyses', 'runtime', 'node_modules')]
            for name in sorted(files):
                file = Path(base) / name
                if not name.startswith('.') and file.is_file():
                    visit(file)
    return inventory_plan(str(path), entries, sorted(unsupported))


def inventory_plan(source, entries, warnings=()):
    warnings = list(warnings)
    names = set()
    for e in entries:
        parts = PurePosixPath(e["member"]).parts[:-1] if e.get("member") else Path(e["source"]).parts[:-1]
        folders = [part for part in parts if re.fullmatch(r"\d{6}", part)]
        if folders and folders[-1] != e["date"]:
            warnings.append(f"資料夾日期 {folders[-1]} 與產品日期 {e['date']} 不同，將以核對後的產品日期整理。")
        target = (e['date'], e['name'])
        if target in names:
            raise ValueError('有重複影像產品：' + e['name'] + '。請只保留一份來源後重新掃描；工作台不會猜測要用哪一版。')
        names.add(target)
    leaders = {(e['identity'], e['date'], e['mode']) for e in entries if e['name'].startswith('LED-')}
    images = {(e['identity'], e['date'], e['mode']) for e in entries if e['name'].startswith('IMG-')}
    problems = []
    if leaders != images:
        problems.append('部分產品缺少同日期、同 frame 與模式的 LED 或 IMG。')
    if any(e['size'] == 0 for e in entries):
        problems.append('來源含空檔案，請確認下載已完成。')
    dates = []
    for date in sorted({e['date'] for e in entries}):
        files = [e for e in entries if e['date'] == date]
        dates.append(dict(date=date, frames=sorted({e['identity'][-4:] for e in files}),
                          polarizations=sorted({e['name'].split('-')[1] for e in files if e['name'].startswith('IMG-')}),
                          modes=sorted({e['mode'] for e in files}), files=len(files)))
    if len(dates) < 2:
        problems.append('至少需要兩個日期的完整 ALOS-2 Level 1.1 產品。')
    if len({e['mode'] for e in entries}) > 1:
        problems.append('偵測到不同觀測模式或處理版本，請先分組。')
    if dates and any(d['frames'] != dates[0]['frames'] for d in dates):
        problems.append('各日期的 frame 範圍不同，請選擇相同 frame 組合。')
    return dict(source=str(source), entries=entries, dates=dates, problems=problems,
                warnings=sorted(set(warnings)), bytes=sum(e['size'] for e in entries),
                extract_bytes=sum(e['size'] for e in entries if e['member']),
                file_count=len(entries), can_import=bool(entries) and not problems)


def organize(plan, destination, progress=lambda text: None, cancelled=lambda: False):
    if not plan['can_import']:
        raise ValueError('資料檢查未通過，請先處理缺檔或分組問題。')
    destination = safe_path(destination)
    if destination.exists():
        raise ValueError('匯入目的地已存在，不會覆蓋。')
    destination.mkdir(parents=True, mode=0o700)
    # Require enough space for archive expansion plus a margin. Ordinary files
    # are linked rather than duplicated; their original paths stay untouched.
    if shutil.disk_usage(destination).free < plan['extract_bytes'] + 1024**3:
        raise ValueError('磁碟空間不足以展開這批影像。')
    for source in {e['source'] for e in plan['entries']}:
        p = Path(source)
        expected = next(e['stamp'] for e in plan['entries'] if e['source'] == source)
        if [p.stat().st_size, p.stat().st_mtime_ns] != expected:
            raise ValueError('來源在掃描後改變，請重新掃描；尚未開始讀取影像。')
    archives = {}
    try:
        for i, e in enumerate(plan['entries']):
            if cancelled(): raise RuntimeError('已停止匯入，原始資料未修改；未完成的整理不會用於運算。')
            progress(f'整理 {i + 1} / {len(plan["entries"])}：{e["date"]}')
            folder = destination / e['date']; folder.mkdir(exist_ok=True)
            target = folder / e['name']
            if e['member']:
                if e['source'] not in archives:
                    archive = zipfile.ZipFile(e['source']); validate_archive(archive); archives[e['source']] = archive
                with archives[e['source']].open(e['member']) as src, target.open('xb') as dst:
                    while chunk := src.read(1024 * 1024):
                        if cancelled(): raise RuntimeError('已停止解壓縮；原始 ZIP 保持不變。')
                        dst.write(chunk)
                if target.stat().st_size != e['size']:
                    raise ValueError('解壓縮檔案大小不符，未啟用本次匯入。')
            else:
                target.symlink_to(e['source'])
        atomic_json(destination / 'import-manifest.json', dict(source=plan['source'], entries=plan['entries'], complete=True))
    finally:
        for archive in archives.values():
            archive.close()
    return dict(path=str(destination), dates=inspect_data(destination), linked=True)


def inspect_terrain(value, kind):
    if kind not in ('dem', 'wbd'):
        raise ValueError('地形種類必須為 dem 或 wbd。')
    p = Path(value).expanduser().resolve()
    xml = Path(str(p) + '.xml')
    if not p.is_file() or not xml.is_file():
        raise ValueError('請選擇影像本體，旁邊需有同名 .xml。若只有 GeoTIFF，請先轉為 ISCE 格式。')
    root = ET.parse(xml).getroot()
    def props(element):
        return {x.get('name', '').lower().replace(' ', '_'): (x.findtext('value') if x.find('value') is not None else x.text or '').strip()
                for x in element.findall('property')}
    values = props(root)
    components = {x.get('name', '').lower(): props(x) for x in root.findall('component')}
    c1, c2 = components.get('coordinate1', {}), components.get('coordinate2', {})
    width, height = int(values.get('width', c1.get('size', '0'))), int(values.get('length', c2.get('size', '0')))
    if width <= 0 or height <= 0:
        raise ValueError('XML 缺少有效的寬度／長度。')
    datatype = values.get('data_type', '').upper()
    sizes = {'BYTE': 1, 'SHORT': 2, 'INT': 4, 'FLOAT': 4, 'DOUBLE': 8}
    if datatype not in sizes or int(values.get('number_bands', '1')) != 1:
        raise ValueError('目前接受單波段 BYTE／SHORT／INT／FLOAT／DOUBLE 的 ISCE 地形影像。')
    if p.stat().st_size != width * height * sizes[datatype]:
        raise ValueError('影像大小與 XML 不一致；可能尚未複製完整或描述檔不符。')
    bounds = None
    try:
        x, y = float(c1['startingvalue']), float(c2['startingvalue'])
        dx, dy = float(c1['delta']), float(c2['delta'])
        bounds = [min(y, y + dy*(height-1)), max(y, y + dy*(height-1)), min(x, x + dx*(width-1)), max(x, x + dx*(width-1))]
    except (KeyError, ValueError):
        raise ValueError('缺少地理座標資訊，不能確認涵蓋範圍。') from None
    if (not all(math.isfinite(v) for v in [x,y,dx,dy,*bounds]) or dx <= 0 or dy >= 0
            or not (-90 <= bounds[0] <= bounds[1] <= 90 and -180 <= bounds[2] <= bounds[3] <= 180)):
        raise ValueError('需要由西向東、由北向南的有效經緯度格網，請確認地形座標。')
    reference = values.get('reference', '').upper()
    if kind == 'dem' and reference != 'WGS84':
        raise ValueError('DEM 必須有 WGS84 橢球高標記。EGM96／EGM2008 高程需先做高程基準轉換，不能只更改檔名。')
    if kind == 'wbd' and datatype != 'BYTE':
        raise ValueError('此工作流的 WBD 需要 ISCE BYTE 水體遮罩（陸地 0、水體 -1）。')
    return dict(path=str(p), width=width, height=height, bounds=bounds, reference=reference,
                note='尺寸與座標描述已檢查；仍須確認地形覆蓋全部影像。準備專案時會另建立本機路徑描述檔。')


class Onboarding:
    def __init__(self, home=None):
        self.runtime = RuntimeManager(home)
        self.home = self.runtime.home
        self.plans = {}
        self.jobs = {}
        self.lock = threading.Lock()
        self.process = None
        self.cancelled = threading.Event()
        self.jobs_file = self.home / 'onboarding-jobs.json'
        try:
            previous = json.loads(self.jobs_file.read_text())
            for job in previous[-10:]:
                if job['status'] == 'running':
                    job.update(status='failed', stage='上次工作中斷', error='上次服務在工作完成前結束。已保留來源與紀錄；請重新開始準備。')
                self.jobs[job['id']] = job
        except (OSError, ValueError, KeyError, TypeError):
            self.jobs = {}

    def recent(self):
        return [{k:v for k,v in j.items() if k != 'result'} for j in list(self.jobs.values())[-10:]][::-1]

    def _save_jobs(self):
        self.home.mkdir(parents=True, exist_ok=True)
        atomic_json(self.jobs_file, list(self.jobs.values())[-10:])

    def scan(self, path, environment=None):
        plan = enrich(scan_source(path), environment)
        return self._store_plan(plan)

    def select(self, plan_id, keys):
        original = self.plans.get(plan_id)
        if not original or not isinstance(keys, list) or not keys:
            raise ValueError("請先掃描並選取候選組。")
        allowed = {p["key"] for p in original.get("metadata", {}).get("products", [])}
        if not all(isinstance(k, str) and k in allowed for k in keys):
            raise ValueError("選取的影像不在原始清單中。")
        entries = [e for e in original["entries"] if "|".join([e["identity"],e["date"],e["mode"]]) in keys]
        plan = inventory_plan(original["source"], entries)
        return self._store_plan(enrich(plan, original.get("metadata_environment")))

    def _store_plan(self, plan):
        key = uuid.uuid4().hex
        with self.lock:
            if len(self.plans) >= 20:
                self.plans.pop(next(iter(self.plans)))
            self.plans[key] = plan
        return {k: v for k, v in dict(plan, id=key).items() if k != 'entries'}

    def start_job(self, kind, plan_id=None, options=None):
        with self.lock:
            if any(j['status'] == 'running' for j in self.jobs.values()):
                raise ValueError('已有資料整理或教學運算進行中。')
            if kind not in ('import', 'demo', 'terrain'):
                raise ValueError('未知工作。')
            plan = self.plans.get(plan_id)
            if kind == 'import' and (not plan or not plan['can_import']):
                raise ValueError('請先掃描並確認資料。')
            prefix = ready_environment(self.home)
            if kind in ('demo', 'terrain') and self.runtime.status()['status'] == 'running':
                raise ValueError('請等待目前的環境驗證完成。')
            if kind in ('demo', 'terrain') and not prefix:
                raise ValueError('請先完成專用環境的安裝與驗證。')
            if kind == 'terrain': plan = plan_terrain((options or {}).get('bounds'))
            key = uuid.uuid4().hex[:12]
            job = dict(id=key, kind=kind, status='running', stage='準備中')
            self.jobs[key] = job
            self._save_jobs()
            self.cancelled.clear()
            threading.Thread(target=self._worker, args=(job, plan, prefix), daemon=True).start()
            return dict(job)

    def _worker(self, job, plan, prefix):
        try:
            if job['kind'] == 'import':
                path = self.home / 'imports' / job['id']
                result = organize(plan, path, lambda msg: job.update(stage=msg), self.cancelled.is_set)
                if plan.get('metadata_environment'):
                    job['stage'] = '補讀整理後影像的邊界與分組資訊'
                    checked = enrich(scan_source(path), plan['metadata_environment'])
                    result['inspection'] = {k:v for k,v in checked.items() if k != 'entries'}
                    atomic_json(path / 'inspection.json', result['inspection'])
                    if not checked['can_import']:
                        raise ValueError('整理後的資料未通過檢查：'+'；'.join(checked['problems']))
            else:
                terrain = job['kind'] == 'terrain'
                base = self.home / ('terrain' if terrain else 'lessons'); base.mkdir(parents=True, exist_ok=True)
                output = base / job['id']
                job['stage'] = '下載公開地形與水體 → 轉換橢球高 → 檢查 ISCE 影像' if terrain else '實際執行 MintPy：干涉圖反演 → 速度估計 → 核對已知答案'
                worker = 'terrain_worker.py' if terrain else 'demo_worker.py'
                extra = ['--cache',str(self.home/'terrain-cache'),'--bounds',*map(str,plan['bounds'])] if terrain else []
                with (base / (job['id'] + '.log')).open('w') as log:
                    self.process = subprocess.Popen([str(self.home / 'runtime/miniforge/bin/conda'), 'run', '--no-capture-output',
                                    '-p', str(prefix), 'python', str(Path(__file__).with_name(worker)),
                                    '--output', str(output),*extra], env=private_env(self.home),
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    if self.cancelled.is_set(): self.stop()
                    try:
                        if self.process.wait(timeout=7200 if terrain else 600):
                            message = (base / (job['id']+'.log')).read_text(errors='replace')[-1600:]
                            raise RuntimeError('工作未完成，已保留紀錄。\n'+message)
                    except subprocess.TimeoutExpired:
                        self.stop(); raise RuntimeError('工作等待逾時，已保留紀錄；請檢查環境或網路後重試。') from None
                    finally:
                        self.process = None
                result = json.loads((output / 'result.json').read_text())
                if terrain:
                    inspect_terrain(result['dem'],'dem');inspect_terrain(result['wbd'],'wbd')
            if self.cancelled.is_set():
                raise RuntimeError('工作已停止，未啟用此次結果。')
            job.update(status='complete', stage='已完成', result=result)
        except Exception as e:
            job.update(status='failed', error=str(e), stage='需要處理')
        finally:
            with self.lock:
                self._save_jobs()

    def stop(self):
        self.cancelled.set()
        if self.process and self.process.poll() is None:
            with contextlib.suppress(ProcessLookupError): os.killpg(self.process.pid, signal.SIGTERM)

    def job(self, key):
        if key not in self.jobs:
            raise ValueError('這項工作不在目前服務中，可能服務曾重新啟動。')
        result = dict(self.jobs[key])
        if result['kind'] in ('terrain','demo') and result['status'] == 'running':
            log = self.home / ('terrain' if result['kind']=='terrain' else 'lessons') / (key+'.log')
            if log.is_file():
                with log.open('rb') as f:
                    f.seek(max(0,log.stat().st_size-1600));result['output']=f.read().decode(errors='replace')
        return result
