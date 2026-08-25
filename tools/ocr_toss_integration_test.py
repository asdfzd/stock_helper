from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paddle_ocr_validation import (
    INPUTS,
    analyze_capture,
    capture_image,
    create_reader,
)
from stock_models import StockRegistry


def main() -> int:
    reader = create_reader()
    # 이미지마다 전체 OCR은 한 번뿐이다. API 현재가는 OCR 완료 후 일괄 조회한다.
    captures = [capture_image(reader, name, path) for name, path in INPUTS.items()]

    registry = StockRegistry()
    for capture in captures:
        if not capture.stock.stock_code:
            print(f"error: {capture.name}에서 stock_code를 찾지 못했습니다.")
            return 1
        registry.update_ocr_values(
            capture.stock.stock_code,
            stock_name=capture.stock.stock_name or capture.stock.stock_code,
        )

    refresh = registry.refresh_current_prices()
    print(f"price_refresh_requested: {', '.join(refresh.requested_symbols)}")
    print(f"price_refresh_updated: {', '.join(refresh.updated_symbols) or 'none'}")
    if refresh.error:
        print(f"price_refresh_error: {refresh.error}")

    for capture in captures:
        stock = registry.get(capture.stock.stock_code or "")
        current_price = (
            stock.current_price
            if stock is not None and stock.price_status == "valid"
            else None
        )
        analysis = analyze_capture(reader, capture, current_price)
        registry.merge_analysis_result(analysis)

    stock = registry.get("SDOT")
    if stock is None:
        print("error: SDOT 통합 결과가 없습니다.")
        return 1

    print("\n================ integrated SDOT ================")
    print(f"stock_code: {stock.stock_code}")
    print(f"stock_name: {stock.stock_name}")
    print(f"display_name: {stock.display_name}")
    print("current_price:")
    print(f"  value: {stock.current_price if stock.current_price is not None else 'null'}")
    print("  source: toss_api")
    print(f"  status: {stock.price_status}")
    print(f"  timestamp: {stock.last_price_update or 'null'}")
    print(f"buy_price: {stock.buy_price if stock.buy_price is not None else 'null'}")
    print(f"rebound_price: {stock.rebound_price if stock.rebound_price is not None else 'null'}")
    print(f"taecho: {stock.taecho if stock.taecho is not None else 'null'}")
    print(f"absolute_half: {stock.absolute_half if stock.absolute_half is not None else 'null'}")
    print("daily_price_candidates:")
    for wall in stock.daily_price_candidates:
        print(f"- {wall}")
    print("minute_walls:")
    for wall in stock.minute_walls:
        print(f"- {wall}")
    return 0 if stock.price_status == "valid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
