from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import argparse
import json
import mimetypes
import os
import secrets
import webbrowser
import signal
import threading
import fcntl

from .core import inspect_data, read_template
from .engine import Engine
from .native_picker import choose_native
from .analysis import Analyses
from .pair import PairWorkflow, FIELDS, import_pair_xml
from .tops import TopsWorkflow, FIELDS as TOPS_FIELDS, preflight as tops_preflight
from .sentinel import inspect_pair as inspect_sentinel_pair
from .workflows import WORKFLOWS, inventory
from .mintpy_parameters import catalogue
from .onboarding import Onboarding, inspect_terrain
from .runtime import ready_environment, workbench_home
from .terrain import plan_terrain
from .core import atomic_json
from .preflight import check_project, require_preflight
from .recovery import Recovery
from .confirmation import Confirmations, saved_review, verify_saved_inputs

STATIC = Path(__file__).parent/'static'

def defaults():
    candidates = [Path(os.environ.get('CONDA_PREFIX', '/nonexistent'))]
    managed = ready_environment()
    if managed: candidates.insert(0, managed)
    for base in [Path.home()/'miniconda3', Path.home()/'miniforge3', Path('/opt/miniconda3')]:
        if (base/'envs').is_dir(): candidates.extend((base/'envs').iterdir())
    candidates = sorted(set(candidates), key=lambda p: (p != managed, p.name != 'insar_arm', str(p)))
    env = next((p for p in candidates if (p/'share/isce2/alosStack/create_cmds.py').is_file()), None)
    return {'home':str(Path.home()), 'env':str(env) if env else '',
            'template':str(env/'share/isce2/alosStack/alosStack.xml') if env else '',
            'environments':[{'path':str(p),'name':p.name,'isce':(p/'share/isce2/alosStack/create_cmds.py').is_file(),'mintpy':bool(list((p/'conda-meta').glob('mintpy-*.json'))) or any((p/'lib').glob('python*/site-packages/mintpy'))} for p in candidates if (p/'bin/python').is_file()]}

def make_server(engine, port=8765):
    token = secrets.token_urlsafe(32)
    analyses = Analyses(engine)
    onboarding = Onboarding()
    confirmations = Confirmations()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, value, status=200, mime='application/json'):
            data = json.dumps(value, ensure_ascii=False).encode() if mime == 'application/json' else value
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://tile.openstreetmap.org; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(data)
        def auth(self):
            host = self.headers.get('Host','')
            if host != f'127.0.0.1:{self.server.server_port}': return False
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+host: return False
            return secrets.compare_digest(self.headers.get('X-Token',''), token)
        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query)
            try:
                if u.path in ('/', '/app.js', '/recovery.js', '/workbench.js', '/maps.js', '/pair.js', '/tops.js', '/mintpy-settings.js', '/onboarding.js', '/import-metadata.js', '/confirmation.js', '/onboarding.css', '/style.css','/leaflet.js','/leaflet.css','/plotly-basic.min.js'):
                    if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                        return self.send({'error':'Invalid host'},403)
                    name = 'index.html' if u.path=='/' else u.path[1:]
                    data = (STATIC/name).read_bytes()
                    if name == 'index.html':
                        data = data.replace(b'__ONBOARDING__', (STATIC/'onboarding-panel.html').read_bytes())
                        data = data.replace(b'__TOKEN__', token.encode())
                    return self.send(data, mime=mimetypes.guess_type(name)[0] or 'text/plain')
                if not self.auth(): return self.send({'error':'請從本機頁面操作。'},403)
                if u.path == '/api/onboarding': return self.send(dict(onboarding.runtime.status(), jobs=onboarding.recent()))
                if u.path == '/api/onboarding-job': return self.send(onboarding.job(q.get('job', [''])[0]))
                if u.path == '/api/state': return self.send({'projects':engine.list(), 'defaults':defaults(), 'workflows':WORKFLOWS, 'busy':bool(engine.active)})
                if u.path == '/api/tops-schema':return self.send({'fields':TOPS_FIELDS})
                if u.path == '/api/pair-schema':return self.send({'fields':FIELDS})
                if u.path == '/api/mintpy-schema':return self.send(catalogue(q.get('environment',[None])[0] or None))
                if u.path == '/api/browse':
                    p = Path(q.get('path',[str(Path.home())])[0]).expanduser().resolve()
                    entries = []
                    for x in sorted(p.iterdir(),key=lambda x:(not x.is_dir(), x.name.lower())):
                        if not x.name.startswith('.'):
                            entries.append({'name':x.name,'path':str(x),'dir':x.is_dir()})
                    return self.send({'path':str(p),'parent':str(p.parent),'entries':entries})
                key = q.get('id',[''])[0]; s = engine.get(key)
                root, proc, meta = engine.paths(s)
                if u.path == '/api/recovery': return self.send(Recovery(engine,s).report())
                if u.path == '/api/pair-artifact':
                    p=PairWorkflow(engine).artifact(s,q['file'][0])
                    return self.send(p.read_bytes(),mime=mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
                if u.path == '/api/analyses': return self.send({'runs':analyses.listing(key)})
                if u.path == '/api/analysis': return self.send(analyses.details(key,q['run'][0]))
                if u.path in ('/api/analysis-artifact','/api/tops-artifact'):
                    p=TopsWorkflow(engine).artifact(s,q['file'][0]) if u.path=='/api/tops-artifact' else analyses.artifact(key,q['run'][0],q['file'][0])
                    self.send_response(200)
                    self.send_header('Content-Type',mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
                    self.send_header('Content-Length',str(p.stat().st_size))
                    self.send_header('X-Content-Type-Options','nosniff')
                    self.send_header('Cache-Control','no-store')
                    self.end_headers()
                    with p.open('rb') as f:
                        for chunk in iter(lambda:f.read(1024*1024),b''):self.wfile.write(chunk)
                    return

                if u.path == '/api/survey-artifact':
                    p=analyses.survey_artifact(key,q['file'][0])
                    return self.send(p.read_bytes(),mime='application/octet-stream')

                if u.path in ('/api/project-output', '/api/log'):
                    p = meta/'run.log'
                    if p.exists():
                        with p.open('rb') as f:
                            f.seek(max(0,p.stat().st_size-60000)); data=f.read().decode(errors='replace')
                    else: data='尚未開始執行。'
                    return self.send({'text':data})
                if u.path == '/api/review': return self.send(engine.review(s, []))
                if u.path == '/api/image':
                    base = (proc/'fig_ion').resolve(); p = (base/q['file'][0]).resolve()
                    if base not in p.parents or p.suffix.lower() not in ('.png','.jpg','.jpeg'):
                        raise ValueError('無效的圖片路徑。')
                    return self.send(p.read_bytes(),mime=mimetypes.guess_type(p.name)[0])
                return self.send({'error':'Not found'},404)
            except Exception as e: self.send({'error':str(e)},400)
        def do_POST(self):
            if not self.auth(): return self.send({'error':'請從本機頁面操作。'},403)
            try:
                size=int(self.headers.get('Content-Length',0))
                if size > 1024*1024: raise ValueError('請求過大。')
                d=json.loads(self.rfile.read(size))
                if self.path == '/api/setup-start':
                    if engine.active: raise ValueError('請等待目前的研究運算結束，再進行安裝或驗證。')
                    if any(j['status']=='running' for j in onboarding.jobs.values()):
                        raise ValueError('請先完成目前的資料準備或教學工作。')
                    return self.send(onboarding.runtime.start(consent=d.get('consent') is True, recheck=d.get('recheck') is True))
                if self.path == '/api/setup-stop': onboarding.runtime.stop(); return self.send({'ok':True})
                if self.path == '/api/import-scan': return self.send(onboarding.scan(d['path'], d.get('environment') or defaults()['env']))
                if self.path == '/api/import-select': return self.send(onboarding.select(d['plan'], d['keys']))
                if self.path == '/api/import-start': return self.send(onboarding.start_job('import', d.get('plan')))
                if self.path == '/api/onboarding-stop': onboarding.stop(); return self.send({'ok':True})
                if self.path == '/api/demo-start':
                    if engine.active: raise ValueError('請先完成目前的研究運算，再開始教學範例。')
                    return self.send(onboarding.start_job('demo'))
                if self.path == '/api/terrain-check': return self.send(inspect_terrain(d['path'], d['kind']))
                if self.path == '/api/terrain-plan': return self.send(plan_terrain(d.get('bounds')))
                if self.path == '/api/terrain-start':
                    if engine.active: raise ValueError('請先完成目前的研究運算。')
                    return self.send(onboarding.start_job('terrain', options=d))
                if self.path == '/api/pick': return self.send(choose_native(d.get('mode'), d.get('start')))
                if self.path == '/api/pair-template':return self.send(import_pair_xml(d['path']))
                if self.path == '/api/template': return self.send({'properties':read_template(d['path'])})
                if self.path == '/api/scan': return self.send({'dates':inspect_data(d['path'])})
                if self.path == '/api/tops-scan':return self.send(inspect_sentinel_pair(d['reference_safe'],d['secondary_safe']))
                if self.path == '/api/tops-preflight':return self.send(tops_preflight(d))
                if self.path == '/api/preflight': return self.send(check_project(d))
                if self.path == '/api/confirmation': return self.send(confirmations.preview(d))
                if self.path == '/api/confirmation-saved': return self.send(saved_review(engine,d['id']))
                if self.path == '/api/project-check':
                    s = engine.get(d['id'])
                    return self.send(tops_preflight(s['config'],existing=True) if s['config'].get('workflow')=='topsApp' else check_project(s['config'], existing=True))
                if self.path == '/api/recovery-enable':
                    if type(d.get('enabled')) is not bool: raise ValueError('請選擇是否啟用保護。')
                    engine.recovery_enable(d['id'],d['enabled']); return self.send({'ok':True})
                if self.path == '/api/create':
                    if d.get('workflow')=='topsApp':return self.send(engine.create(d))
                    confirmation = confirmations.validate(d) if d.get('confirmation_id') else None
                    report = require_preflight(d)
                    s = engine.create(d)
                    atomic_json(engine.paths(s)[2]/'preflight.json', report)
                    if confirmation: atomic_json(engine.paths(s)[2]/'confirmation.json',confirmation)
                    return self.send(s)
                if self.path == '/api/analysis-create': return self.send(analyses.create(d['id'],d['kind'],d))
                if self.path == '/api/analysis-invert': return self.send(analyses.invert(d['id'],d['run'],d))
                if self.path == '/api/analysis-point': return self.send(analyses.point(d['id'],d['run'],d['y'],d['x']))
                if self.path == '/api/survey':return self.send(analyses.survey(d['id'],d))
                if self.path == '/api/geo-view':return self.send(analyses.geo_view(d['id'],d['run']))
                if self.path == '/api/geo-point':return self.send(analyses.geo_point(d['id'],d['run'],d))
                if self.path == '/api/geo-export':return self.send(analyses.geo_export(d['id'],d['run'],d))
                if self.path == '/api/geo-figure':return self.send(analyses.geo_figure(d['id'],d['run'],d))
                if self.path == '/api/analysis-rename':return self.send(analyses.rename(d['id'],d['run'],d['name']))
                if self.path == '/api/action':
                    s = engine.get(d['id'])
                    if s['config'].get('workflow')=='topsApp':
                        TopsWorkflow(engine).verify(s)
                        engine.launch(d['id'],d['action'],options=d);return self.send({'ok':True})
                    if d['action'] in ('prepare', 'run', 'continue', 'resume', 'recover'):
                        report = require_preflight(s['config'], existing=True)
                        atomic_json(engine.paths(s)[2]/'preflight.json', report)
                    if d['action'] in ('prepare','run'):
                        verify_saved_inputs(s,engine.paths(s)[2])
                    engine.launch(d['id'],d['action'],d.get('excluded'),d); return self.send({'ok':True})
                if self.path == '/api/stop': engine.stop(); return self.send({'ok':True})
                return self.send({'error':'Not found'},404)
            except Exception as e: self.send({'error':str(e)},400)
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.onboarding = onboarding
    return server

def main():
    parser=argparse.ArgumentParser(description='alosStack visual workbench (local only)')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--state-dir',default=str(Path.home()/'.local/share/alosstack-helper'))
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--desktop-session', type=Path, help=argparse.SUPPRESS)
    args=parser.parse_args()
    # A second server must never reconcile or mutate the same project registry.
    state_dir = Path(args.state_dir).expanduser()
    state_dir.mkdir(parents=True, exist_ok=True)
    guard = (state_dir / 'server.lock').open('a')
    try: fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: parser.exit(1, '這個工作台已經在執行；請開啟原本的瀏覽器頁面。\n')
    usage = None
    runtime = workbench_home()/'runtime'
    if runtime.is_dir():
        usage = (runtime/'usage.lock').open('a')
        try: fcntl.flock(usage, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError: parser.exit(1, '運算工具正在解除安裝，請稍後重新開啟。\n')
    engine = Engine(args.state_dir)
    server=make_server(engine,args.port)
    url=f'http://127.0.0.1:{server.server_port}'
    print('alosStack Helper: '+url,flush=True)
    if args.desktop_session:
        atomic_json(args.desktop_session, {'url':url, 'pid':os.getpid()})
    if not args.no_browser: webbrowser.open(url)
    def shutdown(*_):
        server.onboarding.runtime.stop()
        server.onboarding.stop()
        if engine.active: engine.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.onboarding.runtime.stop()
        server.onboarding.stop()
        if engine.active: engine.stop()
        server.server_close()
        if usage: usage.close()
        guard.close()

if __name__ == '__main__': main()
