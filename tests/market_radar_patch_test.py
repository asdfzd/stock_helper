"""Offline persistent-cache and current/last telemetry regressions."""
from contextlib import closing
import copy
from dataclasses import replace
import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.config import CONFIG, DEFAULT_SHARED_CACHE_PATH
from market_radar.controller import CollectorController
from market_radar.dashboard_widgets import health_rows, HISTORY_COLUMNS
from market_radar.providers.kiwoom import KiwoomProvider
from market_radar.providers.kiwoom_cache import KiwoomCache, POLICY
from market_radar.research.master_db import MasterDB
from market_radar.research.importer import import_session, live_health
from tests.kiwoom_provider_test import raw_bars, SESSION


class FakeWS:
    def __init__(self, *args):
        self.lock = threading.RLock()
        self.connected = False
        self.active = {}
    def start(self): pass
    def close(self): self.connected = False; self.active.clear()


class OfflineKiwoom(KiwoomProvider):
    """Real connect/cache/close, fake REST/WS and no scheduler IO."""
    def __init__(self, config):
        super().__init__(config, rest_factory=lambda *args: Mock(), ws_factory=FakeWS, calendar=object())
    def _io_loop(self): pass
    def fetch(self, tracked=()): raise StopIteration


class PersistentCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache_path = self.root/"canonical/market_radar_cache/kiwoom_v2.db"
    def tearDown(self): self.tmp.cleanup()

    def test_two_dashboard_runs_share_cache_and_keep_distinct_research_dbs(self):
        configs = []
        def factory(config):
            configs.append(config)
            return OfflineKiwoom(config)
        master = self.root/"master.db"
        controller = CollectorController(master, provider_factory=factory)
        with patch("market_radar.controller.DEFAULT_SHARED_CACHE_PATH", self.cache_path), \
                patch("market_radar.providers.kiwoom_lock.APP_ROOT", self.root):
            self.assertFalse(self.cache_path.exists())
            controller.start(replace(CONFIG, data_dir=self.root/"data-a"))
            controller.thread.join(10)
            self.assertEqual(controller.state, "STOPPED")
            cache = KiwoomCache(self.cache_path)
            cache.put_minutes("TEST", "ND", raw_bars(minutes=1), "2026-09-28T14:00:00+00:00")
            cache.close()
            controller.start(replace(CONFIG, data_dir=self.root/"data-b"))
            controller.thread.join(10)
            self.assertEqual(controller.state, "STOPPED")
        self.assertEqual(configs[0].cache_path, configs[1].cache_path)
        self.assertEqual(configs[1].cache_path, self.cache_path)
        self.assertNotEqual(configs[0].db_path, configs[1].db_path)
        self.assertTrue(all(c.db_path.exists() for c in configs))
        self.assertFalse(any((c.data_dir/"market_radar_cache").exists() for c in configs))
        self.assertEqual(controller.master_path, master)
        self.assertEqual(len(MasterDB(master).sessions()), 2)
        cache = KiwoomCache(self.cache_path)
        try: self.assertEqual(len(cache.rows("TEST", "ND", SESSION.date)), 1)
        finally: cache.close()

    def test_explicit_dashboard_cache_override(self):
        controller = CollectorController(self.root/"master.db", provider_factory=OfflineKiwoom)
        with patch("market_radar.providers.kiwoom_lock.APP_ROOT", self.root):
            controller.start(replace(CONFIG, data_dir=self.root/"session", historical_cache_path=self.cache_path))
            controller.thread.join(10)
        self.assertEqual(controller.state, "STOPPED")
        self.assertEqual(controller.config.cache_path, self.cache_path)

    def test_cli_legacy_data_dir_still_controls_cache(self):
        config = replace(CONFIG, data_dir=self.root/"legacy-cli")
        self.assertIsNone(config.historical_cache_path)
        with patch("market_radar.providers.kiwoom_lock.APP_ROOT", self.root):
            provider = OfflineKiwoom(config)
            try:
                provider.connect()
                self.assertTrue((config.data_dir/"market_radar_cache/kiwoom_v2.db").exists())
            finally: provider.close()

    def test_cli_explicit_cache_option_and_default(self):
        from market_radar import __main__ as cli
        for extra, expected in (([], None), (["--cache-path", str(self.cache_path)], self.cache_path)):
            captured = []
            def worker(provider, config, **kwargs):
                captured.append(config)
                return Mock(error=None, config=config)
            with patch.object(sys, "argv", ["market_radar", "--collect", "--data-dir", str(self.root/"cli"), *extra]), \
                    patch.object(cli, "create_live_provider"), patch.object(cli, "RadarWorker", side_effect=worker), \
                    patch.object(cli, "collect_until_stopped"), patch("builtins.print"):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(captured[0].historical_cache_path, expected)

    def test_all_cache_contract_mismatches_rejected_without_mutation(self):
        correct = {"provider":"kiwoom", "mode":"real", "schema":1, "bar_policy":POLICY}
        for field in correct:
            with self.subTest(field=field):
                path = self.root/f"bad-{field}.db"
                identity = dict(correct, **{field:"incompatible"})
                with closing(sqlite3.connect(path)) as db:
                    db.execute("CREATE TABLE cache_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                    db.execute("INSERT INTO cache_metadata VALUES('identity',?)", (json.dumps(identity, sort_keys=True),))
                    db.commit()
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, "identity mismatch"): KiwoomCache(path)
                self.assertEqual(before, path.read_bytes())
                self.assertFalse(Path(str(path)+"-wal").exists())

    def test_shared_incomplete_coverage_not_promoted(self):
        cache = KiwoomCache(self.cache_path)
        cache.put_minutes("TEST", "ND", raw_bars(minutes=389), "2026-09-29T00:00:00+00:00")
        self.assertFalse(cache.mark_complete("TEST", "ND", SESSION, "2026-09-29T00:00:00+00:00"))
        cache.close()
        cache = KiwoomCache(self.cache_path)
        try:
            self.assertFalse(cache.is_complete("TEST", "ND", SESSION.date))
            self.assertEqual(len(cache.rows("TEST", "ND", SESSION.date)), 389)
        finally: cache.close()


class HealthStateTests(unittest.TestCase):
    def setUp(self):
        self.summary = {"tracking":{"active_subscription_count":100, "protected_count":82,
            "discovery_count":77, "tracking_slot_unavailable_count":15, "slot_saturation_pct":100.0}}
    def values(self, state, connection):
        return {r["metric"]:r["value"] for r in health_rows(self.summary, 100, state=state, connection=connection)}

    def test_stopped_current_zero_last_preserved(self):
        before = copy.deepcopy(self.summary)
        values = self.values("STOPPED", {"connected":True,"active_ws":100})
        self.assertEqual(values["Current Active WS"], 0)
        self.assertEqual(values["Current Connected"], "No")
        self.assertEqual(values["Last Active WS"], 100)
        self.assertEqual(values["Last Protected"], 82)
        self.assertEqual(values["Last Slot saturation %"], 100)
        self.assertEqual(self.summary, before)

    def test_running_current_uses_ack_state_and_latest_batch(self):
        v = self.values("RUNNING", {"connected":True,"active_ws":95})
        self.assertEqual(v["Current Connected"], "Yes")
        self.assertEqual(v["Current Active WS"], 95)
        self.assertEqual(v["Current Protected"], 82)
        self.assertEqual(v["Current Discovery"], 77)
        self.assertEqual(v["Current Slot unavailable"], 15)
        self.assertEqual(v["Current Saturation %"], 95)
        self.assertEqual(v["Last Slot saturation %"], 100)

    def test_disconnected_and_stopping_not_assumed_clean(self):
        v = self.values("RUNNING", {"connected":False,"active_ws":100})
        self.assertEqual(v["Current Active WS"], 0)
        self.assertEqual(self.values("STOPPING", {"connected":True,"active_ws":3})["Current Active WS"], 3)
        self.assertIsNone(self.values("ERROR", {})["Current Connected"])

    def test_provider_status_reads_live_ws_without_io(self):
        provider = KiwoomProvider(CONFIG)
        self.assertEqual(provider.connection_status(), {"connected":False,"active_ws":0})
        provider.ws = FakeWS()
        provider.ws.connected = True
        provider.ws.active = {"TEST":"ND"}
        self.assertEqual(provider.connection_status(), {"connected":True,"active_ws":1})
        provider.ws.connected = False
        self.assertEqual(provider.connection_status(), {"connected":False,"active_ws":0})

    def test_capacity_columns_visible_before_details(self):
        self.assertEqual({key for key, _ in HISTORY_COLUMNS[:9]}, {"session_date_et", "max_ws_symbols", "candidate_count",
            "snapshot_count", "max_slot_saturation_pct", "slot_unavailable_count", "rest_error_count", "rvol_available_pct", "clean_shutdown"})

    def test_ui_stopped_semantics_cache_tooltip_and_clean_message(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from market_radar.ui import RadarPanel
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            panel = RadarPanel(master_path=root/"master.db", settings=QSettings(str(root/"ui.ini"), QSettings.Format.IniFormat))
            panel._summary = copy.deepcopy(self.summary)
            panel.controller.lifecycle = {"clean_shutdown":True,"shutdown_elapsed_ms":422}
            panel.poll()
            values = {r["metric"]:r["value"] for r in panel.health.rows}
            self.assertEqual(values["Current Active WS"], 0)
            self.assertEqual(values["Last Active WS"], 100)
            self.assertIn("Stopped cleanly", panel.shutdown_label.text())
            self.assertIn("422", panel.shutdown_label.text())
            self.assertEqual(panel.cache_label.toolTip(), str(DEFAULT_SHARED_CACHE_PATH.resolve()))
            panel.import_done([])
            panel.poll()
            self.assertIn("Stopped cleanly", panel.shutdown_label.text())
            deadline=time.monotonic()+3
            while panel.jobs and time.monotonic()<deadline:
                panel.poll(); app.processEvents(); time.sleep(.01)
            panel.close(); panel.deleteLater(); app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
