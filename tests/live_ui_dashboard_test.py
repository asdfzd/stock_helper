from __future__ import annotations

import os
import sys
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from capture_history import CaptureHistoryItem  # noqa: E402
from live_ui import (  # noqa: E402
    CaptureGallery,
    CaptureStatusCardsView,
    ConsoleLogBridge,
    LiveStockWindow,
    LogDashboard,
    TeeTextStream,
)
from stock_models import StockRecord, StockRegistry  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    registry = StockRegistry()
    dashboard = LogDashboard()
    status_cards = CaptureStatusCardsView(registry)

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

    dashboard.append_console_lines(
        [
            "[OCR] complete status: success",
            "ticker: unresolved",
            "[CAPTURE FAILED] parser error",
        ]
    )
    process_text = dashboard.process_log.toPlainText()
    activity_text = dashboard.activity_log.toPlainText()
    assert "[OCR] complete" in process_text
    assert "ticker: unresolved" in process_text
    assert "[CAPTURE FAILED]" in process_text
    assert "ticker: unresolved" in activity_text
    assert "[CAPTURE FAILED]" in activity_text

    bridge = ConsoleLogBridge()
    assert 100 <= bridge.BATCH_INTERVAL_MS <= 150
    received_batches: list[list[str]] = []
    bridge.lines_received.connect(received_batches.append)
    stream = TeeTextStream(StringIO(), bridge)
    stream.write("first\nsecond\n")
    assert received_batches == []
    bridge.flush_pending()
    assert received_batches == [["first", "second"]]
    stream.write("final without newline")
    stream.flush_pending()
    bridge.stop()
    assert received_batches == [
        ["first", "second"],
        ["final without newline"],
    ]

    window = LiveStockWindow()
    assert window.page_stack.count() == 3
    assert window.page_stack.currentIndex() == 0
    window._show_page(1)
    assert window.page_stack.currentIndex() == 1
    assert window.log_button.isChecked()
    assert not window.realtime_button.isChecked()
    window._show_page(2)
    assert window.page_stack.currentIndex() == 2
    assert window.log_button.isChecked()
    assert [action.text() for action in window.log_menu.actions()] == [
        "진행사항 오류",
        "캡처 사진",
    ]
    assert all(
        button.text() != "티커명 직접 입력"
        for button in window.findChildren(QPushButton)
    )

    deleted: list[str] = []
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
        raw_path = Path(temporary_directory) / "capture_raw.png"
        image = QImage(120, 180, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        assert image.save(str(raw_path))
        gallery = CaptureGallery(deleted.append)
        gallery.add_or_update(
            CaptureHistoryItem("capture_1", raw_path, None, parsed_ticker="TS")
        )
        card = gallery.cards["capture_1"]
        assert card.ticker_label.text() == "TS"
        assert card.ticker_label.property("tickerWarning") is True
        assert not card.delete_button.isVisible()
        app.sendEvent(card, QEvent(QEvent.Type.Enter))
        assert not card.delete_button.isHidden()
        card.delete_button.click()
        assert deleted == ["capture_1"]
        gallery.add_or_update(
            CaptureHistoryItem("capture_2", raw_path, None, parsed_ticker=None)
        )
        assert gallery.cards["capture_2"].ticker_label.text() == "오류"
        gallery.deleteLater()

    print("[LIVE UI DASHBOARD TEST] passed")
    print("initial_cards: 0")
    print("dynamic_cards: 6 (max)")
    print("daily_minute_status: verified")
    print("realtime_price_status: verified")
    print("process_activity_logs: verified")
    print("batched_console_logs_and_shutdown_flush: verified")
    print("left_menu_pages: verified")
    print("capture_gallery: verified")
    print("manual_ticker_input_ui: removed")
    print("tracking_remove_status_card: verified")
    window.deleteLater()
    dashboard.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
