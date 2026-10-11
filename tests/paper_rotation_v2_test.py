"""Synthetic/offline tests only. All writes use temporary directories."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests.paper_trading_test import SESSION, stamp, row
from market_radar.config import CONFIG
from market_radar.paper.engine import PaperEngine
from market_radar.paper.storage import PaperStore
from market_radar.paper_rotation_v2.engine import RotationEngine
from market_radar.paper_rotation_v2.paired import PairedEngine
from market_radar.paper_rotation_v2.storage import RotationStore
from market_radar.paper_rotation_v2.runtime import PairedRuntime
from market_radar.paper_rotation_v2.comparison import comparisons
from market_radar.paper_rotation_v2.replay import replay


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = PaperStore(self.root/"baseline.db")
        self.review = RotationStore(self.root/"rotation.db")
        self.addCleanup(self.base.close)
        self.addCleanup(self.review.close)
        self.engine = PairedEngine(PaperEngine(self.base, "test", enabled=True), self.review, rotation_enabled=True)
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("network/order forbidden"))
        self.request = self.network.start()
        self.addCleanup(self.network.stop)
        self.events = []

    def frame(self, at, rows):
        self.events.append(dict(event="frame", session=asdict(SESSION), at=stamp(at), rows=rows))
        self.engine.on_frame(SESSION, rows, stamp(at))
        self.assertFalse(self.engine.review_error)

    def tick(self, at, ticker, price, **kwargs):
        self.events.append(dict(event="tick", at=stamp(at), ticker=ticker, price=price, **kwargs))
        self.engine.on_tick(ticker, price, stamp(at), **kwargs)
        self.assertFalse(self.engine.review_error)

    def opened(self):
        self.frame(360, [row(360, "A", 99, current_leader_rank=5, emerging_rank=5)])
        self.frame(422, [row(422, "A", 100, current_leader_rank=5, emerging_rank=5)])
        self.tick(423, "A", 100)
        self.assertEqual(self.engine.rotation.active["state"], "OPEN")

    def signal(self, start=450, target="B", current_changes=None, candidate_changes=None):
        current = self.engine.rotation.active["ticker"]
        price = self.engine.rotation.active["entry_price"]
        self.tick(start-1, current, price)
        self.frame(start, [row(start, current, price, current_leader_rank=5, emerging_rank=5),
                           row(start, target, 20, current_leader_rank=1, emerging_rank=1)])
        self.tick(start+61, current, price)
        old = dict(current_leader_rank=5, emerging_rank=5, return_1m=-1, return_5m=1)
        new = dict(current_leader_rank=1, emerging_rank=1, return_1m=4, return_5m=6)
        old.update(current_changes or {})
        new.update(candidate_changes or {})
        self.frame(start+62, [row(start+62, current, price, **old), row(start+62, target, 21, **new)])

    def switched(self, start=450, target="B"):
        current = self.engine.rotation.active["ticker"]
        price = self.engine.rotation.active["entry_price"]
        self.signal(start, target)
        self.tick(start+63, current, price)
        self.tick(start+64, target, 21)

    def test_v1_exact_replay_results_preserved_with_rotation_on_and_off(self):
        from market_radar.paper.replay import replay as v1_replay
        fixture = Path(__file__).parent/"fixtures/paper_baseline_v1.jsonl"
        expected = v1_replay(fixture)
        self.assertEqual(replay(fixture)["baseline"], expected)
        self.assertEqual(replay(fixture, rotation_enabled=False)["baseline"], expected)

    def test_slightly_better_strong_current_does_not_switch(self):
        self.opened()
        self.signal(current_changes=dict(return_1m=3.9, return_5m=5.9))
        self.assertIsNone(self.engine.rotation.pending_switch)

    def test_all_dominance_conditions_switch_and_baseline_holds(self):
        self.opened()
        self.switched()
        trades = self.review.entered(SESSION.date)
        self.assertEqual([t["ticker"] for t in trades], ["A", "B"])
        self.assertEqual(trades[0]["exit_reason"], "SUPERIOR_CANDIDATE_SWITCH")
        self.assertEqual(trades[1]["rotation_entry_price"], 21)
        self.assertEqual(self.engine.baseline.active["ticker"], "A")
        self.assertIsNone(self.engine.baseline.active.get("exit_reason"))
        evidence = self.review.documents("rotation_switches")[0]["evidence"]
        self.assertEqual(evidence["rule"], "material_evidence_dominance")
        self.assertEqual(evidence["current_weakness"], ["return_1m_nonpositive"])

    def test_current_weakness_required_even_large_dominance(self):
        self.opened()
        self.signal(current_changes=dict(return_1m=.1, return_5m=.1))
        self.assertIsNone(self.engine.rotation.pending_switch)

    def test_each_dominance_gate_is_required(self):
        self.opened()
        self.signal(candidate_changes=dict(return_1m=0))
        policy = self.engine.rotation.policy
        watch = self.engine.rotation.rotation_watch
        current = row(512, "A", 100, current_leader_rank=5, emerging_rank=5, return_1m=-1, return_5m=1)
        candidate = row(512, "B", 21, current_leader_rank=1, emerging_rank=1, return_1m=4, return_5m=6)
        self.assertIsNotNone(policy.dominance(candidate, current, watch, stamp(512), 90))
        failures = [dict(current_leader_rank=11), dict(emerging_rank=11), dict(current_leader_rank=5),
            dict(emerging_rank=5), dict(return_1m=-1), dict(return_5m=1), dict(return_1m=None),
            dict(price=19), dict(price_age_ms=91000), dict(ws_gap_detected=True),
            dict(completed_bar_at=stamp(420)), dict(completed_bar_at=None)]
        for change in failures:
            with self.subTest(change=change):
                self.assertIsNone(policy.dominance(dict(candidate, **change), current, watch, stamp(512), 90))
        for change in [dict(current_leader_rank=None), dict(return_5m=None), dict(price_age_ms=91000), dict(ws_gap_detected=True)]:
            with self.subTest(current=change):
                self.assertIsNone(policy.dominance(candidate, dict(current, **change), watch, stamp(512), 90))

    def test_top10_departure_is_weakness(self):
        self.opened()
        self.signal(current_changes=dict(emerging_rank=11, return_1m=1))
        self.assertIsNotNone(self.engine.rotation.pending_switch)

    def test_missing_current_is_not_evidence_of_weakness(self):
        self.opened()
        self.signal(candidate_changes=dict(return_1m=0))
        self.frame(513, [row(513, "B", 21)])
        self.assertIsNone(self.engine.rotation.pending_switch)

    def test_switch_signal_exit_entry_strictly_separated_and_first_prices(self):
        self.opened()
        self.signal()
        self.tick(512, "A", 101)
        self.tick(512.5, "B", 40)  # Before source exit: must never become entry.
        self.tick(513, "A", 102)
        self.tick(513, "B", 50)  # Same receipt timestamp cannot fill both legs.
        self.assertEqual(self.engine.rotation.active["state"], "ENTRY_SIGNAL")
        self.tick(513.001, "B", 22)
        switch = self.review.documents("rotation_switches")[0]
        self.assertEqual(switch["rotation_exit_price"], 102)
        self.assertEqual(switch["rotation_entry_price"], 22)
        self.assertLess(switch["switch_signal_at"], switch["rotation_exit_at"])
        self.assertLess(switch["rotation_exit_at"], switch["rotation_entry_at"])

    def test_rotation_entries_count_toward_four_and_no_fifth_exit(self):
        self.opened()
        for start, ticker in [(450, "B"), (540, "C"), (630, "D")]:
            self.switched(start, ticker)
        self.assertEqual(len(self.review.entered(SESSION.date)), 4)
        self.signal(720, "E")
        self.assertIsNone(self.engine.rotation.pending_switch)
        self.assertEqual(self.engine.rotation.active["ticker"], "D")

    def test_same_ticker_reentry_forbidden(self):
        self.opened()
        self.switched()
        self.signal(540, "A")
        self.assertIsNone(self.engine.rotation.pending_switch)
        self.tick(604, "B", 25)
        self.frame(605, [row(605, "A", 110)])
        self.assertIsNone(self.engine.rotation.active)

    def test_sl_then_original_tp_and_recovery(self):
        self.opened()
        self.tick(424, "A", 89)
        self.tick(425, "A", 100)
        self.tick(426, "A", 116)
        for post in self.review.documents("paper_post_exit"):
            self.assertEqual(post["original_entry_price"], 100)
            self.assertAlmostEqual(post["original_tp_price"], 115)
            self.assertEqual(post["original_sl_price"], 90)
            self.assertTrue(post["recovered_entry_after_sl"])
            self.assertEqual(post["recovered_entry_at"], stamp(425))
            self.assertTrue(post["original_tp_hit_after_sl"])
            self.assertEqual(post["original_tp_hit_after_sl_at"], stamp(426))
            self.assertEqual(post["max_price_after_sl"], 116)
            self.assertEqual(post["tags"], ["STOP_THEN_ORIGINAL_TP"])

    def test_tp_extra_runup_and_chronological_drawdown(self):
        self.opened()
        self.tick(424, "A", 116)
        self.tick(425, "A", 130)
        self.tick(426, "A", 100)
        post = self.review.documents("paper_post_exit")[0]
        self.assertEqual(post["max_price_after_tp"], 130)
        self.assertEqual(post["lowest_price_after_tp"], 100)
        self.assertAlmostEqual(post["extra_runup_after_tp_pct"], (130/116-1)*100)
        self.assertAlmostEqual(post["max_drawdown_after_tp_pct"], (100/130-1)*100)
        self.assertEqual(post["tags"], ["TP_THEN_CONTINUED_RUN", "TP_THEN_REVERSAL"])
        self.assertEqual(post["observation_count"], 2)  # Closing tick isn't reused.

    def test_post_exit_future_data_cannot_change_decisions(self):
        self.opened()
        self.switched()
        before = self.review.trades()
        self.tick(515, "A", 1000)
        self.assertEqual(self.review.trades(), before)
        self.assertEqual(self.engine.rotation.active["ticker"], "B")
        self.assertTrue(next(p for p in self.review.documents("paper_post_exit") if p["strategy_version"] == "paper_rotation_v2")["original_tp_hit_after_exit"])
        # Even poisoned review aggregates cannot feed into the execution engine.
        with self.review.db:
            self.review.db.execute("UPDATE paper_post_exit SET detail_json='{}'")
        self.tick(516, "B", 21.1)
        self.assertEqual(self.engine.rotation.active["ticker"], "B")

    def test_same_replay_same_results_and_zero_network_orders(self):
        self.opened()
        self.switched()
        self.tick(515, "A", 120)
        self.tick(516, "B", 25)
        self.events.append(dict(event="finish", at=stamp(520)))
        fixture = self.root/"replay.jsonl"
        fixture.write_text("\n".join(json.dumps(e) for e in self.events), encoding="utf-8")
        first = replay(fixture)
        self.assertEqual(first, replay(fixture))
        from market_radar.paper.replay import replay as baseline_replay
        self.assertEqual(first["baseline"], baseline_replay(fixture))
        off = replay(fixture, rotation_enabled=False)
        self.assertEqual(first["baseline"], off["baseline"])
        self.assertEqual(off["rotation_trades"], [])
        self.assertEqual(first["actual_order_calls"], 0)
        self.request.assert_not_called()

    def test_comparison_links_same_start_and_compounds_rotation_chain(self):
        self.opened()
        self.switched()
        self.tick(515, "A", 120)
        self.tick(516, "B", 25)
        result = comparisons(self.review.documents("review_trades"), self.review.documents("paper_post_exit"), self.review.documents("rotation_switches"))
        root = next(r for r in result if "baseline_v1_return" in r)
        self.assertEqual(root["match_status"], "EXACT_SAME_ENTRY")
        self.assertAlmostEqual(root["baseline_v1_return"], 20)
        self.assertAlmostEqual(root["rotation_v2_return"], (25/21-1)*100)
        switch = next(r for r in result if "switch_id" in r)
        self.assertTrue(switch["old_ticker_original_tp_after_switch"])
        self.assertAlmostEqual(switch["new_ticker_mfe_pct"], (25/21-1)*100)

    def test_export_has_review_and_both_strategies(self):
        self.opened()
        self.switched()
        self.engine.finish(stamp(520))
        folder = self.root/"export"
        self.review.export_review(SESSION.date, folder, self.base.summary(SESSION.date))
        expected = {"paper_trades.csv", "paper_post_exit.csv", "paper_post_exit_marks.csv",
                    "paper_rotation_comparison.csv", "paper_rotation_switches.csv", "paper_daily_summary.json"}
        self.assertEqual({p.name for p in folder.iterdir()}, expected)
        text = (folder/"paper_trades.csv").read_text(encoding="utf-8")
        self.assertIn("paper_v1_baseline", text)
        self.assertIn("paper_rotation_v2", text)
        self.assertIn("switch_evidence_json", text)
        self.assertIn("entry_reason", text)

    def test_tracking_stops_at_early_official_close_not_wallclock_1600(self):
        self.opened()
        short = replace(SESSION, close_utc=stamp(500))
        self.engine.on_frame(short, [], stamp(423.5))
        self.tick(424, "A", 89)
        self.tick(499, "A", 101)
        self.engine.heartbeat(stamp(500))
        self.tick(501, "A", 1000)
        for post in self.review.documents("paper_post_exit"):
            self.assertEqual(post["post_exit_max_price"], 101)
            self.assertEqual(post["tracking_status"], "OBSERVED_TO_CLOSE")

    def test_interrupted_and_gap_tracking_are_explicit(self):
        self.opened()
        self.tick(424, "A", 89)
        self.engine.gap(stamp(425), "ws_disconnect")
        self.tick(426, "A", 120)
        self.engine.finish(stamp(430))
        for post in self.review.documents("paper_post_exit"):
            self.assertEqual(post["tracking_status"], "INCOMPLETE")
            self.assertIn("ws_disconnect", post["gap_reasons"])
            self.assertIn("collector_stopped_before_close", post["gap_reasons"])

    def test_session_end_has_no_invented_post_exit_window(self):
        self.opened()
        short = replace(SESSION, close_utc=stamp(450))
        self.engine.on_frame(short, [], stamp(424))
        self.engine.heartbeat(stamp(450))
        for post in self.review.documents("paper_post_exit"):
            self.assertEqual(post["exit_reason"], "SESSION_END")
            self.assertEqual(post["tracking_status"], "NO_POST_EXIT_WINDOW")
            self.assertIsNone(post["post_exit_max_price"])

    def test_restart_cancels_pending_switch_and_preserves_daily_guard(self):
        self.opened()
        self.signal()
        restarted = RotationEngine(self.review, "new", enabled=True, started_at=stamp(513))
        restarted.on_frame(SESSION, [row(514, "A", 110)], stamp(514))
        self.assertIsNone(restarted.active)
        self.assertEqual(len(self.review.entered(SESSION.date)), 1)
        self.assertEqual(self.review.documents("rotation_switches")[0]["stage"], "CANCELLED")

    def test_invalid_switch_tick_cancels_without_fake_price(self):
        self.opened()
        self.signal()
        self.tick(513, "A", 100, valid=False)
        self.assertIsNone(self.engine.rotation.active)
        self.assertIsNone(self.review.entered(SESSION.date)[0]["exit_price"])
        self.assertEqual(self.review.documents("rotation_switches")[0]["stage"], "CANCELLED")
        source = next(t for t in self.review.documents("review_trades") if t["strategy_version"] == "paper_rotation_v2")
        self.assertFalse(source["switched"])
        self.assertTrue(source["switch_signaled"])

    def test_sidecar_fault_does_not_change_baseline(self):
        self.opened()
        with patch.object(self.engine.tracker, "on_tick", side_effect=OSError("synthetic")):
            self.engine.on_tick("A", 116, stamp(424))
        self.assertTrue(self.engine.review_error)
        self.assertEqual(self.base.entered(SESSION.date)[0]["exit_reason"], "TAKE_PROFIT_15")

    def test_baseline_database_cannot_be_opened_as_rotation(self):
        with self.assertRaises(ValueError):
            RotationStore(self.root/"baseline.db")
        with self.assertRaises(ValueError):
            PairedRuntime(CONFIG, "bad", self.root/"baseline.db", review_path=self.root/"baseline.db")

    def test_ui_rotation_off_and_capacity_forces_both_off(self):
        from PySide6.QtWidgets import QApplication
        from market_radar.paper_rotation_v2.ui import PairedPaperPanel
        app = QApplication.instance() or QApplication([])
        panel = PairedPaperPanel()
        self.assertFalse(panel.rotation_enable.isChecked())
        panel.rotation_enable.setChecked(True)
        self.assertTrue(panel.enable.isChecked())
        panel.render(self.engine.view(), busy=True, collection_mode="Regular Session")
        self.assertFalse(panel.rotation_enable.isEnabled())
        panel.render(None, busy=False, collection_mode="Capacity Test")
        self.assertFalse(panel.enable.isChecked())
        self.assertFalse(panel.rotation_enable.isChecked())
        self.assertIn("paper_rotation_v2", panel.rotation.status.text())
        panel.close()

    def test_ws_protection_extended_to_close_without_changing_other_symbols(self):
        from types import SimpleNamespace
        from market_radar.providers.kiwoom_ws import KiwoomWebSocket
        received = []
        provider = SimpleNamespace(name="kiwoom", mode="live", ws_factory=KiwoomWebSocket,
            raw_dropped=0, catalog={"A": {"exchange": "ND"}}, set_result_tracking=lambda p: received.append(p))
        runtime = PairedRuntime(CONFIG, "test", self.root/"runtime.db")
        runtime.attach(provider)
        self.opened()
        self.tick(424, "A", 116)
        runtime._publish(self.engine)
        original = {"X": dict(exchange="NY", tracking_protected_until=stamp(600))}
        provider.set_result_tracking(original)
        self.assertEqual(received[0]["A"]["tracking_protected_until"], SESSION.close_utc)
        self.assertEqual(received[0]["X"], original["X"])
        self.assertNotIn("A", original)
        self.assertIn("A", runtime.interests)

    def test_sidecar_reopen_and_pending_target_recovery(self):
        self.opened()
        self.signal()
        self.tick(513, "A", 100)
        other = RotationStore(self.root/"rotation.db")
        try:
            restarted = RotationEngine(other, "restart", enabled=True, started_at=stamp(514))
            self.assertIsNone(restarted.active)
            self.assertEqual(other.documents("rotation_switches")[0]["stage"], "CANCELLED")
            self.assertEqual(len(other.entered(SESSION.date)), 1)
        finally:
            other.close()

    def test_equal_time_post_exit_prices_preserve_trajectory_arrival_order(self):
        self.opened()
        self.tick(424, "A", 116)
        self.tick(425, "A", 130)
        self.tick(425, "A", 100)
        post = self.review.documents("paper_post_exit")[0]
        self.assertEqual(post["observation_count"], 2)
        self.assertAlmostEqual(post["max_drawdown_after_tp_pct"], (100/130-1)*100)

    def test_exact_time_counterfactual_and_missing_price_remain_distinct(self):
        self.opened()
        self.switched()
        self.tick(516, "A", 110)
        self.tick(516, "B", 25)
        marks = [dict(r) for r in self.review.db.execute("SELECT * FROM paper_post_exit_marks ORDER BY observation_id")]
        args = [self.review.documents(t) for t in ("review_trades", "paper_post_exit", "rotation_switches")]
        record = next(r for r in comparisons(*args, marks) if "switch_id" in r)
        self.assertAlmostEqual(record["hold_old_from_switch_return_pct"], 10)
        self.assertAlmostEqual(record["switch_advantage_at_new_exit_pct"], (25/21-1)*100-10)
        missing = next(r for r in comparisons(*args) if "switch_id" in r)
        self.assertIsNone(missing["switch_advantage_at_new_exit_pct"])

    def test_capacity_runtime_rejects_before_creating_files(self):
        from types import SimpleNamespace
        runtime = PairedRuntime(replace(CONFIG, collection_mode="Capacity Test"), "test", self.root/"capacity.db", rotation_enabled=True)
        with self.assertRaises(ValueError):
            runtime.attach(SimpleNamespace(name="kiwoom", mode="live"))
        self.assertFalse((self.root/"capacity.db").exists())
        self.assertFalse(runtime.review_path.exists())

    def test_background_shared_consumer_uses_one_evaluation_time(self):
        import time
        from datetime import timedelta
        from types import SimpleNamespace
        from market_radar.models import instant, utc
        from market_radar.providers.kiwoom_ws import KiwoomWebSocket
        initial = time.monotonic()
        clock = lambda: instant(stamp(423)) + timedelta(seconds=time.monotonic()-initial)
        path = self.root/"background.db"
        runtime = PairedRuntime(CONFIG, "background", path, clock=clock, rotation_enabled=True)
        def seeded_pair(baseline, store, **kwargs):
            engine = PairedEngine(baseline, store, **kwargs)
            engine.on_frame(SESSION, [row(360)], stamp(360))
            return engine
        def wait_for(status):
            deadline = time.monotonic()+5
            while time.monotonic() < deadline and runtime.view()["status"] != status:
                time.sleep(.01)
            self.assertEqual(runtime.view()["status"], status)
        provider = SimpleNamespace(name="kiwoom", mode="live", ws_factory=KiwoomWebSocket, raw_dropped=0)
        with patch("market_radar.paper_rotation_v2.runtime.PairedEngine", side_effect=seeded_pair):
            runtime.attach(provider)
            runtime.start()
            try:
                runtime._put(("frame", SESSION, [row(422, price=11)], stamp(422), True, False))
                wait_for("ENTRY_SIGNAL")
                signal = runtime.view()["active"]["entry_signal_at"]
                self.assertEqual(signal, runtime.view()["rotation_v2"]["active"]["entry_signal_at"])
                runtime._put(("tick", "TEST", 11, signal, True))
                fill_at = utc(clock()+timedelta(milliseconds=1))
                runtime._put(("tick", "TEST", 11.2, fill_at, True))
                wait_for("OPEN")
                self.assertEqual(runtime.view()["rotation_v2"]["active"]["entry_price_at"], fill_at)
                self.assertEqual(runtime.view()["active"]["entry_price"], 11.2)
                runtime._put(("tick", "TEST", 14, utc(clock()+timedelta(milliseconds=2)), True))
                wait_for("FLAT")
            finally:
                runtime.finish()
        self.assertFalse(runtime.error)
        self.assertFalse(runtime.sidecar_error)
        self.assertTrue((runtime.review_path.parent/"export"/SESSION.date/"paper_daily_summary.json").exists())
        self.assertTrue((path.parent/"export"/SESSION.date/"summary.json").exists())
        self.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
