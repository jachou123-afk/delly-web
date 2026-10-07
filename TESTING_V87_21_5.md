# V87.21.5 既有商品獨立補存原文

商品已保存而原文存證未完成、工作階段中的重試資料又已不存在時，可透過獨立「📄 原文補存」入口補存，不需要新增商品或建立廣告批次。沿用 V87.21.4 保溫袋配件重量修正及既有成本規則。

## 預期操作

1. 開啟「📄 原文補存」，輸入 NO、貨號或品名並按「搜尋商品」。
2. 明確選擇商品，核對分頁、NO、貨號、品名與廠商，按「載入本款原文與參數」。
3. 填寫「補存核對人」，展開「補存／修正原文（不改商品與價格）」。
4. 貼入完整廠商原文，對照本款原表／公式候選值與當時紀錄，填寫參數來源說明並勾選確認，按「保存依據並重新驗算」。
5. 在「查看原文與計算參數」確認完整原文、保存方式及原雲表位置，另重新讀回正式雲表確認保存。

## 保存邊界

- 只追加原雲表 `_報價依據` 歷史，不改商品、NO、價格、公式、圖片、廣告批次或發送紀錄。
- 補存明確標為人工補存，不偽造最初解析值；完整原文保留繁簡字、空白及換行。
- 初次開啟不讀雲表或成本設定；搜尋錯誤、重新搜尋、切換商品及載入失敗會清除舊目標，防止沿用別款原文與確認。
- 唯一商品身分、位置、來源快照及公式須保持一致。輸入參數須通過獨立成本驗算，未知數字保持空白，跨商品公式、進價／裝箱量／單位不一致皆停止；不由售價倒推。
- 保存前後重新讀取存證索引，只有原文、參數及商品來源讀回一致才顯示成功。逾時／讀回失敗保留當前表單，明確重試不重複追加相同存證。

## 離線驗證

- 所有測試使用合成商品與 FakeSpreadsheet，不連 Google／NAS，不讀憑證，不寫入正式商品。
- 核心指令：`python -m pytest tests/test_quote_evidence_repair.py tests/test_quote_evidence.py tests/test_cost_audit.py -q`：93 passed。
- 獨立覆核指令：`python -m pytest tests/test_quote_evidence_repair.py tests/test_quote_evidence.py tests/test_cost_audit.py tests/test_dispatch_ui.py tests/test_v74_ui.py -q`：133 passed。
- 測試涵蓋單獨存證、原文逐字保存、商品／圖庫／批次不變、重複 NO、來源／公式變更、未知或相異參數、跨商品公式、保存逾時重試、實際讀回資料消失、搜尋失敗、切換目標與主程式分頁路由。
- `git diff --check` 通過。

## 發布狀態

- 程式已推送既有 GitHub `main`：`bf03413158bd2937ee38b5a44744ebf0951b68f8`；發布執行者已確認正式站顯示 V87.21.5，並完成獨立「原文補存」入口的一筆既有商品補存。
- 2026-10-07 17:19:24（台灣時間）保存的正式 `_報價依據` 讀回檔已獨立核對：完整原文與來源 manifest 逐字一致、8 項輸入參數一致、商品身分及來源雜湊一致；保存方式為 `review_attachment`，`parsed` 為空，不偽造最初擷取值。
- 該次存證讀回相較補存前僅增加 1 筆，既有 78 筆內容不變。商品補存前與重新開啟的六列讀回檔位元組一致；進價、重量、運費、成本及報價顯示值與採用參數的獨立計算一致。此次 TSV 是顯示值讀回，未據此宣稱獨立驗證所有原公式字串。
- 私人原文、商品資料及逐項驗收檔保留在原作業資料夾，不加入 GitHub。20 MB 原圖功能已隨本版部署，但正式 NAS 大於 2 MB 的保存與 SHA-256 讀回尚未 live 驗收，不能與本次原文補存成功混稱。

## NAS 商品原圖上限

- 報價原圖與商品補圖每張上限提高至 20 MB（20 MiB），NAS 商品原圖與位置索引採同一上限。保留原始位元組與 SHA-256，不壓縮、不重新編碼。
- 維持 JPG／PNG／WebP 靜態格式、2500 萬像素、每款 1～5 張不同原圖及既有整批總量限制。讀回仍核對長度、雜湊與格式。
- 超過 2 MB 的原圖必須使用 NAS 商品圖庫；舊 Google Sheet base64 圖庫仍限制 2 MB，並於任何圖片寫入前明確報錯。既有紀錄分段規則不變，避免擴大雲表的圖片資料量。
- 發送管理「加入商品圖片」的獨立 2 MB 上傳限制，以及縮圖限制維持不變。
- 新測試 `tests/test_original_image_limits.py`：9 passed。以 3／13／20 MiB 合成有效 PNG 核對 NAS 上傳、商品綁定與重新開啟後原始位元組一致；另測試 20 MiB 邊界、超限拒絕、舊圖庫拒絕大型原圖、雜湊破損、格式／動畫／像素／張數守門及實際 UI 上傳限制。
- 回歸指令：`python -m pytest tests/test_original_image_limits.py tests/test_dispatch_storage.py tests/test_product_image_library.py tests/test_dispatch_images.py tests/test_quote_image_repair.py tests/test_quote_image_repair_ui.py tests/test_synology_image_store.py tests/test_nas_dispatch_storage.py tests/test_thumbnails.py -q`：191 passed。
- 全部測試僅使用合成圖片、FakeSpreadsheet 與假的 NAS transport，不連正式服務、不存入私人原圖。正式 NAS 超過 2 MB 的實際保存與 SHA-256 讀回仍需發布後驗收。

## NAS 上傳失敗的安全診斷補強

- 上傳 API 的失敗仍顯示「原圖保存結果待確認」，只補充白名單錯誤類型、HTTP 狀態及數字 API 代碼，以區分連線逾時、讀取逾時、TLS／憑證、連線失敗、轉向、HTTP／API 回應與格式錯誤；無法細分的情形維持未分類，不推測原因。
- 不顯示 URL、Location、回應本文、帳密、SID 或原始例外訊息／repr。API 代碼只接受有界整數，HTTP 代碼只接受合法範圍數字。
- 未變更 `TIMEOUT=(8,30)`、NAS 端限制、`allow_redirects=False`、TLS 驗證、原圖大小／SHA-256 核對與 `overwrite=False`。未知結果不自動重送、不發布索引，後續明確核對仍須讀回同一原圖。
- 原始轉向錯誤文字保持相容；上傳後的原圖下載逾時不會被誤標為上傳 API 失敗。
- 測試指令：`python -m pytest tests/test_synology_image_store.py tests/test_nas_dispatch_storage.py tests/test_quote_image_repair.py tests/test_original_image_limits.py -q`：131 passed。
- 合成測試包含敏感例外與回應不外洩、HTTP／API 413 區分、轉向不跟隨、連線／讀取逾時分類、未知結果只發一次上傳，以及原檔已保存後可僅下載核對而不再次上傳。
- 此診斷補強目前僅完成本機實作與測試，尚未推送或部署；未據此認定正式 NAS 失敗原因，也未宣稱大型原圖已保存。

## 單獨核對 NAS 已存在的原圖

- 「商品補圖」新增獨立按鈕「只核對 NAS 已存在的本款原圖」。操作者先選定唯一商品並載入來源快照，再選取同一組本機原檔供程式計算 SHA-256；此模式不重新上傳至 NAS，也不退回舊圖庫。
- 檔案須已由既有 NAS 檔案管理方式放在已設定專用圖庫的 `originals/<SHA 前兩碼>/<完整 SHA>.<實際格式副檔名>`。程式從目前驗證過的 NAS 設定與原檔導出 root、library_id、SHA、大小及 MIME，不接受另外輸入的 URL、路徑或手寫位置索引。
- NAS 只執行現有登入、指定圖庫檢查與 FileStation.Download 唯讀 API。該下載 API 沿用 HTTP POST 傳遞參數，沒有檔案上傳內容；不呼叫 FileStation.Upload，不建立 NAS 資料夾，不改原圖，也沒有失敗後上傳的分支。
- 一款所有原檔均須通過長度、SHA-256、實際格式與逐位元組核對，並重新核對商品唯一身分、原表來源、六列內容、公式及既有圖片 revision，才發布位置索引與綁定。索引、綁定及原檔均重新讀回，讀回期間有變動不顯示成功。
- 缺檔、HTTP 403／404／500、API 錯誤、轉向、TLS、逾時、連線失敗、大小／雜湊／格式不符皆停止；不把錯誤當成允許上傳的缺檔訊號。保留現有 timeout、TLS 驗證、禁止轉向，以及 20 MiB／2500 萬像素／靜態格式／每款 5 張／整批總量限制。
- 不修改商品、價格、原文、批次、核對或 LINE 紀錄。已有不同圖片時停止；同組圖片明確重試不重複追加。索引或綁定寫入結果未知時維持待確認，保留已寫入紀錄供下一次明確核對，不刪除或盲目重寫。
- 合成回歸指令：`python -m pytest tests/test_existing_nas_image_repair.py tests/test_quote_image_repair.py tests/test_quote_image_repair_ui.py tests/test_nas_dispatch_storage.py tests/test_synology_image_store.py tests/test_original_image_limits.py tests/test_product_image_library.py -q`：197 passed。UI 測試 mock 隔離調整後另覆跑 `tests/test_quote_image_repair_ui.py`：16 passed。
- 測試禁止呼叫 NAS `put_asset`、Upload API、舊圖庫讀寫；另檢查不存在上傳檔案內容。涵蓋多圖其中一張失敗不得先建索引、其他圖庫指標、來源／公式／revision 競爭、最終讀回變更、未知索引寫入明確重試，以及獨立按鈕不觸發一般保存。
- 獨立覆核同組 197 passed；另外模擬綁定追加已實際寫入後拋錯，首次維持待處理，明確重試讀回既有綁定且不重複索引／綁定列，原圖位元組與順序不變，沒有 Upload 呼叫。
- 目前僅完成本機實作與合成測試，尚待推送、部署及正式原圖讀回驗收；測試不含私人原圖、貨號、真實路徑或憑證。
