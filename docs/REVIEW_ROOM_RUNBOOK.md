# KinetiQ review-room runbook

Purpose: a short, honest, recoverable sequence for the final review. The primary
path is the phone's AccessibilityService reading the visible Huawei Health home
screen. The notification listener and the PC ADB relay are supplementary paths;
they never invent a reading.

## 1. Pre-flight (5 minutes before the panel)

1. Phone connected by USB; unlock it and accept any "Allow USB debugging" prompt.
2. Huawei Health open on its home page, with the band connected and the genuine
   HR / SpO2 / stress / steps tiles visible.
3. KinetiQ Streamer open; confirm:
   - Accessibility service: ENABLED
   - Notification listener: ENABLED
   - Streaming: ON
   - Endpoint: `http://127.0.0.1:8000/api/wearable`
   - Last result: `HTTP 200`
4. On the laptop, run `start_integration.bat` (or `start_demo.bat` for the
   receiver dashboard only).
5. Open `http://127.0.0.1:8777/` for the Home Edition and
   `http://127.0.0.1:8777/classic` for the partner view.
6. Confirm the Home Edition shows a green `LIVE READINGS` state and the four
   genuine cards. Do not proceed if it shows sample/preview state.

`start_integration.bat` starts an **ADB tunnel watcher**. If the USB cable is
bumped, it restores `adb reverse tcp:8000 tcp:8000` within about three seconds.
The watcher is a separate window titled `KinetiQ ADB tunnel watch`.

Manual equivalent, if ever needed in a spare terminal:

```powershell
powershell -NoProfile -Command "while($true) { adb reverse tcp:8000 tcp:8000 2>$null; Start-Sleep -Seconds 3 }"
```

## 2. Cable-free / LAN fallback

Use this only when walking around the room is part of the demonstration.

1. Connect the phone and laptop to the same Wi-Fi or laptop hotspot.
2. Find the laptop IPv4 address with `ipconfig`.
3. In KinetiQ Streamer change the endpoint from
   `http://127.0.0.1:8000/api/wearable` to
   `http://<LAPTOP-IP>:8000/api/wearable`, then tap **Save**.
4. Allow Python through the Windows firewall on the private network if prompted.
5. The receiver already listens on `0.0.0.0:8000`, so no receiver restart is
   normally required. Verify from the laptop with
   `http://<LAPTOP-IP>:8000/api/health`.
6. When returning to USB, set the endpoint back to `127.0.0.1` and tap **Save**.

LAN mode does not use `adb reverse`; it uses the laptop's actual address.

## 3. Display awake

The scripts offer an optional timed prompt. To set it explicitly:

```powershell
adb shell settings put global stay_on_while_plugged_in 7
```

`7` = AC + USB + wireless charging. It is a demo convenience, not a silent
default. Revert with:

```powershell
adb shell settings put global stay_on_while_plugged_in 0
adb shell svc power stayon false
```

`stop_demo.bat` performs the revert. It does not stop the receiver or mirror
windows.

## 4. Live demonstration sequence

1. **Trinity of truth.** Show the Band 6 AMOLED, the phone's Huawei Health
   screen, and the laptop Home Edition at the same time. The values should match
   and the dashboard must say `genuine`, not `sample` or `preview`.
2. **Causality test.** Note the current step count, walk 15-20 paces, tap the
   band, and wait for the Health tile and then the dashboard to update. The
   watchdog polls every 4 seconds; unchanged values are heartbeat-posted no more
   often than every 15 seconds.
3. **Honesty check.** Open the Home Edition **Detailed readings** tab. Confirm:
   - Respiratory rate: `not measured`
   - Skin temperature: `not measured`
   - Readiness score: `not measured`
   - PPG quality: `not relayed`
4. **Provenance check.** Open `/classic`. The bottom-right chip should read
   `HUAWEI BAND 6` with genuine HR, SpO2, stress, steps, freshness and
   `genuine, not simulated`.
5. **Lane switch.** In `/classic`, switch to FreeRTOS and back to Band 6. The
   band lane resumes from the receiver; no value is fabricated during the
   transition.

## 5. If something looks wrong

| Symptom | Check | Recovery |
|---|---|---|
| Dashboard says offline / stale | Receiver running? `http://127.0.0.1:8000/api/health` | Start `start_integration.bat`; check the receiver window |
| Phone shows POST errors | Endpoint and token; USB tunnel | `adb reverse --list`; re-run `adb reverse tcp:8000 tcp:8000` or use LAN |
| No new values | Health foreground? Screen on? Streamer ON? | Re-open Health home page; confirm the accessibility service is bound |
| No notification updates | Health may not post a status-bar notification on this ROM | Use the screen watchdog or the ADB relay; do not claim a notification was received when it was not |
| Mirror port busy | An old 8777 process is still open | Close the old mirror window; run `start_integration.bat` again |
| Phone locked / dim | Display-awake setting | `adb shell input keyevent KEYCODE_WAKEUP`; set `stay_on_while_plugged_in` explicitly |

## 6. Privacy note for Git

The live biometric log is local runtime output. It has been hidden from local
`git status` with:

```powershell
git update-index --assume-unchanged backend\data\kinetiq_wearable.csv
```

This is a local convenience. It does **not** remove earlier copies of the file
from existing Git history, and it does not protect a clone. For a genuinely
segregated release, use `git rm --cached` plus a `.gitignore` entry, and rewrite
history only if the earlier biometric rows must not exist in the submitted
repository. Do not claim more than the chosen mechanism actually does.

## 7. Examiner questions

- **Why Accessibility rather than direct BLE?** The Band 6 uses Huawei's
  proprietary Link Protocol v2; the standard Heart Rate characteristic is not
  exposed to unauthenticated devices. Bypassing the handshake would require
  destructive pairing changes. The project keeps the manufacturer pairing
  intact and reads only genuine values that Huawei Health displays.
- **What if the Health layout changes?** Parsing is semantic (labels, units,
  range checks), not coordinate clicking. Unknown or absent values become
  `null`/`not measured`; the app refuses to POST a vitals-free packet. The ADB
  relay parses the raw view hierarchy as a fallback.
- **Why no respiratory rate, skin temperature or readiness?** The retail Band 6
  does not measure them. The dashboard says `not measured` rather than filling
  them with plausible-looking fake numbers.
