"""Shared serial event consumer. Frozen v1 remains usable independently.

Frame enrichment, WS parsing and stop fencing are inherited without alteration.
A single evaluation timestamp and ordered FE stream feed both state machines.
"""
import queue
import time
from pathlib import Path

from ..models import instant, utc
from ..paper.runtime import PaperRuntime, DEFAULT_PAPER_PATH
from ..paper.storage import PaperStore
from ..paper.engine import PaperEngine
from .paired import PairedEngine
from .storage import RotationStore


class PairedRuntime(PaperRuntime):
    def __init__(self, config, run_id, path=DEFAULT_PAPER_PATH, *, review_path=None, rotation_enabled=False, **kwargs):
        super().__init__(config, run_id, path, **kwargs)
        self.review_path = Path(review_path) if review_path else Path(path).parent / "paper_rotation_v2" / "review.db"
        if self.review_path.resolve() == Path(path).resolve():
            raise ValueError("Baseline and rotation require separate databases")
        self.rotation_enabled = rotation_enabled
        self.sidecar_error = False
        self.protection = {}

    def attach(self, provider):
        super().attach(provider)
        if not hasattr(provider, "set_result_tracking"):
            return
        original_tracking = provider.set_result_tracking

        def tracking(protected):
            # Reuse the existing protected-subscription mechanism, without changing
            # ranking or slot allocation. Existing entered symbols stay until close.
            merged = dict(protected)
            with self.lock:
                paper = dict(self.protection)
            catalog = getattr(provider, "catalog", {})
            for ticker, close in paper.items():
                existing = merged.get(ticker, {})
                exchange = existing.get("exchange") or catalog.get(ticker, {}).get("exchange")
                if exchange and (not existing.get("tracking_protected_until")
                                 or instant(existing["tracking_protected_until"]) < instant(close)):
                    merged[ticker] = dict(existing, exchange=exchange, tracking_protected_until=close)
            original_tracking(merged)
        provider.set_result_tracking = tracking

    def _publish(self, engine):
        super()._publish(engine)
        with self.lock:
            close = self.state.get("official_close_utc")
            self.protection = {t: close for t in self.state.get("tracking_tickers", [])} if close else {}
            if self.sidecar_error:
                self.state["rotation_v2"] = {"status": "ERROR · sidecar storage/export unavailable"}
                self.state["post_exit_tracking"] = "ERROR"

    def _run(self):
        store = engine = review = None
        try:
            store = PaperStore(self.path, result_tolerance_seconds=self.config.result_tolerance_seconds, metrics=self.metrics)
            engine = PaperEngine(store, self.run_id, enabled=True, collection_mode=self.config.collection_mode,
                                 max_age=self.config.max_data_delay_seconds, started_at=utc(self.clock()))
            try:
                review = RotationStore(self.review_path, result_tolerance_seconds=self.config.result_tolerance_seconds, metrics=self.metrics)
                engine = PairedEngine(engine, review, rotation_enabled=self.rotation_enabled, started_at=utc(self.clock()), metrics=self.metrics)
            except Exception:
                self.sidecar_error = True
                if review:
                    review.close()
                    review = None
            self.ready.set()
            self._publish(engine)
            last_wall, last_mono = self.clock(), time.monotonic()
            while not self.done.is_set() or not self.queue.empty():
                now, mono = self.clock(), time.monotonic()
                publish_required = False
                if self.overflow.is_set():
                    engine.gap(utc(now), "paper_queue_overflow")
                    publish_required = True
                    self.error = True
                    self.overflow.clear()
                if self.input_failure.is_set():
                    engine.gap(utc(now), "paper_input_unavailable")
                    publish_required = True
                    self.error = True
                    self.input_failure.clear()
                # Sleep/resume or wall-clock changes make event ordering unverifiable.
                if mono-last_mono > 5 or abs((now-last_wall).total_seconds()-(mono-last_mono)) > 2:
                    engine.gap(utc(now), "application_gap_or_clock_jump")
                    publish_required = True
                    self.error = True
                last_wall, last_mono = now, mono
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    item = None
                if not self.error and item:
                    publish_required = True
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
                        publish_required = True
                        self.last_heartbeat = mono
                # Keep interests/protected tickers immediate after every action;
                # reuse the published state only when no engine action occurred.
                if publish_required:
                    self._publish(engine)
                elif self.metrics:
                    self.metrics.complete(self.error)
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
            if review and engine.session:
                try:
                    review.export_review(engine.session.date, review.path.parent / "export" / engine.session.date,
                                         store.summary(engine.session.date))
                except Exception:
                    self.sidecar_error = True
                    self._publish(engine)
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
            if review:
                review.close()
            if store:
                store.close()
            if self.metrics:
                self.metrics.close()
