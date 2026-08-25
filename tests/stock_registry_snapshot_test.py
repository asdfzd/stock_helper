from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from stock_models import StockRegistry, build_price_candidates


def item(key: str, value: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        value=Decimal(value) if value is not None else None,
        status="valid" if value is not None else "uncertain",
    )


def analysis(chart_type: str, values: list[tuple[str, str | None]]) -> SimpleNamespace:
    return SimpleNamespace(
        chart_type=chart_type,
        stock=SimpleNamespace(stock_code="XPON", stock_name="엑스피온"),
        current=SimpleNamespace(status="unavailable", value=None),
        items=[item(key, value) for key, value in values],
    )


def main() -> int:
    registry = StockRegistry()
    daily = analysis(
        "daily",
        [
            ("day20_wall", "20.0"),
            ("day33_wall", "33.0"),
        ],
    )
    minute_a = analysis(
        "minute",
        [
            ("buy_price", "10"),
            ("rebound_price", "9"),
            ("taecho", "8"),
            ("absolute_half", "8.5"),
            ("corpse_wall_1", "1.0"),
            ("corpse_wall_2", "2.0"),
            ("corpse_wall_3", "3.0"),
            ("corpse_wall_4", "4.0"),
        ],
    )
    minute_b = analysis(
        "minute",
        [
            ("buy_price", "11"),
            ("rebound_price", None),
            ("taecho", "7"),
            ("corpse_wall_1", "5.0"),
            ("corpse_wall_2", "6.0"),
        ],
    )

    stock = registry.merge_analysis_result(daily)
    stock.current_price = Decimal("12.34")
    stock.price_status = "valid"
    daily_values = dict(stock.daily_values)
    daily_levels = list(stock.daily_price_levels)
    daily_candidates = list(stock.daily_price_candidates)

    registry.merge_analysis_result(minute_a)
    stock = registry.merge_analysis_result(minute_b)

    assert len(registry.all()) == 1
    assert stock.stock_code == "XPON"
    assert stock.daily_loaded and stock.minute_loaded
    assert stock.daily_values == daily_values
    assert stock.daily_price_levels == daily_levels
    assert stock.daily_price_candidates == daily_candidates
    assert stock.buy_price == Decimal("11")
    assert stock.rebound_price is None
    assert stock.taecho == Decimal("7")
    assert stock.absolute_half is None
    assert stock.minute_walls == [Decimal("5.0"), Decimal("6.0")]
    assert stock.minute_values == {
        "buy_price": Decimal("11"),
        "taecho": Decimal("7"),
        "corpse_wall_1": Decimal("5.0"),
        "corpse_wall_2": Decimal("6.0"),
    }
    assert not any(
        value in stock.minute_walls
        for value in map(Decimal, ("1.0", "2.0", "3.0", "4.0"))
    )
    assert stock.current_price == Decimal("12.34")
    assert stock.price_status == "valid"

    minute_values = dict(stock.minute_values)
    minute_walls = list(stock.minute_walls)
    daily_b = analysis("daily", [("day60_wall", "60.0")])
    stock = registry.merge_analysis_result(daily_b)
    assert stock.daily_values == {
        "day20_wall": Decimal("20.0"),
        "day33_wall": Decimal("33.0"),
        "day60_wall": Decimal("60.0"),
    }
    assert stock.daily_price_levels == [
        ("day20_wall", Decimal("20.0")),
        ("day33_wall", Decimal("33.0")),
        ("day60_wall", Decimal("60.0")),
    ]
    assert stock.daily_price_candidates == [
        Decimal("20.0"),
        Decimal("33.0"),
        Decimal("60.0"),
    ]

    # 이름이 같은 일봉 벽도 가격이 다르면 별도 후보로 누적한다.
    daily_c = analysis(
        "daily",
        [("day20_wall", "21.0"), ("day60_wall", "60.0")],
    )
    stock = registry.merge_analysis_result(daily_c)
    assert stock.daily_values["day20_wall"] == Decimal("21.0")
    assert ("day20_wall", Decimal("20.0")) in stock.daily_price_levels
    assert ("day20_wall", Decimal("21.0")) in stock.daily_price_levels
    assert stock.daily_price_levels.count(("day60_wall", Decimal("60.0"))) == 1
    assert Decimal("21.0") in stock.daily_price_candidates
    day20_candidates = [
        candidate
        for candidate in build_price_candidates(stock)
        if candidate.key == "day20_wall"
    ]
    assert [(candidate.label, candidate.value) for candidate in day20_candidates] == [
        ("day20 벽", Decimal("20.0")),
        ("day20 벽", Decimal("21.0")),
    ]
    assert stock.minute_values == minute_values
    assert stock.minute_walls == minute_walls
    assert stock.buy_price == Decimal("11")
    assert stock.rebound_price is None
    assert stock.taecho == Decimal("7")
    assert stock.current_price == Decimal("12.34")

    print("[SNAPSHOT TEST] passed")
    print(f"registry_count: {len(registry.all())}")
    print(f"ticker: {stock.stock_code}")
    print(f"daily_loaded: {str(stock.daily_loaded).lower()}")
    print(f"minute_loaded: {str(stock.minute_loaded).lower()}")
    print(f"buy_price: {stock.buy_price}")
    print("rebound_price: null")
    print(f"taecho: {stock.taecho}")
    print(f"minute_walls: {stock.minute_walls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
