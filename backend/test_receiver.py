#!/usr/bin/env python3
"""Tests for the KinetiQ laptop receiver. Stdlib only: python -m unittest discover -s backend -v"""
import json
import threading
import time
import unittest
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from kinetiq_receiver import (
    WearableStore, create_server, load_config, validate_payload,
)

TOKEN = "test-token-12345"


def make_cfg(**over):
    cfg = {
        "host": "127.0.0.1", "port": 0, "auth_token": TOKEN,
        "data_dir": ":memory-test:",  # replaced per-test with tmp dir
        "stale_after_seconds": 60, "offline_after_seconds": 120,
        "max_future_skew_seconds": 300,
    }
    cfg.update(over)
    return cfg


def good_payload(**over):
    p = {
        "schema_version": 1,
        "source": "huawei_band_6",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "heart_rate_bpm": 78,
        "spo2_percent": 97.0,
        "heart_rate_mode": "realtime",
        "spo2_mode": "historical",
        "status": "ok",
        "device_model": "Huawei Band 6",
        "simulated": False,
    }
    p.update(over)
    return p


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()

    def test_valid_real_payload(self):
        rec, errs = validate_payload(good_payload(), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["heart_rate_bpm"], 78)
        self.assertEqual(rec["spo2_percent"], 97.0)

    def test_missing_readings_stay_null(self):
        rec, errs = validate_payload(
            good_payload(heart_rate_bpm=None, spo2_percent=None,
                         heart_rate_mode="unavailable", spo2_mode="unavailable"),
            self.cfg)
        self.assertEqual(errs, [])
        self.assertIsNone(rec["heart_rate_bpm"])
        self.assertIsNone(rec["spo2_percent"])

    def test_zero_heart_rate_rejected(self):
        _, errs = validate_payload(good_payload(heart_rate_bpm=0), self.cfg)
        self.assertTrue(any("heart_rate_bpm" in e for e in errs))

    def test_out_of_range_rejected(self):
        _, errs = validate_payload(good_payload(heart_rate_bpm=250), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(spo2_percent=55), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(spo2_percent=101), self.cfg)
        self.assertTrue(errs)

    def test_bad_types_rejected(self):
        _, errs = validate_payload(good_payload(heart_rate_bpm="78"), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(heart_rate_bpm=True), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(schema_version=2), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(source="fitbit"), self.cfg)
        self.assertTrue(errs)

    def test_timestamp_handling(self):
        # trailing Z accepted
        rec, errs = validate_payload(
            good_payload(timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            self.cfg)
        self.assertEqual(errs, [])
        # naive timestamp rejected
        _, errs = validate_payload(
            good_payload(timestamp="2026-10-15T10:00:00"), self.cfg)
        self.assertTrue(any("timezone" in e for e in errs))
        # garbage rejected
        _, errs = validate_payload(good_payload(timestamp="yesterday"), self.cfg)
        self.assertTrue(errs)
        # far-future rejected (phone clock wrong)
        _, errs = validate_payload(
            good_payload(timestamp=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()),
            self.cfg)
        self.assertTrue(any("future" in e for e in errs))
        # non-object body rejected
        _, errs = validate_payload([1, 2], self.cfg)
        self.assertTrue(errs)

    def test_simulated_flag_consistency(self):
        # genuine source must not claim simulated
        _, errs = validate_payload(good_payload(simulated=True), self.cfg)
        self.assertTrue(errs)
        # mock source must declare simulated
        _, errs = validate_payload(
            good_payload(source="mock_simulator", simulated=False), self.cfg)
        self.assertTrue(errs)
        rec, errs = validate_payload(
            good_payload(source="mock_simulator", simulated=True), self.cfg)
        self.assertEqual(errs, [])
        self.assertTrue(rec["simulated"])

    def test_phone_status_enum(self):
        rec, errs = validate_payload(good_payload(status="permission_denied"), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["status"], "permission_denied")
        _, errs = validate_payload(good_payload(status="nope"), self.cfg)
        self.assertTrue(errs)

    def test_optional_modes_default(self):
        p = good_payload()
        del p["heart_rate_mode"]
        del p["spo2_mode"]
        rec, errs = validate_payload(p, self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["heart_rate_mode"], "unavailable")

    def test_stress_score_validation(self):
        # Additive Task-003 field: stress on a 0-100 scale.
        rec, errs = validate_payload(
            good_payload(stress_score=62, stress_mode="historical"), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["stress_score"], 62)
        self.assertEqual(rec["stress_mode"], "historical")
        # None/absent stays null like HR and SpO2.
        rec, errs = validate_payload(good_payload(), self.cfg)
        self.assertEqual(errs, [])
        self.assertIsNone(rec["stress_score"])
        _, errs = validate_payload(good_payload(stress_score=101), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(stress_score=-1), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(stress_score=62.5), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(stress_score=True), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(good_payload(stress_mode="liquid"), self.cfg)
        self.assertTrue(errs)

    def test_ble_source_and_transport(self):
        # BLE-probe source (Task 002) accepted, transport validated, additive
        # and backward compatible (hms payload without transport still fine).
        rec, errs = validate_payload(
            good_payload(source="huawei_band_6_ble", transport="ble_lpv2"), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["transport"], "ble_lpv2")
        rec, errs = validate_payload(good_payload(), self.cfg)
        self.assertEqual(errs, [])
        self.assertIsNone(rec["transport"])
        _, errs = validate_payload(good_payload(transport="warp_drive"), self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(
            good_payload(source="huawei_band_6_ble", simulated=True), self.cfg)
        self.assertTrue(errs)
        # Empty BLE probe (no values) must not clobber a genuine reading.
        _, errs = validate_payload(
            good_payload(source="huawei_band_6_ble", heart_rate_bpm=None,
                         spo2_percent=None, stress_score=None), self.cfg)
        self.assertTrue(any("decoded" in e for e in errs))
        # A BLE payload carrying ONE decoded metric satisfies the guard.
        rec, errs = validate_payload(
            good_payload(source="huawei_band_6_ble", heart_rate_bpm=None,
                         spo2_percent=None, stress_score=77), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["stress_score"], 77)

    def test_healthapp_source_and_transport(self):
        # Health-UI relay source (Phase 5 PoC): additive, never simulated,
        # and must carry at least one value (genuine-slot hygiene).
        rec, errs = validate_payload(
            good_payload(source="huawei_band_6_healthapp",
                         transport="health_app_ui"), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["transport"], "health_app_ui")
        _, errs = validate_payload(
            good_payload(source="huawei_band_6_healthapp", simulated=True),
            self.cfg)
        self.assertTrue(errs)
        _, errs = validate_payload(
            good_payload(source="huawei_band_6_healthapp", heart_rate_bpm=None,
                         spo2_percent=None, stress_score=None), self.cfg)
        self.assertTrue(any("value" in e for e in errs))
        # One metric is enough to satisfy the guard.
        rec, errs = validate_payload(
            good_payload(source="huawei_band_6_healthapp",
                         heart_rate_bpm=None, spo2_percent=None,
                         stress_score=35), self.cfg)
        self.assertEqual(errs, [])
        self.assertEqual(rec["stress_score"], 35)


class ServerTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(data_dir=self.tmp.name)
        self.server = create_server(self.cfg)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.1)
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _post(self, body, token=TOKEN, raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + "/api/wearable", data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"} if token else
                    {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode())
            except ValueError:
                return exc.code, {}

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_health(self):
        code, body = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])

    def test_auth_required(self):
        code, _ = self._post(good_payload(), token=None)
        self.assertEqual(code, 401)
        code, _ = self._post(good_payload(), token="wrong-token")
        self.assertEqual(code, 401)

    def test_malformed_and_invalid_rejected(self):
        code, body = self._post({}, raw=b"{not json")
        self.assertEqual(code, 400)
        code, body = self._post(good_payload(heart_rate_bpm=0))
        self.assertEqual(code, 422)
        self.assertIn("reasons", body)

    def test_valid_post_updates_latest(self):
        code, _ = self._post(good_payload(heart_rate_bpm=78))
        self.assertEqual(code, 200)
        code, snap = self._get("/api/wearable/latest")
        self.assertEqual(code, 200)
        self.assertEqual(snap["real"]["heart_rate_bpm"], 78)
        self.assertEqual(snap["real_freshness"], "live")

    def test_simulated_never_overwrites_real(self):
        self._post(good_payload(heart_rate_bpm=78))
        sim = good_payload(source="mock_simulator", simulated=True, heart_rate_bpm=120)
        code, _ = self._post(sim)
        self.assertEqual(code, 200)
        _, snap = self._get("/api/wearable/latest")
        self.assertEqual(snap["real"]["heart_rate_bpm"], 78)   # genuine kept
        self.assertEqual(snap["simulated"]["heart_rate_bpm"], 120)  # sim separate
        self.assertTrue(snap["simulated"]["simulated"])

    def test_missing_values_preserved_end_to_end(self):
        code, _ = self._post(good_payload(heart_rate_bpm=None, spo2_percent=None,
                                          heart_rate_mode="unavailable",
                                          spo2_mode="unsupported"))
        self.assertEqual(code, 200)
        _, snap = self._get("/api/wearable/latest")
        self.assertIsNone(snap["real"]["heart_rate_bpm"])
        self.assertIsNone(snap["real"]["spo2_percent"])
        self.assertEqual(snap["real"]["spo2_mode"], "unsupported")

    def test_server_refuses_without_token(self):
        from kinetiq_receiver import create_server as cs
        with self.assertRaises(SystemExit):
            cs(make_cfg(auth_token=""))

    def test_load_config_env_override(self):
        import os
        os.environ["KINETIQ_TOKEN"] = "env-token"
        try:
            cfg = load_config()
            self.assertEqual(cfg["auth_token"], "env-token")
        finally:
            del os.environ["KINETIQ_TOKEN"]


if __name__ == "__main__":
    unittest.main()
