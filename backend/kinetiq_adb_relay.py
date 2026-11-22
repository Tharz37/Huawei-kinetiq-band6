#!/usr/bin/env python3
"""KinetiQ PC-side ADB relay.

Reads Huawei Health's active notification text and, when Health is in the
foreground, its uiautomator view hierarchy. Values are posted to the local
KinetiQ receiver only when at least one vital (HR, SpO2 or stress) was actually
seen. Missing values stay JSON null; nothing is zero-filled or invented.

This is a fallback / parallel path for the Android app:
  * the app's accessibility watchdog reads the visible Health screen;
  * the app's notification listener reads Health's status-bar update;
  * this relay can read the same sources over USB when the app is not running.

Duplicate suppression: before posting, the relay reads
``/api/wearable/latest``. If the receiver's genuine reading is no older than
``--dedupe-seconds`` and has the same values, the relay stays quiet. That keeps
the app, the notification listener and this relay from tripling every CSV row.

Usage:
    python backend/kinetiq_adb_relay.py
    python backend/kinetiq_adb_relay.py --once --verbose
    python backend/kinetiq_adb_relay.py --endpoint http://LAPTOP:8000/api/wearable

ADB may be selected with ``--adb PATH`` or ``KINETIQ_ADB``. The receiver token
comes from ``--token`` or ``KINETIQ_TOKEN`` (default poc-demo).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENDPOINT = "http://127.0.0.1:8000/api/wearable"
DEFAULT_TOKEN = "poc-demo"
DEFAULT_ADB = r"C:\Users\Tharz\AppData\Local\Android\Sdk\platform-tools\adb.exe"

HEALTH_PACKAGE = "com.huawei.health"
SOURCE = "huawei_band_6_healthapp"
TRANSPORT = "health_app_ui"
DEVICE_MODEL = "Huawei Band 6 (ADB Relay)"

HR_MIN, HR_MAX = 20, 260
SPO2_MIN, SPO2_MAX = 70, 100
STRESS_MIN, STRESS_MAX = 0, 100
STEPS_MIN, STEPS_MAX = 0, 200_000
LABEL_WINDOW = 3

HR_RE = re.compile(r"\b(\d{2,3})\s*bpm\b", re.IGNORECASE)
SPO2_VALUE_RE = re.compile(r"\b(\d{2,3})(?!\d)\s*%?")
PERCENT_RE = re.compile(r"\b(\d{2,3})(?!\d)\s*%")
# Do not turn the "4" in "4,181 steps" into a stress score.
STRESS_VALUE_RE = re.compile(r"(?<!\d)(\d{1,3})(?!\d|,\d{3})")
STEPS_INLINE_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})*|\d+)\s*steps?\b", re.IGNORECASE)
STEPS_NUMBER_RE = re.compile(r"^\d{1,3}(?:,\d{3})*$")


# --------------------------------------------------------------------------- parsing


def _clean(texts: list[str]) -> list[str]:
    return [text.strip() for text in texts if text and text.strip()]


def _find_hr(texts: list[str]) -> int | None:
    for text in texts:
        for match in HR_RE.finditer(text):
            value = int(match.group(1))
            if HR_MIN <= value <= HR_MAX:
                return value
    # Health sometimes splits a value and its unit across sibling nodes.
    joined = " ".join(texts)
    for match in HR_RE.finditer(joined):
        value = int(match.group(1))
        if HR_MIN <= value <= HR_MAX:
            return value
    return None


def _find_spo2(texts: list[str]) -> float | None:
    label_index = next(
        (
            index
            for index, text in enumerate(texts)
            if "oxygen" in text.lower() or "spo" in text.lower()
        ),
        -1,
    )
    if label_index >= 0:
        label_text = texts[label_index]
        lower = label_text.lower()
        label = "oxygen" if "oxygen" in lower else "spo"
        label_end = lower.find(label)
        label_end = label_end + len(label) if label_end >= 0 else 0
        candidates = [label_text[label_end:]]
        last = min(len(texts) - 1, label_index + LABEL_WINDOW)
        candidates.extend(texts[label_index + 1 : last + 1])
        for candidate in candidates:
            for match in SPO2_VALUE_RE.finditer(candidate):
                value = int(match.group(1))
                if SPO2_MIN <= value <= SPO2_MAX:
                    return float(value)
    for text in texts:
        for match in PERCENT_RE.finditer(text):
            value = int(match.group(1))
            if SPO2_MIN <= value <= SPO2_MAX:
                return float(value)
    return None


def _find_stress(texts: list[str]) -> int | None:
    label_index = next(
        (index for index, text in enumerate(texts) if "stress" in text.lower()),
        -1,
    )
    if label_index < 0:
        return None
    label_text = texts[label_index]
    label_end = label_text.lower().find("stress")
    label_end = label_end + len("stress") if label_end >= 0 else 0
    candidates = [label_text[label_end:]]
    last = min(len(texts) - 1, label_index + LABEL_WINDOW)
    candidates.extend(texts[label_index + 1 : last + 1])
    for candidate in candidates:
        for match in STRESS_VALUE_RE.finditer(candidate):
            value = int(match.group(1))
            if STRESS_MIN <= value <= STRESS_MAX:
                return value
    return None


def _step_value(text: str) -> int | None:
    candidate = text.strip()
    if not STEPS_NUMBER_RE.fullmatch(candidate):
        return None
    value = int(candidate.replace(",", ""))
    return value if STEPS_MIN <= value <= STEPS_MAX else None


def _find_steps(texts: list[str]) -> int | None:
    for index, text in enumerate(texts):
        if text.strip().lower() == "steps" and index + 1 < len(texts):
            value = _step_value(texts[index + 1])
            if value is not None:
                return value
    for index in range(len(texts) - 1):
        if texts[index + 1].strip().lower() == "steps":
            value = _step_value(texts[index])
            if value is not None:
                return value
    for text in texts:
        for match in STEPS_INLINE_RE.finditer(text):
            value = int(match.group(1).replace(",", ""))
            if STEPS_MIN <= value <= STEPS_MAX:
                return value
    return None


def parse_health_texts(texts: list[str]) -> dict[str, int | float | None]:
    """Parse the same labels the Android HealthParser understands."""
    clean = _clean(texts)
    return {
        "heart_rate_bpm": _find_hr(clean),
        "spo2_percent": _find_spo2(clean),
        "stress_score": _find_stress(clean),
        "steps": _find_steps(clean),
    }


def has_vitals(reading: dict[str, int | float | None]) -> bool:
    return any(
        reading.get(key) is not None
        for key in ("heart_rate_bpm", "spo2_percent", "stress_score")
    )


def merge_readings(
    primary: dict[str, int | float | None],
    fallback: dict[str, int | float | None],
) -> dict[str, int | float | None]:
    """Prefer UI values, filling only missing metrics from the notification."""
    merged = dict(primary)
    for key in ("heart_rate_bpm", "spo2_percent", "stress_score", "steps"):
        if merged.get(key) is None:
            merged[key] = fallback.get(key)
    return merged


def reading_summary(reading: dict[str, int | float | None]) -> str:
    return (
        f"HR={reading.get('heart_rate_bpm')} "
        f"SpO2={reading.get('spo2_percent')} "
        f"Stress={reading.get('stress_score')} "
        f"Steps={reading.get('steps')}"
    )


# --------------------------------------------------------------------------- adb


def resolve_adb(explicit: str | None) -> str:
    candidates = [
        explicit,
        os.environ.get("KINETIQ_ADB"),
        shutil.which("adb"),
        DEFAULT_ADB,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return "adb"


def run_adb(adb: str, args: list[str], timeout: float = 8.0, verbose: bool = False) -> str:
    try:
        result = subprocess.run(
            [adb, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            if verbose:
                stderr = (result.stderr or "").strip()
                print(f"[adb] {' '.join(args)} -> rc={result.returncode} {stderr}", file=sys.stderr)
            return result.stdout or ""
        return result.stdout or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        if verbose:
            print(f"[adb] {' '.join(args)} failed: {exc}", file=sys.stderr)
        return ""


def check_device(adb: str, verbose: bool = False) -> bool:
    state = run_adb(adb, ["get-state"], timeout=5, verbose=verbose).strip()
    if state != "device":
        print(
            f"[relay] ADB device state is {state or 'unavailable'!r}; "
            "connect USB and accept the debugging prompt.",
            file=sys.stderr,
        )
        return False
    return True


def health_is_foreground(adb: str, verbose: bool = False) -> bool:
    window_dump = run_adb(adb, ["shell", "dumpsys", "window"], timeout=10, verbose=verbose)
    for line in window_dump.splitlines():
        if "mCurrentFocus=" in line:
            return HEALTH_PACKAGE in line
    return False


def ui_texts(adb: str, verbose: bool = False) -> list[str]:
    if not health_is_foreground(adb, verbose=verbose):
        return []

    run_adb(
        adb,
        ["shell", "uiautomator", "dump", "/sdcard/kinetiq_view.xml"],
        timeout=10,
        verbose=verbose,
    )
    xml = run_adb(
        adb,
        ["exec-out", "cat", "/sdcard/kinetiq_view.xml"],
        timeout=6,
        verbose=verbose,
    )
    if not xml.strip():
        return []
    start = xml.find("<?xml")
    if start > 0:
        xml = xml[start:]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        if verbose:
            print(f"[relay] uiautomator XML parse failed: {exc}", file=sys.stderr)
        return []

    texts: list[str] = []
    for node in root.iter():
        for attribute in ("text", "content-desc"):
            value = (node.attrib.get(attribute) or "").strip()
            if value:
                texts.append(value)
    return texts


def _notification_text_lines(block: list[str]) -> list[str]:
    texts: list[str] = []
    field_re = re.compile(
        r"android\.(?:title|text|bigText|subText|summaryText|infoText|tickerText)="
    )
    for line in block:
        line = line.strip()
        if field_re.search(line):
            match = re.search(r"\((.*)\)\s*$", line)
            if match:
                value = match.group(1).strip()
                if value:
                    texts.append(value)
        elif "android.textLines=" in line:
            for match in re.finditer(r"String \((.*?)\)", line):
                value = match.group(1).strip()
                if value:
                    texts.append(value)
    return texts


def notification_texts(adb: str, verbose: bool = False) -> list[str]:
    dump = run_adb(
        adb,
        ["shell", "dumpsys", "notification", "--noredact"],
        timeout=12,
        verbose=verbose,
    )
    if not dump:
        return []

    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in dump.splitlines():
        if "NotificationRecord(" in line:
            if current:
                blocks.append(current)
            current = [line] if f"pkg={HEALTH_PACKAGE}" in line else None
        elif current is not None:
            current.append(line)
    if current:
        blocks.append(current)

    texts: list[str] = []
    for block in blocks:
        texts.extend(_notification_text_lines(block))
    return _clean(texts)


# --------------------------------------------------------------------------- receiver


def latest_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    suffix = "/api/wearable"
    if base.endswith(suffix):
        base = base[: -len(suffix)]
    return base + "/api/wearable/latest"


def fetch_latest(endpoint: str, token: str, verbose: bool = False) -> dict | None:
    request = urllib.request.Request(
        latest_url(endpoint),
        headers={"X-KinetiQ-Token": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        if verbose:
            print(f"[relay] latest fetch failed: {exc}", file=sys.stderr)
        return None


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _same_value(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(float(left) - float(right)) < 0.01
    except (TypeError, ValueError):
        return str(left) == str(right)


def latest_has_same_reading(
    endpoint: str,
    token: str,
    reading: dict[str, int | float | None],
    dedupe_seconds: float,
    verbose: bool = False,
) -> bool:
    latest = fetch_latest(endpoint, token, verbose=verbose)
    if not latest:
        return False
    real = latest.get("real")
    if not isinstance(real, dict):
        return False
    timestamp = _parse_timestamp(real.get("timestamp"))
    if timestamp is None:
        return False
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    if age < 0:
        age = 0
    if age > dedupe_seconds:
        return False
    return all(
        _same_value(real.get(key), reading.get(key))
        for key in ("heart_rate_bpm", "spo2_percent", "stress_score", "steps")
    )


def payload_for(reading: dict[str, int | float | None]) -> dict:
    hr = reading.get("heart_rate_bpm")
    spo2 = reading.get("spo2_percent")
    stress = reading.get("stress_score")
    steps = reading.get("steps")
    return {
        "schema_version": 1,
        "source": SOURCE,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "heart_rate_bpm": hr,
        "spo2_percent": None if spo2 is None else float(spo2),
        "stress_score": stress,
        "steps": steps,
        "heart_rate_mode": "realtime" if hr is not None else "unavailable",
        "spo2_mode": "historical" if spo2 is not None else "unavailable",
        "stress_mode": "realtime" if stress is not None else "unavailable",
        "status": "ok",
        "device_model": DEVICE_MODEL,
        "simulated": False,
        "transport": TRANSPORT,
    }


def post_reading(endpoint: str, token: str, reading: dict[str, int | float | None]) -> bool:
    data = json.dumps(payload_for(reading)).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-KinetiQ-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            status = getattr(response, "status", response.getcode())
            print(f"[{datetime.now().strftime('%H:%M:%S')}] POST HTTP {status} -> {reading_summary(reading)}")
            return 200 <= status < 300
    except (OSError, urllib.error.URLError) as exc:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] POST failed: {exc}", file=sys.stderr)
        return False


# --------------------------------------------------------------------------- loop


def run_cycle(args: argparse.Namespace) -> bool:
    adb = resolve_adb(args.adb)
    if not check_device(adb, verbose=args.verbose):
        return False

    ui = [] if args.no_ui else parse_health_texts(ui_texts(adb, verbose=args.verbose))
    notification = (
        {}
        if args.no_notifications
        else parse_health_texts(notification_texts(adb, verbose=args.verbose))
    )
    reading = merge_readings(ui, notification)
    if not has_vitals(reading):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] no genuine vital seen -> {reading_summary(reading)}")
        return False

    if not args.force and latest_has_same_reading(
        args.endpoint,
        args.token,
        reading,
        args.dedupe_seconds,
        verbose=args.verbose,
    ):
        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] receiver already has this reading "
            f"(within {args.dedupe_seconds:g}s) -> {reading_summary(reading)}"
        )
        return False

    return post_reading(args.endpoint, args.token, reading)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read Huawei Health via ADB and POST to KinetiQ.")
    parser.add_argument("--endpoint", default=os.environ.get("KINETIQ_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--token", default=os.environ.get("KINETIQ_TOKEN", DEFAULT_TOKEN))
    parser.add_argument("--adb", default=None, help="Path to adb.exe (or set KINETIQ_ADB).")
    parser.add_argument("--interval", type=float, default=4.0, help="Seconds between cycles.")
    parser.add_argument(
        "--dedupe-seconds",
        type=float,
        default=30.0,
        help="Skip if the receiver's latest genuine reading is this recent and identical.",
    )
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--force", action="store_true", help="POST even if the receiver has the same reading.")
    parser.add_argument("--no-ui", action="store_true", help="Do not run uiautomator; notifications only.")
    parser.add_argument("--no-notifications", action="store_true", help="Do not read notifications; UI only.")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.interval = max(1.0, args.interval)
    args.dedupe_seconds = max(0.0, args.dedupe_seconds)

    if args.once:
        run_cycle(args)
        return 0

    print("KinetiQ ADB relay running. Ctrl+C to stop.")
    print(f"  endpoint: {args.endpoint}")
    print(f"  interval: {args.interval:g}s; dedupe window: {args.dedupe_seconds:g}s")
    try:
        while True:
            run_cycle(args)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
