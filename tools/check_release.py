#!/usr/bin/env python3
"""Exercise the shipped server with an empty project registry, then its tests.

Run inside the scientific environment. No existing project or user registry is
read. The native bootstrap itself is tested separately by the CI workflow.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def smoke(home, source, python):
    from alosstack_helper.runtime import private_env, ready_environment, validate_report
    prefix = ready_environment(home)
    if not prefix:
        raise RuntimeError('The new private environment is not ready.')
    ready = json.loads((home/'runtime/ready.json').read_text())
    checks = validate_report(json.loads((Path(ready['report'])/'report.json').read_text()))
    with tempfile.TemporaryDirectory() as folder:
        temporary = Path(folder)
        env = private_env(home)
        env.update(PYTHONPATH=str(source), SAR_WORKBENCH_HOME=str(home))
        args = [str(python), '-s', '-m', 'alosstack_helper.server', '--port', '0', '--no-browser',
                '--state-dir', str(temporary/'state'), '--desktop-session', str(temporary/'session.json')]
        with (temporary/'server.log').open('w') as log:
            server = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(150):
                    if (temporary/'session.json').exists(): break
                    if server.poll() is not None: raise RuntimeError((temporary/'server.log').read_text())
                    time.sleep(.1)
                session = json.loads((temporary/'session.json').read_text())
                assert session['pid'] == server.pid
                base = session['url']
                html = urllib.request.urlopen(base).read().decode()
                assert 'id="welcome"' in html and '__ONBOARDING__' not in html
                token = re.search(r'name="api-token" content="([^"]+)"',html).group(1)
                def get(path, headers=None):
                    return urllib.request.urlopen(urllib.request.Request(base+path,headers=headers or {}))
                for asset in re.findall(r'<script src="([^"]+)"',html):
                    with get(asset) as response:
                        assert response.status == 200 and 'javascript' in response.headers['Content-Type']
                        assert response.read()
                try: get('/api/state')
                except urllib.error.HTTPError as e: assert e.code == 403
                else: raise AssertionError('API accepted a request without its page token')
                with get('/api/state', {'X-Token':token}) as response:
                    state = json.load(response)
                assert state['projects'] == [] and state['defaults']['env'] == str(prefix)
                with get('/api/onboarding', {'X-Token':token}) as response:
                    assert json.load(response)['status'] == 'ready'
                # A second process must not attach to the same project registry.
                duplicate = subprocess.run(args,env=env,capture_output=True,timeout=20)
                assert duplicate.returncode != 0
            finally:
                server.terminate()
                try: server.wait(timeout=15)
                except subprocess.TimeoutExpired: server.kill(); server.wait()
    return dict(runtime_checks=len(checks), api_auth='PASS', empty_registry='PASS',
                bundled_assets='PASS', preferred_private_environment='PASS', duplicate_server_guard='PASS')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', type=Path, required=True)
    p.add_argument('--bundled-source', type=Path, required=True)
    a = p.parse_args()
    os.environ['SAR_WORKBENCH_HOME'] = str(a.home)
    report = smoke(a.home, a.bundled_source, a.home/'runtime/miniforge/bin/python')
    from alosstack_helper.runtime import ready_environment
    os.environ['ALOSSTACK_TEST_ENV'] = str(ready_environment(a.home))
    tests = unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    report.update(tests=result.testsRun, skipped=len(result.skipped), passed=result.wasSuccessful())
    (a.home/'runtime/workbench-validation.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__': sys.exit(main())
