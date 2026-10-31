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


def run(output, *, case='default'):
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
    config = FamilyConfig(runtime, data, cache, normal_shutdown=True, minimum_free_gib=0)
    config.bind_runtime()
    config_path = runtime / 'family_config.json'
    config_path.write_text(json.dumps(dict(config_version=1, project_root=str(runtime),
        data_root=str(data), cache=str(cache), normal_shutdown=True, emergency_shutdown=False)), encoding='utf-8')
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication
    from market_radar.family_ui import FamilyWindow
    from market_radar.family_mode import FamilyRunner
    from market_radar.watchdog import Watchdog
    from market_radar import __version__
    from unittest.mock import Mock, patch
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(runtime))
    class Clock:
        def now(self): return datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)
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
    from tools import family_collect as entry
    from dataclasses import replace
    frozen = bool(getattr(sys, 'frozen', False))
    selected = entry.launch_mode()[1]
    if frozen:
        assert selected == Path(sys.executable).resolve().parent / 'family_config.json'
    results = {'version': __version__, 'frozen': frozen, 'case': case, 'status': 'PENDING',
        'default_config_path': str(selected) if selected else None,
        'isolation': 'same EXE main(argv) re-entry; synthetic roots/clock/power/scheduler; no real authentication',
        'shutdown_commands': 0}
    captured = {}
    original_load = FamilyConfig.load
    def isolated_load(path, *, dry_run=False):
        results['requested_config_path'] = str(path)
        if case in {'missing-config', 'invalid-config'}:
            target = runtime / 'broken.json'
            if case == 'invalid-config': target.write_text('[]', encoding='utf-8')
            return original_load(target)
        # Check the real sidecar's structure/paths, then substitute only runtime IO.
        checked = original_load(path, dry_run=dry_run)
        results['loaded_config_flags'] = dict(normal_shutdown=checked.normal_shutdown,
            emergency_shutdown=checked.emergency_shutdown)
        return replace(config, normal_shutdown=checked.normal_shutdown,
            emergency_shutdown=checked.emergency_shutdown)
    real_create, real_error = entry.create_window, entry.startup_error
    def create(family, cfg):
        if family:
            window = FamilyWindow(cfg, runner_factory=factory)
        else:
            window = real_create(False, cfg)
        captured.update(window=window, family=family)
        return window
    def startup_error(reason, path):
        captured.update(window=real_error(reason, path), error=reason, family=False)
        return captured['window']
    argv = {'default': [], 'family': ['--family', '--config', str(config_path)],
        'manual': ['--manual'], 'dry-run': ['--family', '--config', str(config_path), '--dry-run'],
        'missing-config': ['--family', '--config', str(runtime/'missing.json')],
        'invalid-config': ['--family', '--config', str(runtime/'broken.json')]}.get(case)
    if case in {'watchdog-run', 'watchdog-cancel', 'scheduler-probe'}:
        from datetime import timedelta
        from market_radar.watchdog import stamp
        import uuid
        with patch.object(entry, 'create_window', side_effect=AssertionError('helper opened GUI')):
            if case == 'scheduler-probe':
                nonce = uuid.uuid4().hex
                probe = output / ('probe_' + nonce + '.json')
                rc = entry.main(['--scheduler-probe', str(probe), '--probe-nonce', nonce])
                assert json.loads(probe.read_text())['pid'] == os.getpid()
            else:
                wd = Watchdog(output/'watchdog.json', scheduler, now=clock.now, uptime=clock.monotonic)
                plan = dict(open=stamp(clock.now()+timedelta(minutes=90)), session_date='2026-11-02')
                wd.arm(plan, uuid.uuid4().hex, [sys.executable], test=True)
                with patch('market_radar.watchdog.Watchdog', return_value=wd):
                    args = ['--watchdog-cancel', str(wd.path)] if case == 'watchdog-cancel' else [
                        '--watchdog-run', str(wd.path), '--nonce', wd.view['nonce'], '--watchdog-dry-run']
                    rc = entry.main(args)
                assert wd.view['status'] == ('CANCELLED' if case == 'watchdog-cancel' else 'ARMED')
                wd.cancel()
            assert rc == 0 and not attempts
        results.update(status='PASS', gui_created=False, automatic_arm=False, external_attempts=attempts)
        (output/'smoke.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    results['entry_arguments'] = argv
    import time
    deadline = time.monotonic() + 20
    def inspect():
        window = captured.get('window')
        family = captured.get('family')
        if family and time.monotonic() < deadline and (window.runner is None or window.runner.state == 'STARTING' or
                (window.runner.state == 'ARMED' and (window.runner.watchdog is None or
                 window.runner.watchdog.view.get('status') in {'NOT_REGISTERED', 'PREPARED'}))):
            QTimer.singleShot(250, inspect)
            return
        try:
            assert window is not None
            if case in {'missing-config', 'invalid-config'}:
                assert captured.get('error') == 'family_config_invalid'
                results.update(status='PASS', automatic_arm=False, persistent_startup_error=True)
                window.grab().save(str(output/'error.png'))
                return
            if not family:
                assert case == 'manual' or (case == 'default' and not frozen)
                assert window.one_hour is None and not window.controller.busy and window.controller.worker is None
                window.grab().save(str(output / 'manual.png'))
                results.update(status='PASS', automatic_arm=False)
                return
            r, d = window.runner, window.dashboard
            assert r and r.state == 'ARMED', (r.state if r else None)
            assert r.controller is d.controller
            assert d.controller.config.max_ws_symbols == 150
            assert all((d.controller.paper_enabled, d.controller.paper_rotation_enabled, d.controller.opening_hour_enabled))
            assert r.watchdog.view['status'] == 'ARMED'
            assert not r.watchdog.view['emergency_allowed']
            assert r.no_shutdown == (case == 'dry-run')
            assert r.family_preflight['paths']['shared_cache'] == str(cache)
            d.poll()
            window.refresh()
            app.processEvents()
            assert '예약 완료' in window.phase.text()
            assert '23:30:00' in window.schedule.text() and '00:35:00' in window.schedule.text()
            assert '00:41:00' in window.power.text() and '01:00:00' in window.power.text()
            assert d.controller.worker is None  # 22:00 KST: before preopen preparation.
            window.grab().save(str(output / 'family.png'))
            d.show()
            d.grab().save(str(output / 'dashboard.png'))
            assert not any(name in sys.modules for name in ('paddle', 'paddleocr', 'paddlex', 'live_ui', 'execution.nhplug_client'))
            results.update(status='PASS', automatic_arm=True, settings='65min/150/Paper/Rotation/Opening',
                watchdog='MOCK_ARMED', deadline=r.watchdog.view['deadline'], loaded_ocr=False,
                path_binding='SYNTHETIC_ONLY', normal_shutdown_allowed=not r.no_shutdown,
                phase=window.phase.text(), schedule=window.schedule.text(), power=window.power.text(),
                shutdown_commands=0, external_attempts=attempts,
                scheduler_events=scheduler.events)
        except Exception as exc:
            results.update(status='FAIL', error=type(exc).__name__ + ':' + str(exc), external_attempts=attempts)
        finally:
            if family: window.stop()
            QTimer.singleShot(1000, finish)
    def finish():
        window = captured.get('window')
        family = captured.get('family')
        try:
            if family and window.runner and window.runner.active:
                QTimer.singleShot(250, finish)
                return
            window.close()
            if family:
                window.dashboard.executor.shutdown(wait=True)
                results['watchdog_after_cancel'] = window.runner.watchdog.view['status'] if window.runner and window.runner.watchdog else None
            elif not captured.get('error'):
                window.executor.shutdown(wait=True)
            if attempts:
                results['status'] = 'FAIL'
            (output / 'smoke.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            app.quit()
    QTimer.singleShot(2500, inspect)
    with patch.object(FamilyConfig, 'load', side_effect=isolated_load), \
            patch.object(entry, 'create_window', side_effect=create), \
            patch.object(entry, 'startup_error', side_effect=startup_error):
        entry.main(argv)
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
