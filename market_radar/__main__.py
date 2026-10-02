import argparse
import signal
import sys
from dataclasses import replace
from pathlib import Path

from .config import CONFIG
from .provider import JsonlReplayProvider, create_live_provider
from .runtime import RadarWorker
from .console import collect_until_stopped


def main():
    parser = argparse.ArgumentParser(description="Market Radar research collector (no trading)")
    parser.add_argument("--replay", type=Path, help="Explicit recorded/test JSONL; separate replay DB")
    parser.add_argument("--fast", action="store_true", help="Replay without wall-clock delays")
    parser.add_argument("--collect", action="store_true", help="Headless collection; requires configured provider")
    parser.add_argument("--data-dir", type=Path, default=CONFIG.data_dir)
    parser.add_argument("--cache-path", type=Path, default=None,
                        help="Explicit historical SQLite cache; omitted CLI default stays under --data-dir")
    parser.add_argument("--provider", choices=("kiwoom", "unconfigured"), default=CONFIG.provider)
    parser.add_argument("--max-ws-symbols", type=int, default=CONFIG.max_ws_symbols)
    parser.add_argument("--rvol-backfill-min-day-volume", type=float, default=CONFIG.rvol_backfill_min_day_volume)
    parser.add_argument("--rvol-backfill-min-price", type=float, default=CONFIG.rvol_backfill_min_price)
    parser.add_argument("--result-pre-target-grace-seconds", type=float, default=CONFIG.result_pre_target_grace_seconds)
    parser.add_argument("--disable-rvol-backfill", action="store_true")
    args = parser.parse_args()
    config = replace(CONFIG, data_dir=args.data_dir, historical_cache_path=args.cache_path,
                     provider=args.provider, max_ws_symbols=args.max_ws_symbols,
                     rvol_backfill_min_day_volume=args.rvol_backfill_min_day_volume,
                     rvol_backfill_min_price=args.rvol_backfill_min_price,
                     rvol_backfill_enabled=not args.disable_rvol_backfill,
                     result_pre_target_grace_seconds=args.result_pre_target_grace_seconds,
                     test_version="kiwoom_radar_v2_1" if args.provider == "kiwoom" else "market_radar_v1")
    if args.replay or args.collect:
        provider = JsonlReplayProvider(args.replay) if args.replay else create_live_provider(config)
        worker = RadarWorker(provider, config, fast_replay=args.fast)
        collect_until_stopped(worker)
        print(worker.error or f"Stopped. DB/export/logs: {worker.config.data_dir}")
        return 1 if worker.error else 0
    from PySide6.QtWidgets import QApplication
    from .ui import RadarPanel
    app = QApplication(sys.argv[:1])
    panel = RadarPanel(config=config)
    app.aboutToQuit.connect(panel.stop)
    panel.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
