# Final Report — Task 002 (2026-10-18)
Huawei Band 6 BLE investigation + parallel laptop/website pipeline

## 1. Research outcome (docs/RESEARCH_LPV2.md — completed)

Both required wiki pages read, plus the referenced primary sources
(zyv/huawei-lpv2 wiki + code, Bluetooth-GATT page). Documented:

- Wire format `0x5A LLLL 0x00 BODY CRC16`, CRC-16/XMODEM — **our CRC
  implementation independently reproduces the wiki's example packet CRC
  (0xf13b)** and the LLLL= len(BODY)+1 convention. Wire decode = verified.
- Command format `SS CC TLVs`, VarInt lengths, nested TLVs (TAG MSB).
- Handshake: link params (1/1) → challenge-response auth (1/19) → bond
  params (1/15) → optional bond (1/14) → encrypted exchange (AES-128-CBC
  or GCM, universal tags 124/125/126/127; ack `00 01 86 A0`).
- Authentication material: HMAC-SHA256 double hash with a 16-byte 2022
  reverse-engineered DIGEST_SECRET, bonding key derived from a 32-byte
  SECRET_KEY mixed with the band MAC. Unverified for 2026 firmware.
- **Documented uncertainty:** NO command in Fitness service (7) is
  documented for streaming real-time heart rate (HRMonitorState=23 exists
  but is marked "permission denied?" — status query only). The
  quantified-student wiki explicitly leaves HR/stress command discovery as
  future work starting from DeviceConfig 1/2 (SupportedServices) and 1/3
  (SupportedCommands). Documented risk: sliced/multi packets unsupported by
  the reference implementation; SECRET/DIGEST constants may have rotated;
  MAC-dependence of bonding keys; device-specific "normal mode".
- **Compatibility risk for the milestone:** the band's standard Heart Rate
  Service characteristic 0x2A37 was documented "Not permitted" via plain BLE
  (zyv). Cross-checks pending on our device; per the task, no HR value will
  ever be claimed from unauthenticated notification bytes.

## 2. BLE diagnostic app (`ble-diagnostic/` — source complete, NOT compiled)

Kotlin/Android 13 (minSdk 31/target 33), no HMS dependency. Passive-first:
permissions `BLUETOOTH_SCAN(neverForLocation)` + `BLUETOOTH_CONNECT` →
scan list (name, address where available, RSSI, advertised service UUIDs) →
explicit row tap identifies the band → Connect only on button press → GATT
service/characteristic dump → passive notification hex annotation (no SIG
2A37 subscription — unauthenticated vendor bytes are deliberately not
treated as readings) → documented-characteristic reads (2A19/2A24/2A26/
2A29/2A38/2A37) → manual disconnect + onDestroy cleanup. Diagnostic log
app-scoped only; unrelated devices are never logged; no unpair/reset/settings
writes exist in the code. The Huawei-Health-held-link case (typical connect
status 133) produces an explanatory banner; disconnecting Health is the
user's decision, never automated. Build: `./gradlew :app:assembleDebug`.

## 3. Protocol stages completed vs pending

| Stage | Status |
|---|---|
| Literature + primary-source protocol description | COMPLETE |
| Wire-format parser (detect/classify/CRC-verify, sliced-aware) | COMPLETE (CRC cross-verified vs documented packet) |
| Kernel-level LPv2 handshake (link params → auth → bond) | NOT IMPLEMENTED — needs live frames first; auth stage expected blocker: HMAC constants unverified for current firmware |
| SupportedServices/SupportedCommands enumeration (1/2, 1/3) | PENDING — next legitimate step after connect (documented, plaintext) |
| Real-time HR command discovery | PENDING — not documented anywhere public yet |
| Decoded-value verification vs on-watch/Health display | NOT DONE — nothing decoded |

## 4–6. Honest outcome lines requested by the task

1. **BLE scan + GATT discovery results:** NOT AVAILABLE YET — no phone/Band
   6 in hand during this session; the app is built to produce exactly these
   (log file + on-screen). Template placeholders exist in
   `docs/BLE_FIELD_RESULTS.md`.
2. **Genuine heart-rate reading obtained:** **NO.** Nothing decoded,
   nothing authenticated, nothing sent to the receiver's genuine slot.
3. **Genuine SpO₂ reading obtained:** **NO.** No SpO₂ data type/handle is
   even documented for the LPv2 path; no claims made.
4. **Huawei Health disconnect required:** not exercised (no connection
   attempted). The code path documents that the band typically serves ONE
   ATT link; if connect fails because Health holds it, the user decides.

## 5. Laptop + website pipeline (parallel work — done)

Reused Task-001 receiver (18/18 unittests pass after changes; smoke-tested):
- Backward-compatible schema-v1 additive extension: new source
  `huawei_band_6_ble`, optional `transport` ∈ {hms_health_kit, ble_lpv2,
  null}; extra CSV column `transport`; PLUS a new integrity guard — a BLE
  probe with no decoded values is rejected (HTTP 422) so it can never clobber
  the last genuine reading with nulls (smoke-verified live).
- Website (JS re-checked with `node --check`): wearable section now renders
  source + transport attribution ("HMS Health Service Kit" vs
  "Bluetooth LE · LPv2 protocol probe"); SIMULATED feed stays in its own
  labelled panel; no simulated/real mixing anywhere.

## 6. Limitations & exact next step

- No Android toolchain on this laptop ⇒ neither app compiled; both are
  build-ready (`android-bridge/` still awaiting HMS Core in AppGallery;
  `ble-diagnostic/` is the unblocked path).
- The diagnostic app stops at passive observation by design; the handshake
  implementation is a separate consented phase with expected blockers at
  stage 1/19 (auth constants) — documented rather than pretended.
- **Next concrete step:** install `ble-diagnostic` on the Android 13 phone,
  run scan + connect, export `kinetiq_ble_diag.log` into
  `docs/BLE_FIELD_RESULTS.md`; then implement the plaintext 1/2 + 1/3
  probes against the collected frames.
