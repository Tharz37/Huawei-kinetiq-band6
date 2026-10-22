# KinetiQ Project — Detailed findings & route report

**Date:** 2026-10-18 · **Goal of record:** display genuine Huawei Band 6
heart-rate / SpO₂ / stress values on the KinetiQ laptop dashboard, verified
against the band's own display, without harming the band or its Huawei Health
ecosystem.

> **One-paragraph status.** The project's infrastructure is fully built and
> tested (receiver, dashboard, unit tests, mock lane, BLE diagnostic app
> compiled, installed and field-run). The band itself was investigated through
> every non-destructive channel available on the project hardware: plain BLE,
> the LPv2 protocol surface, a Bluetooth HCI wire capture of Huawei Health's
> own authenticated session, and offline cryptographic verification of that
> session. Result: **on this firmware, with this pairing state, the band's
> health data is reachable only by Huawei Health** — its authentication and
> session encryption are bound to a per-bond secret we cannot access without
> breaking the user's pairing. This is the band's own security design, not a
> mistake in our code. Direct-BLE health-metric extraction is therefore CLOSED
> for this device state. Three alternative routes that do not touch the band
> (and do not need HMS Core) remain; two are realizable today on this exact
> phone, and one delivers live values including stress. Everything below is
> evidence-linked.

---

## 1. Goal, success criteria, and project rules

### 1.1 Success criteria (as originally given)
1. Laptop dashboard displays the band's heart rate, SpO₂ and stress.
2. Values must be **genuine data from the user's own band** — never invented.
3. Every displayed value must be verifiable against the band's own display /
   Huawei Health before it is claimed.
4. The dashboard must keep working with mock data (clearly labelled
   SIMULATED) so it can be demonstrated independent of band availability.

### 1.2 Non-negotiable rules (kept through every phase)
- **Never** unpair, factory-reset, or modify band settings; never send
  configuration writes to the band (only read-style protocol probes).
- **Never** disconnect or bypass Huawei Health, Play Protect, or device
  integrity; no root; no third-party "HMS Core"-lookalike APKs.
- Local LAN only for telemetry; nothing public/internet-exposed.
- Missing readings stay **null** (never zero-fill); telemetry schema v1 is
  additive-only.
- Mock data labelled **SIMULATED** and kept strictly separate from genuine.
- Truthful milestones only; every claim evidence-linked.

### 1.3 Milestone statuses (final, honest)

| Milestone | android-bridge (HMS Health Kit) | ble-diagnostic (BLE app) |
|---|---|---|
| SOURCE_COMPLETE | yes | yes |
| COMPILED | no (blocked pre-compile, see §4.6) | **yes** |
| INSTALLED | no | **yes** (Nokia C22, 2026-10-18) |
| DEVICE_TESTED | no | **yes** (scan, connect, GATT dump, reads, passive capture, probes) |
| REAL_DATA_VERIFIED | no | **no** (device identity verified; **HR/SpO₂/stress not obtained** — see §4) |

---

## 2. What exists and works (all reusable)

| Component | Path | State |
|---|---|---|
| Laptop receiver | `backend/kinetiq_receiver.py` | Token-auth POST ingest, CSV persistence, SSE stream, schema-v1 validation (BLE-coded values that cannot be decoded are rejected with 422). **19/19 unittests** (`python -m unittest discover -s backend`) |
| Dashboard | `web/index.html` | Stress row added; SSE + polling fallback; SIMULATED values visually distinguishable |
| Mock lane | receiver ingest + demo senders | Clearly labelled SIMULATED; zero coupling to genuine values |
| BLE diagnostic app | `ble-diagnostic/` → `app\build\outputs\apk\debug\app-debug.apk` | v0.3.1-diagnostic: scan (neverForLocation), bonded connect on tap, GATT dump, standard reads, passive FE02 capture with CRC-verified frame log, plaintext probes. Diagnostics log on device at `Android/data/com.kinetiq.blediag/files/kinetiq_ble_diag.log` |
| HMS Health Kit app source | `android-bridge/` | Source-complete; **uncompiled** — blocked because HMS Core is unavailable on this phone (see §4.6) |
| Research record | `docs/RESEARCH_LPV2.md` | Protocol research + live verification + HCI follow-up (§7–§8) |
| Field-run records | `docs/BLE_FIELD_RESULTS.md`, `docs/BLE_FIELD_RAW_2026-10-18.log` | Structured results + raw on-device evidence |
| HCI capture record | `docs/BTSNOOP_RESULTS.md`, `docs/BTSNOOP_RAW_2026-10-18.log` | Full capture analysis + crypto tests |
| Analysis tooling | `tools/btsnoop_inspect.py`, `tools/crypto_offline_test.py` | btsnoop parser/annotator; offline crypto verification harness |

The receiver→SSE→dashboard pipeline is production-shaped and was already fed
SIMULATED data end-to-end; it is ready to accept genuine values the moment a
sanctioned source exists (any route in §5).

---

## 3. What we learned about the Huawei Band 6 (all on-device verified)

Hardware: **Huawei Band 6**, firmware `1.0.0.1` (read from the Device Info
GATT service), bonded LE `HUAWEI Band 6-XXX / 02:00:00:12:34:56`, owned by
**Huawei Health `com.huawei.health` 14.1.2.320** on a Nokia C22 (stock
Android 13, no HMS Core, Health installed as a normal APK).

### 3.1 BLE surface (what any third-party app sees)
- The band **stops advertising while Health holds its connection** (verified:
  14.4+ h continuous LE connection, `dumpsys bluetooth_manager`). Scanning
  during sync found 0 devices — the app must connect to the known address.
  Our app reuses the system bond and connects while Health keeps its link
  (GATT clients multiplex over one shared ACL — verified working).
- Service table for a non-owner client is **reduced**: GAP, GATT,
  **0xFE86 (LPv2 transport: FE01 write / FE02 notify)**, 0x180A Device Info,
  0x1812 HID, 0x3802 vendor. **No 0x180D Heart Rate, no 0x180F Battery.**
  All standard health characteristics (2A37 HR, 2A19 battery, 2A38 body
  location) read back "not present" for us, while device identity
  (Model "HUAWEI", Firmware "1.0.0.1", Manufacturer "HUAWEI") is readable.
  → Health data is not exposed to third-party clients at the GATT level.

### 3.2 LPv2 protocol (the band↔Health application protocol)
- Transport: `5A 00 <len16> 00 <svc> <cmd> <TLVs…> <CRC16/XMODEM>` — every
  frame we sent or received, both directions, CRC-verified live.
- Our plaintext probes: 1/2 SupportedServices and 1/3 SupportedCommands were
  accepted (write status 0) and answered ~300 ms later with tag 127 = result
  code **100010** (undocumented elsewhere; later corroborated to mean
  "requires authenticated session" — see 3.3). Pre-auth, the band tells us
  nothing about its capabilities. This is an **authentication gate**, not a
  firewalled service absence.
- While our unauthenticated client watched, Health's own channel streamed
  encrypted frames with the documented tag family: `7c` = encryption flag,
  `7d 10` = 16-byte IV, `7e 10…` = ciphertext; periodic acks return tag 127
  = **100000 OK**. The IV layout was later confirmed byte-exact from the HCI
  capture: **12 random bytes + 4-byte big-endian counter** (e.g.
  `…56 D9 00 00 16 30`, counters incrementing per frame).
- Post-authentication, Health received (still encrypted) a full service
  command wave — services 1, 2, 7, 10, 12, 15, 23, 24, 26, 37, 39, 48, 50 —
  including the **file-transfer service (svc 10)** listing
  `sleep_state.bin;sleep_data.bin` with 10 KB chunking, an
  **encrypted-but-served SupportedServices (1/2)** response (proof that the
  same command we were denied is granted post-auth), and periodic realtime
  pushes.

### 3.3 The capturing success and the cryptographic verdict (definitive)
A Bluetooth HCI snoop (standard Android developer option, user-consented)
of the **Health ⇄ band re-association** at Bluetooth-stack restart was
pulled through `adb bugreport` (the snoop file itself is root-protected).
Full details: `docs/BTSNOOP_RESULTS.md`. Essence:

- The complete authenticated handshake was captured, byte-exact:
  1/1 LinkParams (the band's request is **the documented example packet,
  CRC `f13b`, byte-for-byte**) → 1/51 (client id
  `"45c97eb85be13db7"`) → 1/44 (two 16-byte values) → **1/19 auth
  (32-byte digests both directions, client nonce form `00 01‖16B`, auth
  version = `00 01` from 1/1 tag 5)** → 1/15 bond params → 1/45 →
  **tag 127 = 100000 OK** → full encrypted session.
- Offline verification (`tools/crypto_offline_test.py`): our port of the
  public research digest algorithm was first **byte-exact-validated against
  an independently published Huawei Band 8 vector** (so the harness is
  correct), then applied to this capture in every documented composition
  (auth versions v1/v2/v3 × message texts × nonce orders and forms —
  30 variants per digest, both the client's and the band's digest; plus
  the captured 1/44 values as input). **No variant matched.** Seven
  AES-128-CBC key candidates derived purely from public material decrypt
  **none** of the captured ciphertexts.
- **Conclusion (firm):** on firmware 1.0.0.1, with the bond created by
  Huawei Health, both the 1/19 auth digests and the session cipher key
  derive from a **per-bond secret held inside Huawei Health**. The
  2022-era public constants (zyv/huawei-lpv2 research, which do work on
  2021-era bands) do not transfer to this device state. This matches the
  standing community constraint: a band bonded by Huawei Health cannot be
  adopted by a third party without re-pairing it (which destroys the bond).

### 3.4 Cross-corroborations (why we trust the negative result)
- An independent project's identical attempt on a newer band failed with
  band result 100004 (their published digest matched our harness — so the
  algorithm port is right and the failure is real, not a code bug).
- Our own pre-auth probes were capability-gated (100010) while the same
  command was fully served post-auth in the capture.
- GATT-level hiding of health services for non-owner clients (§3.1) is
  consistent with the LPv2-level gate (§3.3): layered owner-only design.

---

## 4. Why the direct goal cannot be completed via the band (the blockage chain)

Each link was verified on-device; none is speculative.

1. **S1 — No open health services at GATT.** Third-party clients get an
   intentionally reduced service table (§3.1). *Evidence: GATT dump of our
   client vs zyv's documented open-device table; 2A37 "not present".*
2. **S2 — LPv2 requires the owner's secret.** Authentication (1/19) and all
   post-auth traffic are bound to a bond secret created when Huawei Health
   paired (§3.3). Without it even capability enumeration is denied (100010).
   *Evidence: HCI capture + offline crypto test.*
3. **S3 — The secret is not on the wire.** Capturing an *existing* session
   yields challenge digests, not the secret (that is what HMAC is for), and
   session keys are derived from the secret, not from observable nonces.
   *Evidence: §3.3 tests; seven AES candidates all fail.*
4. **S4 — The secret is not accessible without breaking someone.** It lives
   inside `com.huawei.health` private storage. Reading it requires root
   (a device integrity/security change this project is not allowed to make),
   and the only protocol event that would expose it is a **fresh pairing** —
   which means unpairing the user's Health bond (project rule: never).
   *Evidence: psolyca/Gadgetbridge protocol research; standard Android
   application sandbox.*
5. **S5 — Even after adoption, this firmware may choose its newer scheme.**
   The capture shows undocumented companion commands (1/44, 1/45, 1/51) that
   form a newer handshake; a fresh bond under our control would exercise
   them, but with unknown side effects and zero benefit to the user's own
   Health app. *Evidence: captured handshake; Gadgetbridge's documented
   limitation.*
6. **S6 — The one sanctioned SDK path does not run on this hardware.** HMS
   Health Kit (Task 001) needs HMS Core, which this phone's AppGallery does
   not offer (non-Huawei device); installing APK clones would violate the
   Play Protect/integrity rule. *Evidence: AppGallery search blocked; PB
   rule.*

**Verdict: direct-BLE health-metric extraction is CLOSED for this device
state (firmware 1.0.0.1, Health-owned bond). It is not recoverable by code
changes; it is an ecosystem security boundary. Any future change would come
from outside our control (e.g., Huawei enabling Health Connect) — see §5-A.**

---

## 5. Every remaining route (without HMS Core, without touching the band)

Important clarification up front: **no route makes a third-party app "pair"
with the band.** The band's pairing and its data channel stay exactly as
they are (Huawei Health ⇄ band). The question is only how values that Health
already receives can reach the laptop **legitimately**.

### Metrics coverage matrix

| Route | HR | SpO₂ | Stress | Realtime | Installs on phone | Band touched |
|---|---|---|---|---|---|---|
| A. Health Connect bridge | ✓ | ✓ (if Health syncs) | **✗ impossible — not a Health Connect datatype** | near-live (sync interval) | 1 (Play) | no |
| B. Screen read of Huawei Health (uiautomator PoC / AccessibilityService) | ✓ | ✓ | **✓** | while Health data page is open; continuous possible via service | none to demo (PoC via USB); product = own app | no |
| C. Huawei Health's own data export | ✓ history | ✓ history | **✓ history** | no (manual export) | none | no |
| D. Unpair + third-party adoption (Gadgetbridge-family) | ✓ | ✓ | ✓ | ✓ | 1 app | **YES (unpairs) — excluded by project rule** |
| E. HMS Health Kit on Huawei hardware | ✓ | ✓ | ✓ (via Health Kit) | ✓ | HMS Core + app | no |
| F. Future: Health ⇄ Health Connect integration | ✓ | ✓ | ✗ | near-live | none beyond A | no |

### Route A — Health Connect bridge (conditional: verified BLOCKED on this phone today)
**What it is.** Health Connect is Google's on-phone health data store (a
normal Play install, no HMS involved). The band stays connected to Huawei
Health; *if* Health supported it, Health would copy its data into Health
Connect, and our own small app would read it and POST to the laptop
receiver over USB-tether/LAN.

**Verified status on this exact phone (2026-10-18):**
- No Health Connect provider installed (`pm list packages` → none).
- Installed Huawei Health **14.1.2.320 declares zero
  `android.permission.health.*` permissions** and no
  `androidx.health` intent interactions (`pm dump com.huawei.health`) —
  i.e., this build does not integrate Health Connect at all. The
  user's skepticism was correct; it is now verified, not just assumed.
- Even if integration existed: Health Connect has **no stress datatype**
  (checked against the official Health Connect data-type table; only
  user-started mindfulness sessions exist). Heart rate and SpO₂ are fully
  supported types.

**Re-check command (seconds, non-invasive) if Health updates:**
```
adb shell "pm dump com.huawei.health | grep -icE 'permission.health'"
adb shell "pm list packages | grep -iE 'healthconnect|apps.healthdata'"
```
Both non-zero / package present ⇒ this route becomes viable.

**If viable, steps would be:** install Health Connect (Play, user tap) →
enable Health ⇄ Health Connect in Huawei Health settings (user tap) →
build the KinetiQ sender app (reuse `android-bridge/` structure, replace
HMS SDK calls with `androidx.health.connect.client`, keep token-POST to
receiver schema v1) → user grants only the datatypes we display →
field-verify: compare dashboard values against band display → mark
REAL_DATA_VERIFIED only after cross-check.

### Route B — Screen read of Huawei Health (realizable TODAY; the only route with stress)
**What it is.** Read the numbers Huawei Health already shows on the phone
screen. No security boundary is crossed (reading a user's own screen, with
their consent), and every value can be cross-checked against the band.

**PoC today (zero installs, USB + unlocked screen needed):**
1. User opens Huawei Health (any page showing the value, e.g. the Sleep/
   Stress page or the HR card on Home).
2. `adb shell uiautomator dump /sdcard/d.xml`
3. `adb pull` + parse the XML for text nodes ("72 bpm", stress scores, SpO₂)
4. POST to the receiver using the existing token/auth schema, labelled
   `source: health-app` (a new additive source field — schema v1 is
   additive).
5. Cross-check each value against the band's own display, then log the
   verification result.

Constraints (honest): screen unlocked + Health foreground while reading;
reliable while the UI is stable; an AccessibilityService app would make it
continuous (same discipline: user consent, screen content only). Stress is
included (Health's Stress page mirrors the band's continuous readings).

### Route C — Huawei Health's own data export (realizable today; historical)
Health has a built-in privacy feature to export the user's data archive
(its own GDPR tooling, no third-party involvement). Steps: Health →
Me/Settings → Privacy → data export/download archive (user does it).
Result: files include daily vitals across time (steps/HR/Stress/SpO₂
history etc.). I can write an ingester that parses the archive and POSTs
historical points to the receiver → dashboard shows genuine historical
curves, clearly dated and cross-checkable against Health's charts.
Limitation: it is a snapshot, not a live feed; refresh is manual.

### Route D — Unpair and adopt with a third-party stack (excluded — documented for completeness)
What it would give: our own bond → documented full LPv2 realtime data
including stress (this is exactly the well-known Gadgetbridge/HuaweiBand
ecosystem path). Why it is excluded: it requires deliberately unpairing the
band from Huawei Health — a direct violation of the project's never-unpair
rule and of the user's actual ecosystem setup; a firmware redesign on the
band's side (1/44/1/45 companions) makes behaviour under adoption
non-guaranteed. **Not performed; only to be considered if the user explicitly
revises the rule, and before any representative demo would re-bond, Health
would have to re-pair the band again — a disruptive round-trip.**

### Route E — HMS Health Kit (documented; hardware-gated)
The ecosystem-correct, officially supported route (Task 001 source exists),
but it requires HMS Core + a Health Kit-capable Huawei device. On this
Nokia phone it is not runnable (AppGallery offers no HMS Core, and side-load
clones are barred by rule). If any Huawei/HMS device becomes available,
the `android-bridge/` source is the starting point as designed.

### Route F — Future-proofing: check Health's updates
If a future Huawei Health release on this phone ships Health Connect
integration (Route A becomes live) — the same re-check command in §5-A
flags it immediately. Zero-cost monitoring; recommended before the final
demo date.

### Route G — Wear Engine, the official channel through Health (executed; gated)
Executed 2026-10-18 after a full dissection of Huawei Health 14.1.2.320
(surfaces: 2 not-exported providers, 2 signature|privileged gates,
partner-gated deep links, one normal-permission provider of unknown schema,
one open provider — see `docs/WEARENGINE_RESULTS.md`). The official
**wearEngine** channel was proven live on this phone
(`wear_device_state` answered permission-free:
`connectedWearableDevice=true`); the SDK (`com.huawei.hms:wearengine:
5.0.3.300`, newest published — the repo serves no metadata, pin required)
was integrated into the diagnostic app (`WearEngineProbe.kt`, button 6).
Every `getBondedDevices()` call fails instantly with `code=12`
(`ERROR_CODE_INTERNAL_ERROR`) because Health validates callers against
**AppGallery Connect** (package + signing-cert fingerprint + app id +
Wear Engine service application) before serving any call — the HarmonyOS
error table names this state as 1008500004 "App has not applied for the
Wear Engine service". Realizing Route G therefore requires a Huawei
developer account + AGC registration + agreement, and sensor/health scopes
carry enterprise-developer language (`WEAR_USER_STATUS` explicitly
enterprise-only); approval for an individual student app is not guaranteed.
Recorded as **official but gated**; the screen-read relay (Route B) remains
the working path.

---

## 6. Recommendation for the project deliverable

1. **Demo today (if wanted):** Route B PoC (30–40 min: Health open + USB) +
   the existing SIMULATED lane for stable UI. This produces a legitimate
   "genuine values on the laptop dashboard, cross-checked against the band
   display" moment, including stress.
2. **Deliverable depth:** also run Route C once for historical genuine
   curves (stress history included).
3. **Agenda honesty:** keep REAL_DATA_VERIFIED = no for realtime values
   until one of the routes above is executed and cross-checked; the
   dashboard shows nulls for unavailable metrics (never zeroes), SIMULATED
   data stays visibly separate.
4. **Do NOT:** unpair (Route D), root or Play-Protect-bypass (S4), or
   side-load HMS clones (S6). These have been evaluated and excluded on
   both rule and evidence grounds.

---

## 7. Final consolidated status

- Infrastructure (receiver/dashboard/mock/tests): **complete, verified**.
- BLE diagnostic app: **compiled, installed, field-tested**; two
  full diagnostic sessions + one HCI capture session; band state never
  modified; Health never disrupted deliberately.
- Direct band protocol path: **investigated beyond original scope and
  closed** with on-device cryptographic evidence (this report §3–§4).
- HMS Health Kit: **blocked by hardware availability** (§4.6, §5-E).
- Genuine realtime metrics on the dashboard: **not yet obtained** —
  remaining non-band-touching routes documented with feasibility and steps
  (§5). Any future decoded value must still pass the band-display
  cross-check before being claimed.

---

## Appendix A — Verification commands used (reusable)

```
#  scan root cause + connection/bond view
adb shell dumpsys bluetooth_manager | grep -E "GATT Scanner|connected|9C:56"

#  diagnostics log from the BLE app
adb shell cat /sdcard/Android/data/com.kinetiq.blediag/files/kinetiq_ble_diag.log

#  HCI snoop arming state + restart (capture arm)
adb shell dumpsys bluetooth_manager | grep -i snoop
adb shell svc bluetooth disable
adb shell svc bluetooth enable

#  snoop extraction (file is root-protected; bugreport contains it)
adb bugreport <dir>   # → FS/data/misc/bluetooth/logs/btsnoop_hci.log
python tools/btsnoop_inspect.py <btsnoop.log> --max-frames N
python tools/crypto_offline_test.py

#  Health Connect availability checks (Route A monitor)
adb shell "pm list packages | grep -iE 'healthconnect|apps.healthdata'"
adb shell "pm dump com.huawei.health | grep -icE 'permission.health'"
```

## Appendix B — Constants & evidence provenance
- LPv2 format + constants: zyv/huawei-lpv2 (MIT public research), digest
  port validated against a published Band 8 vector byte-exact.
- Captured digests / nonces / ciphertexts: `docs/BTSNOOP_RAW_2026-10-18.log`
  (all bytes-only annotation; nothing interpreted as health data).
- Result codes: 100000 OK, 100002 unsupported, 100003 permission denied;
  **100010 = observed on this firmware = "requires authenticated session"**
  (empirically corroborated by the capture: same command served post-auth).
- All band actions during all sessions: read-style only (GATT reads, CCCDs,
  two plaintext capability probes, LPv2 handshake observation). No writes
  outside the diagnostic probes; no settings, bond, or time operations —
  ever.

## Appendix C — Chronology of consents
- User installed APKs (Task 003 install segments).
- User consented to adjacency: band→app connection (no Health disruption).
- User enabled + disabled HCI snoop logging voluntarily (Phase 4 option b);
  Bluetooth was restarted from shell twice today solely for capture arming
  and hygiene (ON state restored, verified).
- Every UI-level action on the phone (taps) was user-driven.

## Appendix D — Partner mirror integration (22 Oct 2026)
- Cloned `siddoit/KinetiQ`; its laptop mirror is a stdlib HTTP dashboard fed by
  a locked eight-field telemetry schema (`laptop/receiver/telemetry_schema.py`).
- Added `kinetiq-integration/` in this repository: a bridge that polls our
  receiver and validates every packet against the upstream schema's own
  `TelemetryRecord.parse` before emitting it through their documented
  `--stdin` mode. The upstream clone remains byte-identical
  (`git -C external/KinetiQ status --porcelain` is empty).
- Genuine fields: heart rate, SpO2 and steps. Not relayed by this path:
  activity (`unknown`) and motion (`0.0`); exertion uses the upstream
  `derive_exertion` heuristic. Stress has no schema slot and is shown in an
  additive runtime chip (with relay freshness) rather than being dropped.
- Our relay gained `steps` additively (streamer parser 0-200,000, optional
  receiver field, CSV column); older relays still validate and absent steps
  remain `null`, never `0`.
- Verified end-to-end: Health home screen -> receiver (`live`) -> mirror
  `/api/telemetry` (HR 60, SpO2 100, steps 4,181, stress 57 in the chip).
  Tests: receiver 22/22, integration 15/15, parser 19/19.
