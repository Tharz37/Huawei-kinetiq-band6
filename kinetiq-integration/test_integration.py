#!/usr/bin/env python3
"""Tests for the Band 6 -> upstream mirror integration (stdlib unittest).

Run:  python -m unittest discover -s kinetiq-integration -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from band_bridge import (  # noqa: E402
    load_upstream, map_snapshot, reading_key,
)
from mirror_band import build_band_view, inject_chip  # noqa: E402

REPO = HERE.parent / "external" / "KinetiQ"
READING_TS = "2026-10-22T13:14:35.912000+00:00"


def snapshot(freshness: str = "live", **real_over):
    real = {
        "schema_version": 1,
        "source": "huawei_band_6_healthapp",
        "timestamp": READING_TS,
        "heart_rate_bpm": 74,
        "spo2_percent": 100.0,
        "stress_score": 57,
        "heart_rate_mode": "realtime",
        "spo2_mode": "historical",
        "stress_mode": "realtime",
        "status": "ok",
        "device_model": "Huawei Band 6 (via Health UI)",
        "simulated": False,
        "transport": "health_app_ui",
        "steps": 4181,
    }
    real.update(real_over)
    return {
        "server_time": "2026-10-22T13:14:37.000000+00:00",
        "real": real,
        "real_freshness": freshness,
        "simulated": None,
        "simulated_freshness": "offline",
        "bridge_last_seen": "2026-10-22T13:14:37.000000+00:00",
    }


class MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (REPO / "laptop" / "receiver" / "telemetry_schema.py").is_file():
            raise unittest.SkipTest(f"upstream clone missing at {REPO}")
        cls.TelemetryRecord, derive_exertion = load_upstream(REPO)
        # staticmethod keeps it a plain function when read through self.
        cls.derive_exertion = staticmethod(derive_exertion)

    def test_live_reading_maps_onto_upstream_schema(self):
        packet, reason = map_snapshot(snapshot(), self.derive_exertion)
        self.assertEqual(reason, "")
        assert packet is not None
        record = self.TelemetryRecord.parse(packet)  # the upstream gate
        self.assertEqual(record.heart_rate, 74)
        self.assertEqual(record.spo2, 100)
        self.assertEqual(record.steps, 4181)
        self.assertEqual(record.activity, "unknown")
        self.assertEqual(record.motion_intensity, 0.0)
        self.assertEqual(record.ppg_quality, "good")
        self.assertEqual(record.exertion, self.derive_exertion(74, "unknown"))
        expected_ts = int(datetime.fromisoformat(READING_TS).timestamp())
        self.assertEqual(record.timestamp, expected_ts)

    def test_exertion_uses_upstream_heuristic(self):
        self.assertEqual(self.derive_exertion(74, "unknown"), "light")
        self.assertEqual(self.derive_exertion(127, "unknown"), "moderate")
        self.assertEqual(self.derive_exertion(150, "unknown"), "high")

    def test_only_live_readings_are_forwarded(self):
        for freshness in ("stale", "offline", "unknown"):
            packet, reason = map_snapshot(snapshot(freshness), self.derive_exertion)
            self.assertIsNone(packet)
            self.assertIn(freshness, reason)

    def test_missing_genuine_reading_is_not_forwarded(self):
        packet, reason = map_snapshot({"real": None, "real_freshness": "offline"},
                                      self.derive_exertion)
        self.assertIsNone(packet)
        self.assertIn("no genuine reading", reason)

    def test_missing_steps_blocks_the_packet(self):
        packet, reason = map_snapshot(snapshot(steps=None), self.derive_exertion)
        self.assertIsNone(packet)
        self.assertIn("steps", reason)

    def test_missing_spo2_blocks_the_packet(self):
        packet, reason = map_snapshot(snapshot(spo2_percent=None), self.derive_exertion)
        self.assertIsNone(packet)
        self.assertIn("spo2_percent", reason)

    def test_missing_heart_rate_blocks_the_packet(self):
        packet, reason = map_snapshot(snapshot(heart_rate_bpm=None), self.derive_exertion)
        self.assertIsNone(packet)
        self.assertIn("heart_rate_bpm", reason)

    def test_simulated_slot_is_refused(self):
        packet, reason = map_snapshot(snapshot(simulated=True), self.derive_exertion)
        self.assertIsNone(packet)
        self.assertIn("simulated", reason)

    def test_out_of_range_values_are_refused(self):
        for over in ({"heart_rate_bpm": 250}, {"spo2_percent": 55},
                     {"heart_rate_bpm": 0}, {"steps": -1}):
            packet, reason = map_snapshot(snapshot(**over), self.derive_exertion)
            self.assertIsNone(packet, f"{over} should not map")
            self.assertTrue(reason)

    def test_reading_key_tracks_all_genuine_values(self):
        base = reading_key(snapshot())
        self.assertEqual(base, reading_key(snapshot()))
        self.assertNotEqual(base, reading_key(snapshot(steps=4182)))
        self.assertNotEqual(base, reading_key(snapshot(heart_rate_bpm=75)))
        self.assertNotEqual(base, reading_key(snapshot(timestamp="2026-10-22T13:15:00+00:00")))


class OverlayTests(unittest.TestCase):
    def test_chip_is_injected_before_body_end(self):
        page = "<html><body><h1>mirror</h1></body></html>"
        patched = inject_chip(page)
        self.assertIn('id="bandbar"', patched)
        self.assertIn('fetch(\'/band/api\'', patched)
        self.assertLess(patched.index('id="bandbar"'), patched.index("</body>"))

    def test_chip_is_skipped_when_page_has_no_body(self):
        self.assertEqual(inject_chip("no body here"), "no body here")

    def test_band_view_reports_freshness_and_age(self):
        now = datetime.fromisoformat(READING_TS) + timedelta(seconds=12)
        view = build_band_view(snapshot(), now=now)
        self.assertTrue(view["ok"])
        self.assertEqual(view["freshness"], "live")
        self.assertEqual(view["age_s"], 12)
        self.assertEqual(view["real"]["heart_rate_bpm"], 74)
        self.assertEqual(view["real"]["stress_score"], 57)
        self.assertEqual(view["real"]["steps"], 4181)
        self.assertIs(view["real"]["simulated"], False)

    def test_band_view_without_receiver_data(self):
        view = build_band_view(None, "ConnectionRefusedError: down")
        self.assertFalse(view["ok"])
        self.assertIsNone(view["real"])
        self.assertEqual(view["freshness"], "offline")
        self.assertIn("down", view["error"])

    def test_band_view_with_no_reading_yet(self):
        view = build_band_view({"real": None, "real_freshness": "offline"})
        self.assertTrue(view["ok"])  # receiver answered
        self.assertIsNone(view["real"])


if __name__ == "__main__":
    unittest.main()
