"""Bounded diagnostics for ISCE2 alosStack ampcor.off / cull.off products.

Offset columns follow Alos2ProcPublic.writeOffset: x, dx, y, dy, SNR,
covariance xx/yy/xy. The independent affine refit below is a descriptive
diagnostic, not the polynomial used by ISCE2 and not scientific validation.
No pickle loading, raster writes, or scientific subprocesses are performed.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import time

from .core import atomic_json

MAX_FILE = 16 * 1024**2
MAX_REPORT = 16 * 1024**2
MAX_POINTS = 50000
MAX_ROWS = 500
LIMITATIONS = [
    '偏移以像素表示，是影像對位量；不是地表位移。',
    '殘差為工作台另作的一階平面診斷擬合，並非 ISCE2 實際二階重取樣模型，也不能單獨證明配準精度。',
    '目前不以固定殘差門檻自動判定科學品質；請結合點位分布、日誌、振幅與後續相干性判讀。',
]


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def local_file(proc, relative):
    """No traversal, symlink leaf/ancestor or external path is accepted."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('品質檢查路徑超出專案。')
    path = proc / relative
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('品質檢查不接受符號連結：' + str(relative))
        if part == proc:
            break
    if proc.resolve() not in path.resolve().parents:
        raise ValueError('品質檢查路徑超出專案。')
    return path


def file_evidence(path, small=True):
    if not path.exists():
        return {'missing': True}
    if not path.is_file():
        raise ValueError('品質檢查需要一般檔案。')
    st = path.stat()
    if small and st.st_size > MAX_FILE:
        raise ValueError('品質檢查檔案過大。')
    result = {'size': st.st_size}
    if small:
        result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        result['mtime_ns'] = st.st_mtime_ns
    # Detect changes while reading, without binding recovery copies to inode.
    after = path.stat()
    if (st.st_size, st.st_mtime_ns, st.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('讀取品質證據時檔案發生變動。')
    return result


def offsets(path):
    evidence = file_evidence(path)
    if evidence.get('missing'):
        return None, evidence
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != evidence['sha256']:
        raise ValueError('偏移檔在讀取期間改變。')
    try:
        lines = content.decode('ascii').splitlines()
    except UnicodeError:
        raise ValueError('偏移檔不是可讀取的 ASCII 文字。') from None
    if len(lines) > MAX_POINTS:
        raise ValueError('偏移檔超過 50000 點的診斷上限。')
    points = []
    for line in lines:
        if not line.strip():
            continue
        try:
            point = tuple(map(float, line.split()))
        except ValueError:
            raise ValueError('偏移檔含非數值資料。') from None
        if len(point) != 8 or any(not math.isfinite(v) or abs(v) > 1e12 for v in point):
            raise ValueError('偏移檔需為 8 欄有限數值，且在可診斷範圍內。')
        if point[0] < 0 or point[2] < 0 or point[4] < 0:
            raise ValueError('偏移點座標或 SNR 無效。')
        points.append(point)
    return points, evidence


def distribution(values):
    if not values:
        return None
    absolute = sorted(abs(v) for v in values)
    return dict(min=min(values), median=statistics.median(values), max=max(values),
                p95_abs=absolute[math.ceil(.95 * len(absolute)) - 1])


def solve(matrix, vector):
    a = [list(row) + [value] for row, value in zip(matrix, vector)]
    for i in range(3):
        pivot = max(range(i, 3), key=lambda j: abs(a[j][i]))
        a[i], a[pivot] = a[pivot], a[i]
        if abs(a[i][i]) < 1e-10:
            return None
        divisor = a[i][i]
        a[i] = [x / divisor for x in a[i]]
        for j in range(3):
            if j != i:
                factor = a[j][i]
                a[j] = [x - factor * y for x, y in zip(a[j], a[i])]
    return [row[-1] for row in a]


def diagnostic_fit(points):
    result = dict(model='affine', points=len(points), range_rms_px=None, azimuth_rms_px=None, reason='')
    if len(points) < 6:
        result['reason'] = '保留點少於 6 個，未計算診斷殘差。'
        return result
    xs, ys = [p[0] for p in points], [p[2] for p in points]
    xspan, yspan = max(xs) - min(xs), max(ys) - min(ys)
    if not xspan or not yspan:
        result['reason'] = '點位沒有足夠的二維分布，未計算診斷殘差。'
        return result
    xmin, ymin = min(xs), min(ys)
    design = [(1., (p[0] - xmin) / xspan, (p[2] - ymin) / yspan) for p in points]
    matrix = [[math.fsum(r[i] * r[j] for r in design) for j in range(3)] for i in range(3)]
    for column, key in ((1, 'range_rms_px'), (3, 'azimuth_rms_px')):
        vector = [math.fsum(r[i] * p[column] for r, p in zip(design, points)) for i in range(3)]
        fitted = solve(matrix, vector)
        if fitted is None:
            result['reason'] = '點位近似共線，無法穩定擬合。'
            return result
        result[key] = math.sqrt(math.fsum((p[column] - sum(a * b for a, b in zip(r, fitted)))**2
                                        for r, p in zip(design, points)) / len(points))
    return result


def bindings(state, proc):
    """Inputs and sampling receipts bound independently of diagnostic content."""
    from .sampling import completion
    post = completion(state, proc)
    files = {}
    for name in ('helper_sampling_plan.json', 'helper_sampling_approval.json', 'helper_sampling/receipt.json'):
        files[name] = file_evidence(local_file(proc, name))
    if len(post.get('rows', [])) > MAX_ROWS or len(post.get('metadata', {})) > MAX_ROWS * 10:
        raise ValueError('品質檢查的影格數超出上限。')
    for row in post.get('rows', []):
        relative = Path('dates') / row['relative']
        for suffix in ('.xml', '.vrt'):
            name = str(relative) + suffix
            files[name] = file_evidence(local_file(proc, name))
            original = row.get(suffix[1:], {})
            if any(files[name].get(k) != original.get(k) for k in ('size', 'sha256')):
                raise ValueError('取樣後的 SLC 描述檔已變更，請先檢查成果。')
        raw = Path(row['raw'])
        # The sampling receipt itself was produced by a confined native reader.
        # Still require the path to stay inside the configured input/dates roots.
        allowed = [Path(state['config']['data']).resolve(), (proc / 'dates').resolve()]
        resolved = raw.resolve()
        if not any(base in resolved.parents for base in allowed):
            raise ValueError('取樣來源已超出專案選定範圍。')
        files[str(raw)] = file_evidence(raw, small=False)
        if files[str(raw)] != row.get('raw_stamp'):
            raise ValueError('取樣後的 SLC 來源已變更，請先檢查成果。')
    for name in post.get('metadata', {}):
        name = str(Path('dates') / name)
        files[name] = file_evidence(local_file(proc, name))
        # cmd1 mosaic_parameter.py legitimately rewrites track/frame XML.
        # Bind the post-matching version here, rather than rejecting it because
        # it differs from the pre-matching sampling receipt.
    return dict(config=signature(state['config']), plan_id=state['sampling']['plan_id'],
                sampling_match_approval=state.get('sampling_match_approval'), files=files)


def collect(state, proc):
    before = bindings(state, proc)
    reference = state['sampling'].get('reference') or state['config']['reference']
    plan_rows = state['sampling'].get('rows', [])
    if len(plan_rows) > MAX_ROWS:
        raise ValueError('品質檢查的影格數超出上限。')
    rows, evidence = [], {}
    total_bytes = 0
    seen = set()
    for source in plan_rows:
        if source['date'] == reference:
            continue
        date, frame, swath = source['date'], source['frame'], source['swath']
        key = (date, frame, swath)
        if (not re.fullmatch(r'\d{6}', str(date)) or not re.fullmatch(r'\d{4}', str(frame))
                or type(swath) is not int or not 1 <= swath <= 5 or key in seen):
            raise ValueError('匹配診斷的日期／影格／子波束無效或重複。')
        seen.add(key)
        relative = Path(source['relative'])
        if (relative.name != date + '.slc' or len(relative.parts) != 4 or relative.parts[0] != date
                or not re.fullmatch(r'f[1-9]\d*_' + frame, relative.parts[1]) or relative.parts[2] != f's{swath}'):
            raise ValueError('匹配診斷的 SLC 相對路徑無效。')
        folder = Path('dates') / relative.parent
        row = dict(date=date, frame=frame, swath=swath, kind='missing', raw_count=None,
                   retained_count=None, rejected_fraction=None, range=None, azimuth=None,
                   snr_median=None, diagnostic_fit=None, warnings=[], errors=[], points=[])
        parsed = {}
        for name in ('ampcor.off', 'cull.off'):
            file = folder / name
            try:
                path = local_file(proc, file)
                total_bytes += path.stat().st_size if path.exists() else 0
                if total_bytes > 64 * 1024**2:
                    raise ValueError('偏移證據合計超過 64 MB 的診斷上限。')
                parsed[name], evidence[str(file)] = offsets(path)
            except (ValueError, OSError) as exc:
                parsed[name] = None
                row['errors'].append(f'{name}：{exc}')
        raw, retained = parsed['ampcor.off'], parsed['cull.off']
        row['raw_count'] = None if raw is None else len(raw)
        row['retained_count'] = None if retained is None else len(retained)
        if not retained:
            row['errors'].append('缺少非空的 cull.off 保留偏移點，不能開始干涉處理。')
        else:
            row['kind'] = 'correlation' if raw else 'geometry_or_unknown'
            ref_row = next((r for r in plan_rows if r['date'] == reference
                            and r['frame'] == frame and r['swath'] == swath), {})
            width, length = ref_row.get('width'), ref_row.get('length')
            if type(width) is int and type(length) is int and min(width, length) > 0:
                # ISCE2 ampcor/OffsetField coordinates are one based in the
                # reference swath, prior to resampling onto the common grid.
                if any(not (1 <= p[0] <= width and 1 <= p[2] <= length) for p in (raw or retained)):
                    row['errors'].append('偏移點超出參考 SLC 範圍，可能是錯誤影格或不一致的偏移資料。')
            else:
                row['warnings'].append('缺少參考 SLC 尺寸，未能核對偏移點是否位於影像範圍內。')
            if raw:
                # Geometrical outputs can use the same .off format. Only a
                # multiset subset supports the culling fraction interpretation.
                if Counter(retained) - Counter(raw):
                    row['errors'].append('cull.off 並非 ampcor.off 的保留子集，來源不一致。')
                else:
                    row['rejected_fraction'] = 1 - len(retained) / len(raw)
                    if len(retained) == len(raw):
                        row['warnings'].append('未剔除匹配點；可能原始點全數保留，也可能 ISCE2 因保留點不足而退回原始點，請核對日誌。')
                row['snr_median'] = statistics.median(p[4] for p in retained)
            else:
                row['warnings'].append('沒有 ampcor.off 相關匹配證據；cull.off 可能來自幾何偏移。不能把這些點視為已驗證的相關匹配點。')
            row['range'] = distribution([p[1] for p in retained])
            row['azimuth'] = distribution([p[3] for p in retained])
            row['diagnostic_fit'] = diagnostic_fit(retained)
            if row['diagnostic_fit']['reason']:
                row['warnings'].append(row['diagnostic_fit']['reason'])
            stride = max(1, math.ceil(len(retained) / 100))
            row['points'] = [dict(x=p[0], y=p[2], range=p[1], azimuth=p[3]) for p in retained[::stride]]
        rows.append(row)
    report = dict(schema=1, plan_id=state['sampling']['plan_id'], reference=reference, rows=rows,
                  warnings=list(LIMITATIONS), errors=[], _binding=before, _files=evidence)
    report['summary'] = dict(expected=len(rows), **{kind: sum(r['kind'] == kind for r in rows)
                            for kind in ('correlation', 'geometry_or_unknown', 'missing')})
    if not rows:
        report['errors'].append('沒有次要日期的預期匹配資料，不能確認匹配完成。')
    report['can_continue'] = bool(rows) and not report['errors'] and not any(r['errors'] for r in rows)
    if bindings(state, proc) != before:
        raise ValueError('讀取品質資料期間取樣成果或來源改變，請重新檢查。')
    for name, recorded in evidence.items():
        if file_evidence(local_file(proc, name)) != recorded:
            raise ValueError('讀取期間匹配證據已改變，請重新檢查。')
    report['report_id'] = signature(report)
    if len(json.dumps(report, ensure_ascii=False, indent=2).encode()) > MAX_REPORT:
        raise ValueError('匹配品質報告超過可讀取大小上限，未發佈無法驗證的報告。')
    return report


def publish(state, proc):
    report = collect(state, proc)
    path = local_file(proc, 'helper_matching_quality.json')
    atomic_json(path, report)
    state['matching_quality'] = report
    state.pop('matching_approval', None)
    local_file(proc, 'helper_matching_approval.json').unlink(missing_ok=True)
    return report


def current(state, proc):
    report = state.get('matching_quality')
    path = local_file(proc, 'helper_matching_quality.json')
    if not isinstance(report, dict) or not path.is_file() or path.stat().st_size > MAX_REPORT:
        raise ValueError('缺少匹配品質報告，請先更新品質檢查。')
    if json.loads(path.read_text()) != report or report.get('report_id') != signature({k: v for k, v in report.items() if k != 'report_id'}):
        raise ValueError('匹配品質報告已改變，請更新品質檢查。')
    if bindings(state, proc) != report['_binding']:
        raise ValueError('取樣或來源資料已改變，請更新品質檢查。')
    for name, expected in report['_files'].items():
        if file_evidence(local_file(proc, name)) != expected:
            raise ValueError('匹配證據已改變，請更新品質檢查後重新確認。')
    return report


def approve(state, options, proc):
    report = current(state, proc)
    if options.get('report_id') != report['report_id']:
        raise ValueError('這不是目前的匹配品質報告，請更新品質檢查。')
    if not report['can_continue']:
        raise ValueError('匹配證據缺失或格式不符，請先處理報告中的問題。')
    notes = options.get('matching_notes', '')
    if options.get('matching_reviewed') is not True or not isinstance(notes, str) or not 3 <= len(notes.strip()) <= 2000:
        raise ValueError('請檢查匹配品質、勾選確認並填寫至少 3 字的判讀依據。')
    approval = dict(report_id=report['report_id'], matching_reviewed=True, notes=notes.strip(),
                    time=time.strftime('%Y-%m-%d %H:%M:%S'))
    atomic_json(local_file(proc, 'helper_matching_approval.json'), approval)
    state['matching_approval'] = approval


def verified_approval(state, proc):
    report = current(state, proc)
    path = local_file(proc, 'helper_matching_approval.json')
    approval = state.get('matching_approval')
    if (not isinstance(approval, dict) or not path.is_file() or path.stat().st_size > 10000
            or json.loads(path.read_text()) != approval or approval.get('matching_reviewed') is not True
            or approval.get('report_id') != report['report_id'] or not report['can_continue']):
        raise ValueError('缺少與目前證據相符的匹配品質人工確認。')
    return approval
