from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from halt_monitor import HaltInfo, halt_display_text, parse_halts  # noqa: E402
from main import StockCard, StockData  # noqa: E402


def main() -> int:
    payload = {
        "data": [
            {
                "symbol": "ABCD",
                "status": "halted",
                "reasons": [{"code": "LUDP", "title": "Volatility Pause"}],
                "halted_at": "2026-08-25T10:00:00Z",
                "quote_resumed_at": None,
                "resumed_at": None,
            }
        ]
    }
    halt = parse_halts(payload)["ABCD"]
    assert halt_display_text(
        halt, datetime(2026, 8, 25, 10, 0, 1, tzinfo=timezone.utc)
    ) == "5분 남음"
    assert halt_display_text(
        halt, datetime(2026, 8, 25, 10, 4, 31, tzinfo=timezone.utc)
    ) == "29초후 풀림"
    assert halt_display_text(
        halt, datetime(2026, 8, 25, 10, 5, 0, tzinfo=timezone.utc)
    ) == "재개중..."
    quote_resumed = HaltInfo(
        "ABCD", "quote_resumed", ("LUDP",), halt.halted_at, None, None
    )
    assert halt_display_text(quote_resumed) == "재개중..."

    app = QApplication.instance() or QApplication([])
    card = StockCard(StockData("ABCD", 1.25, []))
    assert not any(button.text() in {"ON", "OFF"} for button in card.findChildren(QPushButton))
    card.set_halt_info(quote_resumed)
    assert card.halt_label.text() == "재개중..."
    assert card.halt_label.isVisibleTo(card)
    card.deleteLater()
    app.processEvents()
    print("[HALT MONITOR TEST] passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
