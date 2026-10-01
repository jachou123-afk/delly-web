# 企業微信存檔跨電腦交接（2026-10-01）

下一台電腦先讀此文件與 [操作說明](README.md)，再處理目前的同步失敗。正式程式來源為 [jachou123-afk/delly-web](https://github.com/jachou123-afk/delly-web) 的 `main`；本次核對到的功能提交為 `60738ebfad97b978a8299a713600c4b043c36c38`。

## 公司電腦接續準備（2026-10-01）

### 目前方向：公司全新建置

- NAS 與 TeamViewer 傳檔尚未成功，改採公司建立新金鑰、切換後只存新版本對話。家裡原私鑰、歷史資料庫與附件原地保留，不再以搬移原私鑰作為公司新存檔的前置條件。下方 NAS／直接傳檔段落是先前方案，僅保留交接紀錄。
- 公司一般 Windows 的 `C:\Users\User\AppData\Local\WeComArchive` 已建立一組 RSA 2048 金鑰：`archive_private_key.pem`（1704 bytes）與 `archive_public_key.pem`（451 bytes）。已核對公私鑰配對；未覆蓋原檔。私鑰只在公司本機，未提交 GitHub、貼到聊天或傳入 NAS。
- 使用者已回報管理端新公鑰儲存成功，顯示版本 **2**；公司首次同步使用 `run --fresh-key-version 2`。此為使用者後台回報，尚未透過實際拉取及解密驗證；未預先保存資料庫的已驗證版本或略過游標。
- 公司目前對外 IP 經 ipify 與 AWS 兩個查詢結果一致，為 `1.165.150.117`（2026-10-01 查詢）。使用者已回報此公司 IP 加入管理端可信 IP；此為使用者後台回報，尚待實際拉取驗證，未代為修改後台。IP 可能變動，正式執行前須重查。
- 已新增 `prepare-fresh` 與首次 `run --fresh-key-version <後台實際版本>`。全新建置紀錄會阻止未指定版本的首次同步；略過舊訊息前必須成功解密新版本訊息。版本及公鑰指紋保存後不可直接改版或換私鑰。ZIP 明示僅含新版本對話，不宣稱完整歷史。
- 16 項離線測試通過，包含舊批次掃描、首次版本驗證、同批失敗不保存訊息或游標、API 失敗、既有歷史保護、金鑰替換拒絕、排程沿用及匯出範圍。測試使用臨時資料，不拉取真實對話。
- 公司已收到使用者下載的 `C:\Users\User\Downloads\sdk_win_v3.zip`（13,714,329 bytes），Windows 下載來源紀錄指向官方文件 `/document/path/91774` 與 `https://wwcdn.weixin.qq.com/node/wework/images/sdk_win_v3.zip`。ZIP SHA-256：`42c056c2ba7dda38c24ba27a7bf8430b7086b551191c620d698c23129187fb97`。
- 已將 ZIP 中 `C_sdk/FinanceSdkDemo/` 的四個 DLL 寫入公司一般 Windows `sdk-v3/`；核對均為 x64、內容與 ZIP 一致，並在公司 64 位元 Python 下通過載入、現有 C API 綁定、SDK／Slice／Media 物件配置及釋放檢查。未輸入憑證、初始化企業連線或呼叫拉取 API。`WeWorkFinanceSdk.dll` SHA-256：`b1f73e40b66fe4c1e15573ff2d593584ea793d7ad65932ced0b49a6008abb2a9`。
- 已提供 `wecom_archive/configure-local.cmd`，供使用者在一般 Windows 檔案總管雙擊後輸入企業 ID 與隱藏的 Secret。此入口只保存 DPAPI 加密憑證，既有憑證會拒絕覆蓋；不自動拉取對話或啟用排程。
- 使用者曾將企業 ID 與 Secret 填在設定入口的兩行提示文字，而非執行視窗。已在公司一般 Windows 帳號將這兩項以 DPAPI 加密保存，讀回確認一致後還原入口；未顯示憑證內容、未提交明文或加密憑證，Git 工作目錄已恢復乾淨。後續在檔案總管雙擊執行設定入口，資料只輸入執行視窗。
- 公司首次 `run --fresh-key-version 2` 於 2026-10-01 13:24:27（台灣時間）完成 SDK 連線與拉取，這次未收到平台錯誤 `301042`；尚未取得並解密版本 2 的新訊息，故全新模式未通過驗收。新資料庫為 0 則訊息、0 個附件，沒有已驗證版本或接續游標；尚未建立 ZIP。已請使用者自行傳一則存檔範圍內的新訊息，再次執行驗收。
- 使用者詢問能否讀舊訊息後，已另外從 `seq=0` 只讀查詢平台現有可拉取資料：共 **1 則、公鑰版本 1**，下一批為空，尚無版本 2 訊息。只檢查筆數與版本，未輸出訊息內容、未保存或推進本機接續游標。此舊版密文需要家裡版本 1 的原私鑰解密；家裡已解密的歷史備份也可另行閱讀。公司版本 2 新金鑰無法取代原私鑰，不能宣稱可補回全部過往聊天。
- **尚未完成**：取得並解密版本 2 的新對話；匯出並驗證 ZIP；啟用公司排程與更新原私人下載頁。SDK 與本機加密憑證已完成；這次 API 成功不代表公私鑰解密、長期同步或雲端聊天備份已完成。
- 本次工具的網站安全政策阻擋企業微信後台及官方 SDK 文件的瀏覽操作；未改用其他介面繞過。後台設定與官方下載由使用者自行操作，程式及本機準備可先完成。
- 家裡排程停止與鏡像暫停為使用者轉述；公司未遠端驗證。切換完成後不啟動家裡舊私鑰的下載工作。先前 `301042` 的原因仍未確認，公司本次拉取未再出現該錯誤；仍以後續實跑結果判斷。

### 以下為先前接續／搬移準備紀錄

- 本機程式已與 GitHub `main` 的 `203f74c28cd3eecb1f36918b64a698b6af0f6d12` 一致；專案位置為 `C:\Users\User\Documents\ChatGPT\半自動-採購表\work\delly-web-repo`。
- 已建立專案 `.venv`，使用 64 位元 Python 3.12.14 與 cryptography 50.0.2；依賴檢查、程式說明入口及一般 Windows 帳號下的 Python 執行檢查通過。
- 公司一般 Windows 的 `%LOCALAPPDATA%\WeComArchive` 接收資料夾已建立，`sdk-v3/` 目前為空。原私鑰、SDK DLL、排程憑證與資料庫均尚未搬入或建立；本機未安裝 `WeCom Conversation Archive Sync` 排程。
- 使用者已確認原本成功下載的來源是家裡電腦，並轉述家裡存檔排程已停用、鏡像排程已暫停、下載程序已結束，原檔均保留。此為家裡執行端的回報，公司端尚未直接驗證。尚待安全搬移原私鑰 `archive_private_key.pem`、`messages.sqlite`、`media/` 與原官方 `sdk-v3/`；Secret 須在公司電腦以隱藏輸入重新設定，不貼在聊天、GitHub 或交接文件中。
- 本次查得兩個對外 IP 查詢結果一致；實際 IP 須在安裝與驗收當下重新核對。本次未修改管理端可信 IP、公鑰、Secret 或服務設定。
- 尚未拉取對話、匯出新 ZIP、安裝排程或更新私人下載頁；本機準備完成不等於存檔已接手成功。下方的成功、失敗、排程與 ZIP 紀錄均為原執行電腦的交接證據。

### NAS 搬移位置（2026-10-01）

- 已在公司一般 Windows 環境驗證 NAS 可連線，並建立 `\\Nas_d224\homes\jachou\WeComArchive-transfer`。新資料夾停用權限繼承，僅允許個人資料夾擁有者完整存取；未修改其他資料夾權限。
- 搬移說明 `README-transfer.txt` 寫入後已讀回一致。此時資料夾只有該說明檔，私鑰、資料庫、附件與 SDK 尚未複製。
- 使用者指出家裡與公司的 NAS 路徑不同；家裡實際可用的路徑或入口尚待提供。上述 UNC 路徑僅在公司端驗證，不可直接當成家裡的連線位置。先核對兩邊是否為同一台 NAS 與同一個資料夾，再依各自入口操作；若為不同 NAS，另行確認安全傳送方式。
- 若家裡仍拒絕存取，回報確切錯誤，勿猜帳密、改用商品共用資料夾或將私鑰貼到聊天。公司搬移資料夾限其個人資料夾擁有者存取；取得家裡入口後仍須實際核對讀取權限。
- 家裡確認下載程序已結束後，再複製指定四項並逐檔核對內容一致。保留家裡原檔，不複製 `credentials.json`、不產生新金鑰；已有同名資料先比較，不直接覆蓋。

### 家裡 NAS 無法連線時的直接傳檔（2026-10-01）

- 家裡執行端回報本機 `C:\Users\user\SynologyDrive\` 對應 NAS「圖片區」，沒有可用映射磁碟；公司指定的私人資料夾尚未讀取或核對。使用者再次回報家裡無法連線 NAS，不以本機可讀的 SynologyDrive 目錄認定 NAS 已可同步。
- 公司接收位置已在一般 Windows 環境建立並核對為 `C:\Users\User\AppData\Local\WeComArchive`。可使用現有遠端工具的檔案傳輸（若支援）或 USB，將家裡一般 Windows `%LOCALAPPDATA%\WeComArchive` 的原 `archive_private_key.pem` 與 `sdk-v3/` 直接複製到該位置。
- 此方式沿用既有企業微信公鑰與原私鑰，公司端可用新資料庫開始；家裡歷史資料庫與附件原地保留，不保證能重新拉回全部歷史訊息。不複製家裡的 `credentials.json`，公司端須重新隱藏輸入 Secret。
- 私鑰不貼到聊天、不放商品共用的「圖片區」或 GitHub。私鑰與 SDK 實際送達後，先核對檔案，再驗收同步及排程；目前只有空接收資料夾，尚未下載新對話。

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
| `%LOCALAPPDATA%\WeComArchive\archive_private_key.pem` | 接續舊版加密訊息時必須搬移配對原私鑰；公司本次採上方全新建置，新金鑰不能解開舊版訊息 |
| 同目錄的 `messages.sqlite` 與 `media/` | 停止舊機同步並確認程序結束後，安全搬移這兩項，保留已存歷史與接續序號；目前工具沒有 ZIP 匯入功能 |
| 同目錄的 `sdk-v3/` | 使用官方 Windows SDK v3；可重新從官方文件取得 |
| 同目錄的 `credentials.json` | Windows DPAPI 加密憑證；在新電腦／新帳號重新執行 `configure`，不要把舊檔當作可用憑證直接複製 |
| Windows 排程與 Codex 鏡像排程 | 屬本機設定，Git 拉取不會安裝或移轉排程 |

Secret、私鑰、資料庫、聊天 ZIP 都不得放進 GitHub。聊天 ZIP 已在原私人下載頁保存；其下載用途不等於可恢復本機資料庫。

本次曾遇到 Codex 封裝環境將 AppData 重導向到應用程式的 `LocalCache`，造成 Codex 內手動同步成功、一般 Windows 排程卻看不到檔案或無法解密。已在一般 Windows 排程環境建立真正的 `%LOCALAPPDATA%\WeComArchive`，並實跑成功。搬移時從一般 Windows 檔案總管／PowerShell 核對真實來源；新機也必須用實際排程帳號驗收，不能只憑 Codex 終端機的成功結果。

## 下一台電腦的接手順序

本節適用搬移並接續原歷史；公司本次全新建置以最上方目前方向及 README 的新流程為準。

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
