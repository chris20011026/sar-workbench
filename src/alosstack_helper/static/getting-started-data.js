/* User-facing preparation guidance. Actions are resolved by the guide UI. */
window.SAR_STARTER_GUIDE = {
  title: '從零開始，準備第一個 SAR 專案',
  intro: '先選你手上的資料，再照清單準備。沒有衛星資料也可以先跑教學範例；不用先學會編輯 XML 或 cmd 指令。',
  toolSteps: [
    {title: '第一次開啟', detail: '目前一鍵安裝適用 Apple Silicon Mac，需 macOS 14.5 或更新版本。從 GitHub Releases 下載 macOS-arm64 應用程式 ZIP，解壓後開啟「SAR 研究工作台」；Source code ZIP 不是新手安裝包。'},
    {title: '準備運算工具', detail: '在「工具與環境」按「安裝並驗證運算工具」。目前一鍵環境包含 ISCE2、MintPy、SNAPHU；已有可用環境也可以選用。安裝成功後先看驗證結果。NISAR 所需的 ISCE3 要另外準備。', action: 'tools', label: '查看工具與環境'},
    {title: '還沒有資料，先熟悉操作', detail: '工作台專用工具環境準備好後，在「先跑一個小範例」執行教學範例。它以合成資料跑 MintPy，適合熟悉速度圖與位移曲線；不需要下載 SAR，也不代表真實影像前處理已驗證。只選外部既有環境不會開啟這項範例。', action: 'demo', label: '開啟教學範例'},
    {title: '安排空間與磁碟', detail: '工具與下載快取先預留至少 12 GB；真實影像與中間產物另外計算，常達數十至數百 GB。處理時保持供電，影像、地形與工作磁碟保持連接。實際處理來源、環境、DEM／WBD、專案路徑使用英文字母、數字、底線、連字號或句點，避免空格與中文；專案顯示名稱可以用中文。'}
  ],
  folderPlan: {
    title: '原始產品、地形資料、工作成果分開放',
    tree: 'SAR_workspace/\n├── raw/                 ← 完整原始產品；內容依下方流程卡片\n│   ├── product_date1/\n│   └── product_date2/\n├── terrain/             ← 該流程適用的地形與描述檔\n└── projects/            ← 選這裡當工作位置\n    └── my_first_project/ ← 建立專案時由工作台建立',
    notes: [
      '這是整理範例，不是必須照抄的名稱；可以放在本機、外接 SSD 或已掛載的 NAS。先建立 raw、terrain、projects 這些父資料夾即可。',
      '不用預先建立 proc、寫 alosStack.xml，或準備 cmd1–4。工作台會在新專案內產生設定、指令與處理資料夾。',
      '原始下載產品保持完整，不要把所有產品攤平到同一層，也不要因為 summary.txt 同名就覆蓋其中一份。',
      '來源與專案不能互相包含。例如 /Volumes/SAR_SSD/sar-data 與 /Volumes/SAR_SSD/sar-projects 可放在同層；不要把專案建在來源裡。磁碟名稱本身也要避免空格與中文。',
      'ALOS-2 資料匯入器會整理它需要的 LED／IMG。已解壓來源預設建立連結，並不是備份；原磁碟仍需連線。ZIP 中的影像則解壓到工作台管理的 imports。',
      '匯入器可以先掃描中文或含空格的原始資料位置，不必改原產品檔名；實際進入處理的來源資料夾、環境、地形與工作位置仍需通過路徑檢查。',
      '外接磁碟拔除時不要刪除工作台中的專案。接回後重新檢查；位置改變可用「重新連結」。需要移動資料時使用「安全搬移」，不要在運算中拖曳資料夾。'
    ]
  },
  workflows: [
    {
      id: 'alosStack', name: 'ALOS-2 多日期 → MintPy', status: 'implemented',
      statusDetail: '目前主要新手路線；以 Level 1.1 Stripmap 為起點。',
      summary: '同一地區多個日期的 SLC，先做 ISCE2 干涉處理，再用 MintPy 建立時間序列。第一次建議使用課程或研究室已確認相容的一批影像。',
      requirements: [
        {title: 'ALOS-2 Level 1.1 複數影像', detail: '準備完整 CEOS 產品，內含彼此對應的 LED-… 與 IMG-極化-…；保留原始 ZIP 或各產品資料夾。Level 1.5／2.1、瀏覽圖、全球鑲嵌圖與一般 GeoTIFF 不能代替這些輸入。', required: true},
        {title: '至少 2 個相容日期；MintPy 至少 3 個', detail: 'ISCE2 多日期專案可由 2 期開始；工作台 MintPy 時序入口至少需要 3 期。日期數只是最低條件，還需足夠的相干性、連通配對網路與適合研究問題的時間跨度。', required: true},
        {title: '相同觀測組合與共同覆蓋', detail: '核對升降軌方向、觀測模式、視角／軌道組合、共同極化及影格；各日期需要同一組影格。相同影格號或相交外框本身不能證明可以干涉。', required: true},
        {title: 'WGS84 橢球高 DEM 與 ISCE 水體遮罩', detail: '已有資料請保留影像本體、同名 .xml 及可用的 .vrt；不是只選一個 XML。DEM 要涵蓋完整處理影像。沒有時可走「我還沒有 DEM／水體遮罩」，先核對影像外框與可下載範圍。', required: true}
      ],
      sources: [
        {label: 'JAXA：ALOS 資料取得與授權範圍', url: 'https://www.eorc.jaxa.jp/ALOS/en/dataset/alos_open_and_free_e.htm', detail: '高解析度 ALOS-2 Stripmap 並非完整公開免費下載；可使用研究室合法取得的資料，或依官方說明向服務商／研究計畫取得。公開 ScanSAR 或全球鑲嵌圖不能直接當成本指南的 Stripmap 輸入。'},
        {label: 'JAXA：PALSAR-2 產品層級與格式', url: 'https://www.eorc.jaxa.jp/ALOS/jp/alos-2/datause/a2_format_j.htm', detail: '取得資料前確認 Level 1.1 與 CEOS 格式，並保留產品附帶說明。'}
      ],
      folders: [
        {path: 'raw/各原始產品資料夾 或 ZIP', role: '你準備', detail: '同一天不同影格各留自己的產品資料夾，summary.txt 也各自保留。新手匯入器可掃描巢狀資料夾和 ZIP，不用手動混放 LED／IMG。'},
        {path: '~/sar-workbench/imports/匯入代號/YYMMDD/', role: '匯入器建立', detail: '預設管理位置，會按日期整理 LED／IMG。YYMMDD 是兩位年＋月＋日，例如 250425。已解壓檔案通常連結原檔，因此來源不能拔除；工具根目錄若自訂，此路徑也隨之改變。'},
        {path: 'terrain/region.dem.wgs84 + .xml + .vrt', role: '你提供或下載精靈建立', detail: '對位 DEM 與地理編碼 DEM 可選同一份適用檔案。兩者都需正確的高程基準與完整影像覆蓋；同資料夾保留相應描述檔。'},
        {path: 'terrain/region.wbd + .xml + .vrt', role: '你提供或下載精靈建立', detail: '這是 ISCE 水體遮罩；任意黑白 PNG 或普通水域圖不能直接代替。'},
        {path: 'projects/my_first_stack/proc/', role: '建立專案後自動產生', detail: '專案資料夾與原始資料分開。工作台在內建立 XML、cmd1–4、處理成果和判讀紀錄；不要先手動建一套 proc。'}
      ],
      checklist: [
        {id: 'products', label: '我保留完整原始產品，已確認是 ALOS-2 Level 1.1 Stripmap。'},
        {id: 'dates', label: '我有至少 2 個日期；若要進 MintPy，至少 3 個日期。'},
        {id: 'compatible', label: '我會在匯入清單核對日期、極化、影格、觀測組合與缺檔。'},
        {id: 'terrain', label: '我有配套 DEM／WBD，或會使用地形下載精靈並核對完整影像範圍。'},
        {id: 'storage', label: '來源磁碟已連線，另有新的工作位置與足夠空間。'}
      ],
      next: [{action: 'alos-import', label: '準備我的 ALOS-2 資料'}, {action: 'identify', label: '先辨識手上的資料'}],
      notes: [
        '接著依序按「檢查這批資料」→核對清單→「確認清單，整理到工作台」→「帶入設定，確認專案」。核對運算環境、參考日期、極化、一般配對數與電離層設定；辨識時所選環境不一定就是專案最後帶入的環境。',
        '只有跳過匯入器、直接使用進階表單時，資料來源才要已整理成 source/YYMMDD/LED-…、IMG-… 的日期結構。原始產品另行保留，不要靠手動覆蓋同名檔整理。',
        '「確認設定，準備資料與指令」會處理專案內的設定及地形描述路徑；再跑到人工檢查點。若啟用電離層估計／校正，會有對應判讀與校正後確認；cmd4 成果驗收與 MintPy 診斷也仍要由人檢查。',
        '地形自動下載需要工作台專用工具環境已準備好；只選外部既有環境時，可以使用已有且適用的 DEM／WBD。',
        '目前地形精靈採 Copernicus GLO-30 DSM／WBM 並轉換高程基準，並非課程原有 SRTM DEM／SWBD。可下載區域有緯度與大小限制；既有完整地形資料也可以使用。',
        '工作台會複製並調整專案內的地形描述檔，但地形影像仍可能連結原本位置。來源影像與 DEM／WBD 磁碟都要保持連線；勾選複製也不代表所有連結已變成離線實體副本。'
      ]
    },
    {
      id: 'alos2App', name: 'ALOS-2 雙日期', status: 'trial', statusDetail: '同模式 Level 1.1 Stripmap 的試用流程。',
      summary: '比較兩個日期。使用 ISCE2 alos2App；一組干涉對不等於 MintPy 多日期時間序列。',
      requirements: [
        {title: '2 期 ALOS-2 Level 1.1 Stripmap', detail: '兩期需同觀測模式、方向、共同極化與影格，且地面範圍重疊；較早一期為 reference，較晚一期為 secondary。保留每份完整產品。', required: true},
        {title: 'DEM 與水體遮罩', detail: '與 ALOS-2 多日期使用相同類型的 WGS84 橢球高 DEM／ISCE WBD 及描述檔，涵蓋完整處理影像。', required: true}
      ],
      sources: [{label: 'JAXA：ALOS 資料取得說明', url: 'https://www.eorc.jaxa.jp/ALOS/en/dataset/alos_open_and_free_e.htm', detail: '先確認取得的是支援的高解析度 Level 1.1 Stripmap 產品。'}],
      folders: [
        {path: 'raw/兩期完整產品', role: '你準備', detail: '可以保留原始 ZIP 或產品資料夾，使用 ALOS-2 匯入器選擇「雙日期」。'},
        {path: 'terrain/DEM 與 WBD', role: '你提供或精靈建立', detail: '影像本體與同名描述檔放在一起。'},
        {path: 'projects/alos2_pair/', role: '工作台建立', detail: '選新的工作位置，再選日期、共同影格與極化。輸出和原始產品分離。'}
      ],
      checklist: [
        {id: 'pair', label: '兩期都是同模式 ALOS-2 Level 1.1 Stripmap，且有共同極化與影格。'},
        {id: 'terrain', label: 'DEM／WBD 與描述檔齊全，來源與輸出位置分開。'}
      ],
      next: [{action: 'alos-import', label: '匯入資料並選擇雙日期'}],
      notes: ['匯入後在流程選擇「ALOS-2 · 雙日期」。不能把 ALOS-4 原始產品直接當 ALOS-2 使用；跨衛星準備另有入口。', '單一雙日期成果應先判讀相干性、解纏與參考區；不能直接當作長期平均速率。']
    },
    {
      id: 'topsApp', name: 'Sentinel-1 TOPS 雙日期', status: 'experimental', statusDetail: 'S1A／S1B IW SLC；仍待真實影像端到端驗證。',
      summary: '在 ASF 選兩期 Sentinel-1 IW SLC。課堂洪水分析用的 GRD 不適用這個干涉流程。',
      requirements: [
        {title: '2 期 S1A 或 S1B 的 IW Level-1 SLC', detail: '每期一個完整原始 ZIP 或 .SAFE 資料夾；同相對軌道、同升降軌方向、共同極化並有覆蓋重疊。不能只留 measurement 內的 TIFF。', required: true},
        {title: 'WGS84 橢球高 DEM', detail: 'ISCE DEM 本體、同名 .xml 及可用的 .vrt；需涵蓋兩期完整所選子波束的候選 bursts 範圍，不只重疊交集。可使用工作台地形精靈。', required: true},
        {title: '兩期精密軌道與足夠共同 bursts', detail: '介面可選自動取得 ESA POEORB，或提供本機 EOF。讀取影像資訊後選共同極化與子波束，檢查幾何階段後仍需人工確認共同 bursts。', required: true}
      ],
      sources: [{label: 'ASF Vertex：搜尋 Sentinel-1 SLC', url: 'https://search.asf.alaska.edu/', detail: '選 Sentinel-1、IW、SLC，查同相對軌道及方向的重疊兩期。先註冊／登入免費 Earthdata 帳戶再下載；工作台不代管帳密或下載原始 SAR。'}],
      folders: [
        {path: 'raw/S1A_IW_SLC__…日期1….zip', role: '你下載', detail: '保留原始檔名與完整產品，也可選擇解壓後完整 .SAFE。'},
        {path: 'raw/S1A_IW_SLC__…日期2….zip', role: '你下載', detail: '必須是不同日期的相容產品；檔名中的 GRDH／GRDM 不是 SLC。'},
        {path: 'terrain/region.dem.wgs84 + 描述檔', role: '你提供或精靈建立', detail: '不要把輸出裁切範圍當作 DEM 只需覆蓋的範圍。'},
        {path: 'projects/sentinel_pair/', role: '工作台建立', detail: '「建立專案」選「Sentinel-1 TOPS · 雙日期」，各自選兩期檔案；不用改成 YYMMDD 資料夾。'}
      ],
      checklist: [
        {id: 'slc', label: '我下載的是 S1A／S1B IW SLC，不是 GRD。'},
        {id: 'match', label: '兩期相對軌道、方向一致，極化與覆蓋範圍有交集。'},
        {id: 'ancillary', label: '我已準備 DEM，並會選擇自動軌道或兩期本機 EOF。'},
        {id: 'trial', label: '我知道這是試驗流程，仍需共同 bursts 與成果人工檢查。'}
      ],
      next: [{action: 'identify', label: '辨識 SLC 並選擇 TOPS 雙日期'}],
      notes: ['目前不支援 S1C／S1D、多 slice 拼接或單 burst 特例；不要把它們當作已驗證的輸入。', '地理編碼成果包含相位、相干性等；這個入口不是 Sentinel-1 的 MintPy 多日期流程。']
    },
    {
      id: 'topsStack', name: 'Sentinel-1 TOPS 多日期', status: 'planned', statusDetail: '尚未開放執行。',
      summary: '規劃中的 Sentinel-1 多日期流程。先了解需求，暫時不要為這個尚未開放的入口下載大量資料。',
      requirements: [{title: '相容的多期 IW SLC', detail: '預期需要同軌道、共同子波束／bursts、極化與覆蓋範圍的 SLC，不能用 GRD。正式支援條件將以開放時的文件為準。', required: true}],
      sources: [{label: 'ASF Vertex', url: 'https://search.asf.alaska.edu/', detail: '可查資料，但本工作台尚不能由此模式啟動多日期處理。'}],
      folders: [{path: 'raw/完整原始產品', role: '先保留原檔', detail: '不需為尚未開放的工作流程預建 proc 或自製 cmd。'}],
      checklist: [{id: 'planned', label: '我知道 TOPS 多日期目前不能在工作台啟動。'}], next: [],
      notes: ['六個模式出現在選單中，不代表六個都已實作。現有 TOPS 雙日期試驗入口不會自動變成多日期處理。']
    },
    {
      id: 'stripmapApp', name: '通用 Stripmap 雙日期', status: 'planned', statusDetail: '尚未開放執行。',
      summary: 'ISCE2 的通用條帶雙日期處理器仍在規劃中；不能只因產品標有 Stripmap 就使用這個入口。',
      requirements: [{title: '特定感測器與格式的相容 SLC', detail: '需要依感測器讀取器確認產品層級、軌道、極化、DEM 及格式。正式支援的感測器與資料整理方式尚未定案。', required: true}],
      sources: [{label: 'ISCE2 官方範例', url: 'https://github.com/isce-framework/isce2/tree/main/examples/input_files', detail: '用於了解上游處理器；不表示工作台已支援所有範例。'}],
      folders: [{path: 'raw/完整原始產品', role: '先保留原檔', detail: '等工作台公布支援格式後再依對應指南建立專案。'}],
      checklist: [{id: 'planned', label: '我知道通用 Stripmap 雙日期目前不能在工作台啟動。'}], next: [],
      notes: ['已有的「ALOS-2 · 雙日期」是獨立的 alos2App 路線；若資料符合其條件，可改用該入口。']
    },
    {
      id: 'stripmapStack', name: '通用 Stripmap 多日期', status: 'planned', statusDetail: '尚未開放執行。',
      summary: '通用條帶多日期工作流程仍在規劃中。不要把它與已實作的 ALOS-2 alosStack 混為一談。',
      requirements: [{title: '特定感測器的相容多期 SLC', detail: '各感測器對產品格式、軌道與地形資料有不同要求；正式支援範圍將在開放時公布。', required: true}],
      sources: [{label: 'ISCE2 官方 Stack 說明', url: 'https://github.com/isce-framework/isce2/tree/main/contrib/stack', detail: '上游工具的能力不等同目前工作台的介面支援。'}],
      folders: [{path: 'raw/完整原始產品', role: '先保留原檔', detail: '尚不需手動產生此模式的設定檔或處理目錄。'}],
      checklist: [{id: 'planned', label: '我知道通用 Stripmap 多日期目前不能在工作台啟動。'}], next: [],
      notes: ['如果手上是符合條件的 ALOS-2 Level 1.1 Stripmap，可使用本指南的 ALOS-2 多日期入口。']
    },
    {
      id: 'alos4', name: 'ALOS-4 格式準備／ALOS-2 與 ALOS-4', status: 'preparation', statusDetail: '格式準備可用；跨衛星影格與科學品質仍需人工確認。',
      summary: '先把支援的 ALOS-4 Level 1.1 產品轉成 PALSAR-2 相容的 CEOS 格式，再決定如何與 ALOS-2 配對。格式轉換不是影像對位。',
      requirements: [
        {title: '完整且已解壓的 ALOS-4 高解析度 Level 1.1 Stripmap', detail: '保留 VOL、LED、TRL 與 IMG；此批次準備入口讀已解壓的產品資料夾，不是 ALOS-2 的 ZIP 匯入器。ScanSAR、Spotlight 或其他層級不在此入口支援範圍。', required: true},
        {title: '官方 P3toP2snap 轉換工具', detail: '由 JAXA ALOS-4 資料頁取得，解壓後在介面選工具資料夾。工作台只接受已核對版本的工具，輸出另建新資料夾。', required: true},
        {title: '跨衛星影格對應與取樣判讀', detail: '若與 ALOS-2 混合，要確認實際地理範圍、參考日期、極化、觀測組合與頻帶／取樣差異。工作台保留人工影格確認，統一取樣安排在讀取後、匹配前。', required: true}
      ],
      sources: [{label: 'JAXA：ALOS-4 資料、範例與轉換工具', url: 'https://www.eorc.jaxa.jp/ALOS/en/alos-4/a4_data_e.htm', detail: '頁面包含樣本資料與授權、產品規格，以及高解析度模式的 P3toP2 範例程式。不是所有樣本都屬於工作台支援模式。'}],
      folders: [
        {path: 'raw/alos4/各完整產品/', role: '你準備', detail: '原始產品不改名、不覆蓋；把 ZIP 解壓到各自資料夾。'},
        {path: 'tools/P3toP2snap/', role: '你從官方取得', detail: '在工具選擇欄選資料夾，不必手動修改課程範例的絕對路徑。'},
        {path: 'prepared/新批次/日期_產品代號/ALOS2_format/', role: '準備流程建立', detail: '每份產品有自己的子資料夾；轉換成果與原始 ALOS-4 分開，附轉換紀錄。'},
        {path: 'projects/mixed_stack/', role: '另建處理專案', detail: '準備完成後才能帶入多日期設定；影格與取樣仍需再次確認。'}
      ],
      checklist: [
        {id: 'complete', label: '我有完整已解壓的支援產品，以及官方轉換工具。'},
        {id: 'separate', label: '我選了不同於原始來源的新輸出位置。'},
        {id: 'gate', label: '我會保留影格人工檢查，不以相同影格號代替地理與科學判斷。'}
      ],
      next: [{action: 'alos4', label: '開啟 ALOS 資料準備'}],
      notes: ['這條路線使用 ISCE2 的準備與 alosStack 能力；ALOS-4 不等於一定要用 ISCE3。', '統一距離與方位取樣間距不等於完成對位、共同頻帶濾波或證明跨衛星可干涉；後續匹配與成果仍要驗收。']
    },
    {
      id: 'nisar', name: 'NISAR RSLC → GUNW', status: 'experimental', statusDetail: '獨立 ISCE3 試驗流程；不包含在目前一鍵 ISCE2 環境中。',
      summary: '從兩期 NISAR L-band RSLC HDF5 進行 ISCE3 處理。不要把 GCOV、GSLC、已完成的 GUNW 或預覽圖當作 RSLC 輸入。',
      requirements: [
        {title: '兩期完整 NISAR RSLC .h5', detail: '同軌道／影格／方向，有共同頻率頻道、極化與地面覆蓋；使用完整產品，不是 STATS.h5 或 PNG。', required: true},
        {title: '涵蓋完整影像的適用 DEM', detail: '需核對座標與高程基準，不能只因副檔名是 TIFF 就認定可用。此流程與 ISCE2 的地形描述格式不同，請依 NISAR 介面的檢查結果準備。', required: true},
        {title: '獨立且通過驗證的 ISCE3 環境', detail: '必須能執行完整 nisar.workflows.insar CPU 流程；只有 import isce3 成功不足。現有一鍵 ISCE2／MintPy 安裝不包含它。', required: true},
        {title: '足夠空間與新的輸出資料夾', detail: '完整 RSLC 與中間產物很大，工作台會先檢查空間。此入口不是先裁切小區域再處理的精靈。', required: true}
      ],
      sources: [
        {label: 'ASF：NISAR RSLC 產品說明', url: 'https://nisar-docs.asf.alaska.edu/rslc/', detail: '確認選取的是 RSLC 產品。'},
        {label: 'ASF：NISAR 資料取得', url: 'https://nisar-docs.asf.alaska.edu/access-overview/', detail: '依官方說明使用 Earthdata 帳戶下載；工作台不代為保管帳密。'},
        {label: 'NASA：NISAR 樣本資料', url: 'https://science.nasa.gov/mission/nisar/sample-data/', detail: '學習用樣本需與當前處理器格式相容；範例可下載不等於已在工作台完整驗證。'}
      ],
      folders: [
        {path: 'raw/nisar/NISAR_…_RSLC_…日期1….h5', role: '你下載', detail: '保留完整第一期產品。'},
        {path: 'raw/nisar/NISAR_…_RSLC_…日期2….h5', role: '你下載', detail: '第二期須相容，不能僅憑副檔名 .h5 判斷。'},
        {path: 'terrain/nisar_dem.tif', role: '你準備並核對', detail: '完整覆蓋與正確的垂直基準都需要確認。'},
        {path: 'projects/nisar_pair/', role: '流程建立', detail: '新輸出位置與原始 RSLC 分開；先完成環境與資料檢查。'}
      ],
      checklist: [
        {id: 'rslc', label: '我有兩期完整且相容的 NISAR L-band RSLC。'},
        {id: 'environment', label: '我已另外準備並驗證完整 ISCE3 CPU 運算環境。'},
        {id: 'dem', label: '我已確認 DEM 覆蓋及高程基準，並安排足夠空間。'}
      ],
      next: [{action: 'nisar', label: '開啟 NISAR 試驗流程'}],
      notes: ['NISAR 的選用水體資料有特定海岸距離編碼，普通 0／1 水體遮罩不能直接替代；不確定時不要任意帶入。', '這是獨立研究試驗入口，仍待完整真實資料驗證；不應當作目前最容易上手的新手路線。']
    },
    {
      id: 'existing', name: '我已有 ISCE2 成果，想接 MintPy', status: 'import', statusDetail: '限已完成的標準 alosStack 下游分析輸入。',
      summary: '已有 cmd4 成果就不必從原始影像重跑。先分清楚你拿到的是原處理資料夾，還是工作台匯出的可攜分析資料包。',
      requirements: [
        {title: '完整標準 alosStack 成果', detail: '需要所有配對的解纏、遮罩後解纏、相干性與連通區，以及幾何參考日期的高度／緯度／經度／LOS／WBD、描述檔、track／frame 與 baseline；只拿一張 GeoTIFF 不足。', required: true},
        {title: '選擇來源方式', detail: '手動成果匯入會連結原影像，原磁碟須保持連接；可攜分析資料包包含必要實體分析輸入，適合跨電腦交接。兩者都在新位置建專案，不續跑舊 cmd 檢查點。', required: true},
        {title: '接收電腦的 ISCE2／MintPy 環境', detail: '資料包不包含 Conda 環境。兩日期可做品質檢查；MintPy 時序介面仍至少需要三日期與連通配對網路。', required: true}
      ],
      sources: [],
      folders: [
        {path: 'existing_project/proc/', role: '手動成果來源', detail: '選原 proc 或其父資料夾；保留 pairs、dates_resampled、baseline 與全部必要描述檔。先停止原處理工作。'},
        {path: 'analysis_package/sar-analysis-package.json', role: '可攜包識別檔', detail: '選含此清單的完整資料夾，不能只複製其中的 proc。'},
        {path: 'projects/imported_analysis/', role: '工作台另建', detail: '先驗證與預覽，再匯入；從品質檢查開始建立新的 MintPy 分析。'}
      ],
      checklist: [
        {id: 'source', label: '我知道拿到的是完整手動成果或工作台可攜分析包。'},
        {id: 'complete', label: '來源已停止運算，必要影像及描述檔齊全且磁碟已連線。'},
        {id: 'new', label: '我已選擇接收環境與新的工作位置。'}
      ],
      next: [{action: 'legacy', label: '匯入手動 ISCE2 成果'}, {action: 'portable', label: '匯入可攜分析資料包'}],
      notes: ['這個入口不支援 TOPS／通用 Stripmap／NISAR，也不把 alos2App 單對成果變成多日期堆疊。', '可攜包是下游分析交接，不包含完整原始 SAR、中間 SLC、舊 MintPy 分析或運算環境；完整研究仍須另做備份。', '匯入成功與檔案校驗通過不等於科學品質通過；電離層、解纏、參考點與時間序列仍需判讀。']
    }
  ],
  glossary: [
    {term: 'SLC／RSLC', detail: '保留振幅與相位的複數 SAR 影像；干涉處理需要相位。產品名稱相似，不代表 GRD、GCOV 或瀏覽圖可以替代。'},
    {term: '日期／影格／極化', detail: '日期是觀測時間；影格是產品分段，編號不能取代地理比對；HH、VV 等極化需存在於每期資料中。'},
    {term: 'DEM／WBD', detail: 'DEM 提供地形高程；WBD 是水體遮罩。影像本體與描述檔要成套，高程基準也要正確。'},
    {term: 'proc／XML／cmd', detail: 'proc 是處理資料夾，XML 保存設定，cmd 是處理指令。使用工作台時由表單與流程管理，不必先手動製作。'},
    {term: '來源連結', detail: '工作資料夾引用原本影像的位置。它節省空間，但不是複製或備份；原硬碟拔除後就無法讀取。'},
    {term: '人工確認', detail: '你的研究判斷紀錄，與這份準備清單不同。勾選新手清單不會批准影格、電離層、解纏或 MintPy 關卡。'}
  ]
};
