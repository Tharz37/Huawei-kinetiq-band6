# Bluetooth HCI snoop run — Phase 4, option (b) (2026-10-18)

Scope: capture the Huawei Health ⇄ Huawei Band 6 authenticated LPv2 session on
THIS phone (user's own devices, user's own phone), then offline-test whether
the auth digests / session keys are derivable from public research material.

Hard rule kept throughout: no band setting was read or written, no pairing
action was triggered, Health was never opened or closed from here. The only
link breakage was the Bluetooth-stack restart Android requires to arm the
capture. Health re-attached automatically afterwards; what got observed is the
band's normal re-association, **not** a re-pairing.

## 1. Consent and arming

- User enabled Developer options → *Bluetooth HCI snoop logging* = FULL
  (explicit consent for option b).
- The stack only reads the option at Bluetooth-enable time
  (`mSnoopLogSettingAtEnable = empty` before restart).
- Arming: `adb shell svc bluetooth disable` → `enable` (shell, no root).
  Verified after restart: `mSnoopLogSettingAtEnable = full`, `state: ON`.
- Band re-linked to Health (`app_if: 5, appName: com.huawei.health`,
  STATE_CONNECTED) within the same minute.

## 2. Acquisition

- `/data/misc/bluetooth/logs/` is root-protected on this build; direct
  `adb pull` → **Permission denied** (expected on locked consumer ROMs).
- Retrieved via `adb bugreport zip` → entry
  `FS/data/misc/bluetooth/logs/btsnoop_hci.log`, 25,585 B, 479 HCI records
  (≈10 s of traffic: classic btsnoop, version 1, DLT 1002 = HCI UART H4).
- Raw evidence preserved: `docs/BTSNOOP_RAW_2026-10-18.log`
  (also at `C:\Users\Tharz\AppData\Local\Temp\opencode\captures\` with the
  originating bugreport zip).

## 3. Parser direction semantics (tool note)

`tools/btsnoop_inspect.py` labels `rx` the HCI ACL packets this build flags
"received", but ATT-opcode invariants prove the opposite: 0x52 write commands
can only originate on the phone and 0x1B notifications only on the band.
Therefore **inspector `rx` = phone→band, `tx` = band→phone**. CRC-16/XMODEM
verified `crc=OK` on every one of the 61 LPv2 frames; first-17 frames were
disassembled in full (`svc, cmd, TLV tags/values`).

## 4. The captured authenticated handshake (svc 1 = DeviceConfig)

Direction is physical (P=phone→band, B=band→phone). Bytes verified against the
raw log; TLV varint lengths are single-byte (`len` field) except where noted.

| # | Dir | Frame | Content (TLV = tag(len) value) |
|---|---|---|---|
| 1 | P | 1/1 LinkParams *request* | empty TLVs 1..4; **CRC `f1 3b` = documented example packet, byte-exact** |
| 2 | B | 1/1 LinkParams *response* | 1(1)`01` protocol, 2(2)`00F4` maxFrame, 3(2)`00F4` maxLink, 4(2)`0000`, 5(18)`00 01 ‖ server-nonce 16B = 39C56067…C387C224`, 7,8,9 flags — `00 01` prefix ⇒ **auth version 1** |
| 3 | P | 1/51 (undocumented id) | 1`01`, 2`01`, 5(16)ASCII `"45c97eb85be13db7"` (client id), 3`01`, 4`00` → B: 1`01`, 2`01` |
| 4 | P | 1/44 (undocumented id) | request with empty tag 1 → B: **1(16)`4B61E5010B04DE3CB4D91FC35D0706E6` + 2(16)`CC496221AB1F8047A507E6BB447883BD`** |
| 5 | P | 1/19 Auth | 1(32)`2728F810…BC0BE49` client digest, 2(18)`00 01 ‖ client-nonce C1F0FB31…8A3D3C0`, 3(1)`01` → B: 1(32)`FA6042A5…82F890` reply digest |
| 6 | P | 1/15 BondParams | 1 empty, 3(6)`"NDVDOT"` client serial, 4(1)`02`, 5 empty, 7(17)ASCII `"FF:FF:FF:FF:FF:CC"`, 9 empty → B: 1`01`, 2`01`, 4`02`, 5(2)`00F4`, 7(16) zeros, 8, 9 |
| 7 | P | 1/45 (undocumented id) | 1(32)`8D80EFF5…161E`, 2(16)`0DC9CFAA…21E` → **B: tag127 = 100000 OK** — session authenticated |
| 8 | P/B | encrypted session | 1/7, 1/5 (+OK answer with tags 1,2), **1/2 SupportedServices request AND encrypted response** (pre-auth our plaintext 1/2 got 100010 ⇒ 100010 = "requires authenticated session"), 1/55, 1/16 (+OK); tag shapes `7c 01 01 / 7d 10 IV / 7e 10.. ciphertext`; IVs = 12 random bytes + 4-byte big-endian counter `…000016 30/31/32/34/3E` — **documented IV layout verified byte-exact** |

Post-auth command/service waves observed across the capture window (mostly
encrypted frames + 100000 OKs): svc **1, 2, 7, 10, 12, 15, 23, 24, 26, 37
(0x25), 39, 48, 50** — including svc 10 file transfer listing
`sleep_state.bin;sleep_data.bin` (10/1 request, 10/2/10/3 replies, 10 KB chunk
size tag) and svc 50 state exchange. All ciphertext bodies logged bytes-only —
**none are decoded and none are treated as health values.**

## 5. Offline crypto tests — `tools/crypto_offline_test.py`

Constant source: zyv/huawei-lpv2 `huawei/protocol.py` (public 2022 research);
`DIGEST_SECRETS` v1/v2/v3, `SECRET_KEYS`, message texts `0110`/`0100`;
digest = nested `HMAC-SHA256(HMAC-SHA256(fromhex(DIGEST_SECRET‖msg), nonce‖nonce), nonce‖nonce)`.

- **Port validation (independent vector, PASS).** With flatsiedatsie's
  published Huawei Band 8 session (server nonce `11287791…`, client nonce
  `CC49AD67…`), our port reproduces the published digest byte-exact:
  `D4EC1496…BE400D8EE` **MATCH**. The formula port is correct.
- **Decisive test on OUR capture (FAIL).** Neither Health's 1/19 client digest
  (`2728…`) nor the band's reply digest (`FA60…`) equals **any** of
  **30 public-material composition variants** tested per digest
  (v1/v2/v3 × message {0110, 0100} × nonce-complete {server‖client,
  client‖server, prefixed-18B forms, concat of the band's two 1/44 values}).
- **Session-cipher test (FAIL).** 7 AES-128-CBC key candidates derived purely
  from public material (`create_secret_key(v1..v3, band MAC)`,
  SHA-256/hmac combos of the captured nonces and digests) decrypt **none** of
  three captured ciphertexts (svc1 1/7, 1/5, 1/2) whose IVs are known.

## 6. Honest verdict

1. **The documented 2022 public-constant auth does not apply to this band in
   its current Health-paired state**, on firmware `1.0.0.1`: both sides'
   1/19 digests are produced from bond-specific secret material (or a newer,
   materially different scheme chained through the undocumented 1/44/1/45
   messages). This is consistent with the ecosystem evidence (zyv/psolyca
   success on 2021-era bands; flatsiedatsie's Band 8 `100004 System Busy`;
   Gadgetbridge's standing limitation: bands paired to Huawei Health cannot
   be adopted without re-pairing).
2. **The session cipher key is equally bond-derived** — Health's captured
   traffic cannot be decrypted offline, and there is no public-material path
   to our own authenticated session.
3. Therefore the capture answers the Phase 4 question negatively but
   definitively: **HR/SpO₂/stress remain gated; `REAL_DATA_VERIFIED` stays
   `no`; nothing was decoded into a claimed reading.**
4. Legitimate paths that remain, unchanged from FINAL_REPORT_003 §10:
   **HMS Health Kit bridge** (Task 001, blocked on this phone: HMS Core not
   available in this phone's AppGallery) or — excluded by the project rule
   (never unpair/reset/modify band state) — a fresh re-pairing capture, which
   would take over the band from Health and is consequently **not recommended
   and not performed**.

## 7. Statuses and hygiene

- Milestone ledger unchanged: COMPILED / INSTALLED / DEVICE_TESTED yes;
  REAL_DATA_VERIFIED **no**.
- The capture contains only this phone's Bluetooth traffic (Health ⇄ band),
  stored locally on the laptop and in `docs/`. HCI logging is STILL ENABLED on
  the phone (user toggle); recommendation: disable the developer option when
  done and restart Bluetooth/phone → capture file rotation (user action; never
  done from here without request).
- The 100010 result code from the field-run probes is now corroborated: the
  same command (1/2) was fully served to Health immediately after
  authentication — supporting "100010 = requires authenticated session".
