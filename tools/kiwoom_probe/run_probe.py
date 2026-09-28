"""Bounded, read-only real Kiwoom market-data probe. No product imports or token cache."""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUT = HERE / "output"
OFFICIAL = OUTPUT / "official"
BASE = "https://api.kiwoom.com"
WS_URL = "wss://api.kiwoom.com:10000/api/us/websocket"
# Explicit reviewed read-only TRs. Account, order, cancel, amend and FX APIs absent.
READ_PATHS = {
    "usa20100": "/api/us/mrkcond", "usa20150": "/api/us/mrkcond",
    "usa20590": "/api/us/mrkcond", "usa06011": "/api/us/chart",
    "usa06012": "/api/us/chart", "usa10098": "/api/us/stkinfo",
    "usa10099": "/api/us/stkinfo", "usa20910": "/api/us/rkinfo",
    "usa20530": "/api/us/rkinfo", "usa20540": "/api/us/rkinfo",
    "usa20930": "/api/us/stkinfo",
}
PRIVATE_KEYS = {"token", "access_token", "appkey", "secretkey", "app_key", "app_secret", "authorization"}


def now():
    return datetime.now(timezone.utc).isoformat()


class Guard:
    def __init__(self, secrets=()):
        self.secrets = [s for s in secrets if s]

    def clean(self, value):
        if isinstance(value, dict):
            return {str(k): "[REDACTED]" if str(k).lower() in PRIVATE_KEYS else self.clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
        return value

    def write(self, path, value):
        text = json.dumps(self.clean(value), ensure_ascii=False, indent=2, allow_nan=False)
        if any(secret in text for secret in self.secrets):
            raise RuntimeError("Secret scan rejected output")
        path.write_text(text, encoding="utf-8")


def load_specs():
    data = json.loads((OFFICIAL / "kiwoom/_data/kiwoom_api_spec.json.txt").read_text(encoding="utf-8"))
    specs = {s["meta"]["API ID"]: s for s in data["apis"].values()}
    for api_id, path in READ_PATHS.items():
        if specs[api_id]["meta"].get("URL") != path or specs[api_id]["meta"].get("Method") != "POST":
            raise RuntimeError("Read-only contract differs from reviewed official spec")
    return specs


def protect_hashes():
    paths = list((ROOT / "market_radar").glob("*.py")) + [ROOT / name for name in (
        "live_ui.py", "live_capture.py", "main.py", "stock_models.py", "CODEX_HANDOFF.md",
        "paddle_ocr_validation.py", "price_refresh.py")]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class Probe:
    def __init__(self, specs, guard, result, max_calls=100):
        self.specs, self.guard, self.result = specs, guard, result
        self.session = requests.Session()
        self.session.trust_env = False
        self.token = None
        self.last_request = 0.0
        self.calls = 0
        self.max_calls = min(max_calls, 240)
        self.deadline = time.monotonic() + 240

    def log(self, event, **values):
        text = json.dumps(self.guard.clean({"timestamp_utc": now(), "event": event, **values}), ensure_ascii=False)
        with (OUTPUT / "probe_runtime.log").open("a", encoding="utf-8") as stream:
            stream.write(text + "\n")
        print(text, flush=True)

    def authenticate(self, key, secret):
        auth = self.result["authentication"]
        try:
            response = self.session.post(BASE + "/oauth2/token", json={
                "grant_type": "client_credentials", "appkey": key, "secretkey": secret,
            }, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=(10, 20), allow_redirects=False)
            payload = response.json()
            # Register returned secret BEFORE any response-derived logging.
            self.token = payload.get("token")
            if self.token:
                self.guard.secrets.append(self.token)
            auth.update(http_status=response.status_code, return_code=payload.get("return_code"),
                        token_issued=bool(self.token), success=response.ok and payload.get("return_code") == 0 and bool(self.token))
            if not auth["success"]:
                auth["return_msg"] = self.guard.clean(str(payload.get("return_msg", "")))
        except Exception as exc:
            auth.update(success=False, token_issued=False, error_type=type(exc).__name__)
        self.log("oauth_result", **auth)
        return auth["success"]

    def fetch(self, api_id, body, label, continuation=None):
        if api_id not in READ_PATHS:
            raise ValueError("TR is not on the read-only allowlist")
        allowed = {f["element"] for f in self.specs[api_id]["request"]["body"]}
        if set(body) - allowed:
            raise ValueError("Request field not present in official specification")
        if self.calls >= self.max_calls or time.monotonic() >= self.deadline:
            raise TimeoutError("Probe request/time budget reached")
        time.sleep(max(0, .55 - (time.monotonic() - self.last_request)))
        headers = {"Content-Type": "application/json;charset=UTF-8", "api-id": api_id,
                   "authorization": "Bearer " + self.token}
        if continuation:
            headers.update({"cont-yn": "Y", "next-key": continuation})
        entry = {"label": label, "api_id": api_id, "path": READ_PATHS[api_id], "request": body,
                 "timestamp_utc": now(), "continuation_request": bool(continuation)}
        self.last_request = time.monotonic()
        self.calls += 1
        payload, next_key = {}, None
        try:
            response = self.session.post(BASE + READ_PATHS[api_id], headers=headers, json=body,
                                         timeout=(10, 20), allow_redirects=False)
            payload = response.json()
            rows = payload.get("result_list", payload.get("list", []))
            rows = rows if isinstance(rows, list) else []
            entry.update(http_status=response.status_code, return_code=payload.get("return_code"),
                         return_msg=payload.get("return_msg"), success=response.ok and payload.get("return_code") == 0,
                         row_count=len(rows), response_fields=list(payload),
                         row_fields=list(rows[0]) if rows and isinstance(rows[0], dict) else [],
                         cont_yn=response.headers.get("cont-yn"), next_key_present=bool(response.headers.get("next-key")))
            if entry["success"]:
                fields = {f["element"] for f in self.specs[api_id]["response"]["body"]}
                # Store documented market fields only, never the full response/headers.
                entry["sample"] = [{k: v for k, v in r.items() if k in fields} for r in rows[:5]] if rows else {
                    k: v for k, v in payload.items() if k in fields and not isinstance(v, (dict, list))}
                if response.headers.get("cont-yn") == "Y":
                    next_key = response.headers.get("next-key")
            else:
                payload = {}
        except Exception as exc:
            entry.update(success=False, error_type=type(exc).__name__)
        self.result["rest_requests"].append(self.guard.clean(entry))
        self.log("rest_result", label=label, api_id=api_id, success=entry.get("success"),
                 return_code=entry.get("return_code"), row_count=entry.get("row_count"), cont_yn=entry.get("cont_yn"))
        self.guard.write(OUTPUT / "probe_summary.json", self.result)
        return payload, next_key

    def example(self, api_id, **overrides):
        body = json.loads(self.specs[api_id]["request_example"])
        body.update(overrides)
        return body

    def rest(self, pages):
        today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
        self.fetch("usa20100", {"stex_tp": "ND", "stk_cd": "AAPL"}, "aapl_quote")
        self.fetch("usa10098", {"stk_cd": "AAPL"}, "aapl_exchange")
        for exchange in ("ND", "NY", "NA"):
            payload, key = self.fetch("usa10099", {"stex_tp": exchange}, "stock_list_" + exchange)
            rows = payload.get("list", [])
            self.result.setdefault("stock_lists", {})[exchange] = {
                "count": len(rows), "aapl_present": any(r.get("stk_cd") == "AAPL" for r in rows),
                "more_available": bool(key), "reported_exchanges": sorted({r.get("stex_tp", "") for r in rows})}
            if exchange == "ND" and key:
                self.fetch("usa10099", {"stex_tp": exchange}, "stock_list_ND_page2", key)
        for api_id, label in (("usa20910", "change_rank"), ("usa20530", "volume_rank"), ("usa20540", "value_rank")):
            body = self.example(api_id)
            if "trde_qty_tp" in body:
                body["trde_qty_tp"] = "0"
            payload, key = self.fetch(api_id, body, label)
            if key:
                self.fetch(api_id, body, label + "_page2", key)
        self.fetch("usa20930", self.example("usa20930", flu_tp="1", tm_tp="1", tm="5", stk_cnd="0"), "price_surge_5m")
        daily, _ = self.fetch("usa06012", {"stex_tp": "ND", "stk_cd": "AAPL", "strt_dt": today,
                                           "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}, "aapl_daily")
        daily_rows = daily.get("result_list", [])
        self.guard.write(OUTPUT / "aapl_daily.json", daily_rows)
        target_dates = sorted({r.get("dt", "") for r in daily_rows if r.get("dt", "") < today}, reverse=True)[:14]
        self.result["history"] = {"target_dates_from_daily": target_dates, "date_timezone_confirmed": False}
        body = {"stex_tp": "ND", "stk_cd": "AAPL", "strt_dt": today, "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}
        all_rows, seen, key = [], set(), None
        stop_reason = "page_budget"
        for i in range(pages):
            payload, next_key = self.fetch("usa06011", body, f"aapl_minutes_page{i+1}", key)
            rows = payload.get("result_list", [])
            all_rows.extend(rows)
            if not rows:
                stop_reason = "empty_or_error"
                break
            if target_dates and min(str(r.get("cntr_tm", ""))[:8] for r in all_rows) < min(target_dates):
                stop_reason = "reached_before_14_daily_dates"
                break
            if not next_key:
                stop_reason = "no_continuation"
                break
            if next_key in seen:
                stop_reason = "repeated_continuation_key"
                break
            seen.add(next_key)
            key = next_key
        fields = {f["element"] for f in self.specs["usa06011"]["response"]["body"]}
        safe_rows = [{k: v for k, v in r.items() if k in fields} for r in all_rows]
        self.guard.write(OUTPUT / "aapl_minutes.json", safe_rows)
        counts = Counter(str(r.get("cntr_tm", ""))[:8] for r in safe_rows)
        timestamps = [r.get("cntr_tm", "") for r in safe_rows]
        self.result["history"].update(rows=len(safe_rows), unique_timestamps=len(set(timestamps)),
            earliest=min(timestamps, default=None), latest=max(timestamps, default=None),
            rows_by_timestamp_date=dict(sorted(counts.items())), stop_reason=stop_reason,
            last_continuation_available=bool(next_key), page_budget=pages)
        self.fetch("usa20150", {"stex_tp": "ND", "stk_cd": "AAPL"}, "aapl_recent_trades")
        self.fetch("usa20590", {"stex_tp": "ND", "stk_cd": "AAPL", "base_dt": today}, "aapl_daily_prices")

    async def websocket(self, seconds):
        sys.path.insert(0, str(OUTPUT / "deps"))
        from websockets.asyncio.client import connect
        # No debug frames: LOGIN contains the in-memory bearer token.
        logging.getLogger("websockets").setLevel(logging.CRITICAL)
        result = {"url": WS_URL, "login_success": False, "register_success": False,
                  "remove_success": False, "realtime_counts": {}, "samples": {}, "heartbeat_count": 0,
                  "listen_seconds": seconds, "condition_list_status": "not_attempted"}
        self.result["websocket"] = result
        items = [{"jmcode": "AAPL", "stex_tp": "ND"}]
        types = ["FE", "FT"]  # Market prints and order book ONLY. Never F4/F5 account events.
        async with connect(WS_URL, proxy=None, open_timeout=10, close_timeout=3,
                           ping_interval=None, max_size=4*1024*1024) as ws:
            async def receive(timeout):
                deadline = time.monotonic()+timeout
                while time.monotonic() < deadline:
                    raw = await asyncio.wait_for(ws.recv(), max(.01, deadline-time.monotonic()))
                    message = json.loads(raw)
                    if message.get("trnm") == "PING":
                        await ws.send(json.dumps(message))
                        result["heartbeat_count"] += 1
                        continue
                    if message.get("trnm") == "REAL":
                        for item in message.get("data", []):
                            kind = item.get("type")
                            if kind not in types:
                                continue
                            result["realtime_counts"][kind] = result["realtime_counts"].get(kind, 0)+1
                            if len(result["samples"].setdefault(kind, [])) < 5:
                                allowed = {f["element"] for f in self.specs[kind]["response"]["body"]}
                                result["samples"][kind].append({"item": item.get("item"), "stexTp": item.get("stexTp"),
                                    "received_utc": now(), "values": {k:v for k,v in item.get("values", {}).items() if k in allowed}})
                    return message
                raise TimeoutError

            await ws.send(json.dumps({"trnm": "LOGIN", "token": self.token}))
            message = await receive(10)
            result["login_return_code"] = message.get("return_code")
            result["login_success"] = message.get("trnm") == "LOGIN" and str(message.get("return_code")) == "0"
            if not result["login_success"]:
                result["login_message"] = self.guard.clean(str(message.get("return_msg", "")))
                return
            await ws.send(json.dumps({"trnm": "GCNSRLST"}))
            try:
                message = await receive(8)
                result["condition_list_return_code"] = message.get("return_code")
                result["condition_list_trnm"] = message.get("trnm")
                conditions = message.get("data", [])
                result["condition_list_count"] = len(conditions) if isinstance(conditions, list) else None
                result["condition_list_status"] = "success" if str(message.get("return_code")) == "0" else "error"
                # Names/strategy definitions are private and aren't persisted.
                # Official usa20280 response_example uses [sequence, name] pairs.
                seq = None
                if conditions and isinstance(conditions, list):
                    first = conditions[0]
                    if isinstance(first, list) and len(first) >= 2:
                        seq = first[0]
                    elif isinstance(first, dict):
                        seq = first.get("seq")
                if seq is not None:
                    await ws.send(json.dumps({"trnm": "GCNSRREQ", "seq": str(seq),
                                              "search_type": "0", "cont_yn": "N", "next_key": ""}))
                    message = await receive(8)
                    result["condition_search_return_code"] = message.get("return_code")
                    result["condition_search_count"] = len(message.get("data", []))
                    result["condition_search_cont_yn"] = message.get("cont_yn")
            except TimeoutError:
                result["condition_list_status"] = "timeout"
            await ws.send(json.dumps({"trnm": "REG", "grp_no": "1", "refresh": "1", "data": [{"item": items, "type": types}]}))
            until = time.monotonic()+seconds
            while time.monotonic() < until:
                try:
                    message = await receive(until-time.monotonic())
                except TimeoutError:
                    break
                if message.get("trnm") == "REG":
                    result["register_return_code"] = message.get("return_code")
                    result["register_success"] = str(message.get("return_code")) == "0"
                    result["register_message"] = self.guard.clean(str(message.get("return_msg", "")))
            await ws.send(json.dumps({"trnm": "REMOVE", "grp_no": "1", "data": [{"item": items, "type": types}]}))
            until = time.monotonic()+5
            while time.monotonic() < until:
                try:
                    message = await receive(until-time.monotonic())
                except TimeoutError:
                    break
                if message.get("trnm") == "REMOVE":
                    result["remove_return_code"] = message.get("return_code")
                    result["remove_success"] = str(message.get("return_code")) == "0"
                    break
        result["connection_closed"] = True


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-minute-pages", type=int, default=40, choices=range(1, 61))
    parser.add_argument("--ws-seconds", type=int, default=20, choices=range(1, 31))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    values = dotenv_values(ROOT / ".env")
    mode, key, secret = (values.get(k, "") for k in ("KIWOOM_MODE", "APP_KEY", "APP_SECRET"))
    guard = Guard((key, secret))
    specs = load_specs()
    result = {"started_utc": now(), "official_revision": json.loads((OFFICIAL / "tree.json").read_text(encoding="utf-8"))["sha"],
              "authentication": {"mode": mode if mode == "real" else "not_real", "app_key_loaded": bool(key), "app_secret_loaded": bool(secret)},
              "rest_requests": [], "protected_before": protect_hashes(),
              "security": {"read_only_allowlist": list(READ_PATHS), "token_persisted": False, "account_order_apis_called": False}}
    probe = Probe(specs, guard, result)
    probe.log("probe_start", mode=result["authentication"]["mode"])
    try:
        if mode != "real" or not key or not secret:
            result["authentication"].update(success=False, reason="Required real-mode credentials not loaded")
        elif probe.authenticate(key, secret):
            probe.rest(args.max_minute_pages)
            try:
                asyncio.run(asyncio.wait_for(probe.websocket(args.ws_seconds), timeout=75))
            except Exception as exc:
                result.setdefault("websocket", {})["error_type"] = type(exc).__name__
    except Exception as exc:
        result["probe_error_type"] = type(exc).__name__
        probe.log("probe_error", error_type=type(exc).__name__)
    finally:
        result["finished_utc"] = now()
        result["protected_files_unchanged"] = result["protected_before"] == protect_hashes()
        result["rest_call_count"] = probe.calls
        result["security"]["output_secret_scan_passed"] = True
        guard.write(OUTPUT / "probe_summary.json", result)
        # Check only generated probe output, not dependency/source archives with public examples.
        for path in OUTPUT.glob("*"):
            if path.suffix in {".json", ".log"}:
                content = path.read_text(encoding="utf-8")
                if any(s in content for s in guard.secrets):
                    raise RuntimeError("Secret output scan failed")
        probe.log("probe_stop", protected_files_unchanged=result["protected_files_unchanged"], rest_calls=probe.calls)
        probe.session.close()
        probe.token = None
    return 0 if result["authentication"].get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
