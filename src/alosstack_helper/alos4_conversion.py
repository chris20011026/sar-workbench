"""Adapter for a locally supplied, hash-verified JAXA P3toP2snap distribution.

Third-party scripts are not redistributed. IMG conversion streams one record at
a time; all source files remain read-only. A conversion is not an InSAR proof.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import struct
import time
import uuid

HASHES = {
 'main.py':'ef3929289181d558953d6c55616d1f4596bf4727691b41272020704addc55efb',
 'modules/ch_P3toP2_IMG.py':'1e7f11876149c6ff46ebea45034852889cfa31f4fb7fe3462a9a65b97b2acb1f',
 'modules/ch_P3toP2_LED.py':'9eec85107c2ab9c7e5a4e38a7145f5e8b014433be49369a05edc2da8dad81f95',
 'modules/ch_P3toP2_TRL.py':'54379850e8e08fc5c0ac57c78c32cc4c3f8fcad79df60ccfc009991cd3a1f2fd',
 'modules/ch_P3toP2_VOL.py':'973e0ce2e3f6f06f3095ab8bdd19730571b11d8e866698abfb8ce721f72f0f33',
}
MODES = dict(UWS='UBS', UWD='UBD', UBD='UBD', UBQ='UBQ', HBQ='HBQ', FWD='FBD')
JAXA_URL = 'https://www.eorc.jaxa.jp/ALOS/en/alos-4/a4_data_e.htm'


def verify_tool(folder):
    for name, expected in HASHES.items():
        p = Path(folder)/name
        if not p.is_file() or p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise ValueError('轉換工具版本不符：' + name + '。請使用 JAXA 2025-09-25 的 P3toP2snap；不執行未核對的腳本。')


def product_files(folder):
    root = Path(folder)
    leaders = sorted(root.glob('LED-ALOS4*'))
    if len(leaders) != 1: raise ValueError('每次請選一個產品資料夾，需有且只有一份 ALOS-4 LED。')
    name = leaders[0].name
    if len(name) != 39 or not name.endswith('-1.1__-') or name[22:25] not in MODES or name[26] not in 'RL' or name[27] not in 'AD':
        raise ValueError('只支援已核對命名格式的 PALSAR-3 高解析度 Level 1.1（UWS/UWD/UBD/UBQ/HBQ/FWD）。')
    suffix = name[4:]
    files = [root/('VOL-'+suffix), leaders[0], root/('TRL-'+suffix), *sorted(root.glob('IMG-??-'+suffix))]
    expected = 1 if name[22:25]=='UWS' else 4 if name[22:25] in ('UBQ','HBQ') else 2
    if len(files) != 3 + expected or any(not f.is_file() or f.is_symlink() or not f.stat().st_size for f in files):
        raise ValueError('產品缺少 VOL／LED／TRL／IMG，或極化檔案數與模式不符；請使用完整單一產品。')
    return files


def inspect_img(path):
    p = Path(path)
    with p.open('rb') as f:
        desc, prefix = f.read(720), f.read(800)
    if len(prefix) != 800: raise ValueError('IMG 記錄不完整。')
    record = struct.unpack_from('>I', prefix, 8)[0]
    pixels = struct.unpack_from('>I', prefix, 24)[0]
    if not 0 < pixels or record > 999999 or record != 800 + pixels*8:
        raise ValueError('IMG 不是已核對的 800-byte prefix / complex-float 格式。')
    if (p.stat().st_size-720) % record: raise ValueError('IMG 檔案被截斷或記錄長度不一致。')
    lines = (p.stat().st_size-720)//record
    if lines <= 0: raise ValueError('IMG 沒有影像記錄。')
    if int(desc[180:186]) != lines: raise ValueError('IMG 行數與 descriptor 不符，可能缺少完整記錄。')
    if int(desc[186:192]) != record: raise ValueError('IMG descriptor 與資料記錄長度不一致。')
    return dict(path=str(p), record_bytes=record, pixels=pixels, lines=lines, output_bytes=720+lines*(record-256))


def convert_img(source, target):
    """Preserve every complex sample byte; adapt the documented P3 CEOS prefix."""
    info = inspect_img(source)
    with Path(source).open('rb') as src, Path(target).open('xb') as dst:
        desc = bytearray(src.read(720))
        desc[48:64] = b'AL2 SARBIMOP    '
        desc[186:192] = str(info['record_bytes']-256).rjust(6).encode('ascii')
        desc[276:280] = b' 544'
        dst.write(desc)
        for row in range(info['lines']):
            prefix = bytearray(src.read(800)); samples = src.read(info['pixels']*8)
            if len(prefix) != 800 or len(samples) != info['pixels']*8 or struct.unpack_from('>I', prefix,8)[0] != info['record_bytes'] or struct.unpack_from('>I',prefix,24)[0] != info['pixels']:
                raise ValueError(f'第 {row+1} 行 IMG 記錄長度不同，已停止，保留半成品。')
            struct.pack_into('>I', prefix, 8, info['record_bytes']-256)
            struct.pack_into('>I', prefix, 32, 0)
            struct.pack_into('>I', prefix, 116, struct.unpack_from('>I', prefix,116)[0]//100)
            dst.write(prefix[:544]); dst.write(samples)
            if row % 2000 == 0: print(f'IMG {Path(source).name}: {row}/{info["lines"]}', flush=True)
    if Path(target).stat().st_size != info['output_bytes']: raise ValueError('轉換 IMG 大小不符。')
    return info


def original_output_name(name):
    if name.startswith('IMG-'):
        return name[:11]+'240'+name[12:19]+'-'+name[19:25]+'-'+MODES[name[25:28]]+'R1.1__A'
    return name[:8]+'240'+name[9:16]+'-'+name[16:22]+'-'+MODES[name[22:25]]+'R1.1__A'


def run_conversion(c):
    import importlib.util
    verify_tool(c['tool'])
    files = product_files(c['source'])
    root = Path(c['root']); root.mkdir()
    work = root/'work'; work.mkdir()
    # Only the small CEOS metadata are copied. IMG is read-only and streamed.
    for p in files[:3]: shutil.copyfile(p, work/p.name)
    code = root/'tool'; code.mkdir()
    for name in ('LED', 'VOL', 'TRL'):
        path = Path(c['tool'])/f'modules/ch_P3toP2_{name}.py'
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != HASHES[f'modules/ch_P3toP2_{name}.py']:
            raise ValueError('轉換工具在檢查後被修改，已停止。')
        source = data.decode().replace('\r\n', '\n')
        # Replace the sample's quadratic-memory table of blank strings. The
        # lookup is byte-identical (bb[n] is n+1 spaces) and bounded in memory.
        source = re.sub(r"    bb = \[\].*?        bb.append\(blnk\)",
                        "    class Blanks:\n        def __getitem__(self, n): return b' ' * (n + 1)\n    bb = Blanks()", source, count=1, flags=re.S)
        p = code/(name+'.py'); p.write_text(source)
        spec = importlib.util.spec_from_file_location('jaxa_'+name, p); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        old = Path.cwd()
        try:
            os.chdir(work); getattr(module, 'ch_p3top2_'+name.lower())()
        finally: os.chdir(old)
    output = root/'ALOS2_format'; (work/'ALOS2_format').rename(output)
    images = []
    for p in files[3:]: images.append(convert_img(p, output/original_output_name(p.name)))
    # JAXA sample writes R/A suffixes unconditionally. Match the original
    # product's look/pass labels instead, but retain synthetic frame IDs.
    led = files[1].name; direction, node = led[26], led[27]
    if direction not in 'RL' or node not in 'AD': raise ValueError('來源檔名的觀測方向不合法。')
    for p in list(output.iterdir()): p.rename(p.with_name(p.name[:-7]+direction+'1.1__'+node))
    manifest = dict(source_sensor='ALOS-4', output_format='PALSAR-2 CEOS compatibility', source=str(Path(c['source']).resolve()),
                    tool_source=JAXA_URL, tool_hashes=HASHES, changes=['streamed IMG records','bounded blank lookup','preserved source look/pass filename suffix'],
                    images=images, scientific_validation='not performed', warning='合成 frame 編號不是跨衛星對應依據；不可直接視為 ALOS-2 原生觀測。')
    (output/'conversion.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    return dict(output=str(output), manifest=manifest)


def manager_type():
    # Imported only by the web process. The science worker uses the pure
    # conversion functions above without importing the job manager.
    from .nisar_jobs import NisarJobs, stamps
    from .core import find_conda
    class ConversionJobs(NisarJobs):
        worker = Path(__file__).with_name('alos4_worker.py')
        settings_name = 'conversion-plan.json'
        label = 'ALOS-4 格式轉換'
        run_stage = '建立 ALOS-4 格式相容副本'
        def __init__(self, home): super().__init__(home, 'alos4-conversion')
        def create(self, data):
            with self.lock:
                if self.active: raise ValueError('已有格式轉換工作。')
                c = {k:str(Path(str(data.get(k,'')).strip()).expanduser().resolve()) for k in ('source','tool','root','environment')}
                if any(not str(data.get(k,'')).strip() for k in c): raise ValueError('請選擇來源、工具、環境與新輸出資料夾。')
                root = Path(c['root']); env = Path(c['environment'])
                if root.exists() or not root.parent.is_dir(): raise ValueError('請選擇尚不存在的輸出資料夾，且上層必須存在。')
                for value in ('source','tool','environment'):
                    p=Path(c[value])
                    if root==p or root in p.parents or p in root.parents: raise ValueError('輸出與來源、工具、環境目錄不可互相包含。')
                if not (env/'bin/python').is_file() or not find_conda(env): raise ValueError('請選擇有效的 Conda 環境。')
                verify_tool(c['tool']); files=product_files(c['source'])
                files.extend(Path(c['tool'])/f for f in HASHES)
                c.update(name='ALOS-4 · '+Path(c['source']).name,options={'threads':1})
                key=uuid.uuid4().hex
                s=dict(id=key,config=c,created=time.time(),status='checking',stage='檢查轉換條件',source=stamps(files),error='')
                self.jobs[key]=s;self.save(s);self._launch(s,'check');return self.get(key)
    return ConversionJobs
