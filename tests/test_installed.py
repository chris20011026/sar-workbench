"""Optional real ISCE command-generation test. Does NOT process SAR imagery."""
import os
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from alosstack_helper.core import write_xml, parse_cmd3, split_pre_review, declared_pairs, find_conda
from alosstack_helper.engine import Engine, RESOLVE_CONFIG_SCRIPT

@unittest.skipUnless(os.environ.get('ALOSSTACK_TEST_ENV'), 'Set ALOSSTACK_TEST_ENV to test an installed ISCE2')
class InstalledGeneratorTest(unittest.TestCase):
    def test_runner_finds_mdx_in_selected_isce_home(self):
        env=Path(os.environ['ALOSSTACK_TEST_ENV'])
        with tempfile.TemporaryDirectory(prefix='alos-runner-') as tmp:
            root=Path(tmp);(root/'proc').mkdir();(root/'.helper').mkdir()
            engine=Engine(root/'registry')
            state={'config':{'root':str(root),'env':str(env),'conda':str(find_conda(env))}}
            engine.run(state,['python','-c','import json,os,shutil; json.dump(dict(mdx=shutil.which("mdx"),isce_home=os.environ["ISCE_HOME"],python=shutil.which("python")),open("tools.json","w"))'],conda=True)
            result=json.loads((root/'proc/tools.json').read_text())
            plotting=Path(__file__).resolve().parents[1]/'.runtime/bin/mdx'
            managed=Path(os.environ.get('SAR_WORKBENCH_HOME',str(Path.home()/'sar-workbench')))/'runtime/envs'
            private=managed.resolve() in env.resolve().parents
            expected=(env/'bin/mdx') if (env/'bin/mdx').is_file() else Path(result['isce_home'])/'bin/mdx'
            if plotting.is_file() and not private: expected=plotting
            self.assertEqual(result['mdx'],str(expected))
            if private: self.assertIn(env.resolve(),Path(result['mdx']).resolve().parents)
            self.assertEqual(result['python'],str(env/'bin/python'))
    def test_shebang_uses_selected_python_when_environment_already_active(self):
        env=Path(os.environ['ALOSSTACK_TEST_ENV'])
        conda=find_conda(env)
        # Obtain real activation state, then emulate a launcher prepending Python.
        activated=subprocess.run([str(conda),'run','-p',str(env),str(env/'bin/python'),
            '-c','import os,json; print(json.dumps(dict(os.environ)))'],
            capture_output=True,text=True,check=True)
        inherited=json.loads(activated.stdout)
        with tempfile.TemporaryDirectory(prefix='alos-shebang-') as tmp:
            root=Path(tmp);(root/'proc').mkdir();(root/'.helper').mkdir()
            foreign=root/'foreign';foreign.mkdir()
            fake=foreign/'python3';fake.write_text('#!/bin/sh\necho WRONG_PYTHON >&2\nexit 91\n');fake.chmod(0o755)
            inherited['PATH']=str(foreign)+os.pathsep+inherited['PATH']
            probe=root/'probe.py'
            probe.write_text('#!/usr/bin/env python3\nimport sys,json,isce,isceobj,numpy\njson.dump(dict(prefix=sys.prefix),open("python.json","w"))\n')
            probe.chmod(0o755)
            engine=Engine(root/'registry')
            state={'config':{'root':str(root),'env':str(env),'conda':str(conda)}}
            with patch.dict(os.environ,inherited,clear=True):
                engine.run(state,[str(probe)],conda=True)
                engine.run(state,[str(env/'share/isce2/alosStack/read_data.py'),'--help'],conda=True)
            self.assertEqual(Path(json.loads((root/'proc/python.json').read_text())['prefix']).resolve(),env.resolve())

    def test_real_generator_adapter(self):
        env=Path(os.environ['ALOSSTACK_TEST_ENV']);conda=find_conda(env)
        with tempfile.TemporaryDirectory(prefix='alos-generator-') as tmp:
            base=Path(tmp).resolve();data=base/'data';data.mkdir()
            for date in ['150206','151113','160219']:
                folder=data/date;folder.mkdir()
                for name in [f'LED-ALOS2000012800-{date}-FBDR1.1__A',f'IMG-HH-ALOS2000012800-{date}-FBDR1.1__A']:
                    (folder/name).write_bytes(b'COMMAND GENERATION FIXTURE ONLY')
            dem=base/'dem.wgs84';dem.write_bytes(b'\0'*16)
            Path(str(dem)+'.xml').write_text(f'<imageFile><property name="file_name"><value>{dem}</value></property><property name="width"><value>2</value></property><property name="length"><value>2</value></property></imageFile>')
            cfg=dict(data=str(data),dem=str(dem),dem_geo=str(dem),wbd=str(dem),reference='151113',properties={'number of range looks 1':'3','number of azimuth looks 1':'2','number of range looks 2':'4','number of azimuth looks 2':'5','number of range looks ion':'16','number of azimuth looks ion':'20'})
            write_xml(cfg,base/'alosStack.xml')
            clean=os.environ.copy();clean.pop('PYTHONPATH',None)
            result=subprocess.run([str(conda),'run','--no-capture-output','-p',str(env),'python',str(env/'share/isce2/alosStack/create_cmds.py'),'-stack_par','alosStack.xml'],cwd=base,env=clean,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            script=(base/'cmd_3.sh').read_text();opts,_=parse_cmd3(script)
            self.assertEqual(opts['-nrlks1'],'3');self.assertEqual(opts['-nalks_ion'],'20')
            self.assertEqual(len(declared_pairs(script,'insarpair')),3)
            self.assertEqual(len(declared_pairs(script,'ionpair')),3)
            pre=base/'pre.sh';pre.write_text(split_pre_review(script))
            self.assertEqual(subprocess.run(['/bin/bash','-n',str(pre)]).returncode,0)
            for n in range(1,5):self.assertTrue((base/f'cmd_{n}.sh').exists())
            for estimate,apply in [(False,False),(False,True),(True,False),(True,True)]:
                with self.subTest(estimate=estimate,apply=apply):
                    cfg['properties'].update({'do ionospheric phase estimation':str(estimate),'apply ionospheric phase correction':str(apply)})
                    write_xml(cfg,base/'alosStack.xml')
                    prefix=[str(conda),'run','--no-capture-output','-p',str(env),'python']
                    result=subprocess.run(prefix+['-c',RESOLVE_CONFIG_SCRIPT],cwd=base,env=clean,capture_output=True,text=True,timeout=60)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    resolved=json.loads((base/'helper_resolved.json').read_text())
                    self.assertEqual(resolved['estimate'],estimate)
                    self.assertEqual(resolved['apply'],apply)
                    self.assertEqual(resolved['subsequent_ion'],4)
                    result=subprocess.run(prefix+[str(env/'share/isce2/alosStack/create_cmds.py'),'-stack_par','alosStack.xml'],cwd=base,env=clean,capture_output=True,text=True,timeout=60)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    script=(base/'cmd_3.sh').read_text()
                    self.assertEqual('ion_ls.py' in script,estimate)
                    self.assertEqual('ion_correct.py' in script,estimate and apply)
                    self.assertEqual(len(declared_pairs((base/'cmd_2.sh').read_text(),'insarpair')),3)
