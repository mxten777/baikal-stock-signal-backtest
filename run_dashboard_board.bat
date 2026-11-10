@echo off
REM Read-only PC entry point; shares the existing mobile server launcher.
setlocal
cd /d "%~dp0"

set "LAUNCHER_PS1=%~dp0scripts\run_mobile_dashboard.ps1"
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Windows PowerShell is required but was not found.
    set "LAUNCHER_EXIT=1"
    goto :finish
)
if not exist "%LAUNCHER_PS1%" (
    echo [ERROR] Shared launcher helper not found: "%LAUNCHER_PS1%"
    set "LAUNCHER_EXIT=1"
    goto :finish
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER_PS1%" -RepositoryRoot "%~dp0." -LocalDashboard
set "LAUNCHER_EXIT=%ERRORLEVEL%"
if not "%LAUNCHER_EXIT%"=="0" echo [STOP] Dashboard was not opened. Review the error above. No analysis was run.

:finish
echo.
pause
exit /b %LAUNCHER_EXIT%
