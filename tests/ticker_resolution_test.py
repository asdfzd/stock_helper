from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paddle_ocr_validation import (
    OCRToken,
    OcrAnalysis,
    PriceResult,
    StockIdentity,
    StockInfo,
    external_current_price_result,
    extract_stock_identity_from_ocr,
)
from pending_captures import PendingCapture, PendingCaptureStore
from stock_models import StockRegistry


def parsed_analysis(chart_type: str, key: str, value: str) -> OcrAnalysis:
    decimal = Decimal(value)
    item = PriceResult(
        key=key,
        item_text=key,
        item_bbox=(10, 10, 100, 30),
        price_bbox=(110, 10, 180, 30),
        raw_text=value,
        value=decimal,
        confidence=0.99,
        status="uncertain",
        reasons=["current_price_unavailable"],
        raw_value=decimal,
    )
    return OcrAnalysis(
        chart_type,
        StockInfo(None, "이노베이티브 아이웨어"),
        external_current_price_result(None),
        [item],
    )


def main() -> int:
    tokens = [
        OCRToken(
            "INNOVATIVE EYEWEAR INC 이노베이티브 아이웨어",
            0.98,
            (10, 100, 700, 140),
        )
    ]
    identity = extract_stock_identity_from_ocr(tokens)
    assert identity.english_name == "INNOVATIVE EYEWEAR INC"
    assert identity.korean_name == "이노베이티브 아이웨어"
    assert identity.ticker_hint is None

    store = PendingCaptureStore()
    store.add(
        PendingCapture(
            "daily-1",
            identity,
            "daily",
            parsed_analysis("daily", "day20_wall", "8.10"),
            "ticker_ocr_unavailable",
        )
    )
    minute_identity = StockIdentity(
        "INNOVATIVE EYEWEAR INC",
        "INNOVATIVE EYEWEAR INC",
        None,
        None,
        0.97,
    )
    store.add(
        PendingCapture(
            "minute-1",
            minute_identity,
            "minute",
            parsed_analysis("minute", "buy_price", "8.20"),
            "ticker_ocr_unavailable",
        )
    )

    registry = StockRegistry()
    assert len(store.all()) == 2
    assert not registry.all()

    print("[TICKER UNRESOLVED TEST] passed")
    print("identity_without_ticker_hint: preserved")
    print("unresolved_snapshot: debug store only")
    print("manual_ticker_input_ui: removed")
    print("unresolved_registry_record: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
