@echo off
REM Compatibility entry point; the retired API/LAN launcher is not started.
echo [INFO] BAIKAL now uses the browser-local investment hub on this PC.
call "%~dp0run_dashboard_board.bat"
exit /b %ERRORLEVEL%
