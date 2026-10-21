"""Isolated read-cost and safety regressions; no provider, order or OS calls."""
from dataclasses import replace
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from market_radar.config import CONFIG
from market_radar.models import instant
from market_radar.paper.engine import PaperEngine
from market_radar.paper.runtime import PaperRuntime
from market_radar.paper.storage import PaperStore
from market_radar.paper_rotation_v2.engine import RotationEngine
from market_radar.paper_rotation_v2.runtime import PairedRuntime
from market_radar.paper_rotation_v2.storage import RotationStore
from tests.paper_trading_test import SESSION, stamp, row


class ViewCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("offline only"))
        network.start()
        self.addCleanup(network.stop)

    def test_view_reuses_one_fresh_read_without_writes_for_each_strategy(self):
        for store_type, engine_type in ((PaperStore, PaperEngine), (RotationStore, RotationEngine)):
            with self.subTest(strategy=engine_type.__name__):
                store = store_type(self.root/(engine_type.__name__+".db"))
                try:
                    engine = engine_type(store, "read-cost", enabled=True)
                    engine.on_frame(SESSION, [row(360)], stamp(360))
                    before = store.db.total_changes
                    expected = store.summary(SESSION.date)
                    with patch.object(store, "entered", wraps=store.entered) as read:
                        view = engine.view()
                    self.assertEqual(read.call_count, 1)
                    self.assertEqual(view["summary"], expected)
                    self.assertEqual(view["trades"], 0)
                    self.assertEqual(view["recent"], [])
                    self.assertEqual(store.db.total_changes, before)
                    # A later entry must be visible immediately: no cross-event cache.
                    engine.on_frame(SESSION, [row(422, price=11)], stamp(422))
                    engine.on_tick("TEST", 11.1, stamp(423))
                    self.assertEqual(engine.view()["trades"], 1)
                    self.assertEqual(engine.view()["recent"][0]["entry_price"], 11.1)
                    self.assertEqual(engine.view()["summary"], store.summary(SESSION.date))
                finally:
                    store.close()

    def test_summary_snapshot_keeps_rotation_switch_statistics(self):
        for store_type in (PaperStore, RotationStore):
            with self.subTest(store=store_type.__name__):
                store = store_type(self.root/(store_type.__name__+".db"))
                try:
                    engine = PaperEngine(store, "summary", enabled=True)
                    engine.on_frame(SESSION, [row(360)], stamp(360))
                    engine.on_frame(SESSION, [row(422, price=11)], stamp(422))
                    engine.on_tick("TEST", 11.1, stamp(423))
                    engine.on_tick("TEST", 13, stamp(424))
                    entries = store.entered(SESSION.date)
                    expected = store.summary(SESSION.date)
                    with patch.object(store, "entered", side_effect=AssertionError("duplicate read")):
                        self.assertEqual(store.summary(SESSION.date, entries=entries), expected)
                    if isinstance(store, RotationStore):
                        entries[0]["exit_reason"] = "SUPERIOR_CANDIDATE_SWITCH"
                        with patch.object(store, "entered", side_effect=AssertionError("duplicate read")):
                            summary = store.summary(SESSION.date, entries=entries)
                        self.assertEqual(summary["switch_count"], 1)
                        self.assertEqual(summary["valid_closed_count"], 1)
                finally:
                    store.close()

    def test_view_does_not_keep_rows_from_a_rolled_back_transaction(self):
        store = PaperStore(self.root/"rollback.db")
        try:
            engine = PaperEngine(store, "rollback", enabled=True)
            engine.on_frame(SESSION, [row(360)], stamp(360))
            engine.on_frame(SESSION, [row(422, price=11)], stamp(422))
            engine.on_tick("TEST", 11.1, stamp(423))
            original = engine.view()["recent"]
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with store.db:
                    store.db.execute("UPDATE paper_trades SET current_price=999")
                    self.assertEqual(engine.view()["recent"][0]["current_price"], 999)
                    raise RuntimeError("rollback")
            self.assertEqual(engine.view()["recent"], original)
        finally:
            store.close()

    def test_two_second_boundary_remains_fail_closed_in_both_runtimes(self):
        for runtime_type in (PaperRuntime, PairedRuntime):
            for age in (2.0, 2.001):
                with self.subTest(runtime=runtime_type.__name__, age=age):
                    folder = self.root/(runtime_type.__name__+str(age))
                    folder.mkdir()
                    now = [instant(stamp(360))]
                    kwargs = dict(review_path=folder/"review.db", rotation_enabled=True) if runtime_type is PairedRuntime else {}
                    runtime = runtime_type(replace(CONFIG, collection_mode="Regular Session"),
                        "delay-safety", folder/"paper.db", clock=lambda: now[0], **kwargs)
                    runtime.start()
                    try:
                        runtime._put(("frame", SESSION, [row(360)], stamp(360), True, False))
                        deadline = time.monotonic()+5
                        while runtime.view()["status"] != "WATCHING" and time.monotonic()<deadline:
                            time.sleep(.005)
                        self.assertEqual(runtime.view()["status"], "WATCHING")
                        now[0] = instant(stamp(360+age))
                        runtime._put(("tick", "TEST", 10.1, stamp(360), True))
                        deadline = time.monotonic()+5
                        while not runtime.error and runtime.view().get("active", {}).get("current_price") != 10.1 and time.monotonic()<deadline:
                            time.sleep(.005)
                        self.assertEqual(runtime.error, age>2)
                    finally:
                        runtime.finish()
                    for db_path in folder.glob("*.db"):
                        with closing(sqlite3.connect(db_path)) as db:
                            reason = db.execute("SELECT exit_reason_detail FROM paper_trades").fetchone()[0]
                            self.assertEqual(reason == "paper_processing_delay", age>2)


if __name__ == "__main__":
    unittest.main()
