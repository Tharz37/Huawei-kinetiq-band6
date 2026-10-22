# BLE field results — transcribed 2026-10-18

Source of truth: `docs/BLE_FIELD_RAW_2026-10-18.log`
(pulled from the phone: `Android/data/com.kinetiq.blediag/files/kinetiq_ble_diag.log`).
App: `com.kinetiq.blediag` v0.3.1-diagnostic, handset Nokia C22 (Android 13),
band Huawei Band 6. **No value below is declared a health reading** — nothing
was decoded (the LPv2 data frames observed are encrypted / gated), so nothing
is claimed. Device-*identity* values (§reads) are the only decoded plaintext.

## Run 1 — 2026-10-18, 13:0x–13:2x IST

- Device identified: **`HUAWEI Band 6-XXX`** · `02:00:00:12:34:56` — found via
  the system bond list ("2B · Show bonded devices"), NOT via advertising.
- System-bond state: **bonded** (LE bond exists; phone Bluetooth "Bonded
  devices" dumpsys lists it: `02:00:00:12:34:56 [  LE  ] HUAWEI Band 6-XXX`).
- Advertised UUIDs observed on the band: **none** — see the scan note below.

## Key scan finding (root-caused, not a bug)

With a passive unfiltered LOW_LATENCY scan running, the stack reported
**0 results** (confirmed twice via `dumpsys bluetooth_manager`: the scanner was
registered and active, "0 results"). Root cause, evidenced from the same
dumpsys: the band has been **continuously LE-connected to this phone for
≈14.4 h** (GATT connection record `22:50:04 → 51,695,853 ms`) — held by
`com.huawei.health` (`app_if 5` GATT client). A connected peripheral stops
general advertising, so a scan cannot discover it. Health's own filtered scan
history (`BluetoothLeScanFilter DeviceAddress=02:00:00:12:34:56`, yesterday
22:39) saw results — i.e. the band *was* advertising while unconnected.

Two real app bugs were found and fixed during this run (documented because
they affect every future run):

1. `stopScan` must reuse the same callback object as `startScan` (the first
   build created a fresh one — stack showed `started 2 / stopped 0`).
2. Characteristics reads issued while CCCD writes are still in flight return
   "not triggerable" — reads are now queued one-per-callback after a settle.

## Connection

- Connect pressed: **yes** (user tap on the bonded-device row, Connect button).
  ATT result: **connected, status=0** — achieved **while Huawei Health kept
  its link**: one ACL connection is shared by multiple GATT clients, so the
  diagnostic attached without touching Health at any point.
- Health app was never disconnected, Dayton stopped, unpaired or modified.
- Two full sessions (13:18 and 13:25) plus a clean manual disconnect —
  log: `user requested disconnect — GATT closed, nothing modified on the band`.

## GATT discovery dump (connected, unauthenticated client — reduced table)

```
Service 00001800-0000-1000-8000-00805f9b34fb(inst 1)          [GAP]
  char 00002a00 [read]   char 00002a01 [read]   char 00002aa6 [read]
Service 00001801-0000-1000-8000-00805f9b34fb(inst 16)         [GATT]
  char 00002a05 [indicate] (CCCD)   char 00002b29 [read]   char 00002b2a [read]
Service 0000fe86-0000-1000-8000-00805f9b34fb(inst 42)         [Huawei LPv2]
  char 0000fe01 [write]   char 0000fe02 [notify] (CCCD)
Service 0000180a-0000-1000-8000-00805f9b34fb(inst 48)         [Device Info]
  char 00002a29 [read] char 00002a24 [read] char 00002a25 [read]
  char 00002a26 [read] char 00002a27 [read] char 00002a28 [read]
Service 00001812-0000-1000-8000-00805f9b34fb(inst 80)         [HID over GATT]
  char 00002a4a [read]  char 00002a4b [read] (CCCD)  char 00002a4c []
  char 00002a22 [read,notify] (CCCD)  char 00002a32 [read,write]
  char 00002a4d [read,notify] (CCCD)  ×2
  char 00002a4e [read]
Service 00003802-0000-1000-8000-00805f9b34fb(inst 768)        [vendor]
  char 00004a02 [read,write,notify] (CCCD)
```

Notably **absent** for this unauthenticated client: Heart Rate service (0x180D),
Battery service (0x180F), Body Composition etc. This matches the reduced
service exposure described for non-owner clients.

## Documented-characteristic reads

| UUID | Result |
|---|---|
| 2A19 Battery Level | **not present** (Battery service absent in this table) |
| 2A24 Model Number | `48 55 41 57 45 49` → **"HUAWEI"** |
| 2A26 Firmware Revision | `31 2e 30 2e 30 2e 31` → **"1.0.0.1"** |
| 2A29 Manufacturer Name | `48 55 41 57 45 49` → **"HUAWEI"** |
| 2A38 Body Sensor Location | **not present** |
| 2A37 Heart Rate Measurement | **not present** (HR service not exposed) |

Cross-check opportunity (not yet done): Settings → System → About on the band
should show version/model matching `1.0.0.1` / HUAWEI.

## Passive LPv2 frames observed (FE02 notify, all CRC-OK)

Representative (full stream in the raw log):

```
5a 00 2a 00 25 01 7c 01 01 7d 10 47 78 70 … 7e 10 b2 54 47 …   svc=37 cmd=1 tags=[124,125,126] crc=OK
5a 00 09 00 25 02 7f 04 00 01 86 a0 bb 14                     svc=37 cmd=2 tags=[127] crc=OK
5a 00 06 00 25 03 01 01 64 da 86                              svc=37 cmd=3 tags=[1] crc=OK
5a 00 2a 00 0f 02 … / 0f 04 … / 0f 06 …                       svc=15 cmd=2/4/6 tags=[124,125,126] crc=OK
5a 00 09 00 0f 01 7f 04 00 01 86 a0 …                         svc=15 cmd=1/8 tags=[127] crc=OK
5a 00 2a 00 02 06 7f 04 00 01 86 a0 c6 a2                     svc=2  cmd=6 tags=[127] crc=OK
5a 00 2a 00 01 38 …  (identity)                               svc=1  cmd=56 tags=[124,125,126] crc=OK
```

Reading of the catalogue *(annotation only — no values interpreted as data)*:

- Frame shape matches `docs/RESEARCH_LPV2.md` exactly: `5A LLLL 00 SS CC TLVs CRC16(XMODEM)`.
- Tags 124 (`0x7c`)=encrypted-flag(value 01), 125 (`0x7d`)=tag len 16, 126
  (`0x7e`)=ciphertext len 16 — the documented **encrypted-message** layout;
  bodies are random-looking 16-byte AES blocks. These are **not decoded and
  not claimed as readings**.
- Tag 127 (`0x7f`) len 4 value `00 01 86 a0/aa` — result-code TLV:
  `0x186a0` = **100000 = OK** (documented acknowledgement); `0x186aa` =
  **100010** seen on our probe replies — a result code *not* in the
  documented list (100000 OK, 100002 unsupported, 100003 permission
  denied); meaning unconfirmed, plausibly "needs authenticated session".
- Tag 1, value `0x64` (100) appears on `svc=37 cmd=3` heartbeats.
- Services seen: 0x25(37) "smart device" data push (encrypted body, cmd 1/2/3
  every ~1 s), 0x0F(15) with encrypted pushes + 127-acks, 0x02(2) cmd 6,
  0x01 cmd 56 — i.e. Health's **live authenticated activity sync** is running
  on the shared link right beside our passive client.

## LPv2 plaintext probes (active, documented, non-mutating)

Sent via FE01 write (writeType default), frames assembled with the verified
CRC-16/XMODEM assembler:

| Probe | Frame written | Write result | Band reply |
|---|---|---|---|
| 1/2 SupportedServices | `5a 00 03 00 01 02 7e 39 00` | status 0 | `5a 00 09 00 01 02 7f 04 00 01 86 aa 6e 5b` (crc OK) |
| 1/3 SupportedCommands | `5a 00 03 00 01 03 6e 18 00` | status 0 | `5a 00 09 00 01 03 7f 04 00 01 86 aa d6 3a` (crc OK) |

Interpretation (honest): the band ACKs the transport to frame level and
responds within ~300 ms. Replies carry tag 0x7f with result code **100010**
(0x186aa) — outside the documented result-code list (100000 OK / 100002
unsupported / 100003 permission denied), meaning unconfirmed; the plausible
reading is "this enumeration needs an authenticated session". Either way,
**detailed capability enumeration is gated**, matching research expectations.
Auth (1/19), bond (1/14), bond-params (1/15) and any settings/service write
were **never sent**.

## Bottom line for the pipeline

- Transport: **verified live** (CRC-16/XMODEM assembler + parser validated on
  the wire, both directions: writes accepted, replies parsed).
- Connection policy: bonded-device connect with shared ACL **works and leaves
  the Health app and the band untouched** — reproducible on demand.
- Health values (HR/SpO₂/stress): **NOT obtained**. The data frames are
  crypto-gated; the only remaining legitimate paths are (a) HMS Health Kit
  (blocked: HMS Core absent from this phone's AppGallery) or (b) capturing the
  *pairing-secret establishment* during a fresh authenticated session via a
  passive BT HCI snoop log on THIS phone — a standard Android developer
  option, requires user consent and is not scheduled yet.
