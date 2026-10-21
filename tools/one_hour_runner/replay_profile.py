"""Offline, instrumented playback; never installed into the live collector.

Source SQLite files are opened only through disposable DB/WAL copies. Playback
preserves received timestamps and FIFO order; it does not reproduce the original
machine load or infer missing enqueue/commit timestamps. WAL is an experiment on
new disposable databases, not a change to production storage or the two-second gate.
"""
from collections import defaultdict
from datetime import timedelta
import argparse
import json
import math
from pathlib import Path
import queue
import sqlite3
import time
from unittest.mock import patch

from market_radar.evidence_io import copied_database
from market_radar.models import Session, instant, utc


def distribution(values):
    values = sorted(values)
    if not values:
        return dict(count=0, mean_ms=None, p95_ms=None, p99_ms=None, max_ms=None)
    return dict(count=len(values), mean_ms=sum(values)/len(values),
                p95_ms=values[math.ceil(.95*len(values))-1],
                p99_ms=values[math.ceil(.99*len(values))-1], max_ms=values[-1])


class Measurements:
    def __init__(self):
        self.samples = defaultdict(list)

    def call(self, name, function, *args, **kwargs):
        before = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            self.samples[name].append((time.perf_counter()-before)*1000)

    def summary(self):
        return {name: distribution(values) for name, values in sorted(self.samples.items())}


def incident_inputs(run, cache):
    """Recover the first WATCHING frame and raw ticks through the recorded fault.

The original frame enqueue instant was not saved. Use its WATCHING evaluation
instant as the playback frame deadline and explicitly record that approximation.
No future frames or synthetic executions are introduced.
"""
    with copied_database(Path(run)/"paper_v1/paper.db") as db:
        trade = dict(db.execute("SELECT * FROM paper_trades ORDER BY created_at LIMIT 1").fetchone())
        start, end = instant(trade["watch_started_at"]), instant(trade["exit_signal_at"])
        expected = [tuple(row) for row in db.execute(
            "SELECT timestamp_utc,price FROM paper_trade_marks ORDER BY observation_id")]
    with copied_database(Path(run)/"radar/stock_helper_market_kiwoom_v2_1.db") as db:
        session_row = db.execute("SELECT * FROM market_sessions WHERE session_date=?", (trade["session_date_et"],)).fetchone()
        session = Session(session_row["session_date"], session_row["open_utc"], session_row["close_utc"])
        first = db.execute("SELECT MIN(timestamp_utc) FROM radar_snapshots").fetchone()[0]
        rows = [dict(r, completed_bar_at=None, candidate_detected_at=first,
                     bar_cutoff_utc=utc(instant(r["bar_cutoff_et"])))
                for r in db.execute("SELECT * FROM radar_snapshots WHERE timestamp_utc=?", (first,))]
    with copied_database(cache) as db:
        raw = list(db.execute("SELECT id,received_utc,payload FROM raw_records WHERE kind='FE' "
                              "AND ticker=? AND received_utc>=? AND received_utc<=? ORDER BY id",
                              (trade["ticker"], utc(start), utc(end))))
    ticks = [("tick", trade["ticker"], abs(float(json.loads(r["payload"])["values"]["10"])), r["received_utc"], True)
             for r in raw]
    return dict(start=start, end=end, session=session, rows=rows, ticks=ticks,
                expected=expected, run_id=trade["radar_run_id"], ticker=trade["ticker"])


def profile(inputs, output, *, journal="default"):
    """Use the unchanged PairedRuntime, engines and delay checks in real time."""
    from market_radar.config import CONFIG
    from market_radar.paper.engine import PaperEngine
    from market_radar.paper_rotation_v2.engine import RotationEngine
    from market_radar.paper_rotation_v2.runtime import PairedRuntime

    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    measure = Measurements()
    real_connect = sqlite3.connect
    baseline_mono = time.perf_counter()
    wall = lambda: inputs["start"]+timedelta(seconds=time.perf_counter()-baseline_mono)

    class Connection(sqlite3.Connection):
        label = None

        def execute(self, sql, *args, **kwargs):
            if self.label is None:
                return super().execute(sql, *args, **kwargs)
            kind = "read" if sql.lstrip().upper().startswith(("SELECT", "PRAGMA")) else "write"
            return measure.call(self.label+".sql_"+kind, super().execute, sql, *args, **kwargs)

        def __exit__(self, *args):
            action = "commit" if self.in_transaction and args[0] is None else "rollback" if self.in_transaction else "transaction_noop"
            return measure.call(self.label+"."+action, super().__exit__, *args)

        def commit(self):
            return measure.call(self.label+".explicit_commit", super().commit)

    def connect(database, *args, **kwargs):
        if str(database) not in {str(output/"paper.db"), str(output/"rotation.db")}:
            return real_connect(database, *args, **kwargs)
        con = real_connect(database, *args, factory=Connection, **kwargs)
        con.label = "paper" if Path(database).name == "paper.db" else "rotation"
        if journal == "wal_full":
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=FULL")
        return con

    def timed_engine(cls, label):
        class Instrumented(cls):
            pass
        for name in ("on_tick", "on_frame", "heartbeat", "gap", "finish", "view"):
            original = getattr(cls, name)
            def method(self, *args, _method=original, _name=name, **kwargs):
                return measure.call(label+"."+_name, _method, self, *args, **kwargs)
            setattr(Instrumented, name, method)
        return Instrumented

    event_times = []
    current_event = None
    maximum_queue = 0
    original_publish = PairedRuntime._publish

    def publish(runtime, engine):
        nonlocal current_event
        original_publish(runtime, engine)
        if current_event is not None:
            finished = wall()
            current_event["finished_utc"] = utc(finished)
            measure.samples["receipt_to_processing_end"].append((finished-instant(current_event["received_utc"])).total_seconds()*1000)
            measure.samples["event_processing"].append((time.perf_counter()-current_event.pop("dequeued_mono"))*1000)
            event_times.append(current_event)
            current_event = None

    class TimedQueue(queue.Queue):
        def _put(self, item):
            nonlocal maximum_queue
            now = time.perf_counter()
            stamp = item[3] if item[0] in {"frame", "tick"} else item[1]
            measure.samples["enqueue_after_receipt"].append((wall()-instant(stamp)).total_seconds()*1000)
            super()._put((now, utc(wall()), item))
            maximum_queue = max(maximum_queue, len(self.queue))

        def _get(self):
            nonlocal current_event
            before = time.perf_counter()
            enqueued, enqueued_utc, item = super()._get()
            measure.samples["queue_wait"].append((before-enqueued)*1000)
            stamp = item[3] if item[0] in {"frame", "tick"} else item[1]
            measure.samples["dequeue_after_receipt"].append((wall()-instant(stamp)).total_seconds()*1000)
            current_event = dict(kind=item[0], received_utc=stamp, enqueued_utc=enqueued_utc,
                                 dequeued_utc=utc(wall()), dequeued_mono=before, queue_length=len(self.queue))
            measure.samples["dequeue_overhead"].append((time.perf_counter()-before)*1000)
            return item

    runtime = PairedRuntime(CONFIG, inputs["run_id"], output/"paper.db", review_path=output/"rotation.db",
                            rotation_enabled=True, clock=wall)
    runtime.queue = TimedQueue(10000)
    first_error_at = None
    with patch.object(PairedRuntime, "_publish", publish), \
         patch("sqlite3.connect", side_effect=connect), \
         patch("market_radar.paper_rotation_v2.runtime.PaperEngine", timed_engine(PaperEngine, "paper")), \
         patch("market_radar.paper_rotation_v2.paired.RotationEngine", timed_engine(RotationEngine, "rotation")):
        runtime.start()
        # Shift the playback origin after database initialization so startup disk
        # cost is not charged to an already-running session's first event.
        baseline_mono = time.perf_counter()
        runtime._put(("frame", inputs["session"], inputs["rows"], utc(inputs["start"]), True, False))
        for tick in inputs["ticks"]:
            deadline = baseline_mono+(instant(tick[3])-inputs["start"]).total_seconds()
            while time.perf_counter() < deadline:
                time.sleep(min(.005, max(0, deadline-time.perf_counter())))
            runtime._put(tick)
            if runtime.error and first_error_at is None:
                first_error_at = utc(wall())
        # Allow the final recorded ticks to drain without changing the source's
        # official session close or runtime's two-second threshold.
        while not runtime.queue.empty() and time.perf_counter()-baseline_mono < 40:
            time.sleep(.01)
        runtime.request_finish(utc(inputs["end"])); runtime.finish()
    with real_connect(output/"paper.db") as db:
        marks = db.execute("SELECT timestamp_utc,price FROM paper_trade_marks ORDER BY observation_id").fetchall()
        events = db.execute("SELECT timestamp_utc,event_type,evidence_json FROM paper_trade_events").fetchall()
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    source_ticks = [(tick[3], tick[2]) for tick in inputs["ticks"]]
    result = dict(journal_experiment=journal, actual_journal_mode=mode, error=runtime.error,
        sidecar_error=runtime.sidecar_error, first_error_observed_at=first_error_at,
        source_tick_count=len(source_ticks), processed_tick_count=len(marks),
        processed_marks_match_source_prefix=marks == source_ticks[:len(marks)], events=events,
        sqlite_integrity=integrity, measurements=measure.summary(),
        maximum_queue_length=maximum_queue, event_times=event_times,
        limitations=["Original enqueue timestamps and machine load were not recorded",
                     "First frame availability approximated by persisted WATCHING timestamp",
                     "Timings include instrumentation; engine times include SQLite times; do not sum nested metrics",
                     "No live market, original Radar workload, or automatic shutdown is replayed"])
    (output/"profile.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--journal", choices=("default", "wal_full"), default="default")
    args = parser.parse_args()
    result = profile(incident_inputs(args.run, args.cache), args.output, journal=args.journal)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
