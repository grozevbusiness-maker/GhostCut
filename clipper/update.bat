@echo off
setlocal
rem Runs a copy of itself from %TEMP%, so it can safely replace this file.
if /i not "%~1"=="--from-temp" (
  copy /y "%~f0" "%TEMP%\ghostclip_update.bat" >nul
  "%TEMP%\ghostclip_update.bat" --from-temp "%~dp0."
  exit /b
)
set "TARGET=%~2"
cd /d "%TARGET%"
title GhostClip update
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
echo.
echo   Updating GhostClip. Close GhostClip first if it's running.
echo.
set "URL=https://github.com/grozevbusiness-maker/GhostCut/archive/refs/heads/claude/nifty-ritchie-hhx2yk.zip"
set "ZIP=%TEMP%\ghostclip_update.zip"
set "DIR=%TEMP%\ghostclip_update"
echo [1/2] Downloading the latest version...
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing '%URL%' -OutFile '%ZIP%'; if (Test-Path '%DIR%') { Remove-Item -Recurse -Force '%DIR%' }; Expand-Archive -Force '%ZIP%' '%DIR%'"
if errorlevel 1 (
  echo   Download failed. Check your internet connection and try again.
  pause
  exit /b 1
)
set "SRC="
for /d %%D in ("%DIR%\*") do set "SRC=%%D\clipper"
if not exist "%SRC%\ghostclip" (
  echo   The download didn't contain GhostClip. Nothing was changed.
  pause
  exit /b 1
)
robocopy "%SRC%" "%TARGET%" /E /NFL /NDL /NJH /NJS /NP /XD .venv ffmpeg >nul
if errorlevel 8 (
  echo   Copying the new files failed. Is GhostClip still open? Close it and try again.
  pause
  exit /b 1
)
echo [2/2] Installing anything new...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
) else (
  echo   GhostClip isn't set up yet - running setup...
  call setup_windows.bat
)
echo.
echo   Updated. Start GhostClip again from the desktop icon.
echo.
pause
