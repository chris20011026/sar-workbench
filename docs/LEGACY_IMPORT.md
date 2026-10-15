# 匯入手動完成的 alosStack 成果

專案總覽的「匯入手動 ISCE2 成果」可將以前在終端機完成的 **ALOS-2 多日期 alosStack** 結果接入工作台。這不是重跑、續跑或搬移原始 ISCE2 專案。

## 操作

1. 確認原本的 ISCE2 工作已停止，選擇 `proc`，或包含它的資料夾。
2. 選擇新工作資料夾的父位置、輸入名稱，選擇已安裝的 ISCE2／MintPy 環境。
3. 預覽日期、配對、幾何參考日期、影像尺寸、looks、極化與外部來源依賴。
4. 確認後，工作台重新檢查來源是否改變，再建立獨立專案。先做品質檢查，再建立 MintPy 分析。

預覽與匯入可停止，外接硬碟讀取由背景程序負責。服務停止會終止背景讀取。若在建立途中停止，未完成的資料夾會保留，但不會自動加入清單或覆寫後重試；可檢查其內容後另外選擇新資料夾。

## 支援範圍

必須有一個連通配對網路，所有配對與幾何影像使用相同尺寸及單一 looks 版本。日期採 `YYMMDD`。目前只接受 little-endian、標準 raw raster VRT；不接受虛擬運算或指向來源範圍外的 VRT／符號連結。

```text
proc/
  pairs/YYMMDD-YYMMDD/insar/
    filt_PAIR_Nrlks_Malks.unw
    filt_PAIR_Nrlks_Malks_msk.unw
    PAIR_Nrlks_Malks.cor
    filt_PAIR_Nrlks_Malks.unw.conncomp
  dates_resampled/REFERENCE/
    REFERENCE.track.xml
    f1_NNNN/REFERENCE.frame.xml
    insar/REFERENCE_Nrlks_Malks.{hgt,lat,lon,los,wbd}
  baseline/baseline_center.txt
```

每個 raster 都需要同名 `.xml` 與 `.vrt`。至少有一個 frame；track 需要有效軌道向量。baseline 必須涵蓋全部配對日期且使用同一個幾何參考日期。如果找到多個完整幾何日期，需在表單明確選擇參考日期。

兩個日期可匯入做品質檢查；目前 MintPy 介面至少需要三個日期。TOPS、stripmap、alos2App 雙日期、非標準檔名或只有 GeoTIFF 的成果不在這個入口的支援範圍。工作台會指出缺少的檔案或不支援的結構，不猜測不同流程的意義。

## 寫入與科學邊界

- 原始來源保持唯讀。大型 raster 建立連結，不複製完整影像。
- 新 `proc` 只包含必要連結及重新建立的描述檔。track/frame XML 以純資料解析後重建固定 ISCE 元件；不載入來源腳本、Python 模組、shelve 或 pickle，不執行來源 XML factory 設定。
- ISCE2 的原始運算階段與檢查點恢復停用；新專案的狀態是「已匯入」，不捏造 cmd1–4、電離層校正、配對排除或品質通過的歷史。
- 描述檔記錄完整 SHA-256；大型影像記錄大小、修改時間、檔案身分與三段 8 KiB 抽樣 SHA-256。這是來源變動檢查，**不是整幅影像的完整性驗證**。
- 建立每次新品質／MintPy 分析前，檢查來源及本機描述資料是否仍符合匯入紀錄。原硬碟未連線或來源已變更時停止；不會自動將新內容視為同一批成果。
- 工作資料夾只包含外部影像的引用，不能當成完整備份；搬移工作資料夾也不會自動納入來源影像。
- 未用真實使用者 alosStack 專案完成獨立科學驗證；測試包括合成標準目錄、來源變動／路徑防護，以及安裝環境中的 ISCE metadata → MintPy `load_data`／`modify_network`。合成載入確認原始檔案完全未變更。

## API integration

`LegacyImports(engine)` provides:

- `preview({source,root,name,environment,reference?})`: asynchronous job, `preview_id`.
- `get(preview_id)`: `running`, `ready`, `importing`, `complete`, `failed`, `stopped`, or `interrupted`; public preview excludes internal descriptor bodies/manifests.
- `create(preview_id, reviewed=True)`: explicit import after preview, returns the same job. `project_id` is present on completion.
- `stop(preview_id)` / `shutdown()` terminate the owned background process.

Routes are `/api/legacy-preview`, `/api/legacy-import?preview=...`, `/api/legacy-create`, and `/api/legacy-stop`; POST create/stop use `preview_id`. All endpoints require the normal local token and launch guard. The service reserves `engine.active` while checking/importing. UI entry point: `window.openLegacyImport()`.

The imported project has `import_only: true` and `status: imported`. `workflows.inventory` must allow this status for import-only projects. New analysis creation/inversion calls `require_legacy_source(state)`; original engine launch/recovery actions must reject import-only projects.
