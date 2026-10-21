"""Offline stabilization regressions. No live provider, original data or OS calls."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests import one_hour_runner_test as original
from tests import one_hour_runner_audit_test as audit
from tools.one_hour_runner.artifacts import validate_zip
from tools.one_hour_runner.diagnostics import validate_diagnostic_zip
from tools.one_hour_runner.replay_profile import distribution


class DelayGuardTests(unittest.TestCase):
    def run_delay(self, delay):
        from tests.paper_trading_test import SESSION, row, stamp
        from market_radar.models import instant
        from market_radar.config import CONFIG
        from market_radar.paper_rotation_v2.paired import PairedEngine
        from market_radar.paper_rotation_v2.runtime import PairedRuntime
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = PairedRuntime(CONFIG, "synthetic", root/"paper.db", review_path=root/"rotation.db",
                                    rotation_enabled=True, clock=lambda: instant(stamp(360+delay)))
            def seeded(baseline, review, **kwargs):
                engine = PairedEngine(baseline, review, **kwargs)
                engine.on_frame(SESSION, [row(360)], stamp(360), ws_connected=True, ws_gap=False)
                return engine
            runtime._put(("tick", "TEST", 10.01, stamp(360), True))
            runtime.done.set()  # Drain this one queued event synchronously; no thread timing assumption.
            with patch("market_radar.paper_rotation_v2.runtime.PairedEngine", side_effect=seeded):
                runtime._run()
            reasons = []
            for name in ("paper.db", "rotation.db"):
                with closing(sqlite3.connect(root/name)) as db:
                    reasons.append([json.loads(r[0]).get("reason") for r in db.execute("SELECT evidence_json FROM paper_trade_events")])
            return runtime.error, runtime.sidecar_error, reasons

    def test_incident_2047033_ms_triggers_unchanged_gate_in_both_engines(self):
        error, sidecar, reasons = self.run_delay(2.047033)
        self.assertTrue(error)
        self.assertFalse(sidecar)
        for values in reasons:
            self.assertIn("paper_processing_delay", values)

    def test_exact_two_seconds_is_not_silently_changed(self):
        error, _, reasons = self.run_delay(2.0)
        self.assertFalse(error)
        for values in reasons:
            self.assertNotIn("paper_processing_delay", values)

    def test_percentiles_include_tail_not_just_mean(self):
        d = distribution(list(range(1, 101)))
        self.assertEqual((d["mean_ms"], d["p95_ms"], d["p99_ms"], d["max_ms"]), (50.5, 95, 99, 100))


class SubscriptionScopeTests(unittest.TestCase):
    setUp = audit.CoveragePolicyTests.setUp
    stamp = audit.CoveragePolicyTests.stamp
    add_candidate = audit.CoveragePolicyTests.add_candidate
    change_batches = audit.CoveragePolicyTests.change_batches
    assess = audit.CoveragePolicyTests.assess
    subscribe = audit.CoveragePolicyTests.subscribe

    def test_unsubscribed_discoveries_do_not_fail_live_collection(self):
        for number in range(311):
            self.add_candidate("UNSUB"+str(number), missing=range(60))
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        coverage = q["opening_coverage"]
        self.assertLess(coverage["complete_post_detection_coverage"], .01)
        self.assertEqual(coverage["subscribed_ohlcv_coverage"], 1)
        self.assertEqual(coverage["subscription_scope"]["explicit_slot_unavailable_minutes"], 0)
        self.assertEqual(coverage["scope_status"], "PARTIAL")

    def test_capacity_requires_explicit_evidence(self):
        self.add_candidate("WAIT", missing=range(60))
        self.change_batches(0, 3600, lambda r: r["metadata"].update(missing_reasons={"WAIT": "tracking_slot_unavailable"}))
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(q["opening_coverage"]["subscription_scope"]["explicit_slot_unavailable_minutes"], 60)

    def test_subscribed_cache_gap_remains_a_failure(self):
        for b in self.bars[20:25]:
            b.update(missing_bar=True, aggregate_complete=False)
        q = self.assess()
        self.assertEqual(q["timelines"]["fresh_observation"]["coverage"], 1)
        self.assertIn("first_hour_candidate_coverage", q["failures"])
        self.assertEqual(q["opening_coverage"]["subscription_scope"]["cache_missing_subscribed_minutes"], 5)

    def test_healthy_symbol_cannot_hide_other_subscribed_receipt_outage(self):
        self.add_candidate("B")
        self.subscribe("B")
        self.change_batches(900, 2100, lambda r: r.update(observations=[o for o in r["observations"] if o["ticker"] != "B"]))
        q = self.assess()
        self.assertEqual(q["timelines"]["fresh_observation"]["coverage"], 1)
        self.assertIn("first_hour_subscribed_observation_coverage", q["failures"])

    def test_unknown_subscription_is_not_reported_as_unsubscribed(self):
        self.change_batches(600, 1200, lambda r: r["metadata"].pop("active"))
        q = self.assess()
        self.assertEqual(q["opening_coverage"]["subscription_scope"]["unknown_subscription_minutes"], 10)
        self.assertEqual(q["opening_coverage"]["subscription_scope"]["not_subscribed_minutes"], 0)
        self.assertEqual(q["status"], "FAIL")

    def test_high_symbol_coverage_cannot_hide_120_second_internal_gap(self):
        self.add_candidate("B")
        self.subscribe("B")
        self.change_batches(900, 990, lambda r: r.update(observations=[o for o in r["observations"] if o["ticker"] != "B"]))
        q = self.assess()
        candidate = q["opening_coverage"]["candidates"][1]
        self.assertGreater(candidate["subscribed_observation_coverage"], .95)
        self.assertEqual(candidate["maximum_subscribed_observation_gap_seconds"], 120)
        self.assertIn("first_hour_subscribed_observation_coverage", q["failures"])

    def test_late_subscribed_window_excludes_pre_discovery(self):
        self.add_candidate("LATE", 2715, missing=range(46))
        self.subscribe("LATE", 2730)
        q = self.assess()
        late = q["opening_coverage"]["candidates"][1]
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(late["subscribed_minutes"], list(range(46, 60)))

    def test_partial_subscription_boundary_is_not_full_minute(self):
        self.add_candidate("LATE", 0, missing=range(31))
        self.subscribe("LATE", 1830)
        q = self.assess()
        late = q["opening_coverage"]["candidates"][1]
        self.assertEqual(late["subscription_scope"]["partially_subscribed_minutes"], 1)
        self.assertEqual(q["status"], "PASS")


class DiagnosticArchiveTests(unittest.TestCase):
    setUp = original.FixtureTests.setUp
    assert_no_network = original.FixtureTests.assert_no_network
    prepare = original.FixtureTests.prepare
    finish = original.FixtureTests.finish

    def diagnostic(self, *, sidecar=False):
        self.prepare()
        if sidecar:
            self.r.controller.paper.sidecar_error = True
        else:
            self.r.controller.paper.error = True
        self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.shutdown.request.assert_not_called()
        self.assertFalse(self.r.summary["shutdown_eligible"])
        self.assertEqual(self.r.summary["archive_kind"], "diagnostic")
        return validate_diagnostic_zip(self.r.archive, self.r.summary["run_id"], self.r.summary["session_date"])

    def test_paper_failure_preserves_exports_and_original_validation(self):
        manifest = self.diagnostic()
        self.assertEqual(manifest["status"], "FAILED")
        with zipfile.ZipFile(self.r.archive) as z:
            d = json.loads(z.read("diagnostics.json"))
            self.assertTrue(d["runtime"]["paper_error"])
            self.assertEqual(d["original_validator"]["overall"], "FAIL")
            self.assertEqual(d["missing_or_rejected_exports"], {})
            self.assertEqual(d["data_scope"]["opening_hour"]["included"], 11)
            self.assertEqual(d["collection_quality"]["status"], "PASS")
            self.assertTrue(all(Path(n).suffix in {".json", ".csv"} for n in z.namelist()))
        self.assertFalse((self.r.folder/"shutdown_status.json").exists())

    def test_rotation_failure_preserves_diagnostic(self):
        self.diagnostic(sidecar=True)
        with zipfile.ZipFile(self.r.archive) as z:
            self.assertTrue(json.loads(z.read("diagnostics.json"))["runtime"]["sidecar_error"])

    def test_paired_engine_view_error_cannot_bypass_runtime_flags(self):
        self.prepare()
        self.r.controller.paper.view = lambda: dict(post_exit_tracking="ERROR", rotation_v2=dict(status="ERROR sidecar unavailable"))
        self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.assertEqual(self.r.summary["archive_kind"], "diagnostic")
        with zipfile.ZipFile(self.r.archive) as z:
            state = json.loads(z.read("diagnostics.json"))["runtime"]
            self.assertFalse(state["paper_error"])
            self.assertFalse(state["sidecar_error"])
            self.assertTrue(state["rotation_view_error"])
        self.shutdown.request.assert_not_called()

    def test_diagnostic_is_rejected_by_success_validator(self):
        self.diagnostic()
        with self.assertRaises(Exception):
            validate_zip(self.r.archive, self.r.summary["run_id"], self.r.summary["session_date"])

    def test_zero_trades_still_produce_success_zip(self):
        self.r.no_shutdown = True
        self.finish()
        self.assertEqual(self.r.state, "COMPLETE_NO_SHUTDOWN")
        validate_zip(self.r.archive, self.r.summary["run_id"], self.r.summary["session_date"])
        self.assertFalse(self.r.archive.name.startswith("one_hour_diagnostic_"))

    def test_missing_export_is_named_and_other_exports_survive(self):
        self.prepare()
        missing = self.r.folder/"paper_v1/export"/self.r.plan["session_date"]/"paper_horizons.csv"
        missing.unlink()
        self.r.controller.paper.error = True
        self.r.tick()
        with zipfile.ZipFile(self.r.archive) as z:
            d = json.loads(z.read("diagnostics.json"))
            self.assertIn("paper_v1/paper_horizons.csv", d["missing_or_rejected_exports"])
            self.assertIn("radar/snapshots.csv", z.namelist())
        self.shutdown.request.assert_not_called()

    def test_sensitive_export_is_omitted_without_leaking_value(self):
        self.prepare()
        p = self.r.folder/"paper_v1/export"/self.r.plan["session_date"]/"summary.json"
        p.write_text('{"access_token":"SYNTHETIC_ONLY_SENTINEL"}', encoding="utf-8")
        self.r.controller.paper.error = True
        self.r.tick()
        with zipfile.ZipFile(self.r.archive) as z:
            self.assertNotIn("paper_v1/summary.json", z.namelist())
            for name in z.namelist():
                self.assertNotIn(b"SYNTHETIC_ONLY_SENTINEL", z.read(name))

    def test_zip_write_failure_never_requests_shutdown(self):
        self.prepare()
        self.r.controller.paper.error = True
        with patch("tools.one_hour_runner.diagnostics.zipfile.ZipFile", side_effect=OSError("synthetic disk failure")):
            self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.assertIsNone(self.r.archive)
        self.assertFalse(list(self.r.folder.glob("one_hour_diagnostic_*.zip")))
        self.assertFalse(list(self.r.folder.glob("diagnostic_*.tmp")))
        self.shutdown.request.assert_not_called()

    def test_diagnostic_verification_failure_prevents_publish(self):
        self.prepare()
        self.r.controller.paper.error = True
        with patch("tools.one_hour_runner.diagnostics.validate_diagnostic_zip", side_effect=ValueError("synthetic")):
            self.r.tick()
        self.assertFalse(list(self.r.folder.glob("one_hour_diagnostic_*.zip")))
        self.shutdown.request.assert_not_called()

    def test_diagnostic_hash_tampering_is_rejected(self):
        self.diagnostic()
        changed = self.root/"tampered.zip"
        with zipfile.ZipFile(self.r.archive) as src, zipfile.ZipFile(changed, "w") as dst:
            for name in src.namelist():
                data = src.read(name)+(b" " if name == "paper_v1/summary.json" else b"")
                dst.writestr(name, data)
        with self.assertRaisesRegex(Exception, "diagnostic_hash_mismatch"):
            validate_diagnostic_zip(changed, self.r.summary["run_id"], self.r.summary["session_date"])

    def test_original_databases_and_exports_are_unchanged(self):
        self.prepare()
        paths = [p for p in self.r.folder.rglob("*") if p.is_file() and p.name != "one_hour_run_summary.json"]
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths}
        self.r.controller.paper.error = True
        self.r.tick()
        after = {p: (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
