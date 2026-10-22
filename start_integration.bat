@echo off
setlocal
title KinetiQ + Huawei Band 6 Integration
cd /d "%~dp0"

set "ADB=C:\Users\Tharz\AppData\Local\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"
set "TOKEN=poc-demo"

echo ==================================================
echo  KINETIQ mirror + Huawei Band 6 (genuine values)
echo ==================================================
echo.

echo [1/4] KinetiQ receiver on http://127.0.0.1:8000 ...
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2; if ($r.ok) { exit 0 } } catch {}; exit 1"
if errorlevel 1 (
  start "KinetiQ Receiver" cmd /k "set KINETIQ_TOKEN=%TOKEN% && python backend\kinetiq_receiver.py"
  powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(20); while((Get-Date) -lt $deadline){ try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 1; if($r.ok){ exit 0 } } catch {}; Start-Sleep -Milliseconds 300 }; exit 1"
  if errorlevel 1 (
    echo    ERROR: receiver did not start. Is python on PATH?
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
  echo             (Demo still runs; the phone cannot post until it is set.)
) else (
  echo    OK - the phone can reach http://127.0.0.1:8000 .
)

choice /m "Keep the phone screen awake while charging (svc power stayon true)"
if errorlevel 2 goto afterstay
"%ADB%" shell svc power stayon true
echo    screen-on enabled - revert with stop_demo.bat
:afterstay

echo.
echo [3/4] Starting bridge + mirror (upstream app.py untouched) ...
start "KINETIQ mirror + Band 6" cmd /k "python kinetiq-integration\run_stack.py"

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
echo   2. Phone: KinetiQ Streamer accessibility service toggled on.
echo   3. Mirror: http://127.0.0.1:8777/
echo      The HUAWEI BAND 6 chip shows stress + relay freshness.
echo.
echo Upstream KinetiQ stays read-only:
echo   git -C external\KinetiQ status --porcelain   (must print nothing)
echo Close the "KINETIQ mirror + Band 6" window to stop the bridge/mirror.
echo stop_demo.bat removes the USB tunnel; the receiver window closes manually.
echo.
pause
