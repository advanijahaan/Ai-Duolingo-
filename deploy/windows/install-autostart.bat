@echo off
rem Double-click once. Starts the bot now and every time you log in, and stops the PC sleeping while plugged in.
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
echo @echo off> "%STARTUP%\trading-bot.bat"
echo start "Trading bot" /min "%~dp0start-bot.bat">> "%STARTUP%\trading-bot.bat"
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
start "Trading bot" /min "%~dp0start-bot.bat"
echo.
echo Done! The bot is running in a minimized window and will start again every time you log in.
echo Your PC will no longer go to sleep while plugged in.
echo To see what it's doing, open bot.log in the project folder.
echo To remove: delete "%STARTUP%\trading-bot.bat" and close the "Trading bot" window.
pause
