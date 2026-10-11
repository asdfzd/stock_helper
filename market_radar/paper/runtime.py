"""Opt-in Kiwoom event consumer, independent of the lossy UI snapshot queue.

Wrap the existing WS factory/raw sink only for an enabled, non-Capacity run.
No provider subscription, REST request, ranking, cache or schedule modification.
Queue overflow / processing delay fail closed; never backfill a paper execution.
"""
from datetime import datetime, timezone
import queue
import threading
import time

from runtime_paths import APP_ROOT
from ..models import ET, instant, utc
from ..providers.kiwoom_cache import number
from .engine import PaperEngine
from .storage import PaperStore

DEFAULT_PAPER_PATH = APP_ROOT / "data" / "paper_trading" / "paper_trades.db"


class PaperRuntime:
    def __init__(self, config, run_id, path=DEFAULT_PAPER_PATH, *, clock=None, queue_size=10000):
        self.config, self.run_id, self.path = config, run_id, path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.queue = queue.Queue(queue_size)
        self.done = threading.Event()
        self.ready = threading.Event()
        self.overflow = threading.Event()
        self.input_failure = threading.Event()
        self.lock = threading.Lock()
        self.state = dict(status="WAITING", trades=0, active=None, recent=[], actual_order_calls=0)
        self.interests = frozenset()
        self.provider = None
        self.thread = None
        self.error = False
        self.last_heartbeat = None
        self.finish_at = None

    def view(self):
        with self.lock:
            return dict(self.state)

    def _put(self, event):
        if self.done.is_set() or self.error:
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            self.overflow.set()

    def attach(self, provider):
        if self.config.collection_mode == "Capacity Test" or provider.name != "kiwoom" or provider.mode != "live":
            raise ValueError("Paper live requires Kiwoom Regular/Manual; Capacity is disabled")
        self.provider = provider
        original_factory = provider.ws_factory

        def factory(rest, config, stop, raw_sink, logger):
            def sink(kind, ticker, received, payload):
                raw_sink(kind, ticker, received, payload)  # Original evidence pipeline unchanged.
                if kind == "GAP":
                    self._put(("gap", received, "ws_disconnect"))
                elif kind == "FE" and ticker in self.interests:
                    try:
                        price = number(payload.get("values", {}).get("10"), price=True)
                        with ws.lock:
                            valid = ws.connected and not ws.gap and ticker in ws.active
                        valid = valid and not provider.raw_dropped
                        self._put(("tick", ticker, price, received, valid))
                    except (ValueError, TypeError, OverflowError):
                        self._put(("gap", received, "invalid_live_price"))
            ws = original_factory(rest, config, stop, sink, logger)
            return ws

        provider.ws_factory = factory

    def start(self):
        """Called only after provider.connect acquired the existing collector lock."""
        if self.thread is not None or self.done.is_set():
            return
        self.thread = threading.Thread(target=self._run, name="paper-shadow", daemon=False)
        self.thread.start()
        self.ready.wait(5)

    def frame(self, batch, rows, first_detected, available_at):
        sessions = {s.date: s for s in batch.sessions}
        observations = {o.ticker: o for o in batch.observations}
        enriched = []
        for row in rows:
            o = observations.get(row["ticker"])
            if o is None:
                continue
            sessions[o.session.date] = o.session
            cutoff = o.metadata.get("bar_cutoff_utc")
            # Only a real completed bar at the cutoff, never clock advancement alone.
            completed = next((b.end_utc for b in reversed(o.bars) if cutoff and instant(b.end_utc) == instant(cutoff)), None)
            enriched.append(dict(row, completed_bar_at=completed, bar_cutoff_utc=cutoff,
                                 candidate_detected_at=first_detected.get(o.ticker)))
        day = instant(batch.timestamp_utc).astimezone(ET).date().isoformat()
        session = sessions.get(day)
        if session:
            self._put(("frame", session, enriched, available_at,
                       batch.metadata.get("ws_connected") is True,
                       bool(batch.metadata.get("ws_gap_detected", True) or batch.metadata.get("raw_dropped"))))

    def request_finish(self, at=None):
        # Nonblocking: stop may be called from Qt. Preserve the first stop instant,
        # not the later time when network shutdown/export eventually completes.
        with self.lock:
            if not self.done.is_set():
                self.finish_at = at or utc(self.clock())
                self.done.set()

    def finish(self):
        self.request_finish()
        if self.thread:
            self.thread.join()  # Only controller/worker thread waits; no network in consumer.

    def fail(self):
        self.input_failure.set()

    def _publish(self, engine):
        value = engine.view()
        self.interests = frozenset(engine.interests())
        with self.lock:
            self.state = value

    def _run(self):
        store = engine = None
        try:
            store = PaperStore(self.path)
            engine = PaperEngine(store, self.run_id, enabled=True, collection_mode=self.config.collection_mode,
                                 max_age=self.config.max_data_delay_seconds, started_at=utc(self.clock()))
            self.ready.set()
            self._publish(engine)
            last_wall, last_mono = self.clock(), time.monotonic()
            while not self.done.is_set() or not self.queue.empty():
                now, mono = self.clock(), time.monotonic()
                if self.overflow.is_set():
                    engine.gap(utc(now), "paper_queue_overflow")
                    self.error = True
                    self.overflow.clear()
                if self.input_failure.is_set():
                    engine.gap(utc(now), "paper_input_unavailable")
                    self.error = True
                    self.input_failure.clear()
                # Sleep/resume or wall-clock changes make event ordering unverifiable.
                if mono-last_mono > 5 or abs((now-last_wall).total_seconds()-(mono-last_mono)) > 2:
                    engine.gap(utc(now), "application_gap_or_clock_jump")
                    self.error = True
                last_wall, last_mono = now, mono
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    item = None
                if not self.error and item:
                    kind, *args = item
                    if kind == "frame":
                        evaluation_at = utc(self.clock())
                        if self.done.is_set():
                            continue  # No new selection/signal after the stop request.
                        session, rows, at, connected, gap = args
                        if (now-instant(at)).total_seconds() > 2:
                            engine.gap(utc(now), "paper_processing_delay")
                            self.error = True
                        else:
                            # Signal time is evaluation time, never the older
                            # snapshot/enqueue timestamp (ticks can arrive meanwhile).
                            engine.on_frame(session, rows, evaluation_at, ws_connected=connected, ws_gap=gap)
                    elif kind == "tick":
                        ticker, price, at, valid = args
                        if self.finish_at and instant(at) > instant(self.finish_at):
                            continue
                        if (self.clock()-instant(at)).total_seconds() > 2:
                            engine.gap(utc(self.clock()), "paper_processing_delay")
                            self.error = True
                        else:
                            engine.on_tick(ticker, price, at, valid=valid)
                    else:
                        engine.gap(*args)
                heartbeat_at = utc(self.clock())
                if not self.error and not self.done.is_set() and (self.last_heartbeat is None or mono-self.last_heartbeat >= 1):
                    # Drain queued observations before heartbeat so a queued valid tick
                    # cannot be overtaken by a stale/end-of-session decision.
                    if self.queue.empty():
                        engine.heartbeat(heartbeat_at)
                        self.last_heartbeat = mono
                self._publish(engine)
                if self.error:
                    with self.lock:
                        self.state["status"] = "ERROR · paper data gap; restart required"
            if self.overflow.is_set() or self.input_failure.is_set():
                engine.gap(utc(self.clock()), "paper_pending_input_failure")
                self.error = True
            engine.finish(self.finish_at or utc(self.clock()), "collector_stop")
            self._publish(engine)
            if self.error:
                with self.lock:
                    self.state["status"] = "ERROR · paper data gap; restart required"
            if engine.session:
                store.export(engine.session.date, store.path.parent / "export" / engine.session.date)
        except Exception:
            self.error = True
            if engine is not None:
                try:
                    engine.gap(utc(self.clock()), "paper_consumer_failure")
                except Exception:
                    pass  # Unwritable committed state is handled by restart recovery.
            # Do not propagate paper IO failures to the collector. OPEN state in a
            # committed DB is invalidated by recovery before any next entry.
            with self.lock:
                self.state = dict(status="ERROR · paper storage/processing unavailable", actual_order_calls=0)
        finally:
            self.ready.set()
            if store:
                store.close()
