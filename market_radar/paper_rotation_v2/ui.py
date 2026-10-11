"""Two visibly separated strategies; rotation is opt-in before collection."""
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QCheckBox
from ..paper.ui import PaperPanel


class PairedPaperPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Baseline v1"))
        self.baseline = PaperPanel()
        self.enable = self.baseline.enable
        layout.addWidget(self.baseline)
        self.rotation_enable = QCheckBox("Enable Rotation v2 · Baseline v1과 동시 실행 (기본 OFF)")
        self.rotation_enable.toggled.connect(lambda checked: self.enable.setChecked(True) if checked else None)
        self.enable.toggled.connect(lambda checked: self.rotation_enable.setChecked(False) if not checked else None)
        layout.addWidget(self.rotation_enable)
        layout.addWidget(QLabel("Rotation v2 · material evidence dominance · 실제 주문 0"))
        self.rotation = PaperPanel()
        self.rotation.enable.hide()
        self.rotation.note.setText("독립 실험 전략 · 현재 종목 약화 + 새 후보의 모든 우월 근거 + 정상 진입 확인 필요")
        layout.addWidget(self.rotation)
        self.review = QLabel("Post-exit tracking: OFF")
        self.review.setWordWrap(True)
        layout.addWidget(self.review)

    def render(self, state, *, busy, collection_mode):
        self.baseline.render(state, busy=busy, collection_mode=collection_mode)
        capacity = collection_mode == "Capacity Test"
        if capacity:
            self.rotation_enable.setChecked(False)
        self.rotation_enable.setEnabled(not busy and not capacity)
        rotation = (state or {}).get("rotation_v2") or dict(status="WAITING" if self.rotation_enable.isChecked() else "OFF")
        self.rotation.render(rotation, busy=busy, collection_mode=collection_mode)
        self.rotation.status.setText(f"Rotation v2: {rotation.get('status', 'OFF')} · paper_rotation_v2 · Trades: {rotation.get('trades', 0)} / 4")
        pending = rotation.get("pending_switch") or {}
        self.review.setText(f"Post-exit tracking: {(state or {}).get('post_exit_tracking', 'OFF')} · "
            f"Switch: {pending.get('stage', '-')} · {pending.get('source_ticker', '-')} → {pending.get('target_ticker', '-')}\n"
            "공식 종가까지 관측한 경로는 review export에 저장합니다. 공백·조기중지는 INCOMPLETE로 기록합니다.")
