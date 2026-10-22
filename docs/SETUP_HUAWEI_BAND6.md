# Setup Guide — Huawei Band 6 → KinetiQ (Task 001 PoC)

Scope: wellness assistance only, not medical diagnosis.

## A. Laptop receiver (5 min, verified working)

1. Install Python 3.11+ (stdlib only — no `pip install` needed).
2. `cd backend && cp config.example.json config.json` (or set `KINETIQ_TOKEN`).
   Put a long random token in `config.json`. This file is gitignored.
3. Run: `python kinetiq_receiver.py`
   Open `http://127.0.0.1:8000` locally; note your LAN IP for the phone:
   this laptop is currently `192.168.220.13` (Wi-Fi) — re-check with
   `ipconfig` if the network changes.
4. Windows Firewall: allow inbound TCP on your port (default 8000) for the
   **Private** profile only. Keep the network profile Private, never expose
   this port to the internet — the token is a LAN-only shared secret, and
   health data must not traverse public hosting in this PoC.

## B. Pipeline test with mock data (before Huawei access)

```sh
set KINETIQ_TOKEN=<same token>
python mock_sender.py --host 127.0.0.1 --port 8000 --count 3
```

The dashboard shows these under a striped **SIMULATED** badge in a separate
panel; genuine slots are untouched (`real_freshness` stays `offline`).

## C. Huawei console + phone (see also android-bridge/README.md)

1. AppGallery Connect: project → app `com.kinetiq.bridge` → App ID.
2. SHA-256 signing fingerprint registered; `agconnect-services.json` in
   `android-bridge/app/`; App ID in the manifest metadata.
3. Health Kit enabled; request heart-rate read + real-time heart-rate + SpO₂
   scopes and wait for approval.
4. Pin `healthKitVersion` in `gradle.properties`; confirm the `[V1]` scope
   constant and `[V2]` SpO₂ data-type constant in `HuaweiHealthDataSource.kt`
   against your console/SDK version, then `./gradlew :app:assembleDebug`.
5. Phone: HMS Core ≥ 5.0.4.300, Huawei Health ≥ 11.0.0.512, Band 6 paired and
   syncing, same Wi-Fi as the laptop.

## D. Real-device acceptance run

1. App → set `http://<laptop-LAN-IP>:8000` + token → Save.
2. **Authorise** → grant scopes. 3. Wear band → **Start heart-rate**.
4. Genuine BPM appears in app → dashboard shows identical value + timestamp.
5. SpO₂: take an on-demand measurement on the band, sync Health, then
   **Query latest SpO₂**. `unsupported/unavailable` is a valid outcome.

## E. Troubleshooting quick table

- HTTP 401 → token mismatch. HTTP 422 → read `reasons` (range/mode/clock).
- Dashboard stale → phone clock wrong (host rejects >5 min future skew) or
  band not syncing (values older than 15 s are labelled historical, not live).
- `permission_denied` pill on website → re-grant in Huawei Health / console.
- SSE red `bridge: disconnected` → laptop server down; page retries via poll.
