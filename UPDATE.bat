@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AI Trading Bot update
echo ============================================
echo    AI Trading Bot - update to the latest version
echo ============================================
echo.
if not exist ".python-path" (
  echo Run SETUP first.
  pause
  exit /b 1
)
set /p PY=<".python-path"
set "ZIP=%TEMP%\ai-trading-update.zip"
set "OUT=%TEMP%\ai-trading-update"

echo Downloading the latest version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Invoke-WebRequest 'https://github.com/advanijahaan/Ai-trading/archive/refs/heads/claude/jolly-mendel-hoavwy.zip' -OutFile $env:ZIP; if (Test-Path $env:OUT) { Remove-Item $env:OUT -Recurse -Force }; Expand-Archive $env:ZIP $env:OUT"
if errorlevel 1 goto failed
if not exist "%OUT%\Ai-trading-claude-jolly-mendel-hoavwy\trading_bot" goto failed

echo Stopping the bot...
taskkill /fi "WINDOWTITLE eq Trading bot - keep this open*" /t /f >nul 2>&1
timeout /t 2 /nobreak >nul

echo Installing the new version (your keys and the bot's memory are kept)...
robocopy "%OUT%\Ai-trading-claude-jolly-mendel-hoavwy" "%CD%" /E /XF UPDATE.bat /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto failed
"%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt

echo Starting the bot again...
start "Trading bot - keep this open" /min "%~dp0deploy\windows\start-bot.bat"
echo.
echo DONE! The bot is running the latest version.
pause
exit /b 0

:failed
echo.
echo The update didn't work (check your internet). The bot hasn't been changed.
echo If it was stopped, restart your PC and it will start again.
pause
exit /b 1
