@echo off
rem Runs the trading bot and restarts it 5 seconds after it ever stops.
cd /d "%~dp0..\.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m pip install -q -r requirements.txt
:loop
echo %date% %time% starting bot
%PY% -u -m trading_bot.bot >> bot.log 2>&1
echo %date% %time% bot stopped, restarting in 5 seconds >> bot.log
timeout /t 5 /nobreak >nul
goto loop
