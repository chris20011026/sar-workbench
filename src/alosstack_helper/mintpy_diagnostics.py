"""Review receipts and bounded read-only MintPy diagnostics.

The web server can verify receipts without importing the scientific environment.
Raster previews are sampled diagnostics, not an automatic scientific verdict.
"""
from pathlib import Path
import datetime, hashlib, json, math, re, time


def save(path, value):
    path = Path(path); tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    tmp.replace(path)


def evidence(folder):
    folder = Path(folder)
    files = [folder/'mintpy.cfg', folder/'inputs/ifgramStack.h5', folder/'inputs/geometryRadar.h5']
    for pattern in ('timeseries*.h5', '*Coherence.h5', 'avgSpatialCoh.h5', 'mask*.h5', 'numTri*.h5', 'rms_*.txt', 'exclude_date.txt', 'reference_date.txt'):
        files.extend(folder.glob(pattern))
    rows = []
    for p in sorted(set(files)):
        if not p.is_file(): continue
        if not p.resolve().is_relative_to(folder.resolve()): raise ValueError('診斷輸出不可連到分析資料夾外。')
        stat = p.stat(); row = dict(path=str(p.relative_to(folder)), size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        if p.suffix in ('.cfg', '.txt'): row['sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
        rows.append(row)
    return rows


def seal(folder, report):
    report['evidence'] = evidence(folder)
    report['report_id'] = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    save(Path(folder)/'diagnostics.json', report)
    save(Path(folder)/'exports'/('diagnostics-'+report['stage']+'.json'), report)
    return report


def verify(folder, report_id):
    path = Path(folder)/'diagnostics.json'
    if not path.is_file(): raise ValueError('找不到診斷報告，請重新建立分析。')
    report = json.loads(path.read_text()); saved = report.pop('report_id', None)
    actual = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    report['report_id'] = saved
    if not report_id or saved != report_id or actual != saved:
        raise ValueError('診斷報告已變更，請重新開啟最新報告再確認。')
    if evidence(folder) != report.get('evidence'):
        raise ValueError('診斷來源或設定已改變，這份確認不能繼續使用；請另建分析版本。')
    return report


def decision(report, data):
    if data.get('reviewed') is not True: raise ValueError('請先完成診斷判讀並勾選確認。')
    if report.get('network',{}).get('connected') is False:
        raise ValueError('保留配對的網路不連通，請另建分析調整配對，不能以人工確認略過。')
    notes = str(data.get('notes', '')).strip()
    if not 3 <= len(notes) <= 4000: raise ValueError('請記錄判讀依據（3–4000 個字）。')
    result = dict(report_id=report['report_id'], stage=report['stage'], notes=notes, time=time.strftime('%Y-%m-%d %H:%M:%S'))
    if report['stage'] == 'residual':
        excluded = data.get('excluded_dates')
        if not isinstance(excluded, list) or any(not isinstance(d, str) for d in excluded):
            raise ValueError('請明確選擇速度擬合的排除日期；保留全部請傳送空清單。')
        excluded = sorted(set(excluded)); dates = report['dates']
        if any(d not in dates for d in excluded): raise ValueError('排除日期不在本次時序內。')
        reference = data.get('reference_date')
        if reference not in dates or reference in excluded: raise ValueError('參考日期必須在保留日期中。')
        if len(set(dates)-set(excluded)) < 3: raise ValueError('速度擬合至少需要三個日期。')
        result.update(excluded_dates=excluded, reference_date=reference)
    return result


def dates_text(path, allowed):
    if not Path(path).is_file(): return []
    return sorted({v for v in Path(path).read_text().split() if v in allowed})


def render(array, valid, folder, name, shape=None, stride=1, limits=None, cmap='viridis'):
    import numpy as np
    from matplotlib import colormaps
    from PIL import Image
    a = np.asarray(array); valid = np.asarray(valid)&np.isfinite(a); values = a[valid]
    lo, hi = limits or (np.percentile(values, [2, 98]).tolist() if values.size else [0., 1.])
    lo, hi = float(lo), float(hi)
    if hi <= lo: hi = lo+1
    rgba = (colormaps[cmap](np.clip(np.nan_to_num((a-lo)/(hi-lo)), 0, 1))*255).astype('uint8')
    rgba[~valid] = [224, 230, 223, 255]
    Image.fromarray(rgba).save(Path(folder)/'exports'/name)
    h, w = shape or a.shape
    return dict(file=name, min=lo, max=hi, width=w, height=h, preview_width=a.shape[1], preview_height=a.shape[0], stride=stride)


def sampled(dataset, index=None):
    shape = dataset.shape[-2:]; stride = max(1, math.ceil(max(shape)/600))
    a = dataset[::stride, ::stride] if index is None else dataset[index, ::stride, ::stride]
    return a, shape, stride


def scalar(value):
    value = float(value)
    return value if math.isfinite(value) else None


def network(folder, config):
    import h5py, numpy as np
    with h5py.File(Path(folder)/'inputs/ifgramStack.h5', 'r') as f:
        pairs = [[d.decode() for d in row] for row in f['date'][:]]
        kept = f['dropIfgram'][:].astype(bool); all_dates = sorted({d for row in pairs for d in row})
        dates = sorted({d for row,keep in zip(pairs,kept) if keep for d in row})
        bp = f['bperp'][:] if 'bperp' in f else None
        rows = []; components = []
        explicit = set(str(config.get('mintpy.network.excludeDate12', '')).replace(',', ' ').split())
        for i, (a, b) in enumerate(pairs):
            coh, shape, stride = sampled(f['coherence'], i); valid = np.isfinite(coh)&(coh > 0)
            pair = a+'_'+b
            rows.append(dict(pair=pair, kept=bool(kept[i]), coherence=scalar(np.mean(coh[valid])) if valid.any() else None,
                             bperp=scalar(np.nanmean(bp[i])) if bp is not None else None,
                             reason='保留' if kept[i] else ('設定中明確排除' if pair in explicit else '原生網路篩選排除；請參照設定及日誌')))
            cc, _, _ = sampled(f['connectComponent'], i)
            components.append(dict(pair=pair, kept=bool(kept[i]), labels=[int(v) for v in np.unique(cc[cc > 0])],
                                   preview=render(cc, cc > 0, folder, 'diagnostic-cc-'+pair+'.png', shape, stride, cmap='tab20')))
    # Fit date baselines to the native per-pair differences. This is a display
    # reconstruction anchored at the earliest date, not a new orbital solution.
    index = {d:i for i,d in enumerate(dates)}; matrix = []; values = []; graph = {d:set() for d in dates}
    for row, (a,b) in zip(rows,pairs):
        if row['kept']: graph[a].add(b); graph[b].add(a)
        if row['bperp'] is not None and row['kept']:
            v = np.zeros(len(dates)); v[index[a]] = -1; v[index[b]] = 1; matrix.append(v[1:]); values.append(row['bperp'])
    seen = set(); pending = [dates[0]] if dates else []
    while pending:
        d = pending.pop()
        if d not in seen: seen.add(d); pending.extend(graph[d]-seen)
    baseline = (np.r_[0., np.linalg.lstsq(np.asarray(matrix), values, rcond=None)[0]]
                if matrix and len(dates)>1 and np.linalg.matrix_rank(np.asarray(matrix))==len(dates)-1 else None)
    triangles = sum(len(graph[a]&graph[b]&set(dates[j+1:])) for i,a in enumerate(dates) for j,b in enumerate(dates) if j>i and b in graph[a])
    return (dict(dates=[dict(date=d,retained=d in index,bperp=scalar(baseline[index[d]]) if baseline is not None and d in index else None) for d in all_dates],
                 retained_dates=dates,pairs=rows, connected=bool(dates) and len(seen)==len(dates), triangles=triangles,
                 meaning='橫軸為真實日期；垂直基線由保留配對的原生 bperp 差值重建並以最早日期設零。相干性為有值像素的降採樣平均。'),
            dict(pairs=components, sampled=True, meaning='顏色只表示連通區標籤，不表示高低或好壞。不同配對的同色不代表同一區域；小區域可能未被預覽抽到。'))


def closure(folder, triangles):
    import h5py, numpy as np
    path = Path(folder)/'numTriNonzeroIntAmbiguity.h5'
    if triangles == 0:
        return dict(status='skipped', reason='保留網路沒有三角形，無法用三角閉合檢查；這不代表沒有解纏錯誤。', triangles=0)
    if not path.is_file(): return dict(status='unavailable', reason='原生步驟未產生閉合統計，不能視為零錯誤。請檢查日誌。', triangles=triangles)
    with h5py.File(path, 'r') as f:
        names = [k for k in f if isinstance(f[k], h5py.Dataset) and f[k].ndim==2]
        if not names: return dict(status='unavailable', reason='閉合統計沒有可讀取的二維資料。', triangles=triangles)
        a, shape, stride = sampled(f[names[0]])
    valid = np.isfinite(a); values = a[valid]; counts, edges = np.histogram(values, bins=min(40,max(1,triangles+1)))
    return dict(status='available', reason='每個像素非零整數閉合的三角形數；可能包含解纏與非閉合相位影響，需配合地物、相干性與連通區判讀。灰色包含原生無效值，不等於零錯誤。', triangles=triangles,
                preview=render(a,valid,folder,'diagnostic-closure.png',shape,stride,[0,max(1,triangles)]),
                histogram=dict(edges=edges.tolist(),counts=counts.tolist(),sampled=True))


def rms(folder, config, dates):
    folder = Path(folder)
    if not (folder/'timeseriesResidual.h5').is_file():
        return dict(status='skipped', reason='沒有 timeseriesResidual.h5，本次未執行殘差 RMS；停用 DEM 誤差校正常會出現此情況。無法據此判定所有日期良好。', rows=[], recommended_excluded_dates=[], recommended_reference_date=None)
    name = 'rms_timeseriesResidual'+('_ramp' if config.get('mintpy.residualRMS.deramp') else '')+'.txt'
    path = folder/name; rows=[]
    if path.is_file():
        for line in path.read_text().splitlines():
            cols = line.split()
            if len(cols)>=2 and cols[0] in dates:
                try: value=float(cols[1])*1000
                except ValueError: continue
                if math.isfinite(value): rows.append(dict(date=cols[0], rms_mm=value))
    if len(rows)!=len(dates) or {r['date'] for r in rows} != set(dates):
        return dict(status='unavailable', reason='殘差檔存在，但原生 RMS 日期表不完整；請檢查日誌，不會自動採用排除建議。', rows=rows, recommended_excluded_dates=[], recommended_reference_date=None)
    recommended=dates_text(folder/'exclude_date.txt', dates); reference=dates_text(folder/'reference_date.txt', dates)
    for row in rows: row['suggested_exclude']=row['date'] in recommended
    # Native MAD threshold uses center=0, normal-distribution scaling.
    import numpy as np
    threshold=float(np.median([r['rms_mm'] for r in rows])/0.6744897501960817*float(config.get('mintpy.residualRMS.cutoff',3)))
    return dict(status='available', reason='RMS 來自原生殘差檔與所選遮罩／去斜坡設定。高 RMS 只是待檢查建議，可能混合誤差與模型未描述的訊號；不自動排除日期。', rows=rows, recommended_excluded_dates=recommended, recommended_reference_date=reference[0] if reference else None, threshold_mm=threshold, source=name)


def build(job_path, stage):
    try: from . import mintpy_runtime as runtime
    except ImportError: import mintpy_runtime as runtime
    p = Path(job_path).parent; job=json.loads(Path(job_path).read_text()); config=runtime.resolve_file(p/'mintpy.cfg')
    (p/'exports').mkdir(exist_ok=True)
    net, cc = network(p,config); dates=net['retained_dates']
    if stage=='residual':
        import h5py
        with h5py.File(p/'timeseries.h5','r') as f:dates=[v.decode() for v in f['date'][:]]
    if stage=='unwrap':
        configured=runtime.excluded_dates(config.get('mintpy.timeFunc.excludeDate'), dates, p)
        job['review_initial_excluded_dates']=configured; save(job_path,job)
    else: configured=job.get('review_initial_excluded_dates',[])
    report=dict(stage=stage,created=time.strftime('%Y-%m-%d %H:%M:%S'),dates=dates,network=net,components=cc,closure=closure(p,net['triangles']),
                rms=rms(p,config,dates) if stage=='residual' else dict(status='not_requested',reason='反演與校正尚未執行。',rows=[]),
                reference_date=runtime.date8(job['options']['reference_date']),current_excluded_dates=configured,
                steps=list(job.get('parameter_report',{}).get('corrections',[])),
                guidance=['先看配對網路是否連通，再檢視閉合統計與連通區；圖像異常不等於必須排除。',
                          '若需改配對或修正方法，請保留本分析並另建版本。繼續表示已記錄判讀，不代表通過科學驗證。'] if stage=='unwrap' else
                         ['先比較可疑日期的空間分布及校正前後結果，再決定是否從速度擬合排除。',
                          '此處排除只影響速度時間模型，不會刪除原始影像、已反演時序或干涉配對。',
                          '先前設定的速度擬合起訖日期與時間模型仍會套用；繼續前會再檢查日期數與模型是否可估計。',
                          '參考日期只是時間零點，不保證該日沒有真實變形。'])
    report['steps'].append(dict(step='quick_overview',state=report['closure']['status'],detail=report['closure']['reason']))
    report['steps'].append(dict(step='residual_RMS',state=report['rms']['status'],detail=report['rms']['reason']))
    return seal(p,report)


def spatial(job_path, date):
    """Compare same epoch in available time-series stages on one sampled grid."""
    import h5py, numpy as np
    try: from . import mintpy_runtime as runtime
    except ImportError: import mintpy_runtime as runtime
    job_path=Path(job_path); p=job_path.parent; job=json.loads(job_path.read_text())
    if job.get('status') not in ('residual_review','complete'): raise ValueError('請先完成時序反演至殘差判讀關卡。')
    if not isinstance(date,str) or not re.fullmatch(r'\d{8}',date): raise ValueError('請選擇本次時序日期。')
    reference=runtime.date8(job['options']['reference_date']); ref=job.get('reference_pixel')
    candidates=[('raw','反演後（校正前）',p/'timeseries.h5')]
    for row in job.get('parameter_report',{}).get('corrections',[]):
        if row.get('state')=='applied' and row.get('artifact'):
            path=p/row['artifact']
            if path not in [v[2] for v in candidates]: candidates.append((row['step'],row['label'],path))
    if job.get('timeseries_file'):
        try: from .science_worker import timeseries_path
        except ImportError: from science_worker import timeseries_path
        path=timeseries_path(job_path,job)
        if path not in [v[2] for v in candidates]: candidates.append(('final','最終時序',path))
    arrays=[]; shape=None; stride=None; limits=[]
    with h5py.File(p/'maskTempCoh.h5','r') as f: mask,shape,stride=sampled(f['mask']);mask=mask.astype(bool)
    for identity,label,path in candidates:
        if not path.is_file() or not path.resolve().is_relative_to(p.resolve()): continue
        with h5py.File(path,'r') as f:
            if 'timeseries' not in f or 'date' not in f: continue
            dates=[v.decode() for v in f['date'][:]]
            if date not in dates or reference not in dates: raise ValueError('比較日期或共同參考日期不在時序內。')
            ds=f['timeseries']
            if ds.shape[-2:] != shape: raise ValueError('各校正階段的網格不一致，不能直接比較。')
            a=np.asarray(ds[dates.index(date),::stride,::stride],dtype='float64')-ds[dates.index(reference),::stride,::stride]
            y,x=ref or [int(f.attrs['REF_Y']),int(f.attrs['REF_X'])]
            a-=float(ds[dates.index(date),y,x])-float(ds[dates.index(reference),y,x]);a*=1000
            valid=mask&np.isfinite(a); vals=a[valid]
            if vals.size: limits.append(float(np.percentile(np.abs(vals),98)))
            arrays.append((identity,label,path.name,a,valid))
    if not arrays: raise ValueError('找不到可比較的時序影像。')
    bound=max(limits,default=1.) or 1.; stages=[]
    for identity,label,file,a,valid in arrays:
        preview=render(a,valid,p,'diagnostic-epoch-'+identity+'-'+date+'.png',shape,stride,[-bound,bound],'RdBu_r')
        stages.append(dict(id=identity,label=label,file=file,preview=preview))
    return dict(date=date,reference_date=reference,reference_pixel=ref,unit='mm',limits=[-bound,bound],stages=stages,
                meaning='雷達座標降採樣預覽；同一日期使用共同空間參考點、時間參考日期與色階。校正前後差異不是獨立真值誤差，灰色為遮罩／無資料。')
