"""Read-only US balance + AAPL buyable quantity smoke. No RC connection."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from market_radar.execution.nhplug_accounts import NhplugAccountProbe
from market_radar.execution.nhplug_client import NhplugClientError


def main(*, client_factory=NhplugAccountProbe):
    try:
        client = client_factory()
        client.authenticate()
        result = client.inspect_overseas_accounts("AAPL")
        print(f"[NHPLUG] live accounts: {result['live_accounts']}")
        for account in result["accounts"]:
            balance, buyable = account["balance"], account["buyable"]
            print(f"[NHPLUG] account {account['account_ref']}: {account['account']}")
            print(f"[NHPLUG] overseas balance: {balance['status']}; positions: {balance['positions']}")
            if balance.get("reason"):
                print(f"[NHPLUG] balance reason: {balance['reason']}; code: {balance.get('response_code')}")
            print(f"[NHPLUG] AAPL buyable quantity inquiry: {buyable['status']}")
            for field in ("buyable_quantity", "orderable_quantity", "max_buyable_quantity"):
                if field in buyable:
                    print(f"[NHPLUG] {field}: {buyable[field]}")
            if buyable.get("reason"):
                print(f"[NHPLUG] quantity reason: {buyable['reason']}; code: {buyable.get('response_code')}")
        print(f"[NHPLUG] usable overseas accounts: {result['usable_overseas_accounts']}")
        print(f"[NHPLUG] selected overseas account: {result['selected_account']}; {result['selection']}")
        print(f"[NHPLUG] AAPL current price: {result['quote']['price']}")
        print("[NHPLUG] order calls: 0")
        failed = any(a['balance']['status'] == 'FAIL' or a['buyable']['status'] == 'FAIL'
                     for a in result['accounts'])
        return int(failed or not result['usable_overseas_accounts'])
    except NhplugClientError as exc:
        print(f"[NHPLUG] account probe: FAIL ({exc})")
    except Exception:
        print("[NHPLUG] account probe: FAIL (details suppressed)")
    print("[NHPLUG] order calls: 0")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("[NHPLUG] account probe: interrupted")
        raise SystemExit(130)
