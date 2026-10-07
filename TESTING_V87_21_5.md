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

目前完成本機實作與離線驗證；尚未推送、部署或 live 驗收。正式站是否顯示 V87.21.5、獨立入口可操作，以及真實原文在原雲表讀回成功，仍由發布後驗收確認。

## NAS 商品原圖上限

- 報價原圖與商品補圖每張上限提高至 20 MB（20 MiB），NAS 商品原圖與位置索引採同一上限。保留原始位元組與 SHA-256，不壓縮、不重新編碼。
- 維持 JPG／PNG／WebP 靜態格式、2500 萬像素、每款 1～5 張不同原圖及既有整批總量限制。讀回仍核對長度、雜湊與格式。
- 超過 2 MB 的原圖必須使用 NAS 商品圖庫；舊 Google Sheet base64 圖庫仍限制 2 MB，並於任何圖片寫入前明確報錯。既有紀錄分段規則不變，避免擴大雲表的圖片資料量。
- 發送管理「加入商品圖片」的獨立 2 MB 上傳限制，以及縮圖限制維持不變。
- 新測試 `tests/test_original_image_limits.py`：9 passed。以 3／13／20 MiB 合成有效 PNG 核對 NAS 上傳、商品綁定與重新開啟後原始位元組一致；另測試 20 MiB 邊界、超限拒絕、舊圖庫拒絕大型原圖、雜湊破損、格式／動畫／像素／張數守門及實際 UI 上傳限制。
- 回歸指令：`python -m pytest tests/test_original_image_limits.py tests/test_dispatch_storage.py tests/test_product_image_library.py tests/test_dispatch_images.py tests/test_quote_image_repair.py tests/test_quote_image_repair_ui.py tests/test_synology_image_store.py tests/test_nas_dispatch_storage.py tests/test_thumbnails.py -q`：191 passed。
- 全部測試僅使用合成圖片、FakeSpreadsheet 與假的 NAS transport，不連正式服務、不存入私人原圖。正式 NAS 超過 2 MB 的實際保存與 SHA-256 讀回仍需發布後驗收。
