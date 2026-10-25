# SAR Workbench 0.8.0 beta 7

此版新增可攜分析資料包，讓已完成的標準 alosStack 成果在新的位置或電腦建立品質檢查與 MintPy 分析。操作與範圍見[可攜分析資料包](PORTABLE_PROJECT.md)。

## 本版變更

- 預覽後續分析必要的影像與描述資料、檔案量及存放位置，確認後獨立複製並完整核對 SHA-256。
- 以資料包清單保留完整性及來源設定／歷史；移至其他位置後先驗證，再選擇當地環境建立新專案。
- 原專案及外部來源保留。新專案不執行來源腳本或 pickle，也不繼承舊運算的人工同意。
- 介面區分可攜分析、整個專案的安全搬移及引用外部影像的手動成果匯入。
- 修正 README 對 Sentinel-1 雙日期成熟度的舊說明；下載連結指向包含測試版的 Releases 清單。

## 隨此版提供的 beta 6 功能

[Beta 6](RELEASE_0.8_BETA6.md) 的視覺化流程導覽、相容性圖表、五組已知答案進階自測與 MintPy 安全恢復一併提供。最新 beta 6 commit 的 GitHub 控制流程與乾淨 ARM Mac 安裝驗證均已成功：[驗證紀錄](https://github.com/chris20011026/sar-workbench/actions/runs/40113134400)。Beta 7 仍須以自己的發布 commit 通過驗證，不能沿用舊 commit 的結果。

## 更新與下載

不需更動既有科學套件或建立額外 Conda 環境。先完成或停止背景工作，結束舊 App，再換新版 App；保留原工具與專案登記位置。原始碼 ZIP 和 Mac App 是不同項目，實際發布附件、SHA-256 及對應的驗證紀錄見 [Releases](https://github.com/chris20011026/sar-workbench/releases)。

App 需要 Apple Silicon 與 macOS 14.5 以上，仍採 ad-hoc 簽署，尚未經 Apple Developer ID 公證。其他平台維持原本支援範圍。

## 驗證邊界

本機 Apple Silicon 驗證：726 項 Python 測試全數通過（啟用原生環境與進階自測，無略過）；10 組 JavaScript 介面測試通過。可攜合成資料測試移走原來源及資料包後，仍從新專案完成 MintPy 流程並符合已知答案。另以瀏覽器驗證預覽、確認、複製、匯入及來源紀錄。這些結果不等於真實資料科學驗證。

可攜資料包只包含新的品質檢查與 MintPy 分析所需的完整輸入，不是原始資料、所有中間成果及舊檢查點的研究封存。完整雜湊不代表科學品質通過；新分析仍需人工判讀。

真實 ALOS-2／ALOS-4 影格人工確認不因更新而通過。Sentinel-1 與 NISAR 真實端到端驗證，以及尚未開放的 TOPS 多日期與通用 Stripmap 模式仍保留各自的限制。正式研究需自行檢查來源、幾何、相干性、解纏、參考基準及誤差。
