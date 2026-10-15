# NISAR／ALOS-4 實驗流程

入口位於「建立專案」，與既有六種 ISCE2 模式共用流程選擇頁。這些是 **v0.7.0-beta.1 的實驗功能**，新版 Mac 下載包已包含介面與執行入口；ISCE3 環境仍需另備，不代表真實 NISAR 或混合衛星的科學流程已完成驗證。

| 功能 | 本版狀態 |
| --- | --- |
| NISAR RSLC 雙日期 → GUNW | 表單、資料與設定檢查、CPU 執行、停止、日誌及成果地圖入口已實作；未完成真實影像全流程驗證 |
| ALOS-4 → PALSAR-2 CEOS 相容格式 | 單產品轉換、版本核對與獨立副本已實作；小片段的 IMG 與官方工具逐位元組一致 |
| ALOS-2／ALOS-4 配對準備 | 日期、極化、取樣率、影格數與設定檔問題檢查 |
| 混合 ALOS-2／ALOS-4 雙日期與多日期自動接續 | 尚未開放；必須先驗證跨衛星影格對應、共同覆蓋／頻帶與取樣率調整 |

## NISAR

1. 選擇較早、較晚日期的兩份 RSLC HDF5、DEM，以及全新的輸出資料夾。可從「資料與成果瀏覽」的配對摘要帶入兩期影像。
2. 選擇 **獨立的 ISCE3 Conda 環境**。本版核對 `isce3-cpu 0.25.21`，需完整的 `nisar.workflows.insar` 依賴，包含 `whirlwind-insar`、SNAPHU Python bindings 與 GDAL HDF5 driver。只通過 `import isce3` 不足以判定環境完整。
3. 設定頻段 A/B、同極化 HH/VV、range/azimuth looks、電離層估計、精細對位、濾波、CPU 執行緒與輸出 EPSG／間距。
4. 按「檢查資料並產生設定」。檢查觀測日期、相對軌道、影格、升降軌、共同頻段／極化、地理外框交集與完整 DEM 覆蓋，並使用安裝版本的 `InsarRunConfig` 驗證設定。
5. 閱讀摘要與空間估計後，才可開始運算。保守空間預算是兩期輸入總大小的六倍加 10 GiB，僅用於開跑前的資源防護，不是實際峰值保證。
6. 運算成功後核對 GUNW 的相位、相干性、連通區結構，再開啟現有成果地圖。程式成功不等於科學品質合格。

設定依據為安裝版本的 [ISCE3 defaults](https://github.com/isce-framework/isce3/blob/v0.25.21/share/nisar/defaults/insar.yaml) 與 [schema](https://github.com/isce-framework/isce3/blob/v0.25.21/share/nisar/schemas/insar.yaml)。其他參數沿用該版本預設；單一 SNAPHU tile 的 overlap 使用 schema 接受的值 4（單 tile 不使用 overlap），避免該版本預設值 0 與 schema 下限不一致。

注意：

- 本版沒有雷達 SLC 裁切介面，輸出網格間距也不會減少前段完整影像處理。
- 水體選項需要 NISAR 水岸**距離編碼**，0–100 為海岸距離、101–200 為內陸水體距離。普通 0/1 mask 不可直接代入；無相容資料可留白。抽樣值域檢查不能證明其語意正確。
- DEM 應使用相容的高程基準；介面無法僅憑檔案確認正高／橢球高。地理外框覆蓋檢查也不代表每個像素都有有效 DEM。
- 電離層使用 `main_side_band`，需 A/B 都有選取的同極化；輸出相位屏需判讀。這是單一配對，沒有時間序列，也不能直接當成垂直位移。
- 本版限定 CPU、關閉自動網路下載，保留 scratch。停止或服務中斷後保留半成品，另建工作，不自動續跑或覆寫。
- ISCE3 不會安裝到 ISCE2 環境。現有一鍵安裝提供 ISCE2／MintPy；ISCE3 的可重建安裝包與全流程驗證是此實驗功能正式發布前的必要工作。

輸出目錄含 `insar.yaml`、`inputs.json`、`scratch/` 和 `GUNW.h5`。檢查、版本、原始輸入大小／時間戳及日誌儲存於工作台 state-dir 的 `nisar/<id>/`；不要把含本機路徑的工作紀錄上傳公開儲存庫。

## ALOS-4 格式轉換

從 [JAXA ALOS-4 資料頁](https://www.eorc.jaxa.jp/ALOS/en/alos-4/a4_data_e.htm) 取得 `P3toP2snap`，解壓後在介面選取工具資料夾。本儲存庫不重新散布第三方轉換腳本；執行前核對已審閱版本的 SHA-256。不同版本會停止並提示，不能用任意腳本替代。

每次選一份完整高解析度 Level 1.1 Stripmap 產品，需 VOL／LED／TRL 與對應極化的 IMG。ScanSAR、Spotlight、其他級別與不符記錄格式的資料會被拒絕。

- VOL、LED、TRL 依官方樣例在工作副本中轉換。用等價的空白字元查表替代其大量記憶體配置。
- IMG 每次讀一行 CEOS 記錄，逐位元組保留複數樣本，修改 prefix 與 descriptor；檢查行數、記錄大小、缺檔及截斷。
- 保留原始觀測方向的檔名後綴，避免官方樣例固定 R/A 後綴造成混淆。
- 輸出在新資料夾的 `ALOS2_format/`，另含 `conversion.json`，註明真正來源仍是 ALOS-4、工具版本與限制。既有檔案不覆寫。
- **這不是取樣率重採樣，也不是跨衛星對位。** 轉換後的合成 frame 編號不能代替地理對應。多日期取樣率調整需要在擷取 SLC 後、Ampcor 前處理；不同日期影格數不一致時先停止人工確認。

轉換工作紀錄位於 state-dir 的 `alos4-conversion/<id>/`。工作台在同一服務內協調 ISCE2、MintPy、NISAR 與轉換工作，避免同時啟動重型任務。

## 本次驗證邊界

- 224 項自動測試通過，包含既有 ISCE2／MintPy 測試、新輸入防護、HTTP 授權與工作互斥、服務中斷防重跑、IMG 複數樣本保留。
- 實際 RSLC 的 ISCE3 0.25.21 設定解析通過（電離層關閉／開啟兩種）；沒有啟動整幅科學運算。
- 課程 DEM 未覆蓋完整 RSLC 北端，實際檢查能攔下；磁碟空間不足也會阻止開始。
- ALOS-4 的兩個極化各取三行作小片段測試，IMG 與 JAXA 官方版本輸出相同；完整工作管理與網頁操作另以小片段驗證。這不是完整衛星產品或干涉驗證。
- 真實多日期集合能列出影格數不同、取樣率差異、失效路徑與不存在的參考日期。
