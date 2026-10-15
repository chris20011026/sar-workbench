# Sentinel-1 真實影像驗證紀錄

這是 **TOPS 雙日期的驗證工具**，不是另一個處理引擎，也不會自動開始科學運算。沒有兩期相容的真實 IW SLC 時，可以準備工具、用合成資料測試介面與檔案檢查；**不能因此宣稱完成真實資料驗證**。TOPS 保持試驗標籤，`topsStack` 不會因填完表格而開放。

## 操作入口

建立或開啟 Sentinel-1 TOPS 雙日期專案後，展開 **Sentinel-1 真實影像驗證**，按「整理驗證紀錄」。建立專案表單也附有準備清單。

1. 先確認兩期影像識別、實際共同 burst、原生步驟與成果紀錄。
2. 選擇資料性質：尚未確認、合成／示範資料、真實衛星影像。真實影像需填寫產品取得來源；這是評閱者聲明，工具不會自行認證。
3. 填寫評閱者，逐項選「待檢查」、「已看過，尚未判定」、「人工判定符合」或「發現問題」。除待檢查之外，每項都需留下觀察說明。
4. 在另一個資料夾使用原生 `topsApp.py` 獨立處理，填寫版本、輸入／設定如何核對、網格／相位參考／遮罩及比較結果。**工作台不會執行填入的文字，不會自動比對像素。**複製工作台輸出不屬於獨立執行。
5. 儲存草稿或完成評閱。下載自含 HTML（可在瀏覽器列印）或完整 JSON，保留可追溯紀錄。

## 自動整理什麼

| 證據 | 實際檢查範圍 | 不代表什麼 |
| --- | --- | --- |
| 影像識別 | 建立專案時保存的兩期產品名稱、日期、軌道、升降軌、來源位置 | 不重新解壓 SAFE、不認證真實像素、不重新校驗所有來源內容 |
| 原生步驟 | 完成紀錄需符合 21 步順序，各步 `PICKLE/<step>` 與 XML 檔案非空 | 不反序列化 pickle；檔案存在不等於科學品質合格 |
| 共同 burst | 工作台由原生 `computeBaselines` 整理的 JSON，每個選定子波束至少兩個共同 burst | annotation 外框不是正式共同 burst；不單憑 burst 數判斷對位正確 |
| ESD／距離向統計 | 只讀明確標示 `runESD` 或 `runRangeCoreg` 的完整原生日誌段落；有限中位數、平均值、標準差及非零樣本數 | 偏移值是估計修正量，**不是修正後殘差或精度**；不套用通用科學門檻 |
| 地理編碼成果 | 四個原生成果檔案非空；安全地解析 VRT 的尺寸、座標系與地理轉換，核對是否一致 | 不透過 VRT 開啟來源；不保證每個像素、地理位置或解纏正確 |
| 有效像素 | 先前成果整理紀錄中的有效像素數 | 不重新掃描所有像素；有效像素不等於陸地或高品質像素 |

為避免讀取巨大日誌時拖慢工作台，目前最多讀取 `.helper/run.log` 最後 **1 MiB**。若 ESD 位於更早部分，就明示缺少自動統計；需要查閱原生日誌，不以最終 XML 猜測，也不把缺值當成零。

這項區分尤其重要：目前安裝版上游 `runESD.py` 與 `runRangeCoreg.py` 都把部分數值放在名為 `esd` 的 catalogue 中。最後的合併 catalogue 可能覆蓋欄位，不能一律把其 `Median` 當成 ESD 數值。工具使用完整、具標題的日誌段落分開識別；上游格式改變時保持缺值。

## 人工要檢查什麼

- **輸入／軌道／DEM／設定**：兩期產品是否相容，實際解析的設定是否符合研究目的。
- **共同 burst**：涵蓋範圍與處理區，DEM 是否含完整幾何範圍。
- **ESD 與精對位**：樣本分布、接縫、重採樣後重疊及獨立處理結果。不能以修正量小就宣告精度高。
- **相干性**：有效覆蓋與低相干區的位置；不以全圖平均代替地物判讀。
- **解纏**：連通區、可能的整圈跳變、低可信區及基準的一致性。
- **地理編碼**：對照獨立地理資訊；網格一致僅是比較的必要格式條件。
- **獨立結果**：在可比較的網格、相位參考、共同有效遮罩下記錄差異、單位、樣本數及接受依據。此版由評閱者提供比較證據，不宣稱自動計算數值差。

原生相位成果單位為 **rad**。尚未建立適當參考、誤差處理及觀測幾何假設時，不應直接稱為絕對位移、速度或垂直下陷。

## 儲存與過期

評閱與匯出都放在工作台本機資料目錄：

```
<workbench home>/research/tops-validation/<project id>/review.json
<workbench home>/research/tops-validation/<project id>/validation.json
<workbench home>/research/tops-validation/<project id>/validation.html
```

不寫回科學專案的 `.helper`、ISCE2 產物或原始影像，不修改 Conda 環境。專案搬移後仍從工作台登記的目前位置讀取；這不會解除原科學流程的搬移後續跑限制。

儲存時會綁定這次檢查的證據指紋。小型 JSON／XML、日誌讀取範圍及檔案大小／修改時間等檢查紀錄改變後，舊觀察保留但標示「需重新檢查」。**不是整個大型專案的密碼學完整驗證**：不雜湊全部 SAR、檢查點或原生二進位成果內容，也不持續監看外部原始來源。

所有項目人工判定符合、聲明使用真實資料且必要紀錄完整時，只表示「已保存本專案的人工真實影像評閱」。這不是維護者獨立驗證、科學認證或對所有資料通用的保證。JSON 永遠保留 `workflow_experimental: true` 與 `tops_stack_enabled: false`。

## 測試邊界與上游參考

自動測試使用**合成**影像識別、階段檔、VRT、日誌及評閱文字，驗證 ESD／距離向統計不混淆、缺證據不冒充通過、過期檢查、人工草稿、HTML 跳脫、路徑／符號連結防護與匯出。沒有真實 Sentinel-1 SLC 的端到端科學驗證。

判讀說明依據已安裝 ISCE2 的相應實作；上游新版可能不同：

- [TOPS processing steps](https://github.com/isce-framework/isce2/blob/main/applications/topsApp.py)
- [Common burst and baseline computation](https://github.com/isce-framework/isce2/blob/main/components/isceobj/TopsProc/runComputeBaseline.py)
- [ESD estimate and reported statistics](https://github.com/isce-framework/isce2/blob/main/components/isceobj/TopsProc/runESD.py)
- [Range correction estimate and reported statistics](https://github.com/isce-framework/isce2/blob/main/components/isceobj/TopsProc/runRangeCoreg.py)

相關流程條件見 [Sentinel-1 TOPS 雙日期](SENTINEL_TOPS.md)。
