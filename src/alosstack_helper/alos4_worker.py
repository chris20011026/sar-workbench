"""Bounded PALSAR-3 conversion worker, separate from every SAR processor."""
from pathlib import Path
import hashlib
import json
import shutil
import sys
import traceback
from alos4_conversion import verify_tool, product_files, inspect_img, run_conversion


def verify(request):
    for f in request['source']:
        p=Path(f['path']);s=p.stat()
        if s.st_size!=f['size'] or s.st_mtime_ns!=f['mtime_ns']:raise ValueError('來源或工具已改變，請重新檢查。')


def main(r):
    c=r['config'];verify(r);verify_tool(c['tool']);files=product_files(c['source'])
    images=[inspect_img(p) for p in files[3:]]
    if len({(x['pixels'],x['lines']) for x in images})!=1:raise ValueError('不同極化 IMG 尺寸不一致。')
    estimate=sum(x['output_bytes'] for x in images)+1024**3
    settings=Path(r['yaml'])
    if r['mode']=='check':
        plan=dict(images=images,source_sensor='ALOS-4',mode=files[1].name[22:25],disk_estimate_bytes=estimate,free_bytes=shutil.disk_usage(Path(c['root']).parent).free,
                  warnings=['此操作只轉換格式；未驗證跨衛星配對、共同頻帶、影格與干涉品質。','來源保持原樣；輸出含轉換來源與版本紀錄。'])
        settings.write_text(json.dumps(plan,indent=2));return plan
    if r['mode']!='run':raise ValueError('未知的工作')
    if settings.is_symlink() or hashlib.sha256(settings.read_bytes()).hexdigest()!=r['config_sha256']:raise ValueError('轉換計畫已改變。')
    if shutil.disk_usage(Path(c['root']).parent).free<estimate:raise ValueError('磁碟空間不足。')
    result=run_conversion(c);verify(r)
    return result


if __name__=='__main__':
    req,res=map(Path,sys.argv[1:3])
    try:res.write_text(json.dumps(main(json.loads(req.read_text())),ensure_ascii=False))
    except Exception as e:
        traceback.print_exc();res.write_text(json.dumps({'error':str(e)},ensure_ascii=False));sys.exit(1)
