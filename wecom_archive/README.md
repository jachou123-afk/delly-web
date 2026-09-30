# 企業微信會話內容存檔下載工具

這是獨立的 Windows 工具，不會啟動或修改採購報價網站。它使用[企業微信官方會話存檔 SDK](https://developer.work.weixin.qq.com/document/path/91774)拉取已開通存檔的會話，解密後保留完整訊息 JSON，並可匯出 CSV、JSONL 與媒體檔案。

## 前置設定

1. 企業微信管理端：確認存檔成員範圍、可信 IP、消息加密公鑰，並依目前後台畫面確認存檔服務已啟用與告知流程。外部客戶的同意狀態由企業微信管理。
2. 從[官方文件](https://developer.work.weixin.qq.com/document/path/91774)下載 Windows SDK v3；把 `WeWorkFinanceSdk.dll`、`libcrypto-3-x64.dll`、`libssl-3-x64.dll`、`libcurl-x64.dll` 放到 `%LOCALAPPDATA%\WeComArchive\sdk-v3`。
3. 將與管理端公鑰配對的 RSA 私鑰放到 `%LOCALAPPDATA%\WeComArchive\archive_private_key.pem`。不要把私鑰、會話存檔 Secret 或聊天資料提交到 GitHub。
4. 執行電腦的對外 IP 必須與管理端可信 IP 相符。手動 `sync` 每次輸入企業 ID 與 Secret，不儲存 Secret；若使用下方的自動排程，則須先執行 `configure`，以 Windows 帳號加密保存 Secret。若將來更換管理端公鑰，務必保留舊版本私鑰，才能解開舊版本訊息。

## 執行

在專案根目錄的 PowerShell：

```powershell
.\.venv\Scripts\python.exe .\wecom_archive\download.py sync
.\.venv\Scripts\python.exe .\wecom_archive\download.py export --output .\wecom_archive_data\wecom-conversations.zip
```

首次拉取從 `seq=0` 開始。後續從本機資料庫儲存的最大 `seq` 接續；同批資料和游標一起提交，重新執行不會重複寫入。`sync` 會拉取訊息與附件；附件失敗時保留待重試清單，下一次同步會再試。`export` 只有在附件全部成功時才建立 ZIP，內含原始解密 JSONL、方便開啟的 CSV、附件及摘要。CSV 是閱讀用，JSONL 保留全部原始欄位。

本機資料庫與 SDK 放在 `%LOCALAPPDATA%\WeComArchive`。請將 `export` 的輸出位置設在已確認可同步的雲端資料夾，並確認雲端上傳完成。訊息可能只保留短期拉取窗口，請定期執行同步；開通前的歷史對話無法藉此補抓。

## 自動同步與隨時下載

在執行電腦上設定一次本機加密憑證，再安裝 Windows 排程：

```powershell
.\.venv\Scripts\python.exe .\wecom_archive\download.py configure --corp-id <企業ID>
powershell -NoProfile -ExecutionPolicy Bypass -File .\wecom_archive\install-schedule.ps1
```

`configure` 會隱藏輸入 Secret，並用目前 Windows 帳號的 DPAPI 加密後存於 `%LOCALAPPDATA%\WeComArchive\credentials.json`。它不在 OneDrive 或 GitHub 專案中；私鑰仍只放在 `%LOCALAPPDATA%\WeComArchive`。換 Windows 帳號或電腦時必須重新設定。不要把 Secret 放在命令參數或排程內容。

排程每天台灣時間 00:15、04:15、08:15、12:15、16:15、20:15 及登入時執行。成功時更新同一個 `wecom_archive_data\wecom-conversations.zip`；若沒有新對話就沿用原 ZIP，`wecom-sync-status.txt` 會記錄最近同步結果。附件未下載完成或同步失敗時保留上次 ZIP，狀態檔會標明失敗。需要立即刷新時，雙擊 `wecom_archive\sync-now.cmd`，成功後會在檔案總管選取 ZIP。

這個方案靠本機 Windows 與 OneDrive：電腦需開機、登入、連網，執行時的對外 IP 須在企業微信可信 IP 清單。關機期間無法抓新對話，但已上傳的 ZIP 仍可從 OneDrive 下載。OneDrive 同步完成後，從其他裝置開啟 OneDrive 中相同專案路徑，點 `wecom-conversations.zip` 即可下載。另有本機 Codex 排程約每四小時 30 分檢查 ZIP 是否變動，若有新版且檔案大小在 Pages 上傳限制內，更新原本的 ChatGPT Page 下載連結；以該頁顯示的同步時間為準。
