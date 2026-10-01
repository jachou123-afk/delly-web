# 企業微信存檔跨電腦交接（2026-10-01）

下一台電腦先讀此文件與 [操作說明](README.md)，再處理目前的同步失敗。正式程式來源為 [jachou123-afk/delly-web](https://github.com/jachou123-afk/delly-web) 的 `main`；本次核對到的功能提交為 `60738ebfad97b978a8299a713600c4b043c36c38`。

## 公司電腦接續準備（2026-10-01）

- 本機程式已與 GitHub `main` 的 `203f74c28cd3eecb1f36918b64a698b6af0f6d12` 一致；專案位置為 `C:\Users\User\Documents\ChatGPT\半自動-採購表\work\delly-web-repo`。
- 已建立專案 `.venv`，使用 64 位元 Python 3.12.14 與 cryptography 50.0.2；依賴檢查、程式說明入口及一般 Windows 帳號下的 Python 執行檢查通過。
- 一般 Windows 的 `%LOCALAPPDATA%\WeComArchive` 尚不存在；本機未安裝 `WeCom Conversation Archive Sync` 排程，常用資料夾未找到原私鑰、歷史資料庫或官方 SDK。
- 使用者已確認原本成功下載的來源是家裡電腦。尚待從家裡電腦安全搬移原私鑰 `archive_private_key.pem`、`messages.sqlite` 與 `media/`，並確認家裡電腦的同步已停止。SDK 需使用官方 Windows v3；Secret 須在公司電腦以隱藏輸入重新設定，不貼在聊天、GitHub 或交接文件中。
- 本次查得兩個對外 IP 查詢結果一致；實際 IP 須在安裝與驗收當下重新核對。本次未修改管理端可信 IP、公鑰、Secret 或服務設定。
- 尚未拉取對話、匯出新 ZIP、安裝排程或更新私人下載頁；本機準備完成不等於存檔已接手成功。下方的成功、失敗、排程與 ZIP 紀錄均為原執行電腦的交接證據。

## 原執行電腦的狀態與待處理問題

| 項目 | 已確認狀態（台灣時間） |
| --- | --- |
| 企業微信存檔 | 已開通；設定時服務版範圍 1 人、公鑰版本 1；後續以管理端實際狀態為準 |
| 實際串接 | 官方 Windows SDK 已成功拉取並解密 1 則測試文字訊息，附件 0 個 |
| 最近成功 | 2026-10-01 04:15:06，同步成功；04:30 頁面鏡像比對相同，未重複上傳 |
| 最近失敗 | 2026-10-01 08:15:04，拉取對話失敗，平台錯誤碼 `301042`；原因尚未確認 |
| Windows 排程 | `WeCom Conversation Archive Sync` 仍啟用，狀態 Ready；08:15 結果碼 1，下一次 12:15 |
| 同步時段 | 每日 00:15、04:15、08:15、12:15、16:15、20:15 及登入時；需要開機、登入、連網 |
| 頁面鏡像 | 本機 Codex `automation-3` 仍啟用，每四小時 30 分檢查新版 ZIP；只做鏡像，不拉 API |

08:30 鏡像工作讀到失敗狀態，因此保留原下載連結。`301042` 沒有經官方來源確認含義，不應直接判定是 Secret、可信 IP 或服務到期，也不應僅依此錯誤碼重設 Secret／公鑰。

下一步先查明此錯誤碼與管理端狀態、實際執行電腦的對外 IP、存檔服務／權限是否相符，再實際同步驗收。目前沒有進行修復或新的 API 拉取。

## 正式下載位置

- 私人下載頁：[企業微信會話存檔下載紀錄](https://chatgpt.com/space/page_6ede76df756c8191ac05de24f9dafba2)。使用原頁面、原下載區塊更新。
- 專案內輸出：`wecom_archive_data/wecom-conversations.zip`，旁邊的 `wecom-sync-status.txt` 記錄最新嘗試結果。
- 保留的 ZIP 為 1 則訊息、0 個附件，SHA-256：`35bfc697c3682367b5f76f0baaac5b2232d59b01c069ef62816aeffa2f4b0098`。失敗後不能稱為最新對話備份。
- 下載頁的 `archive_sha256` 與此 ZIP 相同。只有成功且 ZIP 變動時才上傳與更新頁面；Page 上傳限制為 10 MiB，超過時保留前次連結並回報。

## GitHub 與每台電腦的資料

GitHub 保存下載程式、排程安裝腳本與文件。以下內容留在執行電腦，沒有提交至 GitHub：

| 本機項目 | 換電腦時的處理 |
| --- | --- |
| `%LOCALAPPDATA%\WeComArchive\archive_private_key.pem` | 必須安全搬移原私鑰，與管理端既有公鑰配對；不要產生新私鑰代替它 |
| 同目錄的 `messages.sqlite` 與 `media/` | 停止舊機同步並確認程序結束後，安全搬移這兩項，保留已存歷史與接續序號；目前工具沒有 ZIP 匯入功能 |
| 同目錄的 `sdk-v3/` | 使用官方 Windows SDK v3；可重新從官方文件取得 |
| 同目錄的 `credentials.json` | Windows DPAPI 加密憑證；在新電腦／新帳號重新執行 `configure`，不要把舊檔當作可用憑證直接複製 |
| Windows 排程與 Codex 鏡像排程 | 屬本機設定，Git 拉取不會安裝或移轉排程 |

Secret、私鑰、資料庫、聊天 ZIP 都不得放進 GitHub。聊天 ZIP 已在原私人下載頁保存；其下載用途不等於可恢復本機資料庫。

本次曾遇到 Codex 封裝環境將 AppData 重導向到應用程式的 `LocalCache`，造成 Codex 內手動同步成功、一般 Windows 排程卻看不到檔案或無法解密。已在一般 Windows 排程環境建立真正的 `%LOCALAPPDATA%\WeComArchive`，並實跑成功。搬移時從一般 Windows 檔案總管／PowerShell 核對真實來源；新機也必須用實際排程帳號驗收，不能只憑 Codex 終端機的成功結果。

## 下一台電腦的接手順序

1. 確認 GitHub `main` 最新版本與本機修改。乾淨且只有落後時才能 `git pull --ff-only`；有未提交修改先保留並比較。
2. 先讀最新同步狀態並排查 `301042`。本次沒有暫停舊機排程；正式接手前停止舊機同步並確認程序結束，避免兩台同時更新同一份 OneDrive ZIP。
3. 安全搬移原私鑰、資料庫與附件至新機一般 Windows 帳號的 `%LOCALAPPDATA%\WeComArchive`。若新機已有存檔，先備份並比較來源，不直接覆蓋。保留原公鑰版本，不因換電腦而重設管理端公鑰。
4. 準備 64 位元 Python 與 `.venv`；本次驗證使用 Python 3.12。安裝 `wecom_archive/requirements.txt` 的依賴與官方 SDK。確認新機對外 IP 符合管理端可信 IP 設定。
5. 在新機一般 Windows PowerShell 設定憑證並先手動同步：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r .\wecom_archive\requirements.txt
   $wecomCorpId = Read-Host '企業 ID'
   .\.venv\Scripts\python.exe .\wecom_archive\download.py configure --corp-id $wecomCorpId
   .\.venv\Scripts\python.exe .\wecom_archive\download.py run
   ```

   Secret 由程式隱藏輸入，不寫在命令參數、聊天或交接文件中。檢查 `wecom-sync-status.txt` 的成功時間與 ZIP 摘要。

6. 手動成功後安裝 Windows 排程，再從排程啟動一次並確認 `LastTaskResult` 為 0、狀態檔成功時間有更新：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\wecom_archive\install-schedule.ps1
   Start-ScheduledTask -TaskName 'WeCom Conversation Archive Sync'
   Get-ScheduledTaskInfo -TaskName 'WeCom Conversation Archive Sync'
   ```

   程序結束後才判讀結果；執行中不算驗收成功。

7. 新機專案路徑可能不同。確認既有 Codex 鏡像排程能讀取新機輸出後，才切換執行來源；保留原 Page ID，避免重複建立 Page 或兩套鏡像工作。成功後驗證上傳摘要、原下載連結與讀回結果，再宣稱雲端已更新。

接手時只處理企業微信存檔工具；採購網站、雲表、LINE 發送與真實商品資料不屬本次工作。
