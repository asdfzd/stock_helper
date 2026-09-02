from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paddle_ocr_validation import PriceResult, merge_taecho_and_absolute_half
from stock_models import StockRecord, StockRegistry
from taecho_rebreak_config import TAECHO_MERGED_METADATA_KEY
from toss_api import CurrentPrice


class SequencePriceClient:
    def __init__(self, prices: list[str]) -> None:
        self.prices = [Decimal(price) for price in prices]
        self.index = 0

    def get_current_prices(self, symbols: list[str]) -> dict[str, CurrentPrice]:
        if self.index >= len(self.prices):
            raise AssertionError("test price sequence exhausted")
        price = self.prices[self.index]
        self.index += 1
        return {
            symbol: CurrentPrice(symbol, price, f"tick-{self.index}")
            for symbol in symbols
        }


def price_result(key: str, value: str) -> PriceResult:
    decimal = Decimal(value)
    return PriceResult(
        key=key,
        item_text=key,
        item_bbox=(0, 0, 10, 10),
        price_bbox=(10, 0, 20, 10),
        raw_text=value,
        value=decimal,
        confidence=0.99,
        status="valid",
        raw_value=decimal,
    )


def minute_analysis(symbol: str, taecho: PriceResult) -> SimpleNamespace:
    return SimpleNamespace(
        chart_type="minute",
        stock=SimpleNamespace(stock_code=symbol, stock_name=f"{symbol} NAME"),
        current=SimpleNamespace(status="unavailable", value=None),
        items=[taecho],
    )


def run_prices(
    prices: list[str],
    *,
    merged: bool = True,
) -> list[tuple[Decimal, Decimal]]:
    client = SequencePriceClient(prices)
    registry = StockRegistry(api_client=client)  # type: ignore[arg-type]
    registry.register(
        StockRecord(
            "TEST",
            "TEST NAME",
            taecho=Decimal("10"),
            taecho_merged_with_absolute_half=merged,
        )
    )
    alerts: list[tuple[Decimal, Decimal]] = []
    for _price in prices:
        result = registry.refresh_current_prices()
        alerts.extend(
            (alert.wall_price, alert.current_price)
            for alert in result.taecho_rebreak_alerts
        )
    return alerts


def main() -> int:
    # A: -10% 이탈 뒤 벽 가격 재돌파 시 1회.
    assert run_prices(["10.2", "9.5", "9.0", "9.8", "10.0"]) == [
        (Decimal("10"), Decimal("10.0"))
    ]

    # B: -10% 이탈이 없으면 알림 없음.
    assert run_prices(["9.5", "9.8", "10.0"]) == []

    # C: 같은 이탈 사이클에서는 재교차해도 1회만.
    assert run_prices(["10.1", "9.0", "10.0", "9.99", "10.01"]) == [
        (Decimal("10"), Decimal("10.0"))
    ]

    # D: ALERTED 뒤 다시 -10% 이탈하면 새 사이클로 2회.
    assert run_prices(["10.1", "9.0", "10.0", "8.9", "10.1"]) == [
        (Decimal("10"), Decimal("10.0")),
        (Decimal("10"), Decimal("10.1")),
    ]

    # 시작부터 -10% 아래이면 ARMED로 간주하지 않는다.
    assert run_prices(["8.5", "8.7", "9.5", "10.0"]) == []

    # E: 병합되지 않은 일반 태초마을은 감시하지 않는다.
    assert run_prices(["10.1", "9.0", "10.0"], merged=False) == []

    # F: 1% 이내만 metadata가 남고 absolute_half 표시값은 제거된다.
    merged_items = merge_taecho_and_absolute_half(
        [price_result("taecho", "10"), price_result("absolute_half", "10.1")]
    )
    merged_taecho = next(item for item in merged_items if item.key == "taecho")
    assert merged_taecho.metadata[TAECHO_MERGED_METADATA_KEY] is True
    assert [item.key for item in merged_items] == ["taecho"]

    unmerged_items = merge_taecho_and_absolute_half(
        [price_result("taecho", "10"), price_result("absolute_half", "10.11")]
    )
    unmerged_taecho = next(item for item in unmerged_items if item.key == "taecho")
    assert TAECHO_MERGED_METADATA_KEY not in unmerged_taecho.metadata
    assert {item.key for item in unmerged_items} == {"taecho", "absolute_half"}

    metadata_registry = StockRegistry()
    stock = metadata_registry.merge_analysis_result(
        minute_analysis("META", merged_taecho), capture_id="minute-meta"
    )
    assert stock.taecho == Decimal("10")
    assert stock.absolute_half is None
    assert stock.taecho_merged_with_absolute_half is True

    # 벽 가격이 바뀌면 이전 ARMED 상태를 이어받지 않는다.
    reset_client = SequencePriceClient(["10.2", "9.0", "11.0"])
    reset_registry = StockRegistry(api_client=reset_client)  # type: ignore[arg-type]
    reset_registry.register(
        StockRecord(
            "RESET",
            "RESET NAME",
            taecho=Decimal("10"),
            taecho_merged_with_absolute_half=True,
        )
    )
    assert reset_registry.refresh_current_prices().taecho_rebreak_alerts == ()
    snapshots = reset_registry.taecho_rebreak_watch_snapshots()
    assert len(snapshots) == 1 and snapshots[0].state == "IDLE"
    assert reset_registry.refresh_current_prices().taecho_rebreak_alerts == ()
    assert reset_registry.taecho_rebreak_watch_snapshots()[0].state == "ARMED"
    replacement = price_result("taecho", "11")
    replacement.metadata[TAECHO_MERGED_METADATA_KEY] = True
    reset_registry.merge_analysis_result(
        minute_analysis("RESET", replacement), capture_id="minute-reset"
    )
    assert reset_registry.refresh_current_prices().taecho_rebreak_alerts == ()
    assert reset_registry.taecho_rebreak_watch_snapshots()[0].state == "IDLE"
    assert reset_registry.remove("RESET") is not None
    assert reset_registry.taecho_rebreak_watch_snapshots() == ()
    assert reset_registry.refresh_current_prices().requested_symbols == ()

    print("[TAECHO REBREAK TEST] passed")
    print("cases_A_to_D: alert cycles verified")
    print("starting_below_threshold: no implicit arm")
    print("case_E: unmerged taecho ignored")
    print("case_F: merged metadata preserved")
    print("wall_change_and_remove: watch reset/cleanup verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
