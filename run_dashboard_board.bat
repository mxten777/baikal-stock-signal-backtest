@echo off
REM One-click launcher for BAIKAL Stock Daily Signal Board (Windows).
REM Starts backend (dashboard.api) + frontend (Vite) and opens the browser.
REM Does NOT touch production signal engine, scheduler, or dashboard code.

setlocal enabledelayedexpansion
cd /d "%~dp0"

set "REPO_ROOT=%~dp0"
set "VENV_PY=%REPO_ROOT%.venv\Scripts\python.exe"
set "FRONTEND_DIR=%REPO_ROOT%dashboard\frontend"
set "BACKEND_PORT=8765"
set "FRONTEND_PORT=5173"

if not exist "%VENV_PY%" (
    echo [ERROR] venv python not found at "%VENV_PY%".
    echo         Create the virtual environment first: python -m venv .venv
    pause
    exit /b 1
)

if not exist "%FRONTEND_DIR%\package.json" (
    echo [ERROR] Frontend project not found at "%FRONTEND_DIR%".
    pause
    exit /b 1
)

echo [INFO] Checking backend port %BACKEND_PORT% ...
netstat -ano | findstr ":%BACKEND_PORT% " | findstr "LISTENING" >nul
if %ERRORLEVEL%==0 (
    echo [SKIP] Backend already running on port %BACKEND_PORT%. Not starting a duplicate.
) else (
    echo [INFO] Starting backend: "%VENV_PY%" -m dashboard.api
    start "BAIKAL Dashboard Backend" cmd /k "cd /d "%REPO_ROOT%" && "%VENV_PY%" -m dashboard.api"
)

echo [INFO] Checking frontend port %FRONTEND_PORT% ...
netstat -ano | findstr ":%FRONTEND_PORT% " | findstr "LISTENING" >nul
if %ERRORLEVEL%==0 (
    echo [SKIP] Frontend already running on port %FRONTEND_PORT%. Not starting a duplicate.
) else (
    echo [INFO] Starting frontend: npm run dev
    start "BAIKAL Dashboard Frontend" cmd /k "cd /d "%FRONTEND_DIR%" && npm run dev"
)

echo [INFO] Waiting for services to become ready ...
timeout /t 8 /nobreak >nul

echo [INFO] Opening browser at http://localhost:%FRONTEND_PORT%/
start "" "http://localhost:%FRONTEND_PORT%/"

echo.
echo ============================================================
echo  BAIKAL Daily Signal Board is starting.
echo    Backend window : "BAIKAL Dashboard Backend"  (port %BACKEND_PORT%)
echo    Frontend window: "BAIKAL Dashboard Frontend" (port %FRONTEND_PORT%)
echo  If the browser page does not load, check those two windows
echo  for error messages (they stay open on failure).
echo.
echo  TO STOP: close the "BAIKAL Dashboard Backend" and
echo  "BAIKAL Dashboard Frontend" windows (or press Ctrl+C inside
echo  each, then close them). Closing this window only closes
echo  this launcher, not the backend/frontend windows.
echo ============================================================
echo.
pause
