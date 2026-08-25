from __future__ import annotations

import os
from decimal import Decimal
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from main import PriceLevel, StockCard, StockCardsView, StockData  # noqa: E402
from stock_models import StockRegistry  # noqa: E402


def analysis(
    ticker: str,
    chart_type: str,
    values: list[tuple[str, str]],
    current_price: str = "8.0000",
) -> SimpleNamespace:
    return SimpleNamespace(
        chart_type=chart_type,
        stock=SimpleNamespace(stock_code=ticker, stock_name=f"{ticker} 테스트"),
        current=SimpleNamespace(
            status="valid",
            value=Decimal(current_price),
        ),
        items=[
            SimpleNamespace(key=key, value=Decimal(value), status="valid")
            for key, value in values
        ],
    )


def merge_and_refresh(
    registry: StockRegistry,
    view: StockCardsView,
    result: SimpleNamespace,
) -> None:
    stock = registry.merge_analysis_result(result)
    view.refresh_from_registry(stock.stock_code)
    QApplication.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication([])
    registry = StockRegistry()
    view = StockCardsView(registry)

    merge_and_refresh(
        registry,
        view,
        analysis("AAAA", "daily", [("day20_floor", "7.4400"), ("day20_wall", "8.7200")]),
    )
    merge_and_refresh(
        registry,
        view,
        analysis(
            "AAAA",
            "minute",
            [
                ("buy_price", "8.4147"),
                ("rebound_price", "6.6500"),
                ("taecho", "6.5000"),
                ("corpse_wall_1", "9.2100"),
            ],
        ),
    )
    merge_and_refresh(
        registry,
        view,
        analysis("BBBB", "daily", [("moving_average_20_wall", "12.0000")], "10.0000"),
    )
    merge_and_refresh(
        registry,
        view,
        analysis(
            "BBBB",
            "minute",
            [("buy_price", "10.5000"), ("corpse_wall_1", "9.5000")],
            "10.0000",
        ),
    )

    assert len(registry.all()) == 2
    assert view.card_count == 2
    assert view.tickers == ("AAAA", "BBBB")
    assert view._row_layouts[0].count() == 2
    assert view._row_widgets[1].isHidden()
    for ticker in view.tickers:
        stock = registry.get_snapshot(ticker)
        assert stock is not None
        assert stock.daily_loaded and stock.minute_loaded

    first_card = view.cards["AAAA"]
    upper, lower = first_card._nearest_levels(8.0)
    assert upper and upper[0].kind == "매입가" and upper[0].price == 8.4147
    assert lower and lower[0].kind == "day20 바닥" and lower[0].price == 7.44

    # 동일 ticker minute 재캡처는 카드 추가 없이 minute 후보 전체를 교체한다.
    merge_and_refresh(
        registry,
        view,
        analysis(
            "AAAA",
            "minute",
            [("buy_price", "8.2000"), ("corpse_wall_1", "9.5000")],
        ),
    )
    assert view.card_count == 2
    prices = {level.price for level in view.cards["AAAA"].stock.levels}
    assert 8.2 in prices and 9.5 in prices
    assert 8.4147 not in prices and 9.21 not in prices

    selection_card = StockCard(
        StockData(
            "RANGE",
            100.0,
            [
                PriceLevel("-15%", 85.0),
                PriceLevel("-10%", 90.0),
                PriceLevel("-7%", 93.0),
                PriceLevel("+5%", 105.0),
                PriceLevel("+11%", 111.0),
                PriceLevel("+19%", 119.0),
            ],
        )
    )
    selected_upper, selected_lower = selection_card._nearest_levels(100.0)
    assert [level.price for level in selected_upper] == [105.0, 111.0]
    assert [level.price for level in selected_lower] == [93.0, 90.0]

    # 실시간 카드는 Registry 등록 순서의 처음 6개만, 위 3개/아래 3개로 배치한다.
    for ticker in ("CCCC", "DDDD", "EEEE", "FFFF", "GGGG"):
        merge_and_refresh(
            registry,
            view,
            analysis(ticker, "daily", [("moving_average_20_wall", "12.0000")]),
        )
    assert len(registry.all()) == 7
    assert view.card_count == 6
    assert view.tickers == ("AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF")
    assert view._row_layouts[0].count() == 3
    assert view._row_layouts[1].count() == 3
    assert not view._row_widgets[1].isHidden()
    assert not view._row_layouts[0].alignment() & Qt.AlignmentFlag.AlignLeft
    assert view._row_layouts[0].alignment() & Qt.AlignmentFlag.AlignHCenter

    removed = registry.remove("AAAA")
    assert removed is not None
    view.sync_from_registry()
    assert view.card_count == 6
    assert view.tickers == ("BBBB", "CCCC", "DDDD", "EEEE", "FFFF", "GGGG")
    assert "AAAA" not in view.cards

    print("[UI REGISTRY TEST] passed")
    print(f"registry_count: {len(registry.all())}")
    print(f"card_count: {view.card_count}")
    print(f"tickers: {', '.join(view.tickers)}")
    print("AAAA: daily_loaded=true minute_loaded=true")
    print("BBBB: daily_loaded=true minute_loaded=true")
    print("minute_replace_reflected: true")
    print("nearest_two_each_side: -10%,-7%,+5%,+11%")
    print("card_grid: top=3 bottom=3 max=6 centered")
    print("card_grid_single_row: 1-3 use full height")
    print("tracking_remove: registry and card removed")
    selection_card.deleteLater()
    view.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
