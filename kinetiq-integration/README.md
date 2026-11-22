# Huawei Band 6 → partner KINETIQ mirror (runtime integration)

This folder connects our genuine Huawei Band 6 relay to the partner KINETIQ
mirror dashboard (`siddoit/KinetiQ`, cloned to `external/KinetiQ`).

**Their repository is not modified.** `mirror_band.py` imports their
`laptop/web/app.py` at runtime, serves a new Home Edition UI at `/`, and keeps
their original UI available at `/classic` with a small provenance chip. Their
`git status` stays empty; nothing in their pipeline, schema, routes or copy is
replaced.

```
Huawei Band 6 ──BLE── Huawei Health (untouched, home screen)
                             │  AccessibilityService (KinetiQ Streamer, ours)
                             ▼
              kinetiq_receiver.py  :8000  (our schema v1 + steps, token POST)
                             │  GET /api/wearable/latest
                             ▼
              mirror_band.py  (runtime wrapper, ours)
                 │  genuine slot only; stores a packet only when live
                 │  and the reading timestamp is new
                 ▼
       THEIR laptop/web/app.py, imported in-process
          ├── /          Home Edition UI (ours; home/index.html)
          ├── /classic   their original UI + provenance chip
          ├── /band/api  read-only JSON for the Home Edition
          └── /api/*     their existing API, unchanged
```

There is **no `--stdin` pipe and no bridge process** in the normal path. The
wrapper uses their own `TelemetryRecord` validation and `map_snapshot` helpers,
then writes a genuine packet into their in-process store. Their direct Band
collector is not started, because it republishes stale readings with a new
timestamp and turns missing steps into `0`.

## Quick start

1. Clone the partner repo once (it is git-ignored, not vendored):

   ```bat
   git clone https://github.com/siddoit/KinetiQ external\KinetiQ
   ```

2. Run `start_integration.bat` from the repository root.
   It reuses or starts the receiver, sets up `adb reverse tcp:8000 tcp:8000`
   (and offers the same screen-awake toggle as `start_demo.bat`), starts the
   wrapper directly, and opens the Home Edition at
   <http://127.0.0.1:8777/>.

3. On the phone: open **Huawei Health**, stay on the home screen, and make sure
   the **KinetiQ Streamer** accessibility service is on.

Manual equivalent (cmd, not PowerShell), after the receiver is running:

```bat
python kinetiq-integration\mirror_band.py --strict-port
```

`--strict-port` refuses to silently reuse a different server on 8777. Without
it the wrapper falls back to 8778 (or the next free port) and prints the URL.

The partner's original UI is always at <http://127.0.0.1:8777/classic>.
The Home Edition has a clearly labelled visual-preview mode at
<http://127.0.0.1:8777/?preview=1>; it uses sample values, says so in the
header, panel and footer, and never presents them as a Band reading.

## What the Home Edition shows

| Tile | Source | Notes |
|---|---|---|
| Heart rate | `heart_rate_bpm` | genuine; Health UI realtime value when available |
| SpO₂ | `spo2_percent` | genuine; Health's latest completed value |
| Stress | `stress_score` | genuine; display-only here and in the classic chip |
| Steps | `steps` | genuine; Health home-screen step count |

The Home Edition shows only the values the relay actually has. It does not
invent activity, motion, PPG quality, respiratory rate, skin temperature or a
readiness score. The Detailed readings tab marks the last three as
**not measured** and PPG as **not relayed**.

- `live` readings are shown with green status and normal labels.
- `stale` readings keep their real values but are labelled amber.
- `offline`/unreachable blanks the values; it never substitutes `0`.
- Missing individual values render as `—`.
- Stress is shown outside the upstream packet; it is never inserted into their
  locked eight-field schema or their AI payload.
- Guidance cards are deterministic from genuine readings, not dependent on the
  AI. The optional **Ask** modal calls their existing `/api/ask` endpoint.
- Camera preview is off by default. When enabled, frames stay in the browser
  and only motion is posted to their `/api/camera_ingest`; no video is stored.

## What the classic upstream mirror receives

The upstream schema is locked v1.0: exactly eight keys, no nulls, no unknown
keys. Every value below is either genuine, **their own** derivation, or a
documented schema-legal placeholder.

| Upstream field | Source | Status |
|---|---|---|
| `timestamp` | Band reading timestamp | genuine |
| `heart_rate` | `heart_rate_bpm` | genuine |
| `spo2` | `spo2_percent` rounded | genuine (Health's latest completed value; `spo2_mode=historical`) |
| `steps` | `steps` (Health home screen, e.g. 4,181) | genuine |
| `exertion` | `mock_streamer.derive_exertion(hr, "unknown")` | derived with the upstream repo's own heuristic |
| `activity` | `"unknown"` | not relayed by this path |
| `motion_intensity` | `0.0` | not relayed by this path |
| `ppg_quality` | `"good"` | proxy: Health reported a completed SpO₂ measurement |

No packet is stored unless heart rate, SpO₂ and steps are all present and the
receiver reports the reading as `live`. Missing values are never replaced by
zero and the mock lane is never read. Stale or offline readings do not create a
new packet with a fake `timestamp=now`; the Home Edition reads their real
freshness directly from the receiver, and the classic chip ages out honestly.

The schema has no stress field, and the upstream footer hardcodes a
"simulated" note even in this mode. The injected chip therefore shows:

* the genuine source (`HUAWEI BAND 6 · genuine, not simulated`),
* `HR`, `SpO₂`, **`Stress`** and `Steps`,
* relay freshness and age (`live` / `stale` / `offline`).

Activity/motion are shown by their dashboard as `UNKNOWN` / `0.00`; that is
honest — this relay has no such sensors. `ppg_quality=good` only means Health
returned a completed SpO₂ value, not that we measured PPG signal quality.

## Why the `steps` field exists in our relay

The upstream schema requires `steps`, and putting a false `0` in their Steps
tile would break our honesty rules. The Health home screen displays the step
count, so we added it additively to our own stack (not to their code):

* `kinetiq-streamer/.../HealthParser.kt` — parses the real `Steps` tile
  (`"Steps", "4,181", "steps"`), range-checked, adjacent-node only so the
  lowercase unit or a later stress number cannot masquerade as steps.
* `StreamService.kt` — posts `steps` (JSON `null` when absent).
* `backend/kinetiq_receiver.py` — accepts optional `steps` (0..200000),
  stores it, logs it as a new trailing CSV column and rotates an older CSV
  header aside instead of corrupting it.

Schema v1 remains additive/backward compatible: old relays without `steps`
still validate, and `steps` is `null`, never `0`, when absent.

## Files

| File | Role |
|---|---|
| `mirror_band.py` | runtime wrapper: imports their `app.py`, serves `/` + `/classic`, owns the honest Band collector and `/band/api` |
| `home/index.html` | Home Edition UI, CSS and JS; no external assets |
| `band_bridge.py` | mapping/validation helpers, plus a legacy stdin bridge kept only for compatibility with their `scripts/demo_band_lane.ps1`; not used by the Home Edition or `start_integration.bat` |
| `test_integration.py` | 25 stdlib tests: mapping, honesty gates, chip injection, collector storage, page contract |
| `run_stack.py` | removed; the wrapper replaces the old bridge → mirror supervisor |

## Verification

```bat
python -m unittest discover -s kinetiq-integration -v
python -m unittest discover -s backend -v
python -m py_compile kinetiq-integration\mirror_band.py
```

* their clone untouched: `git -C external\KinetiQ status --porcelain` → empty
* Home Edition: <http://127.0.0.1:8777/> shows our UI with genuine tiles
* classic view: <http://127.0.0.1:8777/classic> shows their original UI + chip
* their own API: <http://127.0.0.1:8777/api/telemetry> returns the genuine
  packet with `source=HUAWEI BAND 6`
* Home JSON: <http://127.0.0.1:8777/band/api>
* lane switch: `POST /api/mode {"mode":"rtos"}` and back to `band`; the RTOS
  engine repaints their classic view, and the Band collector repaints once when
  it returns

The last on-device end-to-end run was on 2026-10-22: HR 60 bpm, SpO₂ 100 %,
stress 57, steps 4,181 — Health home screen → receiver → wrapper → Home
Edition/classic view. On 2026-10-23 the phone was disconnected; the final UI
and lane checks used a local receiver fixture and are not presented as hardware
evidence.

## Limitations / notes

* The Health home screen must stay in the foreground; the streamer is
  event-driven, so relay age grows when the screen does not refresh.
* This relay does not expose respiratory rate, skin temperature, readiness,
  activity, motion or PPG signal quality. They are omitted or marked
  **not measured**; no sample values are inserted into live mode.
* The upstream schema can only express one SpO₂-style value; stress lives in
  the Home Edition and the classic chip, not in their packet.
* Upstream's static texts ("Sensors simulated…", "Mock engine driving…") refer
  to their mock engine and are left untouched; the chip and Home Edition state
  the real source.
* The partner's own `scripts/demo_band_lane.ps1` is not the supported launcher:
  it starts their direct Band collector as well as a stdin bridge, which can
  duplicate packets, and their RTOS lane is inert in that mode. Use
  `mirror_band.py` for the integrated lane switching.
* The upstream `79815da` tree contains an uninitialized
  `Tharun work/Huawei-kinetiq-band6` submodule. It is not needed by this
  integration and is left untouched.
* `adb reverse` keeps the phone on `127.0.0.1:8000`; the whole path is local
  and works offline.
