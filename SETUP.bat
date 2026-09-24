@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AI Trading Bot setup
echo ============================================
echo    AI Trading Bot - one-click setup
echo ============================================
echo.

call :findpython
if defined PY goto havepy
echo Installing Python (this takes a minute or two)...
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :findpython
if defined PY goto havepy
echo.
echo Could not install Python automatically.
echo Install it from https://www.python.org/downloads/ (tick "Add Python to PATH"), then double-click SETUP again.
pause
exit /b 1

:havepy
> ".python-path" echo %PY%
echo Installing what the bot needs...
"%PY%" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto nointernet

:keys
if exist ".env" goto testkeys
echo.
echo Now paste your Alpaca PAPER keys.
echo Find them at app.alpaca.markets - Paper account - API Keys.
echo (To paste in this window: right-click.)
echo.
set /p "KEY=API Key ID, then Enter: "
set /p "SECRET=Secret Key, then Enter: "
> ".env" echo ALPACA_API_KEY_ID=%KEY%
>> ".env" echo ALPACA_API_SECRET_KEY=%SECRET%
>> ".env" echo ALPACA_BASE_URL=https://paper-api.alpaca.markets/v2

:testkeys
echo.
echo Checking your keys...
"%PY%" -m trading_bot.bot --dry-run --once > setup-check.log 2>&1
if errorlevel 1 goto badkeys
del setup-check.log

echo Turning on auto-start...
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\trading-bot.bat" echo @echo off
>> "%STARTUP%\trading-bot.bat" echo start "Trading bot - keep this open" /min "%~dp0deploy\windows\start-bot.bat"
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1
start "Trading bot - keep this open" /min "%~dp0deploy\windows\start-bot.bat"

echo.
echo ============================================
echo   DONE! The bot is running.
echo ============================================
echo  - It runs in a small window called "Trading bot" on your taskbar. Don't close it.
echo  - It starts again by itself if it crashes or when you log in.
echo  - Your PC won't go to sleep while it's plugged in.
echo  - See your trades at app.alpaca.markets (Paper account).
echo.
echo  Now tell Claude "it's running on my PC" so the cloud copy gets turned off.
echo.
pause
exit /b 0

:badkeys
echo.
echo That didn't work - the keys are wrong or there's no internet. Let's try again.
del ".env"
goto keys

:nointernet
echo Couldn't download what the bot needs. Check your internet, then double-click SETUP again.
pause
exit /b 1

:findpython
set "PY="
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Python311\python.exe") do if not defined PY if exist %%P set "PY=%%~P"
if defined PY exit /b 0
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
if defined PY exit /b 0
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
exit /b 0
