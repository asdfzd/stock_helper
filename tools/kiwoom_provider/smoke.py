"""Bounded REAL read-only smoke, separate DB. AAPL benchmark keeps its real source flags."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market_radar.config import CONFIG
from market_radar.providers.kiwoom import KiwoomProvider
from market_radar.runtime import RadarWorker


class SmokeProvider(KiwoomProvider):
    def _refresh_universe(self):
        super()._refresh_universe()
        # Four live source lists are unchanged. Limit this diagnostic to AAPL plus
        # one actual high-volume candidate; no synthetic prices or source ranks.
        with self.lock:
            volume = sorted((c for c in self.candidates.values() if "volume" in c["sources"]),
                            key=lambda c: c["source_ranks"]["volume"])
            self.selected = {"AAPL": "ND"}
            for candidate in volume:
                if candidate["ticker"] != "AAPL":
                    self.selected[candidate["ticker"]] = candidate["exchange"]
                    break
            desired = dict(self.selected)
        self.ws.set_symbols(desired)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=int,default=150,choices=range(30,241))
    args=parser.parse_args()
    folder=ROOT/'data'/'kiwoom_provider_smoke'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    config=replace(CONFIG,data_dir=folder,max_ws_symbols=2)
    provider=SmokeProvider(config)
    worker=RadarWorker(provider,config)
    started=time.monotonic()
    worker.start()
    try:
        while worker.running and time.monotonic()-started < args.seconds:
            time.sleep(.25)
    finally:
        worker.stop()
        worker.thread.join(timeout=55)
    if worker.running:
        raise RuntimeError('Smoke shutdown timed out')
    with sqlite3.connect(config.db_path) as db:
        snapshots=db.execute('SELECT COUNT(*) FROM radar_snapshots').fetchone()[0]
        metrics=db.execute('SELECT COUNT(rvol),COUNT(return_5m),COUNT(vwap) FROM radar_snapshots').fetchone()
        symbols=[r[0] for r in db.execute('SELECT DISTINCT ticker FROM radar_snapshots')]
    cache_path=folder/'market_radar_cache'/'kiwoom_v1.db'
    with sqlite3.connect(cache_path) as db:
        days=db.execute('SELECT ticker,COUNT(*) FROM cache_days WHERE complete=1 GROUP BY ticker').fetchall()
        raw=db.execute('SELECT kind,COUNT(*) FROM raw_records GROUP BY kind').fetchall()
        source_ids=[r[0] for r in db.execute("SELECT DISTINCT json_extract(payload,'$.api_id') FROM raw_records WHERE kind='REST'")]
    summary={'mode':'real','duration_seconds':time.monotonic()-started,'worker_error':worker.error,
        'failed_batches':worker.failed_batches,'stopped':not worker.running,'snapshots':snapshots,
        'rvol_values':metrics[0],'return_5m_values':metrics[1],'vwap_values':metrics[2],
        'tickers':symbols,'universe_size':len(provider.candidates),'ws_counts':provider.ws.counts,
        'ws_generations':provider.ws.generation,'complete_cache_days':days,'raw_records':raw,
        'actual_rest_trs':source_ids,'rest_request_count':provider.rest.request_count,
        'csv_files':[str(p.relative_to(folder)) for p in (folder/'export').rglob('*.csv')],
        'data_directory':str(folder),'smoke_policy':'AAPL benchmark + one actual volume-source candidate; real source membership preserved'}
    (folder/'smoke_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 1 if worker.error or worker.failed_batches or not snapshots else 0


if __name__=='__main__':
    raise SystemExit(main())
