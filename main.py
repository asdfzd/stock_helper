from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class PriceLevel:
    kind: str
    price: float


@dataclass
class StockData:
    code: str
    current_price: float
    levels: list[PriceLevel]
    owned: bool = False


class PriceArea(QWidget):
    """현재가와 가장 가까운 위/아래 가격을 그리는 영역."""

    PRICE_SPAN = 0.30
    MIN_TEXT_GAP = 24
    EDGE_MARGIN = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(280)
        self._current_price = 0.0
        self._upper: PriceLevel | None = None
        self._lower: PriceLevel | None = None

    def set_prices(
        self,
        current_price: float,
        upper: PriceLevel | None,
        lower: PriceLevel | None,
    ) -> None:
        self._current_price = current_price
        self._upper = upper
        self._lower = lower
        self.update()

    def _level_y(self, price: float, center_y: float, half_height: float) -> float:
        difference = price - self._current_price
        distance = min(abs(difference) / self.PRICE_SPAN, 1.0) * half_height
        return center_y - distance if difference > 0 else center_y + distance

    def _label_text(self, kind: str, price: float, emphasized: bool = False) -> str:
        if emphasized:
            return f"{kind}  {price:.2f}"
        percentage = ((price - self._current_price) / self._current_price) * 100
        return f"{kind}  {price:.2f} ({percentage:+.2f}%)"

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 메서드명
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(24, 0, -24, 0)
        center_y = self.height() / 2
        half_height = max(center_y - self.EDGE_MARGIN, 0)

        painter.setPen(QPen(QColor("#69db7c"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(rect.left(), center_y, rect.right(), center_y)
        self._draw_label(painter, center_y, "현재가", self._current_price, QColor("#69db7c"), True)

        if self._upper is not None:
            upper_y = self._level_y(self._upper.price, center_y, half_height)
            upper_text_y = min(upper_y, center_y - (self.MIN_TEXT_GAP / 2))
            self._draw_label(
                painter,
                upper_y,
                self._upper.kind,
                self._upper.price,
                QColor("#ff6b6b"),
                text_y=upper_text_y,
            )

        if self._lower is not None:
            lower_y = self._level_y(self._lower.price, center_y, half_height)
            lower_text_y = max(lower_y, center_y + (self.MIN_TEXT_GAP / 2))
            self._draw_label(
                painter,
                lower_y,
                self._lower.kind,
                self._lower.price,
                QColor("#4dabf7"),
                text_y=lower_text_y,
            )

    def _draw_label(
        self,
        painter: QPainter,
        y: float,
        kind: str,
        price: float,
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
        label = self._label_text(kind, price, emphasized)
        label_y = y if text_y is None else text_y
        if emphasized:
            text_width = painter.fontMetrics().horizontalAdvance(label)
            painter.drawText(line_right - text_width, int(label_y) - 8, label)
        else:
            painter.drawText(line_left, int(label_y) - 8, label)


class StockCard(QFrame):
    def __init__(self, stock: StockData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stock = stock
        self.setObjectName("stockCard")
        self.setMinimumSize(420, 480)
        self.setMaximumWidth(520)

        card_layout = QVBoxLayout(self)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("cardHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 24, 20)

        self.code_label = QLabel(stock.code)
        self.code_label.setObjectName("stockCode")
        self.price_label = QLabel()
        self.price_label.setObjectName("currentPrice")
        self.price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(stock.owned)
        self.toggle_button.clicked.connect(self._on_toggle)

        header_layout.addWidget(self.code_label)
        header_layout.addStretch()
        header_layout.addWidget(self.price_label)
        header_layout.addStretch()
        header_layout.addWidget(self.toggle_button)

        self.price_area = PriceArea()
        card_layout.addWidget(header, 1)
        card_layout.addWidget(self.price_area, 2)

        self._refresh_state()
        self.set_current_price(stock.current_price)

    def set_current_price(self, price: float) -> None:
        """새 현재가를 반영하고 표시할 위/아래 가격을 다시 선택한다."""
        self.stock.current_price = price
        upper, lower = self._nearest_levels(price)
        self.price_label.setText(f"{price:.2f}")
        self.price_area.set_prices(price, upper, lower)

    def _nearest_levels(self, current_price: float) -> tuple[PriceLevel | None, PriceLevel | None]:
        upper_candidates = [level for level in self.stock.levels if level.price > current_price]
        lower_candidates = [level for level in self.stock.levels if level.price < current_price]
        upper = min(upper_candidates, key=lambda level: level.price, default=None)
        lower = max(lower_candidates, key=lambda level: level.price, default=None)
        return upper, lower

    def _on_toggle(self, checked: bool) -> None:
        self.stock.owned = checked
        self._refresh_state()

    def _refresh_state(self) -> None:
        is_on = self.stock.owned
        self.toggle_button.setText("ON" if is_on else "OFF")
        self.toggle_button.setProperty("owned", is_on)
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)


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
