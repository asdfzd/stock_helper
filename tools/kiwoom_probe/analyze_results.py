"""Offline evidence analysis. ET/start-labelled bars are an explicit hypothesis."""
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

from run_probe import OUTPUT, Guard


def read(name):
    return json.loads((OUTPUT / name).read_text(encoding="utf-8"))


def regular_window(rows, day, end):
    return {r["cntr_tm"]: r for r in rows if day + "093000" <= r["cntr_tm"] < day + end}


def expected(day, end):
    start = datetime.strptime(day + "093000", "%Y%m%d%H%M%S")
    stop = datetime.strptime(day + end, "%Y%m%d%H%M%S")
    return {(start + timedelta(minutes=i)).strftime("%Y%m%d%H%M%S")
            for i in range(int((stop-start).total_seconds()/60))}


def main():
    summary = read("probe_summary.json")
    days = summary["history"]["target_dates_from_daily"]
    evidence = {"assumption": "cntr_tm is ET minute-start; not officially confirmed",
                "regular_interval": "[09:30,16:00)", "days": {}}
    for day in sorted(days):
        path = OUTPUT / f"aapl_minutes_{day}.json"
        if not path.exists():
            evidence["days"][day] = {"available": False}
            continue
        rows = read(path.name)
        full = regular_window(rows, day, "160000")
        first = regular_window(rows, day, "094500")
        elapsed = regular_window(rows, day, "143500")
        evidence["days"][day] = {
            "available": True, "regular_minutes": len(full),
            "regular_missing": sorted(expected(day, "160000") - set(full)),
            "first15_missing": sorted(expected(day, "094500") - set(first)),
            "first15_volume": sum(abs(int(r["trde_qty"])) for r in first.values()),
            "to1435_missing": sorted(expected(day, "143500") - set(elapsed)),
            "to1435_volume": sum(abs(int(r["trde_qty"])) for r in elapsed.values()),
            "regular_volume": sum(abs(int(r["trde_qty"])) for r in full.values()),
        }
    available = list(evidence["days"].values())
    evidence["all14_regular_minutes_present"] = len(available) == 14 and all(
        d["available"] and not d["regular_missing"] for d in available)
    if evidence["all14_regular_minutes_present"]:
        evidence["mean_first15_volume"] = sum(d["first15_volume"] for d in available)/14
        baseline = sum(d["to1435_volume"] for d in available)/14
        rows = read("aapl_minutes.json")
        current_day = max(r["cntr_tm"][:8] for r in rows)
        current = regular_window(rows, current_day, "143500")
        evidence["current_to1435_missing"] = sorted(expected(current_day, "143500") - set(current))
        evidence["current_to1435_volume"] = sum(abs(int(r["trde_qty"])) for r in current.values())
        evidence["mean_to1435_volume"] = baseline
        evidence["rvol_1435_diagnostic_only"] = evidence["current_to1435_volume"]/baseline if not evidence["current_to1435_missing"] else None
    initial = summary.get("websocket_initial", summary["websocket"])
    samples = initial.get("samples", {}).get("FE", [])
    if len(samples) >= 2:
        a, b = (sample["values"] for sample in samples[:2])
        delta = Decimal(b["14"]) - Decimal(a["14"])
        trade = abs(Decimal(b["10"])) * abs(Decimal(b["15"]))
        evidence["notional_scale_observation"] = {
            "fid14_delta": str(delta), "next_price_times_size": str(trade),
            "ratio": str(trade/delta) if delta else None,
            "interpretation": "approximately 1000 USD per FID14 unit; inference, not official unit confirmation",
        }
    summary["analysis"] = evidence
    summary["assessment"] = {
        "Current price": "PASS", "Previous close": "PASS", "Day change": "PASS", "Volume": "CONDITIONAL",
        "Market universe": "PASS", "Leader ranking source": "PASS", "1-minute bars": "PASS",
        "14-day intraday history": "PASS" if evidence["all14_regular_minutes_present"] else "CONDITIONAL",
        "RVOL": "CONDITIONAL", "VWAP": "CONDITIONAL", "HOD": "CONDITIONAL",
        "Opening Range": "CONDITIONAL", "EOD close": "CONDITIONAL", "D+1~D+3": "CONDITIONAL",
        "Realtime updates": "PASS",
    }
    summary["final_verdict"] = "KIWOOM_WITH_LIMITATIONS"
    summary["official_sources"] = {
        "portal_limits": "https://openapi.kiwoom.com/intro?dummyVal=0",
        "spec": "https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/" + summary["official_revision"] + "/kiwoom/_data/kiwoom_api_spec.json",
        "manifest": "official/manifest.json",
    }
    summary["official_limits"] = {
        "read_per_second_normal": 5, "read_per_second_peak": 3, "peak_kst": "09:00-10:00",
        "additional_us_all_per_second": 50, "additional_us_chart_per_second": 20,
        "usa10099_per_minute": 5, "sessions_per_token": 1, "realtime_symbols_per_session": 200,
        "us_realtime_conditions_per_session": 10, "condition_result_symbols": 100,
        "chart_max_page_rows": "UNKNOWN (100 observed)", "history_retention": "UNKNOWN",
        "condition_request_rate": "UNKNOWN", "continuation_max_pages": "UNKNOWN",
    }
    Guard().write(OUTPUT / "probe_summary.json", summary)
    print(json.dumps(evidence, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
