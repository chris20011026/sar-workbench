import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.preflight import check_project, require_preflight
from alosstack_helper.environment import isolated_env
from alosstack_helper.core import find_conda
from alosstack_helper.analysis import Analyses


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name).resolve()
        env = self.base/'conda/envs/science'
        for name in ('bin/python', 'share/isce2/alosStack/create_cmds.py', 'conda-meta/isce.json'):
            p=env/name; p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{"subdir":"osx-arm64"}')
        (self.base/'conda/bin').mkdir(); (self.base/'conda/bin/conda').touch()
        source=self.base/'data'
        for date in ('200101','200201'):
            folder=source/date;folder.mkdir(parents=True)
            for frame in ('0480','0490'):
                for head in ('LED','IMG-HH'):
                    (folder/f'{head}-ALOS200001{frame}-{date}-FBDR1.1__A').write_bytes(b'\0'*8+(24).to_bytes(4,'big')+b'\0'*12)
        self.config=dict(root=str(self.base/'project'), source=str(source),env=str(env),reference='200101',properties={'polarization':'HH'},workflow='alosStack')
        for key in ('dem','dem_geo','wbd'):
            p=self.base/key; p.write_bytes(b'\0'*(9 if key=='wbd' else 18))
            Path(str(p)+'.xml').write_text('<imageFile><property name="width"><value>3</value></property><property name="length"><value>3</value></property><property name="data_type"><value>'+('BYTE' if key=='wbd' else 'SHORT')+'</value></property><property name="reference"><value>WGS84</value></property><component name="coordinate1"><property name="startingValue"><value>120</value></property><property name="delta"><value>1</value></property></component><component name="coordinate2"><property name="startingValue"><value>26</value></property><property name="delta"><value>-1</value></property></component></imageFile>')
            self.config[key]=str(p)

    def tearDown(self): self.tmp.cleanup()
    def errors(self,c=None):return [x['detail'] for x in check_project(c or self.config)['checks'] if x['status']=='error']
    def test_passes_without_claiming_unknown_coverage_and_requires_review(self):
        before=sorted(str(p) for p in self.base.rglob('*'))
        r=check_project(self.config);self.assertTrue(r['can_proceed']);self.assertTrue(r['needs_review'])
        self.assertEqual(next(x['status'] for x in r['checks'] if x['key']=='coverage'),'warning')
        with self.assertRaisesRegex(ValueError,'人工'):require_preflight(self.config)
        self.assertEqual(before,sorted(str(p) for p in self.base.rglob('*')))

    def test_checks_each_frame_polarization(self):
        next((self.base/'data/200201').glob('IMG-HH-*0490-*')).unlink()
        self.assertIn('每個 frame',''.join(self.errors()))

    def test_wrong_folder_date_and_truncated_header(self):
        p=next((self.base/'data/200201').glob('IMG-HH-*'))
        p.write_bytes(b'\0'*8+(999).to_bytes(4,'big'))
        self.assertIn('CEOS',''.join(self.errors()))
        p.rename(p.with_name(p.name.replace('200201','200301')))
        self.assertIn('日期與資料夾',''.join(self.errors()))

    def test_coverage_and_dem_size_are_blockers(self):
        self.config['image_bounds']=[23,25,120,121]
        self.assertIn('未涵蓋',''.join(self.errors()))
        self.config['image_bounds']=[24.5,25.5,120.5,121.5]
        self.assertEqual(self.errors(),[])
        Path(self.config['dem']).write_bytes(b'partial')
        self.assertIn('大小與 XML',''.join(self.errors()))

    def test_mixed_architecture_and_low_space_block(self):
        (Path(self.config['env'])/'conda-meta/other.json').write_text('{"subdir":"osx-64"}')
        r=check_project(self.config)
        self.assertEqual(next(x['status'] for x in r['checks'] if x['key']=='architecture'),'error')
        with patch('alosstack_helper.preflight.shutil.disk_usage') as disk:
            disk.return_value.free=1
            self.assertIn('可用',''.join(self.errors()))

    def test_unmount_and_runtime_project_blocked(self):
        c=copy.deepcopy(self.config);c['source']=str(self.base/'missing')
        self.assertIn('NAS',''.join(self.errors(c)))
        with patch.dict(os.environ,{'SAR_WORKBENCH_HOME':str(self.base/'workbench')}):
            c=copy.deepcopy(self.config);c['root']=str(self.base/'workbench/runtime/research')
            self.assertIn('runtime 外',''.join(self.errors(c)))

    def test_two_date_subset_ignores_other_dates_and_frames(self):
        self.config.update(workflow='alos2App',secondary='200201',frames=['0480'])
        next((self.base/'data/200201').glob('IMG-HH-*0490-*')).unlink()
        self.assertEqual(self.errors(),[])

    def test_stack_sampling_selects_common_frames_without_inventing_correspondence(self):
        for p in (self.base/'data/200201').glob('*0490-*'): p.unlink()
        self.assertIn('frame 組合不同',''.join(self.errors()))
        self.config['properties']['frames']='["0480"]'
        self.config['sampling_equalization']=True
        self.assertEqual(self.errors(),[])
        r=check_project(self.config)
        self.assertTrue(any(x['key']=='sampling' and x['status']=='warning' for x in r['checks']))
        self.config['properties']['frames']='["0490"]'
        self.assertTrue(self.errors())

    def test_sampling_does_not_silently_accept_scansar(self):
        for p in (self.base/'data').rglob('*FBDR1.1__A'):p.rename(p.with_name(p.name.replace('FBD','WBS')))
        self.config['sampling_equalization']=True
        self.assertIn('Stripmap',''.join(self.errors()))

    def test_api_rechecks_at_create_and_launch_without_starting_science(self):
        import re
        import threading
        import urllib.request
        import urllib.error
        from alosstack_helper.engine import Engine
        from alosstack_helper.server import make_server
        engine=Engine(self.base/'registry');server=make_server(engine,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base=f'http://127.0.0.1:{server.server_port}'
            with urllib.request.urlopen(base) as response: html=response.read().decode()
            token=re.search('name="api-token" content="([^"]+)"',html).group(1)
            def post(path,value):
                req=urllib.request.Request(base+path,data=json.dumps(value).encode(),headers={'X-Token':token,'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as response:return json.load(response)
            self.assertTrue(post('/api/preflight',self.config)['can_proceed'])
            with self.assertRaises(urllib.error.HTTPError):post('/api/create',self.config)
            self.assertFalse(Path(self.config['root']).exists())
            s=post('/api/create',{**self.config,'preflight_reviewed':True})
            next((self.base/'data/200201').glob('IMG-HH-*0490-*')).unlink()
            with self.assertRaises(urllib.error.HTTPError):post('/api/action',{'id':s['id'],'action':'prepare'})
            self.assertIsNone(engine.active);self.assertEqual(engine.get(s['id'])['status'],'new')
        finally:
            server.shutdown();server.server_close();thread.join()


class EnvironmentIsolation(unittest.TestCase):
    def test_poisoned_shell_is_not_inherited_and_parent_unchanged(self):
        poison={k:'/old/intel/incorrect' for k in ('CONDA_PREFIX','CONDA_SUBDIR','PYTHONPATH','PYTHONHOME','GDAL_DATA','PROJ_LIB','DYLD_LIBRARY_PATH','LD_LIBRARY_PATH','ISCE_HOME','ISCE_STACK','BASH_ENV','ENV','VIRTUAL_ENV','MPLBACKEND','_CE_CONDA')}
        poison['PATH']='/old/intel/bin';poison['CONDARC']='/old/condarc'
        with patch.dict(os.environ,poison):
            result=isolated_env(Path('/tmp/science-cache'))
            self.assertFalse(any('/old/' in v for v in result.values()))
            self.assertEqual(result['CONDARC'],'/dev/null')
            for k,v in poison.items():self.assertEqual(os.environ[k],v)

    def test_analysis_resolves_its_selected_environment_not_old_job_conda(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); managed=root/'runtime';conda=managed/'miniforge/bin/conda'
            conda.parent.mkdir(parents=True);conda.touch()
            env=managed/'envs/science';env.mkdir(parents=True)
            with patch.dict(os.environ,{'SAR_WORKBENCH_HOME':str(root)}):
                cmd=Analyses(None).command({'environment':str(env),'conda':'/old/intel/conda'},['load'])
                self.assertEqual(Path(cmd[0]),conda)
                conda.unlink();self.assertIsNone(find_conda(env))
                with self.assertRaises(ValueError):Analyses(None).command({'environment':str(env)},[])
