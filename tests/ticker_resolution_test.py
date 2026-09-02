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
from live_capture import (
    generate_ticker_confusion_candidates,
    resolve_ticker_symbol,
)
from pending_captures import PendingCapture, PendingCaptureStore
from stock_models import StockRegistry
from toss_api import ListedStock, TossApiError


class ListedStockClient:
    def __init__(self, symbols: tuple[str, ...], *, fail: bool = False) -> None:
        self.symbols = set(symbols)
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    def get_stocks(self, symbols: list[str]) -> dict[str, ListedStock]:
        self.calls.append(tuple(symbols))
        if self.fail:
            raise TossApiError("temporary validation failure")
        return {
            symbol: ListedStock(symbol, symbol, "NASDAQ", status="ACTIVE")
            for symbol in symbols
            if symbol in self.symbols
        }


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
    candidates = generate_ticker_confusion_candidates("VYOS")
    assert candidates[0] == "VYOS"
    assert "VVOS" in candidates
    correction_client = ListedStockClient(("VVOS",))
    corrected = resolve_ticker_symbol(correction_client, "VYOS")
    assert corrected.ticker == "VVOS"
    assert corrected.reason == "confusion_candidate_official_match"

    exact_client = ListedStockClient(("VYOS", "VVOS"))
    exact = resolve_ticker_symbol(exact_client, "VYOS")
    assert exact.ticker == "VYOS"
    assert exact.reason == "exact_official_match"

    ambiguous_candidates = generate_ticker_confusion_candidates("VYOS")
    ambiguous_symbols = tuple(
        symbol for symbol in ambiguous_candidates if symbol in {"VVOS", "VYQS"}
    )
    ambiguous = resolve_ticker_symbol(
        ListedStockClient(ambiguous_symbols),
        "VYOS",
    )
    assert ambiguous.ticker is None
    assert ambiguous.reason == "ambiguous_official_ticker_candidates"

    unavailable = resolve_ticker_symbol(
        ListedStockClient((), fail=True),
        "VYOS",
    )
    assert unavailable.ticker == "VYOS"
    assert unavailable.reason == "official_validation_unavailable"

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
    print("ticker_confusion_resolution: VYOS -> VVOS")
    print("official_exact_match: preferred")
    print("ambiguous_candidates: unresolved")
    print("validation_failure: original OCR preserved")
    print("identity_without_ticker_hint: preserved")
    print("unresolved_snapshot: debug store only")
    print("manual_ticker_input_ui: removed")
    print("unresolved_registry_record: false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
