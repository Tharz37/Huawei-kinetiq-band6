@echo off
setlocal
title KinetiQ Demo Teardown
cd /d "%~dp0"

set "ADB=C:\Users\Tharz\AppData\Local\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"

echo Reverting demo-time phone settings ...
"%ADB%" shell svc power stayon false
"%ADB%" reverse --remove tcp:8000
echo Done: screen-on policy restored, USB tunnel removed.
echo (The receiver window can be closed manually.)
pause
