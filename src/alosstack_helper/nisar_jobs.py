"""Project-owned NISAR jobs; distinct name avoids shadowing upstream nisar."""
from pathlib import Path
import json
import math
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid

from .core import atomic_json, digest, find_conda
from .environment import isolated_env

WORKER = Path(__file__).with_name('nisar_worker.py')
SUPPORTED_VERSION = '0.25.21'
FIELDS = [
    dict(key='frequency', label='處理頻段', type='choice', choices=['A', 'B'], value='A', help='兩期都必須有此頻段；先處理一個頻段與一個同極化。'),
    dict(key='polarization', label='極化', type='choice', choices=['HH', 'VV'], value='HH', help='本版使用同極化，供精細對位與電離層估計使用。'),
    dict(key='range_looks', label='Range looks', type='int', min=1, max=128, value=11, help='距離方向的多視數；增加可減少雜訊，但會降低空間解析度。'),
    dict(key='azimuth_looks', label='Azimuth looks', type='int', min=1, max=128, value=11, help='方位方向的多視數。兩個方向都沿用 ISCE3 0.25.21 預設 11。'),
    dict(key='ionosphere', label='估計電離層相位', type='bool', value=False, help='會增加運算量。本版使用 A/B 差分頻段法，須有 A、B 的相同極化；成果需人工判讀。'),
    dict(key='fine_registration', label='精細對位', type='bool', value=True, help='連動 dense offsets、rubbersheet 與 fine resample；關閉時只做幾何對位，可能降低相干性。'),
    dict(key='filter', label='干涉圖濾波', type='choice', choices=['no_filter', 'boxcar'], value='no_filter', help='no_filter：不額外濾波；boxcar：9 × 9 均值視窗，會平滑細節。'),
    dict(key='threads', label='CPU 執行緒上限', type='int', min=1, max=64, value=2, help='限制 OpenMP 與數值函式庫執行緒，實際速度仍取決於各步驟。'),
    dict(key='epsg', label='輸出投影 EPSG（可留白）', type='int', min=1024, max=32767, value='', help='留白沿用 DEM 投影；指定網格間距時請使用公尺為單位的投影。'),
    dict(key='posting', label='輸出網格間距（公尺，可留白）', type='float', min=1, max=1000, value='', help='須搭配以公尺為單位的 EPSG。更細的輸出網格不會增加原始解析度。'),
]


def normalize(raw):
    if not isinstance(raw, dict) or set(raw) - {f['key'] for f in FIELDS}:
        raise ValueError('含未支援的 NISAR 設定；請使用表單提供的選項。')
    raw = dict(raw)
    result = {}
    for f in FIELDS:
        key = f['key']; value = raw.get(key, f['value'])
        if f['type'] == 'bool':
            if type(value) is not bool: raise ValueError(f['label'] + '須選擇開啟或關閉。')
        elif f['type'] == 'choice':
            if value not in f['choices']: raise ValueError(f['label'] + '選項不合法。')
        elif value in ('', None) and f['value'] == '':
            value = None
        else:
            if type(value) is bool: raise ValueError(f['label'] + '須為數字。')
            try: value = float(value)
            except (TypeError, ValueError): raise ValueError(f['label'] + '須為數字。')
            if not math.isfinite(value) or not f['min'] <= value <= f['max'] or (f['type'] == 'int' and not value.is_integer()):
                raise ValueError(f['label'] + '超出允許範圍。')
            if f['type'] == 'int': value = int(value)
        result[key] = value
    if result['posting'] is not None and result['epsg'] is None:
        raise ValueError('設定公尺網格間距時，請一併指定投影 EPSG。')
    if result['ionosphere'] and result['frequency'] != 'A':
        raise ValueError('此版電離層流程需選頻段 A，並使用輸入檔中的 B 頻段。')
    return result


def stamps(paths):
    rows = []
    for value in paths:
        p = Path(value).resolve(); st = p.stat()
        if not p.is_file() or not st.st_size: raise ValueError('檔案不存在或為空：' + str(p))
        rows.append(dict(path=str(p), size=st.st_size, mtime_ns=st.st_mtime_ns))
    return rows


class NisarJobs:
    worker = WORKER
    settings_name = 'insar.yaml'
    label = 'NISAR / ISCE3'
    run_stage = '執行 NISAR InSAR'

    def __init__(self, home, namespace='nisar'):
        self.home = Path(home) / namespace
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        self.cancel = threading.Event()
        self.thread = None
        self.jobs = {}
        if self.home.exists():
            for p in self.home.glob('*/state.json'):
                try:
                    s = json.loads(p.read_text())
                    if s['status'] in ('checking', 'running', 'stopping', 'orphaned'):
                        if self._alive(s):
                            s.update(status='orphaned', error='前一個服務的工作程序仍在執行。請先停止它，再開始新工作；不會自動接管或重跑。')
                            self.active = s['id']
                        else:
                            s.update(status='interrupted', error='服務中斷，已保留記錄與輸出。請建立新的執行資料夾，避免覆寫半成品。')
                        atomic_json(p, s)
                    self.jobs[s['id']] = s
                except (OSError, ValueError, KeyError): continue

    def folder(self, key):
        if not re.fullmatch(r'[0-9a-f]{32}', str(key)): raise ValueError('無效的 NISAR 工作代號。')
        return self.home / key

    def save(self, s):
        atomic_json(self.folder(s['id']) / 'state.json', s)

    def _alive(self, s):
        pid = s.get('pid')
        if type(pid) is not int or pid <= 1: return False
        try: os.kill(pid, 0)
        except ProcessLookupError: return False
        except PermissionError: return True
        return True

    def _owns_process(self, s):
        pid=s.get('pid')
        if not self._alive(s): return False
        try:
            command=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True,timeout=3,check=True).stdout
            return os.getpgid(pid)==pid and str(self.folder(s['id'])) in command and str(self.worker) in command
        except (OSError, subprocess.SubprocessError): return False

    def reconcile(self):
        with self.lock:
            for s in self.jobs.values():
                if s['status']=='orphaned' and not self._alive(s):
                    s.update(status='interrupted',error='前次工作已退出；保留其日誌與輸出，不自動標記成功。')
                    if self.active==s['id']: self.active=None
                    self.save(s)

    def get(self, key):
        self.folder(key)
        self.reconcile()
        with self.lock:
            if key not in self.jobs: raise ValueError('找不到這份 NISAR 工作。')
            s = json.loads(json.dumps(self.jobs[key]))
        log = self.folder(key) / 'run.log'
        if log.exists():
            with log.open('rb') as f:
                f.seek(max(0, log.stat().st_size - 60000)); s['log'] = f.read().decode(errors='replace')
        else: s['log'] = ''
        return s

    def listing(self):
        self.reconcile()
        with self.lock:
            return [dict(id=s['id'], name=s['config']['name'], status=s['status'], root=s['config']['root'])
                    for s in sorted(self.jobs.values(), key=lambda x: x['created'], reverse=True)]

    def create(self, data):
        with self.lock:
            if self.active: raise ValueError('請先完成目前的 NISAR 工作。')
            env = Path(data.get('environment', '')).expanduser().resolve()
            if not (env / 'bin/python').is_file() or not find_conda(env): raise ValueError('請選擇已安裝 ISCE3 的 Conda 環境。')
            root_text = str(data.get('root', '')).strip()
            if not root_text: raise ValueError('請選擇新的專案資料夾。')
            root = Path(root_text).expanduser().resolve()
            if root.exists(): raise ValueError('請使用尚不存在的資料夾；本版不會覆寫現有目錄。')
            if not root.parent.is_dir(): raise ValueError('專案上層資料夾不存在。')
            c = dict(name=str(data.get('name', '')).strip()[:100] or 'NISAR 雙日期', root=str(root), environment=str(env), options=normalize(data.get('options', {})))
            for k in ('reference', 'secondary', 'dem', 'water'):
                value = str(data.get(k, '')).strip()
                if not value and k != 'water': raise ValueError('請選擇兩期 RSLC 與 DEM。')
                c[k] = str(Path(value).expanduser().resolve()) if value else ''
            paths = [c[k] for k in ('reference', 'secondary', 'dem', 'water') if c[k]]
            if c['reference'] == c['secondary']: raise ValueError('請選擇不同日期的兩份 RSLC。')
            for p in [env, *map(Path, paths), self.home]:
                if p == root or p in root.parents or root in p.parents:
                    raise ValueError('輸出資料夾不可包含輸入或環境，也不可放入環境目錄。')
            source = stamps(paths)
            key = uuid.uuid4().hex
            s = dict(id=key, config=c, created=time.time(), status='checking', stage='檢查資料與設定', source=source, error='')
            self.jobs[key] = s
            self.save(s)
            self._launch(s, 'check')
            return self.get(key)

    def start(self, key, reviewed=False):
        with self.lock:
            s = self.jobs.get(key)
            if not s or s['status'] != 'ready': raise ValueError('請先完成並通過此工作的檢查。')
            if reviewed is not True: raise ValueError('請先閱讀檢查結果並確認本次運算。')
            if self.active: raise ValueError('已有工作進行中。')
            self.verify(s)
            if Path(s['config']['root']).exists(): raise ValueError('輸出目錄已存在；請建立新工作以保護既有檔案。')
            needed = s['report']['disk_estimate_bytes']
            if shutil.disk_usage(Path(s['config']['root']).parent).free < needed:
                raise ValueError('磁碟空間低於保守估計，請選擇更大的輸出磁碟。')
            s.update(status='running', stage=self.run_stage, error='')
            self.save(s); self._launch(s, 'run')
            return self.get(key)

    def verify(self, s):
        if stamps([p['path'] for p in s['source']]) != s['source']:
            raise ValueError('輸入檔已改變，請重新建立檢查。')
        cfg = self.folder(s['id']) / self.settings_name
        if cfg.is_symlink() or digest(cfg) != s['config_sha256']:
            raise ValueError('產生的設定已被修改，請從介面重新建立工作。')

    def _launch(self, s, mode):
        self.active = s['id']; self.cancel.clear()
        self.thread = threading.Thread(target=self._work, args=(s, mode), daemon=True)
        self.thread.start()

    def _work(self, s, mode):
        folder = self.folder(s['id']); c = s['config']
        req, res = folder / (mode + '-request.json'), folder / (mode + '-result.json')
        atomic_json(req, dict(mode=mode, config=c, source=s['source'], expected_version=SUPPORTED_VERSION,
                              yaml=str(folder / self.settings_name), config_sha256=s.get('config_sha256')))
        args = [find_conda(c['environment']), 'run', '--no-capture-output', '-p', c['environment'],
                'python', str(self.worker), str(req), str(res)]
        env = isolated_env(folder / 'cache')
        for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
            env[k] = str(c['options']['threads'])
        try:
            with (folder / 'run.log').open('a') as log:
                log.write('\n=== ' + ('檢查' if mode == 'check' else '開始運算') + ' · ' + self.label + ' ===\n'); log.flush()
                with self.lock:
                    if self.cancel.is_set(): raise RuntimeError('已停止。')
                    proc = subprocess.Popen(args, cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    self.process = proc
                    s['pid']=proc.pid; self.save(s)
                try: code = proc.wait(timeout=240 if mode == 'check' else None)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL); proc.wait()
                    raise RuntimeError('資料檢查逾時，請確認檔案可讀取後重試。')
            if self.cancel.is_set(): raise RuntimeError('已停止，部分輸出保留在專案資料夾。')
            result = json.loads(res.read_text()) if res.exists() else {}
            if code != 0 or result.get('error'): raise RuntimeError(result.get('error') or f'工作結束碼 {code}；請查看日誌。')
            with self.lock:
                if mode == 'check':
                    s.update(report=result, status='ready', stage='檢查完成，等待確認', config_sha256=digest(folder / self.settings_name))
                else:
                    s.update(result=result, status='complete', stage='運算結束，請檢查科學品質')
                self.save(s)
        except Exception as e:
            with self.lock:
                s.update(status='stopped' if self.cancel.is_set() else 'failed', stage='工作已停止', error=str(e)); self.save(s)
        finally:
            with self.lock: self.process = None; self.active = None

    def stop(self, shutdown=False):
        with self.lock:
            if not self.active: return
            s=self.jobs[self.active]
            if s['status']=='orphaned':
                if not self._owns_process(s):
                    self.reconcile()
                    if self.active and not shutdown: raise ValueError('無法確認前次程序的身分；未傳送停止訊號。請先確認前次本機服務是否仍在運作。')
                    return
                os.killpg(s['pid'],signal.SIGTERM)
                def stop_orphan():
                    time.sleep(5)
                    with self.lock:
                        if self._owns_process(s):
                            try:os.killpg(s['pid'],signal.SIGKILL)
                            except ProcessLookupError:pass
                        self.reconcile()
                threading.Thread(target=stop_orphan,daemon=True).start()
                return
            self.cancel.set()
            proc = self.process
            if proc and proc.poll() is None:
                try: os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                def force():
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                threading.Thread(target=force, daemon=True).start()
