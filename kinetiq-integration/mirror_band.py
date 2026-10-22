#!/usr/bin/env python3
"""Run the upstream KINETIQ browser mirror with genuine Band 6 values.

The upstream repository is used **as-is**:

* ``laptop/web/app.py`` is imported at runtime, never copied or edited
  (``git -C external/KinetiQ status --porcelain`` stays empty);
* telemetry enters through its documented ``--stdin`` mode, fed by
  ``band_bridge.py``;
* two additive things are layered on at runtime only:
    - ``GET /band/api``  a read-only view of the local KinetiQ receiver;
    - a small provenance chip pinned to the page showing the genuine source,
      freshness and the stress score -- because the upstream locked schema has
      no stress field and the upstream footer always says "simulated".

Nothing in the upstream request flow, pages or APIs is replaced or hidden.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

PREFIX = "[band-mirror]"
DEFAULT_RECEIVER = "http://127.0.0.1:8000"
DEFAULT_PORT = 8777

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
  var dot = document.getElementById('bbdot');
  var vals = document.getElementById('bbvals');
  var meta = document.getElementById('bbmeta');
  function paint(data) {
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


def load_app(repo_dir: Path) -> Any:
    """Import the upstream mirror module without executing its ``main()``."""
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
    """Append the provenance chip to the upstream page (additive only)."""
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
        "real": None,
        "error": error,
    }
    if not snapshot:
        return view

    real = snapshot.get("real")
    if not isinstance(real, dict):
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
            "simulated": real.get("simulated"),
        },
    })
    return view


def fetch_snapshot(receiver: str, timeout: float = 2.5) -> Dict[str, Any]:
    url = receiver.rstrip("/") + "/api/wearable/latest"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("receiver returned a non-object snapshot")
    return data


def build_handler(app_module: Any, receiver: str) -> type:
    """Subclass (never modify) the upstream request handler."""

    class BandHandler(app_module.Handler):  # type: ignore[misc, valid-type]
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if urlparse(self.path).path == "/band/api":
                self._serve_band_api()
                return
            super().do_GET()

        def _serve_band_api(self) -> None:
            try:
                snapshot = fetch_snapshot(receiver)
                view = build_band_view(snapshot)
            except Exception as exc:
                view = build_band_view(
                    None, f"{type(exc).__name__}: {exc}"
                )
            self._send(200, json.dumps(view), "application/json")

    return BandHandler


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        prog="mirror-band",
        description=(
            "Serve the upstream KINETIQ mirror (stdin mode) plus a read-only "
            "Band 6 provenance view. The upstream site is not modified on disk."
        ),
    )
    parser.add_argument("--kinetiq-repo", type=Path,
                        default=here.parent / "external" / "KinetiQ",
                        help="path to the upstream KINETIQ clone")
    parser.add_argument("--receiver", default=DEFAULT_RECEIVER,
                        help=f"KinetiQ receiver base URL (default {DEFAULT_RECEIVER})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"preferred local port (default {DEFAULT_PORT})")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # The upstream module reads --stdin from sys.argv at import time.
    if "--stdin" not in sys.argv:
        sys.argv.append("--stdin")
    app = load_app(args.kinetiq_repo.resolve())
    if getattr(app, "TelemetryRecord", None) is None or getattr(app, "StreamerState", None) is None:
        log("upstream receiver modules are not importable; cannot run the mirror")
        return 2
    app.PAGE = inject_chip(app.PAGE)
    handler = build_handler(app, args.receiver)

    threading.Thread(target=app._stdin_loop, daemon=True).start()

    server = None
    chosen = args.port
    last_error: Optional[Exception] = None
    for candidate in (args.port, args.port + 1):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            chosen = candidate
            break
        except OSError as exc:
            last_error = exc
    if server is None:
        log(f"cannot bind ports {args.port}/{args.port + 1}: {last_error}")
        return 2

    print(
        f"KINETIQ mirror + Band 6: http://127.0.0.1:{chosen}/  "
        f"(upstream app.py unmodified; receiver={args.receiver})",
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
