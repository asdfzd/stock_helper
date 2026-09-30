"""Production provider, bounded 20-slot read-only smoke during the regular session."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from market_radar.config import CONFIG
from market_radar.models import ET, instant
from market_radar.providers.kiwoom import KiwoomProvider
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.runtime import RadarWorker
from market_radar.console import collect_until_stopped


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=int,default=1200,choices=range(60,1801))
    parser.add_argument("--max-ws-symbols",type=int,default=20)
    parser.add_argument("--data-dir",type=Path,default=ROOT/"data/kiwoom_smoke_v2_1")
    args=parser.parse_args()
    now=datetime.now(timezone.utc)
    session=ExchangeCalendar().session(now.astimezone(ET).date().isoformat())
    if not session or not instant(session.open_utc)<=now<instant(session.close_utc):
        print(json.dumps({"status":"not_run_market_closed","checked_utc":now.isoformat()}))
        return 2
    config=replace(CONFIG,data_dir=args.data_dir,max_ws_symbols=args.max_ws_symbols)
    if config.db_path.exists():
        raise SystemExit("Use a fresh data directory for each smoke")
    provider=KiwoomProvider(config)
    worker=RadarWorker(provider,config)
    start=time.monotonic()
    collect_until_stopped(worker, args.seconds)
    result={"mode":"real","started_utc":now.isoformat(),"ended_utc":datetime.now(timezone.utc).isoformat(),
        "duration_seconds":time.monotonic()-start,
        "shutdown_requested_utc":worker.shutdown_requested_utc, "shutdown_elapsed_ms":worker.shutdown_elapsed_ms,"stopped":not worker.running,"worker_error":worker.error,
        "failed_batches":worker.failed_batches,"max_ws_symbols":config.max_ws_symbols,
        "policy":"production KiwoomProvider; no selection overrides"}
    if config.db_path.exists():
        with sqlite3.connect(config.db_path.resolve().as_uri()+"?mode=ro",uri=True) as db:
            result["snapshots"]=db.execute("SELECT COUNT(*) FROM radar_snapshots").fetchone()[0]
            result["integrity"]=db.execute("PRAGMA integrity_check").fetchone()[0]
    result["quality_summaries"]=[str(p) for p in config.data_dir.glob("export/*/quality_summary.json")]
    config.data_dir.mkdir(parents=True,exist_ok=True)
    (config.data_dir/"smoke_summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
    return int(worker.running or bool(worker.error) or not result.get("snapshots"))


if __name__=="__main__":raise SystemExit(main())
