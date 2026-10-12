"""Offline shadow tests: synthetic public prices, temporary storage, no networking."""
from dataclasses import replace
from datetime import timedelta
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from market_radar.config import CONFIG
from market_radar.models import Session, instant, utc
from market_radar.paper.engine import PaperEngine
from market_radar.paper.policy import ExperimentalBaselinePolicyV1, reason
from market_radar.paper.storage import PaperStore
from market_radar.paper.runtime import PaperRuntime

SESSION = Session("2026-10-08", "2026-10-08T13:30:00+00:00", "2026-10-08T20:00:00+00:00")


def stamp(seconds):
    return utc(instant(SESSION.open_utc)+timedelta(seconds=seconds))


def row(seconds=360, ticker="TEST", price=10, **kwargs):
    value = dict(ticker=ticker, price=price, timestamp_utc=stamp(seconds),
                 price_received_utc=stamp(seconds), price_age_ms=0.0, provider="kiwoom",
                 current_leader_rank=3, emerging_rank=1, ws_gap_detected=False,
                 completed_bar_at=stamp(int(seconds//60)*60), return_1m=1.0, return_5m=3.0,
                 candidate_sources='["5m_spike", "volume"]', rvol=None, rvol_status="incomplete_history",
                 candidate_detected_at=stamp(360), hod=None, opening_1m_high=None)
    value.update(kwargs)
    return value


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/"paper.db"
        self.store = PaperStore(self.path)
        self.engine = PaperEngine(self.store, "synthetic", enabled=True)
        # Any accidental broker/network use is a failure, including order requests.
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.store.close()
        self.tmp.cleanup()

    def frame(self, seconds=360, **kwargs):
        self.engine.on_frame(SESSION, [row(seconds, **kwargs)], stamp(seconds))

    def signal(self, ticker="TEST", start=360):
        self.frame(start, ticker=ticker)
        self.frame(start+62, ticker=ticker, price=11)

    def opened(self, ticker="TEST", start=360):
        self.signal(ticker, start)
        self.engine.on_tick(ticker, 11.1, stamp(start+62.001))
        self.assertEqual(self.engine.status, "OPEN")

    def test_default_off(self):
        engine = PaperEngine(self.store, "off")
        engine.on_frame(SESSION, [row()], stamp(360))
        self.assertEqual(engine.status, "OFF")
        self.assertEqual(self.store.trades(), [])

    def test_capacity_forced_off(self):
        engine = PaperEngine(self.store, "capacity", enabled=True, collection_mode="Capacity Test")
        engine.on_frame(SESSION, [row()], stamp(360))
        self.assertEqual(engine.status, "OFF")
        self.assertEqual(self.store.trades(), [])

    def test_priority_groups_ranks_ticker(self):
        policy = ExperimentalBaselinePolicyV1()
        rows = [row(ticker="C", emerging_rank=None), row(ticker="B", current_leader_rank=None), row(ticker="A")]
        self.assertEqual(policy.select(rows, set(), stamp(360), 90)["ticker"], "A")
        self.assertEqual(policy.select(rows, {"A"}, stamp(360), 90)["ticker"], "B")
        self.assertEqual(policy.select([row(ticker="Z"), row(ticker="D")], set(), stamp(360), 90)["ticker"], "D")

    def test_stale_excluded(self):
        self.frame(price_age_ms=91000)
        self.assertIsNone(self.engine.active)

    def test_ws_gap_excluded(self):
        self.frame(ws_gap_detected=True)
        self.assertIsNone(self.engine.active)

    def test_missing_freshness_excluded(self):
        self.frame(price_received_utc=None)
        self.assertIsNone(self.engine.active)

    def test_null_momentum_no_entry(self):
        self.frame()
        self.frame(422, price=11, return_5m=None)
        self.assertEqual(self.engine.status, "WATCHING")

    def test_watch_starts_without_entry(self):
        self.frame()
        self.assertEqual(self.engine.status, "WATCHING")
        self.assertEqual(self.engine.view()["trades"], 0)

    def test_no_immediate_chase(self):
        self.frame()
        self.frame(361, price=12)
        self.assertEqual(self.engine.status, "WATCHING")

    def test_new_completed_bar_required(self):
        self.frame()
        self.frame(422, price=12, completed_bar_at=stamp(360))
        self.assertEqual(self.engine.status, "WATCHING")

    def test_null_completed_bar_no_proxy(self):
        self.frame()
        self.frame(422, price=12, completed_bar_at=None)
        self.assertEqual(self.engine.status, "WATCHING")

    def test_both_momentum_positive(self):
        for field in ("return_1m", "return_5m"):
            self.frame()
            self.frame(422, price=12, **{field: 0})
            self.assertEqual(self.engine.status, "WATCHING")

    def test_watch_reference_break_required(self):
        self.frame()
        self.frame(422, price=10)
        self.assertEqual(self.engine.status, "WATCHING")

    def test_signal_is_not_entry(self):
        self.signal()
        self.assertEqual(self.engine.status, "ENTRY_SIGNAL")
        self.assertIsNone(self.engine.active.get("entry_price"))
        self.assertEqual(self.engine.view()["trades"], 0)

    def test_same_timestamp_cannot_fill(self):
        self.signal()
        self.engine.on_tick("TEST", 11, stamp(422))
        self.assertEqual(self.engine.status, "ENTRY_SIGNAL")
        self.engine.on_tick("TEST", 11.2, stamp(422.001))
        self.assertEqual(self.engine.active["entry_price"], 11.2)

    def test_first_later_live_price_and_precision(self):
        self.opened()
        self.assertEqual(self.engine.active["entry_price_at"], stamp(422.001))
        self.assertEqual(self.engine.active["entry_price_source"], "kiwoom_live")

    def test_take_profit_observed_overshoot(self):
        self.opened()
        self.engine.on_tick("TEST", 13, stamp(423))
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual((trade["exit_reason"], trade["exit_price"]), ("TAKE_PROFIT_15", 13))

    def test_stop_loss_observed_undershoot(self):
        self.opened()
        self.engine.on_tick("TEST", 9.5, stamp(423))
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual((trade["exit_reason"], trade["exit_price"]), ("STOP_LOSS_10", 9.5))

    def test_distinct_prices_same_receipt_timestamp_still_trigger_tp(self):
        self.opened()
        self.engine.on_tick("TEST", 11.2, stamp(423))
        self.engine.on_tick("TEST", 13, stamp(423))
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual((trade["exit_reason"], trade["exit_price"]), ("TAKE_PROFIT_15", 13))

    def test_distinct_prices_same_receipt_timestamp_still_trigger_sl(self):
        self.opened()
        self.engine.on_tick("TEST", 11.2, stamp(423))
        self.engine.on_tick("TEST", 9, stamp(423))
        self.assertEqual(self.store.entered(SESSION.date)[0]["exit_reason"], "STOP_LOSS_10")

    def test_identical_tick_duplicate_does_not_duplicate_entry(self):
        self.opened()
        self.engine.on_tick("TEST", 11.1, stamp(422.001))
        self.assertEqual(len(self.store.entered(SESSION.date)), 1)

    def test_no_discretionary_early_exit(self):
        self.opened()
        self.engine.on_tick("TEST", 12, stamp(423))
        self.assertEqual(self.engine.status, "OPEN")

    def test_mfe_and_timestamp(self):
        self.opened()
        self.engine.on_tick("TEST", 12, stamp(423))
        self.assertAlmostEqual(self.engine.active["mfe_pct"], (12/11.1-1)*100)
        self.assertEqual(self.engine.active["mfe_at"], stamp(423))

    def test_mae_and_timestamp(self):
        self.opened()
        self.engine.on_tick("TEST", 10.5, stamp(423))
        self.assertAlmostEqual(self.engine.active["mae_pct"], (10.5/11.1-1)*100)
        self.assertEqual(self.engine.active["mae_at"], stamp(423))

    def test_same_ticker_banned(self):
        self.opened()
        self.engine.on_tick("TEST", 13, stamp(423))
        self.frame(424)
        self.assertIsNone(self.engine.active)

    def test_daily_four_entries_and_fourth_exit(self):
        for i in range(4):
            self.opened("T"+str(i), 360+i*180)
            self.engine.on_tick("T"+str(i), 13, stamp(423+i*180))
        self.frame(1100, ticker="FIFTH")
        self.assertEqual(self.engine.status, "DAILY_LIMIT_REACHED")
        self.assertEqual(len(self.store.entered(SESSION.date)), 4)

    def test_next_et_day_resets_and_allows_same_ticker(self):
        self.opened()
        self.engine.on_tick("TEST", 13, stamp(423))
        next_session = Session("2026-10-09", "2026-10-09T13:30:00+00:00", "2026-10-09T20:00:00+00:00")
        value = {k: v.replace("2026-10-08", "2026-10-09") if isinstance(v, str) else v for k,v in row().items()}
        self.engine.on_frame(next_session, [value], value["timestamp_utc"])
        self.assertEqual(self.engine.status, "WATCHING")
        self.assertEqual(self.engine.view()["trades"], 0)

    def test_session_end_last_observed_price(self):
        self.opened(start=23280)
        self.engine.on_tick("TEST", 11.5, stamp(23395))
        self.engine.heartbeat(SESSION.close_utc)
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual((trade["exit_reason"], trade["exit_price"]), ("SESSION_END", 11.5))
        self.assertEqual(trade["exit_price_at"], stamp(23395))
        self.assertIsNone(self.store.summary(SESSION.date)["tp_sl_win_rate"])

    def test_disconnect_invalidates_not_tp(self):
        self.opened()
        self.engine.gap(stamp(423))
        self.engine.on_tick("TEST", 20, stamp(424))
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual(trade["exit_reason"], "DATA_INTEGRITY_INVALID")
        self.assertIsNone(trade["exit_price"])
        self.assertIsNone(trade["return_pct"])
        self.assertIn("gap_start", json.loads(trade["exit_evidence_json"]))

    def test_stale_gap_invalidates(self):
        self.opened()
        self.engine.heartbeat(stamp(520))
        self.assertEqual(self.store.entered(SESSION.date)[0]["exit_reason"], "DATA_INTEGRITY_INVALID")

    def test_tick_after_gap_cannot_fabricate_tp(self):
        self.opened()
        self.engine.on_tick("TEST", 20, stamp(520))
        self.assertEqual(self.store.entered(SESSION.date)[0]["exit_reason"], "DATA_INTEGRITY_INVALID")

    def test_restart_recovers_count_and_invalidates_open(self):
        self.opened()
        restarted = PaperEngine(self.store, "restart", enabled=True, started_at=stamp(500))
        restarted.on_frame(SESSION, [row(501)], stamp(501))
        self.assertEqual(self.store.entered(SESSION.date)[0]["exit_reason"], "INTERRUPTED")
        self.assertEqual(restarted.view()["trades"], 1)
        self.assertIsNone(restarted.active)

    def test_restart_pending_watch_is_recorded_and_rewatched(self):
        self.frame()
        restarted = PaperEngine(self.store, "restart", enabled=True, started_at=stamp(500))
        restarted.on_frame(SESSION, [row(501)], stamp(501))
        self.assertEqual(self.store.trades()[0]["exit_reason"], "INTERRUPTED")
        self.assertEqual(restarted.active["watch_started_at"], stamp(501))
        self.assertEqual(restarted.view()["trades"], 0)

    def test_entry_evidence_immutable(self):
        self.opened()
        before = self.engine.active["entry_evidence_json"]
        self.frame(423, price=12, rvol=9.9)
        self.engine.on_tick("TEST", 12, stamp(424))
        self.assertEqual(self.engine.active["entry_evidence_json"], before)

    def test_summary_from_persisted_evidence_only(self):
        self.signal()
        trade = self.engine.active
        self.assertEqual(trade["entry_reason"], reason(json.loads(trade["entry_evidence_json"])))
        for word in ("주포", "세력", "Strong Buy", "상승확률"):
            self.assertNotIn(word, trade["entry_reason"])

    def test_db_roundtrip(self):
        self.opened()
        self.store.close()
        self.store = PaperStore(self.path)
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual(trade["entry_price"], 11.1)
        self.assertEqual(self.store.db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_export_and_summary_exclude_invalid(self):
        self.opened()
        self.engine.gap(stamp(423))
        self.store.export(SESSION.date, Path(self.tmp.name)/"export")
        summary = json.loads((Path(self.tmp.name)/"export/summary.json").read_text())
        self.assertEqual(summary["invalid_count"], 1)
        self.assertIsNone(summary["average_return_pct"])
        self.assertIsNone(summary["tp_sl_win_rate"])

    def test_horizons_only_after_finish(self):
        self.opened()
        self.engine.on_tick("TEST", 13, stamp(423))
        self.engine.on_tick("TEST", 14, stamp(452.001))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM paper_horizons").fetchone()[0], 0)
        self.engine.finish(stamp(500))
        horizons = list(self.store.db.execute("SELECT * FROM paper_horizons ORDER BY target_utc"))
        self.assertEqual(len(horizons), 6)
        self.assertEqual(horizons[0]["price"], 14)
        self.assertIsNone(horizons[1]["price"])
        self.assertEqual(horizons[1]["missing_reason"], "no_post_target_observation_within_tolerance")

    def test_actual_orders_zero_and_no_broker_import(self):
        self.opened()
        self.assertEqual(self.engine.view()["actual_order_calls"], 0)
        for p in (Path(__file__).resolve().parents[1]/"market_radar/paper").glob("*.py"):
            text = p.read_text(encoding="utf-8")
            self.assertNotIn("nhplug", text)
            self.assertNotIn("/order/", text)

    def test_preopen_wait_no_trade(self):
        self.engine.on_frame(SESSION, [row(-1)], stamp(-1))
        self.assertEqual(self.engine.status, "WAITING")
        self.assertEqual(self.store.trades(), [])

    def test_candidate_replacement_before_signal(self):
        self.frame(ticker="OLD")
        self.frame(361, ticker="NEW")
        self.assertEqual(self.engine.active["ticker"], "NEW")
        self.assertEqual(self.store.trades()[0]["exit_reason_detail"], "watch_replaced")

    def test_signal_not_retroactively_replaced(self):
        self.signal()
        self.frame(423, ticker="NEW")
        self.assertEqual(self.engine.active["ticker"], "TEST")

    def test_replay_deterministic(self):
        def replay(store):
            engine = PaperEngine(store, "same-run", enabled=True)
            engine.on_frame(SESSION, [row()], stamp(360))
            engine.on_frame(SESSION, [row(422, price=11)], stamp(422))
            engine.on_tick("TEST", 11.1, stamp(422.001))
            engine.on_tick("TEST", 13, stamp(423))
            return store.trades()
        first = replay(self.store)
        other = PaperStore(Path(self.tmp.name)/"second.db")
        try:
            self.assertEqual(first, replay(other))
        finally:
            other.close()

    def test_ui_default_off_capacity_disabled(self):
        from PySide6.QtWidgets import QApplication
        from market_radar.paper.ui import PaperPanel
        app = QApplication.instance() or QApplication([])
        panel = PaperPanel()
        self.assertFalse(panel.enable.isChecked())
        panel.enable.setChecked(True)
        panel.render(None, busy=False, collection_mode="Capacity Test")
        self.assertFalse(panel.enable.isChecked())
        self.assertFalse(panel.enable.isEnabled())
        panel.close()

    def test_runtime_capacity_rejected_without_db(self):
        from types import SimpleNamespace
        path = Path(self.tmp.name)/"never_created.db"
        runtime = PaperRuntime(replace(CONFIG, collection_mode="Capacity Test"), "test", path)
        with self.assertRaises(ValueError):
            runtime.attach(SimpleNamespace(name="kiwoom", mode="live"))
        self.assertFalse(path.exists())

    def test_controller_capacity_never_starts_consumer(self):
        from types import SimpleNamespace
        from market_radar.controller import CollectorController
        config = replace(CONFIG, collection_mode="Capacity Test", data_dir=Path(self.tmp.name)/"run",
                         historical_cache_path=Path(self.tmp.name)/"cache.db")
        controller = CollectorController(Path(self.tmp.name)/"master.db")
        with patch("threading.Thread.start"):
            controller.start(config, SimpleNamespace(name="kiwoom", mode="live"),
                             fresh_directory=False, paper_enabled=True)
        self.assertFalse(controller.paper_enabled)
        self.assertIsNone(controller.paper)
        self.assertFalse(config.data_dir.exists())

    def test_frame_adapter_requires_real_completed_bar(self):
        from tests.kiwoom_provider_test import observation
        from market_radar.models import MarketBatch
        from market_radar.calculations import calculate, rank
        o = observation()
        runtime = PaperRuntime(CONFIG, "test", self.path)
        rows = rank([calculate(o, CONFIG)])
        batch = MarketBatch(o.asof_utc, (o,), (o.session,), metadata={"ws_connected": True, "ws_gap_detected": False})
        runtime.frame(batch, rows, {o.ticker: o.asof_utc}, o.asof_utc)
        frame = runtime.queue.get_nowait()
        enriched = frame[2][0]
        self.assertEqual(enriched["candidate_detected_at"], o.asof_utc)
        expected = next((b.end_utc for b in o.bars if b.end_utc == o.metadata.get("bar_cutoff_utc")), None)
        self.assertEqual(enriched["completed_bar_at"], expected)
        empty = replace(o, bars=())
        runtime.frame(replace(batch, observations=(empty,)), rows, {}, o.asof_utc)
        self.assertIsNone(runtime.queue.get_nowait()[2][0]["completed_bar_at"])

    def test_live_sink_preserves_original_and_delivers_every_interested_tick(self):
        import threading
        from types import SimpleNamespace
        from market_radar.providers.kiwoom_ws import KiwoomWebSocket
        provider = SimpleNamespace(name="kiwoom", mode="live", ws_factory=KiwoomWebSocket, raw_dropped=0)
        runtime = PaperRuntime(CONFIG, "test", self.path)
        with patch("threading.Thread.start"), patch.object(runtime.ready, "wait"):
            runtime.attach(provider)
        original = []
        ws = provider.ws_factory(None, CONFIG, threading.Event(), lambda *args: original.append(args), None)
        ws.connected, ws.active = True, {"TEST":"ND"}
        runtime.interests = frozenset({"TEST"})
        for price in (10, 10.1, 10.2):
            ws.handle({"trnm":"REAL", "data":[{"type":"FE", "item":"TEST", "values":{"10":str(price)}}]}, lambda _: None)
        ticks = [runtime.queue.get_nowait() for _ in range(3)]
        self.assertEqual([t[2] for t in ticks], [10, 10.1, 10.2])
        self.assertEqual(len(original), 3)
        self.assertTrue(all(t[-1] for t in ticks))
        self.assertEqual(ws.latest["TEST"]["values"]["10"], "10.2")

    def test_queue_overflow_is_detected(self):
        runtime = PaperRuntime(CONFIG, "test", self.path, queue_size=1)
        runtime._put(("gap", stamp(360), "test"))
        runtime._put(("gap", stamp(361), "test"))
        self.assertTrue(runtime.overflow.is_set())

    def test_attach_does_not_recover_or_touch_another_running_trade(self):
        from types import SimpleNamespace
        from market_radar.providers.kiwoom_ws import KiwoomWebSocket
        self.opened()
        runtime = PaperRuntime(CONFIG, "second-instance", self.path)
        runtime.attach(SimpleNamespace(name="kiwoom", mode="live", ws_factory=KiwoomWebSocket))
        self.assertIsNone(runtime.thread)
        runtime.finish()  # Failed provider lock/connect must not open/recover paper DB.
        self.assertEqual(self.store.entered(SESSION.date)[0]["state"], "OPEN")

    def test_stop_request_timestamp_not_shutdown_completion(self):
        from types import SimpleNamespace
        later = instant(SESSION.close_utc)+timedelta(seconds=10)
        runtime = PaperRuntime(CONFIG, "test", self.path, clock=lambda: later)
        runtime.request_finish(stamp(23399))
        runtime.request_finish(SESSION.close_utc)
        self.assertEqual(runtime.finish_at, stamp(23399))
        self.opened(start=23280)
        self.engine.on_tick("TEST", 11.5, stamp(23398))
        # Consumer runs after close, but shutdown was requested before close.
        with patch("market_radar.paper.runtime.PaperStore", return_value=self.store), \
             patch("market_radar.paper.runtime.PaperEngine", return_value=self.engine), \
             patch.object(self.store, "close"), patch.object(self.store, "export"):
            runtime._run()
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual(trade["exit_reason"], "INTERRUPTED")
        self.assertEqual(trade["exit_signal_at"], stamp(23399))
        self.assertIsNone(trade["exit_price"])

    def test_worker_finishes_paper_before_provider_releases_lock(self):
        from types import SimpleNamespace
        from market_radar.runtime import RadarWorker
        calls = []
        config = replace(CONFIG, data_dir=Path(self.tmp.name)/"worker")
        provider = SimpleNamespace(name="kiwoom", mode="live", connect=lambda: calls.append("connect"),
            close=lambda: calls.append("release"), fetch=lambda *a: (_ for _ in ()).throw(StopIteration()))
        # A normal finite source, avoiding generator's StopIteration conversion.
        def fetch(*args):
            raise StopIteration
        provider.fetch = fetch
        paper = SimpleNamespace(start=lambda: calls.append("paper_start"),
            request_finish=lambda at: calls.append("paper_stop_requested"),
            finish=lambda: calls.append("paper_finish"))
        worker = RadarWorker(provider, config, paper=paper)
        worker._run()
        self.assertIsNone(worker.error)
        self.assertLess(calls.index("connect"), calls.index("paper_start"))
        self.assertLess(calls.index("paper_finish"), calls.index("release"))

    def test_failed_connect_never_starts_paper_recovery(self):
        from types import SimpleNamespace
        from market_radar.runtime import RadarWorker
        calls = []
        def failed():
            raise RuntimeError("synthetic collector lock unavailable")
        provider = SimpleNamespace(name="kiwoom", mode="live", connect=failed, close=lambda: None)
        paper = SimpleNamespace(start=lambda: calls.append("start"), request_finish=lambda at: None, finish=lambda: None)
        worker = RadarWorker(provider, replace(CONFIG, data_dir=Path(self.tmp.name)/"failed"), paper=paper)
        worker._run()
        self.assertEqual(calls, [])
        self.assertIsNotNone(worker.error)

    def test_future_frame_or_bar_not_used(self):
        value = row(422, price=11)
        self.engine.on_frame(SESSION, [value], stamp(360))
        self.assertIsNone(self.engine.active)
        self.frame(completed_bar_at=stamp(422))
        self.assertIsNone(self.engine.active)

    def test_out_of_order_open_tick_invalidated(self):
        self.opened()
        self.engine.on_tick("TEST", 13, stamp(400))
        self.assertEqual(self.store.entered(SESSION.date)[0]["exit_reason"], "DATA_INTEGRITY_INVALID")

    def test_invalid_trade_still_bans_reentry(self):
        self.opened()
        self.engine.gap(stamp(423))
        self.frame(424)
        self.assertIsNone(self.engine.active)

    def test_restart_daily_limit_preserved(self):
        for i in range(4):
            self.opened("T"+str(i), 360+i*180)
            self.engine.on_tick("T"+str(i), 13, stamp(423+i*180))
        restarted = PaperEngine(self.store, "again", enabled=True, started_at=stamp(1000))
        restarted.on_frame(SESSION, [row(1001, ticker="NEW")], stamp(1001))
        self.assertEqual(restarted.status, "DAILY_LIMIT_REACHED")

    def test_checked_in_replay_fixture_deterministic(self):
        from market_radar.paper.replay import replay
        fixture = Path(__file__).parent/"fixtures/paper_baseline_v1.jsonl"
        result = replay(fixture)
        self.assertEqual(result, replay(fixture))
        self.assertEqual(result["trades"][0]["exit_reason"], "TAKE_PROFIT_15")
        self.assertEqual(result["actual_order_calls"], 0)

    def test_background_consumer_uses_evaluation_time_and_next_tick_without_ui(self):
        import threading
        import time
        from types import SimpleNamespace
        from market_radar.providers.kiwoom_ws import KiwoomWebSocket
        path = Path(self.tmp.name)/"background.db"
        base_mono = time.monotonic()
        clock = lambda: instant(stamp(423))+timedelta(seconds=time.monotonic()-base_mono)
        runtime = PaperRuntime(CONFIG, "background", path, clock=clock)
        def seeded_engine(store, run_id, **kwargs):
            engine = PaperEngine(store, run_id, enabled=True)
            engine.on_frame(SESSION, [row(360)], stamp(360))
            return engine
        def wait_for(status):
            deadline = time.monotonic()+3
            while time.monotonic() < deadline and runtime.view()["status"] != status:
                time.sleep(.01)
            self.assertEqual(runtime.view()["status"], status)
        provider = SimpleNamespace(name="kiwoom", mode="live", ws_factory=KiwoomWebSocket, raw_dropped=0)
        with patch("market_radar.paper.runtime.PaperEngine", side_effect=seeded_engine):
            runtime.attach(provider)
            runtime.start()
            try:
                runtime._put(("frame", SESSION, [row(422, price=11)], stamp(422), True, False))
                wait_for("ENTRY_SIGNAL")
                signal_at = runtime.view()["active"]["entry_signal_at"]
                self.assertGreater(instant(signal_at), instant(stamp(422)))
                runtime._put(("tick", "TEST", 11.0, stamp(422.5), True))
                runtime._put(("tick", "TEST", 11.2, signal_at, True))
                entry_at = utc(clock()+timedelta(milliseconds=1))
                runtime._put(("tick", "TEST", 11.3, entry_at, True))
                wait_for("OPEN")
                self.assertEqual(runtime.view()["active"]["entry_price"], 11.3)
                runtime._put(("tick", "TEST", 14, utc(clock()+timedelta(milliseconds=2)), True))
                wait_for("FLAT")
            finally:
                runtime.finish()
        saved = PaperStore(path)
        try:
            trade = saved.entered(SESSION.date)[0]
            self.assertEqual(trade["exit_reason"], "TAKE_PROFIT_15")
            self.assertEqual(trade["entry_price_at"], entry_at)
            self.assertTrue((path.parent/"export"/SESSION.date/"summary.json").exists())
        finally:
            saved.close()


if __name__ == "__main__":
    unittest.main()
