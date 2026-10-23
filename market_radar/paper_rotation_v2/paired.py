"""Same event/time fan-out; independent positions, daily guards and stores."""
from .engine import RotationEngine
from .tracking import PostExitTracker


class PairedEngine:
    def __init__(self, baseline, review_store, *, rotation_enabled=False, started_at=None, metrics=None):
        self.baseline, self.review_store = baseline, review_store
        self.rotation = RotationEngine(review_store, baseline.run_id, enabled=rotation_enabled,
            max_age=baseline.max_age, started_at=started_at)
        if metrics:
            from ..paper.telemetry import measured_engine
            self.baseline = measured_engine(self.baseline, metrics, "paper")
            self.rotation = measured_engine(self.rotation, metrics, "rotation")
        self.tracker = PostExitTracker(review_store, baseline.max_age)
        self.review_error = False

    @property
    def session(self):
        return self.baseline.session

    def interests(self, *, baseline_entries=None, rotation_entries=None):
        result = (self.baseline.interests(entries=baseline_entries) |
                  self.rotation.interests(entries=rotation_entries))
        if self.session:
            result |= {r["ticker"] for r in self.tracker.records.values()
                       if r["session_date_et"] == self.session.date and r["tracking_status"] == "OBSERVING"}
        return result

    def _apply(self, method, *args, **kwargs):
        # Baseline executes first and never reads review/rotation state.
        getattr(self.baseline, method)(*args, **kwargs)
        if self.review_error:
            return
        try:
            # Review receives the tick before capturing exits made by this tick.
            if method == "on_tick":
                self.tracker.on_tick(*args, **kwargs)
            getattr(self.rotation, method)(*args, **kwargs)
            if self.session:
                self.tracker.capture(self.baseline.store.entered(self.session.date) + self.review_store.entered(self.session.date), self.session)
            if method == "gap":
                self.tracker.gap(args[0], args[1] if len(args) > 1 else "ws_disconnect")
            if method in {"heartbeat", "finish"}:
                self.tracker.finish(args[0], stopped=method == "finish")
        except Exception:
            # A sidecar fault cannot invalidate or stop the baseline strategy.
            self.review_error = True
            try:
                at = args[2] if method in {"on_tick", "on_frame"} else args[0]
                self.rotation.gap(at, "rotation_review_failure")
            except Exception:
                pass  # Persisted OPEN is invalidated on recovery.

    def on_frame(self, *args, **kwargs):
        self._apply("on_frame", *args, **kwargs)

    def on_tick(self, *args, **kwargs):
        self._apply("on_tick", *args, **kwargs)

    def gap(self, *args, **kwargs):
        self._apply("gap", *args, **kwargs)

    def heartbeat(self, *args, **kwargs):
        self._apply("heartbeat", *args, **kwargs)

    def finish(self, *args, **kwargs):
        self._apply("finish", *args, **kwargs)

    def publication(self):
        # The consumer publishes after the action/commits. Reuse just this call's
        # rows for view, protection and interests; never cache across an action,
        # transaction or rollback. Each strategy still owns its separate store.
        baseline_entries = self.baseline._entries()
        rotation_entries = self.rotation._entries()
        value = self.baseline.view(entries=baseline_entries)
        value["rotation_v2"] = self.rotation.view(entries=rotation_entries) if not self.review_error else {"status": "ERROR · sidecar unavailable"}
        value["post_exit_tracking"] = "ERROR" if self.review_error else "ON"
        if self.session:
            value["official_close_utc"] = self.session.close_utc
            value["tracking_tickers"] = sorted({t["ticker"] for t in baseline_entries + rotation_entries})
        return value, self.interests(baseline_entries=baseline_entries, rotation_entries=rotation_entries)

    def view(self):
        return self.publication()[0]
