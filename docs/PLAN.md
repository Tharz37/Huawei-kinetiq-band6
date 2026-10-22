# KinetiQ Task 001 — Implementation Plan (from actual repo inspection)

## Phase-1 findings (2026-10-15, `D:\Engineering\s7\minor`)

- The directory is **not a git repo** and contains exactly one file:
  `KINETIQ_V1_final_review.pptx`. No website, backend, telemetry schema,
  dashboard, receiver, or simulation source exists on disk.
- The slides describe the intended V1 system: ESP32 + FreeRTOS sensing
  (MPU6050/DHT22/sonar/cam), structured 8-field telemetry with strict
  validation, a Python laptop dashboard (`dashboard --source stdin`,
  received/dropped counters, CSV logging), a BLE bridge mock, an
  ollama (`gemma2:2b`) voice assistant with grounding checks, and a Wokwi
  firmware simulation. Physical sensor bring-up was still pending.
- Consequence: there is **nothing to break and nothing to reuse verbatim**.
  The plan below recreates the assumed laptop side minimally (schema v1,
  strict validation, CSV logging, live dashboard, labelled simulation) and
  adds the new wearable path as a backward-compatible extension
  (same `schema_version: 1`, new optional fields, separate real/sim slots).

## Build order (smallest working end-to-end first)

1. **Laptop receiver** (`backend/kinetiq_receiver.py`, stdlib only):
   validated `POST /api/wearable`, token auth, CSV logs, SSE live push,
   serves dashboard. Heart-rate first; SpO₂ rides the same payload.
2. **Tests** (`backend/test_receiver.py`, 17 cases): validation, timestamps,
   nulls, malformed, auth, disconnect behaviour, sim/real separation.
3. **Mock sender** (`backend/mock_sender.py`): explicitly labelled
   `SIMULATED` pipeline test (source `mock_simulator`, `simulated: true`).
4. **Dashboard** (`web/index.html`): wearable section (BPM, SpO₂, timestamps,
   realtime/historical/stale/unavailable/denied/unsupported, Band 6
   attribution, bridge indicator), SSE + poll fallback, separate SIMULATED
   panel. Served by the laptop backend (static hosts can't receive phone data).
5. **Android bridge** (`android-bridge/`, Kotlin, API 33): status screen,
   Health Kit authorisation, HR start/stop, one-shot SpO₂ query, token-authed
   LAN upload with retries. Written against signatures verified in Huawei's
   official `hms-health-demo-kotlin` sample; two scope/constant points marked
   `[V1]`/`[V2]` for console/SDK confirmation at build time.
6. **Docs**: setup guide, this plan, final report with honest test matrix.

## Non-goals / guards

- No credentials, tokens, fingerprints, or health data in git (see `.gitignore`;
  `config.example.json` carries placeholders only).
- No invented SDK methods or faked device results; unverifiable items are
  labelled BLOCKED with exact next steps.
- Prioritise one real HR reading on the website; SpO₂ must not risk the HR path.
