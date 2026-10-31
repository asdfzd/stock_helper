"""Dedicated frozen family launcher; source invocation stays manual by default."""
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(arguments=None):
    import argparse
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--family', action='store_true')
    mode.add_argument('--manual', action='store_true', help='diagnostic GUI; never auto-arm')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--dry-run', action='store_true', help='collect normally; disable both power actions')
    parser.add_argument('--watchdog-run', type=Path)
    parser.add_argument('--watchdog-cancel', type=Path)
    parser.add_argument('--watchdog-dry-run', action='store_true')
    parser.add_argument('--nonce')
    parser.add_argument('--offline-family-smoke', type=Path)
    parser.add_argument('--smoke-case', choices=(
        'default', 'family', 'manual', 'missing-config', 'invalid-config',
        'watchdog-run', 'watchdog-cancel', 'scheduler-probe', 'dry-run'))
    parser.add_argument('--scheduler-probe', type=Path)
    parser.add_argument('--probe-nonce')
    args = parser.parse_args(arguments)
    if args.offline_family_smoke:
        from tools.family_smoke import run
        return run(args.offline_family_smoke, case=args.smoke_case or 'default')
    if args.scheduler_probe:
        from tools.family_smoke import probe_action
        return probe_action(args.scheduler_probe, args.probe_nonce)
    if args.watchdog_run or args.watchdog_cancel:
        from market_radar.watchdog import Watchdog, save
        path = args.watchdog_run or args.watchdog_cancel
        watchdog = Watchdog(path)
        try:
            if args.watchdog_cancel:
                watchdog.cancel('explicit_cli_cancel')
            else:
                watchdog.fire(args.nonce, force_dry_run=args.watchdog_dry_run)
            return 0
        except Exception as exc:
            # Only a new sidecar, never source DB/archive verdicts or exception text.
            try:
                save(path.with_name('watchdog_invocation_error.json'), {'error': type(exc).__name__})
            except Exception:
                pass
            return 2
    if args.watchdog_dry_run or args.nonce or args.probe_nonce or args.smoke_case:
        parser.error('helper options require their corresponding helper command')
    family, path = launch_mode(args.family, args.manual, args.config)
    if family and path is None:
        parser.error('--family requires --config in source mode')
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from market_radar.family_config import FamilyConfig, FamilyBlocked
    try:
        config = FamilyConfig.load(path, dry_run=args.dry_run) if path else None
        if config:
            config.bind_runtime()  # Before importing provider/config/lock modules.
        window = create_window(family, config)
    except Exception as exc:
        if not family:
            raise
        # No exception text or credential contents in the startup screen.
        reason = str(exc) if isinstance(exc, FamilyBlocked) else type(exc).__name__
        window = startup_error(reason, path)
    window.show()
    return app.exec()


def launch_mode(family=False, manual=False, config=None):
    dedicated = (getattr(sys, 'frozen', False) and
                 Path(sys.executable).name.lower() == 'marketradarfamily.exe')
    family = family or (dedicated and not manual)
    if family and config is None and dedicated:
        config = Path(sys.executable).resolve().parent / 'family_config.json'
    return family, config


def startup_error(reason, path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget
    window = QWidget()
    window.setWindowTitle('Market Radar 자동 수집 · 시작 오류')
    window.resize(850, 320)
    layout = QVBoxLayout(window)
    label = QLabel('오류 · 자동 예약을 시작하지 못했습니다.\n'
        '이 화면을 사진으로 보내 주세요.\n\n'
        f'오류 코드: {reason}\n설정 파일: {path}\n\n'
        '수집과 PC 종료는 예약되지 않았습니다. 창을 닫아도 됩니다.')
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setStyleSheet('font-size:20px')
    layout.addWidget(label)
    return window


def create_window(family, config=None):
    if family:
        from market_radar.family_ui import FamilyWindow
        return FamilyWindow(config)
    from market_radar.ui import RadarPanel
    return RadarPanel()


if __name__ == '__main__':
    raise SystemExit(main())
