"""python -m tools.one_hour_runner; frozen entry uses the same main()."""
import argparse
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent first-hour research collector")
    parser.add_argument("--dry-run", action="store_true", help="synthetic offline exports; never network/shutdown")
    parser.add_argument("--no-shutdown", action="store_true", help="live collection with PC shutdown disabled")
    parser.add_argument("--headless", action="store_true", help="only with --dry-run")
    parser.add_argument("--smoke-dir", type=Path, help="only with --dry-run; screenshot and automatic GUI exit")
    args = parser.parse_args(argv)
    if (args.headless or args.smoke_dir) and not args.dry_run:
        parser.error("--headless/--smoke-dir require --dry-run")
    from runtime_paths import APP_ROOT
    from .core import Runner
    from .system import InstanceGuard, install_safe_logging
    install_safe_logging()
    root = APP_ROOT/"data"/"one_hour_runner"
    if args.dry_run:
        from .dry_run import SyntheticClock, SyntheticController, drive
        # Never inspect production .env/cache/locks in a dry run.
        root = (args.smoke_dir.resolve() if args.smoke_dir else root/"dry_runs")
        runner = Runner(root, clock=SyntheticClock(), controller_factory=SyntheticController, simulated=True)
        if args.headless:
            drive(runner)
            if sys.stdout:
                print(runner.state)
                print(runner.archive or runner.reason)
            return int(runner.state == "ERROR")
    else:
        runner = Runner(root, guard=InstanceGuard(), no_shutdown=args.no_shutdown)
    from .gui import launch
    return launch(runner, dry_run=args.dry_run, smoke_dir=args.smoke_dir)


if __name__ == "__main__":
    raise SystemExit(main())
