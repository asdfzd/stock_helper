"""Explicit, user-confirmed shutdown experiment, separate from collection policy.

No command on construction. Only an accepted request belongs to this instance;
an existing/unknown OS reservation must never be aborted automatically.
"""
import ctypes
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import subprocess
import time


def system_shutdown_path():
    if os.name != 'nt':
        raise OSError('windows_required')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    fn = kernel.GetSystemDirectoryW
    fn.argtypes, fn.restype = [ctypes.c_wchar_p, ctypes.c_uint], ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    size = fn(buffer, len(buffer))
    if not 0 < size < len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    path = Path(buffer.value) / 'shutdown.exe'
    if not path.is_absolute() or not path.is_file():
        raise FileNotFoundError('system_shutdown_missing')
    return path


def error_kind(code):
    return {5: 'access_denied', 1314: 'privilege_not_held',
            1115: 'shutdown_in_progress', 1190: 'shutdown_already_scheduled',
            1116: 'no_shutdown_in_progress'}.get(code, 'os_rejected')


class ShutdownExperiment:
    def __init__(self, *, execute=None, path_resolver=system_shutdown_path,
                 monotonic=time.monotonic, now=None):
        self.execute = execute or subprocess.run
        self.path_resolver = path_resolver
        self.monotonic = monotonic
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.owned = self.uncertain = False
        self.deadline = self.scheduled_at = None
        self.state, self.error, self.code = 'IDLE', None, None

    @property
    def active(self):
        return self.owned or self.uncertain

    def remaining(self):
        return max(0, math.ceil(self.deadline - self.monotonic())) if self.owned else None

    def _call(self, args):
        self.error = self.code = None
        try:
            executable = self.path_resolver()
        except OSError as exc:
            self.error, self.code = 'system_path_unavailable', getattr(exc, 'winerror', None) or exc.errno
            return None
        try:
            result = self.execute([str(executable), *args], check=False, capture_output=True,
                timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            self.error = 'request_timeout_outcome_unknown'
            return None
        except OSError as exc:
            self.error, self.code = 'process_start_failed', getattr(exc, 'winerror', None) or exc.errno
            return None
        except Exception:
            self.error = 'adapter_outcome_unknown'
            return None
        self.code = result.returncode
        if self.code:
            self.error = error_kind(self.code)
        return self.code

    def request(self):
        if self.active:
            return False
        started, wall = self.monotonic(), self.now()
        code = self._call(['/s', '/f', '/t', '30'])
        if code == 0:
            self.owned = True
            # Start at invocation, so UI never overstates the cancellation window.
            self.deadline, self.scheduled_at = started + 30, wall + timedelta(seconds=30)
            self.state = 'SCHEDULED'
            return True
        self.uncertain = self.error in {'request_timeout_outcome_unknown', 'adapter_outcome_unknown'}
        self.state = 'UNKNOWN' if self.uncertain else 'REJECTED'
        return False

    def cancel(self):
        if not self.owned:
            return False  # No /a for someone else's reservation or uncertain ownership.
        code = self._call(['/a'])
        if code == 0:
            self.owned = False
            self.state = 'CANCELLED'
            return True
        if code == 1116:
            self.owned = False
            self.state = 'NOT_PENDING'  # Not a successful abort; OS says none exists.
        else:
            self.state = 'CANCEL_FAILED'
        return False


def idle_for_experiment(dashboard):
    """Only in-memory lifecycle/thread checks; no SQL, files or hot-path hooks."""
    c, runner = dashboard.controller, dashboard.one_hour
    if dashboard.closed or dashboard._closing or c.state != 'STOPPED' or c.busy or dashboard.jobs:
        return False
    if runner and (runner.active or runner.shutdown_may_be_pending):
        return False
    if c.worker:
        from tools.one_hour_runner.core import threads_finished
        if not threads_finished(c):
            return False
    return True
