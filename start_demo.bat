@echo off
setlocal EnableExtensions
title KinetiQ Demo Launcher
cd /d "%~dp0"

set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"
set "TOKEN=poc-demo"

call :find_python
if not defined PY (
    echo.
    echo ERROR: Python 3 was not found. Install Python 3 or add it to PATH.
    pause
    exit /b 1
)

echo ============================================
echo  KinetiQ demo - local startup (no internet)
echo ============================================
echo.

echo [1/4] Receiver on http://127.0.0.1:8000 ...
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if ($r.ok) { exit 0 } } catch {}; exit 1"
if errorlevel 1 (
    start "KinetiQ Receiver" cmd /k "set KINETIQ_TOKEN=%TOKEN% && %PY% backend\kinetiq_receiver.py"
    powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(20); while((Get-Date) -lt $deadline){ try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){ exit 0 } } catch {}; Start-Sleep -Milliseconds 300 }; exit 1"
    if errorlevel 1 (
        echo    ERROR: the receiver did not start. Check the Python window.
        pause
        exit /b 1
    )
    echo    receiver started.
) else (
    echo    receiver already running - reusing it.
)

echo.
echo [2/4] USB tunnel and auto-healing watcher ...
"%ADB%" reverse tcp:8000 tcp:8000
if errorlevel 1 (
    echo    WARNING: no USB tunnel yet. The watcher will retry when the phone appears.
    echo             LAN fallback: use http://LAPTOP-IP:8000/api/wearable in the app.
) else (
    echo    OK - the phone can reach http://127.0.0.1:8000 .
)
powershell -NoProfile -Command "$self=$PID; $p=Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $self -and $_.CommandLine -like '*watch_adb_reverse.ps1*' }; if ($p) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    start "KinetiQ ADB tunnel watch" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch_adb_reverse.ps1"
) else (
    echo    tunnel watcher already running.
)

choice /c YN /n /t 8 /d N /m "Keep the phone display awake while charging? [Y/N; default N in 8s] "
if errorlevel 2 goto afterstay
"%ADB%" shell settings put global stay_on_while_plugged_in 7
echo    display-awake enabled for AC/USB/wireless charging.
echo    stop_demo.bat restores it.
:afterstay

echo.
echo [3/4] Opening dashboard ...
start "" http://127.0.0.1:8000/

echo.
echo [4/4] Demo steps:
echo   1. Phone: open Huawei Health and stay on the homepage.
echo   2. Phone: open KinetiQ Streamer and confirm streaming is ON.
echo   3. Watch this laptop's dashboard update from genuine Health UI readings.
echo.
echo The tunnel watcher reconnects adb reverse within 3 seconds after a USB bump.
echo If the USB link is unavailable, use the LAN URL shown in the app instead.
echo.
pause
exit /b 0

:find_python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY=python"
    )
)
exit /b 0
