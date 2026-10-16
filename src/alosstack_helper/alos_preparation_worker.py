"""Bounded ALOS inventory, serial conversion and source-bound coverage checks.

Runs only in the selected science environment. No source script is discovered or
executed; conversion uses the separately verified JAXA distribution.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
import traceback

try:
    from .alos4_conversion import product_files, inspect_img, verify_tool, run_conversion
    from .alos_coverage import read_product, inspect_coverage, MAX_PRODUCTS, MAX_SELECTION
    from .alos4_quality import verify_conversion
except ImportError:
    from alos4_conversion import product_files, inspect_img, verify_tool, run_conversion
    from alos_coverage import read_product, inspect_coverage, MAX_PRODUCTS, MAX_SELECTION
    from alos4_quality import verify_conversion


def plain_path(path):
    """Keep user symlink evidence; normalize only verified macOS system aliases."""
    p = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    if sys.platform == 'darwin' and len(p.parts) > 1 and p.parts[1] in ('var', 'tmp'):
        alias = Path('/') / p.parts[1]
        target = Path('/private') / p.parts[1]
        # macOS tempfile paths normally start with /var. These two root-owned
        # OS aliases are not user-selected directory links. Do not resolve the
        # remainder: a link below /var or /tmp must still fail the checks below.
        try:
            link = alias.lstat()
            private = Path('/private').lstat()
            destination = target.lstat()
            trusted = (stat.S_ISLNK(link.st_mode) and link.st_uid == 0
                       and os.readlink(alias) in (str(target), str(target).lstrip('/'))
                       and stat.S_ISDIR(private.st_mode) and private.st_uid == 0
                       and stat.S_ISDIR(destination.st_mode) and destination.st_uid == 0)
            if trusted:
                p = target.joinpath(*p.parts[2:])
        except OSError:
            pass  # An unverified/missing alias gets no exception to link checks.
    for part in (p, *p.parents):
        if part.is_symlink():
            raise ValueError('資料路徑不接受符號連結：' + str(part))
    return p


def stat_identity(info):
    return dict(size=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns,
                device=info.st_dev, inode=info.st_ino, mode=info.st_mode)


def directory_identity(path):
    info = plain_path(path).lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('不是一般資料夾：' + str(path))
    return dict(device=info.st_dev, inode=info.st_ino)


def write_json(path, value):
    p = plain_path(path)
    directory_identity(p.parent)
    # Unique exclusive creation allows a retry after an interrupted write.
    parent = directory_identity(p.parent)
    data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)
    with tempfile.NamedTemporaryFile(mode='w', dir=p.parent, prefix='.' + p.name + '-', delete=False) as stream:
        tmp = Path(stream.name)
        stream.write(data)
    try:
        if directory_identity(p.parent) != parent:
            raise ValueError('寫入紀錄時磁碟位置已改變。')
        plain_path(p)
        tmp.replace(p)
    finally:
        if tmp.exists() and directory_identity(p.parent) == parent:
            tmp.unlink()


def file_stamp(path):
    p = plain_path(path)
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('來源不是一般檔案或含符號連結：' + str(p))
    if s.st_size <= 0:
        raise ValueError('來源檔案為空：' + p.name)
    is_image = p.name.startswith('IMG-')
    if not is_image and s.st_size > 32 * 1024**2:
        raise ValueError('中繼資料超過檢查上限：' + p.name)
    h = hashlib.sha256()
    descriptor = os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        if stat_identity(os.fstat(stream.fileno())) != stat_identity(s):
            raise ValueError('來源在開啟前變更：' + p.name)
        if is_image:
            h.update(stream.read(1520))
        else:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
        if stat_identity(os.fstat(stream.fileno())) != stat_identity(s):
            raise ValueError('來源在讀取期間變更：' + p.name)
    after = plain_path(p).lstat()
    if stat_identity(s) != stat_identity(after):
        raise ValueError('來源正在變動，請複製完成後重新掃描：' + p.name)
    return dict(path=str(p), **stat_identity(s), sha256=h.hexdigest(),
                hash_scope='first_1520_bytes' if is_image else 'full_file')


def manifest_paths(leader):
    p = plain_path(leader)
    family = p.name[4:]
    paths = [p]
    paths += sorted(p.parent.glob('IMG-??-' + family))
    paths += [p.parent / (prefix + family) for prefix in ('VOL-', 'TRL-') if (p.parent / (prefix + family)).exists()]
    if (p.parent / 'conversion.json').exists():
        paths.append(p.parent / 'conversion.json')
    return paths


def product_manifest(leader):
    return [file_stamp(f) for f in manifest_paths(leader)]


def verify_manifest(product):
    manifest = product.get('manifest', [])
    if not manifest or product_manifest(product['path']) != manifest:
        raise ValueError('產品來源已變更或磁碟未連線，請重新掃描：' + product.get('name', product['path']))


def verify_manifest_identity(product):
    """Cheap current-identity check for a previously scanned coverage review."""
    saved = product.get('manifest', [])
    paths = manifest_paths(product['path'])
    if not saved or [str(p) for p in paths] != [p.get('path') for p in saved]:
        raise ValueError('來源產品檔案清單已變更。')
    for path, original in zip(paths, saved):
        current = stat_identity(plain_path(path).lstat())
        if any(original.get(k) != v for k, v in current.items()):
            raise ValueError('來源產品在覆蓋確認後已變更：' + path.name)


def verify_quality(quality):
    """Require a complete audit, not merely a successful converter return code."""
    if not isinstance(quality, dict) or quality.get('status') != 'passed':
        raise ValueError('轉換後品質檢查未通過，不能標記完成。')
    phases = ('format', 'samples', 'metadata', 'reader', 'input_verification')
    if any(quality.get(k, {}).get('status') != 'passed' for k in phases):
        raise ValueError('品質報告缺少格式、完整樣本、讀取或來源穩定性驗證。')
    if quality['samples'].get('all_samples_equal') is not True:
        raise ValueError('品質報告未確認全部複數樣本一致。')
    if quality.get('scientific_validation', {}).get('status') != 'not_performed':
        raise ValueError('此流程未驗證干涉科學品質，不能使用不同驗證範圍的報告。')


def verify_completed_item(item):
    """Recheck identity of the exact files audited with full hashes, without rehashing GBs."""
    quality = item.get('quality')
    verify_quality(quality)
    if plain_path(item['output']) != plain_path(quality['output']):
        raise ValueError('完成項目的輸出位置與品質報告不一致。')
    for side in ('source', 'output'):
        root = plain_path(quality[side])
        identity = directory_identity(root)
        if item.get('verified_directories', {}).get(side) != identity:
            raise ValueError('通過驗證的來源／輸出資料夾已替換或未連線。')
        rows = quality.get('fingerprints', {}).get(side)
        if not isinstance(rows, list) or not rows:
            raise ValueError('品質報告缺少完整檔案指紋。')
        names = [v.get('name') for v in rows]
        if any(not isinstance(n, str) or Path(n).name != n or n in ('.', '..') for n in names) or len(set(names)) != len(names):
            raise ValueError('品質報告含無效的檔案名稱。')
        current_names = {p.name for p in root.iterdir() if p.name.startswith(('VOL-', 'LED-', 'TRL-', 'IMG-'))}
        if set(names) != current_names:
            raise ValueError('通過驗證後的產品檔案清單已變更。')
        for saved in rows:
            if not re.fullmatch(r'[0-9a-f]{64}', saved.get('sha256', '')):
                raise ValueError('品質報告缺少完整 SHA256。')
            now = stat_identity(plain_path(root / saved['name']).lstat())
            if any(saved.get(k) != v for k, v in now.items()) or not stat.S_ISREG(now['mode']):
                raise ValueError('通過驗證後的來源或輸出已變更：' + saved['name'])
    saved_sidecar = item.get('conversion_manifest')
    if not saved_sidecar or file_stamp(Path(quality['output']) / 'conversion.json') != saved_sidecar:
        raise ValueError('轉換來源說明已變更或缺失，請重新驗證。')
    return dict(status='passed', method='stat identity matches files previously audited with full SHA256; no new full hash', checked=time.time())


def refresh_completed_items(items):
    for item in items:
        item['processing_unlocked'] = False
        if item.get('status') != 'complete':
            continue
        try:
            item['current_verification'] = verify_completed_item(item)
        except (OSError, ValueError, KeyError, TypeError) as error:
            item.update(status='failed', error='先前通過的產品現已失效：' + str(error),
                        current_verification=dict(status='failed', checked=time.time()))
    return items


def scan(source, progress=None):
    root = plain_path(source)
    if not root.is_dir():
        raise ValueError('來源資料夾不存在，請確認外接磁碟已連線。')
    source_identity = directory_identity(root)
    products, warnings, leaders = [], [], []
    visited = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not Path(folder, d).is_symlink())
        visited += len(dirs) + len(files)
        if visited > 12000:
            raise ValueError('資料夾內容超過掃描上限，請選擇較小的影像資料夾。')
        if len(Path(folder).relative_to(root).parts) >= 5:
            if dirs:
                warnings.append('已達五層掃描上限：' + str(folder))
            dirs[:] = []
        for name in sorted(files):
            if name.startswith(('LED-ALOS2', 'LED-ALOS4')):
                leaders.append(Path(folder, name))
                if len(leaders) > MAX_PRODUCTS:
                    raise ValueError(f'一次最多掃描 {MAX_PRODUCTS} 份產品，請分批選取。')
    if not leaders:
        raise ValueError('沒有找到 ALOS-2／ALOS-4 LED；請先解壓產品並選擇包含影像的資料夾。')
    for index, leader in enumerate(leaders):
        if progress:
            progress(dict(stage='讀取產品中繼資料', completed=index, total=len(leaders), current_product=leader.name, items=[]))
        key = hashlib.sha256(str(leader).encode()).hexdigest()[:24]
        row = dict(id=key, name=leader.parent.name + ' · ' + leader.name, path=str(leader),
                   sensor='ALOS-4' if leader.name.startswith('LED-ALOS4') else 'ALOS-2',
                   date=None, polarizations=[], metadata={}, footprint=None, bounds=None,
                   footprint_status='unknown', warnings=[], errors=[], convertible=False,
                   output_bytes=0, reason='', manifest=[])
        try:
            before = product_manifest(leader)
            row.update(read_product(leader))
            row.update(id=key, name=leader.parent.name + ' · ' + leader.name)
            if product_manifest(leader) != before:
                raise ValueError('來源在掃描中變更，請重新掃描。')
            row['manifest'] = before
            if row.get('errors'):
                raise ValueError('產品中繼資料檢查未通過：' + '；'.join(map(str, row['errors'])))
            if leader.name.startswith('LED-ALOS4'):
                files = product_files(leader.parent)
                images = [inspect_img(p) for p in files[3:]]
                if len({(i['lines'], i['pixels']) for i in images}) != 1:
                    raise ValueError('同一產品不同極化的 IMG 尺寸不一致。')
                row.update(convertible=True, output_bytes=sum(i['output_bytes'] for i in images), image_info=images)
            else:
                row['reason'] = '已是 PALSAR-2 CEOS 格式；保留於地圖確認，不需 P3toP2 轉換。'
        except Exception as error:
            row.update(convertible=False, reason=str(error))
            row.setdefault('errors', []).append(str(error))
        products.append(row)
    products.sort(key=lambda p: (p.get('date') or '', p['path']))
    if directory_identity(root) != source_identity:
        raise ValueError('來源磁碟在掃描期間被替換或中斷，請重新掃描。')
    if progress:
        progress(dict(stage='掃描完成', completed=len(products), total=len(products), current_product='', items=[]))
    return dict(products=products, warnings=warnings, source=str(root),
                total_bytes=sum(p['output_bytes'] for p in products if p['convertible']),
                convertible_count=sum(p['convertible'] for p in products),
                scope='資料準備；不代表跨衛星可干涉。IMG 掃描僅檢查 header 與檔案資訊；完整樣本驗證在轉換後執行。',
                processing_unlocked=False)


def selected_products(report, ids, convertible=False):
    if not isinstance(ids, list) or not ids or len(ids) > MAX_PRODUCTS or any(not isinstance(k, str) for k in ids) or len(ids) != len(set(ids)):
        raise ValueError('請選擇至少一份不同的產品。')
    rows = {p['id']: p for p in report['products']}
    if set(ids) - rows.keys():
        raise ValueError('選取項目不屬於這份掃描。')
    result = [rows[k] for k in ids]
    if convertible and any(not p.get('convertible') for p in result):
        raise ValueError('選取項目含不支援轉換或檢查失敗的產品。')
    return result


def run_batch(request, notify):
    import shutil
    c = request['config']
    selected = selected_products(request['report'], request['selected'], convertible=True)
    verify_tool(c['tool'])
    for p in selected:
        verify_manifest(p)
    root = plain_path(c['root'])
    if root.exists() or not root.parent.is_dir():
        raise ValueError('輸出位置已存在或上層磁碟無法讀取，請另選全新的資料夾。')
    for value in ('source', 'tool', 'environment'):
        if c.get(value):
            other = plain_path(c[value])
            if root == other or root in other.parents or other in root.parents:
                raise ValueError('輸出與來源、工具或環境不可互相包含。')
    if str(root) in request.get('attempted_roots', []):
        raise ValueError('不能重用前次輸出位置，請另選全新資料夾。')
    parent_identity = directory_identity(root.parent)
    if c.get('output_parent_identity') and c['output_parent_identity'] != parent_identity:
        raise ValueError('輸出上層磁碟在開始前已被替換。')
    need = sum(p['output_bytes'] for p in selected) + 1024**3
    if shutil.disk_usage(root.parent).free < need:
        raise ValueError('可用空間不足；所選產品的估計輸出加 1 GiB 緩衝超過磁碟空間。')
    if directory_identity(root.parent) != parent_identity:
        raise ValueError('輸出上層磁碟在建立前已被替換。')
    root.mkdir()
    identity = directory_identity(root)
    def check_root():
        if directory_identity(root) != identity or directory_identity(root.parent) != parent_identity:
            raise ValueError('輸出磁碟已中斷或位置被替換，已停止；不會建立替代目錄。')
    items = {p['id']: dict(p) for p in request.get('previous_items', [])}
    refresh_completed_items(list(items.values()))
    for p in selected:
        items[p['id']] = dict(id=p['id'], status='queued', output='', error='', quality=None)
    def update(**extra):
        state = dict(items=list(items.values()), completed=sum(v['status']=='complete' for v in items.values()),
                     total=len(items), stage='依序轉換並檢查', **extra)
        notify(state)
    update(current_product='')
    for p in selected:
        check_root()
        item = items[p['id']]
        child = root / ((p.get('date') or 'unknown-date') + '_' + p['id'][:12])
        item.update(status='running', output=str(child / 'ALOS2_format'), error='')
        update(current_product=p['id'])
        try:
            verify_manifest(p)
            if shutil.disk_usage(root).free < p['output_bytes'] + 1024**3:
                raise ValueError('可用空間已不足，停止這份產品的轉換。')
            print('轉換產品：' + p['path'], flush=True)
            result = run_conversion(dict(source=str(Path(p['path']).parent), tool=c['tool'], root=str(child)))
            check_root()
            item['status'] = 'verifying'
            update(current_product=p['id'])
            def on_quality(value):
                item['verification_progress'] = value
                update(current_product=p['id'])
            expected_output = child / 'ALOS2_format'
            if plain_path(result['output']) != expected_output:
                raise ValueError('轉換工具回傳非預期的輸出位置。')
            quality = verify_conversion(Path(p['path']).parent, expected_output, progress=on_quality)
            item['quality'] = quality
            verify_quality(quality)
            if plain_path(quality['source']) != plain_path(Path(p['path']).parent):
                raise ValueError('品質報告的來源與本次選取產品不一致。')
            verify_manifest(p)
            check_root()
            item.update(verified_directories={side: directory_identity(quality[side]) for side in ('source', 'output')},
                        conversion_manifest=file_stamp(expected_output / 'conversion.json'))
            item['current_verification'] = verify_completed_item(item)
            write_json(child / 'quality.json', quality)
            item.update(status='complete', quality=quality)
        except Exception as error:
            item.update(status='failed', error=str(error), quality=getattr(error, 'report', item.get('quality')))
            print('產品未通過：' + str(error), flush=True)
        update(current_product=p['id'])
    check_root()
    # A later conversion/callback can mutate an earlier result; no stale pass.
    refresh_completed_items(list(items.values()))
    update(current_product='')
    result = dict(items=list(items.values()), output=str(root), processing_unlocked=False)
    write_json(root / 'batch-results.json', result)
    return result


def main(request):
    progress = Path(request['progress']) if request.get('progress') else None
    def notify(value):
        if progress:
            write_json(progress, dict(value, updated=time.time()))
    mode = request['mode']
    if mode == 'check':
        result = scan(request['config']['source'], notify)
        write_json(request['inventory'], result)
        return result
    if mode == 'run':
        return run_batch(request, notify)
    if mode == 'coverage':
        all_ids = list(dict.fromkeys(request['left_ids'] + request['right_ids']))
        if len(all_ids) > MAX_SELECTION:
            raise ValueError(f'兩組合計最多選取 {MAX_SELECTION} 份產品，請分批檢查。')
        products = selected_products(request['report'], all_ids)
        for product in products:
            verify_manifest(product)
        result = inspect_coverage(request['report']['products'], request['left_ids'], request['right_ids'])
        for product in products:
            verify_manifest(product)
        return result
    raise ValueError('未知的資料準備操作。')


def parent_watch(parent_fd):
    def watch():
        try:
            while os.read(parent_fd, 1):
                pass
        finally:
            os._exit(75)
    threading.Thread(target=watch, daemon=True).start()


if __name__ == '__main__':
    if '--parent-fd' in sys.argv:
        parent_watch(int(sys.argv[sys.argv.index('--parent-fd') + 1]))
    req, res = map(Path, sys.argv[1:3])
    try:
        write_json(res, main(json.loads(req.read_text())))
    except Exception as error:
        traceback.print_exc()
        write_json(res, dict(error=str(error)))
        sys.exit(1)
