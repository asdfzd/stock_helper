"""Small asynchronous observer, outside collection queues and power eligibility."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import queue
import threading
import time
import uuid

from .models import ET, utc, instant
from .postrun_audit import OWNER, SCHEMA, confined, write_output, publish, number, boolean, failure_code
from .audit_task import AuditTask, recover_pending


class AuditSession:
    def __init__(self, attempt, *, now=None, scheduler=None, command=None, recover_base=None):
        import psutil
        self.attempt = Path(attempt).absolute()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.command = command
        self.task = AuditTask(attempt, scheduler=scheduler, now=self.now)
        self.error = None
        self.result = None
        self.recover_base = recover_base
        self.pending = queue.Queue(maxsize=1)
        self.changed = threading.Condition()
        self.sequence = self.completed = 0
        self.stopping = False
        self.registration_attempted = False
        self.next_sample = 0
        self.last_phase = None
        suffix = self.attempt.name.removeprefix('attempt_')
        from .postrun_audit import ID
        audit_id = suffix if ID.fullmatch(suffix) else uuid.uuid4().hex
        self.state = dict(schema=SCHEMA, owner=OWNER, audit_id=audit_id, started_at=utc(self.now()),
            et_date=self.now().astimezone(ET).date().isoformat(), pid=os.getpid(),
            process_created=psutil.Process().create_time(), heartbeat=None, gui_heartbeat=None,
            collector_run_id=None, run_folder=None, armed_at=None, audit_due=None, plan=None,
            terminal=False, first_failure=None, audit_task_status='NOT_REGISTERED', telemetry={})
        confined(self.attempt, 'audit').mkdir(parents=True, exist_ok=False)
        self.thread = threading.Thread(target=self._work, name='family-postrun-audit', daemon=True)
        self.thread.start()
        self._enqueue()

    def _enqueue(self):
        with self.changed:
            self.sequence += 1
            value = (self.sequence, deepcopy(self.state))
            try: self.pending.get_nowait()
            except queue.Empty: pass
            self.pending.put_nowait(value)

    def failure(self, reason):
        if self.state['first_failure'] is None:
            self.state['first_failure'] = dict(code=failure_code(reason), occurred_at_utc=utc(self.now()))
        self.state['terminal'] = True
        self._enqueue()

    def observe(self, runner, *, force=False):
        # No SQL, file reads, subprocesses or blocking WS locks on the supervisor.
        phase = runner.state
        if not force and time.monotonic() < self.next_sample and phase == self.last_phase:
            return
        self.next_sample = time.monotonic()+5
        self.last_phase = phase
        c = runner.controller
        summary = runner.summary or {}
        if runner.folder is not None:
            self.state['run_folder'] = Path(runner.folder).relative_to(self.attempt).as_posix()
        self.state.update(heartbeat=utc(self.now()), gui_heartbeat=runner.gui_heartbeat,
            collector_run_id=summary.get('run_id'), shutdown_disabled=runner.no_shutdown,
            terminal=runner.finalized or phase == 'WAITING_SHUTDOWN', user_stopped=runner.cancel_event.is_set())
        if runner.plan:
            self.state.update(plan={k: runner.plan[k] for k in ('open', 'stop', 'official_close', 'session_date')},
                et_date=runner.plan['session_date'], audit_due=utc(instant(runner.plan['open'])+timedelta(minutes=100)))
        if phase == 'ARMED' and self.state['armed_at'] is None:
            self.state['armed_at'] = utc(self.now())
        if runner.failures and self.state['first_failure'] is None:
            self.state['first_failure'] = dict(code=failure_code(runner.failures[0]), occurred_at_utc=utc(self.now()))
        # Keep the last live observation. STOPPED naturally disconnects WS and
        # must not be mislabeled as a market-data outage.
        if c and c.session_phase == 'REGULAR_LIVE':
            provider = getattr(getattr(c, 'worker', None), 'provider', None)
            ws = getattr(provider, 'ws', None)
            metrics = dict(sampled_at=utc(self.now()), raw_dropped=number(getattr(provider, 'raw_dropped', None)),
                paper_error=boolean(getattr(getattr(c, 'paper', None), 'error', None)),
                rotation_error=boolean(getattr(getattr(c, 'paper', None), 'sidecar_error', None)))
            lock = getattr(ws, 'lock', None)
            if lock and lock.acquire(timeout=.002):
                try:
                    rows = tuple(getattr(ws, 'latest', {}).values())
                    received = [instant(v['received_utc']) for v in rows if isinstance(v, dict) and v.get('received_utc')]
                    at = max(received) if received else None
                    metrics.update(ws_connected=boolean(ws.connected), sticky_gap=boolean(ws.gap),
                        generation=number(ws.generation), fe_count=number(ws.counts.get('FE')),
                        last_fe_at=utc(at) if at else None,
                        last_fe_age_seconds=(self.now()-at).total_seconds() if at else None)
                finally:
                    lock.release()
            self.state['telemetry'] = metrics
            totals = self.state.setdefault('continuity_totals', dict(samples=0, disconnected_samples=0,
                sticky_gap_samples=0, maximum_fe_age_seconds=None, maximum_generation=None, first_receipt_gap_at=None))
            totals['samples'] += 1
            totals['disconnected_samples'] += metrics.get('ws_connected') is False
            totals['sticky_gap_samples'] += metrics.get('sticky_gap') is True
            for source, target in [('last_fe_age_seconds','maximum_fe_age_seconds'),('generation','maximum_generation')]:
                value = number(metrics.get(source))
                if value is not None: totals[target] = max(totals[target] or 0, value)
            from tools.one_hour_runner.quality import POLICY
            if (metrics.get('last_fe_age_seconds') or 0) > POLICY['maximum_price_age_seconds'] and not totals['first_receipt_gap_at']:
                totals['first_receipt_gap_at'] = metrics['sampled_at']
        elif c:
            self.state['telemetry'].update(paper_error=boolean(getattr(getattr(c, 'paper', None), 'error', None)),
                rotation_error=boolean(getattr(getattr(c, 'paper', None), 'sidecar_error', None)))
        self._enqueue()

    def _work(self):
        recovered = False
        while True:
            try:
                seq, state = self.pending.get(timeout=.1)
            except queue.Empty:
                if self.stopping: return
                continue
            try:
                folder = confined(self.attempt, 'audit')
                write_output(folder, 'state.json', state)
                if state.get('armed_at') and state.get('plan') and not self.registration_attempted:
                    self.registration_attempted = True
                    try:
                        self.task.register(state, self.command)
                        self.state['audit_task_status'] = 'REGISTERED'
                    except Exception:
                        self.state['audit_task_status'] = 'REGISTRATION_FAILED'
                        self.error = 'audit_registration_failed'
                state['audit_task_status'] = self.state['audit_task_status']
                write_output(folder, 'state.json', state)
                self.result = publish(self.attempt, state=state, now=self.now())
                if state.get('terminal'):
                    from .audit_delivery import DeliveryQueue
                    DeliveryQueue(folder).prepare(self.result)
                if self.recover_base and not recovered:
                    recovered = True
                    recover_pending(self.recover_base, now=self.now())
            except Exception:
                self.error = 'audit_write_failed'  # Never propagate into power/collection policy.
            finally:
                with self.changed:
                    self.completed = seq
                    self.changed.notify_all()

    def flush(self, timeout=.75):
        deadline = time.monotonic()+timeout
        with self.changed:
            target = self.sequence
            while self.completed < target and time.monotonic() < deadline:
                self.changed.wait(max(0, deadline-time.monotonic()))
            return self.completed >= target

    def close(self, timeout=.2):
        self.flush(timeout)
        self.stopping = True
        self.thread.join(timeout)
