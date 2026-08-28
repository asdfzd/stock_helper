from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
import traceback
from datetime import datetime
from enum import Enum
from typing import TextIO

from PySide6.QtCore import QObject, QPoint, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from capture_history import CaptureHistoryItem
from halt_monitor import HaltRefreshEvent, HaltRefreshWorker
from live_capture import (
    CaptureProcessor,
    GlobalCaptureHotkey,
    TickerCalibrationResult,
    enable_dpi_awareness,
)
from live_ui_config import (
    ENABLE_REALTIME_PRICE_POLLING,
    HALT_REFRESH_INTERVAL_SECONDS,
    PRICE_REFRESH_INTERVAL_SECONDS,
)
from main import STYLESHEET, StockCardsView
from paddle_ocr_validation import create_reader
from pending_captures import PendingCapture
from price_refresh import PriceRefreshEvent, PriceRefreshWorker
from runtime_paths import APP_ROOT
from stock_models import StockRecord, StockRegistry
from tooltip_capture_config import TICKER_FIXED_ROI, USE_FIXED_TICKER_ROI


class CaptureMode(str, Enum):
    TICKER_CALIBRATION = "ticker_calibration"
    CAPTURE_READY = "capture_ready"


def initial_ticker_state(
    use_fixed_roi: bool,
    fixed_roi: tuple[int, int, int, int],
) -> tuple[CaptureMode, tuple[int, int, int, int] | None, str]:
    if not use_fixed_roi:
        return (
            CaptureMode.TICKER_CALIBRATION,
            None,
            "티커명 위치에서 ` 키를 눌러주세요",
        )
    x1, y1, x2, y2 = fixed_roi
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"TICKER_FIXED_ROI가 유효하지 않습니다: {fixed_roi}")
    return (
        CaptureMode.CAPTURE_READY,
        fixed_roi,
        "고정 티커 ROI 사용 중 · ` 키로 종목 캡처",
    )


class ConsoleLogBridge(QObject):
    """worker 로그를 잠시 모아 GUI thread에 batch로 전달한다."""

    BATCH_INTERVAL_MS = 125
    lines_received = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending_lines: list[str] = []
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(self.BATCH_INTERVAL_MS)
        self._timer.timeout.connect(self.flush_pending)
        self._timer.start()

    def enqueue_lines(self, lines: list[str]) -> None:
        if not lines:
            return
        with self._lock:
            self._pending_lines.extend(lines)

    @Slot()
    def flush_pending(self) -> None:
        with self._lock:
            if not self._pending_lines:
                return
            lines = self._pending_lines
            self._pending_lines = []
        self.lines_received.emit(lines)

    @Slot()
    def stop(self) -> None:
        self._timer.stop()
        self.flush_pending()


class TeeTextStream:
    """Git Bash 출력은 유지하면서 완성된 로그 줄을 UI에도 복제한다."""

    def __init__(self, original: TextIO, bridge: ConsoleLogBridge) -> None:
        self._original = original
        self._bridge = bridge
        self._buffers: dict[int, str] = {}
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        written = self._original.write(text)
        if not text:
            return written
        thread_id = threading.get_ident()
        with self._lock:
            buffered = self._buffers.get(thread_id, "") + text.replace("\r\n", "\n")
            lines = buffered.split("\n")
            self._buffers[thread_id] = lines.pop()
        self._bridge.enqueue_lines([line for line in lines if line.strip()])
        return written

    def flush(self) -> None:
        self._original.flush()

    def flush_pending(self) -> None:
        """종료 시 개행 없이 남은 thread별 출력까지 로그 batch에 넣는다."""
        with self._lock:
            lines = [line for line in self._buffers.values() if line.strip()]
            self._buffers.clear()
        self._bridge.enqueue_lines(lines)
        self._original.flush()

    def __getattr__(self, name: str):
        return getattr(self._original, name)


def redirect_unavailable_logging_streams(stream: TextIO) -> list[logging.StreamHandler]:
    """Retarget handlers created while a windowed executable had no stderr."""
    loggers = [logging.getLogger()]
    loggers.extend(
        logger
        for logger in logging.Logger.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    )
    redirected: list[logging.StreamHandler] = []
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.stream is None:
                handler.setStream(stream)
                redirected.append(handler)
    return redirected


def restore_logging_streams(
    handlers: list[logging.StreamHandler], stream: TextIO | None
) -> None:
    for handler in handlers:
        handler.setStream(stream)


class CaptureStateCard(QFrame):
    """ticker 하나의 일봉·분봉·현재가 polling 상태를 표시한다."""

    def __init__(self, stock: StockRecord, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("captureStateCard")
        self.setMinimumWidth(145)
        self.setMaximumWidth(190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        self.ticker_label = QLabel()
        self.ticker_label.setObjectName("captureTicker")
        self.ticker_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.daily_label = QLabel()
        self.daily_label.setObjectName("captureDailyState")
        self.daily_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        divider = QFrame()
        divider.setObjectName("captureDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        self.minute_label = QLabel()
        self.minute_label.setObjectName("captureMinuteState")
        self.minute_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.price_state_label = QLabel()
        self.price_state_label.setObjectName("capturePriceState")
        self.price_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.price_state_label.setWordWrap(True)

        layout.addWidget(self.ticker_label)
        layout.addWidget(self.daily_label)
        layout.addWidget(divider)
        layout.addWidget(self.minute_label)
        layout.addStretch()
        layout.addWidget(self.price_state_label)
        self.update_stock(stock)

    def update_stock(self, stock: StockRecord) -> None:
        self.ticker_label.setText(stock.stock_code)
        self.daily_label.setText("일봉 완료" if stock.daily_loaded else "일봉 아직..")
        self.minute_label.setText("분봉 완료" if stock.minute_loaded else "분봉 아직..")

        if not ENABLE_REALTIME_PRICE_POLLING:
            state = "실시간 감지 꺼짐"
        elif stock.price_status == "valid" and stock.current_price is not None:
            state = f"실시간 감지중\n{stock.current_price:.4f}"
        elif stock.price_status == "stale" and stock.current_price is not None:
            state = f"실시간 지연\n마지막 {stock.current_price:.4f}"
        else:
            state = "현재가 대기"
        if stock.last_price_update:
            try:
                updated_at = datetime.fromisoformat(stock.last_price_update)
                updated_text = updated_at.astimezone().strftime("%H:%M:%S")
            except ValueError:
                updated_text = stock.last_price_update
            state += f"\n최근 {updated_text}"
        self.price_state_label.setText(state)
        self.price_state_label.setProperty("priceStatus", stock.price_status)
        self.price_state_label.style().unpolish(self.price_state_label)
        self.price_state_label.style().polish(self.price_state_label)


class CaptureStatusCardsView(QScrollArea):
    """처음 식별된 ticker 순서대로 최대 6개의 상태 카드만 만든다."""

    MAX_CARDS = 6

    def __init__(self, registry: StockRegistry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.cards: dict[str, CaptureStateCard] = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(185)

        container = QWidget()
        container.setObjectName("captureStatusContainer")
        self._layout = QHBoxLayout(container)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(10)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(container)

    def sync_from_registry(self) -> None:
        stocks = self.registry.all_snapshots()[: self.MAX_CARDS]
        desired_symbols = {stock.stock_code for stock in stocks}
        for symbol in tuple(self.cards):
            if symbol not in desired_symbols:
                card = self.cards.pop(symbol)
                card.setParent(None)
                card.deleteLater()
        for stock in stocks:
            card = self.cards.get(stock.stock_code)
            if card is None:
                card = CaptureStateCard(stock)
                self.cards[stock.stock_code] = card
                self._layout.addWidget(card)
            else:
                card.update_stock(stock)


class LogDashboard(QWidget):
    """전체 처리 로그와 진행/오류 요약."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        process_title = QLabel("캡처 · OCR · 파싱 로그")
        process_title.setObjectName("logSectionTitle")
        self.process_log = self._make_log_view("processLog")
        activity_title = QLabel("진행사항 · 오류")
        activity_title.setObjectName("logSectionTitle")
        self.activity_log = self._make_log_view("activityLog")
        self.activity_log.setMaximumHeight(150)

        layout.addWidget(process_title)
        layout.addWidget(self.process_log, 1)
        layout.addWidget(activity_title)
        layout.addWidget(self.activity_log)

    @staticmethod
    def _make_log_view(object_name: str) -> QTextEdit:
        view = QTextEdit()
        view.setObjectName(object_name)
        view.setReadOnly(True)
        view.setAcceptRichText(False)
        view.document().setMaximumBlockCount(1500)
        return view

    @staticmethod
    def _line_style(line: str) -> tuple[QColor, bool, bool]:
        lowered = line.lower()
        ticker_warning = (
            ("ticker" in lowered and any(
                marker in lowered
                for marker in ("null", "unresolved", "not_found", "failed", "실패", "직접 입력")
            ))
            or "[pending capture]" in lowered
        )
        is_error = any(
            marker in lowered
            for marker in (
                "[capture failed]",
                "[hotkey error]",
                "[live ui error]",
                "traceback",
                "tooltip_content_not_found",
                "status: invalid",
                "exception:",
                "error:",
            )
        )
        if is_error:
            return QColor("#ff6b6b"), True, True
        if ticker_warning:
            return QColor("#ffd43b"), True, True
        return QColor("#74c0fc"), False, False

    @staticmethod
    def _is_activity_line(line: str, important: bool) -> bool:
        stripped = line.strip().lower()
        return important or stripped.startswith(
            (
                "[capture]",
                "[queue]",
                "[ocr step]",
                "[analyze step]",
                "[registry",
                "[price",
                "[pending",
                "[hotkey",
                "[live",
                "[shutdown",
                "reason:",
                "error:",
            )
        )

    @staticmethod
    def _append_colored_lines(
        view: QTextEdit,
        entries: list[tuple[str, QColor, bool, str]],
    ) -> None:
        if not entries:
            return
        cursor = view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        for line, color, bold, timestamp in entries:
            if not view.document().isEmpty():
                cursor.insertBlock()
            text_format = QTextCharFormat()
            text_format.setForeground(color)
            text_format.setFontWeight(700 if bold else 400)
            cursor.setCharFormat(text_format)
            cursor.insertText(f"[{timestamp}] {line}")
        cursor.endEditBlock()
        view.setTextCursor(cursor)
        view.ensureCursorVisible()

    @Slot(str)
    def append_console_line(self, line: str) -> None:
        self.append_console_lines([line])

    @Slot(object)
    def append_console_lines(self, lines: list[str]) -> None:
        process_entries: list[tuple[str, QColor, bool, str]] = []
        activity_entries: list[tuple[str, QColor, bool, str]] = []
        for line in lines:
            color, bold, important = self._line_style(line)
            entry = (line, color, bold, datetime.now().strftime("%H:%M:%S"))
            process_entries.append(entry)
            if self._is_activity_line(line, important):
                activity_entries.append(entry)
        self._append_colored_lines(self.process_log, process_entries)
        self._append_colored_lines(self.activity_log, activity_entries)


class UiResponsivenessMonitor(QObject):
    """낮은 빈도의 timer로 큰 GUI event-loop 지연만 기록한다."""

    INTERVAL_MS = 250
    DELAY_THRESHOLD_MS = 200
    REPORT_COOLDOWN_SECONDS = 2.0

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._check_delay)
        self._last_tick = 0.0
        self._last_report = 0.0
        self._max_delay_ms = 0.0

    def start(self) -> None:
        self._last_tick = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    @Slot()
    def _check_delay(self) -> None:
        now = time.perf_counter()
        delay_ms = max(0.0, (now - self._last_tick) * 1000.0 - self.INTERVAL_MS)
        self._last_tick = now
        self._max_delay_ms = max(self._max_delay_ms, delay_ms)
        if (
            self._max_delay_ms >= self.DELAY_THRESHOLD_MS
            and now - self._last_report >= self.REPORT_COOLDOWN_SECONDS
        ):
            print(f"[UI PERF] max_delay={self._max_delay_ms:.0f}ms", flush=True)
            self._last_report = now
            self._max_delay_ms = 0.0


class HoverMenuButton(QPushButton):
    hovered = Signal()

    def enterEvent(self, event) -> None:
        self.hovered.emit()
        super().enterEvent(event)


class CaptureImageCard(QFrame):
    def __init__(
        self,
        item: CaptureHistoryItem,
        on_delete_requested,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self._pixmap = QPixmap(str(item.raw_image_path))
        self.setObjectName("captureImageCard")
        self.setMinimumWidth(290)
        self.setMaximumWidth(390)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("captureImageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 8, 8, 8)
        self.ticker_label = QLabel(item.ticker_display)
        self.ticker_label.setObjectName("captureImageTicker")
        self.ticker_label.setProperty("tickerWarning", item.ticker_warning)
        self.delete_button = QPushButton("×")
        self.delete_button.setObjectName("deleteCaptureButton")
        self.delete_button.setFixedSize(28, 28)
        self.delete_button.setToolTip("이 캡처와 반영된 종목 정보를 삭제")
        self.delete_button.setVisible(False)
        self.delete_button.clicked.connect(
            lambda: on_delete_requested(self.item.capture_id)
        )
        header_layout.addWidget(self.ticker_label)
        header_layout.addStretch()
        header_layout.addWidget(self.delete_button)

        self.image_label = QLabel()
        self.image_label.setObjectName("captureRawImage")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setMinimumHeight(360)
        if self._pixmap.isNull():
            self.image_label.setText("사진을 불러올 수 없습니다")
        layout.addWidget(header)
        layout.addWidget(self.image_label, 1)
        self._update_pixmap()

    def _update_pixmap(self) -> None:
        if self._pixmap.isNull():
            return
        target = self.image_label.size()
        if target.width() <= 1 or target.height() <= 1:
            target = QSize(340, 420)
        self.image_label.setPixmap(
            self._pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_pixmap()

    def enterEvent(self, event) -> None:
        self.delete_button.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.delete_button.setVisible(False)
        super().leaveEvent(event)


class CaptureGallery(QScrollArea):
    COLUMNS = 3

    def __init__(self, on_delete_requested, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_delete_requested = on_delete_requested
        self.items: dict[str, CaptureHistoryItem] = {}
        self.cards: dict[str, CaptureImageCard] = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        container.setObjectName("captureGalleryContainer")
        self.grid = QGridLayout(container)
        self.grid.setContentsMargins(20, 20, 20, 20)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(16)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setWidget(container)

    def add_or_update(self, item: CaptureHistoryItem) -> None:
        self.items[item.capture_id] = item
        self._rebuild()

    def remove_capture(self, capture_id: str) -> None:
        self.items.pop(capture_id, None)
        self._rebuild()

    def _rebuild(self) -> None:
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        self.cards.clear()
        for index, item in enumerate(reversed(tuple(self.items.values()))):
            card = CaptureImageCard(item, self._on_delete_requested)
            self.cards[item.capture_id] = card
            self.grid.addWidget(card, index // self.COLUMNS, index % self.COLUMNS)


class RegistryUiBridge(QObject):
    """일반 Python worker callback을 Qt queued signal로 바꾸는 경계."""

    registry_updated = Signal(str)
    pending_updated = Signal(str)
    prices_refreshed = Signal(object)
    halts_refreshed = Signal(object)
    capture_status_changed = Signal(str)
    calibration_finished = Signal(object)
    history_updated = Signal(object)

    def notify_complete(self, stock: StockRecord) -> None:
        # CaptureProcessor의 OCR worker thread에서 호출된다. Widget에는 접근하지 않는다.
        self.registry_updated.emit(stock.stock_code)

    def notify_pending(self, pending: PendingCapture) -> None:
        self.pending_updated.emit(pending.capture_id)

    def notify_prices(self, event: PriceRefreshEvent) -> None:
        self.prices_refreshed.emit(event)

    def notify_halts(self, event: HaltRefreshEvent) -> None:
        self.halts_refreshed.emit(event)

    def notify_calibration(self, result: TickerCalibrationResult) -> None:
        self.calibration_finished.emit(result)

    def notify_history(self, item: CaptureHistoryItem) -> None:
        self.history_updated.emit(item)


class LiveStockWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stock Helper - Live Capture")
        self.resize(1280, 760)

        self.registry = StockRegistry()
        self.bridge = RegistryUiBridge()
        self.cards_view = StockCardsView(
            self.registry, on_stock_removed=self._on_stock_removed
        )
        initial_mode, initial_roi, initial_status = initial_ticker_state(
            USE_FIXED_TICKER_ROI,
            TICKER_FIXED_ROI,
        )
        self.status_label = QLabel(initial_status)
        self.status_label.setObjectName("liveStatus")

        realtime_page = QWidget()
        realtime_layout = QVBoxLayout(realtime_page)
        realtime_layout.setContentsMargins(24, 16, 24, 16)
        realtime_layout.addWidget(self.status_label)
        realtime_layout.addWidget(self.cards_view, 1)

        self.log_dashboard = LogDashboard()
        self.capture_gallery = CaptureGallery(self._delete_capture)
        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(realtime_page)
        self.page_stack.addWidget(self.log_dashboard)
        self.page_stack.addWidget(self.capture_gallery)

        menu = QFrame()
        menu.setObjectName("sideMenu")
        menu.setFixedWidth(132)
        menu_layout = QVBoxLayout(menu)
        menu_layout.setContentsMargins(12, 20, 12, 20)
        menu_layout.setSpacing(8)
        menu_title = QLabel("STOCK\nHELPER")
        menu_title.setObjectName("sideMenuTitle")
        self.realtime_button = QPushButton("실시간")
        self.log_button = HoverMenuButton("로그 확인")
        for button in (self.realtime_button, self.log_button):
            button.setObjectName("sideMenuButton")
            button.setCheckable(True)
            menu_layout.addWidget(button)
        self.realtime_button.clicked.connect(lambda: self._show_page(0))
        self.log_button.clicked.connect(lambda: self._show_page(1))
        self.log_menu = QMenu(self)
        self.log_menu.setObjectName("logSubmenu")
        progress_action = self.log_menu.addAction("진행사항 오류")
        captures_action = self.log_menu.addAction("캡처 사진")
        progress_action.triggered.connect(lambda: self._show_page(1))
        captures_action.triggered.connect(lambda: self._show_page(2))
        self.log_button.hovered.connect(self._show_log_menu)
        self.realtime_button.setChecked(True)
        menu_layout.insertWidget(0, menu_title)
        menu_layout.addStretch()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(menu)
        layout.addWidget(self.page_stack, 1)
        self.setCentralWidget(central)

        self.processor = CaptureProcessor(
            create_reader,
            self.registry,
            on_complete=self.bridge.notify_complete,
            on_pending=self.bridge.notify_pending,
            on_calibration=self.bridge.notify_calibration,
            on_history=self.bridge.notify_history,
        )
        self.price_worker = PriceRefreshWorker(
            self.registry,
            PRICE_REFRESH_INTERVAL_SECONDS,
            on_complete=self.bridge.notify_prices,
        )
        self.halt_worker = HaltRefreshWorker(
            HALT_REFRESH_INTERVAL_SECONDS,
            on_complete=self.bridge.notify_halts,
        )
        self.halt_countdown_timer = QTimer(self)
        self.halt_countdown_timer.setInterval(1000)
        self.halt_countdown_timer.timeout.connect(
            self.cards_view.refresh_halt_countdowns
        )
        self.ui_perf_monitor = UiResponsivenessMonitor(self)
        self.ui_perf_monitor.start()
        self.hotkey = GlobalCaptureHotkey(self._capture_for_hotkey)
        self._services_started = False
        self._shutting_down = False
        self._capture_mode = initial_mode
        self._ticker_roi = initial_roi
        self._calibration_in_progress = False

        self.bridge.registry_updated.connect(self._on_registry_updated)
        self.bridge.pending_updated.connect(self._on_pending_updated)
        self.bridge.prices_refreshed.connect(self._on_prices_refreshed)
        self.bridge.halts_refreshed.connect(self._on_halts_refreshed)
        self.bridge.capture_status_changed.connect(self.status_label.setText)
        self.bridge.calibration_finished.connect(self._on_calibration_finished)
        self.bridge.history_updated.connect(self._on_history_updated)

    @Slot(int)
    def _show_page(self, index: int) -> None:
        self.page_stack.setCurrentIndex(index)
        self.realtime_button.setChecked(index == 0)
        self.log_button.setChecked(index in {1, 2})

    @Slot()
    def _show_log_menu(self) -> None:
        position = self.log_button.mapToGlobal(QPoint(self.log_button.width() - 4, 0))
        self.log_menu.popup(position)

    @Slot()
    def start_capture_services(self) -> None:
        if self._services_started:
            return
        try:
            self.processor.start()
            if ENABLE_REALTIME_PRICE_POLLING:
                self.price_worker.start()
            self.halt_worker.start()
            self.halt_countdown_timer.start()
            self.hotkey.start()
        except Exception as exc:
            message = f"시작 실패: {type(exc).__name__}: {exc}"
            self.status_label.setText(message)
            print(f"[LIVE UI ERROR] {message}", flush=True)
            self.processor.stop(drain=False)
            if ENABLE_REALTIME_PRICE_POLLING:
                self.price_worker.stop()
            self.halt_worker.stop()
            self.halt_countdown_timer.stop()
            return
        self._services_started = True
        if USE_FIXED_TICKER_ROI:
            self.status_label.setText("고정 티커 ROI 사용 중 · ` 키로 종목 캡처")
            print(f"[TICKER ROI] fixed={TICKER_FIXED_ROI}", flush=True)
            print("`: capture stock", flush=True)
        else:
            self.status_label.setText("티커명 위치에서 ` 키를 눌러주세요")
            print("`: set ticker position", flush=True)
        if not ENABLE_REALTIME_PRICE_POLLING:
            print("[PRICE REFRESH] disabled for OCR testing", flush=True)
        else:
            print(
                "[PRICE MONITOR] realtime polling started "
                f"interval={PRICE_REFRESH_INTERVAL_SECONDS:.1f}s",
                flush=True,
            )
        print("[LIVE UI] ready", flush=True)

    @Slot(object)
    def _on_halts_refreshed(self, event: HaltRefreshEvent) -> None:
        if event.error:
            print(f"[HALT REFRESH] error={event.error}", flush=True)
            return
        if event.halts is None:
            return
        self.cards_view.set_halts(event.halts)
        active = sum(halt.status != "resumed" for halt in event.halts.values())
        print(f"[HALT REFRESH] active={active}", flush=True)

    def _capture_for_hotkey(self, key: str) -> None:
        try:
            if self._capture_mode == CaptureMode.TICKER_CALIBRATION:
                if self._calibration_in_progress:
                    self.bridge.capture_status_changed.emit("티커 위치 판독 중입니다")
                    return
                self._calibration_in_progress = True
                self.bridge.capture_status_changed.emit("티커 위치 판독 중")
                self.processor.enqueue_ticker_calibration()
                return
            if self._ticker_roi is None:
                raise RuntimeError("ticker_roi_not_calibrated")
            self.bridge.capture_status_changed.emit(f"OCR 처리 중 · 입력 키: {key}")
            self.processor.enqueue_live_capture(self._ticker_roi)
        except Exception as exc:
            self._calibration_in_progress = False
            self.bridge.capture_status_changed.emit(
                f"캡처 실패 · {type(exc).__name__}: {exc}"
            )
            raise

    @Slot(object)
    def _on_calibration_finished(self, result: TickerCalibrationResult) -> None:
        self._calibration_in_progress = False
        if not result.success or result.ticker_roi is None:
            self._capture_mode = CaptureMode.TICKER_CALIBRATION
            self._ticker_roi = None
            self.status_label.setText(
                "티커명을 찾지 못했습니다. 티커명 위치에서 ` 키를 다시 눌러주세요"
            )
            return
        self._ticker_roi = result.ticker_roi
        self._capture_mode = CaptureMode.CAPTURE_READY
        self.status_label.setText(f"티커 위치 설정 완료 · 현재 인식: {result.ticker}")
        print("`: capture stock", flush=True)

    @Slot(str)
    def _on_registry_updated(self, symbol: str) -> None:
        # Signal 수신 객체는 GUI thread에 있으므로 여기서만 Widget을 갱신한다.
        self.cards_view.refresh_from_registry(symbol)
        stock = self.registry.get_snapshot(symbol)
        if stock is None:
            return
        if stock.daily_loaded and stock.minute_loaded:
            state = "daily + minute 완료"
        elif stock.daily_loaded:
            state = "daily 완료"
        else:
            state = "minute 완료"
        self.status_label.setText(f"{symbol} {state} · 대기")

    def _on_stock_removed(self, symbol: str) -> None:
        self.status_label.setText(f"{symbol} 추적 종료 · 대기")

    @Slot(object)
    def _on_history_updated(self, item: CaptureHistoryItem) -> None:
        self.capture_gallery.add_or_update(item)

    def _delete_capture(self, capture_id: str) -> None:
        affected_symbols = self.registry.remove_capture(capture_id)
        self.processor.pending_store.remove(capture_id)
        deleted_files = self.processor.delete_capture_files(capture_id)
        self.capture_gallery.remove_capture(capture_id)
        self.cards_view.sync_from_registry()
        symbols_text = ", ".join(affected_symbols) or "미확정"
        self.status_label.setText(f"캡처 삭제 완료 · {symbols_text}")
        print(
            f"[CAPTURE DELETED] capture_id={capture_id} "
            f"symbols={symbols_text} files={len(deleted_files)}",
            flush=True,
        )

    @Slot(str)
    def _on_pending_updated(self, capture_id: str) -> None:
        self.status_label.setText("ticker 인식 실패 · 캡처 결과 제외")
        print(
            f"[TICKER UNRESOLVED] capture_id={capture_id} manual_input=disabled",
            flush=True,
        )

    @Slot(object)
    def _on_prices_refreshed(self, event: PriceRefreshEvent) -> None:
        # Widget 갱신과 현재가 기준 위/아래 재계산은 GUI thread에서 수행한다.
        self.cards_view.sync_from_registry()
        result = event.result
        for symbol in result.updated_symbols:
            stock = self.registry.get_snapshot(symbol)
            if stock is None:
                continue
            print(
                "[PRICE LIVE] "
                f"symbol={symbol} last_price={stock.current_price} "
                f"timestamp={stock.last_price_update} status={stock.price_status}",
                flush=True,
            )
        for symbol in result.unavailable_symbols:
            stock = self.registry.get_snapshot(symbol)
            print(
                "[PRICE LIVE] "
                f"symbol={symbol} status={stock.price_status if stock else 'unavailable'} "
                f"error={stock.price_error if stock else 'missing_registry_record'}",
                flush=True,
            )
        if result.error and not result.updated_symbols:
            self.status_label.setText("현재가 갱신 지연 · 마지막 정상 가격 유지")

    @Slot()
    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.ui_perf_monitor.stop()
        print("[LIVE UI] stopping ...", flush=True)
        try:
            self.hotkey.stop()
        finally:
            self.halt_countdown_timer.stop()
            self.halt_worker.stop()
            if ENABLE_REALTIME_PRICE_POLLING:
                self.price_worker.stop()
            self.processor.stop(drain=False)
        print("[LIVE UI] stopped", flush=True)


LIVE_STYLESHEET = STYLESHEET + """
QLabel#liveStatus {
    color: #aeb9c5;
    font-family: "Malgun Gothic";
    font-size: 13px;
    font-weight: 600;
    padding: 4px 8px;
}
QScrollArea {
    background: #111820;
    border: none;
}
QWidget#stockCardsContainer, QWidget#captureStatusContainer {
    background: #111820;
}
QFrame#sideMenu {
    background: #18222d;
    border-right: 1px solid #344353;
}
QLabel#sideMenuTitle {
    color: #69db7c;
    font-family: "Segoe UI";
    font-size: 17px;
    font-weight: 800;
    padding: 0 6px 18px 6px;
}
QPushButton#sideMenuButton {
    min-width: 96px;
    min-height: 42px;
    color: #aeb9c5;
    background: transparent;
    border-radius: 8px;
    text-align: left;
    padding-left: 14px;
}
QPushButton#sideMenuButton:checked {
    color: #f2f5f8;
    background: #2b3a49;
    border-left: 3px solid #69db7c;
}
QMenu#logSubmenu {
    color: #f2f5f8;
    background: #18222d;
    border: 1px solid #465464;
    padding: 6px;
}
QMenu#logSubmenu::item {
    min-width: 132px;
    padding: 10px 16px;
    border-radius: 6px;
}
QMenu#logSubmenu::item:selected {
    color: #102117;
    background: #69db7c;
}
QLabel#logSectionTitle {
    color: #f2f5f8;
    font-family: "Malgun Gothic";
    font-size: 14px;
    font-weight: 700;
    padding: 2px 0;
}
QFrame#captureStateCard {
    background: #222f3d;
    border: 1px solid #344353;
    border-radius: 10px;
}
QLabel#captureTicker {
    color: #f2f5f8;
    font-family: "Segoe UI";
    font-size: 18px;
    font-weight: 800;
}
QLabel#captureDailyState, QLabel#captureMinuteState {
    color: #aeb9c5;
    font-family: "Malgun Gothic";
    font-size: 12px;
    font-weight: 600;
}
QFrame#captureDivider {
    color: #465464;
    background: #465464;
    max-height: 1px;
    border: none;
}
QLabel#capturePriceState {
    color: #69db7c;
    font-family: "Malgun Gothic";
    font-size: 11px;
    font-weight: 700;
}
QLabel#capturePriceState[priceStatus="stale"] {
    color: #ffd43b;
}
QLabel#capturePriceState[priceStatus="unavailable"] {
    color: #8fa0b2;
}
QTextEdit#processLog, QTextEdit#activityLog {
    color: #74c0fc;
    background: #0d141b;
    border: 1px solid #344353;
    border-radius: 8px;
    font-family: "Consolas";
    font-size: 11px;
    padding: 6px;
}
QWidget#captureGalleryContainer {
    background: #111820;
}
QFrame#captureImageCard {
    background: #1b2530;
    border: 1px solid #344353;
    border-radius: 12px;
}
QWidget#captureImageHeader {
    background: #222f3d;
    border-top-left-radius: 12px;
    border-top-right-radius: 12px;
}
QLabel#captureImageTicker {
    color: #f2f5f8;
    font-family: "Segoe UI";
    font-size: 18px;
    font-weight: 800;
}
QLabel#captureImageTicker[tickerWarning="true"] {
    color: #ffd43b;
}
QLabel#captureRawImage {
    color: #8fa0b2;
    background: #0d141b;
    border-bottom-left-radius: 12px;
    border-bottom-right-radius: 12px;
}
QPushButton#deleteCaptureButton {
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
    color: #ffffff;
    background: #c92a2a;
    border: 1px solid #ff6b6b;
    border-radius: 14px;
    font-size: 17px;
    font-weight: 800;
}
"""


def run_ocr_self_test() -> int:
    log_path = APP_ROOT / "ocr_self_test.log"
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with log_path.open("w", encoding="utf-8") as log_stream:
        sys.stdout = log_stream
        sys.stderr = log_stream
        redirected_handlers = redirect_unavailable_logging_streams(log_stream)
        try:
            create_reader()
            print("OCR_SELF_TEST=passed", flush=True)
            return 0
        except Exception:
            print("OCR_SELF_TEST=failed", flush=True)
            traceback.print_exc()
            return 1
        finally:
            restore_logging_streams(redirected_handlers, original_stderr)
            sys.stdout = original_stdout
            sys.stderr = original_stderr


def main() -> int:
    if "--self-test-ocr" in sys.argv:
        return run_ocr_self_test()

    enable_dpi_awareness()
    app = QApplication(sys.argv)
    app.setStyleSheet(LIVE_STYLESHEET)
    window = LiveStockWindow()
    console_bridge = ConsoleLogBridge()
    console_bridge.lines_received.connect(window.log_dashboard.append_console_lines)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    null_stream: TextIO | None = None
    if original_stdout is None or original_stderr is None:
        null_stream = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = TeeTextStream(original_stdout or null_stream, console_bridge)
    sys.stderr = TeeTextStream(original_stderr or null_stream, console_bridge)
    redirected_handlers = redirect_unavailable_logging_streams(sys.stderr)
    app.aboutToQuit.connect(window.shutdown)

    def flush_console_logs() -> None:
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, TeeTextStream):
                stream.flush_pending()
        console_bridge.stop()

    app.aboutToQuit.connect(flush_console_logs)

    # Qt event loop 중에도 콘솔 Ctrl+C가 처리되도록 Python에 주기적으로 제어를 준다.
    signal.signal(signal.SIGINT, lambda *_args: app.quit())
    signal_timer = QTimer()
    signal_timer.timeout.connect(lambda: None)
    signal_timer.start(200)

    window.show()
    QTimer.singleShot(0, window.start_capture_services)
    try:
        return app.exec()
    finally:
        flush_console_logs()
        restore_logging_streams(redirected_handlers, original_stderr)
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        if null_stream is not None:
            null_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
