from pathlib import Path
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid

from .core import (LOOKS, PAIR, atomic_json, declared_pairs, digest, inspect_data, parse_cmd3, find_conda,
                   safe_path, split_pre_review, validate_network, write_xml)
from .inputs import prepare_raster
from .environment import isolated_env
from .recovery import Recovery, PHASES, process_alive

RESOLVE_CONFIG_SCRIPT = "import sys, os, json\nsys.path.insert(0, os.path.join(os.environ['ISCE_STACK'], 'alosStack'))\nfrom StackPulic import loadStackUserParameters\nsys.argv = ['helper', 'alosStack.xml']\ns = loadStackUserParameters('alosStack.xml')\njson.dump(dict(estimate=bool(s.doIon), apply=bool(s.applyIon), subsequent=s.numberOfSubsequentDates, subsequent_ion=s.numberOfSubsequentDatesIon), open('helper_resolved.json', 'w'))\n"

class Engine:
    def __init__(self, home):
        self.home = Path(home); self.home.mkdir(parents=True, exist_ok=True)
        self.registry = self.home / 'projects.json'
        self.projects = json.loads(self.registry.read_text()) if self.registry.exists() else {}
        self.lock = threading.Lock(); self.process = None; self.active = None
        self.cancel = threading.Event()
        self.resume_phase = None
        self.worker_action = None
        for key in self.projects:
            s = self.get(key)
            if s['status'] == 'running':
                s.update(status='failed', error='服務已中斷；請先檢查殘留運算與產物，不會自動重跑。')
                self.save(s)

    def get(self, key):
        return json.loads((Path(self.projects[key]) / '.helper/state.json').read_text())

    def save(self, state):
        atomic_json(Path(state['config']['root']) / '.helper/state.json', state)

    def list(self):
        results = [self.get(k) for k in self.projects]
        for s in results:
            s['can_retry_prepare'] = self.can_retry_prepare(s)
        return results

    def can_retry_prepare(self, s):
        if s['config'].get('workflow')=='alos2App':
            from .pair import PairWorkflow
            return PairWorkflow(self).can_retry(s)
        # Only retry preparation, never resume a partially executed science phase.
        if s['status'] != 'failed' or s.get('history') or s.get('hashes'):
            return False
        if s.get('failed_action', 'prepare') != 'prepare':
            return False
        proc = self.paths(s)[1]
        return not any((proc/name).exists() for name in
                       ('dates', 'dates_resampled', 'pairs', 'pairs_ion', 'dates_ion', 'baseline', 'fig_ion'))

    def create(self, c):
        if c.get('workflow')=='alos2App':
            from .pair import PairWorkflow
            return PairWorkflow(self).create(c)
        if c.get('workflow', 'alosStack') != 'alosStack':
            raise ValueError('此處理方式尚未開放，請選 ALOS-2 多日期。')
        c['workflow'] = 'alosStack'
        root = safe_path(c['root'])
        if root.exists() and any(root.iterdir()):
            raise ValueError('請選一個新的空專案資料夾；第一版不接管既有運算成果。')
        for name in ('source', 'env', 'dem', 'dem_geo', 'wbd'):
            c[name] = str(safe_path(c[name]))
        if not Path(c['env'], 'bin/python').is_file():
            raise ValueError('請選擇含 bin/python 的 Conda 環境資料夾。')
        dates = inspect_data(c['source'])
        if len(dates) < 2 or any(not x['leaders'] or not x['images'] for x in dates):
            raise ValueError('需至少兩個 YYMMDD 資料夾，且每個包含 LED-ALOS2 與 IMG 檔。ZIP 請先依研究室流程整理。')
        problems=[x['date']+'：'+','.join(x.get('warnings',[])) for x in dates if x.get('warnings')]
        if problems:raise ValueError('資料整理需要檢查：'+'；'.join(problems))
        modes={m for x in dates for m in x['modes']}
        if len(modes)>1:raise ValueError('不同日期的觀測模式不一致，請先依軌道與模式整理。')
        pol=c.get('properties',{}).get('polarization','').upper()
        if pol and any(pol not in x.get('polarizations',[]) for x in dates):raise ValueError('有日期缺少所選極化 '+pol+'。')
        if c['reference'] not in [x['date'] for x in dates]:
            raise ValueError('參考日期不在資料清單中。')
        source = Path(c['source'])
        if root == source or source in root.parents or root in source.parents:
            raise ValueError('專案與來源資料夾不可互相包含。')
        for name in ('dem', 'dem_geo', 'wbd'):
            p = Path(c[name])
            if not p.is_file() or not Path(str(p) + '.xml').is_file():
                raise ValueError(f'{name} 必須存在，且具有同名 .xml 描述檔。')
        stack = Path(c['env'], 'share/isce2/alosStack')
        if not (stack / 'create_cmds.py').is_file():
            raise ValueError('此環境找不到 share/isce2/alosStack。')
        conda = find_conda(c['env'])
        if not conda:
            raise ValueError('找不到 conda；請从可使用 conda 的終端機啟動本工具。')
        c.update(root=str(root), data=str(root / 'data') if c.get('copy') else c['source'], conda=conda)
        # Validate XML without leaving a partly created project on parameter failure.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            write_xml(c, Path(tmp) / 'check.xml')
        root.mkdir(parents=True, exist_ok=True)
        (root / 'proc').mkdir(); (root / '.helper').mkdir()
        write_xml(c, root / 'proc/alosStack.xml')
        key = uuid.uuid4().hex[:12]
        state = dict(id=key, config=c, dates=dates, status='new', stage='等待準備', history=[], excluded=[], recovery_enabled=True)
        self.save(state); self.projects[key] = str(root); atomic_json(self.registry, self.projects)
        return state

    def paths(self, state):
        root = Path(state['config']['root'])
        return root, root / 'proc', root / '.helper'

    def log(self, state, text):
        with (self.paths(state)[2] / 'run.log').open('a') as f:
            f.write(text + '\n')

    def run(self, s, args, cwd=None, conda=False):
        if self.cancel.is_set():
            raise RuntimeError('已停止，部分輸出可能不完整。')
        c = s['config']; root, proc, meta = self.paths(s)
        env = isolated_env(meta/'cache')
        if c.get('workflow')=='alos2App':
            env.update(OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MPLBACKEND='Agg',MPLCONFIGDIR=str(meta/'cache/matplotlib'))
        if conda:
            selected_conda = find_conda(c['env'])
            if not selected_conda:
                raise ValueError('所選環境找不到 Conda，請重新選擇環境。')
            stack = str(Path(c['env']) / 'share/isce2/alosStack')
            # Pin the selected environment after activation, including when Conda
            # reactivates an already-active environment with another Python first.
            plotting_bin = Path(__file__).resolve().parents[2]/'.runtime/bin'
            plotting_path = str(plotting_bin) if all((plotting_bin/x).is_file() for x in ('mdx','montage','convert')) else ''
            managed = Path(os.environ.get('SAR_WORKBENCH_HOME', str(Path.home()/'sar-workbench')))/'runtime/envs'
            # The managed distribution supplies its own native rendering tools.
            # A legacy checkout's optional compatibility shim must not override them.
            if c.get('workflow')=='alos2App' or managed.resolve() in Path(c['env']).resolve().parents:
                plotting_path=''
            wrapper = ': "${ISCE_HOME:?ISCE_HOME is not set by the selected environment}"; export PATH="${3:+$3:}$2/bin:$1:$ISCE_HOME/bin:$ISCE_HOME/applications:$PATH"; shift 3; exec "$@"'
            args = [selected_conda, 'run', '--no-capture-output', '-p', c['env'],
                    '/bin/bash', '-c', wrapper, 'alos-helper', stack, c['env'], plotting_path, *map(str, args)]
        self.log(s, '$ ' + shlex.join(list(map(str, args))))
        with (meta / 'run.log').open('a') as log:
            p = subprocess.Popen(args, cwd=cwd or proc, stdout=log, stderr=subprocess.STDOUT,
                                 env=env, start_new_session=True)
            self.process = p
            try: atomic_json(meta/'worker-process.json', {'pgid':p.pid})
            except Exception:
                try: os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                p.wait(); self.process=None
                raise
            if self.cancel.is_set():
                os.killpg(p.pid, signal.SIGTERM)
            code = p.wait(); self.process = None
        if process_alive(meta):
            raise RuntimeError('主程序已結束，但仍有子程序存在；請先停止殘留運算，不能開始下一階段。')
        (meta/'worker-process.json').unlink(missing_ok=True)
        if code or self.cancel.is_set():
            raise RuntimeError(f'指令停止（結束碼 {code}），請查看日誌。未自動重試。')

    def shell(self, s, path):
        # -u cannot be enabled: upstream intentionally uses unset parallel variables.
        self.run(s, ['/bin/bash', '-eE', '-o', 'pipefail', str(path)], conda=True)

    def launch(self, key, action, excluded=None, options=None):
        with self.lock:
            if self.active:
                raise ValueError('已有工作執行中，請等待或停止。')
            s = self.get(key)
            if process_alive(self.paths(s)[2]):
                raise ValueError('仍有先前工作留下的運算程序，請先確認已停止。')
            if s['config'].get('workflow')=='alos2App':
                from .pair import PairWorkflow
                PairWorkflow(self).validate_action(s,action,options or {})
                self.active=key;self.cancel.clear();s.update(status='running',error='');self.save(s)
                threading.Thread(target=self.worker,args=(s,action),daemon=True).start()
                return
            if action == 'recover':
                report = Recovery(self,s).report()
                if not report['can_recover']: raise ValueError(report.get('blocked','不能復原。'))
                if not options or options.get('confirmed') is not True or options.get('snapshot') != report['snapshot']['id']:
                    raise ValueError('請先檢視最新診斷並確認復原範圍。')
                self.active=key; self.cancel.clear()
                s.update(status='running',stage='正在驗證復原副本（大量檔案可能需要較久）')
                self.save(s)
                threading.Thread(target=self.recover_worker,args=(s,options['snapshot']),daemon=True).start()
                return
            expected = {'prepare': 'new', 'run': 'ready', 'continue': 'review'}
            retry_prepare = action == 'prepare' and self.can_retry_prepare(s)
            if s['status'] != expected.get(action) and not retry_prepare:
                raise ValueError('目前階段不允許這個操作。')
            if action == 'continue':
                self.review(s, excluded or [])
                s['excluded'] = excluded or []
            self.active = key; self.cancel.clear()
            s.update(status='running', error=''); self.save(s)
            threading.Thread(target=self.worker, args=(s, action), daemon=True).start()

    def checkpoint(self, s, label, operation):
        if self.resume_phase:
            if label != self.resume_phase: return
            self.resume_phase = None
        if s.get('recovery_enabled') and label in PHASES:
            s['stage']='保存階段前副本 · '+label; self.save(s)
            self.log(s, '正在保存並核對復原副本：'+label)
            try: Recovery(self,s).capture(label,self.worker_action)
            except Exception:
                # Nothing scientific has run if the very first baseline failed.
                if label=='cmd1 · 對位與幾何' and not s.get('recovery'):
                    s['retry_first_snapshot']=True
                raise
            s.pop('retry_first_snapshot',None)
        s['stage'] = label; self.save(s); self.log(s, '\n=== ' + label + ' ===')
        operation()
        s['history'].append({'stage': label, 'time': time.strftime('%Y-%m-%d %H:%M:%S')})
        self.save(s)

    def recovery_enable(self, key, enabled):
        with self.lock:
            s=self.get(key)
            if self.active or process_alive(self.paths(s)[2]): raise ValueError('運算中不能變更復原保護。')
            if s['config'].get('workflow','alosStack')!='alosStack' or s['status'] not in ('new','ready','review'):
                raise ValueError('只能在多日期運算開始前或人工判讀時變更後續保護。')
            s['recovery_enabled']=bool(enabled); self.save(s)

    def recover_worker(self,s,snapshot):
        try:
            action, phase = Recovery(self,s).restore(snapshot)
            self.resume_phase=phase
        except Exception as e:
            s.update(status='failed',error=str(e),stage='復原已停止；現有資料已保留')
            self.log(s,'RECOVERY ERROR: '+str(e));self.save(s)
            with self.lock: self.active=None
            return
        self.worker(s,action)

    def verify_commands(self, s):
        _, proc, _ = self.paths(s)
        for name, hash_ in s['hashes'].items():
            if digest(proc / name) != hash_:
                raise ValueError('設定或指令在產生後已變更，已停止以避免混用。')

    def finish(self, s, science_pairs):
        _, proc, meta = self.paths(s)
        self.checkpoint(s, 'cmd4 · 解纏與地理編碼', lambda: self.shell(s, proc/'cmd_4.sh'))
        if not science_pairs:
            raise ValueError('沒有預期的干涉配對。')
        for pair in science_pairs:
            products = list((proc/'pairs'/pair).rglob('*.unw.geo'))
            if not products or any(not p.stat().st_size for p in products):
                raise ValueError('缺少非空的地理編碼解纏結果：'+pair)
        if s.get('recovery_enabled'): Recovery(self,s).check_rasters(proc)
        s.update(status='complete', stage='運算結束，請進行科學品質檢查')
        atomic_json(meta/'report.json', s)

    def worker(self, s, action):
        self.worker_action=action
        try:
            root, proc, meta = self.paths(s); c = s['config']
            if c.get('workflow')=='alos2App':
                from .pair import PairWorkflow
                PairWorkflow(self).worker(s,action)
                return
            if action == 'prepare':
                def prepare():
                    staged = dict(c)
                    for key in ('dem', 'dem_geo', 'wbd'):
                        staged[key] = prepare_raster(c[key], meta/'inputs'/key)
                    backup = meta/'alosStack.before-input-normalization.xml'
                    if not backup.exists():
                        shutil.copy2(proc/'alosStack.xml', backup)
                    write_xml(staged, proc/'alosStack.xml')
                    s['prepared_inputs'] = {key:staged[key] for key in ('dem','dem_geo','wbd')}
                    self.log(s, '已建立專案內 DEM/WBD 描述檔並修正絕對路徑；來源資料保持不變。')
                    if c.get('copy'):
                        Path(c['data']).mkdir(exist_ok=True)
                        required = sum(p.stat().st_size for p in Path(c['source']).rglob('*') if p.is_file())
                        if shutil.disk_usage(root).free < required:
                            raise ValueError('目的地空間不足以容納來源資料。')
                        self.run(s, ['rsync', '-a', '--partial', '--progress', c['source']+'/', c['data']+'/'])
                        # Check content rather than trusting timestamps alone.
                        self.run(s, ['rsync', '-rcn', '--out-format=%n', c['source']+'/', c['data']+'/'])
                        for p in Path(c['source']).rglob('*'):
                            if self.cancel.is_set(): raise RuntimeError('已停止驗證。')
                            if p.is_file():
                                target = Path(c['data']) / p.relative_to(c['source'])
                                if not target.is_file() or digest(p) != digest(target):
                                    raise ValueError('複製後檔案驗證失敗：' + p.name)
                    # Resolve omitted properties with this installation, not UI defaults.
                    self.run(s, ['python', '-c', RESOLVE_CONFIG_SCRIPT], conda=True)
                    s['ion'] = json.loads((proc/'helper_resolved.json').read_text())
                    requirements = ['imageMath.py'] + (['mdx', 'montage', 'convert'] if s['ion']['estimate'] else [])
                    self.run(s, ['python', '-c', 'import isce, isceobj, numpy, shutil; found={x:shutil.which(x) for x in '+repr(requirements)+'}; print("工具路徑:",found); missing=[x for x,p in found.items() if not p];\nif missing: raise RuntimeError("找不到必要工具："+", ".join(missing)+"；請檢查所選 ISCE2 環境的安裝與搜尋路徑。")'], conda=True)
                    if s['ion']['estimate']:
                        self.run(s, ['python', str(Path(__file__).with_name('plot_check.py'))], conda=True)
                    # Test the same /usr/bin/env python3 entry point used by cmd1.
                    self.run(s, [str(Path(c['env'])/'share/isce2/alosStack/read_data.py'), '--help'], conda=True)
                    self.run(s, ['python', str(Path(c['env'])/'share/isce2/alosStack/create_cmds.py'), '-stack_par', 'alosStack.xml'], conda=True)
                    text = (proc / 'cmd_3.sh').read_text()
                    names = ['alosStack.xml', 'cmd_1.sh', 'cmd_2.sh', 'cmd_3.sh', 'cmd_4.sh', 'helper_resolved.json']
                    s['expected_pairs'] = declared_pairs((proc/'cmd_2.sh').read_text(), 'insarpair')
                    if s['ion']['estimate']:
                        (proc / 'helper_cmd3_before_review.sh').write_text(split_pre_review(text))
                        s['looks'], _ = parse_cmd3(text)
                        s['expected_ion_pairs'] = declared_pairs(text, 'ionpair')
                        names.append('helper_cmd3_before_review.sh')
                    from .confirmation import compare_generated
                    compare_generated(s,proc,meta)
                    for file in [proc/name for name in names if name.endswith('.sh')]:
                        self.run(s, ['/bin/bash', '-n', str(file)])
                    s['hashes'] = {name: digest(proc/name) for name in names}
                    for p in (meta/'inputs').rglob('*'):
                        if p.suffix in ('.xml', '.vrt'):
                            s['hashes'][os.path.relpath(p, proc)] = digest(p)
                self.checkpoint(s, '準備資料與產生指令', prepare)
                s.update(status='ready', stage='已準備，可開始運算')
            elif action == 'run':
                self.verify_commands(s)
                for name, label in [('cmd_1.sh', 'cmd1 · 對位與幾何'), ('cmd_2.sh', 'cmd2 · 干涉配對')]:
                    self.checkpoint(s, label, lambda name=name: self.shell(s, proc/name))
                if not s.get('ion', {}).get('estimate', True):
                    self.log(s, '電離層估計已關閉：跳過 cmd3、ion_ls 與 ion_correct。')
                    self.finish(s, s['expected_pairs'])
                    return
                self.checkpoint(s, 'cmd3 · 電離層估計', lambda: self.shell(s, proc/'helper_cmd3_before_review.sh'))
                # Upstream ion_check produces TIFF, which browsers cannot reliably display.
                def previews():
                    folder=proc/'fig_ion'
                    for p in sorted(folder.glob('*')):
                        if p.suffix.lower() in ('.tif','.tiff'):
                            output=folder/'helper_preview'/(p.stem+'.png')
                            output.parent.mkdir(exist_ok=True)
                            self.run(s, ['convert', str(p), str(output)], conda=True)
                self.checkpoint(s, '產生瀏覽器檢查圖片', previews)
                review = self.review(s, [])
                if not review['images']:
                    raise ValueError('沒有找到檢查圖片，請查看 ion_check.py 的執行紀錄。')
                s.update(status='review', stage='等待你檢查電離層圖片')
            elif action == 'continue':
                self.verify_commands(s)
                review = self.review(s, s['excluded'])
                options, flags = parse_cmd3((proc/'cmd_3.sh').read_text())
                stack = Path(c['env'])/'share/isce2/alosStack'
                args = ['python', str(stack/'ion_ls.py')]
                for k, v in options.items(): args.extend([k, v])
                args.extend(flags)
                if s['excluded']: args.extend(['-exc_pair', *s['excluded']])
                atomic_json(meta/'review.json', {'excluded':s['excluded'], 'used':review['used'], 'args':args, 'cmd3_sha256':s['hashes']['cmd_3.sh']})
                self.checkpoint(s, '估計各日期電離層', lambda: self.run(s, args, conda=True))
                def correct():
                    nr = int(options['-nrlks1'])*int(options['-nrlks2'])
                    na = int(options['-nalks1'])*int(options['-nalks2'])
                    for date in review['required']:
                        p = proc/'dates_ion'/f'filt_ion_{date}_{nr}rlks_{na}alks.ion'
                        if not p.is_file() or not p.stat().st_size:
                            raise ValueError('缺少日期電離層產物：'+str(p))
                    for pair in review['science_pairs']:
                        a, b = pair.split('-')
                        argv = ['python', str(stack/'ion_correct.py'), '-ion_dir', '../../dates_ion', '-ref_date', a, '-sec_date', b]
                        for k in LOOKS[:4]: argv.extend(['-'+k, options['-'+k]])
                        self.run(s, argv, cwd=proc/'pairs'/pair, conda=True)
                if s.get('ion', {}).get('apply', True):
                    self.checkpoint(s, '套用電離層校正', correct)
                else:
                    self.log(s, '套用電離層校正已關閉：保留估計結果，不執行 ion_correct。')
                self.finish(s, review['science_pairs'])
        except Exception as e:
            s.update(failed_phase=s.get('stage'),status='failed', error=str(e), stage='已停止，需要檢查', failed_action=action)
            if s.pop('retry_first_snapshot',False):
                s.update(status='ready',stage='尚未開始 cmd1；請處理副本問題後重試，或關閉復原保護')
            self.log(s, 'ERROR: '+str(e))
        finally:
            self.resume_phase=None
            self.worker_action=None
            self.save(s)
            with self.lock: self.active = None

    def review(self, s, excluded):
        _, proc, _ = self.paths(s)
        options, _ = parse_cmd3((proc/'cmd_3.sh').read_text())
        pairs = sorted(p.name for p in (proc/'pairs_ion').iterdir() if p.is_dir() and PAIR.fullmatch(p.name))
        science = sorted(p.name for p in (proc/'pairs').iterdir() if p.is_dir() and PAIR.fullmatch(p.name))
        if not science: raise ValueError('沒有找到需要校正的干涉配對。')
        if s.get('expected_pairs', science) != science or s.get('expected_ion_pairs', pairs) != pairs:
            raise ValueError('實際配對資料夾與原始指令清單不一致，請檢查缺漏或額外產物。')
        required = sorted({d for p in science for d in p.split('-')})
        used = validate_network(pairs, excluded, required if s.get('ion', {}).get('apply', True) else [])
        nr = int(options['-nrlks1'])*int(options['-nrlks_ion'])
        na = int(options['-nalks1'])*int(options['-nalks_ion'])
        for pair in used:
            for ext in ('ion', 'std', 'win'):
                file = proc/'pairs_ion'/pair/'ion/ion_cal'/f'filt_ion_{nr}rlks_{na}alks.{ext}'
                if not file.is_file() or not file.stat().st_size:
                    raise ValueError('缺少電離層檔案：'+str(file))
        images = [str(p.relative_to(proc/'fig_ion')) for p in (proc/'fig_ion').rglob('*') if p.suffix.lower() in ('.png','.jpg','.jpeg')]
        return dict(pairs=pairs, science_pairs=science, required=required, used=used, images=images, options=options)

    def stop(self):
        self.cancel.set()
        p = self.process
        if p and p.poll() is None:
            os.killpg(p.pid, signal.SIGTERM)
            def kill_later():
                time.sleep(3)
                if p.poll() is None:
                    try: os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
            threading.Thread(target=kill_later, daemon=True).start()
