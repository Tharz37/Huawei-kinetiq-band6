# KinetiQ Android Bridge — Huawei Band 6 → Laptop

Native Kotlin app (target Android 13, API 33; minSdk 26) that reads genuine
heart-rate / SpO₂ via Huawei Health Service Kit and POSTs schema-v1 JSON to
the laptop receiver. No mock data exists in this app — unavailable readings
are shown, never invented.

## 1. Prerequisites

- Android Studio (Hedgehog or newer) with Android SDK 33 + JDK 17.
- A Huawei Developer account + AppGallery Connect (AGC) project.
- Phone with HMS Core ≥ 5.0.4.300 and Huawei Health app ≥ 11.0.0.512,
  paired with the Band 6 and syncing.
- This laptop's receiver running (`backend/kinetiq_receiver.py`) with its
  shared token, on the same trusted Wi-Fi.

## 2. Console configuration (all in AppGallery Connect)

1. Create project → Add app (package `com.kinetiq.bridge`), note the App ID.
2. Generate the signing fingerprint:
   `keytool -genkey -v -keystore kinetiq.jks -keyalg RSA -keysize 2048 -validity 36500`
   `keytool -list -v -keystore kinetiq.jks` → copy SHA-256.
   Register the SHA-256 under Project settings → App information.
   (Keep `kinetiq.jks` and passwords OUT of git — see `.gitignore`.)
3. Download `agconnect-services.json` into `android-bridge/app/`.
   Put your App ID in `AndroidManifest.xml` (`appid=...` metadata).
4. Enable **Health Kit** for the app (Develop → Health Kit). Request scopes:
   heart-rate read, **real-time heart-rate**, SpO₂ read.
   Scope approval can take time — the app surfaces "permission denied" until
   approval + user authorisation both succeed.
5. Pin the SDK version: set `healthKitVersion` in `gradle.properties` to the
   latest from the official version history. The build fails fast if left as
   a placeholder. Dependency used: `com.huawei.hms:health:<version>` from
   `https://developer.huawei.com/repo/` (per Huawei's official demo README).

## 3. Build & install

```sh
cd android-bridge
./gradlew :app:assembleDebug        # or assembleRelease with your keystore
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

> Local status: no Android SDK/Gradle is installed on this laptop, so the APK
> has NOT been compiled here (see docs/FINAL_REPORT.md). Build on a machine
> with Android Studio.

## 4. Real-device acceptance test

1. Same Wi-Fi on phone + laptop. Start receiver: `python kinetiq_receiver.py`.
   Note the laptop's LAN IP (e.g. `192.168.1.20`).
2. In the app: enter `http://<laptop-LAN-IP>:8000` + shared token → Save.
3. Tap **Authorise** → grant Health Kit scopes in the Huawei flow.
4. Wear the Band 6 snugly → **Start heart-rate** → a genuine BPM appears.
5. Confirm the laptop dashboard shows the same value + timestamp.
6. **Query latest SpO₂** only after an on-demand SpO₂ measurement on the band
   + Huawei Health sync. Expect `unsupported/unavailable` otherwise — that is a
   correct result, not a bug.

## 5. Code map

- `health/HuaweiHealthDataSource.kt` — auth intent, HR + SpO₂ reads
  (verified `SettingController`/`DataController`/`ReadOptions` signatures;
  `[V1]`/`[V2]` mark the two points to confirm against the console/SDK).
- `net/BridgeUploader.kt` — token-authed upload, retries, no secret logging.
- `MainActivity.kt` + `res/layout/activity_main.xml` — status screen.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Auth screen error 50030-ish / no network | Phone offline; HMS sign-in needs internet once |
| HEALTH_APP_NOT_AUTHORISED | Enable data-sharing in Huawei Health app privacy settings |
| Permission denied persists | Scopes not approved in console, or wrong fingerprint/package |
| Stale/historical HR | Band not worn / Health app not synced; values stay honest |
| SpO₂ unsupported | Band 6 SpO₂ needs on-demand measurement + rest; scope may be unapproved |
| Upload HTTP 401 | Token mismatch between app and `backend/config.json` |
| Upload fails | Same Wi-Fi? Laptop firewall allowing inbound TCP 8000 (Private)? |
