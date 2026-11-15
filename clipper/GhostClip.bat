@echo off
cd /d "%~dp0"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
if not exist ".venv\Scripts\python.exe" (
  echo GhostClip isn't set up yet. Double-click setup_windows.bat first.
  pause
  exit /b 1
)
title GhostClip - keep this window open while clipping
echo.
echo   GhostClip is running. The dashboard opens in your browser.
echo   Keep this window open. To finish, press "Stop" (or "Quit app") in the dashboard.
echo.
".venv\Scripts\python.exe" -m ghostclip %*
if errorlevel 1 pause
