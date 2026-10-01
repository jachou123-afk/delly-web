# 企業微信會話內容存檔下載工具

換電腦接手或處理目前的 `301042` 同步失敗，先讀 [交接文件](HANDOFF.md)。

這是獨立的 Windows 工具，不會啟動或修改採購報價網站。它使用[企業微信官方會話存檔 SDK](https://developer.work.weixin.qq.com/document/path/91774)拉取已開通存檔的會話，解密後保留完整訊息 JSON，並可匯出 CSV、JSONL 與媒體檔案。

## 公司全新開始，不搬家裡歷史

2026-10-01 公司改採全新建置。家裡原私鑰、歷史資料庫與附件保留；公司只保存管理端切換到新公鑰後、使用該版本加密的訊息。這不會把家裡歷史匯入公司，也不會補回原先未取得的對話。

1. 在一般 Windows 帳號的 PowerShell、專案根目錄執行以下命令建立新金鑰；若已有金鑰、資料庫、憑證或附件，程式拒絕覆蓋。公司本次已建立，無須重做：

   ```powershell
   .\.venv\Scripts\python.exe .\wecom_archive\download.py prepare-fresh
   ```

2. 私鑰保留在 `%LOCALAPPDATA%\WeComArchive\archive_private_key.pem`。使用者在企業微信管理端的「會話內容存檔」將消息加密公鑰設定為同目錄 `archive_public_key.pem` 的完整內容，並記錄儲存後顯示的**實際公鑰版本**。不要猜版本，也不要上傳私鑰。確認公司對外 IP 已在可信 IP 清單、存檔成員與服務設定有效。後台切換前，家裡原存檔與鏡像工作維持停止。
3. 準備下方所列官方 Windows SDK；在公司一般 Windows 檔案總管雙擊 [`configure-local.cmd`](configure-local.cmd)，或用下方命令執行 `configure`，以隱藏輸入設定自己的 Secret。設定入口會詢問企業 ID 與 Secret，只保存目前 Windows 帳號的加密憑證，不拉取對話、不啟用排程；若已有憑證則停止，不直接覆蓋。不要搬家裡的 DPAPI 憑證。
4. 由使用者在存檔範圍內產生一則切換後的新對話，再執行首次同步：

   ```powershell
   .\.venv\Scripts\python.exe .\wecom_archive\download.py configure
   $wecomNewVersion = Read-Host '管理端儲存新公鑰後顯示的版本'
   .\.venv\Scripts\python.exe .\wecom_archive\download.py run --fresh-key-version $wecomNewVersion
   ```

首次同步只適用空資料庫。程式可以掃描較舊版本，但必須成功解密一則指定版本的新訊息，才會原子保存新訊息、略過數量、公鑰指紋及接續序號。尚未看到新訊息、版本填錯、解密錯誤或 API 失敗時，不會保存尚未驗證的略過序號。遇到更高公鑰版本會停止，不會繼續略過。

成功後，新存檔版本固定在資料庫；一般 `run` 與排程會沿用，不需每次重填。ZIP 的 `manifest.json` 與狀態檔會標明只含新版本訊息，以及略過舊版本的數量。不要把此 ZIP 當成家裡的完整歷史備份。手動及排程實跑驗收成功後才啟用日常排程；新 ZIP 上傳原私人下載頁並讀回驗證前，不能稱為雲端已更新。

## 前置設定

1. 企業微信管理端：確認存檔成員範圍、可信 IP、消息加密公鑰，並依目前後台畫面確認存檔服務已啟用與告知流程。外部客戶的同意狀態由企業微信管理。
2. 從[官方文件](https://developer.work.weixin.qq.com/document/path/91774)下載 Windows SDK v3；把 `WeWorkFinanceSdk.dll`、`libcrypto-3-x64.dll`、`libssl-3-x64.dll`、`libcurl-x64.dll` 放到 `%LOCALAPPDATA%\WeComArchive\sdk-v3`。
3. 接續原歷史時，將與管理端既有公鑰配對的原 RSA 私鑰放到 `%LOCALAPPDATA%\WeComArchive\archive_private_key.pem`；全新建置則依上方流程建立並切換新公鑰。不要把私鑰、會話存檔 Secret 或聊天資料提交到 GitHub。
4. 執行電腦的對外 IP 必須與管理端可信 IP 相符。手動 `sync` 每次輸入企業 ID 與 Secret，不儲存 Secret；若使用下方的自動排程，則須先執行 `configure`，以 Windows 帳號加密保存 Secret。若將來更換管理端公鑰，務必保留舊版本私鑰，才能解開舊版本訊息。

## 執行

在專案根目錄的 PowerShell：

```powershell
.\.venv\Scripts\python.exe .\wecom_archive\download.py sync
.\.venv\Scripts\python.exe .\wecom_archive\download.py export --output .\wecom_archive_data\wecom-conversations.zip
```

首次拉取從 `seq=0` 開始。後續從本機資料庫儲存的最大 `seq` 接續；同批資料和游標一起提交，重新執行不會重複寫入。`sync` 會拉取訊息與附件；附件失敗時保留待重試清單，下一次同步會再試。`export` 只有在附件全部成功時才建立 ZIP，內含原始解密 JSONL、方便開啟的 CSV、附件及摘要。CSV 是閱讀用，JSONL 保留全部原始欄位。

圖片下載後放在 ZIP 的 `media/`。匯出會依實際檔頭給 PNG、JPEG、GIF、WebP 圖片正確副檔名；其他附件保留原檔名，不猜檔案類型。先將 ZIP 解壓縮，再開啟 `media/` 裡的圖片。`messages.csv` 最後的「附件檔案」欄列出每則訊息的對應路徑；原有欄位、原始 JSON 和圖片內容均保留。舊版 ZIP 即使訊息筆數相同，也會在下一次成功 `run` 時重建為新版匯出格式；也可單獨執行 `export`，不須重新下載圖片。

本機資料庫與 SDK 放在 `%LOCALAPPDATA%\WeComArchive`。請將 `export` 的輸出位置設在已確認可同步的雲端資料夾，並確認雲端上傳完成。訊息可能只保留短期拉取窗口，請定期執行同步；開通前的歷史對話無法藉此補抓。

## 自動同步與隨時下載

在執行電腦上設定一次本機加密憑證，再安裝 Windows 排程：

```powershell
.\.venv\Scripts\python.exe .\wecom_archive\download.py configure --corp-id <企業ID>
powershell -NoProfile -ExecutionPolicy Bypass -File .\wecom_archive\install-schedule.ps1
```

`configure` 會隱藏輸入 Secret，並用目前 Windows 帳號的 DPAPI 加密後存於 `%LOCALAPPDATA%\WeComArchive\credentials.json`。它不在 OneDrive 或 GitHub 專案中；私鑰仍只放在 `%LOCALAPPDATA%\WeComArchive`。換 Windows 帳號或電腦時必須重新設定。不要把 Secret 放在命令參數或排程內容。

排程每天台灣時間 00:15、04:15、08:15、12:15、16:15、20:15 及登入時執行。成功時更新同一個 `wecom_archive_data\wecom-conversations.zip`；若沒有新對話就沿用原 ZIP，`wecom-sync-status.txt` 會記錄最近同步結果。附件未下載完成或同步失敗時保留上次 ZIP，狀態檔會標明失敗。需要立即刷新時，雙擊 `wecom_archive\sync-now.cmd`，成功後會在檔案總管選取 ZIP。

下載工作靠本機 Windows：電腦需開機、登入、連網，執行時的對外 IP 須在企業微信可信 IP 清單。公司專案位於 Documents，不能只因 ZIP 建立就認定已同步至 OneDrive 或 NAS；須另行驗證指定雲端位置的上傳完成。家裡原本的 Codex 鏡像排程約每四小時 30 分檢查 ZIP，使用者回報目前已暫停。公司尚未接續鏡像工作；恢復前須核對實際輸出位置、保留原 ChatGPT Page，並以頁面下載檔與摘要讀回為準。
