# 0.6.0 beta 1 — 首次使用入口

此版把「先自行建立科學环境」改成 Apple Silicon Mac 上的圖形化安裝流程。

- 原生 Mac 啟動器：官方來源下載、SHA256 核對、私人 Miniforge、清楚的失敗／重試畫面。
- 固定 `insar-arm 0.2.0b1` 的 381 套件；15 項檢查與工作台自己的數值示範通過才啟用。
- 新手首頁、合成教學、日期／frame 清單、ZIP 匯入及 Finder 選擇器。
- 公開 Copernicus 地形／水體下載、高程基準轉換、ISCE 描述檔及來源紀錄。
- 最近準備工作的進度／成果恢復；保留原本進階表單、既有專案和原始資料。
- 包含 0.5 的 MintPy 常用／進階／全部參數介面，以及日誌請求失敗的局部錯誤處理。

支援範圍、資料準備與首次開啟方式見 [第一次使用](FIRST_USE.md)。此 Mac beta 尚未 Apple 公證；Linux 仍可從原始碼搭配既有環境啟動。Windows 原生、Intel Mac 自動安裝、另外四個 ISCE2 工作流及自動下載原始衛星資料不在此版範圍。

## 驗證方式

每次 main 更新與 pull request 都執行 [Workbench validation](https://github.com/chris20011026/sar-workbench/actions/workflows/validation.yml)：

1. 控制流程、輸入保護、JavaScript 語法與錯誤恢復測試。
2. 乾淨的 GitHub macOS ARM runner 編譯 App，以只有 macOS 工具的 PATH 執行原生 bootstrap。
3. 安裝完整固定科學環境，執行 15 項檢查和合成 MintPy 運算。
4. 使用發布包內的伺服器確認首頁、靜態資產、API 驗證、專用環境預選與重複服務保護，再執行科學格式／座標測試。

此工作流保留報告與下載包。只有成功的執行紀錄表示該次驗證通過；安裝、合成驗證與既有特定 ALOS-2 資料試跑都不代表所有衛星產品已完整驗證。

本機另有實際公開地形下載測試（臺灣小範圍，72 × 72 像素），獨立插值核對高程格網的符號與數值；以及瀏覽器的新手流程測試。私人路徑與研究資料不隨發布上傳。

## 從原始碼重建 App

需要 Apple Silicon Mac、Xcode Command Line Tools 與 Python 3.10+。一般使用者下載 App 不需要這些開發工具。

```sh
python3 tools/build_macos_app.py --output dist/macos-beta1
```

產生 `.app`、ZIP 與 `SHA256SUMS`。使用新的輸出資料夾，不覆蓋舊版；只打包程式、文件、前端函式庫和套件清單，沒有本機科學環境、帳密或衛星影像。
