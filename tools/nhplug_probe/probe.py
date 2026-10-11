"""Run from the project root with .venv-paddle/Scripts/python.exe.

No response dumps, token cache, account files, DB writes or order calls.
"""
from __future__ import annotations

import argparse
import importlib.metadata
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from market_radar.execution.nhplug_client import NhplugClient, NhplugClientError


def main(argv=None, *, client_factory=NhplugClient):
    parser = argparse.ArgumentParser(description="Namuh read-only connection probe")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--accounts", action="store_true", help="Account query (already included by default)")
    parser.add_argument("--realtime", action="store_true", help="Also test one RC execution subscription")
    parser.add_argument("--realtime-timeout", type=float, default=15)
    args = parser.parse_args(argv)
    stage = "SDK"

    def report(label, value):
        print(f"[NHPLUG] {label}: {value}", flush=True)

    try:
        # Loading/credentials stay behind the adapter; no raw SDK diagnostic output.
        version = importlib.metadata.version("nhplug")
        client = client_factory()
        report("SDK", "OK " + version)
        stage = "credentials"
        configured = client.configuration_status()["credentials_configured"]
        report(stage, "configured" if configured else "missing")
        if not configured:
            raise NhplugClientError("credentials_missing")
        stage = "auth"
        client.authenticate()
        report(stage, "OK")
        stage = "quote"
        quote = client.overseas_current_price(args.ticker)
        report("quote " + quote["ticker"], "OK" if quote["price"] is not None else "FAIL")
        report("price", quote["price"])
        if quote["reason"]:
            report("quote reason", quote["reason"])
        stage = "accounts"
        accounts = client.list_accounts()
        report(stage, "OK")
        report("account count", len(accounts))
        usable = client.usable_accounts(accounts)
        report("usable live accounts", len(usable))
        for row in accounts:
            report("account", row["account"])
            report("account type", row["account_type"])
        if not accounts:
            report("account availability", "NO_ACCOUNTS (connection succeeded)")
        elif not usable:
            report("account availability", "NO_USABLE_LIVE_ACCOUNTS (connection succeeded)")
        realtime = None
        if args.realtime:
            stage = "WebSocket"
            realtime = client.realtime_execution(args.ticker, args.realtime_timeout)
            report(stage, realtime["status"])
            report("WS subscription", realtime["subscription_audit"])
            report("WS ACK code", realtime["ack_code"])
            for key in ("connected", "subscribed", "unsubscribe_sent", "unsubscribed", "closed"):
                report("WS " + key, realtime[key])
            if realtime["tick"]:
                tick = realtime["tick"]
                report("tick", f"{tick['ticker']} price={tick['price']} received_at={tick['received_at']}")
            if realtime["reason"]:
                report("WS reason", realtime["reason"])
                report("WS ACK shape", realtime["ack_shape"])
        passed = quote["price"] is not None and (not realtime or realtime["status"] != "FAIL")
        result = "FAIL" if not passed else (realtime["status"] if realtime else "PASS")
        report("order API calls", "0 / not called")
        report("RESULT", result)
        return 0 if passed else 1
    except NhplugClientError as exc:
        report(stage, "FAIL " + str(exc))
    except Exception:
        # Never print an SDK exception, traceback, URL query or untrusted message.
        report(stage, "FAIL (dependency/configuration/transport error; details suppressed)")
    report("order API calls", "0 / not called")
    report("RESULT", "FAIL")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[NHPLUG] RESULT: interrupted", flush=True)
        raise SystemExit(130)
