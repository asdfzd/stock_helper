"""Audit regressions: synthetic evidence only, OS and network boundaries mocked."""
from collections import Counter
from contextlib import closing
from datetime import timedelta
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.models import instant, utc
from tests import one_hour_runner_test as original
from tools.one_hour_runner.artifacts import validate_zip
from tools.one_hour_runner.core import Runner, atomic_json
from tools.one_hour_runner.quality import assess_collection
from tools.one_hour_runner.system import WindowsShutdown


class ShutdownRaceTests(unittest.TestCase):
    """No DB generation needed: inject a verified publisher and mocked OS calls."""
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.clock = original.Clock()
        self.execute = Mock(return_value=SimpleNamespace(returncode=0))
        self.os_shutdown = WindowsShutdown(self.execute)
        self.r = Runner(Path(temp.name), clock=self.clock, shutdown=self.os_shutdown,
                        publisher=lambda r: (r.root/"synthetic.zip", dict(shutdown_eligible=True,
                            failures=[], run_id="synthetic", session_date="2026-10-12")))
        self.r.folder = self.r.root
        self.r.controller = SimpleNamespace(busy=False, worker=SimpleNamespace(running=False, provider=None), paper=None)
        validation = patch("tools.one_hour_runner.artifacts.validate_zip")
        validation.start()
        self.addCleanup(validation.stop)

    def break_clock_after_request(self, method, cancel_fails=False):
        def execute(args, **kwargs):
            if args[1] == "/s":
                setattr(self.clock, method, Mock(side_effect=ValueError("synthetic clock failure")))
                return SimpleNamespace(returncode=0)
            return SimpleNamespace(returncode=5 if cancel_fails else 0)
        self.execute.side_effect = execute
        self.r._finalize()

    def assert_cancel_attempted(self):
        self.assertEqual([c.args[0][1:] for c in self.execute.call_args_list], [["/s", "/f", "/t", "60"], ["/a"]])
        self.assertTrue(self.r.finalized)
        self.r._finalize()
        self.assertEqual(self.execute.call_count, 2)

    def test_wall_clock_failure_after_success_cancels_without_timestamp(self):
        self.break_clock_after_request("now")
        self.assert_cancel_attempted()
        self.assertIsNone(self.r.shutdown_at)
        self.assertFalse(self.r.shutdown_may_be_pending)
        self.assertFalse(self.os_shutdown.requested)
        self.assertEqual(self.r.state, "ERROR")
        self.assertEqual(json.loads((self.r.folder/"shutdown_status.json").read_text())["status"], "CANCELLED")

    def test_monotonic_failure_after_success_cancels(self):
        self.break_clock_after_request("monotonic")
        self.assert_cancel_attempted()
        self.assertIsNotNone(self.r.shutdown_at)
        self.assertIsNone(self.r.shutdown_deadline_mono)
        self.assertEqual(self.r.state, "ERROR")

    def test_wall_clock_and_abort_failure_keeps_pending_without_timestamp(self):
        self.break_clock_after_request("now", cancel_fails=True)
        self.assert_cancel_attempted()
        self.assertIsNone(self.r.shutdown_at)
        self.assertTrue(self.r.shutdown_may_be_pending)
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")
        self.assertEqual(self.r.reason, "shutdown_cancel_failed")
        self.assertEqual(self.r.summary["shutdown_state"], "SHUTDOWN_PENDING")
        status = json.loads((self.r.folder/"shutdown_status.json").read_text())
        self.assertEqual(status, dict(status="SHUTDOWN_PENDING", warning="shutdown_cancel_failed"))
        self.execute.side_effect = None
        self.r.cancel_shutdown()
        self.assertEqual(self.r.state, "SHUTDOWN_CANCELLED")
        self.assertFalse(self.r.shutdown_may_be_pending)

    def test_monotonic_and_abort_failure_keeps_pending(self):
        self.break_clock_after_request("monotonic", cancel_fails=True)
        self.assert_cancel_attempted()
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")

    def test_uncertain_execute_exception_attempts_abort(self):
        self.execute.side_effect = [OSError("return status lost after OS boundary"), SimpleNamespace(returncode=0)]
        self.r._finalize()
        self.assert_cancel_attempted()
        self.assertEqual(self.r.state, "ERROR")
        self.assertFalse(self.os_shutdown.may_be_pending)

    def test_uncertain_execute_and_abort_failure_stays_pending(self):
        self.execute.side_effect = OSError("synthetic boundary failure")
        self.r._finalize()
        self.assert_cancel_attempted()
        self.assertTrue(self.os_shutdown.may_be_pending)
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")

    def test_abort_and_all_status_writes_fail_warning_survives(self):
        write = atomic_json
        def write_until_request(path, value):
            if self.os_shutdown.attempted:
                raise OSError("disk unavailable")
            write(path, value)
        with patch("tools.one_hour_runner.core.atomic_json", side_effect=write_until_request):
            self.break_clock_after_request("now", cancel_fails=True)
        self.assert_cancel_attempted()
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")
        self.assertEqual(self.r.reason, "shutdown_cancel_failed")

    def test_ui_warning_and_close_refusal_without_clock_or_timestamp(self):
        from PySide6.QtWidgets import QApplication
        from tools.one_hour_runner.gui import StatusWindow
        app = QApplication.instance() or QApplication([])
        self.break_clock_after_request("now", cancel_fails=True)
        window = StatusWindow(self.r)
        window.show()
        self.assertIn("종료 취소에 실패", window.body.text())
        self.assertIn("확인할 수 없습니다", window.body.text())
        event = Mock()
        window.closeEvent(event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")
        self.execute.side_effect = None
        window.close()
        self.assertEqual(self.r.state, "SHUTDOWN_CANCELLED")
        app.processEvents()

    def test_ui_clock_failure_with_deadline_keeps_warning_visible(self):
        from PySide6.QtWidgets import QApplication
        from tools.one_hour_runner.gui import StatusWindow
        app = QApplication.instance() or QApplication([])
        self.r._finalize()
        self.clock.monotonic = Mock(side_effect=ValueError("clock broken"))
        self.r.reason = "shutdown_cancel_failed"
        window = StatusWindow(self.r)
        self.assertIn("종료 취소에 실패", window.body.text())
        window.close()
        app.processEvents()


class CoveragePolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE radar_batches(timestamp_utc TEXT,raw_json TEXT)")
        self.opened = instant("2026-10-12T13:30:00+00:00")
        self.candidates = []
        self.bars = []
        self.add_candidate("A")
        for seconds in range(0, 3900, 30):
            at = self.stamp(seconds)
            raw = dict(metadata=dict(ws_connected=True, ws_gap_detected=False, active={"A": "ND"},
                tracking_counts=dict(active_subscription_count=1)),
                observations=[dict(ticker="A", price=100, metadata=dict(price_received_utc=at, ws_gap_detected=False))])
            self.db.execute("INSERT INTO radar_batches VALUES(?,?)", (at, json.dumps(raw)))

    def stamp(self, seconds):
        return utc(self.opened+timedelta(seconds=seconds))

    def add_candidate(self, ticker, seconds=0, missing=(), eligible=True):
        self.candidates.append(dict(ticker=ticker, candidate_first_detected_at=self.stamp(seconds), analysis_eligible=eligible))
        for minute in range(60):
            absent = minute in missing
            self.bars.append(dict(ticker=ticker, bar_start_utc=self.stamp(minute*60),
                missing_bar=absent, aggregate_complete=not absent, volume=0 if not absent else ""))

    def change_batches(self, low, high, change):
        for rowid, stamp, raw_json in list(self.db.execute("SELECT rowid,* FROM radar_batches")):
            if self.stamp(low) <= stamp < self.stamp(high):
                raw = json.loads(raw_json)
                change(raw)
                self.db.execute("UPDATE radar_batches SET raw_json=? WHERE rowid=?", (json.dumps(raw), rowid))

    def assess(self):
        for name, rows in (("candidates", self.candidates), ("bars", self.bars)):
            with (self.root/(name+".csv")).open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        return assess_collection(self.db, self.root/"candidates.csv", self.root/"bars.csv", self.opened)

    def subscribe(self, ticker, start=0):
        def add(raw):
            raw["metadata"]["active"][ticker] = "ND"
            raw["metadata"]["tracking_counts"]["active_subscription_count"] = len(raw["metadata"]["active"])
            raw["observations"].append(dict(raw["observations"][0], ticker=ticker))
        self.change_batches(start, 3600, add)

    def test_full_hour_zero_volume_is_allowed(self):
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(q["timelines"]["batch"]["observed_slots"], 120)
        self.assertEqual(q["ws_evidence"]["raw_observations"], 120)
        self.assertEqual(q["opening_coverage"]["complete_post_detection_minutes"], 60)

    def test_middle_batch_loss_preserves_endpoints_but_fails(self):
        self.db.execute("DELETE FROM radar_batches WHERE timestamp_utc>=? AND timestamp_utc<?", (self.stamp(1200), self.stamp(2400)))
        q = self.assess()
        self.assertIn("first_hour_batch_coverage", q["failures"])
        self.assertIn("first_hour_batch_gap", q["failures"])
        self.assertEqual(q["timelines"]["batch"]["first_offset_seconds"], 0)
        self.assertEqual(q["timelines"]["batch"]["last_offset_seconds"], 3570)

    def test_high_coverage_but_long_consecutive_gap_fails(self):
        self.db.execute("DELETE FROM radar_batches WHERE timestamp_utc>=? AND timestamp_utc<?", (self.stamp(1200), self.stamp(1290)))
        q = self.assess()
        self.assertGreater(q["timelines"]["batch"]["coverage"], .95)
        self.assertIn("first_hour_batch_gap", q["failures"])

    def test_one_missing_batch_is_tolerated_and_reported(self):
        self.db.execute("DELETE FROM radar_batches WHERE timestamp_utc=?", (self.stamp(1200),))
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(q["timelines"]["batch"]["missing_slot_offsets_seconds"], [1200])

    def test_connected_without_acknowledged_subscription_fails(self):
        self.change_batches(900, 2100, lambda r: r["metadata"].update(active={}, tracking_counts=dict(active_subscription_count=0)))
        q = self.assess()
        self.assertEqual(q["timelines"]["ws_connected"]["coverage"], 1)
        self.assertIn("first_hour_ws_subscribed_coverage", q["failures"])
        self.assertIn("first_hour_fresh_observation_coverage", q["failures"])

    def test_disconnected_batches_with_observations_do_not_prove_live_feed(self):
        self.change_batches(900, 2100, lambda r: r["metadata"].update(ws_connected=False))
        q = self.assess()
        self.assertIn("first_hour_ws_connected_coverage", q["failures"])
        self.assertEqual(q["ws_evidence"]["disconnected_batches"], 40)

    def test_unknown_connection_is_not_assumed_connected(self):
        self.change_batches(0, 3600, lambda r: r["metadata"].pop("ws_connected"))
        q = self.assess()
        self.assertEqual(q["ws_evidence"]["unknown_connection_batches"], 120)
        self.assertIn("first_hour_ws_connected_coverage", q["failures"])

    def test_missing_subscription_and_receipt_evidence_is_reported(self):
        def erase(r):
            r["metadata"].pop("active")
            r["metadata"].pop("tracking_counts")
            r["observations"][0]["metadata"].pop("price_received_utc")
        self.change_batches(0, 3600, erase)
        q = self.assess()
        self.assertEqual(q["ws_evidence"]["unknown_subscription_batches"], 120)
        self.assertEqual(q["ws_evidence"]["observations_without_fresh_receipt"], 120)
        self.assertIn("first_hour_fresh_observation_coverage", q["failures"])

    def test_stale_and_future_receipts_never_count_as_fresh(self):
        for receipt in (self.stamp(-120), self.stamp(4000)):
            with self.subTest(receipt=receipt):
                self.change_batches(0, 3600, lambda r: r["observations"][0]["metadata"].update(price_received_utc=receipt))
                q = self.assess()
                self.assertEqual(q["timelines"]["fresh_observation"]["observed_slots"], 0)

    def test_complete_historical_bars_do_not_hide_observation_outage(self):
        self.change_batches(600, 3000, lambda r: r.update(observations=[]))
        q = self.assess()
        self.assertEqual(q["opening_coverage"]["complete_post_detection_coverage"], 1)
        self.assertIn("first_hour_fresh_observation_gap", q["failures"])

    def test_conflicting_active_gauge_blocks_shutdown(self):
        self.change_batches(0, 30, lambda r: r["metadata"]["tracking_counts"].update(active_subscription_count=2))
        self.assertIn("first_hour_subscription_evidence_mismatch", self.assess()["failures"])

    def test_only_one_actual_minute_does_not_pass(self):
        for b in self.bars[1:]:
            b.update(missing_bar=True, aggregate_complete=False)
        q = self.assess()
        self.assertEqual(q["opening_coverage"]["complete_post_detection_minutes"], 1)
        self.assertIn("first_hour_post_detection_ohlcv_coverage", q["failures"])

    def test_hlc_without_open_or_volume_is_observed_but_incomplete(self):
        for b in self.bars:
            b["aggregate_complete"] = False
        c = self.assess()["opening_coverage"]["candidates"][0]
        self.assertEqual(c["observed_post_detection_minutes"], 60)
        self.assertEqual(c["complete_post_detection_minutes"], 0)
        self.assertTrue(c["failures"])

    def test_late_discovery_missing_pre_detection_minutes_is_exempt(self):
        self.add_candidate("LATE", 2700, missing=range(45))
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        late = q["opening_coverage"]["candidates"][1]
        self.assertEqual(late["expected_post_detection_minutes"], 15)
        self.assertEqual(late["pre_detection_minutes_exempt"], 45)
        self.assertEqual(late["full_hour_complete_minutes"], 15)

    def test_partial_discovery_minute_is_exempt(self):
        self.add_candidate("LATE", 2715, missing=range(46))
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(q["opening_coverage"]["candidates"][1]["expected_post_detection_minutes"], 14)

    def test_very_late_candidate_has_explicit_no_full_minute_exemption(self):
        self.add_candidate("LATE", 3590, missing=range(60))
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(q["opening_coverage"]["candidates"][1]["exemption_reason"], "no_full_post_detection_minute")

    def test_post_discovery_gaps_still_fail_late_candidate(self):
        self.add_candidate("LATE", 2700, missing=range(49))
        self.subscribe("LATE", 2700)
        q = self.assess()
        self.assertIn("first_hour_candidate_coverage", q["failures"])
        self.assertEqual(q["opening_coverage"]["candidates"][1]["missing_post_detection_minute_offsets"], [45, 46, 47, 48])

    def test_missing_candidate_cannot_be_hidden_by_large_complete_cohort(self):
        for n in range(20):
            self.add_candidate("FULL"+str(n))
        self.add_candidate("MISSING", missing=range(60))
        self.subscribe("MISSING")
        q = self.assess()
        self.assertGreater(q["opening_coverage"]["complete_post_detection_coverage"], .95)
        self.assertIn("first_hour_candidate_coverage", q["failures"])

    def test_type_excluded_candidates_remain_diagnostic(self):
        self.add_candidate("EXCLUDED", missing=range(60), eligible=False)
        q = self.assess()
        self.assertEqual(q["status"], "PASS")
        self.assertEqual(q["opening_coverage"]["candidate_count"], 2)
        self.assertEqual(q["opening_coverage"]["eligible_candidate_count"], 1)

    def test_low_total_coverage_with_short_individual_gaps_fails(self):
        for i in (3, 13, 23, 33):
            self.bars[i].update(missing_bar=True, aggregate_complete=False)
        q = self.assess()
        self.assertIn("first_hour_post_detection_ohlcv_coverage", q["failures"])
        self.assertNotIn("first_hour_candidate_coverage", q["failures"])


class IntegratedAuditTests(unittest.TestCase):
    setUp = original.FixtureTests.setUp
    assert_no_network = original.FixtureTests.assert_no_network
    prepare = original.FixtureTests.prepare

    def refresh_opening(self):
        from market_radar.opening_hour.service import analyze_session
        c = self.r.controller
        c.opening_hour_result = analyze_session(c.config.data_dir, day=self.r.plan["session_date"],
            cache_path=c.config.cache_path, paper_paths=(c.paper_path, c.paper_review_path), output_db=c.opening_hour_path)

    def assert_verified_archive(self):
        self.assertIsNotNone(self.r.archive, self.r.reason)
        self.assertTrue(self.r.archive.exists())
        if self.r.summary.get("archive_kind") == "diagnostic":
            from tools.one_hour_runner.diagnostics import validate_diagnostic_zip
            validate_diagnostic_zip(self.r.archive, self.r.summary["run_id"], self.r.summary["session_date"])
        else:
            validate_zip(self.r.archive, self.r.summary["run_id"], self.r.summary["session_date"])

    def test_sparse_minutes_preserve_diagnostic_zip_and_block_shutdown(self):
        self.prepare()
        with closing(sqlite3.connect(self.r.controller.config.cache_path)) as db:
            db.execute("DELETE FROM minute_bars WHERE rowid NOT IN (SELECT rowid FROM minute_bars LIMIT 1)")
            db.commit()
        self.refresh_opening()
        self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.shutdown.request.assert_not_called()
        self.assert_verified_archive()
        with zipfile.ZipFile(self.r.archive) as archive:
            summary = json.loads(archive.read("one_hour_run_summary.json"))
            self.assertEqual(summary["first_60m_completeness"]["actual_1m_rows"], 1)
            self.assertEqual(summary["collection_quality"]["status"], "FAIL")
            self.assertFalse(summary["shutdown_eligible"])
            self.assertIn("first_hour_post_detection_ohlcv_coverage", summary["failures"])

    def test_mid_hour_ws_outage_preserves_diagnostic_zip_with_complete_bars(self):
        from market_radar.storage import RadarStore
        from market_radar.research.validator import revalidate_session
        self.prepare()
        c = self.r.controller
        with closing(sqlite3.connect(c.config.db_path)) as db:
            identity = json.loads(db.execute("SELECT value FROM radar_metadata WHERE key='identity'").fetchone()[0])
            for at, value in list(db.execute("SELECT timestamp_utc,raw_json FROM radar_batches")):
                offset = (instant(at)-instant(self.r.plan["open"])).total_seconds()
                if 1200 <= offset < 2400:
                    raw = json.loads(value)
                    raw["metadata"]["ws_connected"] = False
                    db.execute("UPDATE radar_batches SET raw_json=? WHERE timestamp_utc=?", (json.dumps(raw), at))
            db.commit()
        store = RadarStore(c.config.db_path, identity)
        try:
            store.export_all(c.config.data_dir/"export")
        finally:
            store.close()
        revalidate_session(dict(source_db_path=str(c.config.db_path), session_date_et=self.r.plan["session_date"],
            export_path=str(c.config.data_dir/"export"/self.r.plan["session_date"])))
        self.refresh_opening()
        self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.shutdown.request.assert_not_called()
        self.assert_verified_archive()
        self.assertIn("first_hour_ws_connected_coverage", self.r.summary["failures"])
        self.assertEqual(self.r.summary["collection_quality"]["opening_coverage"]["complete_post_detection_coverage"], 1)

    def generate_trades(self):
        """Drive unchanged engines; write only this test's private synthetic DBs."""
        from market_radar.paper.storage import PaperStore
        from market_radar.paper.engine import PaperEngine
        from market_radar.paper_rotation_v2.storage import RotationStore
        from market_radar.paper_rotation_v2.paired import PairedEngine
        from tests.paper_trading_test import row as template_row
        c = self.r.controller
        session = (c.calendar or original.ExchangeCalendar()).session(self.r.plan["session_date"])
        opened = instant(session.open_utc)
        def stamp(seconds):
            return utc(opened+timedelta(seconds=seconds))
        def row(seconds, ticker, price, **changes):
            return dict(template_row(seconds, ticker, price, **changes), timestamp_utc=stamp(seconds),
                price_received_utc=stamp(seconds), completed_bar_at=stamp(int(seconds//60)*60),
                candidate_detected_at=stamp(360))
        base, rotation = PaperStore(c.paper_path), RotationStore(c.paper_review_path)
        try:
            paired = PairedEngine(PaperEngine(base, c.lifecycle["run_id"], enabled=True,
                collection_mode="Regular Session"), rotation, rotation_enabled=True)
            def frame(s, rows):
                paired.on_frame(session, rows, stamp(s))
                self.assertFalse(paired.review_error)
            def tick(s, ticker, price):
                paired.on_tick(ticker, price, stamp(s))
                self.assertFalse(paired.review_error)
            frame(360, [row(360, "A", 99, current_leader_rank=5, emerging_rank=5)])
            frame(422, [row(422, "A", 100, current_leader_rank=5, emerging_rank=5)])
            tick(423, "A", 100)
            tick(449, "A", 100)
            frame(450, [row(450, "A", 100, current_leader_rank=5, emerging_rank=5),
                        row(450, "B", 20, current_leader_rank=1, emerging_rank=1)])
            tick(511, "A", 100)
            frame(512, [row(512, "A", 100, current_leader_rank=5, emerging_rank=5, return_1m=-1, return_5m=1),
                        row(512, "B", 21, current_leader_rank=1, emerging_rank=1, return_1m=4, return_5m=6)])
            tick(513, "A", 100)  # Rotation switch exit; baseline continues holding.
            tick(514, "B", 21)   # Rotation switch entry on a later observation.
            tick(515, "A", 116)  # Baseline TP and post-switch original TP observation.
            tick(516, "B", 18)   # Rotation SL.
            tick(517, "B", 21)   # Post-SL recovery.
            tick(518, "B", 25)   # Post-SL original TP.
            for s in range(540, 3900, 30):
                tick(s, "A", 117)
                tick(s, "B", 25)
            paired.finish(self.r.plan["stop"])
            self.assertFalse(paired.review_error)
            base.export(session.date, c.paper_path.parent/"export"/session.date)
            rotation.export_review(session.date, c.paper_review_path.parent/"export"/session.date, base.summary(session.date))
            self.assertEqual([t["exit_reason"] for t in base.entered(session.date)], ["TAKE_PROFIT_15"])
            self.assertEqual({t["exit_reason"] for t in rotation.entered(session.date)}, {"SUPERIOR_CANDIDATE_SWITCH", "STOP_LOSS_10"})
            self.assertEqual(len(rotation.documents("rotation_switches")), 1)
            posts = rotation.documents("paper_post_exit")
            self.assertTrue(any(p["recovered_entry_after_sl"] and p["original_tp_hit_after_sl"] for p in posts))
            self.assertTrue(all(p["tracking_status"] == "INCOMPLETE" for p in posts))
        finally:
            base.close()
            rotation.close()
        self.refresh_opening()

    def test_nonempty_tp_sl_switch_post_exit_exports_match_sqlite_and_zip(self):
        self.prepare()
        self.generate_trades()
        self.r.tick()
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING", self.r.reason)
        self.shutdown.request.assert_called_once()
        self.assert_verified_archive()
        c = self.r.controller
        with zipfile.ZipFile(self.r.archive) as archive:
            for group, path, tables in (
                ("paper_v1", c.paper_path, ("paper_trades", "paper_trade_events", "paper_trade_marks", "paper_horizons")),
                ("rotation_v2", c.paper_review_path, ("paper_trade_marks", "paper_horizons", "paper_post_exit_marks"))):
                with closing(sqlite3.connect(path)) as db:
                    for table in tables:
                        rows = list(csv.reader(io.StringIO(archive.read(group+"/"+table+".csv").decode("utf-8-sig"))))
                        cursor = db.execute("SELECT * FROM "+table)
                        self.assertEqual(rows[0], [d[0] for d in cursor.description])
                        expected = [tuple("" if v is None else str(v) for v in r) for r in cursor]
                        self.assertTrue(expected, table)
                        self.assertEqual(Counter(map(tuple, rows[1:])), Counter(expected), table)
            with closing(sqlite3.connect(c.paper_review_path)) as db:
                for name, table in (("paper_trades", "review_trades"),
                                    ("paper_rotation_switches", "rotation_switches"),
                                    ("paper_post_exit", "paper_post_exit")):
                    reader = csv.DictReader(io.StringIO(archive.read("rotation_v2/"+name+".csv").decode()))
                    rows, fields = list(reader), reader.fieldnames
                    documents = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM "+table)]
                    def cells(document):
                        def encode(value):
                            if isinstance(value, (dict, list)):
                                return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
                            return "" if value is None else str(value)
                        return tuple(encode(document.get(k)) for k in fields)
                    self.assertTrue(documents, table)
                    self.assertEqual(Counter(tuple(r[k] for k in fields) for r in rows),
                                     Counter(map(cells, documents)), name)
                pairs = list(csv.DictReader(io.StringIO(archive.read("rotation_v2/paper_rotation_comparison.csv").decode())))
                switches = [json.loads(r[0]) for r in db.execute("SELECT detail_json FROM rotation_switches")]
                self.assertEqual({p["switch_id"] for p in pairs if p["switch_id"]}, {s["switch_id"] for s in switches})
                chain = [p for p in pairs if p["rotation_root_trade_id"]]
                self.assertEqual(len(chain), 1)
                self.assertEqual(chain[0]["match_status"], "EXACT_SAME_ENTRY")
                self.assertAlmostEqual(float(chain[0]["baseline_v1_return"]), 16)
                self.assertAlmostEqual(float(chain[0]["rotation_v2_return"]), (18/21-1)*100)
                switched = [p for p in pairs if p["switch_id"]][0]
                self.assertEqual(switched["aligned_comparison_status"], "NO_EXACT_OLD_PRICE_AT_NEW_EXIT")
                self.assertEqual(switched["hold_old_from_switch_return_pct"], "")
            summary = json.loads(archive.read("one_hour_run_summary.json"))
            self.assertEqual(summary["collection_quality"]["status"], "PASS")
            self.assertEqual(summary["original_validation_overall"], "FAIL")

    def test_nonempty_csv_value_tamper_blocks_publication(self):
        self.prepare()
        self.generate_trades()
        path = self.r.controller.paper_path.parent/"export"/self.r.plan["session_date"]/"paper_trades.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields, rows = reader.fieldnames, list(reader)
        rows[0]["exit_price"] = "999"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        self.r.tick()
        self.assertEqual(self.r.reason, "paper_export_rows")
        self.shutdown.request.assert_not_called()

    def test_nonempty_rotation_post_exit_value_tamper_blocks_publication(self):
        self.prepare()
        self.generate_trades()
        path = self.r.controller.paper_review_path.parent/"export"/self.r.plan["session_date"]/"paper_post_exit_marks.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        rows[1][-1] = "999"
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows(rows)
        self.r.tick()
        self.assertEqual(self.r.reason, "research_export_mismatch")
        self.shutdown.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
