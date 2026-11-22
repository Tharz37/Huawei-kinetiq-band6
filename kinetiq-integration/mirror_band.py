#!/usr/bin/env python3
"""Serve the KINETIQ Home Edition with genuine Huawei Band 6 values.

The partner repository is used **as-is** (``git -C external/KinetiQ status
--porcelain`` stays empty). At runtime only, this wrapper:

* imports ``laptop/web/app.py`` without running its ``main()``;
* serves our own ``home/index.html`` at ``/`` (Home Edition);
* keeps their original dashboard available at ``/classic`` with the additive
  provenance chip;
* exposes a read-only ``/band/api`` view of the local KinetiQ receiver;
* feeds their locked eight-field telemetry store from the receiver with an
  honest, in-process collector -- only a fresh genuine reading with heart rate,
  SpO2 and steps is accepted; missing values are never replaced by zero;
* starts their RTOS mock engine as well, so the lane switch in ``/classic``
  still works. The Band lane pauses while RTOS is active and repaints once when
  it returns.

Why the in-process collector instead of their ``--stdin`` script?
-----------------------------------------------------------------
Upstream commit ``79815da`` added a direct Band collector, but it re-stamps
every poll with ``timestamp=now`` without checking ``real_freshness`` (stale
data would look live forever), defaults missing steps to ``0``, and runs at the
same time as the ``--stdin`` bridge in their own ``demo_band_lane.ps1`` (so a
reading can arrive twice). The collector below reuses the same upstream schema
validator and the upstream ``derive_exertion`` heuristic, but gates on
``real_freshness == "live"`` and requires all three genuine values.

Nothing is synthesised
----------------------
* Only the receiver's genuine slot is read (``simulated`` must be false).
* Heart rate, SpO2 and steps must all be present; otherwise nothing is stored
  and the reason is logged to stderr.
* ``activity`` is ``"unknown"`` and ``motion_intensity`` is ``0.0`` because this
  relay does not measure them. ``ppg_quality`` is ``"good"`` as a documented
  proxy (Health returned a completed SpO2 value). ``exertion`` uses the
  upstream repository's own heuristic.
* Stress has no slot in the locked schema. It is shown by the Home Edition and
  the classic provenance chip, never invented inside their packet.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from band_bridge import (
    fetch_snapshot,
    load_upstream,
    map_snapshot,
    reading_key,
)

HERE = Path(__file__).resolve().parent
HOME_PAGE_PATH = HERE / "home" / "index.html"
PREFIX = "[band-home]"
DEFAULT_RECEIVER = "http://127.0.0.1:8000"
DEFAULT_PORT = 8777

# Genuine Band-only HR history for the Home Edition trend card. Kept separately
# from the upstream telemetry store so RTOS sample packets can never leak into
# the Band lane's trend. Process-local; it rebuilds as readings arrive.
HOME_HR_HISTORY: deque[int] = deque(maxlen=24)

CHIP = r"""
<div id="bandbar" title="Genuine Huawei Band 6 readings relayed from the Huawei Health home screen over USB. Stress is shown here because the upstream telemetry schema has no stress field. Activity and motion are not relayed by this path; steps are genuine.">
  <span id="bbdot"></span>
  <span id="bbname">HUAWEI BAND 6</span>
  <span id="bbvals">no reading yet</span>
  <span id="bbmeta">genuine &middot; Health UI relay</span>
</div>
<style>
#bandbar{position:fixed;right:14px;bottom:12px;z-index:9999;display:flex;gap:10px;align-items:center;
 max-width:calc(100vw - 40px);background:rgba(13,17,23,.94);border:1px solid #30363d;border-radius:999px;
 padding:8px 16px;font:13px/1.25 'Segoe UI',Arial,sans-serif;color:#e6edf3;
 box-shadow:0 6px 18px rgba(0,0,0,.45)}
#bandbar #bbdot{width:9px;height:9px;border-radius:50%;background:#8b949e;flex:0 0 auto}
#bandbar #bbname{font-weight:700;font-size:11px;letter-spacing:.6px;color:#9ecbff;white-space:nowrap}
#bandbar #bbvals{font-weight:600;white-space:nowrap}
#bandbar #bbmeta{color:#8b949e;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style>
<script>
(function () {
  var bar = document.getElementById('bandbar');
  var dot = document.getElementById('bbdot');
  var vals = document.getElementById('bbvals');
  var meta = document.getElementById('bbmeta');
  function paint(data) {
    if (data && data.mode && data.mode !== 'band') {
      bar.style.display = 'none';
      return;
    }
    bar.style.display = '';
    if (!data || !data.ok || !data.real) {
      dot.style.background = '#8b949e';
      vals.textContent = 'no reading yet';
      meta.textContent = (data && data.error) ? 'receiver unreachable' : 'genuine \u00b7 Health UI relay';
      return;
    }
    var real = data.real, parts = [];
    if (real.heart_rate_bpm !== null) parts.push('HR ' + real.heart_rate_bpm + ' bpm');
    if (real.spo2_percent !== null) parts.push('SpO\u2082 ' + Math.round(real.spo2_percent) + '%');
    if (real.stress_score !== null) parts.push('Stress ' + real.stress_score);
    if (real.steps !== null) parts.push('Steps ' + Number(real.steps).toLocaleString());
    vals.textContent = parts.length ? parts.join('  \u00b7  ') : 'no reading';
    var fresh = data.freshness || 'offline';
    dot.style.background = fresh === 'live' ? '#3fb950' : (fresh === 'stale' ? '#d29922' : '#f85149');
    var age = '';
    if (data.age_s !== null && data.age_s !== undefined) {
      age = data.age_s < 90 ? (data.age_s + 's ago') : (Math.round(data.age_s / 60) + ' min ago');
    }
    meta.textContent = fresh + (age ? ' \u00b7 ' + age : '') + ' \u00b7 genuine, not simulated';
  }
  function tick() {
    fetch('/band/api', {cache: 'no-store'})
      .then(function (response) { return response.json(); })
      .then(paint)
      .catch(function () { paint(null); });
  }
  tick();
  setInterval(tick, 2000);
}());
</script>
"""

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="9" fill="#0d1c25"/>
<circle cx="16" cy="16" r="9" fill="none" stroke="#8fe6dc" stroke-width="1.4"/>
<circle cx="16" cy="16" r="2.7" fill="#8fe6dc"/>
</svg>"""


def log(message: str) -> None:
    print(f"{PREFIX} {message}", file=sys.stderr, flush=True)


def parse_iso(text: str) -> datetime:
    value = str(text).strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_home_page(path: Path = HOME_PAGE_PATH) -> str:
    """Read the Home Edition page, failing loudly if the asset is missing."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"{PREFIX} home page not found at {path}: {exc}")


def load_app(repo_dir: Path) -> Any:
    """Import the upstream mirror module without executing its ``main()``."""
    import importlib.util

    app_path = repo_dir / "laptop" / "web" / "app.py"
    if not app_path.is_file():
        raise SystemExit(
            f"{PREFIX} upstream mirror not found at {app_path}; clone "
            "https://github.com/siddoit/KinetiQ into external/KinetiQ or pass "
            "--kinetiq-repo"
        )
    spec = importlib.util.spec_from_file_location("kinetiq_upstream_app", app_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"{PREFIX} cannot load {app_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["kinetiq_upstream_app"] = module
    spec.loader.exec_module(module)
    return module


def inject_chip(page: str) -> str:
    """Append the provenance chip to the classic upstream page (additive only)."""
    if "</body>" not in page:
        return page
    return page.replace("</body>", CHIP + "\n</body>", 1)


def build_band_view(
    snapshot: Optional[Dict[str, Any]], error: str = "", now: Optional[datetime] = None
) -> Dict[str, Any]:
    """Compact, display-oriented view of the receiver snapshot."""
    view: Dict[str, Any] = {
        "ok": snapshot is not None,
        "freshness": "offline",
        "age_s": None,
        "reading_ts": None,
        "real": None,
        "error": error,
    }
    if not snapshot:
        return view

    real = snapshot.get("real")
    if not isinstance(real, dict):
        return view

    # The receiver's genuine slot is only genuine when it says so. Never expose
    # a simulated record through this route as if it were a Band reading.
    if real.get("simulated") is not False:
        view["error"] = "receiver real slot is not marked simulated=false; refusing"
        return view

    age: Optional[int] = None
    try:
        current = now or datetime.now(timezone.utc)
        age = max(0, int((current - parse_iso(real.get("timestamp", ""))).total_seconds()))
    except (TypeError, ValueError):
        age = None

    view.update({
        "freshness": snapshot.get("real_freshness") or "offline",
        "age_s": age,
        "reading_ts": real.get("timestamp"),
        "real": {
            "timestamp": real.get("timestamp"),
            "heart_rate_bpm": real.get("heart_rate_bpm"),
            "spo2_percent": real.get("spo2_percent"),
            "spo2_mode": real.get("spo2_mode"),
            "stress_score": real.get("stress_score"),
            "stress_mode": real.get("stress_mode"),
            "steps": real.get("steps"),
            "source": real.get("source"),
            "transport": real.get("transport"),
            "device_model": real.get("device_model"),
            "status": real.get("status"),
            "simulated": real.get("simulated"),
        },
    })
    return view


def ingest_snapshot(
    app_module: ModuleType,
    snapshot: Dict[str, Any],
    derive_exertion: Any,
    last_key: Optional[Tuple[Any, ...]],
) -> Tuple[bool, Optional[Tuple[Any, ...]], str]:
    """Validate and store at most one new genuine reading.

    Returns ``(stored, new_last_key, note)``. ``note`` explains why nothing was
    stored; it is empty for a duplicate poll (not an error).
    """
    packet, note = map_snapshot(snapshot, derive_exertion)
    if packet is None:
        return False, last_key, note

    key = reading_key(snapshot)
    if key == last_key:
        return False, last_key, ""

    record = app_module.TelemetryRecord.parse(packet)
    app_module._store(record.to_dict())
    return True, key, ""


def band_collector_loop(
    app_module: ModuleType,
    repo_dir: Path,
    receiver: str,
    poll_s: float = 1.0,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Poll the receiver and feed only fresh, complete, genuine readings."""
    _, derive_exertion = load_upstream(repo_dir)
    last_key: Optional[Tuple[Any, ...]] = None
    last_note = ""
    last_mode = getattr(app_module, "_ACTIVE_MODE", "band")
    log(f"band lane listening on {receiver} (poll {poll_s:g}s)")

    while not (stop_event and stop_event.is_set()):
        active_mode = getattr(app_module, "_ACTIVE_MODE", "band")
        if active_mode != "band":
            # Do not let a Band reading overwrite the RTOS lane. Reset the
            # de-duplication key so the current reading repaints on return.
            last_key = None
            last_mode = active_mode
            if stop_event is not None:
                stop_event.wait(poll_s)
            else:
                time.sleep(poll_s)
            continue

        if last_mode == "rtos":
            last_key = None  # force one repaint after switching back
        last_mode = active_mode

        try:
            snapshot = fetch_snapshot(receiver)
        except Exception as exc:  # receiver restarting/offline
            note = f"receiver unreachable ({type(exc).__name__}: {exc})"
            if note != last_note:
                log(note)
            last_note = note
        else:
            if getattr(app_module, "_ACTIVE_MODE", "band") != "band":
                # The lane switched while we were fetching; do not overwrite
                # the RTOS lane with a late Band packet.
                last_key = None
                last_mode = getattr(app_module, "_ACTIVE_MODE", "band")
                if stop_event is not None:
                    stop_event.wait(poll_s)
                else:
                    time.sleep(poll_s)
                continue
            try:
                stored, last_key, note = ingest_snapshot(
                    app_module, snapshot, derive_exertion, last_key
                )
            except Exception as exc:  # upstream validator is the gate
                stored = False
                note = f"upstream schema rejected packet ({type(exc).__name__}: {exc})"

            if stored:
                real = snapshot.get("real") or {}
                heart_rate = real.get("heart_rate_bpm")
                if isinstance(heart_rate, (int, float)) and not isinstance(heart_rate, bool):
                    HOME_HR_HISTORY.append(int(heart_rate))
                log(
                    "stored hr={0} spo2={1} stress={2} steps={3} "
                    "(activity/motion not relayed)".format(
                        real.get("heart_rate_bpm"),
                        real.get("spo2_percent"),
                        real.get("stress_score"),
                        real.get("steps"),
                    )
                )
                last_note = ""
            elif note and note != last_note:
                log(note)
                last_note = note
            elif not note:
                last_note = ""

        if stop_event is not None:
            stop_event.wait(poll_s)
        else:
            time.sleep(poll_s)


def build_handler(
    app_module: Any, receiver: str, home_page: str
) -> type:
    """Subclass (never modify) the upstream request handler."""

    class HomeHandler(app_module.Handler):  # type: ignore[misc, valid-type]
        def _send(self, code: int, body: Any, ctype: str) -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = urlparse(self.path).path
            if path in ("/", "/index.html", "/home"):
                self._send(200, home_page, "text/html; charset=utf-8")
                return
            if path in ("/classic", "/classic/"):
                self._send(200, app_module.PAGE, "text/html; charset=utf-8")
                return
            if path == "/band/api":
                self._serve_band_api()
                return
            if path in ("/favicon.ico", "/favicon.svg"):
                self._send(200, FAVICON, "image/svg+xml")
                return
            super().do_GET()

        def _serve_band_api(self) -> None:
            try:
                snapshot = fetch_snapshot(receiver)
                view = build_band_view(snapshot)
            except Exception as exc:
                view = build_band_view(None, f"{type(exc).__name__}: {exc}")
            view["mode"] = getattr(app_module, "_ACTIVE_MODE", "band")
            view["history"] = list(HOME_HR_HISTORY)
            try:
                view["lane"] = app_module._current_lane_label()
            except Exception:
                view["lane"] = ""
            self._send(200, json.dumps(view), "application/json")

    return HomeHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kinetiq-home",
        description=(
            "Serve the KINETIQ Home Edition with genuine Band 6 values; the "
            "upstream clone is imported at runtime and never edited."
        ),
    )
    parser.add_argument(
        "--kinetiq-repo",
        type=Path,
        default=HERE.parent / "external" / "KinetiQ",
        help="path to the upstream KINETIQ clone",
    )
    parser.add_argument(
        "--receiver",
        default=DEFAULT_RECEIVER,
        help=f"KinetiQ receiver base URL (default {DEFAULT_RECEIVER})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"preferred local port (default {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=1.0,
        help="seconds between receiver polls (default 1.0)",
    )
    parser.add_argument(
        "--strict-port",
        action="store_true",
        help="fail if the preferred port is busy instead of trying port+1",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll <= 0:
        log("error: --poll must be > 0")
        return 2

    repo = args.kinetiq_repo.resolve()
    home_page = load_home_page()
    app = load_app(repo)
    if getattr(app, "TelemetryRecord", None) is None or getattr(app, "StreamerState", None) is None:
        log("upstream receiver modules are not importable; cannot run the mirror")
        return 2

    app.PAGE = inject_chip(app.PAGE)
    app._ACTIVE_MODE = "band"
    app._source = "HUAWEI BAND 6"
    handler = build_handler(app, args.receiver, home_page)

    # Their RTOS mock engine still works from /classic. It idles in band mode.
    if hasattr(app, "_engine_loop"):
        threading.Thread(
            target=app._engine_loop, daemon=True, name="kinetiq-rtos-engine"
        ).start()
    else:
        log("upstream has no _engine_loop; the classic RTOS lane will stay idle")
    threading.Thread(
        target=band_collector_loop,
        args=(app, repo, args.receiver, args.poll),
        daemon=True,
        name="kinetiq-band-lane",
    ).start()

    server = None
    chosen = args.port
    last_error: Optional[Exception] = None
    candidates = (args.port,) if args.strict_port else (args.port, args.port + 1)
    for candidate in candidates:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            chosen = candidate
            break
        except OSError as exc:
            last_error = exc
    if server is None:
        if args.strict_port:
            log(f"cannot bind port {args.port}: {last_error}. Close the old mirror first.")
        else:
            log(f"cannot bind ports {args.port}/{args.port + 1}: {last_error}")
        return 2

    print(
        f"KINETIQ Home Edition: http://127.0.0.1:{chosen}/  "
        f"(classic at /classic; upstream app.py unmodified; receiver={args.receiver})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
