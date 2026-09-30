"""Handoff audit: synthetic fixtures stay in tests and temporary databases."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.calculations import calculate, rank
from market_radar.config import CONFIG, RadarConfig
from market_radar.engine import HORIZONS, RadarEngine
from market_radar.models import Bar, DailyClose, HistoricalVolume, instant, utc
from market_radar.provider import UnconfiguredProvider, create_live_provider
from market_radar.runtime import RadarWorker
from market_radar.storage import RadarStore
from tests.market_radar_test import CALENDAR, SESSION, START, batch, observation


class AuditCalculationTests(unittest.TestCase):
    def test_hand_calculated_vwap(self):
        bars = (
            Bar(utc(START), utc(START + timedelta(seconds=30)), 10, 10, 10, 100, 1000),
            Bar(utc(START + timedelta(seconds=30)), utc(START + timedelta(seconds=60)), 12, 12, 12, 300, 3600),
        )
        row = calculate(replace(observation(seconds=60), price=12, bars=bars), CONFIG)
        self.assertEqual(row["vwap"], 11.5)  # (100*10 + 300*12) / 400
        self.assertEqual(row["volume"], 400)
        self.assertAlmostEqual(row["price_vs_vwap_pct"], 100 / 23)

    def test_zero_volume_and_empty_session(self):
        o = observation(seconds=60)
        o = replace(o, bars=tuple(replace(b, volume=0, notional=0) for b in o.bars))
        row = calculate(o, CONFIG)
        self.assertEqual(row["volume"], 0)
        for field in ("vwap", "price_vs_vwap_pct", "above_vwap", "rvol"):
            self.assertIsNone(row[field])
        self.assertIsNone(calculate(replace(o, bars=()), CONFIG)["volume"])

    def test_session_reset_vwap_hod(self):
        calculate(observation(), CONFIG)
        o = observation(seconds=30, session=CALENDAR[1], price=20)
        o = replace(o, bars=(replace(o.bars[0], high=20, low=20, close=20, notional=2000),))
        row = calculate(o, CONFIG)
        self.assertEqual(row["vwap"], 20)
        self.assertEqual(row["hod"], 20)
        self.assertIsNone(row["return_1m"])
        self.assertEqual(row["session_date"], "2026-09-28")

    def test_opening_range_boundaries(self):
        self.assertIsNone(calculate(observation(seconds=30), CONFIG)["opening_1m_high"])
        self.assertIsNone(calculate(observation(seconds=270), CONFIG)["opening_5m_high"])
        completed = calculate(observation(seconds=300), CONFIG)
        later = calculate(observation(seconds=360), CONFIG)
        self.assertAlmostEqual(completed["opening_1m_high"], 10.2)
        self.assertAlmostEqual(completed["opening_5m_high"], 10.6)
        self.assertAlmostEqual(completed["opening_5m_low"], 9.95)
        for field in ("opening_1m_high", "opening_1m_low", "opening_5m_high", "opening_5m_low"):
            self.assertEqual(completed[field], later[field])

    def test_hod_distance(self):
        row = calculate(observation(seconds=60, price=10), CONFIG)
        self.assertAlmostEqual(row["hod"], 10.2)
        self.assertAlmostEqual(row["hod_distance_pct"], -100 / 51)

    def test_rvol_zero_baseline_and_regular_only(self):
        o = replace(observation(), history_dates=("2026-09-24",), historical_volumes=(HistoricalVolume("2026-09-24", 360, 0),))
        self.assertIsNone(calculate(o, RadarConfig(rvol_lookback_days=1))["rvol"])
        with self.assertRaises(ValueError):
            RadarConfig(session="extended")
        with self.assertRaises(ValueError):
            calculate(replace(o, asof_utc=utc(START - timedelta(seconds=30))), CONFIG)

    def test_ranking_independent_priorities_and_ties(self):
        rows = [calculate(observation(t), CONFIG) for t in ("AAA", "BBB", "CCC", "DDD", "EEE")]
        for row, day, five in zip(rows, (50, 10, 10, None, -5), (1, 20, 20, 30, None)):
            row.update(day_change_pct=day, return_5m=five)
        rank(rows)
        self.assertEqual([r["current_leader_rank"] for r in rows], [1, 2, 3, None, 4])
        self.assertEqual([r["emerging_rank"] for r in rows], [4, 2, 3, 1, None])

    def test_top10_from_full_ranked_universe(self):
        rows = [calculate(observation(f"T{i:02}", price=10+i), CONFIG) for i in range(12)]
        rank(rows)
        top = sorted((r for r in rows if r["current_leader_rank"] <= 10), key=lambda r: r["current_leader_rank"])
        self.assertEqual([r["ticker"] for r in top], [f"T{i:02}" for i in range(11, 1, -1)])
        self.assertEqual(rows[0]["current_leader_rank"], 12)


class AuditStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = RadarConfig(data_dir=Path(self.temp.name))
        self.store = RadarStore(self.config.db_path, {"mode": "test", "note": "검수 전용"})
        self.engine = RadarEngine(self.store, self.config, "audit_fixture", "replay")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def process(self, value):
        return self.engine.process(value, value.timestamp_utc)

    def test_all_rank_events_dedup_and_reentry(self):
        self.process(batch(observation()))
        self.process(batch(observation(seconds=390)))
        expected = {f"ENTER_{group}_TOP{n}" for group in ("LEADER", "EMERGING") for n in (10, 5, 3)}
        counts = dict(self.store.db.execute("SELECT event_type,COUNT(*) FROM radar_events GROUP BY event_type"))
        for name in expected | {"FIRST_DETECTED"}:
            self.assertEqual(counts[name], 1, name)
        # More than ten stronger symbols force both ranking exits while TEST remains observed.
        self.process(batch(observation(seconds=420), *(observation(f"Z{i:02}", seconds=420, price=20+i) for i in range(11))))
        self.process(batch(observation(seconds=450)))
        counts = dict(self.store.db.execute("SELECT event_type,COUNT(*) FROM radar_events WHERE ticker='TEST' GROUP BY event_type"))
        for name in expected:
            self.assertEqual(counts[name], 2, name)
        self.assertEqual(counts["FIRST_DETECTED"], 1)

    def test_hod_event_state_not_repeated(self):
        self.process(batch(observation()))
        o = observation(seconds=390)
        self.process(batch(o))
        last = o.bars[-1]
        flat = replace(last, start_utc=last.end_utc, end_utc=utc(START+timedelta(seconds=420)))
        self.process(batch(replace(o, asof_utc=flat.end_utc, bars=o.bars+(flat,))))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM radar_events WHERE event_type='HOD_BREAK'").fetchone()[0], 1)
        state = json.loads(self.store.db.execute("SELECT snapshot_json FROM radar_state").fetchone()[0])
        self.assertAlmostEqual(state["hod"], last.high)

    def test_every_intraday_result_and_sampled_mfe_mae(self):
        self.process(batch(observation(seconds=360, price=10)))
        for (horizon, seconds), price in zip(HORIZONS.items(), (11, 9, 12, 8, 13, 7, 14)):
            self.process(batch(observation(seconds=360+seconds, price=price)))
            row = self.store.db.execute("SELECT * FROM radar_results WHERE horizon=?", (horizon,)).fetchone()
            self.assertEqual(row["status"], "complete", horizon)
            self.assertEqual(row["price"], price)
            self.assertAlmostEqual(row["return_pct"], (price-10)*10)
            self.assertEqual(row["timing_error_seconds"], 0)
        last = self.store.db.execute("SELECT * FROM radar_results WHERE horizon='60m'").fetchone()
        self.assertAlmostEqual(last["mfe_pct"], 40)
        self.assertAlmostEqual(last["mae_pct"], -30)

    def test_eod_through_d3_official_closes(self):
        self.process(batch(observation()))
        for index, session in enumerate(CALENDAR):
            close = DailyClose("TEST", session.date, session.close_utc, 12+index)
            self.process(batch(stamp=session.close_utc, closes=(close,)))
            horizon = "EOD" if index == 0 else f"D+{index}"
            row = self.store.db.execute("SELECT * FROM radar_results WHERE horizon=?", (horizon,)).fetchone()
            self.assertEqual(row["status"], "complete")
            self.assertEqual(row["price"], 12+index)
            self.assertEqual(row["target_utc"], session.close_utc)

    def test_required_fields_pending_and_restart(self):
        self.process(batch(observation()))
        self.store.close()
        self.store = RadarStore(self.config.db_path, {"mode": "test", "note": "검수 전용"})
        row = self.store.db.execute("SELECT * FROM radar_snapshots").fetchone()
        self.assertEqual(row["timestamp_utc"], "2026-09-25T13:36:00+00:00")
        self.assertEqual(row["timestamp_et"], "2026-09-25T09:36:00-04:00")
        self.assertEqual(row["ticker"], "TEST")
        self.assertEqual(row["test_version"], "market_radar_v1")
        for result in self.store.db.execute("SELECT * FROM radar_results"):
            self.assertEqual(result["status"], "pending")
            for key in ("price", "return_pct", "observed_utc", "mfe_pct", "mae_pct"):
                self.assertIsNone(result[key])

    def test_csv_roundtrip_headers_numeric_null_unicode(self):
        self.process(batch(observation()))
        folder = self.store.export_day(SESSION.date, self.config.data_dir / "export")
        for filename in ("candidates", "snapshots", "events", "results"):
            path = folder / f"{filename}.csv"
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                records = list(reader)
                self.assertEqual(len(reader.fieldnames), len(set(reader.fieldnames)))
            self.assertEqual(records[0]["ticker"], "TEST")
            self.assertEqual(records[0]["test_version"], "market_radar_v1")
            if filename == "snapshots":
                self.assertEqual(records[0]["timestamp_et"], "2026-09-25T09:36:00-04:00")
                self.assertEqual(records[0]["timestamp_utc"], "2026-09-25T13:36:00+00:00")
                self.assertAlmostEqual(float(records[0]["price"]), 10.6)
                self.assertEqual(float(records[0]["volume"]), 1200)
                self.assertEqual(records[0]["rvol"], "")
            if filename == "results":
                self.assertEqual(records[0]["price"], "")
                self.assertEqual(records[0]["status"], "pending")
        self.assertEqual(json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["note"], "검수 전용")


class AuditRuntimeTests(unittest.TestCase):
    def test_config_provider_and_gitignore(self):
        self.assertEqual(CONFIG.snapshot_seconds, 30)
        self.assertEqual(CONFIG.rvol_lookback_days, 14)
        self.assertEqual(CONFIG.test_version, "kiwoom_radar_v2_1")
        self.assertEqual(CONFIG.db_path, ROOT / "data/stock_helper_market_kiwoom_v2_1.db")
        self.assertEqual(create_live_provider().name, "kiwoom")
        self.assertIsInstance(create_live_provider(RadarConfig()), UnconfiguredProvider)
        result = subprocess.run(["git", "check-ignore", "data/stock_helper_market_kiwoom_v2.db", ".env"], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_provider_db_calculation_export_thread_ownership(self):
        main_thread = threading.get_ident()
        called = {}
        class RecordedProvider:
            name, mode = "audit_threads", "replay"
            def connect(self): called["connect"] = threading.get_ident(); self.sent = False
            def close(self): called["close"] = threading.get_ident()
            def fetch(self, tracked):
                called["fetch"] = threading.get_ident()
                if self.sent: raise StopIteration
                self.sent = True
                return batch(observation())
        original_process, original_export = RadarEngine.process, RadarStore.export_all
        def process(engine, *args):
            called["calculation_db"] = threading.get_ident()
            return original_process(engine, *args)
        def export(store, *args):
            called["export"] = threading.get_ident()
            return original_export(store, *args)
        with tempfile.TemporaryDirectory() as directory:
            worker = RadarWorker(RecordedProvider(), RadarConfig(data_dir=Path(directory)), fast_replay=True)
            with patch.object(RadarEngine, "process", process), patch.object(RadarStore, "export_all", export):
                worker.start()
                worker.thread.join(5)
            self.assertFalse(worker.running)
            self.assertIsNone(worker.error)
        self.assertEqual(set(called), {"connect", "close", "fetch", "calculation_db", "export"})
        self.assertEqual(len(set(called.values())), 1)
        self.assertNotIn(main_thread, called.values())

    def test_live_ui_panel_smoke_without_capture_services(self):
        from PySide6.QtWidgets import QApplication
        from live_ui import LiveStockWindow
        app = QApplication.instance() or QApplication([])
        window = LiveStockWindow()
        with patch.object(window, "start_capture_services") as services:
            window.show()
            app.processEvents()
            self.assertTrue(window.isVisible())
            for i in range(4):
                window._show_page(i)
                self.assertEqual(window.page_stack.currentIndex(), i)
            window.radar_button.click()
            app.processEvents()
            panel = window._market_radar_panel
            self.assertTrue(panel.isVisible())
            self.assertIsNone(panel.worker)
            self.assertIn("미연결", panel.mode.text())
            self.assertTrue(all(table.rowCount() == 0 for table in panel.tables))
            window.shutdown()
            window.close()
            app.processEvents()
            self.assertFalse(panel.isVisible())
            services.assert_not_called()
        panel.deleteLater()
        window.deleteLater()
        app.processEvents()

    def test_collect_cli_fails_explicitly_without_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, "-m", "market_radar", "--collect", "--provider", "unconfigured", "--data-dir", directory],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn("미연결", result.stdout)
            self.assertFalse((Path(directory) / "stock_helper_market.db").exists())

    def test_main_entrypoint_and_normal_shutdown(self):
        code = '''
import live_ui
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
def no_external_services(window):
    window._open_market_radar()
    assert window._market_radar_panel.isVisible()
    assert window._market_radar_panel.worker is None
    QTimer.singleShot(100, QApplication.instance().quit)
with patch.object(live_ui.LiveStockWindow, 'start_capture_services', no_external_services):
    result = live_ui.main()
assert result == 0
print('AUDIT_MAIN_STARTUP_SHUTDOWN_PASSED')
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                                env=dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8"),
                                capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("AUDIT_MAIN_STARTUP_SHUTDOWN_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
