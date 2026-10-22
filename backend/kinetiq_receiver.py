#!/usr/bin/env python3
"""KinetiQ laptop receiver (schema v1, backward-compatible wearable extension).

Serves the KinetiQ dashboard, accepts validated wearable telemetry from the
Android bridge over the trusted local Wi-Fi network, logs to CSV, and pushes
live updates to browsers via Server-Sent Events (SSE).

Endpoints:
  GET  /                     -> dashboard (web/index.html)
  GET  /api/health           -> {ok, version, server_time}
  GET  /api/wearable/latest  -> {real, simulated, server_time}
  GET  /api/events           -> SSE stream of accepted readings + heartbeats
  POST /api/wearable         -> validated wearable payload (token required)

Auth:  Authorization: Bearer <token>  OR  X-KinetiQ-Token: <token>
Token comes from backend/config.json (gitignored) or the KINETIQ_TOKEN env var.
The token is NEVER logged.

Only the standard library is used (Python 3.11+).
"""
from __future__ import annotations

import csv
import hmac
import json
import os
import queue
import threading
import time
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VERSION = "1.2.0"
BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = (BASE_DIR.parent / "web").resolve()

SOURCES = ("huawei_band_6", "huawei_band_6_ble", "huawei_band_6_healthapp",
           "mock_simulator")
# Sources that carry GENUINE band data (the real slot). Everything not listed
# here (mock_simulator) is simulated and can never overwrite the real slot.
REAL_SOURCES = ("huawei_band_6", "huawei_band_6_ble", "huawei_band_6_healthapp")
TRANSPORTS = ("hms_health_kit", "ble_lpv2", "health_app_ui", None)
HR_MODES = ("realtime", "historical", "unavailable")
SPO2_MODES = ("realtime", "historical", "unavailable", "unsupported")
# Huawei stress (Task 003): score on a 0-100 scale where a higher number means
# more tension/probable fatigue; the band's Health UI groups these bands.
STRESS_MODES = ("realtime", "historical", "unavailable", "unsupported")
STRESS_MIN, STRESS_MAX = 0, 100
PHONE_STATUSES = ("ok", "permission_denied", "unsupported", "error")

HR_MIN, HR_MAX = 30, 220          # bpm, accepted sensor range
SPO2_MIN, SPO2_MAX = 70.0, 100.0  # percent, accepted sensor range

REAL_CSV = "kinetiq_wearable.csv"
SIM_CSV = "kinetiq_simulated.csv"
CSV_FIELDS = [
    "received_at", "source", "schema_version", "timestamp",
    "heart_rate_bpm", "spo2_percent", "stress_score",
    "heart_rate_mode", "spo2_mode", "stress_mode",
    "status", "device_model", "simulated", "transport",
]


# --------------------------------------------------------------------------- config

def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else BASE_DIR / "config.json"
    cfg: dict = {}
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("host", "0.0.0.0")
    cfg.setdefault("port", 8000)
    cfg.setdefault("data_dir", "data")
    cfg.setdefault("stale_after_seconds", 60)
    cfg.setdefault("offline_after_seconds", 120)
    cfg.setdefault("max_future_skew_seconds", 300)
    token = os.environ.get("KINETIQ_TOKEN") or cfg.get("auth_token") or ""
    cfg["auth_token"] = token
    data_dir = Path(cfg["data_dir"])
    if not data_dir.is_absolute():
        data_dir = BASE_DIR / data_dir
    cfg["data_dir"] = str(data_dir)
    return cfg


# --------------------------------------------------------------------------- validation

def parse_timestamp(value: object) -> datetime:
    """Parse an ISO-8601 timestamp. Requires explicit timezone (or trailing Z)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty ISO-8601 string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError("timestamp is not valid ISO-8601")
    if dt.tzinfo is None:
        raise ValueError("timestamp must carry a timezone (e.g. trailing Z)")
    return dt.astimezone(timezone.utc)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_payload(obj: object, cfg: dict) -> tuple[dict, list[str]]:
    """Validate an incoming wearable payload.

    Returns (clean_record, errors). Empty errors => accepted.
    Missing readings stay None; they are never replaced with zero.
    """
    errors: list[str] = []
    if not isinstance(obj, dict):
        return {}, ["body must be a JSON object"]

    schema_version = obj.get("schema_version")
    if schema_version != 1:
        errors.append("schema_version must be 1")

    source = obj.get("source")
    if source not in SOURCES:
        errors.append(f"source must be one of {list(SOURCES)}")

    try:
        ts = parse_timestamp(obj.get("timestamp"))
    except ValueError as exc:
        errors.append(str(exc))
        ts = None

    hr = obj.get("heart_rate_bpm")
    if hr is not None and (not _is_int(hr) or not (HR_MIN <= hr <= HR_MAX)):
        errors.append(f"heart_rate_bpm must be null or an integer {HR_MIN}..{HR_MAX}")

    spo2 = obj.get("spo2_percent")
    if spo2 is not None and (
        isinstance(spo2, bool)
        or not isinstance(spo2, (int, float))
        or not (SPO2_MIN <= float(spo2) <= SPO2_MAX)
    ):
        errors.append(f"spo2_percent must be null or a number {SPO2_MIN:g}..{SPO2_MAX:g}")

    hr_mode = obj.get("heart_rate_mode", "unavailable")
    if hr_mode not in HR_MODES:
        errors.append(f"heart_rate_mode must be one of {list(HR_MODES)}")

    spo2_mode = obj.get("spo2_mode", "unavailable")
    if spo2_mode not in SPO2_MODES:
        errors.append(f"spo2_mode must be one of {list(SPO2_MODES)}")

    # Task 003 additive field: stress_score on the documented 0-100 scale.
    stress = obj.get("stress_score")
    if stress is not None and (
        isinstance(stress, bool)
        or not isinstance(stress, int)
        or not (STRESS_MIN <= stress <= STRESS_MAX)
    ):
        errors.append(f"stress_score must be null or an integer {STRESS_MIN}..{STRESS_MAX}")
    stress_mode = obj.get("stress_mode", "unavailable")
    if stress_mode not in STRESS_MODES:
        errors.append(f"stress_mode must be one of {list(STRESS_MODES)}")

    status = obj.get("status", "ok")
    if status not in PHONE_STATUSES:
        errors.append(f"status must be one of {list(PHONE_STATUSES)}")

    device_model = obj.get("device_model", "")
    if device_model is None:
        device_model = ""
    if not isinstance(device_model, str) or len(device_model) > 64:
        errors.append("device_model must be a string of at most 64 chars")

    simulated = obj.get("simulated", False)
    if not isinstance(simulated, bool):
        errors.append("simulated must be a boolean")
        simulated = False

    # Optional, additive in schema v1: how the reading reached the phone.
    transport = obj.get("transport")
    if transport is not None and transport not in TRANSPORTS:
        errors.append(f"transport must be null or one of {[t for t in TRANSPORTS if t]}")

    if not errors and ts is not None:
        now = datetime.now(timezone.utc)
        if ts > now + timedelta(seconds=cfg["max_future_skew_seconds"]):
            errors.append("timestamp is too far in the future (check phone clock)")

    # Real vs simulated separation: a genuine Band 6 reading must never be
    # labelled simulated, and mock data must never pose as genuine.
    if not errors:
        if source == "huawei_band_6" and simulated is True:
            errors.append("source huawei_band_6 must not set simulated=true")
        if source == "mock_simulator" and simulated is not True:
            errors.append("source mock_simulator must set simulated=true")
        # BLE-probe path: values decoded from an unauthenticated protocol probe
        # cannot be called real. The diagnostic app never sends such payloads;
        # this guard stays as defence in depth.
        if source == "huawei_band_6_ble" and simulated is True:
            errors.append("source huawei_band_6_ble must not set simulated=true")
        # A BLE probe with no decoded values must never occupy the genuine
        # slot (it would clobber the last real reading with nulls).
        if (not errors and source == "huawei_band_6_ble"
                and hr is None and spo2 is None and stress is None):
            errors.append("huawei_band_6_ble payload must carry at least one "
                          "decoded value to update the genuine slot")
        # Health-UI relay path (Task 003 Phase 5): values read off the Huawei
        # Health app screen are genuine band data relayed by Health;
        # they must never be marked simulated, and must carry at least one
        # actual reading (same genuine-slot hygiene as above).
        if source == "huawei_band_6_healthapp" and simulated is True:
            errors.append("source huawei_band_6_healthapp must not set "
                          "simulated=true")
        if (not errors and source == "huawei_band_6_healthapp"
                and hr is None and spo2 is None and stress is None):
            errors.append("huawei_band_6_healthapp payload must carry at "
                          "least one value from the Health UI")

    if errors:
        return {}, errors

    assert ts is not None
    return {
        "schema_version": 1,
        "source": source,
        "timestamp": ts.isoformat(),
        "heart_rate_bpm": hr,
        "spo2_percent": None if spo2 is None else float(spo2),
        "stress_score": stress,
        "heart_rate_mode": hr_mode,
        "spo2_mode": spo2_mode,
        "stress_mode": stress_mode,
        "status": status,
        "device_model": device_model,
        "simulated": simulated,
        "transport": transport,
    }, []


# --------------------------------------------------------------------------- store

class WearableStore:
    """Holds the latest genuine and simulated readings separately.

    Simulated readings can never overwrite genuine ones: they live in
    different slots (and different CSV files).
    """

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._lock = threading.Lock()
        self._latest_real: dict | None = None
        self._latest_sim: dict | None = None
        self._last_seen_phone: str | None = None  # ISO time of last accepted POST
        self._subscribers: list[queue.Queue] = []
        data_dir = Path(cfg["data_dir"])
        data_dir.mkdir(parents=True, exist_ok=True)
        self._real_csv = data_dir / REAL_CSV
        self._sim_csv = data_dir / SIM_CSV

    # -- state --------------------------------------------------------------
    def _append_csv(self, path: Path, record: dict) -> None:
        exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if not exists:
                writer.writeheader()
            writer.writerow({
                "received_at": datetime.now(timezone.utc).isoformat(),
                "source": record["source"],
                "schema_version": record["schema_version"],
                "timestamp": record["timestamp"],
                "heart_rate_bpm": "" if record["heart_rate_bpm"] is None else record["heart_rate_bpm"],
                "spo2_percent": "" if record["spo2_percent"] is None else record["spo2_percent"],
                "stress_score": "" if record["stress_score"] is None else record["stress_score"],
                "heart_rate_mode": record["heart_rate_mode"],
                "spo2_mode": record["spo2_mode"],
                "stress_mode": record["stress_mode"],
                "status": record["status"],
                "device_model": record["device_model"],
                "simulated": record["simulated"],
                "transport": record["transport"],
            })

    def _freshness(self, record: dict | None) -> str:
        if record is None:
            return "offline"
        age = (datetime.now(timezone.utc) - parse_timestamp(record["timestamp"])).total_seconds()
        if age < 0:
            age = 0
        if age <= self._cfg["stale_after_seconds"]:
            return "live"
        if age <= self._cfg["offline_after_seconds"]:
            return "stale"
        return "offline"

    def ingest(self, record: dict) -> dict:
        with self._lock:
            now_iso = datetime.now(timezone.utc).isoformat()
            self._last_seen_phone = now_iso
            if record["source"] in REAL_SOURCES:
                self._latest_real = record
                self._append_csv(self._real_csv, record)
            else:
                self._latest_sim = record
                self._append_csv(self._sim_csv, record)
            event = {
                "type": "wearable",
                "server_time": now_iso,
                "record": record,
                "real_freshness": self._freshness(self._latest_real),
                "bridge_seen": now_iso,
            }
            for sub in list(self._subscribers):
                try:
                    sub.put_nowait(event)
                except queue.Full:
                    pass
            return event

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "server_time": datetime.now(timezone.utc).isoformat(),
                "real": self._latest_real,
                "real_freshness": self._freshness(self._latest_real),
                "simulated": self._latest_sim,
                "simulated_freshness": self._freshness(self._latest_sim),
                "bridge_last_seen": self._last_seen_phone,
            }

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=32)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)


# --------------------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    store: WearableStore
    config: dict
    server_version = "KinetiQReceiver/" + VERSION

    def log_message(self, fmt: str, *args) -> None:  # keep logs quiet, never secrets
        pass

    # -- helpers ------------------------------------------------------------
    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.config.get("auth_token") or ""
        if not token:
            return False
        presented = self.headers.get("Authorization", "")
        if presented.startswith("Bearer "):
            presented = presented[len("Bearer "):].strip()
        else:
            presented = (self.headers.get("X-KinetiQ-Token") or "").strip()
        return bool(presented) and hmac.compare_digest(presented, token)

    # -- routes ---------------------------------------------------------------
    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_dashboard()
        elif path == "/api/health":
            self._send_json(200, {"ok": True, "version": VERSION, "server_time":
                                  datetime.now(timezone.utc).isoformat()})
        elif path == "/api/wearable/latest":
            self._send_json(200, self.store.snapshot())
        elif path == "/api/events":
            self._serve_sse()
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/wearable":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        if not self._authorized():
            # Deliberately vague; never hint whether the token exists.
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 64 * 1024:
            self._send_json(400, {"ok": False, "error": "empty or oversized body"})
            return
        try:
            obj = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"ok": False, "error": "malformed JSON"})
            return
        record, errors = validate_payload(obj, self.config)
        if errors:
            self._send_json(422, {"ok": False, "error": "validation failed",
                                  "reasons": errors})
            return
        event = self.store.ingest(record)
        self._send_json(200, {"ok": True, "real_freshness": event["real_freshness"]})

    # -- dashboard + sse ------------------------------------------------------
    def _serve_dashboard(self) -> None:
        page = WEB_DIR / "index.html"
        if not page.exists():
            self._send_json(500, {"ok": False, "error": "dashboard not installed"})
            return
        body = page.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        sub = self.store.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            # Replay current state so a fresh client paints immediately.
            self.wfile.write(("data: " + json.dumps(
                {"type": "snapshot", **self.store.snapshot()}) + "\n\n").encode())
            self.wfile.flush()
            while True:
                try:
                    event = sub.get(timeout=15)
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (ConnectionError, BrokenPipeError, ValueError):
            pass
        finally:
            self.store.unsubscribe(sub)


def create_server(cfg: dict) -> ThreadingHTTPServer:
    if not cfg.get("auth_token"):
        raise SystemExit(
            "No auth token configured. Copy backend/config.example.json to "
            "backend/config.json with a strong token, or set KINETIQ_TOKEN. "
            "Refusing to expose an unauthenticated health-data endpoint."
        )
    Handler.store = WearableStore(cfg)
    Handler.config = cfg
    server = ThreadingHTTPServer((cfg["host"], cfg["port"]), Handler)
    server.daemon_threads = True
    return server


def main() -> None:
    cfg = load_config()
    server = create_server(cfg)
    print(f"KinetiQ receiver v{VERSION} on http://{cfg['host']}:{cfg['port']}")
    print("Dashboard: http://<this-laptop-LAN-IP>:%d  (trusted local Wi-Fi only)" % cfg["port"])
    print("POST wearable readings to /api/wearable with the shared token.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
