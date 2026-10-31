"""One-shot observational scheduler. No power, collector, AI or upload actions."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
import uuid

from .postrun_audit import (OWNER, ID, MAX_OUTPUT, confined, read_json, write_output,
                            validate_state, publish, process_alive)
from .watchdog import TaskScheduler, lease, instant, stamp


class AuditTaskScheduler(TaskScheduler):
    name_pattern = re.compile(r'MarketRadarAudit_(?:TEST_)?\d{8}_[a-f0-9]{32}\Z')
    script_name = 'audit_task.ps1'


class AuditTask:
    def __init__(self, attempt, *, scheduler=None, now=None):
        self.attempt = Path(attempt).resolve()
        self.path = confined(self.attempt, 'audit/task.json')
        self.scheduler = scheduler or AuditTaskScheduler()
        self.now = now or (lambda: datetime.now(timezone.utc))

    def register(self, session, command, *, test=False, test_deadline=None):
        validate_state(session)
        opened = instant(session['plan']['open'])
        due = opened + timedelta(minutes=100)
        if test_deadline is not None:
            if not test: raise ValueError('test_only_deadline')
            due = test_deadline
        if self.now() >= due or self.path.exists():
            raise ValueError('late_or_duplicate_audit_task')
        nonce = uuid.uuid4().hex
        value = dict(schema=1, owner=OWNER+'/'+nonce, nonce=nonce, audit_id=session['audit_id'],
            task_name='MarketRadarAudit_'+('TEST_' if test else '')+session['et_date'].replace('-', '')+'_'+session['audit_id'],
            deadline=stamp(due), expires=stamp(due+timedelta(minutes=10)),
            command=str(Path(command[0]).resolve()),
            arguments=subprocess.list2cmdline([*command[1:], '--audit-run', str(self.path), '--audit-nonce', nonce]),
            test=bool(test), status='PREPARED', shutdown_commands=0)
        write_output(self.path.parent, 'task.json', value)
        try:
            self.scheduler.register(value, self.path)
            value['status'] = 'REGISTERED'
        except Exception:
            value['status'] = 'REGISTRATION_FAILED'
            write_output(self.path.parent, 'task.json', value)
            raise
        write_output(self.path.parent, 'task.json', value)
        return value

    def cancel(self):
        with lease(self.path, timeout=.3):
            value = read_json(self.path.parent, 'task.json', limit=MAX_OUTPUT)
            self._identity(value)
            try:
                self.scheduler.delete(value)
                value['status'] = 'CANCELLED'
            except Exception:
                value['status'] = 'CANCEL_FAILED'
                write_output(self.path.parent, 'task.json', value)
                raise
            write_output(self.path.parent, 'task.json', value)

    def _identity(self, value):
        state = validate_state(read_json(self.attempt, 'audit/state.json', limit=MAX_OUTPUT))
        if (value.get('schema') != 1 or value.get('owner') != OWNER+'/'+str(value.get('nonce')) or
                not ID.fullmatch(str(value.get('nonce'))) or value.get('audit_id') != state['audit_id'] or
                not AuditTaskScheduler.name_pattern.fullmatch(str(value.get('task_name')))):
            raise ValueError('foreign_audit_task')
        expected = 'MarketRadarAudit_'+('TEST_' if value.get('test') else '')+state['et_date'].replace('-', '')+'_'+state['audit_id']
        if value['task_name'] != expected: raise ValueError('foreign_audit_task')
        return state

    def run(self, nonce):
        with lease(self.path, timeout=.3):
            value = read_json(self.path.parent, 'task.json', limit=MAX_OUTPUT)
            state = self._identity(value)
            if nonce != value['nonce'] or value['status'] not in {'REGISTERED', 'PREPARED'}:
                raise ValueError('audit_invocation_rejected')
            if not instant(value['deadline']) <= self.now() < instant(value['expires']):
                raise ValueError('audit_outside_time_window')
            # Audit never edits the supervisor/watchdog/source verdict. A frozen
            # terminal summary is preferred; task results are a separate receipt.
            value['status'] = 'CHECKED'
            write_output(self.path.parent, 'task.json', value)
            result = publish(self.attempt, state=state, now=self.now(), alive=process_alive(state))
            write_output(self.path.parent, 'task_result.json', dict(schema=1, status='DRY_RUN_CHECKED' if value['test'] else 'CHECKED',
                audit_id=value['audit_id'], pid=os.getpid(), checked_at=stamp(self.now()), severity=result['severity'],
                shutdown_commands=0, external_requests=0))
        # Remove only this verified one-shot task, never a Family watchdog.
        try:
            self.scheduler.delete(value)
        except Exception:
            value['status'] = 'CANCEL_FAILED'
            write_output(self.path.parent, 'task.json', value)
            publish(self.attempt, state=state, now=self.now(), alive=process_alive(state))
        return result


def run_helper(path, nonce):
    path = Path(path).absolute()
    if path.name != 'task.json' or path.parent.name != 'audit' or not ID.fullmatch(str(nonce or '')):
        return 2
    try:
        confined(path.parent.parent, 'audit/task.json')
        AuditTask(path.parent.parent).run(nonce)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return 2  # No exception payloads, collector fallback or hidden OS actions.


def recover_pending(base, *, now=None, limit=32):
    """Next family launch recovery, only audit-owned recent attempts. No scan of DBs."""
    from itertools import islice
    from .models import ET
    now = now or datetime.now(timezone.utc)
    checked = 0
    for delta in range(14):
        day = (now.astimezone(ET).date()-timedelta(days=delta)).isoformat()
        folder = confined(base, day)
        if not folder.is_dir(): continue
        for attempt in islice(folder.iterdir(), limit-checked):
            if not re.fullmatch(r'attempt_[a-f0-9]{32}', attempt.name): continue
            try:
                state = validate_state(read_json(attempt, 'audit/state.json', limit=MAX_OUTPUT))
                if state.get('terminal') and confined(attempt, 'audit/postrun_audit.json').is_file(): continue
                if process_alive(state) is False:
                    publish(attempt, state=state, now=now, alive=False)
                    checked += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if checked >= limit: return checked
    return checked
