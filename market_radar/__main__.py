import argparse
import signal
import sys
from dataclasses import replace
from pathlib import Path

from .config import CONFIG
from .provider import JsonlReplayProvider, create_live_provider
from .runtime import RadarWorker


def main():
    parser = argparse.ArgumentParser(description="Market Radar research collector (no trading)")
    parser.add_argument("--replay", type=Path, help="Explicit recorded/test JSONL; separate replay DB")
    parser.add_argument("--fast", action="store_true", help="Replay without wall-clock delays")
    parser.add_argument("--collect", action="store_true", help="Headless collection; requires configured provider")
    parser.add_argument("--data-dir", type=Path, default=CONFIG.data_dir)
    parser.add_argument("--provider", choices=("kiwoom", "unconfigured"), default=CONFIG.provider)
    parser.add_argument("--max-ws-symbols", type=int, default=CONFIG.max_ws_symbols)
    args = parser.parse_args()
    config = replace(CONFIG, data_dir=args.data_dir, provider=args.provider, max_ws_symbols=args.max_ws_symbols,
                     test_version="kiwoom_radar_v1" if args.provider == "kiwoom" else "market_radar_v1")
    if args.replay or args.collect:
        provider = JsonlReplayProvider(args.replay) if args.replay else create_live_provider(config)
        worker = RadarWorker(provider, config, fast_replay=args.fast)
        signal.signal(signal.SIGINT, lambda *_: worker.stop())
        worker.start()
        while worker.running:
            worker.thread.join(timeout=0.2)
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
