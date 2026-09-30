"""Read-only first-run audit. Outputs evidence outside the input directory."""
import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from market_radar.models import instant
from market_radar.providers.kiwoom_cache import normalize_bars
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.providers.kiwoom_types import classify_symbol


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)


def audit(folder):
    path = folder / "stock_helper_market_kiwoom_v1.db"
    if not path.exists():
        return {"status": "local first-run data unavailable"}
    db = readonly(path)
    cache = readonly(folder / "market_radar_cache/kiwoom_v1.db")
    n, tickers, rv, r1, r5 = db.execute("SELECT COUNT(*),COUNT(DISTINCT ticker),COUNT(rvol),COUNT(return_1m),COUNT(return_5m) FROM radar_snapshots").fetchone()
    log = "\n".join(p.read_text(encoding="utf-8") for p in (folder / "logs").glob("*.log"))
    events = ["kiwoom_backfill_complete", "kiwoom_backfill_error incomplete", "kiwoom_stale_data",
              "websocket_disconnect", "raw_queue_overflow", "kiwoom_subscription_add", "kiwoom_subscription_remove",
              "kiwoom_rest_error", "ERROR api_error", "ERROR db_error", "worker_error", "ERROR export"]
    samples = {}
    for t in ("AAPL", "NVDA", "HYG", "TQQQ", "SOXL", "ARBEW", "WALDW", "BZFDW", "SHFSW"):
        row = cache.execute("SELECT raw_json FROM symbol_catalog WHERE ticker=?", (t,)).fetchone()
        fields = json.loads(row[0]) if row else {}
        kind, evidence = classify_symbol(fields)
        samples[t] = {"type": kind, "evidence": evidence}
    candidate_symbols = {r[0] for r in db.execute("SELECT ticker FROM radar_candidates")}
    volumes = defaultdict(list)
    for (raw,) in db.execute("SELECT raw_json FROM radar_batches"):
        for o in json.loads(raw)["observations"]:
            value = o.get("metadata", {}).get("fe_raw", {}).get("13")
            if o["ticker"] in candidate_symbols and value not in (None, ""):
                volumes[o["ticker"]].append(float(value))
    distribution = {t: {"min": min(v), "max": max(v), "samples": len(v)} for t, v in volumes.items()}
    cal = ExchangeCalendar()
    minute_samples = []
    for ticker in ("AAPL", "NVDA", "SOAR", "ARBEW", "WALDW", "BZFDW", "SHFSW"):
        dates = cache.execute("SELECT exchange,session_date FROM cache_days WHERE ticker=? ORDER BY session_date DESC LIMIT 2", (ticker,)).fetchall()
        for exchange, day in dates:
            session = cal.session(day)
            if not session:
                continue
            raw = [json.loads(r[0]) for r in cache.execute("SELECT raw_json FROM minute_bars WHERE ticker=? AND exchange=? AND session_date=?", (ticker,exchange,day))]
            bars = normalize_bars(raw, session, instant(session.close_utc))
            expected = int((instant(session.close_utc)-instant(session.open_utc)).total_seconds()/60)
            gaps = []
            for left,right in zip(bars,bars[1:]):
                if left.end_utc != right.start_utc:
                    gaps.append({"from":left.end_utc,"to":right.start_utc,"before_volume":left.volume,"after_volume":right.volume})
            daily = cache.execute("SELECT raw_json FROM daily_bars WHERE ticker=? AND exchange=? AND session_date=?", (ticker,exchange,day)).fetchone()
            minute_samples.append({"ticker":ticker,"day":day,"regular_expected":expected,"regular_bars":len(bars),
                "raw_rows":len(raw),"zero_volume_regular_bars":sum(b.volume == 0 for b in bars),
                "minute_volume_sum":sum(b.volume for b in bars),"daily_acc_trde_qty":json.loads(daily[0]).get("acc_trde_qty") if daily else None,
                "interior_gap_examples":gaps[:4],"missing_regular_minutes":expected-len(bars)})
    result = {"snapshot_count":n,"unique_tickers":tickers,"candidate_count":len(candidate_symbols),
        "nonnull_pct":dict(zip(("rvol","return_1m","return_5m"),(100*x/n if n else None for x in (rv,r1,r5)))),
        "results":db.execute("SELECT horizon,status,COUNT(*) FROM radar_results GROUP BY horizon,status").fetchall(),
        "cache_days":cache.execute("SELECT complete,COUNT(*) FROM cache_days GROUP BY complete").fetchall(),
        "raw_kinds":cache.execute("SELECT kind,COUNT(*) FROM raw_records GROUP BY kind").fetchall(),
        "log_counts":{k:len(re.findall(re.escape(k),log)) for k in events},
        "subscription_symbols":{k:sum(map(int,re.findall("kiwoom_subscription_"+k+r" count=(\d+)",log))) for k in ("add","remove")},
        "candidate_day_volume_distribution":distribution,"public_types":samples,"minute_samples":minute_samples,
        "no_trade_conclusion":"MIXED_OR_UNCERTAIN",
        "limitations":["Missing timestamps and adjacent bars cannot prove no executions.",
            "Minute and daily volume session/feed scopes are not confirmed equivalent.",
            "No zero-volume or price synthesis is permitted by this evidence."],
        "evidence_hashes":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}}
    db.close(); cache.close()
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,default=ROOT/"data/kiwoom_week_01")
    parser.add_argument("--output",type=Path,default=ROOT/"data/kiwoom_v2_validation/baseline.json")
    args=parser.parse_args()
    if args.output.resolve().is_relative_to(args.input.resolve()):
        raise SystemExit("Output must be outside read-only evidence")
    result=audit(args.input)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in {"evidence_hashes","public_types","minute_samples","candidate_day_volume_distribution"}},indent=2))
