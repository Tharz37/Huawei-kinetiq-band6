# KinetiQ BLE Diagnostic — Band 6 (Task 002)

Minimal Android 13 Kotlin app for passive discovery of the Huawei Band 6 and
GATT investigation. It contains NO health-claim logic: anything a
characteristic emits is hex-annotated and CRC-verified against the documented
Huawei Link Protocol v2 wire format (see `../docs/RESEARCH_LPV2.md`), and no
value is ever presented as a heart-rate/SpO₂ reading.

## What it does (in user order)

1. Requests Android 13 Bluetooth permissions (`BLUETOOTH_SCAN`
   `neverForLocation` + `BLUETOOTH_CONNECT`).
2. Passive scan: name, address, RSSI, advertised service UUIDs.
3. User explicitly taps the Band 6 row (only the selected device is written
   to the local diagnostic log).
4. Connect happens only when the user presses Connect (manual, no autoConnect).
5. GATT dump: services, characteristics, properties, CCCDs; subscribes
   passively to non-SIG notification characteristics; the Heart Rate Service
   (2A37) is deliberately not subscribed — its bytes are unverified vendor
   data and may not be health values.
6. "Read documented standard characteristics": Battery (2A19), Model (2A24),
   Firmware (2A26), Manufacturer (2A29), Body Sensor Location (2A38),
   Heart Rate Measurement (2A37 — presence/permission is the interesting bit).
7. Disconnect & cleanup button; onDestroy does the same automatically.

## Safety boundaries (enforced in code, not just docs)

- No unpairing, no resets, no band settings writes anywhere in the code.
- The band's single ATT link may be held by the Huawei Health app; if the
  connect fails (often status 133), the app says so and leaves the choice of
  disconnecting Health to the user — it is never done automatically.
- Local log: `Android/data/com.kinetiq.blediag/files/kinetiq_ble_diag.log`,
  app-scoped storage, no network use at all in this app.

## Protocol capability (passive LPv2)

`ble/Lpv2.kt` CRC-16/XMODEM-verifies observed frames
(`0x5A LLLL 0x00 SS CC TLVs CRC16`) and labels sliced/multi frames without
decoding them. Deeper handshake stages are NOT implemented in this
diagnostic app — the documented next steps (SupportedServices/SupportedCommands
probes, challenge-response auth attempts) live in `docs/RESEARCH_LPV2.md` §6
and require a separate, explicitly consented build phase.

## Build & install (verified 2026-10-18)

Toolchain actually used: JDK 17 (Temurin 17.0.20.101), Gradle 8.2, AGP 8.1.4,
Kotlin 1.9.22, Android SDK build-tools 33.0.2 (+ platform 33/34, platform-tools
37.0.1). compileSdk 34 (required by androidx 2.7.x), targetSdk 33.

```sh
cd ble-diagnostic
.\gradlew :app:assembleDebug        # wrapper pins Gradle 8.2
```

Verified result:

- `app/build/outputs/apk/debug/app-debug.apk` (7,323,013 bytes)
- `aapt dump badging`: package `com.kinetiq.blediag`, minSdk 31,
  targetSdk 33; permissions exactly `BLUETOOTH_SCAN (neverForLocation)` +
  `BLUETOOTH_CONNECT` (+ androidx's auto `DYNAMIC_RECEIVER_NOT_EXPORTED`
  guard). No INTERNET, no location, no storage.
- `apksigner verify --print-certs`: stamp `CN=Android Debug` — normal debug
  signing; the auto-keystore lives in the user profile and is never committed.
- Embedded-secret scan: no agconnect/google-services/config/token/keystore
  entries; no assets.

Install once USB debugging is on (Settings → About → build number ×7 →
Developer options → USB debugging, then replug and accept the RSA prompt):

```sh
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

No HMS dependency, no agconnect-services.json required.
