# KinetiQ — Huawei Band 6 health dashboard

RTOS-based IoT healthcare assistant ("the wellness mirror that sees, listens
and answers"). This repository is the **Huawei Band 6 data-bridge** work:
displaying genuine heart rate, SpO2 and stress from a retail Huawei Band 6 on
a local laptop dashboard.

Wellness assistance only - not a medical device, not medical diagnosis.

## What works today (verified on-device)

- **Genuine readings on the dashboard**: the band syncs to Huawei Health; the
  **KinetiQ Streamer** Android app reads the Health home screen through the
  Android Accessibility API and posts the values to a local receiver, which
  serves the dashboard. No root, no HMS Core, no modification of the band or
  of Huawei Health, nothing leaves the local machine. (Example verified run:
  127 bpm / 100% SpO2 / stress 57, cross-checked against the band display.)
- **Clearly separated SIMULATED lane** for stable demo visuals; real readings
  are never mixed with mock data (`simulated: false` + source whitelist are
  enforced by the receiver, 20/20 unit tests).
- **Cumulative research** of every non-destructive channel on the device:
  plain BLE, the Huawei LPv2 protocol, a Bluetooth HCI capture of Huawei
  Health's own authenticated session with offline crypto verification, a full
  dissection of the Health app's inter-app surfaces, and an attempt at the
  official Wear Engine channel through Health (application pending review).

## Architecture

```
Huawei Band 6 ──BLE── Huawei Health (phone, untouched)
                          │  home-screen cards
                          ▼
              KinetiQ Streamer (AccessibilityService, phone app)
                          │  HTTP POST, schema v1, local only
                          ▼
        kinetiq_receiver.py (laptop, port 8000) ── SSE + poll ── dashboard
```

No internet needed at demo time: the phone reaches the laptop over USB
(`adb reverse`) or the local LAN.

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | Receiver (stdlib HTTP, schema v1, SSE) + 20 unit tests + mock sender |
| `web/` | Dashboard front-end |
| `kinetiq-streamer/` | **Presentation app**: AccessibilityService reader for Health's home screen |
| `ble-diagnostic/` | Diagnostic app: BLE scan/GATT dump, LPv2 probes, Wear Engine probe |
| `android-bridge/` | HMS Health Kit source (blocked on this hardware; kept for Huawei hardware) |
| `docs/` | Full research record: BLE field results, LPv2 deep dive, HCI crypto closure, Health app dissection, Wear Engine application, master report |
| `start_demo.bat` / `stop_demo.bat` | One-click local demo startup/teardown (receiver + USB tunnel + dashboard) |

## Quick start

Laptop:

```bat
python backend\kinetiq_receiver.py          # token via KINETIQ_TOKEN or backend/config.json
start_demo.bat                              # or: receiver + adb reverse + open dashboard
```

Phone (one-time):

1. Install `kinetiq-streamer/app/build/outputs/apk/debug/app-debug.apk`
2. Enable its Accessibility service (Settings -> Accessibility -> KinetiQ Streamer)
3. Open Huawei Health on its home screen, open the app, save endpoint + token, start

Receiver API: `POST /api/wearable` (schema v1, `X-KinetiQ-Token` header),
`GET /api/wearable/latest`, `GET /` (dashboard). Example payloads and the exact
contract are in `backend/kinetiq_receiver.py` and `docs/`.

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
