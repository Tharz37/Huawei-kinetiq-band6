# Research notes — Band 6 BLE & Huawei Link Protocol v2 (Task 002, 2026-10-18)

Sources: quantified-student/smartwatch-mobile-android wiki (via github-wiki-see),
zyv/huawei-lpv2 wiki + `band_gatt.py` (MIT), Gadgetbridge Huawei support (referenced).

## 1. Bluetooth GATT layer (what a plain BLE connection sees)

The band exposes a standard Heart Rate Service (0x180D). On the reference
band (via zyv's `band_gatt.py`):

| Characteristic | UUID | Result |
|---|---|---|
| Battery Level | `00002a19-…-00805f9b34fb` | READABLE |
| Body Sensor Location | `00002a38-…` | READABLE |
| Model / Firmware / Software / Manufacturer | `2a24/2a26/2a28/2a29` | READABLE |
| **Heart Rate Measurement** | `00002a37-…` | **"Not permitted"** |

**Implication (verified externally, to re-verify on our band):** ordinary GATT
notifications on 0x2A37 do NOT carry readable HR unless the device permits the
alternate health app. The diagnostic app therefore treats GATT as discovery +
passive observation only and does not claim HR from it. (Task requirement:
"do not assume ordinary GATT notifications contain readable heart-rate".)

## 2. Huawei Link Protocol v2 — wire format (documented, implementable)

Frame: `0x5A LLLL 0x00 BODY CRC16` where LLLL = len(BODY)+1 (uint16),
CRC16 = CRC-CCITT/XMODEM (poly 0x1021, init 0, xor 0) over `0x5A LLLL 0x00 BODY`.
BODY = `SS CC TLV…`; TLV = `TAG(1B) LEN(VarInt) VAL`; TAG MSB set ⇒ nested TLVs.
Sliced (`XX YY` flags 0x1/0x2/0x3) and multi packets exist but the reference
implementation does not support them yet — a parser must detect and label
them rather than mis-decode.

## 3. Command layer — documented services

| Service ID | Name | Notes |
|---|---|---|
| 1 | DeviceConfig | LinkParams(1), SupportedServices(2), SupportedCommands(3), Battery(8), Bond(14), BondParams(15), Auth(19) |
| 2 | Notification | not needed for HR |
| 7 | Fitness | Documented: SetUserInfo(2), GetTodayTotals(3), GetUserInfo(21)⁺, TruSleepState(22/23⁺), HRMonitorState(23)⁺ |
| 12 | LocaleConfig | — |

**Critical gap:** NO documented command in service 7 streams real-time heart
rate. The quantified-student wiki itself says finding the HR/stress commands
is *future work*, starting from DeviceConfig commands 2 (SupportedServices)
and 3 (SupportedCommands). So protocol exploration = ask the band what it
supports first; that is a legitimate, documented next step.

## 4. Handshake stages (normal mode, per zyv wiki)

1. Link params (1/1) — request empty TLVs 01-04; response carries protocol
   version, max frame/link size, conn interval, `AUTH_VER + 16B device nonce`.
2. Challenge-response auth (1/19): key = HMAC-SHA256(secret=DIGEST_SECRET+data,
   msg=peripheralNonce‖clientNonce); digest = HMAC(key, nonce ‖ combined).
   Challenge data `01 00`, response data `01 10`.
   **DIGEST_SECRET is a 2022 reverse-engineered constant (16 bytes)** —
   authenticity must be verified against current band firmware before trusting
   any matching response. Nonce handling marked debug-research material.
3. Bond params (1/15) — client serial/MAC, encrypted-message counter.
4. Optional bond (1/14) — AES-128-CBC(+PKCS7) or AES-128-GCM depending on
   device; IV = 12 random bytes + 4B counter (big endian); bonding key derived
   from a MAC-mixed secret (32B SECRET_KEY, 2022 constant). **Connects band
   setting/bonding — NOT done automatically by this project at any point.**
5. Encrypted command exchange. Universal tags: 123?/124 encryption marker,
   125 IV, 126 ciphertext, 127 acknowledgement (`00 01 86 A0` = 100000 OK).
   Result codes: 100000 OK … 100003 permission denied, 100002 unsupported, etc.

## 5. Suitability for research use & risks

- The protocol knowledge is community-reverse-engineered (MIT-licensed
  sources, 2022–2025); it is research-grade, not an official Huawei SDK.
  Acceptable for a university PoC; not for a product.
- Constants (DIGEST_SECRET / SECRET_KEY) may have changed with firmware
  updates since 2022 — every auth result must be treated as unverified until
  the band actually validates our digest.
- MAC-dependent bonding key: Android 13 may expose the
  connection-allocated MAC, not the permanent one (empty in the documented
  response too) — flagged as an open risk.
- Only single non-sliced packets are handled by the reference implementation;
  our parser must at least detect sliced/multi frames and fail loudly.
- The band is documented as supporting roughly one ATT connection; while the
  Huawei Health app holds it, a diagnostic connect may be rejected or may
  take over the link. **Passive discovery first; connecting may require
  Health app disconnection — needs explicit user consent.** (Never unpair,
  reset, or modify band settings.)

## 6. Legitimate next steps (in order)

1. BLE diagnostic app: scan (BLUETOOTH_SCAN neverForLocation + CONNECT),
   user selects the Band 6, connect on press only, GATT discovery dump,
   passive notification hex log — DONE as `ble-diagnostic/`.
2. If the LPv2 transport service is present: subscribe, log `0x5A…` frames,
   decode wire format offline (CRC check vs wiki constants).
3. Send SupportedServices (1/2) / SupportedCommands (1/3) in plaintext to
   enumerate what the firmware actually supports (documented commands).
4. Attempt challenge-response auth; if it fails, document stage + code
   (never fabricate).
5. Only after independent verification against the band's on-watch display
   could any decoded value be claimed as a genuine HR reading.

## 7. Live verification record (field run 2026-10-18, Nokia C22 + Band 6)

Upgrades stages 1–4 from "documented" to "verified on this firmware":

- **Transport live-verified both directions.** Every FE02 notification
  parsed `crc=OK` (parser over the wire), and two outbound frames written to
  FE01 with `write status=0`: 1/2 SupportedServices (`5a 00 03 00 01 02 7e 39 00`)
  and 1/3 SupportedCommands (`5a 00 03 00 01 03 6e 18 00`).
- **Probes answered ~300 ms later**, tag 127 value `00 01 86 aa` = result
  code **100010** — not in the documented list (100000 OK / 100002
  unsupported / 100003 permission denied). Unconfirmed; plausibly
  "requires authenticated session". Capability lists are pre-auth-gated.
- **Tag shape confirmed live**: encrypted messages arrive as
  `7c 01 01 | 7d 10 <16B> | 7e 10 <16B>` (124=flag, 125=IV-len-16,
  126=ciphertext-len-16) on Health's periodic sync, services 37(0x25)/15(0x0F)
  /1/2(0x02). Periodic acks carry 127 `00 01 86 a0` = 100000 OK.
- **Unauthenticated GATT exposure is REDUCED**: services exposed = GAP,
  GATT, 0xFE86, 0x180A (Device Info), 0x1812 (HID), 0x3802. **No 0x180D HR
  and no 0x180F Battery** for a non-owner client; 2A19/2A37/2A38 read as
  "not present". Device Info reads succeeded: Model "HUAWEI", Firmware
  "1.0.0.1", Manufacturer "HUAWEI".
- **Single-ATT-link concern refined**: Android shares one ACL across GATT
  clients — a second (passive) client CAN attach while Huawei Health keeps
  its session. The band simultaneously streams its authenticated sync and
  serves our diagnostic reads. No Health disconnect was ever needed.
- Raw evidence: `docs/BLE_FIELD_RAW_2026-10-18.log`;
  analysis: `docs/BLE_FIELD_RESULTS.md`.
- Still open: session secret for 1/19 (auth) — see FINAL_REPORT_003 §10
  options; nothing was done that changes band or Health state.

## 8. HCI snoop follow-up (Phase 4 option b, 2026-10-18) — attempt CLOSED

The "Still open" item was empirically resolved: a btsnoop of the restored
Health⇄Band session (`docs/BTSNOOP_RESULTS.md`, raw
`docs/BTSNOOP_RAW_2026-10-18.log`) captured the full authenticated handshake —
1/1 (byte-exact documented example, CRC `f13b` from the band) → 1/51/1/44 →
1/19 (32B digests both directions, auth version from 1/1 tag5 = `00 01`) →
1/15 → 1/45 → tag127 = 100000 OK → full encrypted traffic incl. encrypted
SupportedServices (which our plaintext 1/2 had been denied with 100010).

Offline crypto verification (`tools/crypto_offline_test.py`), with the zyv
digest implementation first byte-exact-validated against an independently
published Band 8 vector, then run against every public-material variant
(30 compositions per digest) and asymmetric-session AES candidates:

- **No** public `DIGEST_SECRETS` v1/v2/v3 combination reproduces either the
  client's or the band's 1/19 digest on this firmware.
- **No** public-material AES key decrypts any captured session ciphertext
  (the `12B random + 4B counter` IV layout is confirmed byte-exact, so the
  cipher is per the documented family — only the key is un-derivable).

Research conclusion to carry forward: for a band whose bond was created by
Huawei Health, both the auth digest and the session key derive from
Health's per-bond secret; the 2022 public-constant normal-auth family does
not transfer to this device state. Health-metric extraction via a direct
BP connection is therefore closed for this project (HMS Health Kit was the
remaining sanctioned route; it is blocked on this phone). This §8 record is
the resolved rewrite of the §7 "Still open" bullet.
