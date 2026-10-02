"""Offline schedule/collector integration. Fake clocks, temporary DBs, no network."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from market_radar.config import CONFIG
from market_radar.controller import CollectorController
from market_radar.models import instant, utc, MarketBatch
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.providers.kiwoom import KiwoomProvider
from market_radar.scheduling import SessionSchedule, validate_protocol_config, LATE_CAPACITY
from market_radar.research.master_db import MasterDB
from market_radar.research.review_export import create_review_zip
from tests.kiwoom_provider_test import observation, SESSION


class Clock:
    def __init__(self, now="2026-09-28T13:29:59+00:00"):
        self.wall, self.mono = instant(now), 0
    def now(self): return self.wall
    def monotonic(self): return self.mono
    def advance(self, seconds, monotonic=None):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds if monotonic is None else monotonic


class ScheduleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.calendar = ExchangeCalendar()
    def make(self, at="2026-09-28T13:29:59+00:00", mode="Capacity Test", **kwargs):
        self.clock = Clock(at)
        return SessionSchedule(replace(CONFIG, collection_mode=mode, **kwargs), self.calendar, self.clock)
    def advance(self, schedule, seconds):
        for _ in range(seconds):
            self.clock.advance(1)
            schedule.tick(True)
    def opened(self, mode="Capacity Test"):
        s = self.make(mode=mode)
        self.assertEqual(s.tick(), "prepare")
        s.tick(True)
        self.advance(s, 1)
        return s

    def test_weekend(self):
        self.assertEqual(self.make("2026-10-03T10:00:00+00:00").data["session_date_et"], "2026-10-05")
    def test_holiday(self):
        self.assertEqual(self.make("2026-12-25T10:00:00+00:00").data["session_date_et"], "2026-12-28")
    def test_dst_uses_calendar(self):
        before = self.make("2026-03-06T10:00:00+00:00")
        after = self.make("2026-03-09T10:00:00+00:00")
        self.assertEqual(before.open.hour, 14)
        self.assertEqual(after.open.hour, 13)
        self.assertIn("09:30:00-04:00", after.data["scheduled_open_et"])
    def test_early_close(self):
        s = self.make("2026-11-27T10:00:00+00:00", "Regular Session")
        self.assertEqual(s.stop_at, instant("2026-11-27T18:00:00+00:00"))
    def test_after_close_selects_next_session(self):
        self.assertEqual(self.make("2026-10-02T20:00:00+00:00").data["session_date_et"], "2026-10-05")
    def test_armed_preparing_wait_live(self):
        s = self.make("2026-09-28T12:00:00+00:00")
        self.assertIsNone(s.tick(True))
        self.assertEqual(s.phase, "ARMED")
        self.advance(s, 3600)
        self.assertEqual(s.phase, "PREPARING")
        s.tick(True)
        self.assertEqual(s.phase, "PREOPEN_WAIT")
        self.advance(s, 1800)
        self.assertEqual(s.phase, "REGULAR_LIVE")
        self.assertEqual(s.data["start_delay_ms"], 0)
    def test_capacity_stop_and_compliance(self):
        s = self.opened()
        self.advance(s, 3900)
        self.assertEqual(s.phase, "FINALIZING")
        self.assertEqual(s.data["stop_reason"], "scheduled_capacity_end")
        self.assertEqual(s.data["stop_delay_ms"], 0)
        s.finish(True)
        self.assertTrue(s.data["protocol_compliant"])
    def test_regular_close_stop(self):
        s = self.opened("Regular Session")
        self.advance(s, 23400)
        self.assertEqual(s.data["stop_reason"], "scheduled_close")
        s.finish(True)
        self.assertTrue(s.data["protocol_compliant"])
    def test_late_capacity_rejected_entire_session(self):
        for stamp in ("13:30", "13:37", "15:00"):
            with self.assertRaisesRegex(ValueError, "standardized capacity window"):
                self.make("2026-09-28T" + stamp + ":00+00:00")
    def test_regular_late_actual_start(self):
        s = self.make("2026-09-28T13:37:00+00:00", "Regular Session")
        s.tick(); s.tick(True)
        self.assertTrue(s.data["late_start"])
        self.assertEqual(s.data["start_delay_ms"], 420000)
        self.assertFalse(s.data["protocol_compliant"])
    def test_delayed_auth_records_observed_start(self):
        s = self.make()
        s.tick()
        for _ in range(11):
            self.clock.advance(1); s.tick(False)
        self.assertIsNone(s.data["actual_start"])
        s.tick(True)
        self.assertEqual(s.data["start_delay_ms"], 10000)
        self.assertIn("delayed_start", s.data["protocol_reason"])
    def test_connection_first_ready_at_open_is_not_compliant(self):
        s = self.make()
        s.tick()
        self.clock.advance(1)
        s.tick(True)
        self.assertEqual(s.data["start_delay_ms"], 0)
        self.assertIn("connection_not_ready_before_open", s.data["protocol_reason"])
    def test_sleep_resume_missed_window(self):
        s = self.make()
        s.tick(True)
        self.clock.advance(4000)
        self.assertEqual(s.tick(True), "stop")
        self.assertIsNone(s.data["actual_start"])
        s.finish(True)
        self.assertFalse(s.data["protocol_compliant"])
        self.assertIn("scheduler_gap_or_resume", s.data["protocol_reason"])
    def test_wall_clock_jump_and_reverse_blocks_research(self):
        s = self.opened()
        self.clock.advance(-60, monotonic=1)
        s.tick(True)
        self.assertFalse(s.allows_research())
        self.assertIn("clock_jump", s.data["protocol_reason"])
    def test_user_stop_reason_and_no_overwrite(self):
        s = self.opened()
        s.stop("user_requested"); s.stop("scheduled_capacity_end"); s.finish(True)
        self.assertEqual(s.data["stop_reason"], "user_requested")
        self.assertFalse(s.data["protocol_compliant"])
    def test_manual_immediate(self):
        s = self.make("2026-10-03T12:00:00+00:00", "Manual")
        self.assertTrue(s.allows_research())
        s.tick(True)
        self.assertEqual(s.phase, "REGULAR_LIVE")
        s.stop("user_requested"); s.finish(True)
        self.assertFalse(s.data["protocol_compliant"])
    def test_protocol_config_fixed_policies_cache_separation(self):
        base = replace(CONFIG, data_dir=ROOT/"data/run", historical_cache_path=ROOT/"data/cache.db", collection_mode="Capacity Test")
        validate_protocol_config(base)
        for changes in ({"snapshot_seconds": 10}, {"result_pre_target_grace_seconds": 1},
                        {"rvol_backfill_enabled":False}, {"historical_cache_path":None}):
            with self.assertRaises(ValueError): validate_protocol_config(replace(base, **changes))
    def test_provider_no_preopen_candidates_or_allocation(self):
        s = self.make()
        provider = KiwoomProvider(CONFIG, calendar=self.calendar)
        provider.collection_schedule = s
        provider.rest, provider.ws = Mock(), Mock()
        provider._refresh_universe()
        provider.rest.request.assert_not_called()
        provider.ws.set_symbols.assert_not_called()
        s.tick(); self.advance(s, 1)
        provider.rest.example.return_value = {}
        provider.rest.request.return_value = ({"result_list": []}, None, utc(self.clock.now()))
        provider._refresh_universe()
        self.assertEqual(provider.rest.request.call_count, 4)
        provider.ws.set_symbols.assert_called_once_with({})


class SyntheticLive:
    name, mode = "kiwoom", "live"
    def __init__(self, clock):
        self.clock, self.connected = clock, True
        self.fetched = threading.Event()
        self.closed = False
    def connect(self): pass
    def connection_status(self): return dict(connected=self.connected, active_ws=0)
    def fetch(self, tracked):
        self.fetched.set()
        o = replace(observation(minute=0, seconds=0), asof_utc=utc(self.clock.now()))
        return MarketBatch(o.asof_utc, (o,), (SESSION,))
    def request_stop(self): pass
    def close(self): self.closed = True


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.controller = CollectorController(self.root/"master.db", clock=self.clock)
        self.config = replace(CONFIG, data_dir=self.root/"runs", historical_cache_path=self.root/"cache.db", collection_mode="Capacity Test")
        self.provider = SyntheticLive(self.clock)
    def tearDown(self):
        self.controller.stop()
        if self.controller.thread: self.controller.thread.join(10)
        self.tmp.cleanup()
    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline: time.sleep(.01)
        self.assertTrue(predicate())
    def start(self):
        self.controller.start(self.config, self.provider)
        self.wait_for(lambda: self.controller.session_phase == "PREOPEN_WAIT")
    def test_no_preopen_rows_then_real_detection_and_final_metadata(self):
        self.start()
        self.assertFalse(self.provider.fetched.is_set())
        with closing(sqlite3.connect(self.controller.worker.config.db_path)) as db:
            for table in ("radar_snapshots", "radar_candidates", "radar_events", "radar_results"):
                self.assertEqual(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertFalse(self.controller.config.cache_path.is_relative_to(self.controller.config.data_dir))
        self.clock.advance(1)
        self.wait_for(self.provider.fetched.is_set)
        def has_candidate():
            with closing(sqlite3.connect(self.controller.worker.config.db_path)) as db:
                return db.execute("SELECT COUNT(*) FROM radar_candidates").fetchone()[0] > 0
        self.wait_for(has_candidate)
        self.controller.stop()
        self.controller.thread.join(8)
        self.assertEqual(self.controller.session_phase, "STOPPED")
        p = self.controller.schedule.snapshot()
        self.assertEqual(p["actual_start"], SESSION.open_utc)
        self.assertEqual(p["stop_reason"], "user_requested")
        self.assertFalse(p["protocol_compliant"])
        with closing(sqlite3.connect(self.controller.worker.config.db_path)) as db:
            self.assertEqual(db.execute("SELECT first_detected_utc FROM radar_candidates").fetchone()[0], SESSION.open_utc)
        root = self.controller.config.data_dir
        metadata = json.loads((root/"export"/SESSION.date/"metadata.json").read_text())
        quality = json.loads((root/"export"/SESSION.date/"quality_summary.json").read_text())
        self.assertEqual(metadata["collection_protocol"], p)
        self.assertEqual(quality["collection_protocol"], p)
        session = MasterDB(self.controller.master_path).sessions()[0]
        self.assertEqual(session["collection_mode"], "Capacity Test")
        self.assertEqual(session["actual_start"], SESSION.open_utc)
        with closing(sqlite3.connect(self.controller.master_path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
        with zipfile.ZipFile(create_review_zip(session)) as archive:
            self.assertEqual(json.loads(archive.read("review_manifest.json"))["collection_protocol"], p)
    def test_autostop_interrupts_30_second_snapshot_wait(self):
        self.start()
        self.clock.advance(1)
        self.wait_for(self.provider.fetched.is_set)
        self.clock.advance(3900)
        self.wait_for(lambda: not self.controller.busy)
        self.assertTrue(self.provider.closed)
        self.assertEqual(self.controller.schedule.data["stop_reason"], "scheduled_capacity_end")
        self.assertFalse(self.controller.schedule.data["protocol_compliant"])  # simulated resume
    def test_finalizing_includes_master_import(self):
        entered, release = threading.Event(), threading.Event()
        from market_radar.research.importer import import_session
        def delayed(*args, **kwargs):
            entered.set(); release.wait(5)
            return import_session(*args, **kwargs)
        with patch("market_radar.controller.import_session", side_effect=delayed):
            self.start()
            self.controller.stop()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.controller.session_phase, "FINALIZING")
                self.assertTrue(self.controller.busy)
            finally: release.set()
            self.controller.thread.join(8)
        self.assertEqual(self.controller.session_phase, "STOPPED")
    def test_late_rejection_does_not_create_provider(self):
        self.clock.advance(60)
        self.controller.provider_factory = Mock()
        self.controller.start(self.config)
        self.controller.thread.join(8)
        self.controller.provider_factory.assert_not_called()
        self.assertEqual(self.controller.state, "ERROR")
        self.assertIn(("collector_error", LATE_CAPACITY), list(self.controller.events.queue))
    def test_armed_stop_without_connect(self):
        self.clock.advance(-3600)
        self.controller.provider_factory = Mock()
        self.controller.start(self.config)
        self.wait_for(lambda: self.controller.schedule is not None)
        self.controller.stop(); self.controller.thread.join(8)
        self.controller.provider_factory.assert_not_called()
        self.assertEqual(self.controller.schedule.data["stop_reason"], "user_requested")
        self.assertFalse(self.controller.schedule.data["protocol_compliant"])

    def test_connection_delay_blocks_fetch_and_uses_actual_time(self):
        self.provider.connected = False
        self.controller.start(self.config, self.provider)
        self.wait_for(lambda: self.controller.worker is not None and self.controller.worker.phase == "RUNNING")
        self.clock.advance(11)
        time.sleep(.15)
        self.assertFalse(self.provider.fetched.is_set())
        self.assertIsNone(self.controller.schedule.data["actual_start"])
        self.provider.connected = True
        self.wait_for(self.provider.fetched.is_set)
        self.assertEqual(self.controller.schedule.data["start_delay_ms"], 10000)
        self.assertFalse(self.controller.schedule.data["protocol_compliant"])

    def test_user_stop_during_connect_without_database(self):
        from market_radar.providers.kiwoom_failures import StopRequested
        entered, release = threading.Event(), threading.Event()
        def connecting():
            entered.set(); release.wait(5)
            raise StopRequested("stopped")
        self.provider.connect = connecting
        self.provider.request_stop = release.set
        self.controller.start(self.config, self.provider)
        self.assertTrue(entered.wait(5))
        self.controller.stop(); self.controller.thread.join(8)
        self.assertEqual(self.controller.session_phase, "STOPPED")
        self.assertFalse(self.controller.worker.config.db_path.exists())
        self.assertEqual(self.controller.schedule.data["stop_reason"], "user_requested")
        self.assertFalse(any(kind == "import_failed" for kind, _ in self.controller.events.queue))

    def test_ui_scheduled_phase_and_wait_subscription_health(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from market_radar.ui import RadarPanel
        app = QApplication.instance() or QApplication([])
        panel = RadarPanel(config=self.config, master_path=self.controller.master_path,
                           settings=QSettings(str(self.root/"ui.ini"), QSettings.Format.IniFormat))
        panel.controller = self.controller
        try:
            self.start()
            panel.poll()
            self.assertEqual(panel.collection_mode.count(), 3)
            self.assertIn("PREOPEN_WAIT", panel.schedule_label.text())
            self.assertIn("XNYS", panel.schedule_label.text())
            health = {r["metric"]:r["value"] for r in panel.health.rows}
            self.assertEqual(health["Current Active WS"], 0)
            self.assertTrue(panel.stop_button.isEnabled())
        finally:
            self.controller.stop(); self.controller.thread.join(8)
            deadline = time.monotonic() + 5
            while panel.jobs and time.monotonic() < deadline:
                panel.poll(); app.processEvents(); time.sleep(.01)
            panel.close(); panel.deleteLater(); app.processEvents()


if __name__ == "__main__": unittest.main(verbosity=2)
