"""Read-only shadow dashboard. No account selector or order controls."""
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from ..dashboard_widgets import DataTable, display


class PaperPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.enable = QCheckBox("Enable Paper Trading · 수집 시작 전에 선택 (기본 OFF)")
        self.enable.setChecked(False)
        layout.addWidget(self.enable)
        self.note = QLabel("Experimental baseline strategy · 수익성 미검증 · 실제 주문 0 / disabled\n"
                           "Kiwoom live 관측가격 기반 shadow test · 체결 가능성/거래비용은 모델링하지 않습니다.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.status = QLabel("Paper Trading: OFF · paper_v1_baseline · Trades: 0 / 4")
        layout.addWidget(self.status)
        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.recent = DataTable(tuple((key, label) for key, label in (
            ("ticker", "Ticker"), ("entry_price", "Entry"), ("exit_price", "Exit"),
            ("return_pct", "Return %"), ("exit_reason", "Exit reason"))))
        layout.addWidget(self.recent)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

    def render(self, state, *, busy, collection_mode):
        capacity = collection_mode == "Capacity Test"
        if capacity:
            self.enable.setChecked(False)
        self.enable.setEnabled(not busy and not capacity)
        if capacity:
            state = dict(status="OFF · Capacity Test disabled")
        elif state is None:
            state = dict(status="WAITING · next collection" if self.enable.isChecked() else "OFF")
        self.status.setText(f"Paper Trading: {state.get('status', 'OFF')} · paper_v1_baseline · Trades: {state.get('trades', 0)} / 4")
        active, row = state.get("active") or {}, state.get("evidence") or {}
        self.details.setText(
            f"Candidate: {active.get('ticker', '-')} · {active.get('candidate_class', '-')} · "
            f"Current #{display(active.get('current_rank'))} / Emerging #{display(active.get('emerging_rank'))}\n"
            f"Watch: {display(active.get('watch_reference_price'))} · completed 1m {display(row.get('return_1m'))}% / "
            f"5m {display(row.get('return_5m'))}%\n"
            f"Entry: {display(active.get('entry_price'))} · Current: {display(active.get('current_price'))} · "
            f"Return: {display(active.get('current_return_pct'))}% · TP: {display(active.get('target_price'))} · "
            f"SL: {display(active.get('stop_price'))} · MFE: {display(active.get('mfe_pct'))}% / MAE: {display(active.get('mae_pct'))}%")
        self.recent.render_rows(state.get("recent", []))
        summary = state.get("summary") or {}
        self.summary.setText(f"TP {summary.get('take_profit_count', 0)} / SL {summary.get('stop_loss_count', 0)} / "
            f"SESSION_END {summary.get('session_end_count', 0)} / Invalid {summary.get('invalid_count', 0)} · "
            f"TP/SL win rate {display(summary.get('tp_sl_win_rate'))}% · "
            f"Average return {display(summary.get('average_return_pct'))}% (valid closed trades only)")
