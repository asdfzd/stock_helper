"""Supplement initial probe: dated AAPL history and documented condition search."""
import asyncio
import json
import sys

from dotenv import dotenv_values
from run_probe import Guard, Probe, OUTPUT, ROOT, load_specs, now, protect_hashes


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    result = json.loads((OUTPUT / "probe_summary.json").read_text(encoding="utf-8"))
    values = dotenv_values(ROOT / ".env")
    key, secret = values.get("APP_KEY"), values.get("APP_SECRET")
    guard = Guard((key, secret))
    if values.get("KIWOOM_MODE") != "real" or not key or not secret:
        raise RuntimeError("Real credentials not loaded")
    result["supplement_started_utc"] = now()
    result["authentication_initial"] = dict(result["authentication"])
    result["websocket_initial"] = result.pop("websocket", {})
    probe = Probe(load_specs(), guard, result, max_calls=220)
    history = result["dated_history"] = {"days": {}, "max_calls": 220, "max_seconds": 240}
    try:
        if not probe.authenticate(key, secret):
            return 1
        for day in reversed(result["history"]["target_dates_from_daily"]):
            body = {"stex_tp": "ND", "stk_cd": "AAPL", "strt_dt": day,
                    "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}
            rows, cursor, seen = {}, None, set()
            info = history["days"][day] = {"pages": 0, "stop_reason": "page_budget"}
            for page in range(16):
                payload, next_cursor = probe.fetch("usa06011", body, f"dated_{day}_page{page+1}", cursor)
                batch = payload.get("result_list", [])
                info["pages"] += 1
                for row in batch:
                    stamp = str(row.get("cntr_tm", ""))
                    if stamp[:8] == day:
                        rows[stamp] = row
                if not batch:
                    info["stop_reason"] = "empty_or_error"
                    break
                earliest = min(str(r.get("cntr_tm", "")) for r in batch)
                if earliest <= day + "093000":
                    info["stop_reason"] = "reached_regular_open_or_earlier"
                    break
                if page == 0 and earliest[:8] > day:
                    info["stop_reason"] = "requested_date_not_honored"
                    break
                if not next_cursor or next_cursor in seen:
                    info["stop_reason"] = "no_or_repeated_continuation"
                    break
                seen.add(next_cursor)
                cursor = next_cursor
            selected = [rows[k] for k in sorted(rows)]
            info.update(rows=len(rows), earliest=min(rows, default=None), latest=max(rows, default=None))
            guard.write(OUTPUT / f"aapl_minutes_{day}.json", selected)
            probe.log("dated_history_result", date=day, **info)
            if info["stop_reason"] == "requested_date_not_honored":
                break
        asyncio.run(asyncio.wait_for(probe.websocket(5), timeout=75))
    except Exception as exc:
        result["supplement_error_type"] = type(exc).__name__
        probe.log("supplement_error", error_type=type(exc).__name__)
    finally:
        result["supplement_finished_utc"] = now()
        result["supplement_rest_call_count"] = probe.calls
        result["protected_files_unchanged"] = result["protected_before"] == protect_hashes()
        probe.log("supplement_stop", rest_calls=probe.calls, protected_files_unchanged=result["protected_files_unchanged"])
        guard.write(OUTPUT / "probe_summary.json", result)
        for path in OUTPUT.glob("*"):
            if path.suffix in {".json", ".log"}:
                if any(s in path.read_text(encoding="utf-8") for s in guard.secrets):
                    raise RuntimeError("Secret output scan failed")
        probe.session.close()
        probe.token = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
