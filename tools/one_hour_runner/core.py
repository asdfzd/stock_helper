"""Clock-driven supervisor using only public Controller/schedule interfaces."""
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import threading
import uuid

from market_radar.config import CONFIG, DEFAULT_SHARED_CACHE_PATH
from market_radar.models import instant, utc
from market_radar.scheduling import SessionSchedule, SystemClock

from . import __version__
from .system import RunBlocked, WindowsShutdown


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def runner_config(session, cache=DEFAULT_SHARED_CACHE_PATH):
    return replace(CONFIG, collection_mode="Regular Session", max_ws_symbols=150,
                   data_dir=Path(session), historical_cache_path=Path(cache))


def plan_for(config, clock, calendar=None):
    schedule = SessionSchedule(config, calendar, clock)
    if clock.now() >= schedule.open:
        raise RunBlocked("late_launch")
    stop_at = schedule.open + timedelta(minutes=65)
    if stop_at > schedule.close:
        raise RunBlocked("session_shorter_than_65_minutes")
    return dict(session_date=schedule.snapshot()["session_date_et"],
                calendar_source=schedule.snapshot()["calendar_source"],
                open=utc(schedule.open), opening_hour_end=utc(schedule.open+timedelta(minutes=60)),
                stop=utc(stop_at), official_close=utc(schedule.close))


def threads_finished(controller):
    if controller.busy or not controller.worker or controller.worker.running:
        return False
    objects = [controller.paper, controller.worker.provider,
               getattr(controller.worker.provider, "ws", None)]
    for obj in objects:
        for name in ("thread", "io_thread"):
            thread = getattr(obj, name, None)
            if thread is not None and thread.is_alive():
                return False
    return True


def paper_health(paper):
    """Read the public view too: a paired-engine fault need not set sidecar_error."""
    result = dict(paper_error=getattr(paper, "error", None),
                  sidecar_error=getattr(paper, "sidecar_error", None),
                  rotation_view_error=None, view_unavailable=False)
    view = getattr(paper, "view", None)
    if callable(view):
        try:
            state = view()
            result["rotation_view_error"] = (state.get("post_exit_tracking") == "ERROR" or
                str(state.get("rotation_v2", {}).get("status", "")).startswith("ERROR"))
        except Exception:
            result["view_unavailable"] = True
    result["failed"] = paper is None or any(result.values())
    return result


class Runner:
    def __init__(self, root, *, clock=None, calendar=None, controller_factory=None,
                 publisher=None, shutdown=None, guard=None, no_shutdown=False, simulated=False):
        self.clock = clock or SystemClock()
        self.root = Path(root)
        self.calendar = calendar
        self.controller_factory = controller_factory
        self.publisher = publisher
        self.shutdown = shutdown or WindowsShutdown()
        self.guard = guard
        self.no_shutdown = no_shutdown or simulated
        self.simulated = simulated
        self.state = "STARTING"
        self.controller = None
        self.folder = None
        self.summary = None
        self.plan = None
        self.archive = None
        self.reason = None
        self.failures = []
        self.requested_stop_at = None
        self.last_wall = self.last_mono = None
        self.cancel_event = threading.Event()
        self.finalized = False
        self._finalizing = False
        self.shutdown_at = None
        self.shutdown_deadline_mono = None
        # Independent of clocks and of an adapter returning successfully. Once we
        # cross the OS boundary, only a confirmed cancellation clears this flag.
        self.shutdown_may_be_pending = False
        self._shutdown_lock = threading.RLock()
        self.next_legacy_check = 0

    def _stop_controller(self, reason):
        self.controller.stop()

    def _clock_observation(self, **sample):
        pass

    def _collection_failures(self):
        return self.failures

    def _collection_due(self, now, mono):
        return now >= instant(self.plan['stop'])

    def fail(self, reason):
        if reason not in self.failures:
            self.failures.append(reason)
        self.reason = reason

    def start(self, *, cache=DEFAULT_SHARED_CACHE_PATH, provider=None):
        if self.folder is not None:
            raise RunBlocked("runner_already_started")
        self.folder = self.root/("run_" + uuid.uuid4().hex)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.summary = dict(runner_version=__version__, summary_schema=1, simulated=self.simulated,
                            status="STARTING", shutdown_eligible=False, failures=[],
                            run_id=None, session_date=None, planned_short_session=True)
        try:
            if self.guard:
                self.guard.acquire()
            config = runner_config(self.folder/"radar", cache)
            self.plan = plan_for(config, self.clock, self.calendar)
            self.summary.update(plan=self.plan, session_date=self.plan["session_date"])
            atomic_json(self.folder/"one_hour_run_summary.json", self.summary)
            if self.controller_factory is None:
                from market_radar.controller import CollectorController
                factory = CollectorController
            else:
                factory = self.controller_factory
            self.controller = factory(self.folder/"master.db", clock=self.clock, calendar=self.calendar)
            self.controller.paper_path = self.folder/"paper_v1"/"paper.db"
            self.controller.paper_review_path = self.folder/"rotation_v2"/"review.db"
            self.controller.opening_hour_path = self.folder/"opening_hour"/"opening_hour.db"
            if not self.controller.start(config, provider, fresh_directory=False, paper_enabled=True,
                                         paper_rotation_enabled=True, opening_hour_enabled=True):
                raise RunBlocked("controller_start_rejected")
            self.summary["run_id"] = self.controller.lifecycle["run_id"]
            self.last_wall, self.last_mono = self.clock.now(), self.clock.monotonic()
            self.state = "ARMED"
        except Exception as exc:
            self.fail(str(exc) if isinstance(exc, RunBlocked) else "startup_"+type(exc).__name__)
            # start() could have started a worker before a later failure. Never abandon it.
            if self.controller and self.controller.busy:
                self._stop_controller('startup_failure')
                self.state = "FINALIZING"
            else:
                self._failure_summary()

    def request_cancel(self):
        self.cancel_event.set()

    def _failure_summary(self):
        if self.shutdown_may_be_pending:
            self.cancel_shutdown()
        pending = self.shutdown_may_be_pending
        self.state = "SHUTDOWN_PENDING" if pending else "ERROR"
        self.finalized = True
        if self.summary is not None:
            self.summary.update(status="FAILED", shutdown_eligible=False, failures=list(self.failures),
                                shutdown_state=self.state)
            try:
                atomic_json(self.folder/"one_hour_run_summary.json", self.summary)
            except Exception:
                self.fail("failure_summary_write_failed")
        if pending:
            self.reason = "shutdown_cancel_failed"

    def tick(self):
        if self.finalized:
            return
        c = self.controller
        if c is None:
            self._failure_summary()
            return
        now, mono = self.clock.now(), self.clock.monotonic()
        if self.last_wall is not None and c.busy:
            wall_delta = (now-self.last_wall).total_seconds()
            mono_delta = mono-self.last_mono
            if wall_delta < 0 or mono_delta < 0 or abs(wall_delta-mono_delta) > 2 or max(wall_delta, mono_delta) > 5:
                self._clock_observation(previous_wall_utc=utc(self.last_wall), wall_utc=utc(now),
                    previous_monotonic=self.last_mono, monotonic=mono,
                    wall_delta_seconds=wall_delta, monotonic_delta_seconds=mono_delta,
                    divergence_seconds=wall_delta-mono_delta)
            if wall_delta < 0 or mono_delta < 0 or abs(wall_delta-mono_delta) > 2:
                self.fail("clock_jump")
            if max(wall_delta, mono_delta) > 5:
                self.fail("scheduler_gap_or_resume")
        self.last_wall, self.last_mono = now, mono
        if self.cancel_event.is_set():
            self.fail("operator_cancelled")
        if c.busy:
            if self.guard and mono >= self.next_legacy_check:
                self.next_legacy_check = mono+1
                try:
                    self.guard.check_legacy()
                except Exception:
                    self.fail("concurrent_collector_or_inventory_error")
            blocking = self._collection_failures()
            if blocking or self._collection_due(now, mono):
                self.state = "FINALIZING"
                if self.requested_stop_at is None:
                    self.requested_stop_at = utc(now)
                    self._stop_controller(blocking[0] if blocking else 'planned_65_minute_stop')
            elif c.session_phase == "REGULAR_LIVE":
                self.state = "COLLECTING"
            elif c.session_phase in {"FINALIZING", "STOPPED", "ERROR"} or c.state in {"STOPPING", "ERROR"}:
                self.state = "FINALIZING"
            else:
                self.state = "ARMED"
            return
        # Controller may finish before provider WS has ended on an exceptional path.
        # Keep the UI alive and wait; no timeout kills or shutdown are permitted.
        if c.worker and not threads_finished(c):
            self.fail("worker_still_active")
            self.state = "FINALIZING"
            return
        self.state = "FINALIZING"
        self._finalize()

    def _finalize(self):
        if self.finalized or self._finalizing:
            return
        self._finalizing = True  # Reentrant ticks cannot publish twice; UI stays cancellable.
        try:
            if self.publisher is None:
                from .artifacts import publish_run
                publisher = publish_run
            else:
                publisher = self.publisher
            archive, summary = publisher(self)
            self.archive, self.summary = archive, summary
            if summary["shutdown_eligible"] is not True:
                self.failures = list(summary["failures"])
                self.reason = self.failures[0] if self.failures else "not_eligible"
                self.state = "ERROR"
                return
            if self.cancel_event.is_set():
                raise RunBlocked("operator_cancelled")
            if self.no_shutdown:
                self.state = "COMPLETE_NO_SHUTDOWN"
                return
            # Re-read the *published* archive immediately before the only OS boundary.
            from .artifacts import validate_zip
            validate_zip(archive, summary["run_id"], summary["session_date"])
            if not threads_finished(self.controller):
                raise RunBlocked("worker_still_active")
            # Persist intent before requesting shutdown. A disk failure inhibits it.
            atomic_json(self.folder/"shutdown_status.json", dict(status="REQUESTING", delay_seconds=60))
            if self.cancel_event.is_set():
                raise RunBlocked("operator_cancelled")
            self.shutdown_may_be_pending = True
            self.shutdown.request()
            self.shutdown_at = self.clock.now()+timedelta(seconds=60)
            self.shutdown_deadline_mono = self.clock.monotonic()+60
            if self.cancel_event.is_set():
                self.cancel_shutdown()
                return
            try:
                atomic_json(self.folder/"shutdown_status.json", dict(status="SCHEDULED", at=utc(self.shutdown_at)))
            except Exception:
                raise RunBlocked("shutdown_status_write_failed") from None
            self.state = "SHUTDOWN_PENDING"
        except Exception as exc:
            self.fail(str(exc) if isinstance(exc, RunBlocked) else "finalization_"+type(exc).__name__)
            # A failed evidence gate must not erase already saved exports. This
            # separate archive can never reach the successful shutdown branch.
            # Do not snapshot workers still writing, or replace an existing ZIP
            # after crossing the operating-system shutdown boundary.
            if self.archive is None and not self.shutdown_may_be_pending and threads_finished(self.controller):
                primary_failure = self.reason
                try:
                    from .diagnostics import publish_diagnostic
                    self.archive, self.summary = publish_diagnostic(self, primary_failure)
                except Exception as diagnostic_error:
                    self.fail("diagnostic_zip_"+type(diagnostic_error).__name__)
                    self.reason = primary_failure
            self._failure_summary()
        finally:
            self.finalized = True

    def cancel_shutdown(self):
        with self._shutdown_lock:
            try:
                self.shutdown.cancel()
            except Exception:
                self.shutdown_may_be_pending = True
                self.fail("shutdown_cancel_failed")
                self.state = "SHUTDOWN_PENDING"
                try:
                    atomic_json(self.folder/"shutdown_status.json",
                                dict(status="SHUTDOWN_PENDING", warning="shutdown_cancel_failed"))
                except Exception:
                    pass  # Disk failure cannot erase the in-memory warning.
                return
            self.shutdown_may_be_pending = False
            self.state = "SHUTDOWN_CANCELLED"
            if self.reason == "shutdown_cancel_failed":
                self.reason = None
            try:
                atomic_json(self.folder/"shutdown_status.json", dict(status="CANCELLED"))
            except Exception:
                self.reason = "shutdown_status_write_failed"

    def close(self):
        if self.guard:
            self.guard.close()
