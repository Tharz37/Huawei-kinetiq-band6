# Huawei Band 6 → upstream KINETIQ mirror (additive integration)

This folder connects our genuine Huawei Band 6 relay to the partner KINETIQ
mirror dashboard (`siddoit/KinetiQ`, cloned to `external/KinetiQ`).

**Their repository is not modified.** The integration uses the mirror's own
documented `--stdin` ingestion mode, then adds two runtime-only pieces: a
read-only `/band/api` route and a small provenance chip pinned to the page.
Their `git status` stays empty; nothing in their pipeline, schema, routes or
copy is replaced.

```
Huawei Band 6 ──BLE── Huawei Health (untouched, home screen)
                             │  AccessibilityService (KinetiQ Streamer, ours)
                             ▼
              kinetiq_receiver.py  :8000  (our schema v1 + steps, token POST)
                             │  GET /api/wearable/latest
                             ▼
              band_bridge.py  (genuine slot only, "live" only)
                             │  upstream locked telemetry schema, one line/reading
                             ▼
              laptop/web/app.py --stdin   ← THEIR code, unmodified
                             │
              http://127.0.0.1:8777  +  /band/api + provenance chip (ours)
```

## Quick start

1. Clone the partner repo once (it is git-ignored, not vendored):

   ```bat
   git clone https://github.com/siddoit/KinetiQ external\KinetiQ
   ```

2. Run `start_integration.bat` from the repository root.
   It reuses or starts the receiver, sets up `adb reverse tcp:8000 tcp:8000`
   (and offers the same screen-awake toggle as `start_demo.bat`), starts the
   bridge → mirror stack, and opens the mirror at
   <http://127.0.0.1:8777/>.

3. On the phone: open **Huawei Health**, stay on the home screen, and make sure
   the **KinetiQ Streamer** accessibility service is on.

Manual equivalent (cmd, not PowerShell):

```bat
python kinetiq-integration\band_bridge.py | python kinetiq-integration\mirror_band.py
```

or one supervised window with clean logs:

```bat
python kinetiq-integration\run_stack.py
```

## What the mirror receives

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

No packet is emitted unless heart rate, SpO₂ and steps are all present and the
receiver reports the reading as `live`. Missing values are never replaced by
zero and the mock lane is never read. If the reading goes `stale`/`offline` the
bridge stops emitting and says why on stderr; the mirror keeps its last paint,
and the chip ages out honestly.

The schema has no stress field, and the upstream footer hardcodes a
"simulated" note even in stdin mode. The injected chip therefore shows:

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
| `band_bridge.py` | receiver → upstream-schema packet bridge, validated by *their* `TelemetryRecord.parse` before printing |
| `mirror_band.py` | imports their `laptop/web/app.py`, adds `/band/api` + chip, serves on 8777/8778 |
| `run_stack.py` | wires bridge stdout → mirror stdin, supervises both processes |
| `test_integration.py` | 15 stdlib tests: mapping, honesty gates, chip injection, freshness view |

## Verification

```bat
python -m unittest discover -s kinetiq-integration -v
python -m unittest discover -s backend -v
```

* their clone untouched: `git -C external\KinetiQ status --porcelain` → empty
* live page: <http://127.0.0.1:8777/> shows their tiles + the band chip
* their own API: <http://127.0.0.1:8777/api/telemetry> returns the genuine
  packet with `source=NATIVE NODE`
* chip data: <http://127.0.0.1:8777/band/api>

Verified end-to-end on 2026-10-22: HR 60 bpm, SpO₂ 100 %, stress 57,
steps 4,181 — Health home screen → receiver → their mirror.

## Limitations / notes

* The Health home screen must stay in the foreground; the streamer is
  event-driven, so relay age grows when the screen does not refresh (the chip
  shows `stale` after 60 s, `offline` after 120 s — by design).
* The upstream schema can only express one SpO₂-style value; stress lives in
  the chip, not in their packet.
* Upstream's static texts ("Sensors simulated…", "Mock engine driving…") refer
  to their mock engine and are left untouched; the chip states the real source.
* `adb reverse` keeps the phone on `127.0.0.1:8000`; the whole path is local
  and works offline.
