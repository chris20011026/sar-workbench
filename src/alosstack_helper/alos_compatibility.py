"""Conservative preparation report for mixed PALSAR-2/PALSAR-3 collections."""
from collections import defaultdict
from pathlib import Path
import math


def compatibility(scan):
    products = [p for p in scan['products'] if p['type'] == 'CEOS']
    checks = []
    def add(status, title, detail): checks.append(dict(status=status, title=title, detail=detail))
    grouped = defaultdict(list)
    for p in products: grouped[p.get('date') or '日期未確認'].append(p)
    dates = []
    for date, group in sorted(grouped.items()):
        rates = sorted({float(p['metadata']['range_sampling_mhz']) for p in group if p['metadata'].get('range_sampling_mhz') is not None})
        dates.append(dict(date=date, count=len(group), sensors=sorted({p['sensor'] for p in group}),
                          polarizations=sorted(set.intersection(*(set(p.get('polarizations', [])) for p in group))), rates=rates))
    if len(dates) < 2: add('warning', '日期數量', '至少需要兩個不同日期才能做干涉；單一產品可先轉換或檢視。')
    else: add('pass', '日期數量', f'辨識到 {len(dates)} 個日期。')
    if any(not p.get('date') or not p.get('images') or str(p['metadata'].get('level', '')).strip() != '1.1' for p in products):
        add('error', '資料完整性', '有產品缺少日期、IMG 或已確認的 Level 1.1，請先修正。')
    elif products: add('pass', '基本格式', 'LED 可讀且有 IMG；尚未驗證整份 IMG 的所有記錄。')
    if scan.get('errors'): add('error', '無法讀取的檔案', '；'.join(e['path'] + '：' + e['message'] for e in scan['errors']))
    has4 = any(p['sensor'] == 'ALOS-4' for p in products)
    if has4:
        add('warning', 'ALOS-4 格式', '先以 JAXA 高解析度 Level 1.1 轉換工具建立副本；原生 ALOS-4 CEOS 不直接送入既有 alosStack。')
    if len({d['count'] for d in dates}) > 1:
        add('error', '影格對應', '各日期影格數不同。需要確認覆蓋與跨衛星影格對應；不能把兩個影格直接當成一個影格。')
    elif len(dates) > 1:
        add('warning' if has4 else 'pass', '影格數', '各日期數量相同；仍需核對地理覆蓋，跨衛星不能只比對檔名的 frame 編號。')
    rates = {rate for d in dates for rate in d['rates'] if math.isfinite(rate)}
    if len(rates) > 1:
        add('warning', '距離取樣率', '存在不同取樣率：' + ' / '.join(f'{r:g} MHz' for r in sorted(rates)) + '。雙日期由 prep_slc 調整；多日期需在 SLC 擷取後、Ampcor 對位前做取樣率統一。')
    if any(not d['rates'] for d in dates): add('error', '取樣率', '部分產品的取樣率未讀取，不能判定是否需重取樣。')
    for c in scan.get('configs', []):
        for w in c.get('warnings', []): add('warning', Path(c['path']).name, w)
    if has4:
        add('warning', 'ISCE2 reader', '需支援非整數 MHz 取樣率的 ALOS2 reader。工作台不會自動改寫你的 Conda 套件。')
    if not products: add('error', 'ALOS 資料', '沒有可辨識的 LED。請選擇包含 ALOS 日期資料夾的目錄。')
    return dict(dates=dates, checks=checks, scan_id=scan['id'], experimental=has4,
                next='目前先開放相容性檢查與格式轉換。混合 ALOS-2／ALOS-4 的雙日期及多日期自動接續，需先驗證影格對應與取樣率處理；本版不會以忽略檢查的方式強制啟動。' if has4 else '可到「建立專案」選擇 ALOS-2 雙日期或多日期，再完成專案健檢。')
