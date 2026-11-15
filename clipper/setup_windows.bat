@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
title GhostClip setup
echo.
echo   GhostClip setup
echo   ===============
echo   This installs what GhostClip needs. It takes a few minutes the first time.
echo.

rem ---- 1. Python 3.10 or newer -------------------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [1/4] Python not found. Installing Python 3.12...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  echo.
  echo   Python is installed. Close this window and double-click setup_windows.bat again.
  echo   If winget did not work, install Python from https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" in the installer.
  pause
  exit /b 1
)
echo [1/4] Python found.

rem ---- 2. GhostClip's own Python packages, in a private folder (.venv) ---------
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || (echo Could not create the .venv folder. & pause & exit /b 1)
)
echo [2/4] Installing streamlink and the caption engine...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet || (
  echo Package install failed. Check your internet connection and run this again.
  pause & exit /b 1
)

rem ---- 3. ffmpeg (records, cuts and edits the video) ----------------------------
".venv\Scripts\python.exe" -c "from ghostclip.utils import find_tool; import sys; sys.exit(0 if find_tool('ffmpeg') else 1)" >nul 2>nul
if errorlevel 1 (
  echo [3/4] Installing ffmpeg...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  ".venv\Scripts\python.exe" -c "from ghostclip.utils import find_tool; import sys; sys.exit(0 if find_tool('ffmpeg') else 1)" >nul 2>nul
  if errorlevel 1 (
    echo.
    echo   ffmpeg could not be installed automatically.
    echo   Download the "release full" build from https://www.gyan.dev/ffmpeg/builds/ ,
    echo   unzip it, and copy its "bin" folder into this folder as  ffmpeg\bin
    pause
  )
) else (
  echo [3/4] ffmpeg found.
)

rem ---- 4. Download the caption model now, so the first clip isn't slow ----------
echo [4/4] Downloading the caption model (about 480 MB, only once)...
".venv\Scripts\python.exe" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('      done')"

rem ---- Desktop shortcut ------------------------------------------------------------
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\GhostClip.lnk');$s.TargetPath='%~dp0GhostClip.bat';$s.WorkingDirectory='%~dp0';$s.IconLocation='%SystemRoot%\System32\shell32.dll,115';$s.Save()" >nul 2>nul

echo.
echo   All set. Start GhostClip with the "GhostClip" icon on your desktop
echo   (or double-click GhostClip.bat in this folder).
echo.
pause
