"""Deterministic fixtures only; never used as a live market source."""
from __future__ import annotations

import csv
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.calculations import calculate, rank
from market_radar.config import RadarConfig
from market_radar.engine import RadarEngine
from market_radar.models import Bar, DailyClose, HistoricalVolume, MarketBatch, Observation, Session, instant, utc
from market_radar.provider import JsonlReplayProvider, RateLimitError, UnconfiguredProvider
from market_radar.runtime import RadarWorker
from market_radar.storage import RadarStore

START = datetime(2026, 9, 25, 13, 30, tzinfo=timezone.utc)  # Friday, ET daylight time
SESSION = Session("2026-09-25", utc(START), utc(START + timedelta(hours=6, minutes=30)))
CALENDAR = (SESSION,) + tuple(
    Session(f"2026-09-{d}", f"2026-09-{d}T13:30:00+00:00", f"2026-09-{d}T20:00:00+00:00")
    for d in (28, 29, 30)
)


def observation(ticker="TEST", seconds=360, price=None, session=SESSION):
    start = instant(session.open_utc)
    bars = []
    for offset in range(0, seconds, 30):
        end = min(seconds, offset + 30)
        close = 10 + end / 600
        bars.append(Bar(utc(start + timedelta(seconds=offset)), utc(start + timedelta(seconds=end)),
                        close + .1, close - .1, close, 100, close * 100))
    return Observation(ticker, utc(start + timedelta(seconds=seconds)),
                       price if price is not None else bars[-1].close, 10, session, tuple(bars))


def batch(*observations, sessions=CALENDAR, closes=(), stamp=None):
    return MarketBatch(stamp or observations[0].asof_utc, tuple(observations), tuple(sessions), tuple(closes), "TEST FIXTURE ONLY")


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.config = RadarConfig(rvol_lookback_days=2)

    def test_vwap_exact_notional(self):
        o = observation()
        r = calculate(o, self.config)
        self.assertAlmostEqual(r["vwap"], sum(b.notional for b in o.bars) / 1200)
        self.assertAlmostEqual(r["price_vs_vwap_pct"], (o.price / r["vwap"] - 1) * 100)
        self.assertEqual(r["above_vwap"], 1)

    def test_no_approximate_vwap_or_missing_volume(self):
        o = observation()
        r = calculate(replace(o, bars=tuple(replace(b, notional=None) for b in o.bars)), self.config)
        self.assertIsNone(r["vwap"])
        self.assertEqual(r["volume"], 1200)
        r = calculate(replace(o, bars=o.bars[1:]), self.config)
        self.assertIsNone(r["volume"])
        self.assertIsNone(r["hod"])

    def test_opening_ranges_completed_only(self):
        self.assertIsNone(calculate(observation(seconds=30), self.config)["opening_1m_high"])
        r = calculate(observation(seconds=60), self.config)
        self.assertAlmostEqual(r["opening_1m_high"], 10.2)
        self.assertAlmostEqual(r["opening_1m_low"], 9.95)
        self.assertIsNone(r["opening_5m_high"])
        r = calculate(observation(), self.config)
        self.assertAlmostEqual(r["opening_5m_high"], 10.6)
        self.assertAlmostEqual(r["opening_5m_low"], 9.95)

    def test_returns_exact_timestamp(self):
        r = calculate(observation(), self.config)
        self.assertAlmostEqual(r["return_1m"], (10.6 / 10.5 - 1) * 100)
        self.assertAlmostEqual(r["return_5m"], (10.6 / 10.1 - 1) * 100)
        self.assertIsNone(calculate(observation(seconds=60), self.config)["return_1m"])
        o = observation()
        o = replace(o, bars=tuple(b for b in o.bars if b.end_utc != utc(START + timedelta(seconds=60))))
        self.assertIsNone(calculate(o, self.config)["return_5m"])

    def test_rvol_same_time_full_lookback(self):
        o = replace(observation(), history_dates=("2026-09-24", "2026-09-23"), historical_volumes=(
            HistoricalVolume("2026-09-24", 360, 600), HistoricalVolume("2026-09-23", 360, 200)))
        self.assertEqual(calculate(o, self.config)["rvol"], 3)
        self.assertIsNone(calculate(replace(o, historical_volumes=o.historical_volumes[:1]), self.config)["rvol"])
        self.assertIsNone(calculate(replace(o, historical_volumes=(HistoricalVolume("2026-09-24", 300, 600),)), self.config)["rvol"])

    def test_rank_null_ties(self):
        rows = [calculate(observation(t), self.config) for t in ("BBB", "AAA", "CCC")]
        rows[2]["day_change_pct"] = rows[2]["return_5m"] = None
        rank(rows)
        self.assertEqual([r["current_leader_rank"] for r in rows], [2, 1, None])
        self.assertEqual([r["emerging_rank"] for r in rows], [2, 1, None])

    def test_rvol_does_not_truncate_subsecond_timestamp(self):
        o = observation()
        later = utc(instant(o.asof_utc) + timedelta(microseconds=500000))
        o = replace(o, asof_utc=later, bars=o.bars[:-1] + (replace(o.bars[-1], end_utc=later),),
                    history_dates=("2026-09-24", "2026-09-23"), historical_volumes=(
                        HistoricalVolume("2026-09-24", 360, 600), HistoricalVolume("2026-09-23", 360, 600)))
        self.assertIsNone(calculate(o, self.config)["rvol"])

    def test_dst_and_early_close(self):
        winter = Session("2026-11-27", "2026-11-27T14:30:00+00:00", "2026-11-27T18:00:00+00:00")
        row = calculate(observation(session=winter), self.config)
        self.assertIn("09:36:00-05:00", row["timestamp_et"])

    def test_invalid_input_rejected(self):
        for o in (replace(observation(), price=float("nan")),
                  replace(observation(), asof_utc="2026-09-25T09:36:00"),
                  replace(observation(), bars=observation().bars * 2)):
            with self.assertRaises(ValueError):
                calculate(o, self.config)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = RadarConfig(data_dir=Path(self.temp.name))
        self.store = RadarStore(self.config.db_path, {"test": True})
        self.engine = RadarEngine(self.store, self.config, "fixture", "replay")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def process(self, value):
        return self.engine.process(value, value.timestamp_utc)

    def query(self, sql):
        return self.store.db.execute(sql).fetchall()

    def test_insert_read_raw_and_pending(self):
        self.process(batch(observation()))
        row = self.query("SELECT * FROM radar_snapshots")[0]
        self.assertEqual(row["ticker"], "TEST")
        self.assertEqual(row["test_version"], "market_radar_v1")
        self.assertEqual(len(self.query("SELECT * FROM radar_results")), 11)
        self.assertTrue(all(r["price"] is None and r["status"] == "pending" for r in self.query("SELECT * FROM radar_results")))
        raw = json.loads(self.query("SELECT raw_json FROM radar_batches")[0][0])
        self.assertEqual(raw["universe"], "TEST FIXTURE ONLY")

    def test_event_dedup_retry_restart(self):
        first = batch(observation())
        self.process(first)
        before = len(self.query("SELECT * FROM radar_events"))
        self.process(first)
        self.assertEqual(before, len(self.query("SELECT * FROM radar_events")))
        self.store.close()
        self.store = RadarStore(self.config.db_path, {"test": True})
        self.engine = RadarEngine(self.store, self.config, "fixture", "replay")
        self.process(batch(observation(seconds=390)))
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='FIRST_DETECTED'")), 1)
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='ENTER_LEADER_TOP3'")), 1)
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='HOD_BREAK'")), 1)

    def test_rank_exit_reentry(self):
        self.process(batch(observation()))
        self.process(batch(stamp=utc(START + timedelta(seconds=390))))
        self.process(batch(observation(seconds=420)))
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='ENTER_LEADER_TOP3'")), 2)
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='FIRST_DETECTED'")), 1)

    def test_vwap_transitions(self):
        self.process(batch(observation()))
        self.process(batch(observation(seconds=390, price=9)))
        self.process(batch(observation(seconds=420, price=9)))
        self.process(batch(observation(seconds=450, price=12)))
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='VWAP_LOSS'")), 1)
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='VWAP_RECLAIM'")), 1)

    def test_outcomes_sampling_and_excursions(self):
        self.process(batch(observation()))
        self.process(batch(observation(seconds=390, price=11)))
        row = self.query("SELECT * FROM radar_results WHERE horizon='30s'")[0]
        self.assertEqual(row["status"], "complete")
        self.assertAlmostEqual(row["return_pct"], (11 / 10.6 - 1) * 100)
        self.assertEqual(row["timing_error_seconds"], 0)
        self.assertEqual(row["mae_pct"], 0)
        self.assertEqual(row["excursion_basis"], "sampled_prices")

    def test_missed_horizon_not_filled_by_later_price(self):
        self.process(batch(observation()))
        self.process(batch(observation(seconds=480)))
        result = self.query("SELECT * FROM radar_results WHERE horizon='30s'")[0]
        self.assertEqual(result["status"], "missing")
        self.assertIsNone(result["price"])

    def test_official_eod_and_trading_days(self):
        self.process(batch(observation()))
        close = DailyClose("TEST", SESSION.date, SESSION.close_utc, 12)
        self.process(batch(stamp=SESSION.close_utc, closes=(close,)))
        self.assertEqual(self.query("SELECT price FROM radar_results WHERE horizon='EOD'")[0][0], 12)
        target = self.query("SELECT target_utc FROM radar_results WHERE horizon='D+1'")[0][0]
        self.assertEqual(target, "2026-09-28T20:00:00+00:00")
        close = DailyClose("TEST", "2026-09-28", target, 13)
        self.process(batch(stamp=target, closes=(close,)))
        self.assertEqual(self.query("SELECT price FROM radar_results WHERE horizon='D+1'")[0][0], 13)
        self.assertIsNone(self.query("SELECT price FROM radar_results WHERE horizon='D+2'")[0][0])

    def test_no_calendar_leaves_d_plus_pending(self):
        self.process(batch(observation(), sessions=()))
        result = self.query("SELECT * FROM radar_results WHERE horizon='D+1'")[0]
        self.assertIsNone(result["target_utc"])
        self.assertEqual(result["status"], "pending")

    def test_transaction_rollback_and_out_of_order(self):
        with patch.object(self.engine, "_resolve_results", side_effect=ValueError("fixture failure")):
            with self.assertRaises(ValueError):
                self.process(batch(observation()))
        self.assertFalse(self.query("SELECT * FROM radar_snapshots"))
        self.assertFalse(self.query("SELECT * FROM radar_batches"))
        self.process(batch(observation()))
        with self.assertRaises(ValueError):
            self.process(batch(observation(seconds=300)))

    def test_export_and_later_result_refresh(self):
        self.process(batch(observation()))
        root = self.config.data_dir / "export"
        folder = self.store.export_day(SESSION.date, root)
        self.assertEqual({p.name for p in folder.iterdir()}, {"snapshots.csv", "candidates.csv", "events.csv", "results.csv", "metadata.json"})
        with (folder / "results.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["price"], "")
        self.process(batch(observation(seconds=390)))
        self.store.export_all(root)
        with (folder / "results.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["status"], "complete")

    def test_db_identity_prevents_live_replay_mixing(self):
        with self.assertRaises(ValueError):
            RadarStore(self.config.db_path, {"test": False})

    def test_tracked_only_ticker_excluded_from_ranking(self):
        rows = self.process(batch(observation(), replace(observation("TRACKED", price=100), in_universe=False)))
        self.assertEqual(rows[0]["current_leader_rank"], 1)
        self.assertIsNone(rows[1]["current_leader_rank"])
        self.assertEqual(len(self.query("SELECT * FROM radar_candidates")), 1)

    def test_snapshot_return_fallback(self):
        self.process(batch(replace(observation(seconds=30), bars=())))
        rows = self.process(batch(replace(observation(seconds=90), bars=())))
        self.assertAlmostEqual(rows[0]["return_1m"], (10.15 / 10.05 - 1) * 100)
        self.assertIsNone(rows[0]["return_5m"])

    def test_local_same_time_rvol(self):
        self.engine.config = replace(self.config, rvol_lookback_days=2)
        for day in (23, 24):
            session = Session(f"2026-09-{day}", f"2026-09-{day}T13:30:00+00:00", f"2026-09-{day}T20:00:00+00:00")
            self.process(batch(observation(seconds=30, session=session), sessions=()))
        rows = self.process(batch(replace(observation(seconds=30), history_dates=("2026-09-24", "2026-09-23"))))
        self.assertEqual(rows[0]["rvol"], 1)

    def test_daily_detection_reset(self):
        self.process(batch(observation()))
        self.process(batch(observation(session=CALENDAR[1])))
        self.assertEqual(len(self.query("SELECT * FROM radar_candidates")), 2)
        self.assertEqual(len(self.query("SELECT * FROM radar_events WHERE event_type='FIRST_DETECTED'")), 2)

    def test_outside_session_horizon(self):
        short = Session("2026-09-25", SESSION.open_utc, utc(START + timedelta(seconds=390)))
        self.process(batch(observation(session=short), sessions=()))
        row = self.query("SELECT * FROM radar_results WHERE horizon='1m'")[0]
        self.assertEqual(row["status"], "outside_session")
        self.assertIsNone(row["price"])


class RuntimeTests(unittest.TestCase):
    def test_stale_live_data_not_stored(self):
        class StaleProvider:
            name, mode = "stale_fixture", "live"
            def connect(self): self.sent = False
            def close(self): pass
            def fetch(self, tracked):
                if self.sent:
                    raise StopIteration
                self.sent = True
                return batch(observation())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = RadarWorker(StaleProvider(), RadarConfig(data_dir=root, snapshot_seconds=.01))
            worker.start()
            worker.thread.join(5)
            self.assertFalse(worker.running)
            import sqlite3
            with closing(sqlite3.connect(root / "stock_helper_market.db")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM radar_snapshots").fetchone()[0], 0)
            self.assertIn("data_delay", "".join(p.read_text(encoding="utf-8") for p in (root / "logs").glob("*.log")))

    def test_rate_limit_backoff_interruptible(self):
        import threading
        called = threading.Event()
        class LimitedProvider:
            name, mode = "rate_limit_fixture", "live"
            def connect(self): self.calls = 0
            def close(self): pass
            def fetch(self, tracked):
                self.calls += 1
                called.set()
                raise RateLimitError(retry_after_seconds=60)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = LimitedProvider()
            worker = RadarWorker(provider, RadarConfig(data_dir=root))
            worker.start()
            self.assertTrue(called.wait(3))
            worker.stop()
            worker.thread.join(3)
            self.assertFalse(worker.running)
            self.assertEqual(provider.calls, 1)
            self.assertIn("rate_limit", "".join(p.read_text(encoding="utf-8") for p in (root / "logs").glob("*.log")))

    def test_invalid_replay_reports_failed_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "invalid.jsonl"
            path.write_text('{"invalid":true}\n', encoding="utf-8")
            worker = RadarWorker(JsonlReplayProvider(path), RadarConfig(data_dir=root), fast_replay=True)
            worker.start()
            worker.thread.join(5)
            self.assertFalse(worker.running)
            self.assertEqual(worker.failed_batches, 1)
            self.assertIsNotNone(worker.error)

    def test_replay_off_thread_exports_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "fixture.jsonl"
            path.write_text("\n".join(json.dumps(batch(observation(seconds=s)).raw()) for s in (360, 390)), encoding="utf-8")
            worker = RadarWorker(JsonlReplayProvider(path), RadarConfig(data_dir=root), fast_replay=True)
            worker.start()
            worker.thread.join(10)
            self.assertFalse(worker.running)
            self.assertIsNone(worker.error)
            message = worker.messages.get_nowait()
            self.assertEqual(message["rows"][0]["ticker"], "TEST")
            self.assertTrue((root / "replay/export/2026-09-25/snapshots.csv").exists())
            self.assertFalse((root / "stock_helper_market.db").exists())
            logs = "".join(p.read_text(encoding="utf-8") for p in (root / "replay/logs").glob("*.log"))
            for event in ("program_start", "program_stop", "provider_connected", "provider_disconnected", "export_success"):
                self.assertIn(event, logs)

    def test_unconfigured_reports_no_data(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = RadarWorker(UnconfiguredProvider(), RadarConfig(data_dir=Path(directory)))
            worker.start()
            worker.thread.join(5)
            self.assertIn("ProviderError", worker.error)
            self.assertFalse((Path(directory) / "stock_helper_market.db").exists())

    def test_ui_responsive_during_slow_fetch(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from market_radar.ui import RadarPanel
        class SlowProvider:
            name, mode = "slow_fixture", "replay"
            def connect(self): pass
            def close(self): pass
            def fetch(self, tracked):
                time.sleep(.3)
                raise StopIteration
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            from PySide6.QtCore import QSettings
            panel = RadarPanel(master_path=Path(directory)/"master.db",
                settings=QSettings(str(Path(directory)/"ui.ini"), QSettings.Format.IniFormat))
            ticks = []
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(1))
            timer.start(10)
            with patch("market_radar.ui.CONFIG", RadarConfig(data_dir=Path(directory))):
                panel.start(SlowProvider())
            deadline = time.monotonic() + 2
            while panel.worker.running and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.005)
            timer.stop()
            panel.poll()
            panel.close()
            self.assertGreater(len(ticks), 10)
            self.assertFalse(panel.worker.running)
            panel.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
