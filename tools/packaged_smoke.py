"""Explicit offline EXE diagnostic; no services, network, or power commands.

The ordinary launch path does not import this module. All writes stay in a NEW
caller-selected scratch directory. The build manifest contains code hashes only.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(destination):
    import subprocess
    from market_radar import __version__
    root = Path(__file__).resolve().parents[1]
    paths = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode('utf-8').split('\0')
    paths += [str(p.relative_to(root)).replace('\\', '/') for folder in ('market_radar', 'tools')
              for p in (root/folder).rglob('*.py') if '__pycache__' not in p.parts]
    source = {name: sha256(root/name) for name in sorted(set(paths))
              if name.endswith(('.py', '.spec', '.ps1')) and (root/name).is_file()
              and not name.startswith(('tests/', 'build/', 'dist/'))}
    result = dict(version=__version__, source_base_head=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(), source_sha256=source,
        exe_sha256=sha256(destination/'StockHelper.exe'), distribution='onedir',
        schema=dict(research=4, paper=2, opening=1, archive=2, master=1, rotation_extension=1),
        credentials_bundled=False)
    (destination/'build_manifest.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


def run(arguments):
    output = Path(arguments[arguments.index('--offline-gui-smoke')+1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    scratch = output/'runtime'
    scratch.mkdir()
    original_root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    attempts = []

    def boundary(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'os.system', 'os.startfile'}:
            attempts.append(event)
            raise RuntimeError('offline_external_operation_blocked')
        if event == 'subprocess.Popen':
            # Deny every process, including shell-mediated shutdown commands.
            attempts.append('blocked_process:' + Path(str(args[0])).name.lower())
            raise RuntimeError('offline_process_execution_blocked')
        if event == 'open':
            path, mode, flags = args
            if isinstance(path, (str, bytes, os.PathLike)):
                if os.fsdecode(path).lower() in {'nul', os.devnull.lower()}:
                    return
                path = Path(os.fsdecode(path)).absolute()
                if path.name == '.env':
                    raise RuntimeError('offline_credentials_read_blocked')
                if ((mode and any(c in mode for c in 'wax+')) or
                    (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))):
                    if not path.is_relative_to(output) and str(path).lower() != os.devnull:
                        raise RuntimeError('offline_write_outside_scratch')
        if event == 'sqlite3.connect':
            if str(args[0]) != ':memory:' and not Path(args[0]).absolute().is_relative_to(output):
                raise RuntimeError('offline_database_outside_scratch')

    # Use the packaged model assets without accessing source/user caches.
    if '--ocr' in arguments:
        shutil.copytree(original_root/'.paddle-cache/official_models', scratch/'.paddle-cache/official_models')
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[key] = str(scratch)
    import tempfile
    tempfile.tempdir = str(scratch)
    import platform
    platform.uname()  # Cache Windows version before denying all child processes.
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.dont_write_bytecode = True
    sys.addaudithook(boundary)
    result = dict(frozen=bool(getattr(sys, 'frozen', False)), executable=str(sys.executable),
                  runtime_root=str(scratch), original_runtime_root=str(original_root),
                  shutdown_commands=0, external_attempts=attempts, status='FAIL')
    log = (output/'smoke.log').open('w', encoding='utf-8')
    sys.stdout = sys.stderr = log
    try:
        import runtime_paths
        runtime_paths.APP_ROOT = scratch
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(scratch/'settings'))
        import live_ui
        from market_radar import __version__
        from market_radar.providers.kiwoom_calendar import ExchangeCalendar
        import market_radar.one_hour_archive
        import market_radar.paper_rotation_v2.paired
        import market_radar.opening_hour.service
        import market_radar.providers.kiwoom_rest
        contract = Path(market_radar.providers.kiwoom_rest.__file__).parent/'kiwoom_contract.json'
        assert json.loads(contract.read_text(encoding='utf-8'))
        ExchangeCalendar()
        result['version'] = __version__
        result['imports'] = 'PASS'
        if '--ocr' in arguments:
            reader = live_ui.create_reader()
            result['ocr_model_initialization'] = 'PASS'
            del reader
        else:
            result['ocr_model_initialization'] = 'NOT_RUN'

        def inspect(window):
            try:
                panel = window._market_radar_panel
                panel.poll()
                assert window.isVisible() and panel.isVisible()
                assert panel.controller.state == 'STOPPED' and panel.controller.worker is None
                assert panel.one_hour is None and panel.one_hour_no_shutdown.isChecked()
                assert panel.shutdown_test.start.text() == '30초 강제 종료 테스트'
                assert panel.shutdown_test.start.isEnabled()
                assert not panel.shutdown_test.cancel.isEnabled()
                assert not panel.shutdown_test.experiment.active
                assert __version__ in panel.windowTitle()
                for i in range(4):
                    window._show_page(i)
                    assert window.page_stack.currentIndex() == i
                window._show_page(0)
                assert window.grab().save(str(output/'stock_helper.png'))
                assert panel.grab().save(str(output/'market_radar.png'))
                result.update(status='PASS', gui='PASS', button='PASS', defaults='PASS')
            except Exception as exc:
                result['error'] = type(exc).__name__
            finally:
                QApplication.instance().quit()

        def no_services(window):
            window._open_market_radar()
            QTimer.singleShot(2000, lambda: inspect(window))

        live_ui.LiveStockWindow.start_capture_services = no_services
        result['exit_code'] = live_ui.main()
        # Optional dependency compiler probes can fail safely on import. Their
        # denied execution is recorded; any network/power attempt fails the smoke.
        if result['exit_code'] or any(not event.startswith('blocked_process:') or
                                     'shutdown' in event for event in attempts):
            result['status'] = 'FAIL'
    except Exception as exc:
        result['error'] = type(exc).__name__
        import traceback
        traceback.print_exc()
    finally:
        (output/'smoke.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        log.flush()
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    if '--manifest' in sys.argv:
        manifest(Path(sys.argv[sys.argv.index('--manifest')+1]).resolve())
    else:
        raise SystemExit(run(sys.argv[1:]))
