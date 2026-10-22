# KinetiQ minor project

RTOS-based IoT healthcare assistant with sensor-grounded voice AI —
“the wellness mirror that sees, listens and answers.”
Wellness assistance only; not medical diagnosis.

## Task 001 — Huawei Band 6 data bridge (PoC)

Retrieves genuine heart-rate (+ latest SpO₂ where supported) from a Huawei
Band 6 via Health Service Kit, POSTs schema-v1 JSON to the laptop over
trusted LAN, and shows it on the local dashboard.

```
android-bridge/        Kotlin app (API 33): auth, HR start/stop, SpO2 query, upload
ble-diagnostic/        Kotlin app (API 33, Task 002): passive BLE scan, GATT dump,
                       LPv2 frame CRC diagnostics (no health-claim logic)
backend/
  kinetiq_receiver.py  laptop receiver: validate + CSV + SSE + serve dashboard
  test_receiver.py     17 unittest cases (stdlib)
  mock_sender.py       SIMULATED pipeline test (mock data, clearly labelled)
  config.example.json  placeholders only (copy to gitignored config.json)
web/index.html         dashboard: wearable section, live updates, SIMULATED panel
docs/
  PLAN.md              implementation plan from actual repo inspection
  SETUP_HUAWEI_BAND6.md console/phone/network/troubleshooting guide
  FINAL_REPORT.md      honest implemented / mocked / compiled / tested matrix
```

## Quickstart (verified)

```sh
cd backend
# copy config.example.json -> config.json with a strong token (or set KINETIQ_TOKEN)
python kinetiq_receiver.py            # dashboard: http://127.0.0.1:8000
python -m unittest discover -s . -v  # 17 tests
python mock_sender.py --count 3       # SIMULATED pipeline check
```

## Status (2026-10-18, after Task 002)

- Laptop side: implemented + tested (18/18 tests, live smoke OK).
- Task 001 Android bridge: source complete, **not compiled** (no Android
  SDK here; blocked on HMS Core in AppGallery — see Task 002).
- Task 002 BLE investigation: research verified (CRC stage proven),
  `ble-diagnostic/` app built for the on-device scan/GATT run; **no genuine
  Band 6 reading obtained yet** — exact next steps and blockers:
  `docs/FINAL_REPORT_002.md`.
