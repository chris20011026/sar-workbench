"""Authenticated library boundary; science packages stay out of the web server."""
from pathlib import Path
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import uuid
from .core import find_conda, atomic_json
from .environment import isolated_env


class Library:
    def __init__(self, home):
        self.home=Path(home)/'library'
        self.lock=threading.Lock()

    def _file(self,key,suffix='.json'):
        if not re.fullmatch(r'[0-9a-f]{32}',str(key)):raise ValueError('無效的資料紀錄')
        return self.home/(key+suffix)

    def listing(self):
        if not self.home.exists():return []
        rows=[]
        for p in sorted(self.home.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:100]:
            try:
                v=json.loads(p.read_text())
                if v.get('kind')=='scan':rows.append(dict(id=p.stem,root=v['root'],count=len(v['products'])))
            except (OSError,ValueError,KeyError):continue
        return rows[:20]

    def get(self,key):
        value=json.loads(self._file(key).read_text())
        if value.get('kind')!='scan':raise ValueError('不是掃描紀錄')
        return value

    def call(self, environment, request, output=None):
        prefix=Path(environment).expanduser().resolve();conda=find_conda(prefix)
        if not conda or not (prefix/'bin/python').is_file():raise ValueError('請選擇有效的 Conda 科學環境（需 h5py、pyproj、Pillow、matplotlib；ALOS LED 另需 ISCE2）')
        if not self.lock.acquire(blocking=False):raise ValueError('目前正在讀取資料，請稍候再試')
        try:
            with tempfile.TemporaryDirectory(prefix='sar-library-') as directory:
                folder=Path(directory);req=folder/'request.json';res=folder/'response.json';png=folder/'preview.png'
                req.write_text(json.dumps(request,ensure_ascii=False,allow_nan=False))
                with (folder/'worker.log').open('w+') as log:
                    proc=subprocess.Popen([conda,'run','--no-capture-output','-p',str(prefix),'python',str(Path(__file__).with_name('library_worker.py')),str(req),str(res),str(png)],cwd=folder,env=isolated_env(folder/'cache'),stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    try:code=proc.wait(timeout=180)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL);proc.wait();raise ValueError('讀取超過三分鐘，請選較小的資料夾或確認來源磁碟連線') from None
                    if not res.exists():
                        log.seek(0);raise ValueError('科學環境無法讀取資料：'+log.read()[-1600:])
                result=json.loads(res.read_text())
                if code or result.get('error'):raise ValueError(result.get('error','資料讀取失敗'))
                if output is not None:
                    # A failed worker never publishes a partial image.
                    output.write_bytes(png.read_bytes())
                return result
        finally:self.lock.release()

    def scan(self,path,environment):
        result=self.call(environment,dict(mode='scan',path=path))
        key=uuid.uuid4().hex
        result.update(id=key,kind='scan',environment=str(Path(environment).expanduser().resolve()))
        atomic_json(self._file(key),result)
        return result

    def request(self,d):
        report=self.get(d['scan']);index=d['product']
        if type(index) is not int or not 0<=index<len(report['products']):raise ValueError('無效的產品')
        product=report['products'][index]
        if product.get('type')!='GUNW':raise ValueError('目前僅提供 GUNW 地圖預覽')
        if d['layer'] not in {v['key'] for v in product['layers']}:raise ValueError('無效的圖層')
        return report,dict(path=product['path'],stamp=product['stamp'],layer=d['layer'],threshold=d.get('threshold',0))

    def preview(self,d):
        report,request=self.request(d);key=uuid.uuid4().hex;self.home.mkdir(parents=True,exist_ok=True)
        png=self._file(key,'.png')
        result=self.call(report['environment'],dict(request,mode='preview'),png)
        result.update(id=key,kind='preview',scan=d['scan'],product=d['product'])
        atomic_json(self._file(key),result)
        return result

    def point(self,d):
        report,request=self.request(d)
        return self.call(report['environment'],dict(request,mode='point',lat=d['lat'],lon=d['lon']))

    def artifact(self,key):
        meta=json.loads(self._file(key).read_text())
        if meta.get('kind')!='preview':raise ValueError('不是預覽圖')
        return self._file(key,'.png')
