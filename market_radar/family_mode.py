"""Family orchestration around the existing single Dashboard collector."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import partial
from pathlib import Path
import subprocess
import sys
import threading
import time

from .family_config import FamilyBlocked, windows_clock_check
from .models import ET, instant, utc
from .one_hour import DashboardOneHour, DashboardGuard
from .watchdog import Watchdog, WatchdogError, save
from tools.one_hour_runner.core import plan_for, runner_config
from tools.one_hour_runner.system import RunBlocked, WindowsShutdown


def family_plan(config, clock, calendar):
    plan = plan_for(runner_config(config.data_root / 'family_plan', config.cache), clock, calendar)
    # The general runner may pick the next trading day. This daily launcher must
    # reject holidays, after-close launches and all same-day late launches.
    if plan['session_date'] != clock.now().astimezone(ET).date().isoformat():
        raise FamilyBlocked('no_remaining_preopen_session_today')
    return plan


def executable_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/family_collect.py')]


class CoordinatedShutdown:
    def __init__(self, runner, adapter=None):
        self.runner = runner
        self.adapter = adapter or WindowsShutdown(partial(subprocess.run, timeout=10))

    def request(self):
        if self.runner.watchdog is None:
            raise WatchdogError('watchdog_not_registered')
        self.runner.watchdog.normal_request(self.adapter.request)

    def cancel(self):
        return self.adapter.cancel()


class FamilyRunner(DashboardOneHour):
    def __init__(self, controller, root, config, *, watchdog_factory=Watchdog,
                 clock_check=windows_clock_check, command=None, **kwargs):
        self.family_config = config
        self.watchdog_factory = watchdog_factory
        self.clock_check = clock_check
        self.command = command or executable_command()
        self.watchdog = None
        self.watchdog_error = None
        self.gui_heartbeat = None
        self.next_heartbeat = 0
        self.cancel_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='family-power-cancel')
        self.cancel_future = None
        self.refresh_future = None
        self.next_refresh = 0
        self.family_preflight = None
        kwargs.setdefault('guard', DashboardGuard())
        kwargs['no_shutdown'] = not config.normal_shutdown
        adapter = kwargs.pop('shutdown', None)
        super().__init__(controller, root, **kwargs)
        self.shutdown = CoordinatedShutdown(self, adapter)

    def start(self, *, cache, provider=None):
        # All IO here is on the supervisor thread, never the GUI or feed queue.
        if self.existing_controller.busy or self.folder is not None:
            raise RunBlocked('collector_already_running')
        try:
            paths = self.family_config.inspect_paths()
            timing = self.clock_check(self.family_config.expected_windows_timezone)
            planned = family_plan(self.family_config, self.clock, self.calendar)
        except FamilyBlocked as exc:
            raise RunBlocked(str(exc)) from None
        self.family_preflight = dict(paths=paths, timing=timing, plan=planned)
        super().start(cache=cache, provider=provider)
        if self.state != 'ARMED' or self.cancel_event.is_set() or self.shutdown_cancel_event.is_set():
            return
        if any(self.plan[k] != planned[k] for k in ('session_date', 'open', 'stop', 'official_close')):
            raise FamilyBlocked('session_plan_changed')
        save(self.folder / 'family_preflight.json', self.family_preflight)
        path = self.folder.parent / 'watchdogs' / self.summary['run_id'] / 'watchdog.json'
        self.watchdog = self.watchdog_factory(path)
        try:
            self.watchdog.arm(self.plan, self.summary['run_id'], self.command,
                              emergency_allowed=self.family_config.emergency_shutdown)
            self._record('family_watchdog_registered', task_name=self.watchdog.view['task_name'],
                         emergency_allowed=self.family_config.emergency_shutdown)
            if self.clock.now() >= instant(self.plan['open']):
                raise RunBlocked('watchdog_registration_completed_after_open')
            # Task Scheduler IPC is pre-open setup, not an unobserved live gap.
            self.last_wall, self.last_mono = self.clock.now(), self.clock.monotonic()
            if self.cancel_event.is_set() or self.shutdown_cancel_event.is_set():
                self._cancel_watchdog()
        except Exception as exc:
            self.watchdog_error = 'watchdog_registration_failed:' + type(exc).__name__
            self._record(self.watchdog_error, error=True)
            raise RunBlocked(self.watchdog_error) from None

    def _cancel_watchdog(self):
        if self.watchdog:
            try:
                self.watchdog.cancel()
                self.watchdog_error = self.watchdog.view.get('error')
            except Exception as exc:
                self.watchdog_error = 'watchdog_cancel_failed:' + type(exc).__name__
        return self.watchdog_error

    def cancel_independent(self):
        # Runs even while the supervisor is stuck inside export/ZIP/provider IO.
        if self.cancel_future is None or self.cancel_future.done():
            self.cancel_future = self.cancel_executor.submit(self._cancel_watchdog)

    def refresh_watchdog(self):
        if (self.watchdog and time.monotonic() >= self.next_refresh and
                (self.refresh_future is None or self.refresh_future.done())):
            self.next_refresh = time.monotonic() + 1
            self.refresh_future = self.cancel_executor.submit(self.watchdog.refresh)

    def request_cancel(self, source='family_cancel'):
        super().request_cancel(source=source)
        self.cancel_independent()

    def request_cancel_shutdown(self):
        super().request_cancel_shutdown()
        self.cancel_independent()

    def tick(self):
        super().tick()
        if self.watchdog and self.clock.monotonic() >= self.next_heartbeat:
            self.next_heartbeat = self.clock.monotonic() + 5
            try:
                self.watchdog.heartbeat(self.state, self.gui_heartbeat,
                    (self.summary or {}).get('preservation_status', 'INCOMPLETE'), archive=self.archive)
            except Exception as exc:
                self.watchdog_error = 'watchdog_state_write_failed:' + type(exc).__name__
                self.no_shutdown = True
                self._record(self.watchdog_error, error=True)
        # Explicit no-shutdown completion and pre-open startup/auth failure must
        # revoke the task, rather than later treating them as a successful run.
        if self.finalized and not self.shutdown_may_be_pending and self.watchdog:
            if self.state == 'COMPLETE_NO_SHUTDOWN' or not (self.controller and self.controller.schedule and
                    self.controller.schedule.started_mono is not None):
                self._cancel_watchdog()

    def dispose(self):
        self.cancel_executor.shutdown(wait=False)
