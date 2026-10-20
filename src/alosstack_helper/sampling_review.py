"""Local review artefacts. Preview generation never changes processing state."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
from urllib.parse import urlencode

from .core import atomic_json, find_conda
from .environment import isolated_env
from .sampling import completion


def stamp(path):
    p = Path(path)
    s = p.stat()
    return dict(path=str(p), size=s.st_size, mtime_ns=s.st_mtime_ns,
                ctime_ns=s.st_ctime_ns, inode=s.st_ino, device=s.st_dev)


class SamplingReviews:
    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.Lock()

    def context(self, key):
        if self.engine.active:
            raise ValueError('運算進行中，請等目前階段結束再載入影格預覽。')
        state = self.engine.require_processing_location(key)
        if (state['config'].get('workflow') != 'alosStack'
                or not state['config'].get('sampling_equalization') or not state.get('sampling')
                or state.get('status') not in ('sampling_review', 'sampling_ready')):
            raise ValueError('請先完成資料讀取，進入影格與取樣檢查。')
        root, proc, meta = self.engine.paths(state)
        folder = meta/'sampling-review'
        for p in (root, proc, meta, folder):
            if p.is_symlink():
                raise ValueError('預覽目錄不可使用符號連結。')
        folder.mkdir(exist_ok=True)
        plan_file = proc/'helper_sampling_plan.json'
        if plan_file.is_symlink():
            raise ValueError('取樣計畫不可使用符號連結。')
        plan = json.loads(plan_file.read_text())
        if plan != state['sampling']:
            raise ValueError('取樣計畫已改變，請先重新檢查。')
        if state['status'] == 'sampling_review' and state.get('sampling_result'):
            raise ValueError('目前仍在影格確認階段，卻有舊取樣成果，請先檢查專案狀態。')
        files = [plan_file]
        if state.get('sampling_result'):
            receipt = proc/'helper_sampling/receipt.json'
            approval = proc/'helper_sampling_approval.json'
            if receipt.parent.is_symlink() or receipt.is_symlink() or approval.is_symlink():
                raise ValueError('取樣完成紀錄不可使用符號連結。')
            if completion(state, proc) != state['sampling_result']:
                raise ValueError('取樣成果紀錄不一致，不能顯示為已完成成果。')
            files.extend((receipt, approval))
        elif state.get('status') == 'sampling_ready':
            raise ValueError('缺少取樣成果紀錄，請先檢查執行日誌。')
        binding = dict(config=state['config'], plan_id=plan['plan_id'],
                       files=[dict(**stamp(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files])
        return state, folder, binding

    def worker(self, state, folder, module, request):
        conda = find_conda(state['config']['env'])
        if not conda:
            raise ValueError('找不到所選 Conda 環境。')
        # Each worker owns disposable request/log files; managed artefacts are
        # published only after successful exit and fresh source checks.
        with tempfile.TemporaryDirectory(prefix='review-', dir=folder) as directory:
            temp = Path(directory)
            request_path, result = temp/'request.json', temp/'result.json'
            request_path.write_text(json.dumps(request))
            script = Path(__file__).with_name(module+'.py')
            args = [conda, 'run', '--no-capture-output', '-p', state['config']['env'],
                    'python', str(script), str(request_path), str(result)]
            with (temp/'worker.log').open('w+') as log:
                child = subprocess.Popen(args, cwd=temp, env=isolated_env(temp/'cache'),
                                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = child.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                    raise ValueError('預覽逾時，請檢查資料磁碟連線或減少候選影格。') from None
                if code or not result.is_file():
                    log.seek(0)
                    raise ValueError('預覽未完成：'+log.read()[-1800:])
            return json.loads(result.read_text())

    @staticmethod
    def current(report):
        try:
            if any(stamp(f['path']) != f for f in report['_files']):
                raise ValueError()
        except (OSError, ValueError):
            raise ValueError('來源或取樣成果已改變／離線，請重新載入影格地圖與預覽。') from None

    @staticmethod
    def public(report):
        return {k: v for k, v in report.items() if not k.startswith('_')}

    def review(self, key, extra_source=None):
        if not self.lock.acquire(blocking=False):
            raise ValueError('另一個影格預覽尚在讀取，請稍候。')
        try:
            state, folder, binding = self.context(key)
            cache = folder/'catalogue.json'
            if cache.is_symlink():
                raise ValueError('影格預覽清單不可使用符號連結。')
            # On normal refresh retain the last explicit comparison folder.
            previous = json.loads(cache.read_text()) if cache.is_file() else {}
            extra = previous.get('_extra_source', '') if extra_source is None else extra_source
            if not isinstance(extra, str) or len(extra) > 4096:
                raise ValueError('候選資料夾路徑不合法。')
            request = dict(config=state['config'], plan=state['sampling'],
                           post=state.get('sampling_result'), extra_source=extra.strip())
            result = self.worker(state, folder, 'sampling_review_worker', request)
            self.current(result)
            if self.context(key)[2] != binding:
                raise ValueError('讀取期間專案計畫已改變，請重新載入。')
            result.update(_binding=binding, _extra_source=extra.strip())
            atomic_json(cache, result)
            return self.public(result)
        finally:
            self.lock.release()

    def loaded(self, key):
        state, folder, binding = self.context(key)
        p = folder/'catalogue.json'
        if p.is_symlink() or not p.is_file():
            raise ValueError('請先載入影格地圖。')
        report = json.loads(p.read_text())
        if report.get('_binding') != binding:
            raise ValueError('專案階段或取樣結果已變更，請重新載入地圖。')
        self.current(report)
        return state, folder, report

    def preview(self, key, image_id, plan_id):
        if not re.fullmatch(r'[a-f0-9]{32}', str(image_id)):
            raise ValueError('無效影像代號。')
        if not self.lock.acquire(blocking=False):
            raise ValueError('另一個振幅預覽正在產生，請稍候。')
        try:
            state, folder, report = self.loaded(key)
            if report['plan_id'] != plan_id or image_id not in report['_image_specs']:
                raise ValueError('影像或計畫已改變，請重新載入。')
            output = folder/(image_id+'.png')
            sidecar = folder/(image_id+'.json')
            if output.is_symlink() or sidecar.is_symlink():
                raise ValueError('預覽成果不可使用符號連結。')
            result = self.cached(folder, image_id, report)
            if result is None:
                result = self.worker(state, folder, 'sampling_preview_worker', dict(
                    image=report['_image_specs'][image_id], output=str(output),
                    max_size=768, stretch=dict(low=2, high=98)))
                self.current(report)
                if self.context(key)[2] != report['_binding']:
                    raise ValueError('預覽期間專案已改變，請重新載入。')
                result.update(image_id=image_id, source_fingerprint=report['source_fingerprint'],
                              png_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
                atomic_json(sidecar, result)
            return dict(result, url='/api/sampling-preview-image?'+urlencode(dict(id=key, image=image_id)))
        finally:
            self.lock.release()

    @staticmethod
    def cached(folder, image_id, report):
        output, sidecar = folder/(image_id+'.png'), folder/(image_id+'.json')
        if output.is_symlink() or sidecar.is_symlink():
            raise ValueError('預覽成果不可使用符號連結。')
        if not output.is_file() or not sidecar.is_file() or output.stat().st_size > 8*1024**2:
            return None
        try:
            result = json.loads(sidecar.read_text())
            if (result.get('status') == 'complete' and result.get('image_id') == image_id
                    and result.get('source_fingerprint') == report['source_fingerprint']
                    and result.get('png_sha256') == hashlib.sha256(output.read_bytes()).hexdigest()):
                return result
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def image(self, key, image_id):
        if not re.fullmatch(r'[a-f0-9]{32}', str(image_id)):
            raise ValueError('無效影像代號。')
        _, folder, report = self.loaded(key)
        if image_id not in report['_image_specs']:
            raise ValueError('此影像不在本次檢查清單。')
        p = folder/(image_id+'.png')
        if self.cached(folder, image_id, report) is None:
            raise ValueError('預覽圖片尚未產生或已變更，請重新載入振幅預覽。')
        return p.read_bytes()
