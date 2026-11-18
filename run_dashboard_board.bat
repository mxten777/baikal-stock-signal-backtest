@echo off
REM Browser-local investment hub. No Python backend or collection is started.
setlocal
cd /d "%~dp0"

where npm.cmd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js and npm are required but were not found.
    set "LAUNCHER_EXIT=1"
    goto :finish
)
if not exist "dashboard\frontend\node_modules\vite\bin\vite.js" (
    echo [ERROR] Frontend dependencies missing. Run npm ci in dashboard\frontend.
    set "LAUNCHER_EXIT=1"
    goto :finish
)

powershell.exe -NoLogo -NoProfile -Command "try { $r = Invoke-WebRequest 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 3; if ($r.Content.Contains('BAIKAL Investment Hub')) { Start-Process 'http://127.0.0.1:5173/'; exit 0 } } catch { Write-Host '[INFO] Starting the investment hub frontend.' }; exit 1"
if not errorlevel 1 (
    set "LAUNCHER_EXIT=0"
    goto :finish
)
cd dashboard\frontend
call npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort --open
set "LAUNCHER_EXIT=%ERRORLEVEL%"
if not "%LAUNCHER_EXIT%"=="0" echo [STOP] Hub could not start. Review the error above. No backend or collection was started.

:finish
echo.
pause
exit /b %LAUNCHER_EXIT%
