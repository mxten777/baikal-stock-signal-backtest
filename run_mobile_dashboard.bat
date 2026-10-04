@echo off
setlocal
cd /d "%~dp0"

set "LAUNCHER_PS1=%~dp0scripts\run_mobile_dashboard.ps1"
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Windows PowerShell is required but was not found.
    goto :stop
)
if not exist "%LAUNCHER_PS1%" (
    echo [ERROR] Mobile launcher helper not found: "%LAUNCHER_PS1%"
    goto :stop
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER_PS1%" -RepositoryRoot "%~dp0."
set "LAUNCHER_EXIT=%ERRORLEVEL%"
if not "%LAUNCHER_EXIT%"=="0" (
    echo.
    echo Mobile launcher stopped. Review the message above.
)
goto :finish

:stop
set "LAUNCHER_EXIT=1"

:finish
echo.
pause
exit /b %LAUNCHER_EXIT%
