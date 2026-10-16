"""ISCE2 sampling adapter, executed only in the selected science environment.

Uses ISCE2's resamp and the sampling transforms in runPrepareSlc.py (NASA/JPL,
isce-framework/isce2). No environment file is patched. Originals are retained
inside the project; an interrupted publish is never accepted as a valid result.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import shutil
import sys
import time
import xml.etree.ElementTree as ET


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
    tmp.replace(path)


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_stamp(path, small=False):
    path = Path(path)
    st = path.stat()
    if not path.is_file():
        raise ValueError('不是一般檔案：'+str(path))
    result = dict(size=st.st_size, mtime_ns=st.st_mtime_ns)
    if small:
        if st.st_size > 16*1024**2:
            raise ValueError('影像描述檔過大：'+str(path))
        result['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@contextmanager
def at(path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def libraries(request):
    import isce  # ISCE2 bootstraps its component import paths.
    import isceobj
    stack = Path(request['argv'][0]).parent
    sys.path.insert(0, str(stack))
    from StackPulic import loadTrack, saveTrack
    from contrib.alos2proc.alos2proc import resamp
    return isceobj, loadTrack, saveTrack, resamp


def compatible_reader():
    """Scope the upstream unknown-rate fallback to each reader instance.

    Standard ALOS-2 nominal rate constants are retained. Converted ALOS-4 has
    valid decimal MHz values absent from that table. Support an unmodified
    upstream install as well as the user's existing reader, in this process only.
    """
    from isceobj.Sensor.MultiMode.ALOS2 import ALOS2
    original = ALOS2.readLeader
    def read_leader(self):
        records = original(self)
        rate = float(records[1].metadata['Range sampling rate in MHz'])
        if not math.isfinite(rate) or not 1 <= rate <= 200:
            raise ValueError('CEOS 距離取樣率無效。')
        if int(rate) not in self.fsampConst:
            self.fsampConst = {**self.fsampConst, int(rate): rate*1e6}
            print(f'使用 CEOS 描述的距離取樣率：{rate:.9f} MHz（僅本次讀取）', flush=True)
        return records
    ALOS2.readLeader = read_leader
    return ALOS2, original


def read(request):
    # An existing date can make upstream silently skip extraction. Fresh data
    # or an explicitly restored checkpoint is required, never a mixed read.
    if Path('dates').exists():
        raise ValueError('dates 已存在；請從讀取前的檢查點復原，不能混入先前讀取結果。')
    libraries(request)
    reader, original = compatible_reader()
    argv = sys.argv
    try:
        sys.argv = request['argv']
        runpy.run_path(request['argv'][0], run_name='__main__')
    finally:
        sys.argv = argv
        reader.readLeader = original


def positive(value, label):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError('取樣描述無效：'+label)
    return value


def coefficients(value):
    if len(value) != 4 or any(not math.isfinite(float(v)) for v in value):
        raise ValueError('Doppler／方位向 FM 係數必須是四個有限數值。')
    return list(map(float, value))


def scale_polynomial(values, ratio):
    # p(ratio*x) is exact for a cubic: no giant coordinate arrays or least-
    # squares fit are needed. Same transform as runPrepareSlc.py's cubic fit.
    return [v*ratio**i for i, v in enumerate(coefficients(values))]


def image_info(slc, width, length, source, dates):
    """Validate the raw layout consumed by native resamp before entering C."""
    import isceobj
    xml = Path(str(slc)+'.xml'); vrt = Path(str(slc)+'.vrt')
    for p in (slc, xml, vrt):
        if p.is_symlink(): raise ValueError('dates 中的 SLC 不接受符號連結：'+str(p))
    xml_stamp = file_stamp(xml, True); vrt_stamp = file_stamp(vrt, True)
    img = isceobj.createSlcImage(); img.load(str(xml))
    root = ET.parse(vrt).getroot()
    bands = root.findall('VRTRasterBand')
    if (root.tag != 'VRTDataset' or len(bands) != 1 or bands[0].get('subClass') != 'VRTRawRasterBand'
            or bands[0].get('dataType') != 'CFloat32' or int(root.get('rasterXSize', '0')) != width
            or int(root.get('rasterYSize', '0')) != length or img.getWidth() != width or img.getLength() != length
            or str(img.dataType).upper() != 'CFLOAT' or img.bands != 1):
        raise ValueError('SLC／XML／VRT 的型別或尺寸不一致：'+str(slc))
    band = bands[0]; filename = band.find('SourceFilename')
    if filename is None or not filename.text or len(band.findall('SourceFilename')) != 1:
        raise ValueError('VRT 缺少唯一原始影像來源。')
    if filename.get('relativeToVRT') not in ('0', '1'):
        raise ValueError('VRT 來源的相對路徑標記無效。')
    raw = Path(filename.text)
    if filename.get('relativeToVRT') == '1': raw = vrt.parent/raw
    elif not raw.is_absolute(): raise ValueError('VRT 來源路徑不是絕對路徑，也未標記為相對路徑。')
    raw = raw.resolve(strict=True)
    if not any(base == raw or base in raw.parents for base in (source.resolve(), dates.resolve())):
        raise ValueError('VRT 指向專案選定來源以外的檔案：'+str(raw))
    image_offset = int(band.findtext('ImageOffset', '-1'))
    pixel_offset = int(band.findtext('PixelOffset', '-1'))
    line_offset = int(band.findtext('LineOffset', '-1'))
    byte_order = band.findtext('ByteOrder')
    raw_stamp = file_stamp(raw)
    if (pixel_offset != 8 or line_offset < width*8 or image_offset < 0 or byte_order not in ('LSB','MSB')
            or raw_stamp['size'] < image_offset+(length-1)*line_offset+width*8):
        raise ValueError('原始 SLC 不完整，或 VRT 的位元序／列偏移不受支援：'+str(slc))
    if slc.is_file() and raw != slc.resolve():
        raise ValueError('實體 SLC 與 VRT 指向不同資料，請先釐清來源。')
    if slc.is_file() and (slc.stat().st_size != width*length*8 or image_offset != 0 or line_offset != width*8):
        raise ValueError('實體 SLC 的大小／配置不符。')
    return dict(virtual=not slc.exists(), raw=str(raw), raw_stamp=raw_stamp, xml=xml_stamp,
                vrt=vrt_stamp, byte_order=byte_order, image_offset=image_offset, line_offset=line_offset)


def inspect(request):
    _, load_track, _, _ = libraries(request)
    dates_dir = Path('dates').resolve()
    names = request['dates']; reference = request['reference']
    actual = sorted(p.name for p in dates_dir.iterdir() if re.fullmatch(r'\d{6}', p.name))
    if actual != names or reference not in names:
        raise ValueError('實際讀取日期與指令不一致。')
    for p in dates_dir.rglob('*'):
        if p.is_symlink(): raise ValueError('dates 中有未支援的符號連結。')
    tracks = {d:load_track(str(dates_dir/d), d) for d in names}
    ref = tracks[reference]
    if not ref.frames: raise ValueError('參考日期沒有影格。')
    metadata = {}; rows = []
    for d, track in tracks.items():
        # ScanSAR burst timing needs additional treatment not provided by this
        # stripmap adapter. Do not silently claim that equal PRF fixes it.
        if getattr(track, 'operationMode', '') not in ('UBS','UBD','HBS','HBD','HBQ','FBS','FBD','FBQ'):
            raise ValueError('目前取樣統一僅支援 Stripmap；ScanSAR／Spotlight 需額外驗證。')
        if len(track.frames) != len(ref.frames): raise ValueError('日期間影格數不同，請先人工整理對應。')
        for p in sorted((dates_dir/d).rglob('*.xml')):
            metadata[str(p.relative_to(dates_dir))] = file_stamp(p, True)
        dirs = sorted((dates_dir/d).glob('f*_*'))
        if len(dirs) != len(track.frames): raise ValueError('影格目錄數與描述檔不同。')
        for i, (frame, rf, folder) in enumerate(zip(track.frames, ref.frames, dirs)):
            if folder.name != f'f{i+1}_{rf.frameNumber}' or frame.frameNumber != rf.frameNumber:
                raise ValueError('影格順序與參考日期不一致；不能自動猜測影格對應。')
            if not frame.swaths or len(frame.swaths) != len(rf.swaths): raise ValueError('子波束數目不一致。')
            swath_dirs = sorted(p.name for p in folder.glob('s*') if p.is_dir())
            expected_dirs = sorted(f's{sw.swathNumber}' for sw in frame.swaths)
            if swath_dirs != expected_dirs: raise ValueError('子波束目錄與描述檔不一致。')
            for sw, rs in zip(frame.swaths, rf.swaths):
                if sw.swathNumber != rs.swathNumber: raise ValueError('子波束順序不一致。')
                if getattr(sw,'burstLength',None): raise ValueError('不支援含 burst 的取樣統一。')
                width, length = int(sw.numberOfSamples), int(sw.numberOfLines)
                rsr, prf = positive(sw.rangeSamplingRate, 'RSR'), positive(sw.prf, 'PRF')
                target_rsr, target_prf = positive(rs.rangeSamplingRate, '參考 RSR'), positive(rs.prf, '參考 PRF')
                changed = d != reference and (abs(rsr-target_rsr) >= 1 or abs(prf-target_prf) >= 1)
                out_width = round(width/rsr*target_rsr) if changed else width
                out_length = round(length/prf*target_prf) if changed else length
                if min(width,length,out_width,out_length) < 32 or max(width,length,out_width,out_length) > 2147483647:
                    raise ValueError('SLC 尺寸不適用原生重取樣工具。')
                slc = folder/f's{sw.swathNumber}'/(d+'.slc')
                info = image_info(slc, width, length, Path(request['source']), dates_dir)
                fields = ('rangePixelSize','azimuthPixelSize','azimuthLineInterval')
                for field in fields:
                    positive(getattr(sw,field), field); positive(getattr(rs,field), '參考 '+field)
                # prfFraction is absent for ordinary stripmap acquisitions.
                for item in (sw, rs):
                    if item.prfFraction is not None: positive(item.prfFraction, 'prfFraction')
                coefficients(sw.dopplerVsPixel); coefficients(sw.azimuthFmrateVsPixel)
                rows.append(dict(date=d, frame=frame.frameNumber, slot=i+1, swath=sw.swathNumber,
                    relative=str(slc.relative_to(dates_dir)), width=width, length=length,
                    out_width=out_width, out_length=out_length, rsr=rsr, prf=prf,
                    target_rsr=target_rsr, target_prf=target_prf, changed=changed, **info))
    evidence = dict(reference=reference, dates=names, frames=request.get('frames', []), rows=rows, metadata=metadata)
    return dict(**evidence, plan_id=signature(evidence), output_bytes=sum(r['out_width']*r['out_length']*8 for r in rows if r['changed']),
                changed=sum(r['changed'] for r in rows), virtual=sum(r['virtual'] for r in rows),
                scope='統一取樣間距；影格地理對應仍需人工確認，後續匹配、共同頻帶與科學品質需另行檢查。'), tracks


def output_headers(slc, width, length):
    import isceobj
    img = isceobj.createSlcImage()
    img.setFilename(slc.name); img.extraFilename = slc.name+'.vrt'
    img.setWidth(width); img.setLength(length); img.setAccessMode('READ')
    img.setByteOrder('l' if sys.byteorder == 'little' else 'b')
    with at(slc.parent): img.renderHdr()


def apply(request):
    root = Path.cwd(); dates = root/'dates'; transaction = root/'helper_sampling'
    if (transaction/'receipt.json').exists():
        verify(request)
        print('已驗證先前完成的取樣結果，不重複重取樣。', flush=True)
        return
    if transaction.exists():
        raise ValueError('發現未完成的取樣作業；已阻擋影像匹配。請使用恢復中心回到取樣前，備份仍在 helper_sampling。')
    approved = json.loads((root/'helper_sampling_approval.json').read_text())
    plan = json.loads((root/'helper_sampling_plan.json').read_text())
    current, tracks = inspect(request)
    if current != plan or approved.get('frames_verified') is not True or approved.get('plan_id') != plan['plan_id']:
        raise ValueError('讀取結果已改變或影格尚未人工確認，已停止取樣。請重新檢查。')
    if shutil.disk_usage(root).free < plan['output_bytes']+256*1024**2:
        raise ValueError('空間不足：需要新 SLC 與基本緩衝，原始讀取結果會保留。')
    transaction.mkdir(); stage = transaction/'staged'; backup = transaction/'before'; stage.mkdir(); backup.mkdir()
    write_json(transaction/'journal.json', dict(status='staging', plan_id=plan['plan_id']))
    _, _, save_track, resamp = libraries(request)
    changed_dates = set()
    for row in plan['rows']:
        if not row['changed']: continue
        date = row['date']; i = row['slot']-1
        track = tracks[date]; reference = tracks[request['reference']]
        sw = next(s for s in track.frames[i].swaths if s.swathNumber == row['swath'])
        rs = next(s for s in reference.frames[i].swaths if s.swathNumber == row['swath'])
        output = stage/row['relative']; output.parent.mkdir(parents=True, exist_ok=True)
        rg_ratio = row['rsr']/row['target_rsr']; az_ratio = row['prf']/row['target_prf']
        print(f"統一取樣 {date} / {row['frame']} / s{row['swath']}：{row['width']}×{row['length']} → {row['out_width']}×{row['out_length']}", flush=True)
        # Native resamp reads VRTRawRasterBand byte order and CEOS line padding
        # directly; a physical input .slc is deliberately NOT required.
        resamp(str(dates/row['relative']), str(output), 'fake', 'fake', row['out_width'], row['out_length'],
               row['prf'], coefficients(sw.dopplerVsPixel),
               rgcoef=[0.,rg_ratio-1.,0.,0.,0.,0.,0.,0.,0.,0.],
               azcoef=[0.,0.,az_ratio-1.,0.,0.,0.,0.,0.,0.,0.], azpos_off=0.)
        if not output.is_file() or output.stat().st_size != row['out_width']*row['out_length']*8:
            raise ValueError('重取樣輸出大小不正確；原始結果尚未變更。')
        validate_pixels(output)
        output_headers(output, row['out_width'], row['out_length'])
        image_info(output, row['out_width'], row['out_length'], stage, stage)
        sw.dopplerVsPixel = scale_polynomial(sw.dopplerVsPixel, rg_ratio)
        sw.azimuthFmrateVsPixel = scale_polynomial(sw.azimuthFmrateVsPixel, rg_ratio)
        sw.numberOfSamples = row['out_width']; sw.numberOfLines = row['out_length']
        for field in ('rangeSamplingRate','prf','rangePixelSize','azimuthPixelSize','azimuthLineInterval','prfFraction'):
            setattr(sw, field, getattr(rs,field))
        changed_dates.add(date)
    for date in sorted(changed_dates):
        for i, frame in enumerate(tracks[date].frames):
            (stage/date/f'f{i+1}_{frame.frameNumber}').mkdir(parents=True, exist_ok=True)
        with at(stage/date): save_track(tracks[date], date)
    # Recheck all inputs before publishing: a concurrent edit invalidates consent.
    if inspect(request)[0] != plan:
        raise ValueError('取樣期間輸入已改變；已保留新檔，但不替換 dates。')
    files = sorted(str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file())
    journal = dict(status='publishing', plan_id=plan['plan_id'], files=files)
    write_json(transaction/'journal.json', journal)
    for relative in files:
        dest = dates/relative; saved = backup/relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists(): dest.replace(saved)
        (stage/relative).replace(dest)
    post, _ = inspect(request)
    if post['changed']:
        raise ValueError('取樣後仍有頻率差異；已停止後續匹配，請復原檢查。')
    write_json(transaction/'receipt.json', dict(plan_id=plan['plan_id'], post=post,
               time=time.strftime('%Y-%m-%d %H:%M:%S'), frames=approved))
    journal['status'] = 'complete'; write_json(transaction/'journal.json', journal)
    print('取樣統一完成；原始 SLC／描述檔保存在 proc/helper_sampling/before。', flush=True)


def validate_pixels(path):
    import numpy as np
    nonzero = False
    with Path(path).open('rb') as stream:
        while block := stream.read(8*1024*1024):
            values = np.frombuffer(block, dtype=np.complex64)
            if not np.isfinite(values).all():
                raise ValueError('重取樣輸出含 NaN／無限值；已阻擋匹配。')
            nonzero = nonzero or bool(np.any(values != 0))
    if not nonzero:
        raise ValueError('重取樣輸出全為零；已阻擋匹配。')


def verify(request):
    transaction = Path('helper_sampling')
    if not (transaction/'receipt.json').is_file():
        raise ValueError('取樣尚未完整完成，禁止開始影像匹配。')
    receipt = json.loads((transaction/'receipt.json').read_text())
    approved = json.loads(Path('helper_sampling_approval.json').read_text())
    if receipt['frames'] != approved or approved.get('frames_verified') is not True or receipt['plan_id'] != approved.get('plan_id'):
        raise ValueError('取樣完成紀錄與人工確認不一致。')
    if inspect(request)[0] != receipt['post']:
        raise ValueError('取樣完成後 SLC 或描述檔已變更，禁止混用成果。')


def main():
    mode, filename = sys.argv[1:]
    request = json.loads(Path(filename).read_text())
    if mode == 'read': read(request)
    elif mode == 'plan':
        plan, _ = inspect(request)
        write_json('helper_sampling_plan.json', plan)
        print(f"{plan['changed']} 個 SLC 需重取樣；等待人工影格檢查。", flush=True)
    elif mode == 'apply': apply(request)
    elif mode == 'verify': verify(request)
    else: raise ValueError('未知取樣操作。')


if __name__ == '__main__':
    main()
