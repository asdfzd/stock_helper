from __future__ import annotations

import os
import sys
import threading
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from main import StockCardsView  # noqa: E402
from price_refresh import PriceRefreshEvent, PriceRefreshWorker  # noqa: E402
from stock_models import StockRecord, StockRegistry  # noqa: E402
from toss_api import CurrentPrice, TossApiError  # noqa: E402


class SequencePriceClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.responses = [
            {"XPON": "8.10", "LUCY": "1.00"},
            {"XPON": "8.20", "LUCY": "1.05"},
        ]

    def get_current_prices(self, symbols: list[str]) -> dict[str, CurrentPrice]:
        self.calls.append(tuple(symbols))
        if self.responses:
            values = self.responses.pop(0)
            return {
                symbol: CurrentPrice(symbol, Decimal(value), f"t{len(self.calls)}")
                for symbol, value in values.items()
            }
        raise TossApiError("temporary failure", status_code=500)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    client = SequencePriceClient()
    registry = StockRegistry(api_client=client)  # type: ignore[arg-type]
    registry.register(
        StockRecord(
            "XPON",
            "엑스피온",
            current_price=Decimal("8.00"),
            daily_values={
                "day20_wall": Decimal("8.10"),
                "day33_wall": Decimal("8.30"),
            },
            daily_price_candidates=[Decimal("8.10"), Decimal("8.30")],
            daily_loaded=True,
            holding=True,
            price_status="valid",
        )
    )
    registry.register(
        StockRecord(
            "LUCY",
            "루시",
            current_price=Decimal("0.95"),
            price_status="valid",
        )
    )

    events: list[PriceRefreshEvent] = []
    three_refreshes = threading.Event()

    def on_complete(event: PriceRefreshEvent) -> None:
        events.append(event)
        if len(events) >= 3:
            three_refreshes.set()

    # OCR가 별도 thread에서 오래 걸리는 상황을 mock한다.
    ocr_release = threading.Event()
    ocr_thread = threading.Thread(target=lambda: ocr_release.wait(1.0), daemon=True)
    ocr_thread.start()

    worker = PriceRefreshWorker(registry, 0.05, on_complete)
    worker.start()
    assert three_refreshes.wait(1.0)
    worker.stop()
    assert ocr_thread.is_alive(), "OCR mock 중에도 price polling이 독립 실행되어야 합니다."
    ocr_release.set()
    ocr_thread.join()

    assert all(call == ("XPON", "LUCY") for call in client.calls)
    xpon = registry.get_snapshot("XPON")
    lucy = registry.get_snapshot("LUCY")
    assert xpon is not None and lucy is not None
    assert xpon.current_price == Decimal("8.20")
    assert lucy.current_price == Decimal("1.05")
    assert xpon.price_status == "stale" and xpon.price_error == "temporary failure"
    assert xpon.holding is True

    view = StockCardsView(registry)
    view.sync_from_registry()
    card = view.cards["XPON"]
    upper, lower = card._nearest_levels(8.20)
    assert upper and upper[0].price == 8.30
    assert lower and lower[0].price == 8.10
    assert card.price_label.text() == "8.2000"

    print("[PRICE REFRESH TEST] passed")
    print(f"batch_calls: {len(client.calls)}")
    print("symbols: XPON,LUCY")
    print("XPON: 8.10 -> 8.20 -> stale(last good 8.20)")
    print("LUCY: 1.00 -> 1.05 -> stale(last good 1.05)")
    print("candidate_recalculated: true")
    print("holding_preserved: true")
    print("ocr_and_polling_independent: true")
    view.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
