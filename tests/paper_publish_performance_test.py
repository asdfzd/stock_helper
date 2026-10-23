"""Offline publication freshness, protection and unchanged store-query semantics."""
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from market_radar.config import CONFIG
from market_radar.models import instant, utc
from market_radar.paper.engine import PaperEngine
from market_radar.paper.runtime import PaperRuntime
from market_radar.paper.storage import PaperStore
from market_radar.paper.telemetry import Timings, TimedQueue
from market_radar.paper_rotation_v2.paired import PairedEngine
from market_radar.paper_rotation_v2.runtime import PairedRuntime
from market_radar.paper_rotation_v2.storage import RotationStore
from tests.paper_trading_test import SESSION, row, stamp


def scripted_consumer(folder, runtime_type, run_method=None, *, stale=False):
    """Exercise the real consumer deterministically, with no sleeps/provider."""
    milliseconds = [0]
    clock = lambda: instant(stamp(360 + milliseconds[0]/1000))
    extra = dict(review_path=folder/"review.db", rotation_enabled=True) if runtime_type is PairedRuntime else {}
    runtime = runtime_type(replace(CONFIG, collection_mode="Regular Session"),
        "scripted-publication", folder/"paper.db", clock=clock, **extra)
    timings = Timings(folder/"timings.json")
    runtime.metrics = timings
    wire = TimedQueue(10000, timings)
    actions = [("frame", ("frame", SESSION, [row(360, "A")], stamp(360), True, False))]
    actions += [("idle", 1000 if stale else 200)] * (92 if stale else 6)
    if not stale:
        actions += [("delay", ("tick", "A", 10.1, stamp(357), True)),
                    ("drain1", ("tick", "A", 10.2, stamp(360), True)),
                    ("drain2", ("tick", "A", 10.3, stamp(360), True)),
                    ("error_idle", 200)]
    observations, publications = [], []
    current = ["startup"]

    def snapshot():
        return json.loads(json.dumps(dict(state=runtime.view(), interests=sorted(runtime.interests),
            protection=getattr(runtime, "protection", {}))))

    class ScriptQueue:
        def empty(self):
            return wire.empty()

        def get(self, timeout):
            observations.append(snapshot())
            if not actions:
                current[0] = "stop"
                runtime.request_finish(utc(clock()))
                raise queue.Empty
            current[0], item = actions.pop(0)
            if isinstance(item, int):
                milliseconds[0] += item
                raise queue.Empty
            wire.put_nowait(item)
            return wire.get_nowait()

    runtime.queue = ScriptQueue()
    original_publish = runtime._publish

    def publish(engine):
        original_publish(engine)
        publications.append(dict(cause=current[0], **snapshot()))

    runtime._publish = publish
    with patch("time.monotonic", lambda: milliseconds[0]/1000):
        (run_method.__get__(runtime, runtime_type) if run_method else runtime._run)()
    databases = {}
    for path in folder.glob("*.db"):
        with closing(sqlite3.connect(path)) as db:
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            databases[path.name] = {t: sorted([list(r) for r in db.execute('SELECT * FROM "'+t+'"')],
                key=lambda r:json.dumps(r, sort_keys=True)) for t in tables}
    return dict(observations=observations, publications=publications, databases=databases,
                final=snapshot(), timings=timings.snapshot())


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        deny = patch("requests.sessions.Session.request", side_effect=AssertionError("offline only"))
        deny.start()
        self.addCleanup(deny.stop)

    def test_paired_publication_reads_each_store_once_and_never_keeps_rows(self):
        a = PaperStore(self.root/"paper.db")
        b = RotationStore(self.root/"review.db")
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        engine = PairedEngine(PaperEngine(a, "same-call", enabled=True), b, rotation_enabled=True)
        runtime = PairedRuntime(CONFIG, "same-call", self.root/"unused.db",
                                review_path=self.root/"unused_review.db", rotation_enabled=True)

        def publish():
            expected_view, expected_interests = engine.view(), engine.interests()
            changes = a.db.total_changes, b.db.total_changes
            with patch.object(a, "entered", wraps=a.entered) as ar, \
                 patch.object(b, "entered", wraps=b.entered) as br:
                runtime._publish(engine)
            self.assertEqual((ar.call_count, br.call_count), (1, 1))
            self.assertEqual(runtime.view(), expected_view)
            self.assertEqual(runtime.interests, expected_interests)
            self.assertEqual((a.db.total_changes, b.db.total_changes), changes)

        engine.on_frame(SESSION, [row(360, "A")], stamp(360))
        publish()
        self.assertEqual(runtime.interests, {"A"})
        self.assertEqual(runtime.protection, {})
        engine.on_frame(SESSION, [row(422, "A", price=11)], stamp(422))
        engine.on_tick("A", 11.1, stamp(423))
        publish()
        self.assertEqual(runtime.protection, {"A": SESSION.close_utc})
        original = runtime.view()["recent"]
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with a.db:
                a.db.execute("UPDATE paper_trades SET current_price=999")
                publish()
                self.assertEqual(runtime.view()["recent"][0]["current_price"], 999)
                raise RuntimeError("rollback")
        publish()
        self.assertEqual(runtime.view()["recent"], original)
        engine.gap(stamp(424), "test_gap")
        publish()
        self.assertIsNone(runtime.view()["active"])

    def test_entered_filter_preserves_rows_order_details_and_rollback(self):
        for store_type in (PaperStore, RotationStore):
            with self.subTest(store=store_type.__name__):
                store = store_type(self.root/(store_type.__name__+".db"))
                try:
                    with store.db:
                        for i, price in enumerate((None, 10, None, 0, 12)):
                            store.save(dict(trade_id=str(i), ticker="T"+str(i),
                                session_date_et="2026-09-28" if i < 4 else "2026-09-29",
                                state="CLOSED", created_at="same", entry_price=price,
                                extension_evidence={"source":"synthetic", "index":i}))
                    for day in ("2026-09-28", "2026-09-29", "empty", None):
                        expected = [r for r in store.trades(day) if r["entry_price"] is not None]
                        changes = store.db.total_changes
                        statements = []
                        store.db.set_trace_callback(statements.append)
                        self.assertEqual(store.entered(day), expected)
                        store.db.set_trace_callback(None)
                        self.assertEqual(store.db.total_changes, changes)
                        details = [s for s in statements if "FROM rotation_details" in s]
                        self.assertEqual(len(details), len(expected) if store_type is RotationStore else 0)
                    original = store.entered("2026-09-28")
                    with self.assertRaisesRegex(RuntimeError, "rollback"):
                        with store.db:
                            store.db.execute("UPDATE paper_trades SET entry_price=15 WHERE trade_id='0'")
                            self.assertEqual(len(store.entered("2026-09-28")), 3)
                            raise RuntimeError("rollback")
                    self.assertEqual(store.entered("2026-09-28"), original)
                finally:
                    store.close()

    def test_idle_heartbeat_and_failed_drain_keep_first_failure_and_fresh_state(self):
        for runtime_type in (PaperRuntime, PairedRuntime):
            with self.subTest(runtime=runtime_type.__name__):
                folder = self.root/runtime_type.__name__
                folder.mkdir()
                result = scripted_consumer(folder, runtime_type)
                causes = [p["cause"] for p in result["publications"]]
                self.assertEqual(causes, ["startup", "frame", "idle", "delay", "stop"])
                self.assertEqual(result["publications"][1]["interests"], ["A"])
                self.assertEqual(result["publications"][3]["interests"], [])
                self.assertIn("ERROR", result["final"]["state"]["status"])
                timing = result["timings"]
                self.assertEqual(timing["measurements"]["tick.processing"]["count"], 3)
                self.assertTrue(timing["first_failure_events"][-1]["failed"])
                self.assertEqual(timing["first_failure_events"][-1]["received_utc"], stamp(357))
                with closing(sqlite3.connect(folder/"paper.db")) as db:
                    self.assertEqual(db.execute("SELECT exit_reason_detail FROM paper_trades").fetchone()[0],
                                     "paper_processing_delay")
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM paper_trade_marks").fetchone()[0], 0)

    def test_idle_heartbeat_still_invalidates_stale_watch_and_finish_is_published(self):
        for runtime_type in (PaperRuntime, PairedRuntime):
            with self.subTest(runtime=runtime_type.__name__):
                folder = self.root/runtime_type.__name__
                folder.mkdir()
                result = scripted_consumer(folder, runtime_type, stale=True)
                self.assertEqual(result["final"]["state"]["status"], "CLOSED")
                self.assertEqual(result["final"]["interests"], [])
                self.assertEqual(result["publications"][-1]["cause"], "stop")
                with closing(sqlite3.connect(folder/"paper.db")) as db:
                    self.assertEqual(db.execute("SELECT exit_reason_detail FROM paper_trades").fetchone()[0], "stale_gap")

    def test_publication_updates_switch_interests_and_subscription_protection_immediately(self):
        a = PaperStore(self.root/"paper.db")
        b = RotationStore(self.root/"review.db")
        try:
            engine = PairedEngine(PaperEngine(a,"protection",enabled=True), b, rotation_enabled=True)
            runtime = PairedRuntime(replace(CONFIG,collection_mode="Regular Session"), "protection",
                self.root/"unused.db", review_path=self.root/"unused_review.db",rotation_enabled=True)
            forwarded = []
            class Provider:
                name, mode = "kiwoom", "live"
                catalog = {"A":{"exchange":"NAS"}, "B":{"exchange":"NAS"}}
                raw_dropped = False
                def set_result_tracking(self, value):
                    forwarded.append(value)
                def ws_factory(self, rest, config, stop, sink, logger):
                    return type("WS", (), dict(lock=threading.RLock(),connected=True,gap=False,
                                              active={"A","B"},sink=staticmethod(sink)))()
            provider = Provider()
            runtime.attach(provider)
            ws = provider.ws_factory(None, runtime.config, None, lambda *a:None, None)
            def call(method, *args):
                getattr(engine, method)(*args)
                runtime._publish(engine)
                self.assertEqual(runtime.interests, frozenset(engine.interests()))
                provider.set_result_tracking({})
                self.assertEqual(set(forwarded[-1]), set(runtime.protection))
                self.assertTrue(all(v["tracking_protected_until"]==SESSION.close_utc for v in forwarded[-1].values()))
            call("on_frame",SESSION,[row(360,"A",99,current_leader_rank=5,emerging_rank=5)],stamp(360))
            self.assertEqual(runtime.interests, {"A"})
            self.assertEqual(runtime.protection,{})
            call("on_frame",SESSION,[row(422,"A",100,current_leader_rank=5,emerging_rank=5)],stamp(422))
            call("on_tick","A",100,stamp(423))
            self.assertEqual(runtime.protection,{"A":SESSION.close_utc})
            call("on_frame",SESSION,[row(450,"A",100,current_leader_rank=5,emerging_rank=5),
                                      row(450,"B",20,current_leader_rank=1,emerging_rank=1)],stamp(450))
            self.assertEqual(runtime.interests,{"A","B"})
            ws.sink("FE","B",stamp(451),{"values":{"10":"20.1"}})
            self.assertEqual(runtime.queue.get_nowait(),("tick","B",20.1,stamp(451),True))
            call("on_tick","A",100,stamp(511))
            call("on_frame",SESSION,[row(512,"A",100,current_leader_rank=5,emerging_rank=5,return_1m=-1,return_5m=1),
                                      row(512,"B",21,current_leader_rank=1,emerging_rank=1,return_1m=4,return_5m=6)],stamp(512))
            call("on_tick","A",100,stamp(513))
            self.assertEqual(runtime.protection,{"A":SESSION.close_utc})
            call("on_tick","B",21,stamp(514))
            self.assertEqual(runtime.protection,{"A":SESSION.close_utc,"B":SESSION.close_utc})
            call("on_tick","B",25,stamp(515))
            call("on_tick","A",116,stamp(516))
            call("gap",stamp(518),"synthetic_gap")
            call("finish",stamp(520),"synthetic_stop")
            self.assertEqual(runtime.view()["status"],"CLOSED")
            self.assertEqual(runtime.protection,{"A":SESSION.close_utc,"B":SESSION.close_utc})
        finally:
            a.close()
            b.close()


if __name__ == "__main__":
    unittest.main()
