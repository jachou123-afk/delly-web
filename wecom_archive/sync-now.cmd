@echo off
set "ROOT=%~dp0.."
"%ROOT%\.venv\Scripts\python.exe" "%~dp0download.py" run --output "%ROOT%\wecom_archive_data\wecom-conversations.zip"
if errorlevel 1 (
  echo Sync failed. See wecom-sync-status.txt next to the ZIP.
  pause
  exit /b 1
)
explorer.exe /select,"%ROOT%\wecom_archive_data\wecom-conversations.zip"
