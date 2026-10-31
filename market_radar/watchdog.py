"""One-shot, process-independent emergency boundary. No collector/DB imports.

The state journal is outside the archive. A file lease serializes cancellation,
normal shutdown and emergency submission across processes. OS acceptance is never
reported as proof that the computer powered off.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

UTC = timezone.utc
NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
OWNER = 'MarketRadarFamily/watchdog/1'
NAME = re.compile(r'MarketRadarFamily_(?:TEST_)?\d{8}_[a-f0-9]{32}\Z')


class WatchdogError(RuntimeError):
    pass


def stamp(value):
    return value.astimezone(UTC).isoformat()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise WatchdogError('timezone_missing')
    return result.astimezone(UTC)


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows scanners/readers can briefly deny atomic replacement. Match the
        # existing controller's bounded retry; never truncate the old journal or
        # treat a persistent failure as durable cancellation/permission to shut down.
        delays = (.025, .05, .1)
        for attempt in range(len(delays) + 1):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if getattr(exc, 'winerror', None) not in {5, 32, 33} or attempt == len(delays):
                    raise
                time.sleep(delays[attempt])
    finally:
        temporary.unlink(missing_ok=True)


def read(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(262145)
    if len(raw) > 262144:
        raise WatchdogError('state_too_large')
    return json.loads(raw)


@contextmanager
def lease(path, timeout=12):
    """Crash releases the OS lock; a leftover .lock file is not ownership."""
    lock = Path(path).with_suffix('.lock')
    stream = lock.open('a+b')
    if not lock.stat().st_size:
        stream.write(b'0')
        stream.flush()
    end = time.monotonic() + timeout
    try:
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise WatchdogError('state_lock_timeout') from None
                time.sleep(.025)
        yield
    finally:
        stream.close()


def boot_identity():
    # GetTickCount64 includes sleep and is shared by all processes on this boot.
    import ctypes
    fn = ctypes.WinDLL('kernel32').GetTickCount64
    fn.restype = ctypes.c_ulonglong
    return fn() / 1000


def boot_generation():
    """Locally verified Windows boot counter; absent/denied fails closed."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r'SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters') as key:
            value = winreg.QueryValueEx(key, 'BootId')[0]
        if not isinstance(value, int):
            raise ValueError()
        return value
    except (OSError, ValueError):
        raise WatchdogError('windows_boot_identity_unavailable') from None


def task_xml(state):
    ET.register_namespace('', NS)
    root = ET.Element('Task', xmlns=NS, version='1.2')
    def child(parent, name, value=None, **attrs):
        result = ET.SubElement(parent, name, attrs)
        if value is not None:
            result.text = str(value)
        return result
    registration = child(root, 'RegistrationInfo')
    child(registration, 'Description', state['owner'])
    trigger = child(child(root, 'Triggers'), 'TimeTrigger')
    child(trigger, 'StartBoundary', state['deadline'])
    child(trigger, 'EndBoundary', state['expires'])
    child(trigger, 'Enabled', 'true')
    principal = child(child(root, 'Principals'), 'Principal', id='Author')
    child(principal, 'LogonType', 'InteractiveToken')
    child(principal, 'RunLevel', 'LeastPrivilege')
    settings = child(root, 'Settings')
    for name, value in dict(MultipleInstancesPolicy='IgnoreNew',
            DisallowStartIfOnBatteries='false', StopIfGoingOnBatteries='false',
            AllowHardTerminate='false', StartWhenAvailable='false',
            RunOnlyIfNetworkAvailable='false', AllowStartOnDemand='true',
            Enabled='true', Hidden='false', RunOnlyIfIdle='false', WakeToRun='false',
            ExecutionTimeLimit='PT1M', DeleteExpiredTaskAfter='PT1H').items():
        child(settings, name, value)
    action = child(child(root, 'Actions', Context='Author'), 'Exec')
    child(action, 'Command', state['command'])
    child(action, 'Arguments', state['arguments'])
    child(action, 'WorkingDirectory', str(Path(state['command']).parent))
    return ET.tostring(root, encoding='unicode')


def verify_task(xml, state):
    root = ET.fromstring(xml)
    def value(path):
        return root.findtext('/'.join('t:' + p for p in path.split('/')), namespaces={'t': NS})
    expected = {'RegistrationInfo/Description': state['owner'],
        'Actions/Exec/Command': state['command'], 'Actions/Exec/Arguments': state['arguments'],
        'Settings/StartWhenAvailable': 'false', 'Settings/WakeToRun': 'false',
        'Settings/MultipleInstancesPolicy': 'IgnoreNew', 'Settings/Enabled': 'true',
        'Principals/Principal/LogonType': 'InteractiveToken',
        'Principals/Principal/RunLevel': 'LeastPrivilege'}
    if any(value(key) != expected_value for key, expected_value in expected.items()):
        raise WatchdogError('task_identity_or_policy_mismatch')
    if len(root.findall('t:Actions/*', {'t': NS})) != 1 or len(root.findall('t:Triggers/*', {'t': NS})) != 1:
        raise WatchdogError('task_action_or_trigger_mismatch')
    if root.find('t:Triggers/t:TimeTrigger/t:Repetition', {'t': NS}) is not None:
        raise WatchdogError('repeating_task_rejected')
    for key, field in [('StartBoundary', 'deadline'), ('EndBoundary', 'expires')]:
        if instant(value('Triggers/TimeTrigger/' + key)) != instant(state[field]):
            raise WatchdogError('task_deadline_mismatch')


class TaskScheduler:
    """Exact-name COM operations; TASK_CREATE (2), never create-or-update."""
    name_pattern = NAME
    script_name = 'watchdog_task.ps1'

    def __init__(self, execute=None):
        self.execute = execute or subprocess.run

    def call(self, operation, name, xml_path=None):
        if not self.name_pattern.fullmatch(name):
            raise WatchdogError('foreign_task_name')
        from .shutdown_test import system_shutdown_path
        powershell = Path(system_shutdown_path()).parent / 'WindowsPowerShell/v1.0/powershell.exe'
        args = [str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                '-File', str(Path(__file__).with_name(self.script_name)),
                '-Operation', operation, '-TaskName', name]
        if xml_path:
            args += ['-XmlPath', str(xml_path)]
        try:
            result = self.execute(args, capture_output=True, timeout=10, check=False,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            payload = json.loads(result.stdout.decode('utf-8-sig') if isinstance(result.stdout, bytes) else result.stdout)
            if result.returncode or payload.get('error'):
                raise WatchdogError('task_' + operation + '_failed_' + str(payload.get('code', result.returncode)))
            xml = payload.get('xml')
            if xml is not None:
                # Scheduler XML elides default-valued fields. Use the actual COM
                # properties, rather than interpreting a missing field as proof.
                root = ET.fromstring(xml)
                for key, value in (payload.get('policy') or {}).items():
                    parent = root
                    for part in key.split('/'):
                        child = parent.find('{' + NS + '}' + part)
                        if child is None:
                            child = ET.SubElement(parent, '{' + NS + '}' + part)
                        parent = child
                    if parent.text not in (None, value):
                        raise WatchdogError('task_xml_com_mismatch')
                    parent.text = value
                return ET.tostring(root, encoding='unicode')
            return None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise WatchdogError('task_' + operation + '_unknown') from None

    def register(self, state, path):
        xml_path = Path(path).with_suffix('.xml')
        xml_path.write_text(task_xml(state), encoding='utf-16')
        self.call('register', state['task_name'], xml_path)
        xml = self.query(state)
        if xml is None:
            raise WatchdogError('task_registration_unconfirmed')

    def query(self, state):
        xml = self.call('query', state['task_name'])
        if xml is not None:
            verify_task(xml, state)
        return xml

    def delete(self, state):
        if self.query(state) is not None:
            self.call('delete', state['task_name'])
        if self.query(state) is not None:
            raise WatchdogError('task_deletion_unconfirmed')


class Watchdog:
    def __init__(self, path, scheduler=None, *, now=None, uptime=None, boot=None):
        self.path = Path(path).resolve()
        self.scheduler = scheduler or TaskScheduler()
        self.now = now or (lambda: datetime.now(UTC))
        self.uptime = uptime or boot_identity
        self.boot = boot or boot_generation
        self.view = {'status': 'NOT_REGISTERED', 'error': None}

    def _save(self, state):
        save(self.path, state)
        self.view = dict(state)

    def refresh(self):
        """Read atomic snapshots, including after the supervisor has finished."""
        if not self.path.exists():
            return
        try:
            state = read(self.path)
            failure = self.path.with_name('watchdog_invocation_error.json')
            if failure.is_file() and state['status'] not in {'CANCELLED', 'NORMAL_REQUEST_ACCEPTED'}:
                state.update(status='INVOCATION_FAILED_OR_UNKNOWN', error=read(failure).get('error', 'unknown'))
            self.view = state
        except (OSError, ValueError, KeyError, WatchdogError):
            self.view = dict(self.view, status='STATE_UNKNOWN', error='watchdog_state_read_failed')

    def arm(self, plan, run_id, command, *, emergency_allowed=False, test=False):
        if not re.fullmatch('[a-f0-9]{32}', run_id):
            raise WatchdogError('invalid_run_id')
        opened = instant(plan['open'])
        if self.now() >= opened:
            raise WatchdogError('late_watchdog_registration')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise WatchdogError('existing_watchdog_state')
        nonce = uuid.uuid4().hex
        due = opened + timedelta(minutes=90)
        state = dict(schema=1, owner=OWNER + '/' + nonce, nonce=nonce, run_id=run_id,
            task_name='MarketRadarFamily_' + ('TEST_' if test else '') + plan['session_date'].replace('-', '') + '_' + run_id,
            session_date=plan['session_date'], open=stamp(opened), deadline=stamp(due),
            expires=stamp(due + timedelta(minutes=5)), registered_at=stamp(self.now()),
            uptime_registered=self.uptime(), uptime_due=self.uptime() + (due-self.now()).total_seconds(),
            boot_generation=self.boot(), status='PREPARED', error=None,
            emergency_allowed=bool(emergency_allowed and not test), test=bool(test),
            command=str(Path(command[0]).resolve()),
            arguments=subprocess.list2cmdline([*command[1:], '--watchdog-run', str(self.path), '--nonce', nonce,
                *(['--watchdog-dry-run'] if not emergency_allowed or test else [])]),
            heartbeat=None, preservation='INCOMPLETE', pid=os.getpid())
        with lease(self.path):
            self._save(state)
            try:
                self.scheduler.register(state, self.path)
                state['status'] = 'ARMED'
                self._save(state)
            except Exception as exc:
                state.update(status='REGISTRATION_FAILED', error=type(exc).__name__ + ':' + str(exc) if isinstance(exc, WatchdogError) else type(exc).__name__)
                self._save(state)
                raise
        return dict(state)

    def cancel(self, reason='operator_cancelled'):
        if not self.path.exists():
            return
        with lease(self.path):
            state = read(self.path)
            submitted = state['status'] == 'EMERGENCY_REQUEST_ACCEPTED'
            state.update(status='CANCELLED', cancelled_at=stamp(self.now()), cancel_reason=reason)
            self._save(state)  # Durable revocation BEFORE scheduler deletion.
            try:
                self.scheduler.delete(state)
                state.update(task_removed=True, error='emergency_already_submitted' if submitted else None)
            except Exception as exc:
                state.update(task_removed=False, error='task_cancel_failed:' + type(exc).__name__)
                self._save(state)
                raise WatchdogError(state['error']) from None
            self._save(state)

    def heartbeat(self, phase, gui_at, preservation='INCOMPLETE', *, archive=None):
        if not self.path.exists():
            return
        with lease(self.path):
            state = read(self.path)
            if state['status'] == 'ARMED':
                state.update(heartbeat=stamp(self.now()), phase=phase, gui_heartbeat=gui_at,
                             preservation=preservation, archive=str(archive) if archive else None)
                self._save(state)
            else:
                self.view = dict(state)

    def normal_request(self, submit):
        with lease(self.path):
            state = read(self.path)
            if state['status'] not in {'ARMED', 'DRY_RUN_FIRED'}:
                raise WatchdogError('normal_shutdown_watchdog_not_armed')
            try:
                self.scheduler.delete(state)
            except Exception:
                state['error'] = 'normal_disarm_failed'
                self._save(state)
                raise
            state.update(status='NORMAL_REQUESTING', task_removed=True)
            self._save(state)
            try:
                submit()
            except Exception:
                state.update(status='NORMAL_REQUEST_FAILED', error='normal_shutdown_not_confirmed')
                self._save(state)
                raise
            state.update(status='NORMAL_REQUEST_ACCEPTED', requested_at=stamp(self.now()))
            self._save(state)

    def fire(self, nonce, *, submit=None, force_dry_run=False):
        """Executed by the independent task, even if every GUI thread has died."""
        with lease(self.path):
            state = read(self.path)
            if state.get('nonce') != nonce or state.get('owner') != OWNER + '/' + nonce:
                raise WatchdogError('run_identity_mismatch')
            if not NAME.fullmatch(state.get('task_name', '')) or not state['task_name'].endswith('_' + state['run_id']):
                raise WatchdogError('task_identity_mismatch')
            expected_name = ('MarketRadarFamily_' + ('TEST_' if state.get('test') else '') +
                state['session_date'].replace('-', '') + '_' + state['run_id'])
            if state['task_name'] != expected_name:
                raise WatchdogError('task_date_mismatch')
            opened, due, expires = (instant(state[k]) for k in ('open', 'deadline', 'expires'))
            if due != opened + timedelta(minutes=90) or expires != due + timedelta(minutes=5):
                raise WatchdogError('invalid_deadline')
            from zoneinfo import ZoneInfo
            if opened.astimezone(ZoneInfo('America/New_York')).date().isoformat() != state['session_date']:
                raise WatchdogError('session_date_mismatch')
            if state['status'] != 'ARMED':
                self.view = dict(state)
                return state['status']
            now, uptime = self.now(), self.uptime()
            # A persistent boot counter avoids confusing a wall-clock correction
            # with a reboot. An unavailable counter fails closed.
            reboot = uptime < state['uptime_registered'] or self.boot() != state['boot_generation']
            if reboot or now >= expires:
                state.update(status='EXPIRED', reason='reboot' if reboot else 'expired')
            elif now < due or uptime < state['uptime_due']:
                return 'TOO_EARLY'
            elif self.scheduler.query(state) is None:
                raise WatchdogError('registered_task_missing')
            else:
                alive = False
                try:
                    import psutil
                    alive = psutil.Process(state['pid']).create_time() <= instant(state['registered_at']).timestamp()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                heartbeat = state.get('heartbeat')
                gui = state.get('gui_heartbeat')
                reason = ('process_not_running' if not alive else 'supervisor_unresponsive' if not heartbeat or
                    (now-instant(heartbeat)).total_seconds() > 15 else 'gui_unresponsive' if not gui or
                    (now-instant(gui)).total_seconds() > 15 else 'deadline_without_normal_shutdown')
                permitted = state['emergency_allowed'] and not state.get('test') and not force_dry_run
                preserved = state.get('preservation') == 'VERIFIED' and state.get('archive') and Path(state['archive']).is_file()
                state.update(status='EMERGENCY_CLAIMED', reason=reason, fired_at=stamp(now), execution_pid=os.getpid(),
                    emergency_result='EMERGENCY_FORCED' if permitted else 'DRY_RUN',
                    preservation='VERIFIED_PREVIOUSLY_RECORDED' if preserved else 'INCOMPLETE')
                self._save(state)  # Refuse submission when evidence cannot be flushed.
                if not permitted:
                    state['status'] = 'DRY_RUN_FIRED'
                else:
                    try:
                        # Task lookup/fsync can take time; repeat both time and
                        # boot checks at the final OS boundary, still under lease.
                        if not (due <= self.now() < expires and self.uptime() >= state['uptime_due'] and
                                self.boot() == state['boot_generation']):
                            state.update(status='EMERGENCY_BLOCKED', error='final_time_or_boot_gate')
                        else:
                            (submit or emergency_shutdown)()
                            state['status'] = 'EMERGENCY_REQUEST_ACCEPTED'
                    except Exception:
                        state.update(status='EMERGENCY_REQUEST_FAILED_OR_UNKNOWN', error='windows_shutdown_not_confirmed')
            self._save(state)
            return state['status']


def emergency_shutdown():
    from .shutdown_test import system_shutdown_path
    result = subprocess.run([str(system_shutdown_path()), '/s', '/f', '/t', '0'],
        check=False, capture_output=True, timeout=5,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise WatchdogError('windows_shutdown_rejected_' + str(result.returncode))
