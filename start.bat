@echo off
setlocal enabledelayedexpansion

REM === Set the path to your Dograh AI folder (relative to this script or full path) ===
set "PROJECT_DIR=%~dp0"
set "DOGRAH_DIR=%PROJECT_DIR%..\dograh"

echo ==================================================
echo  ApkaPG AI Outbound Calling - One-Click Startup
echo ==================================================

REM 1. Verify Docker Desktop is running
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop is not running.
    echo Please open Docker Desktop, wait until it is ready, and run start.bat again.
    pause
    exit /b 1
)

REM 2. Ensure .env exists
if not exist "%PROJECT_DIR%.env" (
    echo [INFO] .env not found. Copying .env.example to .env...
    copy "%PROJECT_DIR%.env.example" "%PROJECT_DIR%.env" >nul
    echo [WARNING] Fill in your keys inside .env before making live calls!
)

REM 3. Start Dograh AI containers
if exist "%DOGRAH_DIR%\docker-compose.yml" (
    echo [1/3] Starting Dograh AI from "%DOGRAH_DIR%"...
    pushd "%DOGRAH_DIR%"
    docker compose up -d
    popd
) else if exist "%DOGRAH_DIR%\docker-compose.yaml" (
    echo [1/3] Starting Dograh AI from "%DOGRAH_DIR%"...
    pushd "%DOGRAH_DIR%"
    docker compose up -d
    popd
) else (
    echo [WARNING] Dograh folder not found at "%DOGRAH_DIR%".
    echo Update DOGRAH_DIR inside start.bat if Dograh is located elsewhere.
)

REM 4. Build and start ApkaPG FastAPI backend in Docker
echo [2/3] Building and starting ApkaPG Backend on port 8001...
pushd "%PROJECT_DIR%"
docker compose up -d --build
if errorlevel 1 (
    echo [ERROR] Failed to start ApkaPG Docker container.
    popd
    pause
    exit /b 1
)
popd

REM 5. Wait for backend /health then open dashboard
echo [3/3] Waiting for http://127.0.0.1:8001/health ...
set /a TRIES=0
:wait_loop
set /a TRIES+=1
curl -s http://127.0.0.1:8001/health >nul 2>&1
if not errorlevel 1 goto ready
if %TRIES% GEQ 20 (
    echo [WARNING] Backend took longer than expected. Check: docker logs apkapg-calling-backend
    goto open_browser
)
timeout /t 2 /nobreak >nul
goto wait_loop

:ready
echo [OK] Everything is running!

:open_browser
start "" "http://127.0.0.1:8001/"
start "" "http://localhost:3010/"
echo Dashboard: http://127.0.0.1:8001/
echo Dograh UI: http://localhost:3010/
pause