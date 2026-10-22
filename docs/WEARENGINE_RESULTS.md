# Wear Engine dissection (2026-10-18) — the official channel, probed to its gate

## Motivation

User proposal: *"dissect the Health app to understand how they take the data, and
by the same logic, we will make one for us."* This document records the complete
dissection of Huawei Health 14.1.2.320 on the Nokia C22 (Android 13) and the
attempted use of the one sanctioned door it exposes.

## 1. Dissection results — every inter-app surface of com.huawei.health

Method: `dumpsys package com.huawei.health` (full component map), APK pull
(105,476,789 bytes base.apk) + decompressed-string scan for `content://` URIs,
and empirical `content query` probes from adb shell (uid 2000).

| Surface | Guard | Verdict |
|---|---|---|
| `com.huawei.health.data.access.provider` (HwContentProvider — the main data DB) | **not exported** | closed |
| `com.huawei.health.provider.HealthAppProvider` | **not exported** | closed |
| `ToHmContentProvider` (`content://com.huawei.health.provider.ToHmContentProvider`) | requires `com.huawei.health.permission.TO_HM_READ_PROVIDER` (`prot=normal` — auto-granted if declared!) | **readable by any app that declares it** — but provider returned only validation errors on root URI; data tables not identified; flagged for future probe |
| Notification providers | `notifypermission.read/write` = `signature\|privileged` | closed (Huawei-signed only) |
| `com.huawei.health.thirdprovider`, `com.huawei.healthcloud.health.provider` (root URI) | none — answered "No result found" | open but empty at root |
| Deep links (`/thirdPartService`, `/fitnesspage`, `/basicHealth`, …) | partner appId gating (Huawei open-platform) | closed |
| `content://com.huawei.healthcloud.health.provider/wear_device_state` | **none — answered from adb**: `boundedDevice=true, boundedWearableDevice=true, connectedWearableDevice=true` | **OPEN — this is wearEngine** |

Scan of Health's own dex literals found `…/wear_device_state/wearEngine` — the
official **Wear Engine** surface (Huawei's sanctioned SDK for third-party apps to
talk to their wearables *through* the Health app; needs no HMS Core, which is why
it exists on a non-Huawei phone).

## 2. SDK integration (all verified empirically)

- Artifact `com.huawei.hms:wearengine` on `https://developer.huawei.com/repo/`
  (repo serves **no** `maven-metadata.xml` — dynamic versions fail; a concrete
  pin is required). Published Android versions found by POM probing:
  `5.0.0.300`, `5.0.1.300`, **`5.0.3.300` (newest)**.
- API mapped by `javap` from the AAR (no guessing):
  `HiWear.getDeviceClient/getAuthClient/getSensorClient(ctx)`;
  `DeviceClient.getBondedDevices(): Task<List<Device>>`;
  `AuthClient.requestPermission(AuthCallback, Permission…)` with
  `Permission.{DEVICE_MANAGER, SENSOR, MOTION_SENSOR, WEAR_USER_STATUS,
  DEVICE_SN, NOTIFY}`;
  `SensorClient.getSensorList(Device)` / `asyncRead(Device, Sensor,
  SensorReadCallback)` / `stopAsyncRead`;
  sensor types include `TYPE_HR` (computed heart rate), `TYPE_PPG` (raw, with
  GREEN/IR/RED channels), `TYPE_ECG`, `TYPE_ACC`, `TYPE_GYRO`.
- Android 13 requirement: `<queries><package android:name="com.huawei.health"/></queries>`.
- Probe built into the diagnostic app v0.4.0 (button 6),
  `WearEngineProbe.kt` — sequence: devices → consent dialog → sensor list →
  auto-stopping live reads.

## 3. Result — the gate

Every `getBondedDevices()` call fails in ~33 ms with
`WearEngineException: code=12 "Internal error"`, thrown when the Health-side
service call raises `IllegalStateException` (decoded from the SDK bytecode:
code 6 = RemoteException, 12 = IllegalStateException from Health, 14 = Health
too old — not our case; Health 14.1.2.320 is current).

Official meaning of 12: `ERROR_CODE_INTERNAL_ERROR` ("internal result code —
requires Huawei Wear Engine contacts"). The HarmonyOS error table names the
practical cause: **`1008500004 — "App has not applied for the Wear Engine
service"`**. Health validates the calling app (package name + signing-certificate
fingerprint + app id) against **AppGallery Connect** before serving any call.
Our diagnostic app has no AGC identity → instant rejection, before the consent
dialog stage. Full-logcat capture shows no Health-side log; the check is silent.

## 4. Verdict

- The dissection is complete and evidence-backed: Health's own data layer is
  closed to third parties; the single sanctioned channel is **wearEngine**, and
  it is **live on this phone** — but it requires a Huawei developer account, an
  AppGallery Connect app registration (package `com.kinetiq.blediag`, debug-keystore
  SHA-256 fingerprint), the app-id meta-data, and signing the Wear Engine
  service agreement. Sensor/health scopes (PPG, user status) additionally carry
  enterprise-developer language in Huawei's docs (`WEAR_USER_STATUS`: enterprise
  only) — approval for an individual student app is **not guaranteed**.
- Consequently, for the KinetiQ demo the wearEngine route is recorded as
  **"official but gated"** (route G). The working path remains the
  Health-UI relay: screen-read PoC (heart-rate 77 bpm verified live) →
  AccessibilityService streaming app (Tier 2).
- The `TO_HM_READ_PROVIDER` normal-permission read remains the one open
  possibility for future exploration (requires deeper schema discovery inside
  the Health APK).

## 5. Status update (same day): AGC identity live, service application submitted

Following the code-12 gate, the project registered on Huawei's developer
platform (individual account):

- AGC project `KinetiQ`, app `KinetiQ BLE Diag`, package `com.kinetiq.blediag`,
  **App ID `119406053`**, debug-keystore SHA-256 fingerprint registered.
- App identity wired into the diagnostic app: `agconnect-services.json` in
  `ble-diagnostic/app/`, manifest meta-data `com.huawei.hms.client.appid`
  (`appid=119406053`).
- Re-test result: **code 12 → code 8 (`Scope unauthorized`)** — Health now
  recognizes and accepts the caller; only the service scope is missing.
- Wear Engine service application submitted (Console → App services →
  Wear Engine, status **"To be reviewed"**, 2026-10-18 16:21): Android App,
  device support "all", no wearable-side app, permission **Basic device
  information (DEVICE_MANAGER)** only; data-permission and user-authorization
  PDF/XLSX documents in `docs/wearengine_application/`.
- Individual accounts may apply for only the two basic scopes (device info,
  notifications); **Sensor (PPG/heart rate) is enterprise-only** — live
  metric streaming via wearEngine therefore remains likely out of reach for
  this project's account type, per Huawei's own documentation.
- Expected review time per docs: 1–2 weeks. University-presentation-critical
  functionality does **not** depend on this approval (Route B carries the demo).

## 6. Artifacts

- `ble-diagnostic/app/src/main/java/com/kinetiq/blediag/WearEngineProbe.kt`
- `ble-diagnostic/app/build.gradle` (wearengine 5.0.3.300), `settings.gradle`
  (Huawei maven last), `AndroidManifest.xml` (`<queries>`)
- SDK AAR inspected at `%TEMP%\opencode\wearengine_aar\`
- Full-logcat capture of a code-12 failure: `%TEMP%\opencode\full_logcat.txt`
