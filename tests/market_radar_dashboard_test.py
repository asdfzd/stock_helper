"""Offline dashboard/master/review coverage; synthetic provider and temporary files."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.config import CONFIG
from market_radar.controller import CollectorController
from market_radar.dashboard_widgets import live_rows, display, health_rows
from market_radar.engine import RadarEngine
from market_radar.models import MarketBatch, utc, instant
from market_radar.providers.kiwoom_lock import CollectorLock
from market_radar.provider import ProviderError, JsonlReplayProvider
from market_radar.research.importer import import_session, open_source, live_health
from market_radar.research.master_db import MasterDB, MASTER_SCHEMA_VERSION
from market_radar.research.review_export import create_review_zip
from market_radar.storage import RadarStore
from tests.kiwoom_provider_test import observation, SESSION


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = replace(CONFIG, data_dir=self.root/"source", max_ws_symbols=50)
        self.master = MasterDB(self.root/"master.db")
        store = RadarStore(self.config.db_path, {"provider":"kiwoom", "mode":"live",
            "test_version": CONFIG.test_version, "max_ws_symbols":50})
        engine = RadarEngine(store, self.config, "kiwoom", "live")
        o = observation()
        metadata = {"tracking_counts": {"active_subscription_count":50, "slot_saturation_pct":100,
            "protected_count":1, "discovery_count":70, "tracking_slot_unavailable_count":20}}
        engine.process(MarketBatch(o.asof_utc, (o,), (SESSION,), metadata=metadata), o.asof_utc)
        later = replace(o, asof_utc=utc(instant(o.asof_utc)+timedelta(seconds=30)), price=202)
        engine.process(MarketBatch(later.asof_utc, (later,), (SESSION,), metadata=metadata), later.asof_utc)
        store.export_all(self.config.data_dir/"export")
        store.close()

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return import_session(self.config.db_path, self.master.path)

    def session(self):
        self.load()
        return self.master.sessions()[0]

    def test_schema_and_indexes(self):
        with closing(self.master.connect()) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], MASTER_SCHEMA_VERSION)
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({"sessions_date", "candidates_ticker", "results_ticker", "results_horizon"} <= names)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='radar_snapshots'").fetchone())

    def test_future_schema_rejected_unchanged(self):
        with closing(self.master.connect()) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError): self.master.connect()
        with closing(sqlite3.connect(self.master.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_candidate_initial_values_and_result_audit(self):
        session = self.session()
        detail = self.master.details(session["id"])
        c = detail["candidates"][0]
        self.assertEqual((c["ticker"], c["initial_price"], c["first_current_rank"]), ("TEST", 200, 1))
        r = next(r for r in detail["results"] if r["horizon"] == "30s")
        self.assertEqual(r["status"], "complete")
        self.assertEqual(r["sample_selection"], "post_target")
        self.assertEqual(r["result_price"], 202)
        self.assertAlmostEqual(r["return_pct"], 1)

    def test_quality_capacity_and_unknown_shutdown(self):
        s = self.session()
        self.assertEqual((s["max_ws_symbols"], s["max_slot_saturation_pct"], s["snapshot_count"]), (50, 100, 2))
        self.assertIsNone(s["clean_shutdown"])
        self.assertEqual(s["timing_basis"], "observed_snapshots")

    def test_duplicate_updates_without_duplicate_candidates(self):
        first, second = self.load(), self.load()
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.assertTrue(second[0]["updated"])
        self.assertEqual(len(self.master.sessions()), 1)
        self.assertEqual(len(self.master.details(first[0]["id"])["candidates"]), 1)

    def test_copied_source_identity(self):
        self.load()
        copied = self.root/"copy.db"
        shutil.copyfile(self.config.db_path, copied)
        result = import_session(copied, self.master.path)
        self.assertTrue(result[0]["updated"])
        self.assertEqual(len(self.master.sessions()), 1)

    def test_reimport_refreshes_pending_results(self):
        self.load()
        with closing(sqlite3.connect(self.config.db_path)) as db:
            db.execute("UPDATE radar_results SET status='provisional',price=210,return_pct=5 WHERE horizon='EOD'")
            db.commit()
        self.load()
        result = next(r for r in self.master.ticker_history("TEST") if r["horizon"] == "EOD")
        self.assertEqual((result["status"], result["return_pct"]), ("provisional", 5))

    def test_source_bytes_unchanged_and_query_only(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.config.data_dir.rglob("*") if p.is_file()}
        self.load()
        with closing(open_source(self.config.db_path)) as db:
            with self.assertRaises(sqlite3.OperationalError): db.execute("DELETE FROM radar_results")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})

    def test_failed_import_rolls_back(self):
        self.load()
        with patch("market_radar.research.importer.insert", side_effect=ValueError("fixture")):
            with self.assertRaises(ValueError): self.load()
        self.assertEqual(self.master.sessions()[0]["candidate_count"], 1)
        self.assertEqual(len(self.master.details(1)["candidates"]), 1)

    def test_multiple_dates_and_missing_initial_snapshot(self):
        with closing(sqlite3.connect(self.config.db_path)) as db:
            db.execute("INSERT INTO radar_candidates(session_date,ticker,first_detected_utc,first_detected_et,first_price,session_close_utc,test_version) "
                "VALUES(?,?,?,?,?,?,?)", ("2026-09-29", "NEXT", "2026-09-29T14:00:00+00:00", "2026-09-29T10:00:00-04:00", 10,
                "2026-09-29T20:00:00+00:00", CONFIG.test_version))
            db.commit()
        rows = self.load()
        self.assertEqual(len(rows), 2)
        next_day = self.master.sessions()[0]
        c = self.master.details(next_day["id"])["candidates"][0]
        self.assertIsNone(c["initial_rvol"])
        self.assertIsNone(c["initial_day_change_pct"])
        self.assertIsNone(next_day["rvol_available_pct"])

    def test_concurrent_initial_master_migration(self):
        from concurrent.futures import ThreadPoolExecutor
        def connect():
            with closing(self.master.connect()) as db:
                return db.execute("PRAGMA user_version").fetchone()[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _: connect(), range(2))), [1, 1])

    def test_history_and_ticker_queries(self):
        self.load()
        self.assertEqual(len(self.master.sessions(limit=1)), 1)
        self.assertEqual(self.master.sessions(offset=1), [])
        rows = self.master.ticker_history("test")
        self.assertEqual({r["horizon"] for r in rows}, {"30s", "1m", "3m", "5m", "10m", "30m", "60m", "EOD", "D+1", "D+2", "D+3"})
        self.assertEqual(self.master.ticker_history("' OR 1=1 --"), [])

    def test_empty_source_directory(self):
        with self.assertRaises(ValueError): import_session(self.root/"missing", self.master.path)

    def test_zip_allowlist_manifest_and_hashes(self):
        session = self.session()
        root = self.config.data_dir
        (root/".env").write_text("fixture-only-secret")
        (root/"export"/SESSION.date/"private.txt").write_text("fixture-only-secret")
        archive = create_review_zip(session)
        with zipfile.ZipFile(archive) as z:
            self.assertNotIn(".env", z.namelist())
            self.assertFalse(any(n.endswith(".db") or n.endswith(".txt") for n in z.namelist()))
            manifest = json.loads(z.read("review_manifest.json"))
            self.assertEqual(manifest["max_ws_symbols"], 50)
            self.assertEqual(manifest["schema_version"], 4)
            for item in manifest["included_files"]:
                self.assertEqual(item["sha256"], hashlib.sha256(z.read(item["name"])).hexdigest())

    def test_zip_missing_optional_files(self):
        session = self.session()
        (Path(session["export_path"])/"events.csv").unlink()
        with zipfile.ZipFile(create_review_zip(session)) as z:
            manifest = json.loads(z.read("review_manifest.json"))
            self.assertIn("events.csv", manifest["missing_files"])
            self.assertIn("session.log", manifest["missing_files"])

    def test_zip_rejects_sensitive_artifact_and_leaves_no_zip(self):
        session = self.session()
        for text in ('APP_SECRET=synthetic', 'Authorization: Bearer synthetic', 'token=synthetic', 'account_number=synthetic'):
            (Path(session["export_path"])/"metadata.json").write_text(text)
            with self.assertRaises(ValueError): create_review_zip(session)
            self.assertEqual(list((self.config.data_dir/"reviews").iterdir()), [])

    def test_zip_rejects_external_export_path(self):
        session = self.session()
        session["export_path"] = str(self.root)
        with self.assertRaises(ValueError): create_review_zip(session)

    def test_zip_failure_preserves_previous_archive(self):
        session = self.session()
        path = create_review_zip(session)
        before = path.read_bytes()
        (Path(session["export_path"])/"metadata.json").write_text('secret=synthetic')
        with self.assertRaises(ValueError): create_review_zip(session)
        self.assertEqual(path.read_bytes(), before)

    def test_live_health_read_thread_contract_and_null_display(self):
        data = live_health(self.config.db_path)
        self.assertIn("summary", data)
        self.assertEqual(display(None), "-")
        rows = live_rows([dict(ticker="NULL", price=1, current_leader_rank=1, rvol=None, rvol_status="warming")], "current_leader_rank")
        self.assertIsNone(rows[0]["rvol"])
        self.assertIn("warming", rows[0]["data_status"])
        health = {r["metric"]:r["value"] for r in health_rows({}, 50)}
        self.assertEqual(health["Max WS"], 50)
        self.assertEqual(health["Current Active WS"], 0)

    def test_stopped_health_preserves_source_and_master_capacity(self):
        session = self.session()
        paths = (self.config.db_path, self.master.path)
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        data = live_health(self.config.db_path, latest_session=True)
        health = {r["metric"]:r["value"] for r in health_rows(data["summary"], 50, state="STOPPED")}
        self.assertEqual(data["day"], SESSION.date)
        self.assertEqual(health["Current Active WS"], 0)
        self.assertEqual(health["Last Active WS"], 50)
        self.assertEqual(health["Last Slot saturation %"], 100)
        self.assertEqual(self.master.sessions()[0]["max_slot_saturation_pct"], session["max_slot_saturation_pct"])
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])


class SlowProvider:
    name, mode = "fixture", "replay"
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.close_thread = None
    def connect(self): pass
    def fetch(self, tracked):
        self.entered.set()
        self.release.wait(2)
        raise StopIteration
    def request_stop(self): self.release.set()
    def close(self): self.close_thread = threading.get_ident()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = replace(CONFIG, data_dir=self.root/"session")
        self.controller = CollectorController(self.root/"master.db")
    def tearDown(self):
        self.controller.stop()
        if self.controller.thread: self.controller.thread.join(10)
        self.tmp.cleanup()

    def test_start_stop_idempotent_off_thread(self):
        p = SlowProvider()
        self.assertTrue(self.controller.start(self.config, p))
        self.assertTrue(p.entered.wait(5))
        self.assertFalse(self.controller.start(self.config, p))
        self.controller.stop()
        self.assertEqual(self.controller.state, "STOPPING")
        self.controller.stop()
        self.controller.thread.join(5)
        self.assertEqual(self.controller.state, "STOPPED")
        self.assertNotEqual(p.close_thread, threading.get_ident())
        self.assertTrue(self.controller.lifecycle["clean_shutdown"])

    def test_stop_during_factory_startup(self):
        gate = threading.Event()
        self.controller.provider_factory = lambda config: (gate.wait(2), SlowProvider())[1]
        self.controller.start(self.config)
        self.controller.stop()
        gate.set()
        self.controller.thread.join(5)
        self.assertEqual(self.controller.state, "STOPPED")

    def test_import_failure_does_not_fail_collector(self):
        p = SlowProvider()
        with patch("market_radar.controller.import_session", side_effect=OSError("fixture")):
            self.controller.start(self.config, p)
            p.entered.wait(5)
            self.controller.stop()
            self.controller.thread.join(5)
        self.assertEqual(self.controller.state, "STOPPED")
        self.assertTrue(any(kind == "import_failed" for kind, value in self.controller.events.queue))

    def test_real_lock_conflict(self):
        class LockedProvider(SlowProvider):
            name, mode = "kiwoom", "live"
            def __init__(self): super().__init__(); self.lock = CollectorLock()
            def connect(self): self.lock.acquire()
            def close(self): self.lock.close()
        with patch("market_radar.providers.kiwoom_lock.APP_ROOT", self.root):
            lock = CollectorLock()
            lock.acquire()
            try:
                self.controller.start(self.config, LockedProvider())
                self.controller.thread.join(5)
                self.assertEqual(self.controller.state, "ERROR")
                self.assertIn("Another local Kiwoom collector", self.controller.worker.error)
            finally: lock.close()

    def test_cli_replay_unchanged(self):
        result = subprocess.run([sys.executable, "-m", "market_radar", "--replay",
            "tests/fixtures/market_radar_replay.jsonl", "--fast", "--data-dir", str(self.root/"cli")],
            cwd=ROOT, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue((self.root/"cli/replay/stock_helper_market.db").is_file())

    def test_final_export_precedes_automatic_import(self):
        config = replace(self.config, snapshot_seconds=.001)
        self.controller.start(config, JsonlReplayProvider(ROOT/"tests/fixtures/market_radar_replay.jsonl"))
        self.controller.thread.join(10)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.state, "STOPPED")
        rows = MasterDB(self.controller.master_path).sessions()
        self.assertTrue(rows)
        self.assertTrue(all(r["clean_shutdown"] for r in rows))
        self.assertTrue(all((Path(r["export_path"])/"results.csv").is_file() for r in rows))

    def test_ui_service_job_and_collect_do_not_block_timer(self):
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication
        from market_radar.ui import RadarPanel
        app = QApplication.instance() or QApplication([])
        panel = RadarPanel(config=self.config, master_path=self.root/"ui-master.db",
            settings=QSettings(str(self.root/"ui.ini"), QSettings.Format.IniFormat))
        ticks, worker_threads, selected = [], [], []
        panel.radar_ticker_selected.connect(selected.append)
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start(10)
        def slow():
            worker_threads.append(threading.get_ident())
            time.sleep(.25)
        panel.submit("slow", slow, lambda value: None)
        panel.start(SlowProvider())
        deadline = time.monotonic()+.4
        while time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        self.assertGreater(len(ticks), 10)
        self.assertNotEqual(worker_threads[0], threading.get_ident())
        panel.render([dict(ticker="TEST", price=1, current_leader_rank=1, emerging_rank=1, rvol=None, rvol_status="warming")])
        panel.select_ticker(panel.tables[0], 0)
        self.assertEqual(selected, ["TEST"])
        panel.stop()
        deadline = time.monotonic()+5
        while (panel.controller.busy or panel.jobs) and time.monotonic()<deadline:
            app.processEvents(); panel.poll(); time.sleep(.01)
        timer.stop()
        panel.close()
        self.assertTrue(panel.closed)
        panel.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
