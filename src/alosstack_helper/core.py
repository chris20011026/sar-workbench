from pathlib import Path
import datetime
import hashlib
import json
import re
import shlex
import os
import shutil
import xml.etree.ElementTree as ET

LOOKS = ['nrlks1', 'nalks1', 'nrlks2', 'nalks2', 'nrlks_ion', 'nalks_ion']
PROPERTIES = ['number of range looks 1', 'number of azimuth looks 1',
              'number of range looks 2', 'number of azimuth looks 2',
              'number of range looks ion', 'number of azimuth looks ion']
PAIR = re.compile(r'^\d{6}-\d{6}$')

def find_conda(prefix):
    """Resolve a private workbench environment before any shell's Conda."""
    prefix = Path(prefix).resolve()
    managed = Path(os.environ.get('SAR_WORKBENCH_HOME', str(Path.home() / 'sar-workbench'))) / 'runtime'
    candidates = []
    if managed.resolve() in prefix.parents:
        conda = managed / 'miniforge/bin/conda'
        return str(conda) if conda.is_file() else None
    candidates += [prefix / 'bin/conda', prefix.parent.parent / 'bin/conda', Path.home() / 'miniconda3/bin/conda',
                   Path.home() / 'miniforge3/bin/conda']
    return next((str(p) for p in candidates if p.is_file()), shutil.which('conda'))

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def safe_path(value):
    p = Path(value).expanduser().resolve()
    # Upstream generated shell commands do not quote paths.
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(p)):
        raise ValueError('ISCE2 產生的指令未完整引用路徑；運算路徑請使用英文字母、數字、底線或連字號，不含空白。')
    return p

def read_template(path):
    root = ET.parse(path).getroot()
    comp = root.find(".//component[@name='stackinsar']")
    if comp is None:
        raise ValueError('請選擇含 stackinsar 元件的 alosStack XML。')
    values = {}
    for node in comp.findall('property'):
        value = node.findtext('value') if node.find('value') is not None else node.text
        values[node.attrib['name']] = (value or '').strip()
    return values

def write_xml(config, dest):
    values = dict(config.get('properties', {}))
    values.update({'data directory': config['data'], 'dem for coregistration': config['dem'],
                   'dem for geocoding': config['dem_geo'], 'water body': config['wbd'],
                   'reference date of the stack': config['reference']})
    # Keep all outputs inside the managed processing directory.
    values.update(dict(zip(['dates processing directory', 'dates resampled directory',
        'pairs processing directory', 'baseline directory', 'dates directory for ionosphere',
        'pairs processing directory for estimating ionosphere'],
        ['dates', 'dates_resampled', 'pairs', 'baseline', 'dates_ion', 'pairs_ion'])))
    root = ET.Element('stack')
    comp = ET.SubElement(root, 'component', name='stackinsar')
    for key, value in values.items():
        value = str(value)
        if not value.strip():
            continue  # Omitted/commented properties use the installed ISCE2 defaults.
        if key in ('do ionospheric phase estimation', 'apply ionospheric phase correction'):
            if value.lower() not in ('true', 'false'):
                raise ValueError(f'{key} 必須是 True、False 或使用程式預設值。')
            value = value.title()
        if re.search(r'[\n\r;$`|&<>\\]', value):
            raise ValueError(f'參數 {key} 含不支援的 shell 字元。')
        ET.SubElement(comp, 'property', name=key).text = value
    ET.indent(root)
    ET.ElementTree(root).write(dest, encoding='utf-8', xml_declaration=True)

def inspect_data(path):
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError('資料目錄不存在或 NAS 尚未掛載。')
    dates = []
    for folder in sorted(p.iterdir()):
        if folder.is_dir() and re.fullmatch(r'\d{6}', folder.name):
            datetime.datetime.strptime(folder.name, '%y%m%d')
            led = list(folder.glob('LED-ALOS2*'))
            images = list(folder.glob('IMG-*ALOS2*'))
            dates.append({'date': folder.name, 'leaders': len(led), 'images': len(images),
                          'frames': sorted({x.name.split('-')[1][-4:] for x in led}),
                          'polarizations': sorted({x.name.split('-')[1] for x in images}),
                          'modes': sorted({x.name.split('-')[-1] for x in led})})
    for row in dates:
        row['warnings']=[]
        if dates and row['leaders']!=dates[0]['leaders']:row['warnings'].append('frame 數量與第一個日期不同')
        if len(row['frames'])!=row['leaders']:row['warnings'].append('存在重複 frame 產品，請確認是否混入不同版本')
        if len(row['modes'])>1:row['warnings'].append('同一天含不同觀測模式')
    return dates

def parse_cmd3(text):
    # Recognize a standalone (possibly commented) command, not arbitrary shell.
    joined = text.replace('\\\n', ' ')
    hits = []
    for line in joined.splitlines():
        line = line.strip().removeprefix('#').strip()
        if not line or 'ion_ls.py' not in line:
            continue
        tokens = shlex.split(line)
        if tokens and Path(tokens[0]).name == 'ion_ls.py':
            hits.append(tokens)
    if len(hits) != 1:
        raise ValueError('cmd3 必須包含唯一的 ion_ls.py 指令；此版本結構尚未支援。')
    tokens = hits[0]
    allowed = {'-idir', '-odir', '-ref_date_stack', '-zro_date', *('-' + x for x in LOOKS)}
    options = {}; flags = []
    i = 1
    while i < len(tokens):
        k = tokens[i]
        if k in ('-interp', '-ww'):
            flags.append(k); i += 1
        elif k in allowed and i + 1 < len(tokens) and k not in options:
            options[k] = tokens[i + 1]; i += 2
        else:
            raise ValueError(f'不支援或重複的 ion_ls 參數：{k}')
    for k in LOOKS:
        if int(options.get('-' + k, 0)) <= 0:
            raise ValueError(f'缺少有效的 {k}')
    if options.get('-idir') != 'pairs_ion' or options.get('-odir') != 'dates_ion':
        raise ValueError('第一版僅支援專案內的 pairs_ion / dates_ion。')
    if not re.fullmatch(r'\d{6}', options.get('-ref_date_stack', '')):
        raise ValueError('缺少參考日期。')
    if any(re.search(r'[$`\n;&|<>]', v) for v in options.values()):
        raise ValueError('參數包含 shell 運算。')
    return options, flags

def split_pre_review(text):
    # Upstream header has a space after '#'.
    matches = list(re.finditer(r'^#\s*estimate ionospheric phase for each date\s*$', text, re.M))
    if len(matches) != 1:
        raise ValueError('无法辨識 cmd3 的人工檢查邊界。')
    pre = text[:matches[0].start()]
    if not re.search(r'^\s*[^#\n]*ion_check\.py\s', pre, re.M):
        raise ValueError('cmd3 缺少檢查圖片產生步驟。')
    if re.search(r'^\s*[^#\n]*(?:ion_ls|ion_correct)\.py\s', pre, re.M):
        raise ValueError('人工檢查前出現校正指令，已拒絕執行。')
    parse_cmd3(text)
    return pre

def declared_pairs(text, name):
    """Read a literal generated array; never evaluate shell expressions."""
    matches = re.findall(r'^'+re.escape(name)+r'=\(([^\n]*)\)\s*$', text, re.M)
    literal = [shlex.split(x) for x in matches if '$' not in x]
    if len(literal) != 1 or not literal[0] or any(not PAIR.fullmatch(x) for x in literal[0]):
        raise ValueError(f'無法辨識 {name} 的完整配對清單。')
    return sorted(literal[0])

def validate_network(pairs, excluded, required_dates):
    if set(excluded) - set(pairs):
        raise ValueError('排除清單含不存在的配對。')
    graph = {}
    for pair in pairs:
        if not PAIR.fullmatch(pair):
            raise ValueError('配對名稱不合法。')
        if pair in excluded:
            continue
        a, b = pair.split('-')
        graph.setdefault(a, set()).add(b); graph.setdefault(b, set()).add(a)
    if not graph:
        raise ValueError('沒有保留任何配對。')
    seen = set(); todo = [next(iter(graph))]
    while todo:
        x = todo.pop()
        if x not in seen:
            seen.add(x); todo.extend(graph[x] - seen)
    if seen != set(graph) or not set(required_dates) <= seen:
        raise ValueError('排除後配對網路不連通，或缺少後續校正需要的日期。')
    return sorted(set(pairs) - set(excluded))
