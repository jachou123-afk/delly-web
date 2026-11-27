# 筆電 Agent 廣告選品交接

店主從私人批發目錄勾選並送出商品後，筆電 Agent 可讀取新批次，依原有廣告規則提出建議發送、暫緩或已做過的品項。批次以雲端保存的商品編號、當時選定的毛利率與售價、箱入數、商品連結及文案為準。 [私人批發目錄](https://toy-wholesale-catalog-review.vicchou.chatgpt.site/)

## 給筆電 Agent 的提示詞

> 請到私人批發目錄的「查看待審批次」，以擁有者帳號讀取最新待審資料。記下批次編號及 BGD 商品編號。依正式發送管理可驗證的紀錄查重，逐款回報建議發送、已發過或暫緩，以及理由。保留批次已保存的售價、計價單位、箱入數、圖片連結及文案。等我確認批次、聊天室及範圍後，再依現行 LINE 發送程序接續。這次先交付判斷結果。

## 讀取方式

- 有 Sites 連接器時，先用 `sites_read_database_overview` 從已登入的擁有者專案確認 D1 綁定 `DB` 與資料表 `ad_selection_batches`，再用 `sites_read_database_table_rows` 唯讀取得批次。依 `status` 找 `pending`，依 `created_at` 查詢時間順序，從 `products_json` 讀取各款內容。若工具回應標記數值截斷，改從擁有者目錄讀取完整批次。
- 有擁有者瀏覽器時，可直接開啟目錄的「查看待審批次」；同一登入工作階段也可讀取 `/api/ad-selections?status=pending`。每款的 `productUrl`、`image` 及 `draft` 均屬內部判斷資料。
- 查看原有批次的進度與重複紀錄時，沿用正式工具的「發送管理」。如紀錄不完整，將該款列為待查，並寫出缺少的證據。 [正式 LINE 執行規範](https://github.com/jachou123-afk/delly-web/blob/main/quote_dispatch/LINE_COMPUTER_USE_SOP.md)
