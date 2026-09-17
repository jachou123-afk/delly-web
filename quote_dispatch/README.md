# 報價轉發規則

- [LINE 商品廣告 Computer Use 執行 SOP](LINE_COMPUTER_USE_SOP.md)：執行電腦每次換機／續作先從 GitHub main 重新讀取；整批準備圖文、同聊天室連續處理、逐款查驗後整批回寫。不可將重新執行當成整批重發。

- `rules/common.yaml`：所有廠商共同的名稱、售價單位、交期與 LINE 群組規則。
- `rules/suppliers/`：每間廠商獨立的例外；修改一間時不會改到其他廠商。
- `pipeline/`：解析、驗算、產生待發文案與發送後對帳。

規則檔採用 JSON 格式的 YAML 1.2 子集，部署時不需額外 YAML 套件。設定錯誤會停止流程，不會猜測或套用部分設定。
