#!/usr/bin/env python3
"""Genuine Huawei Band 6 -> upstream KINETIQ mirror bridge.

Polls the local KinetiQ receiver (``/api/wearable/latest``) and writes one
packet per new genuine reading to stdout, in the upstream repository's locked
telemetry schema. ``laptop/web/app.py --stdin`` consumes exactly this format.

Nothing is synthesised
----------------------
* A packet is emitted only while the receiver reports the reading as ``live``.
* Heart rate, SpO2 and steps must all be present; if any is missing, nothing is
  emitted and the reason is logged to stderr (the mirror keeps its last paint).
* Values are copied from the receiver's genuine slot (``source`` is a Huawei
  Band 6 relay, ``simulated`` must be false). The mock slot is never read.

Mapping (upstream schema <- our relay)
--------------------------------------
    timestamp          <- reading timestamp (Unix seconds)      genuine
    heart_rate         <- heart_rate_bpm                        genuine
    spo2               <- spo2_percent, rounded                 genuine
                          (Health's latest completed measurement)
    steps              <- steps                                 genuine
    activity           <- "unknown"                             not relayed
    motion_intensity   <- 0.0                                   not relayed
    exertion           <- upstream mock_streamer.derive_exertion(hr, "unknown")
                          i.e. the repository's own heuristic, not a new one
    ppg_quality        <- "good"                                proxy: Health
                          reported a completed SpO2 measurement

``activity``, ``motion_intensity`` and ``ppg_quality`` are legally required by
the upstream schema and have no null option. They are documented placeholders,
not measurements; the wrapper chip (mirror_band.py) states this next to the
live values.

EOF on stdout ends the upstream stdin reader; the mirror server keeps serving
the last painted packet until it is stopped.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

PREFIX = "[band-bridge]"
DEFAULT_RECEIVER = "http://127.0.0.1:8000"
DEFAULT_POLL_S = 2.0

#: Upstream schema limits (laptop/receiver/telemetry_schema.py).
UPSTREAM_HR_RANGE = (30, 220)
UPSTREAM_SPO2_RANGE = (70, 100)


def log(message: str) -> None:
    """Diagnostics go to stderr so stdout stays a clean packet stream."""
    print(f"{PREFIX} {message}", file=sys.stderr, flush=True)


def parse_iso(text: str) -> datetime:
    """Parse an ISO-8601 timestamp; tolerate a trailing Z."""
    value = str(text).strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_upstream(repo_dir: Path) -> Tuple[Any, Callable[[int, str], str]]:
    """Import the upstream schema + its own exertion heuristic.

    Fails loudly instead of guessing: the bridge refuses to emit packets that
    the mirror's validator has not seen.
    """
    receiver_dir = repo_dir / "laptop" / "receiver"
    if not (receiver_dir / "telemetry_schema.py").is_file():
        raise SystemExit(
            f"{PREFIX} upstream KINETIQ repo not found at {repo_dir}; "
            "clone https://github.com/siddoit/KinetiQ into external/KinetiQ "
            "or pass --kinetiq-repo"
        )
    if str(receiver_dir) not in sys.path:
        sys.path.insert(0, str(receiver_dir))
    import mock_streamer  # noqa: E402  (path set above)
    import telemetry_schema  # noqa: E402

    return telemetry_schema.TelemetryRecord, mock_streamer.derive_exertion


def fetch_snapshot(receiver: str, timeout: float = 3.0) -> Dict[str, Any]:
    """GET the receiver's latest genuine/simulated snapshot (read-only)."""
    url = receiver.rstrip("/") + "/api/wearable/latest"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("receiver returned a non-object snapshot")
    return data


def reading_key(snapshot: Dict[str, Any]) -> Tuple[Any, ...]:
    """Identity of one accepted reading, used to de-duplicate emissions."""
    real = snapshot.get("real") or {}
    return (
        real.get("timestamp"),
        real.get("heart_rate_bpm"),
        real.get("spo2_percent"),
        real.get("stress_score"),
        real.get("steps"),
    )


def map_snapshot(
    snapshot: Dict[str, Any], derive_exertion: Callable[[int, str], str]
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Translate one receiver snapshot into an upstream-schema packet.

    Returns ``(packet, "")`` on success or ``(None, reason)`` when no honest
    packet can be produced.
    """
    real = snapshot.get("real")
    if not isinstance(real, dict):
        return None, "receiver has no genuine reading yet"

    freshness = snapshot.get("real_freshness") or "offline"
    if freshness != "live":
        return None, f"genuine reading is {freshness} (nothing emitted)"

    if real.get("simulated") is not False:
        return None, "genuine slot is not marked simulated=false; refusing"

    heart_rate = real.get("heart_rate_bpm")
    spo2 = real.get("spo2_percent")
    steps = real.get("steps")
    missing = [
        name
        for name, value in (
            ("heart_rate_bpm", heart_rate),
            ("spo2_percent", spo2),
            ("steps", steps),
        )
        if value is None
    ]
    if missing:
        return None, (
            "reading lacks " + ", ".join(missing) +
            " (upstream schema requires it; nothing emitted)"
        )

    try:
        heart_rate_int = int(heart_rate)
    except (TypeError, ValueError):
        return None, f"heart rate {heart_rate!r} is not an integer"
    low, high = UPSTREAM_HR_RANGE
    if not (low <= heart_rate_int <= high):
        return None, f"heart rate {heart_rate_int} outside upstream range {low}..{high}"

    try:
        spo2_int = int(round(float(spo2)))
    except (TypeError, ValueError):
        return None, f"SpO2 {spo2!r} is not numeric"
    low, high = UPSTREAM_SPO2_RANGE
    if not (low <= spo2_int <= high):
        return None, f"SpO2 {spo2_int} outside upstream range {low}..{high}"

    try:
        steps_int = int(steps)
    except (TypeError, ValueError):
        return None, f"steps {steps!r} is not an integer"
    if steps_int < 0:
        return None, f"steps {steps_int} is negative"

    try:
        timestamp = int(parse_iso(real.get("timestamp", "")).timestamp())
    except (TypeError, ValueError) as exc:
        return None, f"unusable reading timestamp ({type(exc).__name__}: {exc})"
    if timestamp <= 0:
        return None, "reading timestamp must be positive"

    packet = {
        "timestamp": timestamp,
        "heart_rate": heart_rate_int,
        "spo2": spo2_int,
        "activity": "unknown",          # not relayed by the Band path
        "motion_intensity": 0.0,        # not relayed by the Band path
        "exertion": derive_exertion(heart_rate_int, "unknown"),
        "steps": steps_int,
        "ppg_quality": "good",          # proxy: Health completed the SpO2 value
    }
    return packet, ""


def run(receiver: str, repo_dir: Path, poll_s: float, once: bool = False) -> int:
    """Poll the receiver forever (or once) and stream validated packets."""
    TelemetryRecord, derive_exertion = load_upstream(repo_dir)
    last_key: Optional[Tuple[Any, ...]] = None
    last_note = ""
    log(f"receiver={receiver} upstream={repo_dir}")

    while True:
        note = ""
        try:
            snapshot = fetch_snapshot(receiver)
        except Exception as exc:  # receiver restarting/offline
            snapshot = None
            note = f"receiver unreachable ({type(exc).__name__}: {exc})"

        if snapshot is not None:
            packet, note = map_snapshot(snapshot, derive_exertion)
            if packet is not None:
                key = reading_key(snapshot)
                if key != last_key:
                    try:
                        record = TelemetryRecord.parse(packet)
                    except Exception as exc:  # upstream validator is the gate
                        note = f"upstream schema rejected packet: {exc}"
                    else:
                        # One compact line, exactly the upstream wire format.
                        print(record.to_json(), flush=True)
                        stress = (snapshot.get("real") or {}).get("stress_score")
                        log(
                            "emitted hr={0} spo2={1} stress={2} steps={3} "
                            "exertion={4} (activity/motion not relayed)".format(
                                record.heart_rate,
                                record.spo2,
                                "-" if stress is None else stress,
                                record.steps,
                                record.exertion,
                            )
                        )
                        last_key = key
                        note = ""

        if note and note != last_note:
            log(note)
        last_note = note

        if once:
            return 0
        try:
            time.sleep(poll_s)
        except KeyboardInterrupt:
            return 0


def build_parser() -> argparse.ArgumentParser:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        prog="band-bridge",
        description=(
            "Stream genuine Huawei Band 6 readings from the KinetiQ receiver "
            "into the upstream KINETIQ mirror's locked telemetry schema."
        ),
    )
    parser.add_argument("--receiver", default=DEFAULT_RECEIVER,
                        help=f"KinetiQ receiver base URL (default {DEFAULT_RECEIVER})")
    parser.add_argument("--kinetiq-repo", type=Path,
                        default=here.parent / "external" / "KinetiQ",
                        help="path to the upstream KINETIQ clone")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_S,
                        help=f"seconds between polls (default {DEFAULT_POLL_S:g})")
    parser.add_argument("--once", action="store_true",
                        help="poll once, emit at most one packet, exit (smoke test)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.poll <= 0:
        log("error: --poll must be > 0")
        return 2
    try:
        return run(args.receiver, args.kinetiq_repo.resolve(), args.poll, args.once)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
