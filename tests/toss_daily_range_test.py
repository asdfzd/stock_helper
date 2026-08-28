from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from toss_api import CANDLES_PATH, TossApiClient  # noqa: E402


class FakeResponse:
    ok = True
    status_code = 200
    headers: dict[str, str] = {}

    @staticmethod
    def json() -> dict[str, Any]:
        return {
            "result": {
                "candles": [
                    {
                        "timestamp": "2026-08-28T00:00:00-04:00",
                        "openPrice": "0.3584",
                        "highPrice": "0.4900",
                        "lowPrice": "0.2988",
                        "closePrice": "0.4791",
                        "volume": "5929609",
                        "currency": "USD",
                    }
                ],
                "nextBefore": None,
            }
        }


class CandleClient(TossApiClient):
    def __init__(self) -> None:
        super().__init__(client_id="test", client_secret="test")
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _authorized_get(
        self,
        path: str,
        *,
        params: dict[str, str],
        context: str,
    ) -> FakeResponse:
        self.calls.append((path, params))
        return FakeResponse()


def main() -> int:
    client = CandleClient()
    first = client.get_daily_price_range("cyab")
    second = client.get_daily_price_range("CYAB")

    assert first == second
    assert first.symbol == "CYAB"
    assert first.low_price == Decimal("0.2988")
    assert first.high_price == Decimal("0.4900")
    assert len(client.calls) == 1
    path, params = client.calls[0]
    assert path == CANDLES_PATH
    assert params == {
        "symbol": "CYAB",
        "interval": "1d",
        "count": "1",
        "adjusted": "true",
    }

    print("[TOSS DAILY RANGE TEST] passed")
    print("daily_candle_high_low: parsed")
    print("cache_seconds: 10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
