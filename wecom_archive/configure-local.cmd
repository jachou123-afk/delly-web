@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "WECOM_PROJECT=%~dp0.."

if not exist "%WECOM_PROJECT%\.venv\Scripts\python.exe" (
  echo 找不到專案 Python，請回到 Codex 完成執行環境準備。
  pause
  exit /b 1
)
if exist "%LOCALAPPDATA%\WeComArchive\credentials.json" (
  echo 這個 Windows 帳號已有存檔憑證，本次未覆蓋。請回到 Codex 核對設定。
  pause
  exit /b 1
)

echo 企業 ID：在企業微信管理端「我的企業／企業資訊」查看。
echo Secret：在「會話內容存檔」的 Secret 那一列點「查看」。
echo 請在此視窗輸入。Secret 不會顯示，也不會寫入命令參數或 GitHub。
echo.
"%WECOM_PROJECT%\.venv\Scripts\python.exe" "%~dp0download.py" configure
if errorlevel 1 (
  echo.
  echo 設定未完成，請回到 Codex 回報錯誤；不要貼 Secret。
  pause
  exit /b 1
)
echo.
echo 本機加密憑證已保存。請回到 Codex 回覆「已設定」，再驗收首次同步。
pause
exit /b 0
