@echo off
setlocal
title KinetiQ Demo Teardown
cd /d "%~dp0"

set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"

echo Reverting demo-time phone settings and watchers ...
powershell -NoProfile -Command "$self=$PID; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $self -and $_.Name -match 'powershell' -and $_.CommandLine -like '*watch_adb_reverse.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
"%ADB%" shell svc power stayon false
"%ADB%" shell settings put global stay_on_while_plugged_in 0
"%ADB%" reverse --remove tcp:8000
echo Done: tunnel watcher stopped, display-awake policy restored, USB tunnel removed.
echo (The receiver and mirror windows can be closed manually.)
pause
