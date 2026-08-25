from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from live_ui import CaptureStatusCardsView, LiveStockWindow, LogDashboard  # noqa: E402
from stock_models import StockRecord, StockRegistry  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    registry = StockRegistry()
    dashboard = LogDashboard(registry)
    status_cards: CaptureStatusCardsView = dashboard.status_cards

    status_cards.sync_from_registry()
    assert not status_cards.cards

    for index in range(7):
        ticker = f"T{index}"
        registry.register(
            StockRecord(
                ticker,
                f"테스트 {index}",
                current_price=Decimal("10.25"),
                daily_loaded=index % 2 == 0,
                minute_loaded=index % 2 == 1,
                price_status="valid",
                last_price_update="2026-08-25T04:30:00+09:00",
            )
        )
    status_cards.sync_from_registry()

    assert tuple(status_cards.cards) == ("T0", "T1", "T2", "T3", "T4", "T5")
    assert len(status_cards.cards) == 6
    assert status_cards._layout.alignment() & Qt.AlignmentFlag.AlignHCenter
    first = status_cards.cards["T0"]
    assert first.ticker_label.text() == "T0"
    assert first.daily_label.text() == "일봉 완료"
    assert first.minute_label.text() == "분봉 아직.."
    assert "실시간 감지중" in first.price_state_label.text()
    assert "10.2500" in first.price_state_label.text()

    assert registry.remove("T0") is not None
    status_cards.sync_from_registry()
    assert tuple(status_cards.cards) == ("T1", "T2", "T3", "T4", "T5", "T6")
    assert "T0" not in status_cards.cards

    dashboard.append_console_line("[OCR] complete status: success")
    dashboard.append_console_line("ticker: unresolved")
    dashboard.append_console_line("[CAPTURE FAILED] parser error")
    process_text = dashboard.process_log.toPlainText()
    activity_text = dashboard.activity_log.toPlainText()
    assert "[OCR] complete" in process_text
    assert "ticker: unresolved" in process_text
    assert "[CAPTURE FAILED]" in process_text
    assert "ticker: unresolved" in activity_text
    assert "[CAPTURE FAILED]" in activity_text

    window = LiveStockWindow()
    assert window.page_stack.count() == 2
    assert window.page_stack.currentIndex() == 0
    window._show_page(1)
    assert window.page_stack.currentIndex() == 1
    assert window.log_button.isChecked()
    assert not window.realtime_button.isChecked()
    assert all(
        button.text() != "티커명 직접 입력"
        for button in window.findChildren(QPushButton)
    )

    print("[LIVE UI DASHBOARD TEST] passed")
    print("initial_cards: 0")
    print("dynamic_cards: 6 (max)")
    print("daily_minute_status: verified")
    print("realtime_price_status: verified")
    print("process_activity_logs: verified")
    print("left_menu_pages: verified")
    print("manual_ticker_input_ui: removed")
    print("tracking_remove_status_card: verified")
    window.deleteLater()
    dashboard.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
