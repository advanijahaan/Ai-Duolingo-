@echo off
rem Started by SETUP.bat (and at every login). Runs the bot and restarts it 5 seconds after it ever stops.
cd /d "%~dp0..\.."
set /p PY=<".python-path"
:loop
echo %date% %time% bot running - keep this window open
"%PY%" -u -m trading_bot.bot >> bot.log 2>&1
echo %date% %time% bot stopped, restarting in 5 seconds >> bot.log
timeout /t 5 /nobreak >nul
goto loop
