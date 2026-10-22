@echo off
setlocal
title KinetiQ Demo Launcher
cd /d "%~dp0"

set "ADB=C:\Users\Tharz\AppData\Local\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"
set "TOKEN=poc-demo"

echo ============================================
echo  KinetiQ demo - local startup (no internet)
echo ============================================
echo.

echo [1/3] Receiver on http://127.0.0.1:8000 ...
netstat -ano | findstr ":8000 " | findstr LISTENING >nul 2>&1
if errorlevel 1 (
  start "KinetiQ Receiver" cmd /k "set KINETIQ_TOKEN=%TOKEN% && python backend\kinetiq_receiver.py"
  timeout /t 3 /nobreak >nul
) else (
  echo    receiver already running - reusing it.
)

echo.
echo [2/3] USB tunnel for the phone (adb reverse tcp:8000) ...
"%ADB%" reverse tcp:8000 tcp:8000
if errorlevel 1 (
  echo    WARNING: could not set up the USB tunnel.
  echo    - Is the phone plugged in with USB debugging allowed?
  echo    - LAN fallback: use http://LAPTOP-IP:8000/api/wearable inside the app.
  goto afterstay
)
echo    OK - the phone can now reach http://127.0.0.1:8000 .
choice /m "Keep the phone screen awake while charging (svc power stayon true)"
if errorlevel 2 goto afterstay
"%ADB%" shell svc power stayon true
echo    screen-on enabled - revert with stop_demo.bat
:afterstay

echo.
echo [3/3] Opening dashboard ...
start "" http://127.0.0.1:8000/

echo.
echo Done. Demo steps:
echo   1. Phone: open Huawei Health and stay on the homepage.
echo   2. Phone: open KinetiQ Streamer and toggle it on.
echo   3. Watch this laptop's dashboard tiles update.
echo.
pause
