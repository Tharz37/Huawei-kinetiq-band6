@echo off
setlocal
title KinetiQ + Huawei Band 6 Integration
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

echo ==================================================
echo  KINETIQ mirror + Huawei Band 6 (genuine values)
echo ==================================================
echo.

echo [1/4] KinetiQ receiver on http://127.0.0.1:8000 ...
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if ($r.ok) { exit 0 } } catch {}; exit 1"
if errorlevel 1 (
  start "KinetiQ Receiver" cmd /k "set KINETIQ_TOKEN=%TOKEN% && %PY% backend\kinetiq_receiver.py"
  powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(20); while((Get-Date) -lt $deadline){ try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){ exit 0 } } catch {}; Start-Sleep -Milliseconds 300 }; exit 1"
  if errorlevel 1 (
    echo    ERROR: receiver did not start. Is Python 3 installed?
    pause
    exit /b 1
  )
  echo    receiver started.
) else (
  echo    receiver already running - reusing it.
)

echo.
echo [2/4] USB tunnel for the phone (adb reverse tcp:8000) ...
"%ADB%" reverse tcp:8000 tcp:8000 >nul 2>&1
if errorlevel 1 (
  echo    WARNING: no USB tunnel. Plug the phone in with USB debugging on.
  echo             Demo still runs; the phone cannot post until it is set.
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
echo    display-awake enabled for AC/USB/wireless charging - stop_demo.bat restores it.
:afterstay

echo.
echo [3/4] Home Edition mirror on http://127.0.0.1:8777 ...
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:8777/' -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "KINETIQ mirror + Band 6" cmd /k "%PY% kinetiq-integration\mirror_band.py --strict-port"
  echo    mirror starting - upstream app.py untouched, no bridge.
) else (
  echo    mirror already running - reusing it.
)

echo.
echo [4/4] Waiting for http://127.0.0.1:8777 ...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(25); while((Get-Date) -lt $deadline){ try { Invoke-WebRequest -Uri 'http://127.0.0.1:8777/' -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0 } catch {}; Start-Sleep -Milliseconds 400 }; exit 1"
if errorlevel 1 (
  echo    WARNING: the mirror did not answer on 8777.
  echo             Check the "KINETIQ mirror + Band 6" window for errors.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8777/

echo.
echo Running. For genuine values:
echo   1. Phone: open Huawei Health and stay on the homepage.
echo   2. Phone: KinetiQ Streamer streaming ON (accessibility + notification access).
echo   3. Mirror: http://127.0.0.1:8777/
echo      Home Edition is the default; the partner's original UI is /classic.
echo      The Home Edition shows genuine HR, SpO2, stress and steps.
echo      The classic view's HUAWEI BAND 6 chip adds stress + relay freshness.
echo.
echo Upstream KinetiQ stays read-only:
echo   git -C external\KinetiQ status --porcelain   (must print nothing)
echo Close the "KINETIQ mirror + Band 6" window to stop the mirror.
echo stop_demo.bat stops the tunnel watcher and removes the USB tunnel; windows close manually.
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
