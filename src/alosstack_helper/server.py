from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import argparse
import json
import mimetypes
import os
import secrets
import webbrowser

from .core import inspect_data, read_template
from .engine import Engine
from .native_picker import choose_native
from .analysis import Analyses
from .pair import PairWorkflow, FIELDS, import_pair_xml
from .workflows import WORKFLOWS, inventory

STATIC = Path(__file__).parent/'static'

def defaults():
    candidates = [Path(os.environ.get('CONDA_PREFIX', '/nonexistent'))]
    for base in [Path.home()/'miniconda3', Path.home()/'miniforge3', Path('/opt/miniconda3')]:
        if (base/'envs').is_dir(): candidates.extend((base/'envs').iterdir())
    candidates = sorted(set(candidates), key=lambda p: (p.name != 'insar_arm', str(p)))
    env = next((p for p in candidates if (p/'share/isce2/alosStack/create_cmds.py').is_file()), None)
    return {'home':str(Path.home()), 'env':str(env) if env else '',
            'template':str(env/'share/isce2/alosStack/alosStack.xml') if env else '',
            'environments':[{'path':str(p),'name':p.name,'mintpy':bool(list((p/'conda-meta').glob('mintpy-*.json'))) or any((p/'lib').glob('python*/site-packages/mintpy'))} for p in candidates if (p/'bin/python').is_file()]}

def make_server(engine, port=8765):
    token = secrets.token_urlsafe(32)
    analyses = Analyses(engine)
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
                if u.path in ('/', '/app.js', '/workbench.js', '/maps.js', '/pair.js', '/style.css','/leaflet.js','/leaflet.css','/plotly-basic.min.js'):
                    if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                        return self.send({'error':'Invalid host'},403)
                    name = 'index.html' if u.path=='/' else u.path[1:]
                    data = (STATIC/name).read_bytes()
                    if name == 'index.html': data = data.replace(b'__TOKEN__', token.encode())
                    return self.send(data, mime=mimetypes.guess_type(name)[0] or 'text/plain')
                if not self.auth(): return self.send({'error':'請從本機頁面操作。'},403)
                if u.path == '/api/state': return self.send({'projects':engine.list(), 'defaults':defaults(), 'workflows':WORKFLOWS, 'busy':bool(engine.active)})
                if u.path == '/api/pair-schema':return self.send({'fields':FIELDS})
                if u.path == '/api/browse':
                    p = Path(q.get('path',[str(Path.home())])[0]).expanduser().resolve()
                    entries = []
                    for x in sorted(p.iterdir(),key=lambda x:(not x.is_dir(), x.name.lower())):
                        if not x.name.startswith('.'):
                            entries.append({'name':x.name,'path':str(x),'dir':x.is_dir()})
                    return self.send({'path':str(p),'parent':str(p.parent),'entries':entries})
                key = q.get('id',[''])[0]; s = engine.get(key)
                root, proc, meta = engine.paths(s)
                if u.path == '/api/pair-artifact':
                    p=PairWorkflow(engine).artifact(s,q['file'][0])
                    return self.send(p.read_bytes(),mime=mimetypes.guess_type(p.name)[0] or 'application/octet-stream')
                if u.path == '/api/analyses': return self.send({'runs':analyses.listing(key)})
                if u.path == '/api/analysis': return self.send(analyses.details(key,q['run'][0]))
                if u.path == '/api/analysis-artifact':
                    p=analyses.artifact(key,q['run'][0],q['file'][0])
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

                if u.path == '/api/log':
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
                if self.path == '/api/pick': return self.send(choose_native(d.get('mode'), d.get('start')))
                if self.path == '/api/pair-template':return self.send(import_pair_xml(d['path']))
                if self.path == '/api/template': return self.send({'properties':read_template(d['path'])})
                if self.path == '/api/scan': return self.send({'dates':inspect_data(d['path'])})
                if self.path == '/api/create': return self.send(engine.create(d))
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
                    engine.launch(d['id'],d['action'],d.get('excluded'),d); return self.send({'ok':True})
                if self.path == '/api/stop': engine.stop(); return self.send({'ok':True})
                return self.send({'error':'Not found'},404)
            except Exception as e: self.send({'error':str(e)},400)
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def main():
    parser=argparse.ArgumentParser(description='alosStack visual workbench (local only)')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--state-dir',default=str(Path.home()/'.local/share/alosstack-helper'))
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    server=make_server(Engine(args.state_dir),args.port)
    url=f'http://127.0.0.1:{server.server_port}'
    print('alosStack Helper: '+url,flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: server.server_close()

if __name__ == '__main__': main()
