"""Small frozen entry point. Ordinary invocation never arms collection."""
from pathlib import Path
import sys

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(arguments=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--family', action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--dry-run', action='store_true', help='collect normally; disable both power actions')
    parser.add_argument('--watchdog-run', type=Path)
    parser.add_argument('--watchdog-cancel', type=Path)
    parser.add_argument('--watchdog-dry-run', action='store_true')
    parser.add_argument('--nonce')
    parser.add_argument('--offline-family-smoke', type=Path)
    parser.add_argument('--scheduler-probe', type=Path)
    parser.add_argument('--probe-nonce')
    args = parser.parse_args(arguments)
    if args.offline_family_smoke:
        from tools.family_smoke import run
        return run(args.offline_family_smoke)
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
    from market_radar.family_config import FamilyConfig
    if args.config:
        config = FamilyConfig.load(args.config, dry_run=args.dry_run)
        config.bind_runtime()  # Before importing provider/config/lock modules.
    elif args.family:
        parser.error('--family requires --config')
    else:
        config = None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = create_window(args.family, config)
    window.show()
    return app.exec()


def create_window(family, config=None):
    if family:
        from market_radar.family_ui import FamilyWindow
        return FamilyWindow(config)
    from market_radar.ui import RadarPanel
    return RadarPanel()


if __name__ == '__main__':
    raise SystemExit(main())
