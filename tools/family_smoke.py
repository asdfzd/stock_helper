"""Explicit isolated packaging checks. Never authenticates or requests shutdown."""
from pathlib import Path
import json
import os
import sys


class MemoryScheduler:
    def __init__(self):
        self.tasks = {}
        self.events = []

    def register(self, state, path):
        from market_radar.watchdog import task_xml, WatchdogError
        name = state['task_name']
        if name in self.tasks:
            raise WatchdogError('already_exists')
        self.tasks[name] = task_xml(state)
        self.events.append(('register', name))

    def query(self, state):
        from market_radar.watchdog import verify_task
        xml = self.tasks.get(state['task_name'])
        if xml:
            verify_task(xml, state)
        self.events.append(('query', state['task_name']))
        return xml

    def delete(self, state):
        self.query(state)
        self.tasks.pop(state['task_name'], None)
        self.events.append(('delete', state['task_name']))


def run(output):
    from datetime import datetime, timezone
    import platform
    platform.uname()  # Windows subprocess version probe before the strict boundary.
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / 'runtime'
    data = runtime / 'data'
    data.mkdir(parents=True)
    (runtime / '.env').write_text('KIWOOM_MODE=real\nAPP_KEY=synthetic_only\nAPP_SECRET=synthetic_only\n')
    cache = data / 'cache.db'
    cache.write_bytes(b'SQLite format 3\x00' + bytes(84))
    attempts = []
    def boundary(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system', 'os.startfile'}:
            attempts.append(event)
            raise RuntimeError('offline_external_operation_blocked')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).absolute()
            mode, flags = args[1:]
            writing = (mode and any(c in mode for c in 'wax+')) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if str(path).lower() == 'nul':
                return
            if (writing or path.name == '.env') and not path.is_relative_to(output):
                raise RuntimeError('offline_path_outside_scratch')
        if event == 'sqlite3.connect' and str(args[0]) != ':memory:' and not Path(args[0]).absolute().is_relative_to(output):
            raise RuntimeError('offline_database_outside_scratch')
    sys.addaudithook(boundary)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    for name in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[name] = str(runtime)
    import tempfile
    tempfile.tempdir = str(runtime)
    from market_radar.family_config import FamilyConfig
    config = FamilyConfig(runtime, data, cache, minimum_free_gib=0)
    config.bind_runtime()
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from market_radar.family_ui import FamilyWindow
    from market_radar.family_mode import FamilyRunner
    from market_radar.watchdog import Watchdog
    from market_radar import __version__
    from unittest.mock import Mock
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(runtime))
    class Clock:
        def now(self): return datetime(2026, 10, 27, 12, 50, tzinfo=timezone.utc)
        def monotonic(self): return 10000.0
    clock = Clock()
    scheduler = MemoryScheduler()
    def factory(controller, root, cfg, **kwargs):
        controller.clock = clock
        return FamilyRunner(controller, root, cfg, clock=clock, guard=Mock(), power=Mock(),
            clock_check=lambda _: {'synthetic_clock': True, 'absolute_utc_verified': False},
            watchdog_factory=lambda path: Watchdog(path, scheduler, now=clock.now, uptime=clock.monotonic), **kwargs)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from tools.family_collect import create_window
    ordinary = create_window(False)
    ordinary.show()
    app.processEvents()
    assert ordinary.one_hour is None and not ordinary.controller.busy and ordinary.controller.worker is None
    ordinary.grab().save(str(output / 'ordinary.png'))
    ordinary.close()
    ordinary.executor.shutdown(wait=True)
    window = FamilyWindow(config, runner_factory=factory)
    window.show()
    results = {'version': __version__, 'frozen': bool(getattr(sys, 'frozen', False)), 'status': 'PENDING'}
    import time
    deadline = time.monotonic() + 20
    def inspect():
        if time.monotonic() < deadline and (window.runner is None or window.runner.state == 'STARTING'):
            QTimer.singleShot(250, inspect)
            return
        try:
            r, d = window.runner, window.dashboard
            assert r and r.state == 'ARMED', (r.state if r else None)
            assert r.controller is d.controller
            assert d.controller.config.max_ws_symbols == 150
            assert all((d.controller.paper_enabled, d.controller.paper_rotation_enabled, d.controller.opening_hour_enabled))
            assert r.watchdog.view['status'] == 'ARMED'
            assert not r.watchdog.view['emergency_allowed']
            assert r.family_preflight['paths']['shared_cache'] == str(cache)
            window.refresh()
            window.grab().save(str(output / 'family.png'))
            d.show()
            d.grab().save(str(output / 'dashboard.png'))
            assert not any(name in sys.modules for name in ('paddle', 'paddleocr', 'paddlex', 'live_ui', 'execution.nhplug_client'))
            results.update(status='PASS', automatic_arm=True, ordinary_launch_auto_arm=False, settings='65min/150/Paper/Rotation/Opening',
                watchdog='MOCK_ARMED', deadline=r.watchdog.view['deadline'], loaded_ocr=False,
                path_binding='SYNTHETIC_ONLY', shutdown_commands=0, external_attempts=attempts,
                scheduler_events=scheduler.events)
        except Exception as exc:
            results.update(status='FAIL', error=type(exc).__name__ + ':' + str(exc), external_attempts=attempts)
        finally:
            window.stop()
            QTimer.singleShot(1000, finish)
    def finish():
        try:
            if window.runner and window.runner.active:
                QTimer.singleShot(250, finish)
                return
            window.close()
            window.dashboard.executor.shutdown(wait=True)
            results['watchdog_after_cancel'] = window.runner.watchdog.view['status'] if window.runner and window.runner.watchdog else None
            if attempts:
                results['status'] = 'FAIL'
            (output / 'smoke.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            app.quit()
    QTimer.singleShot(2500, inspect)
    app.exec()
    return 0 if results['status'] == 'PASS' else 1


def manifest(destination):
    import hashlib
    import subprocess
    from market_radar import __version__
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
    paths += [str(p.relative_to(root)).replace('\\', '/') for folder in ('market_radar', 'tools') for p in (root/folder).rglob('*') if p.suffix in {'.py', '.ps1'}]
    paths += ['MarketRadarFamily.spec', 'build_family_windows.ps1']
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    result = dict(version=__version__, source_base_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        source_sha256={p: digest(root/p) for p in sorted(set(paths)) if p.endswith(('.py', '.ps1', '.spec')) and (root/p).is_file() and not p.startswith(('tests/', 'build/', 'dist/'))},
        exe_sha256=digest(destination / 'MarketRadarFamily.exe'), distribution='collector-only-onedir',
        credentials_bundled=False, ocr_bundled=False, emergency_shutdown_default=False,
        schema=dict(research=4, paper=2, opening=1, archive=2, master=1, rotation_extension=1, family_config=1, watchdog=1))
    (destination / 'build_manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def probe_action(path, nonce):
    """Safe scheduler plumbing probe: only a receipt, no application imports."""
    import re
    if not nonce or not re.fullmatch('[a-f0-9]{32}', nonce):
        return 2
    path = Path(path).resolve()
    if path.name != 'probe_' + nonce + '.json' or path.exists():
        return 2
    path.write_text(json.dumps({'nonce': nonce, 'pid': os.getpid(), 'shutdown_commands': 0}), encoding='utf-8')
    return 0


if __name__ == '__main__':
    manifest(Path(sys.argv[sys.argv.index('--manifest')+1]).resolve())
