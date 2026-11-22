# KinetiQ — Huawei Band 6 health dashboard

RTOS-based IoT healthcare assistant ("the wellness mirror that sees, listens
and answers"). This repository is the **Huawei Band 6 data-bridge** work:
displaying genuine heart rate, SpO2, stress and steps from a retail Huawei Band 6 on
a local laptop dashboard.

Wellness assistance only - not a medical device, not medical diagnosis.

## What works today (verified on-device)

- **Genuine readings on the dashboard**: the band syncs to Huawei Health; the
  **KinetiQ Streamer** Android app reads the Health home screen through the
  Android Accessibility API and posts the values to a local receiver, which
  serves the dashboard. A second in-app track listens for Huawei Health's
  status-bar notification so the last update can still be caught when Health is
  backgrounded. No root, no HMS Core, no modification of the band or of Huawei
  Health, nothing leaves the local machine. (Example verified run on
  22 Oct 2026: HR 60 bpm / 100% SpO2 / stress 57 / 4,181 steps, read from the
  Health home screen and relayed end-to-end. A second genuine screen run on
  22 Nov 2026 read HR 97 / SpO2 98% / stress 80 / 42 steps.)
- **Clearly separated SIMULATED lane** for stable demo visuals; real readings
  are never mixed with mock data (`simulated: false` + source whitelist are
  enforced by the receiver, 22/22 unit tests).
- **Partner mirror integration**: the same genuine readings also drive the
  partner KINETIQ browser mirror (`siddoit/KinetiQ`) through a runtime wrapper.
  Their repository is left byte-identical; their original UI stays at
  `/classic`, while a new Home Edition at `/` presents the genuine values in the
  Canva-inspired layout. Stress (which their locked schema cannot carry)
  appears in the Home Edition and an additive provenance chip. See
  [`kinetiq-integration/`](kinetiq-integration/README.md).
- **Cumulative research** of every non-destructive channel on the device:
  plain BLE, the Huawei LPv2 protocol, a Bluetooth HCI capture of Huawei
  Health's own authenticated session with offline crypto verification, a full
  dissection of the Health app's inter-app surfaces, and an attempt at the
  official Wear Engine channel through Health (application pending review).

## Architecture

```
Huawei Band 6 ──BLE── Huawei Health (phone, untouched)
                          │  home-screen cards + status-bar notification
                          ▼
              KinetiQ Streamer (phone app)
              ├─ AccessibilityService watchdog (4 s)
              └─ NotificationListenerService (background)
                          │  HTTP POST, schema v1, local only
                          ▼
        kinetiq_receiver.py (laptop, port 8000) ── SSE + poll ── dashboard
                          ▲
        optional: backend/kinetiq_adb_relay.py (USB ADB fallback)
```

No internet needed at demo time: the phone reaches the laptop over USB
(`adb reverse`) or the local LAN.

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | Receiver (stdlib HTTP, schema v1, SSE) + 22 unit tests + mock sender |
| `web/` | Dashboard front-end |
| `kinetiq-streamer/` | **Presentation app**: 4 s AccessibilityService watchdog + Health notification listener |
| `install_optimized_app.bat` | One-click test/build/install + accessibility/notification grant + `adb reverse` |
| `backend/kinetiq_adb_relay.py` | Optional PC-side USB ADB fallback (Health notification/UI scrape, duplicate-aware) |
| `kinetiq-integration/` | Runtime wrapper for the partner KINETIQ mirror: Home Edition UI + `/classic` + 25 tests; their repo untouched |
| `ble-diagnostic/` | Diagnostic app: BLE scan/GATT dump, LPv2 probes, Wear Engine probe |
| `android-bridge/` | HMS Health Kit source (blocked on this hardware; kept for Huawei hardware) |
| `docs/` | Full research record: BLE field results, LPv2 deep dive, HCI crypto closure, Health app dissection, Wear Engine application, master report |
| `start_demo.bat` / `stop_demo.bat` | One-click local demo startup/teardown (receiver + USB tunnel + auto-healing watcher + dashboard) |
| `start_integration.bat` | One-click receiver + Home Edition mirror (port 8777; classic at `/classic`) + tunnel watcher |
| `watch_adb_reverse.ps1` | Restores `adb reverse tcp:8000` after a USB bump |
| `docs/REVIEW_ROOM_RUNBOOK.md` | Pre-flight, fallbacks, demo sequence, recovery table |

## Quick start

Laptop:

```bat
py -3 backend\kinetiq_receiver.py            # token via KINETIQ_TOKEN or backend/config.json
start_demo.bat                                # or: receiver + adb reverse + watcher + dashboard
```

Phone (one-time):

1. Run `install_optimized_app.bat` with the phone connected and USB debugging
   authorized (or install `kinetiq-streamer/app/build/outputs/apk/debug/app-debug.apk`
   manually).
2. Enable its Accessibility service and notification access
   (Settings -> Accessibility / Notifications -> Device & app notifications).
3. Open Huawei Health on its home screen, open the app, save endpoint + token, start.
   The screen watchdog refreshes every 4 s; identical readings are heartbeated
   at most every 15 s, and the notification listener cannot duplicate the same
   foreground reading. Optional fallback:
   `py -3 backend\kinetiq_adb_relay.py`.

Receiver API: `POST /api/wearable` (schema v1, `X-KinetiQ-Token` header),
`GET /api/wearable/latest`, `GET /` (dashboard). Example payloads and the exact
contract are in `backend/kinetiq_receiver.py` and `docs/`.

## Partner mirror integration (runtime, additive)

The genuine values can also drive the partner KINETIQ browser mirror without
editing their code:

```bat
git clone https://github.com/siddoit/KinetiQ external\KinetiQ   rem once
start_integration.bat
```

`mirror_band.py` imports their `laptop/web/app.py` at runtime. The Home Edition
is served at `/`; their original UI is preserved at `/classic` with a small
provenance chip. The wrapper does not use their `--stdin` path or start their
direct Band collector. It validates packets with their own `TelemetryRecord`
and writes only live, genuinely timestamped readings into their store. A
read-only `/band/api` route feeds the Home Edition; their existing `/api/*`
routes are untouched.

Heart rate, SpO2, stress and steps are genuine. Activity and motion are not
relayed by this path and appear in their classic dashboard as `unknown` /
`0.00`. Respiratory rate, skin temperature and readiness are not measured and
are marked as such in the Detailed readings tab, never filled with sample
values. Stress is shown in the Home Edition and the classic chip, never inserted
into their locked eight-field packet. The full field-by-field mapping and
honesty notes are in
[`kinetiq-integration/README.md`](kinetiq-integration/README.md).

## Honesty rules kept throughout

- A value is never claimed unless decoded AND cross-checked against the band /
  Health display; missing readings are `null`, never `0`.
- The band's pairing was never reset; Huawei Health was never modified,
  disconnected, or bypassed.
- No root, no Play Protect / integrity bypass, no side-loaded HMS clones.

## Public-release note

Device identifiers (band Bluetooth MAC, phone serial, MAC-derived device-name
suffix) were replaced with placeholders (`02:00:00:12:34:56`, `Band 6-XXX`)
across all documents and captured logs before publication. The encrypted HCI
capture remains as-is otherwise; its session keys were derived from the
pairing secret and are not included.

## License

MIT (see `LICENSE`).
