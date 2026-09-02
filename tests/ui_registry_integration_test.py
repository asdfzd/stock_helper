from __future__ import annotations

import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from main import PriceArea, PriceLevel, StockCard, StockCardsView, StockData  # noqa: E402
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

    upper_wall_card = StockCard(
        StockData(
            "UPPER-WALL",
            100.0,
            [
                PriceLevel("buy", 105.0, "buy_price"),
                PriceLevel("rebound", 110.0, "rebound_price"),
                PriceLevel("wall", 145.0, "corpse_wall_1"),
            ],
        )
    )
    wall_upper, wall_lower = upper_wall_card._nearest_levels(100.0)
    assert wall_lower == []
    assert [level.price for level in wall_upper] == [105.0, 145.0]
    assert not wall_upper[0].pinned_to_upper_edge
    assert wall_upper[1].pinned_to_upper_edge
    upper_wall_card.price_area.set_prices(100.0, wall_upper, wall_lower)
    assert upper_wall_card.price_area._price_span() == 10.0
    assert upper_wall_card.price_area._level_y(
        145.0,
        150.0,
        120.0,
        pinned_to_upper_edge=wall_upper[1].pinned_to_upper_edge,
    ) == 30.0
    assert "+45.00%" in upper_wall_card.price_area._label_text(wall_upper[1])
    upper_text_positions = PriceArea._upper_text_positions([35.0, 30.0], 150.0)
    assert upper_text_positions[1] >= PriceArea.TEXT_TOP_MARGIN
    assert upper_text_positions[0] - upper_text_positions[1] >= PriceArea.MIN_TEXT_GAP
    lower_text_positions = PriceArea._lower_text_positions(
        [265.0, 270.0],
        150.0,
        300.0,
    )
    assert lower_text_positions[1] <= 300.0 - PriceArea.TEXT_BOTTOM_MARGIN
    assert lower_text_positions[1] - lower_text_positions[0] >= PriceArea.MIN_TEXT_GAP

    assert PriceArea._level_color(
        PriceLevel("매입가", 100.0, "buy_price")
    ).getRgb()[:3] == (0, 0, 0)
    assert PriceArea._level_color(
        PriceLevel("태초마을", 100.0, "taecho")
    ).getRgb()[:3] == (255, 0, 255)
    assert PriceArea._level_color(
        PriceLevel("절대값 half", 100.0, "absolute_half")
    ).getRgb()[:3] == (0, 128, 0)
    assert PriceArea._level_color(
        PriceLevel("day20 바닥", 100.0, "day20_floor")
    ).getRgb()[:3] == (77, 171, 247)

    proximity_card = StockCard(
        StockData(
            "NEAR",
            100.0,
            [PriceLevel("매입가", 100.0, "buy_price")],
        )
    )
    proximity_card._proximity_started[("buy_price", 100.0)] = time.monotonic() - 125
    proximity_levels = proximity_card._levels_with_proximity(103.9)
    assert proximity_levels[0].dwell_minutes == 2
    proximity_card._levels_with_proximity(104.1)
    assert not proximity_card._proximity_started

    touch_card = StockCard(
        StockData(
            "TOUCH",
            98.0,
            [
                PriceLevel("이평33", 100.0, "moving_average_33_wall"),
                PriceLevel("day20 바닥", 90.0, "day20_floor"),
            ],
        )
    )
    initial_touch_levels = touch_card._levels_with_proximity(98.0)
    assert not initial_touch_levels[0].touched
    approached_levels = touch_card._levels_with_proximity(99.0)
    assert not approached_levels[0].touched
    retreated_levels = touch_card._levels_with_proximity(97.5)
    assert retreated_levels[0].touched
    assert not retreated_levels[1].touched
    persisted_levels = touch_card._levels_with_proximity(94.0)
    assert persisted_levels[0].touched
    touch_card.price_area._current_price = 94.0
    touch_label = touch_card.price_area._label_text(persisted_levels[0])
    assert touch_label.endswith("· 터치")
    assert "근처" not in touch_label

    untouched_card = StockCard(
        StockData(
            "MISS",
            96.0,
            [PriceLevel("이평33", 100.0, "moving_average_33_wall")],
        )
    )
    untouched_card._levels_with_proximity(96.0)
    assert not untouched_card._levels_with_proximity(95.0)[0].touched

    crossed_card = StockCard(
        StockData(
            "CROSS",
            101.0,
            [PriceLevel("시체소굴", 100.0, "corpse_wall_1")],
        )
    )
    crossed_card._levels_with_proximity(101.0)
    assert crossed_card._levels_with_proximity(99.0)[0].touched

    daily_range_card = StockCard(
        StockData(
            "DAY-RANGE",
            90.0,
            [
                PriceLevel("wall", 100.0, "corpse_wall_1"),
                PriceLevel("floor", 95.0, "day20_floor"),
            ],
            day_low=80.0,
            day_high=98.0,
        )
    )
    daily_range_levels = daily_range_card._levels_with_proximity(90.0)
    assert daily_range_levels[0].touched
    assert not daily_range_levels[1].touched
    daily_range_card.update_stock(
        StockData(
            "DAY-RANGE",
            90.0,
            [PriceLevel("wall", 100.0, "corpse_wall_1")],
            day_low=80.0,
            day_high=90.0,
        )
    )
    assert daily_range_card._levels_with_proximity(90.0)[0].touched

    missed_range_card = StockCard(
        StockData(
            "DAY-RANGE-MISS",
            90.0,
            [PriceLevel("wall", 100.0, "corpse_wall_1")],
            day_low=80.0,
            day_high=96.0,
        )
    )
    assert not missed_range_card._levels_with_proximity(90.0)[0].touched
    identity_layout = proximity_card.code_label.parentWidget().layout()
    assert identity_layout.itemAt(0).widget() is proximity_card.code_label
    assert identity_layout.itemAt(1).widget() is proximity_card.status_label
    top_layout = proximity_card.findChild(QWidget, "cardHeaderTopRow").layout()
    delete_item = top_layout.itemAt(top_layout.count() - 1)
    assert delete_item.widget() is proximity_card.delete_button
    assert delete_item.alignment() & Qt.AlignmentFlag.AlignTop
    assert delete_item.alignment() & Qt.AlignmentFlag.AlignRight
    assert not any(
        button.text() in {"ON", "OFF"}
        for button in proximity_card.findChildren(QPushButton)
    )
    assert proximity_card.halt_label.objectName() == "circuitBreakerStatus"
    assert proximity_card.price_area.objectName() == "priceArea"
    assert proximity_card.findChild(QWidget, "cardHeader").height() == 104

    scaled_area = proximity_card.price_area
    scaled_area.set_prices(
        100.0,
        [PriceLevel("매입가", 140.0, "buy_price")],
        [PriceLevel("day20 바닥", 80.0, "day20_floor")],
    )
    assert scaled_area._price_span() == 40.0

    # 1~3개는 중앙 한 줄, 4개 이상은 큰 폭을 유지한 가로 스크롤 한 줄이다.
    merge_and_refresh(
        registry,
        view,
        analysis("CCCC", "daily", [("moving_average_20_wall", "12.0000")]),
    )
    assert view.card_count == 3
    assert view._row_layouts[0].count() == 3
    assert view._row_widgets[1].isHidden()
    assert all(card.header.height() == 104 for card in view.cards.values())

    merge_and_refresh(
        registry,
        view,
        analysis("DDDD", "daily", [("moving_average_20_wall", "12.0000")]),
    )
    assert view.card_count == 4
    assert view._row_layouts[0].count() == 4
    assert view._row_layouts[1].count() == 0
    assert view._row_widgets[1].isHidden()
    assert view._row_layouts[0].alignment() & Qt.AlignmentFlag.AlignLeft
    assert all(card.header.height() == 104 for card in view.cards.values())
    assert all(card.minimumWidth() == 440 for card in view.cards.values())

    for ticker in ("EEEE", "FFFF", "GGGG"):
        merge_and_refresh(
            registry,
            view,
            analysis(ticker, "daily", [("moving_average_20_wall", "12.0000")]),
        )
    assert len(registry.all()) == 7
    assert view.card_count == 6
    assert view.tickers == ("AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "FFFF")
    assert view._row_layouts[0].count() == 6
    assert view._row_layouts[1].count() == 0
    assert view._row_widgets[1].isHidden()
    assert view._row_layouts[0].alignment() & Qt.AlignmentFlag.AlignLeft

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
    print("price_colors: buy/rebound black, taecho magenta, absolute_half green")
    print("generic_price_color: day20 floor blue")
    print("proximity_dwell: ±4% continuous minutes and reset verified")
    print("wall_touch: ±3% approach then retreat/cross-down verified")
    print("status_position: immediately right of ticker")
    print("header_controls: delete X only, fixed at top-right")
    print("chart_layout: RGB(255,255,255), 104px header")
    print("chart_scale: proportional to farthest visible price")
    print("card_layout: 4-6 left-to-right horizontal scroll, 440px minimum")
    print("card_grid_single_row: 1-3 use full height")
    print("tracking_remove: registry and card removed")
    selection_card.deleteLater()
    proximity_card.deleteLater()
    touch_card.deleteLater()
    untouched_card.deleteLater()
    crossed_card.deleteLater()
    daily_range_card.deleteLater()
    missed_range_card.deleteLater()
    view.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
