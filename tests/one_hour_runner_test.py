"""Offline only: synthetic DB/export fixtures, public Controller and mocked shutdown."""
from contextlib import closing
from dataclasses import fields, replace
from datetime import timedelta
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.config import CONFIG
from market_radar.controller import CollectorController
from market_radar.models import instant
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.scheduling import SessionSchedule
from tools.one_hour_runner.artifacts import REQUIRED, publish_run, validate_zip
from tools.one_hour_runner.core import Runner, atomic_json, plan_for, runner_config, threads_finished
from tools.one_hour_runner.dry_run import SyntheticClock, SyntheticController
from tools.one_hour_runner.system import InstanceGuard, RunBlocked, WindowsShutdown, install_safe_logging

ROOT = Path(__file__).resolve().parents[1]


class Clock(SyntheticClock):
    def __init__(self, value="2026-10-12T12:59:00+00:00"):
        super().__init__()
        self.wall = instant(value)


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = ExchangeCalendar()

    def plan(self, at):
        return plan_for(runner_config(Path("unused")), Clock(at), self.calendar)

    def test_armed_before_open(self):
        s = SessionSchedule(runner_config(Path("unused")), self.calendar, Clock())
        self.assertEqual(s.phase, "ARMED")
        self.assertFalse(s.allows_research())

    def test_60_and_65_from_official_open(self):
        p = self.plan("2026-10-12T10:00:00+00:00")
        self.assertEqual(instant(p["opening_hour_end"])-instant(p["open"]), timedelta(minutes=60))
        self.assertEqual(instant(p["stop"])-instant(p["open"]), timedelta(minutes=65))
        self.assertEqual(p["open"], "2026-10-12T13:30:00+00:00")

    def test_dst_spring(self):
        self.assertEqual(instant(self.plan("2026-03-06T10:00:00+00:00")["open"]).hour, 14)
        self.assertEqual(instant(self.plan("2026-03-09T10:00:00+00:00")["open"]).hour, 13)

    def test_dst_autumn(self):
        self.assertEqual(instant(self.plan("2026-10-30T10:00:00+00:00")["open"]).hour, 13)
        self.assertEqual(instant(self.plan("2026-11-02T10:00:00+00:00")["open"]).hour, 14)

    def test_holiday(self):
        self.assertEqual(self.plan("2026-12-25T10:00:00+00:00")["session_date"], "2026-12-28")

    def test_weekend(self):
        self.assertEqual(self.plan("2026-10-10T10:00:00+00:00")["session_date"], "2026-10-12")

    def test_early_close_preserved(self):
        p = self.plan("2026-11-27T10:00:00+00:00")
        self.assertEqual(p["official_close"], "2026-11-27T18:00:00+00:00")
        self.assertEqual(p["stop"], "2026-11-27T15:35:00+00:00")

    def test_late_launch_rejected(self):
        for at in ("13:30:00", "13:45:00", "18:00:00"):
            with self.assertRaisesRegex(RunBlocked, "late_launch"):
                self.plan("2026-10-12T"+at+"+00:00")

    def test_after_close_next_day(self):
        self.assertEqual(self.plan("2026-10-12T20:00:00+00:00")["session_date"], "2026-10-13")

    def test_config_policy_unchanged(self):
        config = runner_config(Path("run"))
        allowed = {"collection_mode", "max_ws_symbols", "data_dir", "historical_cache_path"}
        for field in fields(CONFIG):
            if field.name not in allowed:
                self.assertEqual(getattr(config, field.name), getattr(CONFIG, field.name))
        self.assertEqual(config.collection_mode, "Regular Session")
        self.assertEqual(config.max_ws_symbols, 150)
        self.assertFalse(config.cache_path.resolve().is_relative_to(config.data_dir.resolve()))


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # An accidental market call fails the test, even if code catches its exception.
        self.connect = patch.object(socket.socket, "connect", side_effect=AssertionError("network_forbidden"))
        self.network = self.connect.start()
        self.addCleanup(self.connect.stop)
        self.shutdown = Mock(spec=WindowsShutdown)
        self.clock = Clock()
        self.r = Runner(self.root, clock=self.clock, controller_factory=SyntheticController, shutdown=self.shutdown)
        self.r.start(cache=self.root/"synthetic_cache.db")
        self.addCleanup(self.assert_no_network)

    def assert_no_network(self):
        self.network.assert_not_called()

    def prepare(self):
        while self.r.controller.busy:
            self.r.controller.schedule.tick(True)
            self.r.tick()
            if self.r.controller.busy:
                self.clock.advance(1)
        self.assertFalse(self.r.finalized)
        self.shutdown.request.assert_not_called()

    def finish(self):
        self.prepare()
        self.r.tick()
        return self.r

    def test_flags_and_initial_ui_state(self):
        self.assertEqual(self.r.state, "ARMED")
        self.assertFalse(self.r.controller.config.db_path.exists())

    def test_normal_stop_exactly_65_and_shutdown_once(self):
        r = self.finish()
        self.assertEqual(r.state, "SHUTDOWN_PENDING", r.reason)
        self.assertEqual(r.requested_stop_at, r.plan["stop"])
        self.shutdown.request.assert_called_once_with()
        for _ in range(4):
            r.tick()
        self.shutdown.request.assert_called_once_with()

    def test_original_early_regular_fail_preserved(self):
        r = self.finish()
        self.assertEqual(r.summary["original_validation_overall"], "FAIL")
        self.assertFalse(r.summary["original_protocol_compliant"])
        self.assertFalse(r.summary["full_session"])
        codes = {v["code"] for v in r.summary["original_validation_errors"]}
        self.assertIn("stop_outside_tolerance", codes)
        self.assertEqual(r.controller.schedule.stop_at, instant(r.plan["official_close"]))

    def test_no_shutdown_option(self):
        self.r.no_shutdown = True
        self.finish()
        self.assertEqual(self.r.state, "COMPLETE_NO_SHUTDOWN")
        self.shutdown.request.assert_not_called()

    def test_dry_run_forces_no_shutdown(self):
        r = Runner(self.root/"other", clock=Clock(), controller_factory=SyntheticController,
                   shutdown=self.shutdown, simulated=True)
        self.assertTrue(r.no_shutdown)

    def test_zip_and_all_exports(self):
        r = self.finish()
        manifest = validate_zip(r.archive, r.summary["run_id"], r.summary["session_date"])
        self.assertEqual(set(manifest["files"]), REQUIRED)
        self.assertNotEqual(r.summary["run_id"], r.summary["review_run_id"])
        self.assertEqual(r.summary["first_60m_completeness"]["actual_1m_rows"], 60)
        self.assertEqual(r.summary["first_60m_completeness"]["status"], "COMPLETE")

    def test_zero_trades_allowed(self):
        r = self.finish()
        with zipfile.ZipFile(r.archive) as z:
            paper = json.loads(z.read("paper_v1/summary.json"))
            self.assertEqual(paper["total_entries"], 0)
            self.assertEqual(len(z.read("paper_v1/paper_trades.csv").splitlines()), 1)
        self.shutdown.request.assert_called_once()

    def test_zip_failure_no_shutdown(self):
        self.prepare()
        with patch("tools.one_hour_runner.artifacts.zipfile.ZipFile", side_effect=OSError("synthetic")):
            self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.shutdown.request.assert_not_called()
        self.assertEqual(list(self.r.folder.glob("one_hour_research_*.zip")), [])

    def test_save_failure_no_shutdown(self):
        self.prepare()
        with patch("tools.one_hour_runner.artifacts.atomic_json", side_effect=OSError("synthetic")):
            self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.state, "ERROR")

    def test_unclean_shutdown_no_os_call(self):
        self.prepare()
        self.r.controller.lifecycle["clean_shutdown"] = False
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.reason, "collector_not_clean")

    def test_worker_still_running_waits_without_kill(self):
        self.prepare()
        self.r.controller.worker.provider.io_thread = SimpleNamespace(is_alive=lambda: True)
        for _ in range(3):
            self.r.tick()
        self.assertFalse(self.r.finalized)
        self.assertEqual(self.r.state, "FINALIZING")
        self.shutdown.request.assert_not_called()
        self.r.controller.worker.provider.io_thread = None
        self.r.tick()
        self.assertEqual(self.r.state, "ERROR")
        self.shutdown.request.assert_not_called()

    def test_paper_storage_failure_no_shutdown(self):
        self.prepare()
        self.r.controller.paper.error = True
        self.r.tick()
        self.assertEqual(self.r.reason, "paper_processing_error")
        self.shutdown.request.assert_not_called()

    def test_rotation_storage_failure_no_shutdown(self):
        self.prepare()
        self.r.controller.paper.sidecar_error = True
        self.r.tick()
        self.shutdown.request.assert_not_called()

    def test_opening_failure_no_shutdown(self):
        self.prepare()
        self.r.controller.opening_hour_result["status"] = "INCOMPLETE"
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.reason, "opening_processing_error")

    def test_windows_request_failure_error(self):
        self.shutdown.request.side_effect = RunBlocked("windows_shutdown_request_failed")
        self.finish()
        self.assertEqual(self.r.state, "ERROR")
        self.shutdown.request.assert_called_once()
        self.assertTrue(self.r.archive.is_file())

    def test_cancel_shutdown(self):
        self.finish()
        self.r.cancel_shutdown()
        self.shutdown.cancel.assert_called_once()
        self.assertEqual(self.r.state, "SHUTDOWN_CANCELLED")

    def test_cancel_failure_keeps_pending_ui(self):
        self.finish()
        self.shutdown.cancel.side_effect = OSError("synthetic")
        self.r.cancel_shutdown()
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")
        self.assertEqual(self.r.reason, "shutdown_cancel_failed")

    def test_missing_export(self):
        self.prepare()
        (self.r.folder/"paper_v1/export"/self.r.plan["session_date"]/"paper_horizons.csv").unlink()
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.reason, "missing_or_foreign_file")

    def test_foreign_run_same_date_blocked(self):
        self.prepare()
        with closing(sqlite3.connect(self.r.controller.paper_path)) as db:
            db.execute("UPDATE paper_sessions SET radar_run_id='different-run'")
            db.commit()
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.reason, "paper_session_identity")

    def test_export_foreign_date_blocked(self):
        self.prepare()
        path = self.r.folder/"rotation_v2/export"/self.r.plan["session_date"]/"paper_daily_summary.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["session_date_et"] = "2026-10-13"
        atomic_json(path, value)
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.reason, "foreign_session_date")

    def test_sensitive_export_blocks_publish(self):
        self.prepare()
        path = self.r.folder/"paper_v1/export"/self.r.plan["session_date"]/"summary.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["access_token"] = "SYNTHETIC_SENTINEL_ONLY"
        atomic_json(path, value)
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.reason, "sensitive_artifact_content")

    def test_no_original_db_env_or_logs_in_zip(self):
        (self.r.folder/".env").write_text("SYNTHETIC_SENTINEL_ONLY", encoding="utf-8")
        r = self.finish()
        with zipfile.ZipFile(r.archive) as z:
            self.assertEqual(set(z.namelist()), REQUIRED|{"manifest.json"})
            for name in z.namelist():
                self.assertNotIn(b"SYNTHETIC_SENTINEL_ONLY", z.read(name))
                self.assertNotIn(Path(name).suffix, {".db", ".log", ".tmp"})

    def test_manifest_tamper(self):
        r = self.finish()
        changed = self.root/"tampered.zip"
        with zipfile.ZipFile(r.archive) as z, zipfile.ZipFile(changed, "w") as out:
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename == "paper_v1/summary.json":
                    data += b" "
                out.writestr(item.filename, data)
        with self.assertRaisesRegex(RunBlocked, "manifest_hash_mismatch"):
            validate_zip(changed, r.summary["run_id"], r.summary["session_date"])

    def test_crc_corruption(self):
        r = self.finish()
        changed = self.root/"corrupted.zip"
        data = bytearray(r.archive.read_bytes())
        data[70:100] = b"x"*30
        changed.write_bytes(data)
        with self.assertRaises(Exception):
            validate_zip(changed, r.summary["run_id"], r.summary["session_date"])

    def test_zip_identity_rejected(self):
        r = self.finish()
        with self.assertRaisesRegex(RunBlocked, "zip_identity"):
            validate_zip(r.archive, "foreign-run", r.summary["session_date"])

    def test_zip_validation_before_atomic_publish(self):
        self.prepare()
        with patch("tools.one_hour_runner.artifacts.validate_zip", side_effect=RunBlocked("bad_zip")):
            self.r.tick()
        self.assertFalse(list(self.r.folder.glob("one_hour_research_*.zip")))
        self.assertFalse(list(self.r.folder.glob("one_hour_*.tmp")))
        self.shutdown.request.assert_not_called()

    def test_clock_jump_preserved(self):
        self.clock.wall += timedelta(seconds=10)
        # Avoid expensive fixture generation on a deliberately preopen cancellation.
        self.r.controller.stop = Mock()
        self.r.tick()
        self.assertIn("clock_jump", self.r.failures)
        self.r.controller.stop.assert_called_once()
        self.shutdown.request.assert_not_called()

    def test_sleep_resume_preserved(self):
        self.clock.advance(6)
        self.r.controller.stop = Mock()
        self.r.tick()
        self.assertIn("scheduler_gap_or_resume", self.r.failures)
        self.shutdown.request.assert_not_called()

    def test_two_runs_have_separate_paths(self):
        other = Runner(self.root, clock=Clock(), controller_factory=SyntheticController, no_shutdown=True)
        other.start(cache=self.root/"synthetic_cache.db")
        self.assertNotEqual(other.folder, self.r.folder)
        self.assertNotEqual(other.controller.paper_path, self.r.controller.paper_path)
        self.assertNotEqual(other.summary["run_id"], self.r.summary["run_id"])

    def test_stop_at_60_is_not_requested(self):
        while self.clock.now() < instant(self.r.plan["opening_hour_end"]):
            self.r.controller.schedule.tick(True)
            self.r.tick()
            self.clock.advance(1)
        self.r.controller.schedule.tick(True)
        self.r.tick()
        self.assertIsNone(self.r.requested_stop_at)
        self.assertEqual(self.r.state, "COLLECTING")

    def test_shutdown_intent_disk_failure_blocks_request(self):
        self.prepare()
        original = atomic_json
        def write(path, value):
            if path.name == "shutdown_status.json":
                raise OSError("synthetic")
            return original(path, value)
        with patch("tools.one_hour_runner.core.atomic_json", side_effect=write):
            self.r.tick()
        self.shutdown.request.assert_not_called()

    def test_cancel_during_final_zip_validation(self):
        self.prepare()
        original = validate_zip
        def cancel(path, run_id, day):
            self.assertFalse(self.r.finalized)
            result = original(path, run_id, day)
            self.r.request_cancel()
            return result
        with patch("tools.one_hour_runner.artifacts.validate_zip", side_effect=cancel):
            self.r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.state, "ERROR")

    def test_cancel_at_windows_boundary(self):
        self.shutdown.request.side_effect = self.r.request_cancel
        self.finish()
        self.shutdown.request.assert_called_once()
        self.shutdown.cancel.assert_called_once()
        self.assertEqual(self.r.state, "SHUTDOWN_CANCELLED")

    def test_pending_state_if_abort_after_disk_failure_also_fails(self):
        self.prepare()
        self.shutdown.requested = True
        self.shutdown.cancel.side_effect = RunBlocked("windows_shutdown_request_failed")
        original = atomic_json
        def write(path, value):
            if path.name == "shutdown_status.json" and value["status"] == "SCHEDULED":
                raise OSError("synthetic")
            return original(path, value)
        with patch("tools.one_hour_runner.core.atomic_json", side_effect=write):
            self.r.tick()
        self.assertEqual(self.r.state, "SHUTDOWN_PENDING")
        self.assertEqual(self.r.reason, "shutdown_cancel_failed")

    def test_late_launcher_creates_failure_summary(self):
        r = Runner(self.root/"late", clock=Clock("2026-10-12T13:30:00+00:00"), shutdown=self.shutdown)
        r.start()
        self.assertEqual(r.state, "ERROR")
        self.assertEqual(r.reason, "late_launch")
        self.assertIsNone(r.controller)
        self.assertTrue((r.folder/"one_hour_run_summary.json").exists())
        self.shutdown.request.assert_not_called()

    def test_truncated_opening_export_rejected(self):
        self.prepare()
        path = Path(self.r.controller.opening_hour_result["export_path"])/"bars_1m.csv"
        path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
        self.r.tick()
        self.assertEqual(self.r.reason, "research_export_mismatch")
        self.shutdown.request.assert_not_called()

    def add_counter(self, name):
        from market_radar.quality import daily_summary
        from market_radar.research.validator import revalidate_session
        c = self.r.controller
        day = self.r.plan["session_date"]
        with closing(sqlite3.connect(c.config.db_path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("INSERT INTO quality_counters VALUES(?,?,1)", (day, name))
            db.commit()
            summary = daily_summary(SimpleNamespace(db=db, schema_version=4), day)
        folder = c.config.data_dir/"export"/day
        atomic_json(folder/"quality_summary.json", summary)
        revalidate_session(dict(source_db_path=str(c.config.db_path), session_date_et=day, export_path=str(folder)))

    def test_ws_instability_blocks_shutdown(self):
        self.prepare()
        self.add_counter("ws_gap_count")
        self.r.tick()
        self.assertIn("ws_gap_count", self.r.summary["failures"])
        self.shutdown.request.assert_not_called()

    def test_db_error_counter_blocks_shutdown(self):
        self.prepare()
        self.add_counter("db_error_count")
        self.r.tick()
        self.assertIn("db_error_count", self.r.summary["failures"])
        self.shutdown.request.assert_not_called()

    def change_opening_cache(self, empty=False):
        from market_radar.opening_hour.service import analyze_session
        c = self.r.controller
        with closing(sqlite3.connect(c.config.cache_path)) as db:
            if empty:
                db.execute("DELETE FROM minute_bars")
            else:
                db.execute("DELETE FROM minute_bars WHERE rowid IN (SELECT rowid FROM minute_bars LIMIT 1)")
            db.commit()
        c.opening_hour_result = analyze_session(c.config.data_dir, day=self.r.plan["session_date"],
            cache_path=c.config.cache_path, paper_paths=(c.paper_path, c.paper_review_path), output_db=c.opening_hour_path)

    def test_partial_minutes_preserved_as_coverage(self):
        self.prepare()
        self.change_opening_cache()
        self.r.tick()
        self.assertEqual(self.r.summary["first_60m_completeness"]["status"], "PARTIAL")
        self.assertEqual(self.r.summary["first_60m_completeness"]["missing_minute_count"], 1)
        self.shutdown.request.assert_called_once()

    def test_no_observed_minutes_blocks_shutdown(self):
        self.prepare()
        self.change_opening_cache(empty=True)
        self.r.tick()
        self.assertEqual(self.r.summary["first_60m_completeness"]["status"], "EMPTY")
        self.assertIn("opening_hour_empty", self.r.summary["failures"])
        self.shutdown.request.assert_not_called()


class SystemTests(unittest.TestCase):
    def test_shutdown_command_once_and_cancel(self):
        execute = Mock(return_value=SimpleNamespace(returncode=0))
        shutdown = WindowsShutdown(execute)
        shutdown.request()
        self.assertEqual(execute.call_args.args[0][1:], ["/s", "/f", "/t", "60"])
        self.assertTrue(Path(execute.call_args.args[0][0]).is_absolute())
        with self.assertRaises(RunBlocked):
            shutdown.request()
        shutdown.cancel()
        self.assertEqual(execute.call_args.args[0][1:], ["/a"])
        self.assertEqual(execute.call_count, 2)

    def test_shutdown_nonzero_raises(self):
        execute = Mock(return_value=SimpleNamespace(returncode=5))
        shutdown = WindowsShutdown(execute)
        with self.assertRaises(RunBlocked):
            shutdown.request()
        self.assertFalse(shutdown.requested)

    def test_duplicate_global_mutex(self):
        first, second = InstanceGuard(), InstanceGuard()
        with patch.object(first, "check_legacy"), patch("market_radar.providers.kiwoom_lock.CollectorLock"):
            first.acquire()
        try:
            with self.assertRaisesRegex(RunBlocked, "another_one_hour_runner"):
                second.acquire()
        finally:
            first.close(); second.close()

    def test_existing_legacy_exe_blocked(self):
        process = SimpleNamespace(pid=-1, name=lambda: "StockHelper.exe")
        with self.assertRaisesRegex(RunBlocked, "existing_stock_helper_process"):
            InstanceGuard(processes=lambda: [process]).check_legacy()

    def test_existing_legacy_python_blocked(self):
        process = SimpleNamespace(pid=-1, name=lambda: "python.exe", cmdline=lambda: ["python", "-m", "market_radar"])
        with self.assertRaisesRegex(RunBlocked, "existing_collector_process"):
            InstanceGuard(processes=lambda: [process]).check_legacy()

    def test_safe_logs_suppress_credential_and_exception(self):
        previous = install_safe_logging()
        try:
            logger = logging.Logger("synthetic")
            record = logger.makeRecord("synthetic", 40, "", 1, "access_token=%s", ("SYNTHETIC",),
                                       (ValueError, ValueError("SYNTHETIC"), None))
            self.assertNotIn("SYNTHETIC", record.getMessage())
            self.assertIsNone(record.exc_info)
        finally:
            logging.setLogRecordFactory(previous)

    def test_original_strategy_fixtures_and_code_unchanged(self):
        paths = ["tests/fixtures/paper_baseline_v1.jsonl", "tests/fixtures/paper_rotation_v2.jsonl",
                 "market_radar/paper/engine.py", "market_radar/paper/policy.py",
                 "market_radar/paper_rotation_v2/engine.py", "market_radar/paper_rotation_v2/policy.py",
                 "market_radar/controller.py", "market_radar/scheduling.py", "market_radar/runtime.py"]
        for name in paths:
            expected = subprocess.check_output(["git", "show", "6895584:"+name], cwd=ROOT)
            # Git checkout may use CRLF; source semantics and fixture bytes otherwise identical.
            actual = (ROOT/name).read_bytes().replace(b"\r\n", b"\n")
            if name in {"market_radar/paper/engine.py", "market_radar/paper_rotation_v2/engine.py"}:
                # v2.6.6 authorizes only same-publication row reuse. Normalize
                # these exact additions; freeze all strategy bytes as before.
                shared_rows = [
                    (b"def interests(self, *, entries=None):", b"def interests(self):"),
                    (b"def view(self, *, entries=None):", b"def view(self):"),
                ]
                shared_rows += ([
                    (b'(self._entries() if entries is None else entries)', b'self._entries()'),
                    (b'entries = self._entries() if entries is None else entries', b'entries = self._entries()'),
                ] if name == "market_radar/paper/engine.py" else [
                    (b'super().interests(entries=entries)', b'super().interests()'),
                    (b'super().view(entries=entries)', b'super().view()'),
                ])
                for current, original in shared_rows:
                    self.assertEqual(actual.count(current), 1, (name, current))
                    actual = actual.replace(current, original)
            if name == 'market_radar/scheduling.py':
                # Observe the original gate's monotonic instant only. All existing
                # calendar, eligibility, compliance and stop bytes remain frozen.
                actual = actual.replace(b'        self.started_mono = None  # Observed gate opening; no reconstructed timestamp.\n', b'')
                actual = actual.replace(b'                    self.started_mono = mono\n', b'')
            if name == "market_radar/paper/engine.py":
                # Authorized read-only view optimization. Match its exact body;
                # every strategy/state-transition byte remains frozen below.
                optimized = b'''    def view(self):
        # One read per view, also when _state persists it during a tick. Keep the
        # snapshot local: later ticks/transactions must always see fresh rows.
        entries = self._entries()
        return dict(status=self.status, strategy=STRATEGY_VERSION, trades=len(entries),
                    active=dict(self.active) if self.active else None, evidence=dict(self.latest_row) if self.latest_row else None,
                    recent=entries[-10:],
                    summary=self.store.summary(self.session.date, entries=entries) if self.session else {}, actual_order_calls=0)
'''
                expected = expected.replace(b"\r\n", b"\n")
                self.assertTrue(actual.endswith(optimized))
                actual = actual[:-len(optimized)] + expected[expected.index(b"    def view(self):"):]
            if name == "market_radar/controller.py":
                # v2.6.3 authorizes only opt-in diagnostics wiring here. Remove
                # those exact additions, then compare EVERY remaining byte.
                actual = actual.replace(b"        self.paper_telemetry_path = None\n", b"")
                actual = actual.replace(b"        self.operational_diagnostics = None\n", b"")
                actual = actual.replace(b'''            if self.operational_diagnostics is not None:
                from .run_diagnostics import call_path, worker_state
                self.operational_diagnostics.record('controller_stop', self.session_phase,
                    caller=call_path(), workers=worker_state(self))
''', b'')
                actual = actual.replace(b'''            if self.operational_diagnostics is not None:
                from .failure_evidence import exception_evidence
                self.operational_diagnostics.record('controller_exception', self.session_phase,
                    error=True, exception=exception_evidence(exc, 'controller'))
''', b'')
                actual = actual.replace(b"rotation_enabled=self.paper_rotation_enabled,\n                    telemetry_path=self.paper_telemetry_path)",
                                        b"rotation_enabled=self.paper_rotation_enabled)")
                # 2026-10-16 permits the post-session Opening diagnostic handler.
                # Its failure isolation is tested functionally. All other Controller
                # methods, strategy/fixtures and scheduler bytes remain protected.
                def without_opening_handler(data):
                    start = data.index(b'    def _analyze_opening_hour(self):')
                    end = data.index(b'    def _persist_final_protocol(self):', start)
                    return data[:start]+data[end:]
                actual = without_opening_handler(actual)
                expected = without_opening_handler(expected.replace(b'\r\n', b'\n'))
            self.assertEqual(actual, expected.replace(b"\r\n", b"\n"), name)


class PublicControllerTests(unittest.TestCase):
    def test_real_controller_preopen_no_research_and_enabled_modules(self):
        from tests.market_radar_schedule_test import SyntheticLive
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clock = Clock("2026-09-28T13:29:58+00:00")
            provider = SyntheticLive(clock)
            provider.ws_factory = Mock(side_effect=AssertionError("synthetic_provider_has_no_ws"))
            controller = CollectorController(root/"master.db", clock=clock)
            controller.paper_path = root/"paper/paper.db"
            controller.paper_review_path = root/"rotation/review.db"
            controller.opening_hour_path = root/"opening/opening.db"
            with patch.object(socket.socket, "connect", side_effect=AssertionError("network_forbidden")) as network:
                controller.start(runner_config(root/"radar", root/"cache.db"), provider,
                                 fresh_directory=False, paper_enabled=True, paper_rotation_enabled=True, opening_hour_enabled=True)
                try:
                    deadline = time.monotonic()+8
                    while controller.session_phase != "PREOPEN_WAIT" and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(controller.session_phase, "PREOPEN_WAIT")
                    self.assertFalse(provider.fetched.is_set())
                    with closing(sqlite3.connect(controller.config.db_path)) as db:
                        for table in ("radar_snapshots", "radar_candidates", "radar_events"):
                            self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
                    self.assertTrue(controller.paper_enabled and controller.paper_rotation_enabled and controller.opening_hour_enabled)
                    self.assertEqual(controller.config.collection_mode, "Regular Session")
                    clock.advance(2)
                    deadline = time.monotonic()+8
                    while not provider.fetched.is_set() and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertTrue(provider.fetched.is_set())
                    # SyntheticLive sets fetched before returning its batch. A
                    # stop at that point can correctly close the research gate
                    # before any session rows exist. Wait for the evidence this
                    # Opening test requires, as the schedule integration does.
                    count = 0
                    deadline = time.monotonic()+8
                    while not count and time.monotonic() < deadline:
                        with closing(sqlite3.connect(controller.config.db_path)) as db:
                            count = db.execute("SELECT COUNT(*) FROM radar_snapshots").fetchone()[0]
                        if not count:
                            time.sleep(.01)
                    self.assertGreater(count, 0)
                finally:
                    controller.stop()
                    controller.thread.join()
                self.assertFalse(controller.busy)
                self.assertTrue(provider.closed)
                self.assertTrue(threads_finished(controller))
                self.assertIn(controller.opening_hour_result["status"], {"EMPTY", "PARTIAL", "COMPLETE"})
                network.assert_not_called()


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_labels_follow_real_state_and_no_shutdown(self):
        from tools.one_hour_runner.gui import StatusWindow
        r = Runner(Path("unused"), no_shutdown=True)
        window = StatusWindow(r)
        try:
            for state, word in (("ARMED", "대기"), ("COLLECTING", "실행 중"),
                                ("FINALIZING", "ZIP 생성"), ("ERROR", "문제가")):
                r.state = state; window.refresh()
                self.assertIn(word, window.title.text())
            r.state = "COMPLETE_NO_SHUTDOWN"; r.finalized = True; window.refresh()
            self.assertNotIn("60초 후", window.body.text())
            self.assertIn("비활성화", window.body.text())
        finally:
            r.finalized = True
            window.close()

    def test_close_during_collection_requests_graceful_cancel(self):
        from tools.one_hour_runner.gui import StatusWindow
        r = Runner(Path("unused"))
        r.state = "COLLECTING"
        window = StatusWindow(r)
        window.show()
        window.close()
        self.assertTrue(r.cancel_event.is_set())
        self.assertTrue(window.isVisible())
        r.finalized = True
        window.close()

    def test_shutdown_countdown_uses_monotonic_time(self):
        from tools.one_hour_runner.gui import StatusWindow
        clock = Clock()
        r = Runner(Path("unused"), clock=clock)
        r.state, r.finalized = "SHUTDOWN_PENDING", True
        r.shutdown_at = clock.now()+timedelta(seconds=60)
        r.shutdown_deadline_mono = clock.monotonic()+60
        r.shutdown = Mock()
        window = StatusWindow(r)
        clock.wall += timedelta(hours=1)
        clock.mono += 10
        window.refresh()
        self.assertIn("50초 후", window.body.text())
        r.state = "COMPLETE_NO_SHUTDOWN"
        window.close()

    def test_supervisor_and_stop_errors_are_shown_without_killing(self):
        from tools.one_hour_runner.gui import launch
        with tempfile.TemporaryDirectory() as temporary:
            r = Runner(Path(temporary))
            r.folder = Path(temporary)/"run"
            r.summary = dict(run_id="synthetic", session_date="2026-10-12")
            stopped_thread = SimpleNamespace(is_alive=lambda: False)
            r.controller = SimpleNamespace(busy=True, thread=stopped_thread, worker=None, paper=None,
                                           stop=Mock(side_effect=OSError("SYNTHETIC_PRIVATE_EXCEPTION")))
            r.start = Mock()
            r.tick = Mock(side_effect=RuntimeError("SYNTHETIC_PRIVATE_EXCEPTION"))
            result = launch(r, smoke_dir=Path(temporary)/"smoke")
            self.assertEqual(result, 1)
            self.assertEqual(r.state, "ERROR")
            self.assertIn("stop_request_failed", r.failures)
            text = (r.folder/"one_hour_run_summary.json").read_text(encoding="utf-8")
            self.assertNotIn("SYNTHETIC_PRIVATE_EXCEPTION", text)


if __name__ == "__main__":
    unittest.main()
