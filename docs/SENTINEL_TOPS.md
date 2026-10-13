# Sentinel-1 TOPS 雙日期（試驗）

這是 ISCE2 `topsApp.py` 的本機視覺化介面。**尚未完成兩期真實 IW SLC 的端到端驗證**，不應直接將輸出作為已驗證的研究成果。它與 ALOS、SNAP GRD 洪水流程及 MintPy 時序各自分開。

## 需要什麼

- 兩個不同日期、較早作參考的 S1A／S1B IW Level-1 SLC，原始 `.SAFE` 資料夾或 ZIP。每期一個產品，需同相對軌道、同升降軌、有共同極化與子波束重疊。
- 已安裝的相容 ISCE2 Conda 環境，可使用工作台安裝的專用環境，或自己的環境。程式以隔離子程序呼叫，不修改既有套件或 shell。
- 覆蓋所選完整 burst 的 ISCE 格式 WGS84 橢球高 DEM，同名 XML；已有 VRT 時一併核對並建立專案內描述檔。一般海拔 DEM 不能改名當橢球高。
- 精密軌道可自動從 ESA POEORB 下載到專案內，或選每期本機 EOF。衛星、有效期、狀態向量時間及有限數值會檢查，不悄悄退回影像附帶軌道。

Sentinel-1 原始資料可透過 [ASF Vertex](https://search.asf.alaska.edu/) 尋找；實際下載可能需要 Earthdata 登入。工作台目前不代管帳密、不自動下載原始 SAR。**GRD 不適用**，先前 SNAP 課堂資料不能代替 SLC。

## 操作

1. 「建立專案」→「Sentinel-1 TOPS · 雙日期」。選新空資料夾、環境與兩期影像。
2. 「讀取兩期影像資訊」核對日期與相對軌道。選共同極化與相鄰子波束；地圖點兩個對角或輸入南北西東界設定 ROI，全部留白使用完整範圍。
3. 外框來自 annotation 地理格網，僅為候選 burst。每個子波束至少需兩個候選 burst，以保留 ESD 重疊區對位。真正共同 burst 稍後由軌道幾何確認。
4. 選 DEM。沒有 DEM 可展開「還沒有 DEM？準備公開地形資料」，預覽完整選定子波束外框加 0.1° 緩衝後下載。此下載需已安裝並驗證的**工作台專用環境**；僅使用外部環境者可選已有 DEM。公開 Copernicus DSM 地形工具目前限南北緯 49°、最多 36 個圖幅，並非 SRTM DTM。
5. 選軌道來源，按需要調整 looks、濾波與 ESD 門檻，閱讀試驗限制後檢查並建立。建立不會開始科學處理。
6. 「準備 DEM 描述檔、精密軌道與設定」：寫入專案 `proc/topsApp.xml`，讓安裝版 ISCE2 實際解析，記錄版本及參數。
7. 「試驗讀取影像並確認共同 burst」：執行 startup、preprocess、computeBaselines。每個所選子波束必須有至少兩個真正共同 burst，且 DEM 能覆蓋實際幾何範圍，才開放下一階段。
8. 確認共同 burst，再執行至 geocode。檢查地圖與日誌，下載成果供獨立驗證。

底圖預設離線；只有選道路與地名時才向 OpenStreetMap 請求可見範圍圖磚，SAR 本體不會上傳。

## 可調整設定

| 設定 | 工作台初始值 | 用途與取捨 |
| --- | --- | --- |
| 極化 | 兩期共同極化 | 不同極化代表不同散射響應，不保證都適合形變分析 |
| 子波束 | 共同 IW 子波束 | 多子波束限相鄰；增加範圍也增加處理量 |
| ROI | 留白 | 用於 ISCE2 選取完整 burst 及 geocode 範圍，不是任意裁掉 SLC 後再處理 |
| Range looks | 19 | 增加可減少噪聲和輸出量，也降低距離向解析度 |
| Azimuth looks | 7 | 與 range looks 共同決定多視像元；不改變原始資訊解析度 |
| Filter strength | 0.5 | Goldstein 濾波；較強可能犧牲局部細節 |
| ESD coherence threshold | 0.85 | 高門檻排除低可信重疊區，但也可能造成估計樣本不足 |

固定啟用 ESD、虛擬 SLC、SNAPHU MCF 解纏；本版關閉電離層校正、GPU、dense offsets 和二階解纏。這些限制在介面中明示。沒有把全部 ISCE2 參數開放成無條件可用。

## 產物與限制

- `proc/`：原生 ISCE2 中間產物、XML 與各步 `PICKLE` 檢查點。
- `.helper/`：來源與設定紀錄、軌道來源、日誌、共同 burst 報告、成果報告。
- `.helper/tops-exports/`：地理編碼解纏相位（rad）、包裹相位（rad）、相干性、連通區 GeoTIFF，透明預覽及 DEM 底圖。
- 有效零相位會保留；NoData 使用 NaN。成果遮罩以連通區及有限數值為主，**不是水體／高相干性品質遮罩**。
- 未選穩定空間參考點，輸出不標為絕對位移、速度或垂直下陷。單一配對不能做 MintPy 時序；點選參考、LOS 轉換、TOPS stack 為後續工作。
- 每步驗證前一步檢查點、設定及來源紀錄。大型資料只檢查大小與修改時間，小型 metadata／設定使用雜湊；不是全部 TIFF 的完整內容校驗。
- 科學步驟失敗不直接重跑，保留半成品與日誌，診斷後另建專案。僅全部 21 步已完成、成果整理失敗時，可重做成果整理，不重跑科學處理。
- 不支援 S1C／S1D、多 slice 拼接、單 burst ESD 例外、極區與跨換日線。網路下載可能受官方站點可用性影響。

## 驗證邊界

已測試合成 SAFE／ZIP metadata、產品不符、路徑與 ZIP 防護、軌道檢查、來源變動、階段保護、API 隔離、小型原生 GDAL 匯出與有效零相位。安裝環境測試會解析真實 `topsApp` 參數並執行 startup，**不處理真實 SLC 像素**。另實際從 ESA 官方下載 2022-08-15 覆蓋的 S1A 精密軌道並通過檢查。

下一個驗收門檻是取得兩期可合法使用的 IW SLC，以獨立專案跑完，再比較原生命令列結果、共同 burst、ESD／對位、解纏連通性和地理位置。通過以前維持「試驗功能」。

上游參考：[ISCE2 topsApp](https://github.com/isce-framework/isce2/blob/main/applications/topsApp.py)、[Sentinel-1 TOPS reader](https://github.com/isce-framework/isce2/blob/main/components/isceobj/Sensor/TOPS/Sentinel1.py)、[ESA POEORB](https://step.esa.int/auxdata/orbits/Sentinel-1/POEORB/)。
