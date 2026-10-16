"""Persistent serial ALOS preparation jobs; scientific dependencies stay in workers."""
from pathlib import Path
import html
import json
import os
import re
import signal
import subprocess
import time
import uuid

from .core import find_conda
from .environment import isolated_env
from .nisar_jobs import NisarJobs
from .alos_coverage import validate_review, MAX_PRODUCTS, MAX_SELECTION
from .alos_preparation_worker import plain_path, directory_identity, refresh_completed_items, verify_manifest_identity, write_json


class AlosPreparation(NisarJobs):
    worker = Path(__file__).with_name('alos_preparation_worker.py')
    label = 'ALOS 批次資料準備'

    def __init__(self, home):
        plain_path(Path(home) / 'alos-preparation')
        super().__init__(plain_path(home), 'alos-preparation')

    def folder(self, key):
        if not re.fullmatch(r'[0-9a-f]{32}', str(key)):
            raise ValueError('無效的 ALOS 資料準備代號。')
        return plain_path(self.home / key)

    def save(self, state):
        folder = self.folder(state['id'])
        folder.mkdir(parents=True, exist_ok=True)
        write_json(folder / 'state.json', state)

    def get(self, key):
        folder = self.folder(key)
        for name in ('run.log', 'progress.json'):
            plain_path(folder / name)
        state = super().get(key)
        progress = self.folder(key) / 'progress.json'
        if progress.is_file():
            try:
                state['progress'] = json.loads(progress.read_text())
            except (ValueError, OSError):
                pass
        if state['status'] not in ('running', 'checking', 'orphaned'):
            for item in state.get('progress', {}).get('items', []):
                if item['status'] in ('running', 'verifying', 'queued'):
                    item.update(status='stopped', error=item.get('error') or '尚未完成，另選新輸出位置後可重試。')
        items = state.get('progress', {}).get('items', [])
        invalidated = [p for p in items if p.get('status') == 'complete']
        refresh_completed_items(items)
        if any(p['status'] == 'failed' for p in invalidated):
            state['error'] = '先前通過檢查的來源或輸出已變更／未連線，請查看各產品原因。'
            if state['status'] in ('complete', 'partial'):
                state['status'] = 'partial' if any(p['status'] == 'complete' for p in items) else 'failed'
                state['stage'] = '先前結果目前無法確認；保留原始品質紀錄'
        if items:
            state['progress']['completed'] = sum(p.get('status') == 'complete' for p in items)
        if state.get('review'):
            try:
                indexed = {p['id']:p for p in state.get('report', {}).get('products', [])}
                for product_id in state['review'].get('selected_product_ids', []):
                    verify_manifest_identity(indexed[product_id])
            except (ValueError, OSError, KeyError, TypeError):
                state['source_stale'] = True
                state['review']['stale'] = True
        state['processing_unlocked'] = False
        return state

    def listing(self):
        self.reconcile()
        with self.lock:
            return [dict(id=s['id'], name=s['config']['name'], status=s['status'],
                         root=s['config'].get('root') or s['config']['source'], source=s['config']['source'])
                    for s in sorted(self.jobs.values(), key=lambda s:s['created'], reverse=True)]

    def create(self, data):
        with self.lock:
            if self.active:
                raise ValueError('請先完成或停止目前的 ALOS 資料準備。')
            source = str(data.get('source', '')).strip()
            if not source:
                raise ValueError('請選擇包含各日期產品的資料夾。')
            source = plain_path(source)
            env = Path(str(data.get('environment', '')).strip()).expanduser().resolve()
            if not source.is_dir():
                raise ValueError('找不到來源資料夾，請確認外接磁碟或 NAS 已連線。')
            if not (env / 'bin/python').is_file() or not find_conda(env):
                raise ValueError('請選擇有效的 ISCE2 Conda 環境。')
            key = uuid.uuid4().hex
            state = dict(id=key, status='checking', stage='掃描產品與覆蓋中繼資料', created=time.time(),
                         config=dict(source=str(source), environment=str(env), root='', tool='',
                                     name='ALOS 資料準備 · ' + source.name),
                         error='', progress=dict(items=[]), attempts=[], processing_unlocked=False)
            self.jobs[key] = state
            self.save(state)
            self._launch(state, 'check')
            return self.get(key)

    def start(self, key, data):
        with self.lock:
            if self.active:
                raise ValueError('已有資料準備工作進行中，請先等候或停止。')
            state = self.jobs.get(key)
            if not state or state['status'] not in ('ready', 'complete', 'partial', 'stopped', 'failed', 'interrupted') or not state.get('report'):
                raise ValueError('請先完成資料掃描。')
            if data.get('reviewed') is not True:
                raise ValueError('請先確認選取項目、輸出位置及空間需求。')
            ids = data.get('product_ids')
            if not isinstance(ids, list) or not ids or len(ids) > MAX_PRODUCTS or any(not isinstance(k, str) for k in ids) or len(ids) != len(set(ids)):
                raise ValueError('請勾選要轉換的產品。')
            rows = {p['id']:p for p in state['report']['products']}
            if any(k not in rows or not rows[k].get('convertible') for k in ids):
                raise ValueError('勾選內容不屬於可轉換的產品。')
            previous = self.get(key).get('progress', {}).get('items', [])
            done = {p['id'] for p in previous if p['status']=='complete'}
            if done.intersection(ids):
                raise ValueError('已完成的產品不會重複轉換，請選取尚未完成的項目。')
            root_text, tool_text = (str(data.get(k, '')).strip() for k in ('root', 'tool'))
            if not root_text or not tool_text:
                raise ValueError('請選擇官方工具及全新的輸出資料夾。')
            root, tool = (plain_path(v) for v in (root_text, tool_text))
            if root.exists() or not root.parent.is_dir():
                raise ValueError('輸出資料夾必須尚不存在，且上層磁碟已連線。')
            if any(attempt.get('root') == str(root) for attempt in state.get('attempts', [])):
                raise ValueError('不能重用前次輸出位置，請另選全新的資料夾。')
            for p in (Path(state['config']['source']), Path(state['config']['environment']), tool, self.home):
                if root==p or root in p.parents or p in root.parents:
                    raise ValueError('輸出不可放進來源、環境、工具或工作紀錄資料夾，也不能包含它們。')
            # Check in the worker too, immediately before loading any tool code.
            from .alos4_conversion import verify_tool
            verify_tool(tool)
            state['config'].update(root=str(root), tool=str(tool), output_parent_identity=directory_identity(root.parent))
            state.update(status='running', stage='依序轉換與完整樣本檢查', error='', selected=ids,
                         previous_items=previous, source_stale=False)
            state['attempts'].append(dict(root=str(root), selected=ids, started=time.time()))
            self.save(state)
            self._launch(state, 'run')
            return self.get(key)

    def _request(self, state, mode):
        folder = self.folder(state['id'])
        return dict(mode=mode, config=state['config'], report=state.get('report'),
                    selected=state.get('selected'), previous_items=state.get('previous_items', []),
                    attempted_roots=[p['root'] for p in state.get('attempts', [])[:-1]],
                    inventory=str(folder / 'inventory.json'), progress=str(folder / 'progress.json'))

    def _command(self, state, request, result):
        env = state['config']['environment']
        return [find_conda(env), 'run', '--no-capture-output', '-p', env, 'python', str(self.worker), str(request), str(result), '--parent-fd', '0']

    @staticmethod
    def _end_process(proc):
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            except ProcessLookupError:
                pass

    def _work(self, state, mode):
        proc = None
        read_fd = write_fd = None
        try:
            folder = self.folder(state['id'])
            req, res = folder / (mode + '-request.json'), plain_path(folder / (mode + '-result.json'))
            if res.exists():
                res.unlink()
            write_json(req, self._request(state, mode))
            read_fd, write_fd = os.pipe()
            with plain_path(folder / 'run.log').open('a') as log:
                log.write('\n=== ' + ('掃描' if mode=='check' else '批次轉換與品質檢查') + ' ===\n')
                log.flush()
                with self.lock:
                    if self.cancel.is_set():
                        raise RuntimeError('已停止。')
                    proc = subprocess.Popen(self._command(state, req, res), cwd=folder,
                                            env=isolated_env(folder / 'cache'), stdout=log,
                                            stderr=subprocess.STDOUT, start_new_session=True, stdin=read_fd)
                    os.close(read_fd)
                    read_fd = None
                    self.process = proc
                    state['pid'] = proc.pid
                    self.save(state)
                try:
                    code = proc.wait(timeout=600 if mode=='check' else None)
                except subprocess.TimeoutExpired:
                    self._end_process(proc)
                    raise RuntimeError('掃描超時，請確認磁碟連線或選擇較小的資料夾。') from None
            if self.cancel.is_set():
                raise RuntimeError('工作已停止；完成產品與半成品已保留，可在新位置重試未完成項目。')
            result = json.loads(plain_path(res).read_text()) if res.exists() else {}
            if code or result.get('error'):
                raise RuntimeError(result.get('error') or '資料準備程序失敗，請查看日誌。')
            with self.lock:
                if mode=='check':
                    state.update(report=result, status='ready', stage='掃描完成，請選擇產品')
                else:
                    items = result['items']
                    refresh_completed_items(items)
                    write_json(folder / 'progress.json', dict(items=items, total=len(items), completed=sum(i['status']=='complete' for i in items), stage='批次完成'))
                    successes = sum(i['status']=='complete' for i in items)
                    failures = sum(i['status']!='complete' for i in items)
                    state.update(result=result, status='partial' if failures and successes else 'failed' if failures else 'complete',
                                 stage=f'格式與樣本檢查通過 {successes} 份；未完成 {failures} 份',
                                 error='部分產品未完成，請查看各列原因。' if failures else '')
                self.save(state)
        except Exception as error:
            with self.lock:
                state.update(status='stopped' if self.cancel.is_set() else 'failed', stage='工作已停止', error=str(error))
                self.save(state)
        finally:
            self._end_process(proc)
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
            with self.lock:
                self.process = None
                self.active = None
                state.pop('pid', None)
                try:
                    self.save(state)
                    self._export(state['id'])
                except (OSError, ValueError) as error:
                    state['error'] = (state.get('error', '') + '；紀錄無法儲存：' + str(error)).lstrip('；')

    def coverage(self, key, data):
        with self.lock:
            if self.active:
                raise ValueError('請等掃描／轉換結束，再檢查影格。')
            state = self.jobs.get(key)
            if not state or not state.get('report'):
                raise ValueError('請先完成資料掃描。')
            for k in ('left_ids', 'right_ids'):
                ids = data.get(k)
                if not isinstance(ids, list) or not ids or len(ids)>MAX_SELECTION or any(not isinstance(x, str) for x in ids) or len(ids)!=len(set(ids)):
                    raise ValueError('請分別選擇兩組待比較影格。')
            left, right = set(data['left_ids']), set(data['right_ids'])
            known = {p['id'] for p in state['report']['products']}
            if left & right or (left | right) - known or len(left | right)>MAX_SELECTION:
                raise ValueError(f'兩組須選擇不同且屬於本次掃描的影格，合計最多 {MAX_SELECTION} 份。')
            previous_status, previous_stage = state['status'], state.get('stage', '')
            self.active = key
            self.cancel.clear()
            state.update(status='checking', stage='核對影格覆蓋')
        proc = None
        read_fd = write_fd = None
        try:
            with self.lock:
                self.save(state)
            folder = self.folder(key)
            req, res = folder / 'coverage-request.json', plain_path(folder / 'coverage-result.json')
            if res.exists():
                res.unlink()
            write_json(req, dict(self._request(state, 'coverage'), left_ids=data['left_ids'], right_ids=data['right_ids'], progress=None))
            read_fd, write_fd = os.pipe()
            with plain_path(folder / 'coverage.log').open('w') as log:
                with self.lock:
                    if self.cancel.is_set():
                        raise ValueError('覆蓋檢查已停止。')
                    proc = subprocess.Popen(self._command(state, req, res), cwd=folder,
                                            env=isolated_env(folder / 'cache'), stdout=log,
                                            stderr=subprocess.STDOUT, start_new_session=True, stdin=read_fd)
                    os.close(read_fd)
                    read_fd = None
                    self.process = proc
                    state['pid'] = proc.pid
                    self.save(state)
                try:
                    code = proc.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    self._end_process(proc)
                    raise ValueError('覆蓋檢查超時，請確認來源磁碟連線。') from None
            if self.cancel.is_set():
                raise ValueError('覆蓋檢查已停止；原始資料未更動。')
            value = json.loads(plain_path(res).read_text()) if res.exists() else {}
            if code or value.get('error'):
                raise ValueError(value.get('error') or '覆蓋檢查失敗，請查看來源與科學環境。')
            with self.lock:
                state.update(inspection=value, source_stale=False)
                if state.get('review'):
                    state['review']['stale'] = state['review'].get('revision') != value['revision']
            return value
        except Exception:
            with self.lock:
                state['source_stale'] = True
                if state.get('review'):
                    state['review']['stale'] = True
            raise
        finally:
            self._end_process(proc)
            for fd in (read_fd, write_fd):
                if fd is not None:
                    os.close(fd)
            with self.lock:
                state.update(status=previous_status, stage=previous_stage)
                state.pop('pid', None)
                self.active = self.process = None
                self.save(state)
                self._export(key)

    def review(self, key, data):
        inspection = self.coverage(key, data)
        with self.lock:
            result = validate_review(data, inspection)
            result.update(saved=time.time(), stale=False, processing_unlocked=False)
            self.jobs[key]['review'] = result
            self.save(self.jobs[key])
            self._export(key)
            return result

    def _export(self, key):
        value = self.get(key)
        value.pop('log', None)
        value['scope'] = '格式、樣本保存及影格人工確認；未驗證跨衛星干涉品質，也不會開放干涉執行。'
        write_json(self.folder(key) / 'report.json', value)
        esc = lambda v: html.escape(str(v if v is not None else '未確認'))
        rows = []
        items = {v['id']:v for v in value.get('progress', {}).get('items', [])}
        for product in value.get('report', {}).get('products', []):
            item = items.get(product['id'], {})
            rows.append('<tr>' + ''.join('<td>'+esc(v)+'</td>' for v in (
                product.get('date'), product.get('sensor'), product.get('name'),
                ' / '.join(product.get('polarizations', [])), product.get('footprint_status'),
                item.get('status', '尚未轉換'), item.get('error') or product.get('reason') or '')) + '</tr>')
        review = value.get('review') or {}
        text = '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>ALOS 資料準備報告</title>'
        text += '<style>body{font:16px system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:24px;color:#193c32}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd8d0;padding:10px;word-break:break-word}pre{white-space:pre-wrap;overflow-wrap:anywhere}.notice{background:#eef3eb;padding:16px}</style>'
        text += '<h1>ALOS 資料準備報告</h1><p class="notice">'+esc(value['scope'])+'</p>'
        text += '<p>工作：'+esc(value['config']['name'])+' · '+esc(value['stage'])+'</p>'
        text += '<table><thead><tr>'+''.join('<th>'+s+'</th>' for s in ('日期','衛星','產品','極化','覆蓋資料','轉換狀態','說明'))+'</tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
        text += '<h2>人工確認</h2><p>'+esc(review.get('outcome','尚未確認'))+(' · 來源／選擇已變更，需重查' if review.get('stale') or value.get('source_stale') else '')+'</p><p>'+esc(review.get('notes',''))+'</p>'
        text += '<h2>完整設定與驗證紀錄</h2><pre>'+esc(json.dumps(value, ensure_ascii=False, indent=2))+'</pre></html>'
        plain_path(self.folder(key) / 'report.html').write_text(text)

    def artifact(self, key, name):
        if key not in self.jobs or name not in ('report.json', 'report.html'):
            raise ValueError('無效的資料準備報告。')
        self._export(key)
        p = plain_path(self.folder(key) / name)
        if p.is_symlink() or not p.is_file():
            raise ValueError('報告尚未產生，請等工作完成。')
        return p

    def shutdown(self):
        self.stop(shutdown=True)
        if self.thread:
            self.thread.join(timeout=7)
