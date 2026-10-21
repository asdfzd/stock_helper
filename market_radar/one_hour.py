"""Optional supervisor for the Dashboard's existing CollectorController.

No second collector, EXE launch, strategy changes or relaxed quality gates.
The standalone runner's evidence/diagnostic format and policy are reused.
"""
from datetime import timedelta
from copy import deepcopy
import os
from functools import partial
from pathlib import Path
import subprocess
import threading

from .models import instant, utc
from .failure_evidence import exception_evidence
from .power import WindowsSleepInhibitor
from .run_diagnostics import RunDiagnostics, call_path, worker_state
from .providers.kiwoom_lock import CollectorLock
from tools.one_hour_runner.core import Runner, atomic_json, threads_finished
from tools.one_hour_runner.system import InstanceGuard, RunBlocked, WindowsShutdown

INTEGRATION_VERSION = "4"
# Research always fails. Bounded forward corrections may continue raw collection;
# all other interruptions still stop. Shutdown requires independent preservation.
PRESERVABLE_INTERRUPTS = frozenset({'clock_jump', 'scheduler_gap_or_resume'})


class DashboardGuard(InstanceGuard):
    """Share the runner mutex and original collector lock, allow our own GUI."""
    def __init__(self):
        super().__init__()
        self.final_lock = None

    def check_legacy(self):
        import psutil
        try:
            own = {os.getpid(), *(p.pid for p in psutil.Process().parents())}
            for process in psutil.process_iter():
                if process.pid in own:
                    continue
                try:
                    name = process.name().lower()
                    if name in {"stockhelper.exe", "stock_helper.exe", "onehourresearch.exe"}:
                        raise RunBlocked("other_collection_application")
                    if name.startswith(("python", "pyw")):
                        args = [str(a).lower().replace("\\", "/") for a in process.cmdline()]
                        if any(a.endswith(("/live_ui.py", "/main.py", "/launch_stock_helper.py"))
                               or a in {"live_ui.py", "main.py", "market_radar", "market_radar.__main__", "tools.one_hour_runner"}
                               or a.startswith("tools.kiwoom_provider.") for a in args):
                            raise RunBlocked("other_collection_application")
                except psutil.NoSuchProcess:
                    continue
        except psutil.AccessDenied:
            raise RunBlocked("process_inventory_unavailable") from None

    def hold_after_collection(self):
        self.check_legacy()
        if self.final_lock is None:
            lock = CollectorLock()
            lock.acquire()
            self.final_lock = lock

    def close(self):
        if self.final_lock is not None:
            self.final_lock.close()
            self.final_lock = None
        super().close()


class DashboardOneHour(Runner):
    def __init__(self, controller, root, *, no_shutdown=True, clock=None, calendar=None,
                 shutdown=None, guard=None, publisher=None, simulated=False, ready_to_shutdown=None, power=None):
        self.existing_controller = controller
        self.saved_paths = {name: getattr(controller, name, None) for name in
                            ("paper_path", "paper_review_path", "opening_hour_path", "paper_telemetry_path", "operational_diagnostics")}
        super().__init__(root, clock=clock or controller.clock,
                         calendar=calendar or controller.calendar,
                         controller_factory=self._attach_controller,
                         publisher=publisher,
                         shutdown=shutdown or WindowsShutdown(partial(subprocess.run, timeout=10)), guard=guard,
                         no_shutdown=no_shutdown, simulated=simulated)
        self.thread = None
        self._wake = threading.Event()
        self._cancel_processed = False
        self.minimum_shutdown_at = None
        self.minimum_shutdown_mono = None
        self._clock_origin = None
        self.ready_to_shutdown = ready_to_shutdown or (lambda: True)
        self.power = power or WindowsSleepInhibitor()
        self.diagnostics = None
        self.shutdown_cancel_event = threading.Event()
        self._shutdown_cancel_processed = False
        self._last_phase = None
        self._paper_failure_seen = False
        self._power_acquired = False
        self.stop_context = None
        self.continue_after_clock_jump = False
        self.clock_abort = False
        self.collection_clock = dict(policy='bounded_forward_v1', anomalies=[],
            absolute_utc_verified=False, research_valid=True, start_monotonic=None,
            deadline_monotonic=None, stopped_monotonic=None)

    def _ensure_diagnostics(self):
        if self.folder is not None and self.diagnostics is None:
            self.diagnostics = RunDiagnostics(self.folder/'diagnostics', self.clock)
        return self.diagnostics

    def _record(self, code, *, error=False, **details):
        journal = self._ensure_diagnostics()
        if journal is not None:
            journal.record(code, self.state, error=error, **details)

    def fail(self, reason):
        fresh = reason not in self.failures
        super().fail(reason)
        if fresh:
            self._record(reason, error=True, workers=worker_state(self.controller))

    def _clock_observation(self, **sample):
        self._record('clock_observation', error=True, sample=sample)
        # The post-processing shutdown clock has its own conservative deadline.
        if 'previous_wall_utc' not in sample:
            return
        wall, mono = sample['wall_delta_seconds'], sample['monotonic_delta_seconds']
        drift = sample['divergence_seconds']
        classification = ('monotonic_rollback' if mono < 0 else
            'wall_rollback' if wall < 0 or drift < -2 else
            'execution_gap_or_resume' if mono > 5 else
            'large_forward_or_unobserved_gap' if wall > 5 else
            'bounded_forward_adjustment_candidate')
        c = self.controller
        schedule = getattr(c, 'schedule', None)
        paper = getattr(c, 'paper', None)
        provider = getattr(c.worker, 'provider', None)
        ws = getattr(provider, 'ws', None)
        protocol = schedule.snapshot() if schedule else {}
        # This does not certify NTP or feed health. Both existing 5-second gap
        # fences remain in force, and only a live, on-time run may continue.
        allowed = (classification == 'bounded_forward_adjustment_candidate'
            and drift > 2 and 0 < mono <= 5 and 0 < wall <= 5
            and not self.clock_abort and not self.cancel_event.is_set()
            and not self._collection_failures() and c.state == 'RUNNING'
            and schedule.phase == 'REGULAR_LIVE' and schedule.started_mono is not None
            and protocol.get('ready_before_open') is True
            and protocol.get('actual_start') is not None
            and set(protocol.get('protocol_reason', '').split(';')) <= {'in_progress', 'clock_jump'}
            and 0 <= (instant(protocol['actual_start'])-instant(self.plan['open'])).total_seconds() <= 1
            and instant(sample['wall_utc']) < instant(self.plan['official_close'])
            and bool(getattr(c.worker, 'running', False))
            and not getattr(c.worker, 'error', None)
            and not getattr(c.worker, 'failed_batches', 0)
            and not getattr(provider, 'raw_dropped', 0) and not getattr(provider, 'error', None)
            and (ws is None or (ws.connected and not ws.gap))
            and callable(getattr(paper, 'fail', None)))
        # Reuse the consumer's thread-safe fail-closed input flag. No event is
        # inserted, reordered, backdated or replayed; an earlier ERROR stays ERROR.
        if callable(getattr(paper, 'fail', None)):
            paper.fail()
        if schedule:
            schedule.invalidate('clock_jump' if abs(drift) > 2 or wall < 0 or mono < 0
                                else 'scheduler_gap_or_resume')
        if len(self.collection_clock['anomalies']) >= 64:
            allowed = False
            self.fail('clock_diagnostic_capacity_exceeded')
        else:
            self.collection_clock['anomalies'].append(dict(sample=sample,
                classification=classification, action='continue_raw' if allowed else 'stop',
                research_action='fail_closed', source_verified=False,
                uncertainty_interval=dict(start_wall_utc=sample['previous_wall_utc'],
                    end_wall_utc=sample['wall_utc'], start_monotonic=sample['previous_monotonic'],
                    end_monotonic=sample['monotonic']),
                following_epoch='locally_ordered_absolute_utc_unverified' if allowed else 'stopped'))
        self.collection_clock['research_valid'] = False
        self.continue_after_clock_jump = bool(allowed)
        self.clock_abort |= not allowed
        if mono < 0:
            self.fail('monotonic_clock_rollback')
        self._persist_collection_clock('clock_continuation_decision')
        if self.diagnostics and self.diagnostics.write_errors:
            self.continue_after_clock_jump = False
            self.clock_abort = True
            self.fail('diagnostics_write_failed')

    def _persist_collection_clock(self, code):
        journal = self._ensure_diagnostics()
        if journal:
            with journal.lock:
                journal.context['collection_clock'] = deepcopy(self.collection_clock)
            self._record(code, error=not self.collection_clock['research_valid'],
                         workers=worker_state(self.controller))

    def _collection_failures(self):
        return [reason for reason in self.failures
                if reason != 'clock_jump' or not self.continue_after_clock_jump or self.clock_abort]

    def _collection_due(self, now, mono):
        schedule = getattr(self.controller, 'schedule', None)
        started = getattr(schedule, 'started_mono', None)
        if started is not None and self.collection_clock['start_monotonic'] is None:
            self.collection_clock.update(start_monotonic=started, deadline_monotonic=started+65*60,
                actual_start_utc=schedule.snapshot()['actual_start'], planned_stop_utc=self.plan['stop'])
            self._persist_collection_clock('collection_deadline_anchored')
        deadline = self.collection_clock['deadline_monotonic']
        # Keep the official UTC boundary AND a full 65 elapsed minutes. A forward
        # adjustment never shortens elapsed collection; all actual stamps stay raw.
        return now >= instant(self.plan['stop']) and (deadline is None or mono >= deadline)

    def _stop_controller(self, reason):
        self.stop_context = dict(source='supervisor', reason=reason,
            requested_at=self.requested_stop_at or utc(self.clock.now()),
            monotonic=self.clock.monotonic(), caller=call_path())
        self.collection_clock.update(stopped_monotonic=self.stop_context['monotonic'],
            requested_stop_utc=self.stop_context['requested_at'], stop_reason=reason)
        self._persist_collection_clock('collection_scope_closed')
        self._record('collector_stop_requested', stop=self.stop_context,
                     workers=worker_state(self.controller))
        super()._stop_controller(reason)

    def _attach_controller(self, *args, **kwargs):
        if self.cancel_event.is_set():
            raise RunBlocked("operator_cancelled")
        if self.existing_controller.busy:
            raise RunBlocked("collector_already_running")
        self.existing_controller.paper_telemetry_path = self.folder/"paper_v1"/"paper_performance.json"
        self.existing_controller.operational_diagnostics = self._ensure_diagnostics()
        self._record('run_start', power_acquired=self._power_acquired)
        return self.existing_controller

    @property
    def active(self):
        return bool((self.thread and self.thread.is_alive()) or self.shutdown_may_be_pending)

    def launch(self, *, cache):
        if self.active or self.existing_controller.busy or self.folder is not None:
            raise RunBlocked("collector_already_running")
        self.thread = threading.Thread(target=self._drive, kwargs={"cache": cache},
                                       name="radar-one-hour", daemon=False)
        self.thread.start()

    def start(self, *, cache, provider=None):
        if self.existing_controller.busy:
            raise RunBlocked("collector_already_running")
        super().start(cache=cache, provider=provider)
        if self.diagnostics:
            self.diagnostics.context.update(run_id=(self.summary or {}).get('run_id'),
                session_date=(self.summary or {}).get('session_date'))
            self.diagnostics.persist()
        if self.plan:
            self.minimum_shutdown_at = instant(self.plan["open"]) + timedelta(minutes=70)
            wall, mono = self.clock.now(), self.clock.monotonic()
            self.minimum_shutdown_mono = mono + (self.minimum_shutdown_at-wall).total_seconds()
            self._clock_origin = wall, mono
            self.plan["shutdown_not_before"] = utc(self.minimum_shutdown_at)
            self.summary.update(integration="market_radar_gui", integration_version=INTEGRATION_VERSION,
                                actual_orders_enabled=False)
            atomic_json(self.folder/"one_hour_run_summary.json", self.summary)

    def _drive(self, cache):
        try:
            self.power.acquire()
            self._power_acquired = True
            self.start(cache=cache)
            while not self.finalized or self.shutdown_may_be_pending:
                self.tick()
                self._wake.wait(.1)
                self._wake.clear()
        except Exception as exc:
            # A power/startup failure still gets a fresh diagnostic location.
            if self.folder is None:
                import uuid
                self.folder = self.root/('run_' + uuid.uuid4().hex)
                self.summary = dict(status='FAILED', shutdown_eligible=False)
            self._record('supervisor_exception', error=True,
                         exception=exception_evidence(exc, 'supervisor'))
            self.fail(str(exc) if isinstance(exc, RunBlocked) else "supervisor_"+type(exc).__name__)
            self.request_cancel(source='supervisor_exception')
            # Do not abandon workers or kill a slow finalizer, even on exceptions.
            c = self.controller
            if c and c.busy:
                self._stop_controller('supervisor_exception')
            while c and (c.busy or (c.worker and not threads_finished(c))):
                self._wake.wait(.1)
                self._wake.clear()
            self._finalizing = False
            self._finalize()
        finally:
            # A failed OS abort must keep a visible pending state and remain retryable.
            while self.shutdown_may_be_pending:
                self._wake.wait(.1)
                self._wake.clear()
                if (self.cancel_event.is_set() or self.shutdown_cancel_event.is_set()) and not self._cancel_processed:
                    self._cancel_processed = True
                    self.cancel_shutdown()
            if not self.existing_controller.busy:
                for name, value in self.saved_paths.items():
                    setattr(self.existing_controller, name, value)
            try:
                self.power.close()
                self._record('sleep_request_released', acquired=self._power_acquired)
            except Exception as exc:
                self._record('sleep_release_failed', error=True,
                             exception=exception_evidence(exc, 'sleep_release'))
            finally:
                self.close()

    def request_cancel(self, source='public_cancel'):
        # Safe from the Qt thread; never perform subprocess/ZIP/DB work there.
        self.cancel_event.set()
        self._cancel_processed = False
        # UI only enqueues the structured request; durable IO runs on supervisor.
        self._cancel_context = dict(source=source, caller=call_path())
        self._wake.set()

    def request_cancel_shutdown(self):
        self.shutdown_cancel_event.set()
        self._shutdown_cancel_processed = False
        self._cancel_processed = False
        self._wake.set()

    def tick(self):
        if self.cancel_event.is_set() and hasattr(self, '_cancel_context'):
            self._record('collection_cancel_requested', error=True, request=self._cancel_context)
            del self._cancel_context
        if self.shutdown_cancel_event.is_set() and not self._shutdown_cancel_processed:
            self._shutdown_cancel_processed = True
            self.no_shutdown = True
            self._record('shutdown_only_cancel_requested')
            if self.shutdown_may_be_pending:
                self._cancel_processed = True
                self.cancel_shutdown()
            elif self.state == 'WAITING_SHUTDOWN':
                self.state, self.finalized = 'SHUTDOWN_CANCELLED', True
                self.summary.update(shutdown_eligible=False, shutdown_disabled=True, shutdown_state=self.state)
                atomic_json(self.folder/'one_hour_run_summary.json', self.summary)
            return
        if self.shutdown_may_be_pending:
            if self.cancel_event.is_set() and not self._cancel_processed:
                self._cancel_processed = True
                self.cancel_shutdown()
            return
        if self.finalized:
            return
        if self.state == "WAITING_SHUTDOWN":
            if self.cancel_event.is_set():
                self.fail("operator_cancelled")
                self._failure_summary()
                return
            try:
                self._check_clock()
                if (self.clock.now() >= self.minimum_shutdown_at and
                        self.clock.monotonic() >= self.minimum_shutdown_mono and self.ready_to_shutdown()):
                    self._schedule_shutdown()
            except Exception as exc:
                self._reject(exc)
            return
        c = self.controller
        if c:
            phase = c.session_phase
            if phase != self._last_phase:
                self._last_phase = phase
                self._record('phase_changed', controller_phase=phase, workers=worker_state(c))
            paper = getattr(c, 'paper', None)
            if paper and not self._paper_failure_seen:
                from tools.one_hour_runner.core import paper_health
                health = paper_health(paper)
                if health['failed']:
                    self._paper_failure_seen = True
                    metrics = getattr(paper, 'metrics', None)
                    timings = metrics.snapshot() if metrics else {}
                    first = timings.get('first_failure_events')
                    first_error = timings.get('first_error') or {}
                    self._record(first_error.get('code', 'paper_or_rotation_error'), error=True,
                                 paper_error=bool(health['paper_error']), sidecar_error=bool(health['sidecar_error']),
                                 rotation_view_error=bool(health['rotation_view_error']),
                                 first_failure_event=first[-1] if first else None, first_engine_error=first_error)
        super().tick()

    def _check_clock(self):
        wall, mono = self._clock_origin
        elapsed_wall = (self.clock.now()-wall).total_seconds()
        elapsed_mono = self.clock.monotonic()-mono
        if elapsed_wall < 0 or elapsed_mono < 0 or abs(elapsed_wall-elapsed_mono) > 2:
            self._clock_observation(origin_wall_utc=utc(wall), origin_monotonic=mono,
                wall_delta_seconds=elapsed_wall, monotonic_delta_seconds=elapsed_mono,
                divergence_seconds=elapsed_wall-elapsed_mono)
            if elapsed_mono < 0:
                raise RunBlocked('monotonic_clock_rollback')
            # Never advance the original monotonic deadline. Rollback extends it;
            # a new jump requires at least 60 stable seconds before OS submission.
            now, current = self.clock.now(), self.clock.monotonic()
            self.minimum_shutdown_mono = max(self.minimum_shutdown_mono, current+60,
                current+max(0, (self.minimum_shutdown_at-now).total_seconds()))
            self._clock_origin = now, current
            self._record('shutdown_clock_deadline_extended', deadline_monotonic=self.minimum_shutdown_mono)

    def _flush_outputs(self):
        # Only this run's new files, after their owning threads have finished.
        root = self.folder.resolve()
        for path in root.rglob("*"):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise RunBlocked("foreign_output_path")
            if path.is_file():
                with path.open("r+b") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())

    def _finalize(self):
        if self.finalized or self._finalizing:
            return
        self._finalizing = True
        try:
            from .one_hour_archive import publish_archive
            self._record('preservation_started', workers=worker_state(self.controller),
                         collection_failures=list(self.failures), stop=self.stop_context)
            if self.diagnostics:
                self.diagnostics.freeze()
            self.archive, self.summary = (self.publisher or publish_archive)(self)
            self._record('archive_verified', research_status=self.summary.get('research_status'),
                preservation_status=self.summary.get('preservation_status'),
                shutdown_eligible=self.summary.get('shutdown_eligible'))
            if self.summary.get("shutdown_eligible") is not True:
                self.failures = list(self.summary.get("shutdown_blockers", self.summary.get("operational_failures", [])))
                self.reason = self.failures[0] if self.failures else "not_eligible"
                self._failure_summary()
                return
            if self.cancel_event.is_set() or set(self.failures)-PRESERVABLE_INTERRUPTS:
                raise RunBlocked("operator_cancelled" if self.cancel_event.is_set() else "run_failed")
            self._validate_preserved_archive()
            if not threads_finished(self.controller):
                raise RunBlocked("worker_still_active")
            self._flush_outputs()
            if self.cancel_event.is_set():
                raise RunBlocked("operator_cancelled")
            if self.no_shutdown:
                self.state, self.finalized = "COMPLETE_NO_SHUTDOWN", True
                return
            if self.guard:
                self.guard.hold_after_collection()
            self.state = "WAITING_SHUTDOWN"
            self._check_clock()
            atomic_json(self.folder/"shutdown_status.json", dict(status=self.state,
                        not_before=utc(self.minimum_shutdown_at), delay_seconds=60))
            # The next tick checks both clocks; never an unconditional +65 shutdown.
        except Exception as exc:
            self._reject(exc)

    def _reject(self, exc):
        self._record('finalization_exception', error=True, exception=exception_evidence(exc, 'finalization'))
        if self.summary is not None:
            self.summary.setdefault('archive_exceptions', []).append(exception_evidence(exc, 'primary_finalization'))
        self.fail(str(exc) if isinstance(exc, RunBlocked) else "finalization_"+type(exc).__name__)
        if self.archive is None and not self.shutdown_may_be_pending and self.controller:
            if self.controller.worker and threads_finished(self.controller):
                try:
                    from .one_hour_archive import publish_archive
                    self.archive, self.summary = publish_archive(self)
                except Exception as retry_exc:
                    self.summary.setdefault('archive_exceptions', []).append(exception_evidence(retry_exc, 'diagnostic_retry'))
                    self.fail("diagnostic_zip_failed")
        self._failure_summary()

    def _validate_preserved_archive(self):
        from .one_hour_archive import operational_evidence, validate_archive
        operational_evidence(self)
        validate_archive(self.archive, self.summary["run_id"], self.summary["session_date"], self.folder)

    def cancel_shutdown(self):
        with self._shutdown_lock:
            super().cancel_shutdown()
            if self.summary is not None:
                # Keep research/preservation evidence, revoke current OS permission.
                self.summary.update(shutdown_eligible=False, shutdown_state=self.state,
                                    operational_failures=list(self.failures))
                try:
                    atomic_json(self.folder/"one_hour_run_summary.json", self.summary)
                except Exception:
                    self.fail("shutdown_status_write_failed")

    def _failure_summary(self):
        # A storage/OS/cancel fault never rewrites an established research verdict.
        research = (self.summary or {}).get("research_status")
        research_failures = list((self.summary or {}).get("research_failures", []))
        super()._failure_summary()
        if self.summary is not None:
            if research:
                self.summary.update(status=research, research_status=research,
                                    failures=research_failures, research_failures=research_failures)
            self.summary.update(operational_failures=list(self.failures), shutdown_eligible=False,
                                preservation_status="BLOCKED", shutdown_disabled=self.no_shutdown)
            try:
                atomic_json(self.folder/"one_hour_run_summary.json", self.summary)
            except Exception:
                self.fail("failure_summary_write_failed")

    def _schedule_shutdown(self):
        if self.no_shutdown or set(self.failures)-PRESERVABLE_INTERRUPTS or self.cancel_event.is_set() or self.shutdown_cancel_event.is_set():
            raise RunBlocked("shutdown_inhibited")
        if self.summary.get("shutdown_eligible") is not True or not threads_finished(self.controller):
            raise RunBlocked("final_gate_failed")
        if self.guard:
            self.guard.check_legacy()
        self._check_clock()
        if (self.clock.now() < self.minimum_shutdown_at or
                self.clock.monotonic() < self.minimum_shutdown_mono):
            raise RunBlocked("before_open_plus_70")
        self._validate_preserved_archive()
        self._flush_outputs()
        if self.diagnostics and self.diagnostics.write_errors:
            raise RunBlocked('diagnostics_write_failed')
        atomic_json(self.folder/"shutdown_status.json", dict(status="REQUESTING", delay_seconds=60))
        if self.cancel_event.is_set() or self.shutdown_cancel_event.is_set():
            raise RunBlocked("operator_cancelled")
        if not threads_finished(self.controller):
            raise RunBlocked('worker_still_active')
        if not self.ready_to_shutdown():
            return
        # Validation/fsync can take time. A correction during that work must
        # defer submission too, not just corrections observed at tick entry.
        self._check_clock()
        if (self.clock.now() < self.minimum_shutdown_at or
                self.clock.monotonic() < self.minimum_shutdown_mono):
            atomic_json(self.folder/'shutdown_status.json', dict(status='WAITING_SHUTDOWN',
                        not_before=utc(self.minimum_shutdown_at), delay_seconds=60))
            return
        self.shutdown_may_be_pending = True
        self.shutdown.request()
        # Any exception after this point takes _reject -> cancellation, even if
        # no timestamp was returned and the Windows request outcome is uncertain.
        self.shutdown_at = self.clock.now()+timedelta(seconds=60)
        self.shutdown_deadline_mono = self.clock.monotonic()+60
        atomic_json(self.folder/"shutdown_status.json", dict(status="SCHEDULED", at=utc(self.shutdown_at)))
        self.state, self.finalized = "SHUTDOWN_PENDING", True
        self._record('shutdown_scheduled')
        if self.diagnostics and self.diagnostics.write_errors:
            raise RunBlocked('diagnostics_write_failed')
        if self.cancel_event.is_set() or self.shutdown_cancel_event.is_set():
            self.cancel_shutdown()
