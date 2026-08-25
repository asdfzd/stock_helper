from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paddle_ocr_validation import INPUTS, analyze_capture, capture_image, create_reader
from stock_models import StockRecord, StockRegistry


def bool_text(value: bool) -> str:
    return "true" if value else "false"


def print_stock(stock: StockRecord) -> None:
    print(stock.stock_code)
    print(f"stock_name: {stock.stock_name}")
    print(f"display_name: {stock.display_name}")
    print(f"daily_loaded: {bool_text(stock.daily_loaded)}")
    print(f"minute_loaded: {bool_text(stock.minute_loaded)}")
    print(f"current_price: {stock.current_price if stock.current_price is not None else 'null'}")
    print(f"price_status: {stock.price_status}")
    print(f"last_price_update: {stock.last_price_update or 'null'}")
    print(f"daily_price_candidates count: {len(stock.daily_price_candidates)}")
    print(f"minute_walls count: {len(stock.minute_walls)}")


def main() -> int:
    reader = create_reader()
    captures = {
        name: capture_image(reader, name, path) for name, path in INPUTS.items()
    }
    if captures["daily"].stock.stock_code != "SDOT":
        raise AssertionError("daily OCR stock_code가 SDOT가 아닙니다.")
    if captures["minute"].stock.stock_code != "SDOT":
        raise AssertionError("minute OCR stock_code가 SDOT가 아닙니다.")

    registry = StockRegistry()
    registry.update_ocr_values(
        "SDOT", stock_name=captures["daily"].stock.stock_name or "SDOT"
    )
    refresh = registry.refresh_current_prices()
    if refresh.updated_symbols != ("SDOT",):
        raise AssertionError(f"SDOT 현재가 갱신 실패: {refresh}")
    current_price = registry.get("SDOT").current_price  # type: ignore[union-attr]

    daily = analyze_capture(reader, captures["daily"], current_price)
    stock = registry.merge_analysis_result(daily)
    assert len(registry.all()) == 1
    assert stock.daily_loaded and not stock.minute_loaded
    print("\n================ after daily ================")
    print(f"registry_count: {len(registry.all())}\n")
    print_stock(stock)

    minute = analyze_capture(reader, captures["minute"], current_price)
    stock = registry.merge_analysis_result(minute)
    assert len(registry.all()) == 1
    assert stock.daily_loaded and stock.minute_loaded
    # 저장 OCR fixture의 인식 결과가 uncertain이면 Registry가 null을 보존하는 것도
    # 정상이다. buy inline parser 자체는 parser_regression_test.py에서 고정 검증한다.
    assert stock.rebound_price is not None
    assert stock.taecho is not None
    daily_before = list(stock.daily_price_candidates)
    minute_before = list(stock.minute_walls)
    print("\n================ after minute ================")
    print(f"registry_count: {len(registry.all())}\n")
    print_stock(stock)
    print(f"buy_price: {stock.buy_price}")
    print(f"rebound_price: {stock.rebound_price}")
    print(f"taecho: {stock.taecho}")
    print("daily_price_candidates:")
    for value in stock.daily_price_candidates:
        print(f"- {value}")
    print("minute_walls:")
    for value in stock.minute_walls:
        print(f"- {value}")

    # 같은 chart type 재입력은 해당 영역만 교체하고 minute 영역은 보존한다.
    stock = registry.merge_analysis_result(daily)
    assert stock.daily_price_candidates == daily_before
    assert stock.minute_walls == minute_before
    assert stock.minute_loaded

    # 구조 검증용 mock 종목이며 OCR 결과 파일에는 기록하지 않는다.
    registry.register(StockRecord("SOUN", "사운드하운드"))
    registry.register(StockRecord("NVDA", "엔비디아"))
    assert len(registry.all()) == 3
    assert registry.symbols() == ["SDOT", "SOUN", "NVDA"]
    print("\n================ multiple stocks ================")
    print(f"registry_count: {len(registry.all())}")
    print("symbols:")
    for symbol in registry.symbols():
        print(f"- {symbol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
