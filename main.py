from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from stock_models import StockRecord, StockRegistry, build_price_candidates


@dataclass(frozen=True)
class PriceLevel:
    kind: str
    price: float
    key: str = ""
    dwell_minutes: int | None = None


@dataclass
class StockData:
    code: str
    current_price: float | None
    levels: list[PriceLevel]
    owned: bool = False
    stock_name: str = ""
    status: str = ""
    price_decimals: int = 2


class PriceArea(QWidget):
    """현재가와 가장 가까운 위/아래 가격을 방향별 최대 2개 그리는 영역."""

    MIN_PRICE_SPAN_RATIO = 0.10
    MIN_TEXT_GAP = 24
    EDGE_MARGIN = 14
    GENERIC_LEVEL_COLOR = QColor(77, 171, 247)
    BUY_REBOUND_COLOR = QColor(0, 0, 0)
    TAECHO_COLOR = QColor(255, 0, 255)
    ABSOLUTE_HALF_COLOR = QColor(0, 128, 0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("priceArea")
        self.setMinimumHeight(280)
        self._current_price = 0.0
        self._upper: list[PriceLevel] = []
        self._lower: list[PriceLevel] = []
        self._price_decimals = 2

    def set_prices(
        self,
        current_price: float | None,
        upper: list[PriceLevel],
        lower: list[PriceLevel],
        price_decimals: int = 2,
    ) -> None:
        self._current_price = current_price
        self._upper = upper
        self._lower = lower
        self._price_decimals = price_decimals
        self.update()

    def _price_span(self) -> float:
        if self._current_price is None:
            return 0.0001
        visible = [*self._upper, *self._lower]
        largest_difference = max(
            (abs(level.price - self._current_price) for level in visible),
            default=0.0,
        )
        minimum_span = abs(self._current_price) * self.MIN_PRICE_SPAN_RATIO
        return max(largest_difference, minimum_span, 0.0001)

    def _level_y(self, price: float, center_y: float, half_height: float) -> float:
        if self._current_price is None:
            return center_y
        difference = price - self._current_price
        price_span = self._price_span()
        distance = min(abs(difference) / price_span, 1.0) * half_height
        return center_y - distance if difference > 0 else center_y + distance

    def _format_price(self, price: float) -> str:
        return f"{price:.{self._price_decimals}f}"

    def _label_text(self, level: PriceLevel, emphasized: bool = False) -> str:
        kind = level.kind
        price = level.price
        if price is None:
            return f"{kind}  unavailable"
        if emphasized:
            return f"{kind}  {self._format_price(price)}"
        if self._current_price is None or self._current_price <= 0:
            return f"{kind}  {self._format_price(price)}"
        percentage = ((price - self._current_price) / self._current_price) * 100
        label = f"{kind}  {self._format_price(price)} ({percentage:+.2f}%)"
        if level.dwell_minutes is not None:
            label += f" · 근처 {level.dwell_minutes}분째"
        return label

    @classmethod
    def _level_color(cls, level: PriceLevel) -> QColor:
        if level.key in {"buy_price", "rebound_price"}:
            return cls.BUY_REBOUND_COLOR
        if level.key == "taecho":
            return cls.TAECHO_COLOR
        if level.key == "absolute_half":
            return cls.ABSOLUTE_HALF_COLOR
        return cls.GENERIC_LEVEL_COLOR

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 메서드명
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(24, 0, -24, 0)
        center_y = self.height() / 2
        half_height = max(center_y - self.EDGE_MARGIN, 0)

        painter.setPen(QPen(QColor("#69db7c"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(rect.left(), center_y, rect.right(), center_y)
        self._draw_label(
            painter,
            center_y,
            PriceLevel("현재가", self._current_price),
            QColor("#69db7c"),
            True,
        )

        previous_text_y: float | None = None
        for upper in self._upper:
            upper_y = self._level_y(upper.price, center_y, half_height)
            upper_text_y = min(upper_y, center_y - (self.MIN_TEXT_GAP / 2))
            if previous_text_y is not None:
                upper_text_y = min(upper_text_y, previous_text_y - self.MIN_TEXT_GAP)
            self._draw_label(
                painter,
                upper_y,
                upper,
                self._level_color(upper),
                text_y=upper_text_y,
            )
            previous_text_y = upper_text_y

        previous_text_y = None
        for lower in self._lower:
            lower_y = self._level_y(lower.price, center_y, half_height)
            lower_text_y = max(lower_y, center_y + (self.MIN_TEXT_GAP / 2))
            if previous_text_y is not None:
                lower_text_y = max(lower_text_y, previous_text_y + self.MIN_TEXT_GAP)
            self._draw_label(
                painter,
                lower_y,
                lower,
                self._level_color(lower),
                text_y=lower_text_y,
            )
            previous_text_y = lower_text_y

    def _draw_label(
        self,
        painter: QPainter,
        y: float,
        level: PriceLevel,
        color: QColor,
        emphasized: bool = False,
        text_y: float | None = None,
    ) -> None:
        line_left = 42
        line_right = self.width() - 42
        painter.setPen(QPen(color, 3 if emphasized else 1))
        painter.drawLine(line_left, int(y), line_right, int(y))

        font = QFont("Malgun Gothic", 11)
        font.setBold(emphasized)
        painter.setFont(font)
        label = self._label_text(level, emphasized)
        label_y = y if text_y is None else text_y
        if emphasized:
            text_width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(line_right - text_width, int(label_y) - 8, label)
        else:
            painter.drawText(line_left, int(label_y) - 8, label)


class StockCard(QFrame):
    PROXIMITY_KEYS = {"buy_price", "rebound_price", "taecho", "absolute_half"}
    PROXIMITY_RATIO = 0.04
    HEADER_HEIGHT = 104

    def __init__(
        self,
        stock: StockData,
        parent: QWidget | None = None,
        on_holding_changed: Callable[[str, bool], None] | None = None,
        on_delete_requested: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.stock = stock
        self._on_holding_changed = on_holding_changed
        self._on_delete_requested = on_delete_requested
        self._proximity_started: dict[tuple[str, float], float] = {}
        self.setObjectName("stockCard")
        self.setMinimumSize(340, 300)
        self.setMaximumWidth(520)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("cardHeader")
        header.setFixedHeight(self.HEADER_HEIGHT)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 12, 24, 12)

        code_container = QWidget()
        code_layout = QHBoxLayout(code_container)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(8)
        self.code_label = QLabel(stock.code)
        self.code_label.setObjectName("stockCode")
        self.status_label = QLabel()
        self.status_label.setObjectName("stockStatus")
        code_layout.addWidget(self.code_label)
        code_layout.addWidget(self.status_label)
        code_layout.addStretch()
        self.price_label = QLabel()
        self.price_label.setObjectName("currentPrice")
        self.price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(stock.owned)
        self.toggle_button.clicked.connect(self._on_toggle)
        self.delete_button = QPushButton("×")
        self.delete_button.setObjectName("deleteStockButton")
        self.delete_button.setToolTip(f"{stock.code} 추적 종료")
        self.delete_button.setFixedSize(28, 28)
        self.delete_button.clicked.connect(self._request_delete)

        right_controls = QWidget()
        right_layout = QVBoxLayout(right_controls)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        right_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight
        )
        right_layout.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignRight)
        right_layout.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignRight)

        header_layout.addWidget(code_container)
        header_layout.addStretch()
        header_layout.addWidget(self.price_label)
        header_layout.addStretch()
        header_layout.addWidget(
            right_controls,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )

        self.price_area = PriceArea()
        card_layout.addWidget(header, 0)
        card_layout.addWidget(self.price_area, 1)

        self._refresh_state()
        self.update_stock(stock)

    def update_stock(self, stock: StockData) -> None:
        """기존 카드 객체를 유지하면서 Registry 최신 snapshot을 반영한다."""
        self.stock = stock
        self.code_label.setText(stock.code)
        self.code_label.setToolTip(stock.stock_name)
        self.delete_button.setToolTip(f"{stock.code} 추적 종료")
        self.status_label.setText(stock.status)
        self.status_label.setVisible(bool(stock.status))
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(stock.owned)
        self.toggle_button.blockSignals(False)
        self._refresh_state()
        self.set_current_price(stock.current_price)

    def set_current_price(self, price: float | None) -> None:
        """새 현재가를 반영하고 표시할 위/아래 가격을 다시 선택한다."""
        self.stock.current_price = price
        levels = self._levels_with_proximity(price)
        upper, lower = self._nearest_levels(price, levels) if price is not None else ([], [])
        self.price_label.setText(
            f"{price:.{self.stock.price_decimals}f}" if price is not None else "unavailable"
        )
        self.price_area.set_prices(
            price, upper, lower, price_decimals=self.stock.price_decimals
        )

    def _nearest_levels(
        self, current_price: float, levels: list[PriceLevel] | None = None
    ) -> tuple[list[PriceLevel], list[PriceLevel]]:
        candidates = self.stock.levels if levels is None else levels
        upper_candidates = [level for level in candidates if level.price >= current_price]
        lower_candidates = [level for level in candidates if level.price < current_price]
        upper = sorted(upper_candidates, key=lambda level: level.price)[:2]
        lower = sorted(lower_candidates, key=lambda level: level.price, reverse=True)[:2]
        return upper, lower

    def _levels_with_proximity(self, current_price: float | None) -> list[PriceLevel]:
        now = time.monotonic()
        active: set[tuple[str, float]] = set()
        levels: list[PriceLevel] = []
        for level in self.stock.levels:
            identity = (level.key, level.price)
            dwell_minutes: int | None = None
            is_tracked = level.key in self.PROXIMITY_KEYS and level.price > 0
            is_near = (
                is_tracked
                and current_price is not None
                and current_price > 0
                and abs(current_price - level.price) / level.price
                <= self.PROXIMITY_RATIO
            )
            if is_near:
                active.add(identity)
                started = self._proximity_started.setdefault(identity, now)
                dwell_minutes = int((now - started) // 60)
            levels.append(
                PriceLevel(level.kind, level.price, level.key, dwell_minutes)
            )
        for identity in tuple(self._proximity_started):
            if identity not in active:
                self._proximity_started.pop(identity, None)
        return levels

    def _on_toggle(self, checked: bool) -> None:
        self.stock.owned = checked
        self._refresh_state()
        if self._on_holding_changed is not None:
            self._on_holding_changed(self.stock.code, checked)

    def _request_delete(self) -> None:
        if self._on_delete_requested is not None:
            self._on_delete_requested(self.stock.code)

    def _refresh_state(self) -> None:
        is_on = self.stock.owned
        self.toggle_button.setText("ON" if is_on else "OFF")
        self.toggle_button.setProperty("owned", is_on)
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)


def stock_data_from_record(stock: StockRecord) -> StockData:
    current_price = (
        float(stock.current_price)
        if stock.price_status in {"valid", "stale"} and stock.current_price is not None
        else None
    )
    if stock.daily_loaded and stock.minute_loaded:
        status = "daily + minute 완료"
    elif stock.daily_loaded:
        status = "daily 완료"
    elif stock.minute_loaded:
        status = "minute 완료"
    else:
        status = "대기"
    return StockData(
        code=stock.stock_code,
        stock_name=stock.stock_name,
        current_price=current_price,
        levels=[
            PriceLevel(candidate.label, float(candidate.value), candidate.key)
            for candidate in build_price_candidates(stock)
        ],
        owned=stock.holding,
        status=status,
        price_decimals=4,
    )


class StockCardsView(QScrollArea):
    """Registry 순서대로 최대 6개 카드를 중앙 정렬한 3열×2행 영역."""

    MAX_CARDS = 6
    CARDS_PER_ROW = 3

    def __init__(
        self,
        registry: StockRegistry,
        parent: QWidget | None = None,
        on_stock_removed: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self._on_stock_removed = on_stock_removed
        self.cards: dict[str, StockCard] = {}
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        container.setObjectName("stockCardsContainer")
        self.card_layout = QVBoxLayout(container)
        self.card_layout.setContentsMargins(24, 24, 24, 24)
        self.card_layout.setSpacing(20)
        self._row_widgets: list[QWidget] = []
        self._row_layouts: list[QHBoxLayout] = []
        for _row_index in range(2):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(20)
            row_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self._row_widgets.append(row_widget)
            self._row_layouts.append(row_layout)
            self.card_layout.addWidget(row_widget, 1)
        self._row_widgets[1].setVisible(False)
        self.setWidget(container)

    @property
    def card_count(self) -> int:
        return len(self.cards)

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(self.cards)

    def refresh_from_registry(self, updated_symbol: str) -> None:
        normalized = updated_symbol.strip().upper()
        card_existed = normalized in self.cards
        self.sync_from_registry()
        card_created = not card_existed and normalized in self.cards
        card_updated = card_existed and normalized in self.cards

        updated = self.registry.get_snapshot(normalized)
        if updated is not None:
            print("[UI UPDATE]", flush=True)
            print(f"ticker: {normalized}", flush=True)
            print(f"daily_loaded: {str(updated.daily_loaded).lower()}", flush=True)
            print(f"minute_loaded: {str(updated.minute_loaded).lower()}", flush=True)
            print(f"card_created: {str(card_created).lower()}", flush=True)
            print(f"card_updated: {str(card_updated).lower()}", flush=True)
        print("[UI]", flush=True)
        print(f"stock_card_count: {self.card_count}", flush=True)
        print(f"tickers: {', '.join(self.tickers)}", flush=True)

    def sync_from_registry(self) -> None:
        """현재가 polling을 포함한 Registry 최신 snapshot 전체를 다시 그린다."""
        stocks = self.registry.all_snapshots()[: self.MAX_CARDS]
        desired_symbols = {stock.stock_code for stock in stocks}
        for symbol in tuple(self.cards):
            if symbol not in desired_symbols:
                card = self.cards.pop(symbol)
                card.setParent(None)
                card.deleteLater()

        for stock in stocks:
            data = stock_data_from_record(stock)
            card = self.cards.get(stock.stock_code)
            if card is None:
                card = StockCard(
                    data,
                    on_holding_changed=self._set_holding,
                    on_delete_requested=self._confirm_remove_stock,
                )
                self.cards[stock.stock_code] = card
            else:
                card.update_stock(data)
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        for row_layout in self._row_layouts:
            while row_layout.count():
                row_layout.takeAt(0)

        cards = list(self.cards.values())
        use_two_rows = len(cards) >= 4
        self._row_widgets[1].setVisible(use_two_rows)
        for index, card in enumerate(cards):
            row_index = index // self.CARDS_PER_ROW if use_two_rows else 0
            self._row_layouts[row_index].addWidget(card)

    def _confirm_remove_stock(self, symbol: str) -> None:
        answer = QMessageBox.question(
            self,
            "추적 종료",
            f"{symbol} 추적 종료할까요?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        removed = self.registry.remove(symbol)
        if removed is None:
            return
        self.sync_from_registry()
        print(f"[TRACKING STOPPED] ticker={symbol}", flush=True)
        if self._on_stock_removed is not None:
            self._on_stock_removed(symbol)

    def _set_holding(self, symbol: str, holding: bool) -> None:
        self.registry.set_holding(symbol, holding)


class MainWindow(QMainWindow):
    # TEST ONLY: 실제 시세 연동 전 현재가 표시 로직을 검증하기 위한 범위
    TEST_PRICE_MIN = 0.60
    TEST_PRICE_MAX = 1.60
    TEST_PRICE_SCALE = 100

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stock Helper - UI Prototype")
        self.resize(720, 640)

        stock = StockData(
            code="TEST",
            current_price=1.00,
            levels=[
                PriceLevel("매입가", 1.20),
                PriceLevel("반등가", 0.80),
                PriceLevel("벽", 0.92),
                PriceLevel("벽", 1.35),
                PriceLevel("벽", 1.48),
            ],
        )

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(48, 48, 48, 48)

        card_row = QHBoxLayout()
        card_row.addStretch()
        self.stock_card = StockCard(stock)
        card_row.addWidget(self.stock_card)
        card_row.addStretch()
        layout.addLayout(card_row, 1)

        # TEST ONLY: OCR/API 대신 수동으로 가짜 현재가를 입력하는 컨트롤
        test_controls = QWidget()
        test_controls.setObjectName("testControls")
        controls_layout = QVBoxLayout(test_controls)
        controls_layout.setContentsMargins(18, 12, 18, 12)

        self.test_price_label = QLabel()
        self.test_price_label.setObjectName("testPriceLabel")
        self.test_price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.test_price_slider = QSlider(Qt.Orientation.Horizontal)
        self.test_price_slider.setObjectName("testPriceSlider")
        self.test_price_slider.setRange(
            round(self.TEST_PRICE_MIN * self.TEST_PRICE_SCALE),
            round(self.TEST_PRICE_MAX * self.TEST_PRICE_SCALE),
        )
        self.test_price_slider.setValue(round(stock.current_price * self.TEST_PRICE_SCALE))
        self.test_price_slider.setSingleStep(1)
        self.test_price_slider.valueChanged.connect(self._on_test_price_changed)

        controls_layout.addWidget(self.test_price_label)
        controls_layout.addWidget(self.test_price_slider)
        layout.addWidget(test_controls)

        self._on_test_price_changed(self.test_price_slider.value())
        self.setCentralWidget(central)

    def _on_test_price_changed(self, slider_value: int) -> None:
        """테스트 슬라이더 값을 가짜 현재가로 즉시 반영한다."""
        price = slider_value / self.TEST_PRICE_SCALE
        self.test_price_label.setText(
            f"테스트용 현재가  {price:.2f}    "
            f"({self.TEST_PRICE_MIN:.2f} ~ {self.TEST_PRICE_MAX:.2f})"
        )
        self.stock_card.set_current_price(price)


STYLESHEET = """
QMainWindow, QMainWindow > QWidget {
    background: #111820;
}
QFrame#stockCard {
    background: #1b2530;
    border: 1px solid #344353;
    border-radius: 16px;
}
QWidget#priceArea {
    background: #ffffff;
    border-bottom-left-radius: 16px;
    border-bottom-right-radius: 16px;
}
QWidget#cardHeader {
    background: #222f3d;
    border-top-left-radius: 16px;
    border-top-right-radius: 16px;
    border-bottom: 1px solid #344353;
}
QLabel#stockCode {
    color: #f2f5f8;
    font-family: "Malgun Gothic";
    font-size: 22px;
    font-weight: 700;
}
QLabel#stockStatus {
    color: #8fa0b2;
    font-family: "Malgun Gothic";
    font-size: 11px;
    font-weight: 600;
}
QLabel#currentPrice {
    color: #69db7c;
    font-family: "Segoe UI";
    font-size: 30px;
    font-weight: 700;
}
QPushButton {
    min-width: 62px;
    min-height: 34px;
    color: #d8dee5;
    background: #465464;
    border: none;
    border-radius: 17px;
    font-weight: 700;
}
QPushButton[owned="true"] {
    color: #102117;
    background: #69db7c;
}
QPushButton#deleteStockButton {
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
    color: #ff8787;
    background: transparent;
    border: 1px solid #6b3a42;
    border-radius: 14px;
    font-size: 17px;
    font-weight: 800;
}
QPushButton#deleteStockButton:hover {
    color: #ffffff;
    background: #c92a2a;
    border-color: #ff6b6b;
}
QWidget#testControls {
    background: #1b2530;
    border: 1px solid #344353;
    border-radius: 10px;
}
QLabel#testPriceLabel {
    color: #aeb9c5;
    font-family: "Malgun Gothic";
    font-size: 12px;
    font-weight: 600;
}
QSlider#testPriceSlider::groove:horizontal {
    height: 6px;
    background: #344353;
    border-radius: 3px;
}
QSlider#testPriceSlider::sub-page:horizontal {
    background: #69db7c;
    border-radius: 3px;
}
QSlider#testPriceSlider::handle:horizontal {
    width: 18px;
    margin: -6px 0;
    background: #f2f5f8;
    border: 2px solid #69db7c;
    border-radius: 9px;
}
"""


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
