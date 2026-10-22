#!/usr/bin/env python3
"""SIMULATED data sender for pipeline testing ONLY.

Generates clearly-labelled mock readings (source=mock_simulator,
simulated=true) so the full phone->laptop->website path can be exercised
BEFORE Huawei permissions / hardware are available.

Mock data is stored separately from genuine readings and can never overwrite
them. The dashboard renders it under a striped SIMULATED badge.

Usage:
  set KINETIQ_TOKEN=<token>  (or pass --token)
  python mock_sender.py --host 192.168.X.X --port 8000 --count 5
  python mock_sender.py --host 127.0.0.1 --port 8000 --loop   (1 sample / 5 s)
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

SIMULATED_SOURCE = "mock_simulator"


def build_reading() -> dict:
    return {
        "schema_version": 1,
        "source": SIMULATED_SOURCE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "heart_rate_bpm": random.randint(62, 110),
        "spo2_percent": round(random.uniform(95.0, 99.0), 1),
        "heart_rate_mode": "realtime",
        "spo2_mode": "historical",
        "status": "ok",
        "device_model": "SIMULATED-FEED",
        "simulated": True,
    }


def post(url: str, token: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    ap = argparse.ArgumentParser(description="SIMULATED KinetiQ pipeline test sender")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--token", default=os.environ.get("KINETIQ_TOKEN", ""))
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()
    if not args.token:
        print("Set KINETIQ_TOKEN env var or pass --token.", file=sys.stderr)
        return 2
    url = f"http://{args.host}:{args.port}/api/wearable"
    print(f"[SIMULATED] posting mock readings to {url} (NOT real sensor data)")
    n = 0
    try:
        while True:
            payload = build_reading()
            code, body = post(url, args.token, payload)
            print(f"[SIMULATED] sample {n + 1}: HTTP {code} hr={payload['heart_rate_bpm']} "
                  f"spo2={payload['spo2_percent']} -> {body.strip()}")
            n += 1
            if not args.loop and n >= args.count:
                break
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
