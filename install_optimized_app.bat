@echo off
setlocal EnableExtensions
title KinetiQ Streamer - Build, Grant and Install
cd /d "%~dp0"

set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
if not exist "%ADB%" set "ADB=adb"
if not defined JAVA_HOME set "JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot"
set "GRADLE_BIN=%USERPROFILE%\Android-Dev\gradle-8.2\bin\gradle.bat"
if not exist "%GRADLE_BIN%" set "GRADLE_BIN=gradle"
set "APK=kinetiq-streamer\app\build\outputs\apk\debug\app-debug.apk"
set "ACCESSIBILITY=com.kinetiq.streamer/com.kinetiq.streamer.StreamService"
set "NOTIFICATION=com.kinetiq.streamer/com.kinetiq.streamer.HealthNotificationService"

echo ====================================================
echo  Building and Installing Optimized KinetiQ Streamer
echo ====================================================
echo.

echo [1/6] Checking connected Android device...
for /f "delims=" %%s in ('"%ADB%" get-state 2^>nul') do set "DEVICE_STATE=%%s"
if /i not "%DEVICE_STATE%"=="device" (
    echo [ERROR] No authorized Android device detected over ADB.
    echo State: %DEVICE_STATE%
    echo.
    echo Unlock the phone, enable USB debugging, and tap "Allow" on the
    echo "Allow USB debugging?" prompt. Then run this script again.
    pause
    exit /b 1
)
echo Phone detected and authorized.

if not exist "%JAVA_HOME%\bin\java.exe" (
    echo [ERROR] JDK 17 was not found at:
    echo   %JAVA_HOME%
    pause
    exit /b 1
)
if not exist "%GRADLE_BIN%" (
    echo [ERROR] Gradle was not found at:
    echo   %GRADLE_BIN%
    pause
    exit /b 1
)

echo.
echo [2/6] Running unit tests and compiling APK with Gradle...
pushd kinetiq-streamer
call "%GRADLE_BIN%" testDebugUnitTest assembleDebug --console=plain
if errorlevel 1 (
    popd
    echo [ERROR] Gradle build or unit tests failed. Check the output above.
    pause
    exit /b 1
)
popd

if not exist "%APK%" (
    echo [ERROR] APK was not produced at %APK%
    pause
    exit /b 1
)

echo.
echo [3/6] Installing APK onto the phone...
"%ADB%" install -r -d "%APK%"
if errorlevel 1 (
    echo [ERROR] ADB install failed.
    echo If the error is INSTALL_FAILED_UPDATE_INCOMPATIBLE, uninstall the
    echo old copy manually and run this script again:
    echo   "%ADB%" uninstall com.kinetiq.streamer
    pause
    exit /b 1
)
echo Installed.

echo.
echo [4/6] Enabling the accessibility service without removing others...
set "EXISTING_SERVICES="
for /f "delims=" %%s in ('"%ADB%" shell settings get secure enabled_accessibility_services 2^>nul') do set "EXISTING_SERVICES=%%s"
if "%EXISTING_SERVICES%"=="null" set "EXISTING_SERVICES="
set "NEW_SERVICES=%EXISTING_SERVICES%"
echo %EXISTING_SERVICES% | findstr /C:"%ACCESSIBILITY%" >nul
if not errorlevel 1 goto accessibility_ready
if defined NEW_SERVICES (
    set "NEW_SERVICES=%NEW_SERVICES%:%ACCESSIBILITY%"
) else (
    set "NEW_SERVICES=%ACCESSIBILITY%"
)
"%ADB%" shell settings put secure enabled_accessibility_services "%NEW_SERVICES%"
:accessibility_ready
"%ADB%" shell settings put secure accessibility_enabled 1
"%ADB%" shell settings get secure enabled_accessibility_services | findstr /C:"%ACCESSIBILITY%" >nul
if errorlevel 1 (
    echo [WARN] Could not verify accessibility access through the shell.
    echo        Open the app and use "Open accessibility settings" if needed.
) else (
    echo Accessibility service enabled.
)

echo.
echo [5/6] Granting notification-listener access...
"%ADB%" shell cmd notification allow_listener "%NOTIFICATION%"
"%ADB%" shell settings get secure enabled_notification_listeners | findstr /C:"%NOTIFICATION%" >nul
if errorlevel 1 (
    echo [WARN] Notification access was not granted automatically on this ROM.
    echo        Opening the notification-access screen; enable KinetiQ Streamer there.
    "%ADB%" shell am start -a android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS >nul 2>&1
) else (
    echo Notification listener access granted.
)

echo.
echo [6/6] Configuring USB reverse tunnel and opening the app...
"%ADB%" reverse tcp:8000 tcp:8000
if errorlevel 1 (
    echo [WARN] Could not create "adb reverse tcp:8000 tcp:8000".
    echo        The phone and laptop may have disconnected; reconnect USB.
) else (
    echo USB reverse tunnel: phone 127.0.0.1:8000 -^> laptop 8000
)
"%ADB%" shell am start -n com.kinetiq.streamer/.MainActivity >nul 2>&1

echo.
echo ====================================================
echo  SUCCESS: APK installed and permissions configured.
echo ====================================================
echo Next, in the app:
echo  1. Confirm the endpoint and token, then tap Save.
echo  2. Tap Start streaming.
echo  3. Open Huawei Health and leave its home screen visible.
echo.
echo The screen watchdog refreshes every 4 seconds. Health status-bar
echo notifications cover background updates when notification access is ON.
echo Optional display-awake for the demo:
echo   "%ADB%" shell settings put global stay_on_while_plugged_in 7
echo   (revert with: "%ADB%" shell settings put global stay_on_while_plugged_in 0)
echo.
pause
