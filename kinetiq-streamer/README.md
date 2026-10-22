# KinetiQ Streamer

Standalone Android app (package `com.kinetiq.streamer`) that reads the genuine
heart-rate / SpO2 / stress values displayed on the **Huawei Health** home
screen through an `AccessibilityService` and POSTs them to the local KinetiQ
laptop receiver. Built for the offline university demo: no Internet, no cloud,
no simulation.

## Build

```powershell
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jdk-17.0.20.101-hotspot"
C:\Users\Tharz\Android-Dev\gradle-8.2\bin\gradle.bat assembleDebug
```

APK: `app\build\outputs\apk\debug\app-debug.apk`

## Demo setup (USB, no Wi-Fi)

1. On the laptop: `python backend/kinetiq_receiver.py`
   (token from `backend/config.json` / `KINETIQ_TOKEN`, default demo token `poc-demo`).
2. Connect the phone by USB and run `adb reverse tcp:8000 tcp:8000`.
   The default endpoint `http://127.0.0.1:8000/api/wearable` then reaches the
   laptop. For LAN, set `http://<laptop-ip>:8000/api/wearable` instead.
3. Install the APK, open **KinetiQ Streamer**, open *Accessibility settings*
   and enable **KinetiQ Streamer**.
4. Press **Save** (endpoint + token), then **Start streaming**.
5. Open **Huawei Health** and leave its home screen visible. Readings are
   parsed at most every 10 s and posted at most every 15 s.

**Test connection** performs a GET to `/api/wearable/latest` (falling back to
`/`) and never POSTs.

## Honesty rules implemented

- Only `com.huawei.health` events are observed (`android:packageNames` in
  `accessibility_config.xml`) and the active window's package is re-checked.
- The payload constants are fixed: `source=huawei_band_6_healthapp`,
  `transport=health_app_ui`, `simulated=false`.
- No POST unless at least one metric was actually parsed from the screen.
- A metric that is not visible is serialised as JSON `null`, never `0`.
- Values pass sensor range checks (HR 20–260 bpm, SpO2 70–100 %, stress 0–100).
- `heart_rate_mode` / `stress_mode` are `realtime` when present,
  `spo2_mode` is `historical` when present, otherwise `unavailable`.

## Notes / limitations

- Parsing is text-heuristic by design (spec): it reads only what Health
  renders. If Health changes its card layout the labels/values may no longer
  be adjacent; the app then sends `null` rather than guessing.
- The parser accepts HR 20–260, while the receiver validates 30–220; a value
  outside the receiver's envelope is rejected with HTTP 422 and shown in the
  UI (nothing is clamped or fabricated).
