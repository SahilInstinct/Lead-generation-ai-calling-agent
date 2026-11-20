@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "DOGRAH_DIR=%PROJECT_DIR%..\dograh"

echo Stopping ApkaPG Backend...
pushd "%PROJECT_DIR%"
docker compose down
popd

if exist "%DOGRAH_DIR%\docker-compose.yml" (
    echo Stopping Dograh AI...
    pushd "%DOGRAH_DIR%"
    docker compose down
    popd
) else if exist "%DOGRAH_DIR%\docker-compose.yaml" (
    echo Stopping Dograh AI...
    pushd "%DOGRAH_DIR%"
    docker compose down
    popd
)

echo All services stopped.
pause