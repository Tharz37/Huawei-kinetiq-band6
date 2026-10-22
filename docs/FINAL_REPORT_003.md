# Final Report - Task 003 (2026-10-18)
Build, install and validation - Huawei Band 6 bridge (BLE diagnostic route)

## 1. Build status and APK path - COMPILED (ble-diagnostic only)

- `D:\Engineering\s7\minor\ble-diagnostic\app\build\outputs\apk\debug\app-debug.apk`
  - **7,323,013 bytes** at first build, built with `:app:assembleDebug`, finished after
    resolving two real build errors:
  1. AGP 8.1 Groovy DSL rejected `isMinifyEnabled` -> `minifyEnabled false`.
  2. androidx 2.7.x AAR metadata requires compileSdk >= 34 -> installed
     `platforms;android-34`, set compileSdk 34 (targetSdk stays 33).
  - Three field-run rebuilds followed on the same path (theme fix,
    scanner-correctness fixes, probe/auto-read build; final build ~500 KB
    larger). Every rebuild installed successfully via `adb install -r -t`.
- `android-bridge/` (HMS Health Kit route): still `SOURCE_COMPLETE` - not
  compiled, remains blocked on HMS Core in AppGallery.

## 2. Toolchain (installed this session on the laptop)

| Component | Version |
|---|---|
| JDK | Temurin **17.0.20.101** (winget; JDK 25 was unsuitable for AGP 8.1) |
| Gradle | **8.2** (distribution downloaded; wrapper generated after disabling its online URL probe) |
| Android SDK | cmdline-tools 11.0 (11076708); platforms **android-33 + android-34**; build-tools **33.0.2**; platform-tools **37.0.1**; Google USB driver 11.0.0.0 |
| AGP / Kotlin | 8.1.4 / 1.9.22 |
| sdk.dir | `C:\Users\Tharz\AppData\Local\Android\Sdk` (ble-diagnostic/local.properties, gitignored) |

## 3. APK signing type + package identifier

- Debug-signed (`apksigner verify --print-certs`: `CN=Android Debug`,
  SHA-256 306f586e...cb714d). The debug keystore is the user-profile default
  and is NOT in the repo.
- Package: `com.kinetiq.blediag` vName `0.3.0-diagnostic`, minSdk 31,
  targetSdk 33, compileSdk 34.
- Security inspection: permissions are exactly
  `BLUETOOTH_SCAN(neverForLocation)` + `BLUETOOTH_CONNECT` (+ the androidx
  auto-generated `DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`). No INTERNET,
  location or storage. 423 entries; **no** agconnect/google-services/
  token/keystore/config entries; assets empty. Nothing to conceal; no
  Play-Protect or integrity bypass was needed or used.

## 4. Installation status - INSTALLED (2026-10-18)

- Phone-side steps completed by the user (USB debugging + RSA accept).
  `adb devices`: `4Z01Z3220Y352714336  device  product:Hawkeye_00IN
  model:Nokia_C22 transport_id:1`.
- `adb shell getprop`: Android **13** (SDK 33), model "Nokia C22".
- Installs (streamed, `adb install -r -t`): all **Success**. After the first
  launch crashed with `IllegalStateException: You need to use a
  Theme.AppCompat theme (or descendant) with this activity.` (manifest used
  `Theme.Material.Light.NoActionBar` with an `AppCompatActivity`) - fixed to
  `Theme.AppCompat.DayNight.NoActionBar` and rebuilt; subsequent launches clean.
  Later rebuilds during the field run: scanner-correctness build, probe build.
- Runtime permissions `BLUETOOTH_SCAN` + `BLUETOOTH_CONNECT` granted via
  `pm grant` (visible granted=true in `dumpsys package`).

## 5. Actual BLE scan / GATT results - OBTAINED (2026-10-18)

Full record: `docs/BLE_FIELD_RESULTS.md`; raw evidence:
`docs/BLE_FIELD_RAW_2026-10-18.log`. Highlights:

- Passive unfiltered LOW_LATENCY scan ran at the stack level but received
  **0 advertisements** - root-caused from `dumpsys bluetooth_manager`: the
  band has been **continuously LE-connected to this phone ≈14.4 h** (Health
  app holds the ACL), and a connected peripheral stops general advertising.
  Health's own filtered-scan history saw results yesterday, consistent.
- Band identified from the **system bond list** instead:
  `HUAWEI Band 6-061 / 9C:56:36:B1:50:61 (LE)` - the app's new "2B · Show
  bonded devices" path (scan can't see a connected band).
- **GATT connect: status=0** with Health *still holding its link* (shared
  ACL, multiple GATT clients permitted). Health was never disconnected,
  stopped, unpaired or touched; band settings untouched.
- Service table captured (reduced/unauthenticated exposure): GAP, GATT,
  **0xFE86 LPv2 (FE01 write / FE02 notify)**, Device Information (0x180A),
  HID-over-GATT (0x1812), vendor 0x3802. **No** 0x180D HR, **no** 0x180F
  Battery for this client - the ownership gate, confirmed live.
- Standard-tile reads: Model "HUAWEI", Firmware "1.0.0.1", Manufacturer
  "HUAWEI"; 2A19/2A37/2A38 absent in this exposure.
- Passive FE02 stream: every housekeeping frame **CRC-OK** on the wire
  (parser + assembler live-verified both directions), encrypted-message
  shapes observed on svc 37/15/1(56) - logged as bytes only, nothing claimed.

## 6. Connection failures / Huawei Health

Predicted failure mode (single ATT link held by Health preventing connect)
did NOT materialize: Android shares the one ACL across GATT clients, so a
second client can attach - validated twice (13:18, 13:25) with status=0.
No Health disconnect was needed or requested at any point; had connect
failed with status 133 the app's guidance ("The band may be busy with the
Huawei Health app... Disconnecting Health there is a user decision") remains
in the code for future phones/firmware.

## 7. Protocol stages implemented and tested

- LPv2 wire parser (0x5A frame detect, CRC-16/XMODEM verify, sliced-frame
  labelling) - CRC previously verified against the documented example
  (0xf13b); **now additionally live-verified against on-wire band frames**
  (every notification parsed `crc=OK`) and **outbound frames assembled and
  accepted** (write status=0, replies parsed).
- **Plaintext probes executed**: 1/2 SupportedServices and 1/3
  SupportedCommands written to FE01, band replied within ~300 ms with
  tag-0x7f result code **100010** (0x186aa; outside the documented list,
  unconfirmed meaning) - capability enumeration remains **gated**,
  matching research. Auth/bond/settings commands were never sent.
- Encrypted-message layout observed live: tags 124(0x7c)=flag, 125(0x7d)=
  len-16 field, 126(0x7e)=len-16 ciphertext on the periodic Health sync
  (svc 37/15/1/56). Not decrypted; not presented as data.
- No handshake/auth implemented; secrets remain unverified/unavailable for
  current firmware (documented in RESEARCH_LPV2.md section 5).
- Receiver: additive stress metric + BLE-probe guard implemented this task.

## 8. Genuine extraction status (verbatim, per metric)

| Metric | Status |
|---|---|
| Heart rate | **NOT OBTAINED** - HR service not exposed to unauthenticated clients; data frames crypto-gated; nothing decoded |
| Stress | **NOT OBTAINED** - pipeline ready, no authentic source yet |
| SpO2 | **NOT OBTAINED** |

Device-*identity* strings (Model/Manufacturer "HUAWEI", Firmware "1.0.0.1")
ARE genuine decoded values read directly off the band; they may be
cross-checked against the band's Settings→About screen. They are not health
metrics. No value may be claimed as a health reading until decoded AND
cross-checked against the band's on-watch display / Huawei Health.

## 9. Website integration status

Working (`python -m unittest discover -s backend` -> **19/19 OK**; dashboard
JS parses under `node --check`). Additions: stress row with documented
0-100 scale + mode pill, stress mirrors the same no-fabrication rules;
unavailable = "--"/NO DATA, never zero or mock. Receiver still refuses an
empty BLE probe so it can't clobber a genuine reading. Transport remains
local-only (LAN + token); nothing is exposed publicly.

## 10. Next actions (Phase 4 decision point)

Done in Phase 3: install, scan (root-caused zero-advert situation), bonded
connect, GATT dump, standard reads, passive capture, plaintext probes,
clean disconnect - twice, fully documented.

1. Optional cross-check: band display -> Settings -> About. Firmware should
   read `1.0.0.1`; model HUAWEI (Band 6).
2. Health-metric extraction (HR/SpO2/stress) is now purely blocked on the
   authenticated LPv2 session secret. Legitimate options, in order:
   - **HMS Health Kit bridge** (`android-bridge`): still the cleanest path
     but blocked on this phone (no HMS Core in AppGallery).
   - **BT HCI snoop capture** — EXECUTED (2026-10-18, user-consented):
     armed via `svc bluetooth` restart (`mSnoopLogSettingAtEnable = full`),
     pulled through `adb bugreport` (direct path is root-protected). The
     Health re-association handshake was captured completely
     (`docs/BTSNOOP_RESULTS.md`, raw `docs/BTSNOOP_RAW_2026-10-18.log`):
     documented link-params frame byte-exact, 1/19 digest exchange, 1/45 →
     100000 OK, encrypted session with the documented IV layout. Verdict of
     offline testing (`tools/crypto_offline_test.py`, zyv port first
     validated against an independently published Band 8 vector): **no**
     public-constant digest variant and **no** public-material AES key
     candidate matches this firmware's bond-derived crypto. Health-metric
     extraction via a direct BLE session is CLOSED for this band state; HCI
     logging can be disabled by the user at any time (recommend toggling it
     off + restarting Bluetooth when done).
   - **Re-pairing capture**: would deliberately unpair the band from Health
     to observe the pairing secret -> violates the never-unpair rule;
     only with explicit consent, and other options first. Not recommended.
     Still not performed; it remains the only route that could produce a
     fresh normal-auth bond, at the cost of Health's pairing.
3. On any redirect: the app's full field pipeline (2B bonded connect ->
   auto-reads -> probe) is reproducible any time via USB; diagnostics log
   is at `Android/data/com.kinetiq.blediag/files/kinetiq_ble_diag.log`.
4. Master report for the whole project (findings, closed paths, and every
   remaining non-HMS route with steps): `docs/FINAL_REPORT_PROJECT.md`.

## Milestone ledger (accurate statuses)

| Milestone | android-bridge (HMS) | ble-diagnostic (BLE) |
|---|---|---|
| SOURCE_COMPLETE | yes | yes |
| COMPILED | no | **yes** (this task) |
| INSTALLED | no | **yes** (2026-10-18, Nokia C22) |
| DEVICE_TESTED | no | **yes** (2026-10-18: connect, GATT dump, reads, passive capture, probes - see docs/BLE_FIELD_RESULTS.md) |
| REAL_DATA_VERIFIED | no | no (device identity verified; HR/SpO2/stress NOT OBTAINED - auth-gated) |

Former install blockers resolved this Phase: USB debugging was enabled by
the user, RSA accepted, streamed installs succeeded; `dumpsys` route for the
initial empty `adb devices` mystery outdated - phone enumerates normally now.
