# Upstream commit `79815da` — analysis and integration record

Partner repository: `siddoit/KinetiQ`, cloned read-only at `external/KinetiQ`.
Commit: `79815da875992155de3f30bcab26357537e4fb0b`
Author: Sid · 2026-10-23 08:03 IST
Subject: `feat: in-app band/rtos lane switch, conversational ai, 5s presence greeting, and comprehensive report suite`

This document records what the commit added, which parts the Home Edition
reused at runtime, and the defects that made a wrapper necessary. The partner
tree was never edited: `git -C external/KinetiQ status --porcelain` stays empty.

## 1. What their commit set out to do

The commit turns the laptop dashboard into a two-lane demo:

1. **Lane A — real Huawei Band 6.** A new direct collector polls the local
   KinetiQ receiver (`http://127.0.0.1:8000/api/wearable/latest`) and writes
   packets into the same in-process telemetry store used by the RTOS lane.
2. **Lane B — FreeRTOS control.** The existing native/RTOS stream continues to
   drive the mock packet generator.
3. **In-app lane switch.** `/api/mode` changes `_ACTIVE_MODE`; the UI shows a
   `FreeRTOS` button and a lane label.
4. **Conversational AI + presence greeting.** `/api/ask` is wired to the
   assistant; camera motion can trigger a five-second presence greeting.
5. **Report/reporting suite.** New LaTeX/PDF report material and scripts.
6. **Launcher scripts.** `scripts/demo_band_lane.ps1`, `demo_rtos_lane.ps1`,
   `demo_fallback.ps1`.

Most of this is orthogonal to the Band relay. The problematic part is the new
direct Band collector and the way the supplied launcher combines it with the
older stdin bridge.

## 2. Schema status: still the locked eight fields

`laptop/receiver/telemetry_schema.py` still defines exactly:

```
timestamp, heart_rate, spo2, activity, motion_intensity, exertion, steps, ppg_quality
```

The only schema diff in `79815da` is parser robustness: a UTF-8 BOM on bytes
and a leading non-JSON prefix on strings are stripped before `json.loads`.
There is **no stress field** and no optional/null slot for activity, motion or
PPG quality. That is why stress can only be displayed outside the packet.

## 3. Defects in the direct Band collector

`laptop/web/app.py`, `_band_collector_loop()`:

| # | Observation | Why it matters |
|---|---|---|
| 1 | `"timestamp": int(time.time())` is used for every poll; `real_freshness` is never checked | A stale receiver reading is republished with a fresh timestamp and looks live forever. The chart/history then accumulates duplicates of the same measurement at new times. |
| 2 | `steps = real.get("steps", 0)` and `"steps": int(steps or 0)` | A missing step count becomes a genuine-looking `0`, violating the project rule that missing values are `null`/“not measured”, never zero. |
| 3 | `stress_score` is not read at all | The Band's stress value is lost. Their schema cannot carry it; their UI does not show it. |
| 4 | With no camera, `activity = "rest"` and `motion_intensity = 0.0` | Activity is fabricated. The relay has no activity/motion sensor; the honest value is `unknown`, not `rest`. |
| 5 | `ppg_quality = "good"` unconditionally | It is a proxy for “Health returned a completed SpO₂ value”, not a measured PPG quality. This is documented in our chip rather than silently presented as a sensor result. |
| 6 | Duplicate feed in `scripts/demo_band_lane.ps1` | The script starts `band_bridge.py | app.py --stdin --lane band`. `_ACTIVE_MODE` is `band`, so `_band_collector_loop()` also runs. The same reading can arrive once from stdin and once from the direct collector. |
| 7 | RTOS lane is inert under `--stdin` | `_engine_loop()` returns early when `STDIN_MODE` is true (`if _ACTIVE_MODE != "rtos" or STDIN_MODE: ... continue`). The lane button changes the label, but the RTOS generator never produces packets in that launch mode. |
| 8 | Their launcher expects the uninitialized submodule | `demo_band_lane.ps1` defaults to `Tharun work/Huawei-kinetiq-band6`, pinned at `55f727d`; the submodule is not initialized in this checkout, so the script cannot find the receiver/bridge it expects. |

Their direct collector is not used by the Home Edition. The wrapper starts its
own collector and never calls their `main()` (so their collector and `_engine_loop`
are not started automatically). The RTOS engine is started deliberately for the
`/classic` lane switch, with the Band collector pausing while RTOS is active.

## 4. What the integration reuses

Reused unchanged at runtime:

- `laptop/receiver/telemetry_schema.py` — `TelemetryRecord.parse` and
  `to_dict` remain the gate for every packet we store.
- `mock_streamer.derive_exertion(hr, "unknown")` — the upstream repo's own
  heuristic; we do not invent a second one.
- The whole upstream HTTP app and page HTML, served at `/classic`.

Added by our wrapper without touching their tree:

- `/` — Home Edition UI (`kinetiq-integration/home/index.html`).
- `/classic` — their original page plus an additive provenance chip.
- `/band/api` — read-only JSON used by the Home Edition (genuine values,
  freshness, age, stress, steps, HR history for the trend card).
- An in-process collector that:
  - reads only the receiver's genuine slot (`simulated === false`);
  - requires `real_freshness == "live"` and heart rate, SpO₂ and steps;
  - uses the genuine reading timestamp, never `now`;
  - de-duplicates by reading identity;
  - stores nothing when the reading is stale/offline or incomplete;
  - writes `activity="unknown"`, `motion_intensity=0.0`,
    `ppg_quality="good"` as documented schema-legal placeholders;
  - derives `exertion` with the upstream heuristic.
- A separate genuine HR history for the Home Edition trend card, so RTOS
  packets can never leak into the Band trend.
- Lane switching: their `/api/mode` still works; the Band collector pauses in
  RTOS and repaints once on return.

`band_bridge.py` is retained as a compatibility layer for anyone running the
partner's own `demo_band_lane.ps1`. It is **not** part of the Home Edition path.
`run_stack.py` (the old bridge → stdin supervisor) was removed.

## 5. Deliberate omissions in the Home Edition

The Canva reference includes respiratory rate, skin temperature and a readiness
score. The Band 6 relay has no such values, so:

- they are omitted from the calm overview;
- the Detailed readings tab marks respiratory rate, skin temperature and
  readiness as **not measured**, and PPG as **not relayed**;
- no sample value is ever inserted into live mode.

`?preview=1` is a separate visual-preview mode. It uses a sample snapshot and
labels itself “Sample preview” / “Sample readings” / “Demo data only”. It is
never used for a live-state claim.

## 6. Verification performed

- `python -m unittest discover -s kinetiq-integration -p test_*.py` → 25/25.
- `python -m unittest discover -s backend -p test_*.py` → 22/22.
- Runtime check with a local receiver fixture:
  - `/band/api` returned `freshness=live`, `mode=band`, history;
  - `/api/telemetry` returned the eight-field packet with
    `activity=unknown`, `motion_intensity=0.0`, `steps=4181`;
  - `/classic` contained the chip;
  - `POST /api/mode {"mode":"rtos"}` produced an RTOS packet; switching back
    produced a Band packet again;
  - Home Edition rendered live, sample and detail states at 1366×768,
    1366×900, 1366×1123 and 1920×1080.
- The on-device values quoted in the project report are from 2026-10-22
  (HR 60, SpO₂ 100, stress 57, steps 4,181). On 2026-10-23 the phone was
  disconnected, so the final runtime checks used the fixture, not the band.
  They are not presented as hardware evidence.

## 7. Follow-ups and risks

- The partner may fix `_band_collector_loop` upstream. If they do, the wrapper
  can keep using its own collector anyway; the Home Edition contract does not
  depend on their collector.
- Their `/api/mode` endpoint and global `_ACTIVE_MODE` are in-process globals.
  The wrapper runs one server process, so lane switching remains coherent.
- Their `Tharun work/Huawei-kinetiq-band6` submodule remains uninitialized and
  unused. Do not initialize it for the integrated demo; it is not needed and
  would add a nested repository.
- `backend/data/*.csv` health logs are local demo artifacts. They should not be
  published without a separate privacy review.
