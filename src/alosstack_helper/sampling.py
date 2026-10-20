"""Explicit read -> human frame review -> sampling -> matching boundary."""
import ast
import json
from pathlib import Path
import re
import shlex
import time

READ_PHASE = 'cmd1 · 讀取資料'
PLAN_PHASE = 'cmd1 · 檢查取樣與影格'
APPLY_PHASE = 'cmd1 · 統一取樣'
MATCH_PHASE = 'cmd1 · 對位與幾何'


def selected_frames(config):
    value = config.get('properties', {}).get('frames', '')
    if not value or value == 'None':
        return []
    try:
        frames = ast.literal_eval(value) if isinstance(value, str) else value
    except (ValueError, SyntaxError):
        raise ValueError('影格請從清單選擇，或在 XML 使用四位數字的字串清單。') from None
    if not isinstance(frames, (list, tuple)) or not frames or any(
            not isinstance(f, str) or not re.fullmatch(r'\d{4}', f) for f in frames) or len(set(frames)) != len(frames):
        raise ValueError('影格必須是互不重複的四位數字字串，例如 ["0480"]。')
    return sorted(frames)


def split_read_command(text, config):
    """Recognize the installed generator's simple preamble, never evaluate shell.

    Original cmd1 is retained. The later script keeps its date arrays but has no
    read_data invocation, including on recovery. Unknown generator layouts stop.
    """
    lines = text.replace('\\\n', ' ').splitlines(keepends=True)
    hits = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith('#') or not line.strip():
            continue
        tokens = shlex.split(line)
        if any(Path(t).name == 'read_data.py' for t in tokens):
            hits.append((i, tokens))
    if len(hits) != 1:
        raise ValueError('cmd1 必須有唯一且獨立的 read_data.py；無法安全插入取樣關卡。')
    index, args = hits[0]
    expected = Path(config['env'])/'share/isce2/alosStack/read_data.py'
    if args[0] != str(expected) or any(re.search(r'[$`;&|<>\\\n]', t) for t in args):
        raise ValueError('不支援這個 read_data.py 指令結構，已停止。')
    for line in lines[:index]:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not re.fullmatch(r'dates2?=\((?:\d{6}(?: +\d{6})*)?\)\s*', line):
            raise ValueError('read_data 前有非預期操作，不能保證在影像匹配前統一取樣。')
    options = {}; key = None
    for token in args[1:]:
        if token.startswith('-'):
            if token not in ('-idir','-odir','-ref_date','-sec_date','-pol','-frames','-starting_swath','-ending_swath','-virtual') or token in options:
                raise ValueError('讀取指令包含未支援或重複的選項。')
            key = token; options[key] = []
        elif key:
            options[key].append(token)
        else:
            raise ValueError('讀取指令的參數格式無效。')
    for key, value in {'-idir':config['data'], '-odir':'dates', '-ref_date':config['reference']}.items():
        if options.get(key) != [value]:
            raise ValueError('讀取指令與專案設定不一致：'+key)
    dates = options.get('-sec_date', [])
    if len(dates) < 2 or config['reference'] not in dates or len(set(dates)) != len(dates) or any(not re.fullmatch(r'\d{6}', d) for d in dates):
        raise ValueError('取樣統一需要本次所有日期（包含參考日期）的明確清單。')
    if options.get('-pol') not in (['HH'], ['HV'], ['VH'], ['VV']) or options.get('-virtual', []):
        raise ValueError('極化或虛擬讀取選項無效。')
    if options.get('-frames', []) != selected_frames(config):
        raise ValueError('讀取指令的影格與專案選擇不一致。')
    for key in ('-starting_swath', '-ending_swath'):
        if key in options and (len(options[key]) != 1 or not re.fullmatch(r'[1-5]', options[key][0])):
            raise ValueError('子波束範圍無效。')
    remaining = ''.join(lines[:index]+['# read_data 已由工作台獨立執行；取樣確認後才執行下列步驟。\n']+lines[index+1:])
    return dict(argv=args, dates=sorted(dates), reference=config['reference'], source=config['data'],
                frames=selected_frames(config)), remaining


def prepare(engine, state, proc):
    from .core import atomic_json
    request, later = split_read_command((proc/'cmd_1.sh').read_text(), state['config'])
    atomic_json(proc/'helper_sampling_request.json', request)
    (proc/'helper_cmd1_after_read.sh').write_text(later)
    return ['helper_sampling_request.json', 'helper_cmd1_after_read.sh']


def invoke(engine, state, mode):
    engine.run(state, ['python', str(Path(__file__).with_name('sampling_worker.py')), mode,
                      'helper_sampling_request.json'], conda=True)


def approve(state, options, proc):
    from .core import atomic_json
    report = state.get('sampling', {})
    on_disk = json.loads((proc/'helper_sampling_plan.json').read_text())
    if not report.get('plan_id') or report != on_disk or options.get('plan_id') != report['plan_id']:
        raise ValueError('取樣報告已改變，請重新載入檢查關卡。')
    notes = options.get('frame_notes', '')
    if options.get('frames_verified') is not True or not isinstance(notes, str) or not 3 <= len(notes.strip()) <= 2000:
        raise ValueError('請先人工確認各日期的影格地理對應，並填寫確認依據。影格編號相同不代表已對位。')
    approval = dict(plan_id=report['plan_id'], frames_verified=True, notes=notes.strip(),
                    time=time.strftime('%Y-%m-%d %H:%M:%S'))
    atomic_json(proc/'helper_sampling_approval.json', approval)
    state['sampling_approval'] = approval


def completion(state, proc):
    """Bind a completed transaction to this project's reviewed plan.

    The worker's ``verify`` also inspects the actual current SLCs and metadata.
    This lightweight check is used before accepting a new matching action, then
    again within the worker; a status label alone never authorizes matching.
    """
    try:
        report = json.loads((proc/'helper_sampling_plan.json').read_text())
        approved = json.loads((proc/'helper_sampling_approval.json').read_text())
        receipt = json.loads((proc/'helper_sampling/receipt.json').read_text())
    except (OSError, ValueError):
        raise ValueError('缺少有效的取樣完成與人工確認紀錄，不能開始影像匹配。') from None
    if (not isinstance(report, dict) or not isinstance(approved, dict) or not isinstance(receipt, dict)
            or report != state.get('sampling') or not report.get('plan_id')
            or approved != state.get('sampling_approval') or approved.get('frames_verified') is not True
            or approved.get('plan_id') != report['plan_id']
            or receipt.get('frames') != approved or receipt.get('plan_id') != report['plan_id']):
        raise ValueError('取樣完成紀錄與當次計畫或人工確認不一致，不能開始影像匹配。')
    post = receipt.get('post')
    if not isinstance(post, dict) or type(post.get('changed')) is not int or post['changed'] != 0:
        raise ValueError('取樣後檢查尚未通過，不能開始影像匹配。')
    return post


def before_matching(engine, state, proc):
    """Stop independently at frame review and at verified sampling completion."""
    # Explicit matching and recovery at matching/later stages must skip the two
    # earlier gates. Older snapshots use the same phase names and remain valid.
    resume_later = engine.resume_phase is not None and engine.resume_phase not in (
        READ_PHASE, PLAN_PHASE, APPLY_PHASE)
    engine.checkpoint(state, READ_PHASE, lambda: invoke(engine, state, 'read'))
    def plan():
        invoke(engine, state, 'plan')
        state['sampling'] = json.loads((proc/'helper_sampling_plan.json').read_text())
        state.pop('sampling_approval', None)
        state.pop('sampling_result', None)
        state.pop('sampling_match_approval', None)
    engine.checkpoint(state, PLAN_PHASE, plan)
    # A restored phase after equalization retains its confirmed state. Never
    # rerun read_data or resampling just because the page/server was restarted.
    if not state.get('sampling_approval'):
        state.update(status='sampling_review', stage='等待人工確認影格對應與取樣差異')
        return False
    def equalize():
        invoke(engine, state, 'apply')
        invoke(engine, state, 'verify')
        state['sampling_result'] = completion(state, proc)
    engine.checkpoint(state, APPLY_PHASE, equalize)
    if not resume_later:
        state.update(status='sampling_ready', stage='取樣統一已完成，請檢查成果後再開始影像匹配')
        return False
    def match():
        invoke(engine, state, 'verify')
        state['sampling_result'] = completion(state, proc)
        engine.shell(state, proc/'helper_cmd1_after_read.sh')
    engine.checkpoint(state, MATCH_PHASE, match)
    return True
