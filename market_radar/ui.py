from __future__ import annotations

import queue
import json
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView,
)

from .config import CONFIG
from .provider import JsonlReplayProvider, create_live_provider
from .runtime import RadarWorker

COLUMNS = [
    ("ticker", "Ticker"), ("price", "가격"), ("day_change_pct", "당일 %"),
    ("volume", "거래량"), ("rvol", "RVOL"), ("return_1m", "1분 %"),
    ("return_5m", "5분 %"), ("vwap", "VWAP"), ("price_vs_vwap_pct", "VWAP 거리 %"),
    ("above_vwap", "VWAP 위/이하"), ("opening_1m_high", "OR1 High"),
    ("opening_1m_low", "OR1 Low"), ("opening_5m_high", "OR5 High"),
    ("opening_5m_low", "OR5 Low"), ("hod", "HOD"), ("hod_distance_pct", "HOD 거리 %"),
    ("candidate_sources", "Sources"), ("bar_cutoff_et", "봉 기준 ET"),
]


class RadarPanel(QWidget):
    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle("Market Radar · 연구 데이터 수집")
        self.resize(1250, 700)
        self.worker = None
        self.config = config
        layout = QVBoxLayout(self)
        self.mode = QLabel("Kiwoom · 미연결 (시작 전) · 완료 1분 기준 RVOL · VWAP 미확정")
        layout.addWidget(self.mode)
        self.status = QLabel("수집 시작 시 .env의 Kiwoom real 인증으로 연결합니다. 주문 기능 없음.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("실제 수집 시작")
        self.replay_button = QPushButton("기록 JSONL replay 열기")
        self.stop_button = QPushButton("수집 중지")
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.replay_button)
        buttons.addWidget(self.stop_button)
        layout.addLayout(buttons)
        self.tables = []
        for title in ("Current Leaders TOP 10 · 당일 등락률순", "Emerging Candidates TOP 10 · 최근 5분 수익률순"):
            layout.addWidget(QLabel(title))
            table = QTableWidget(0, len(COLUMNS))
            table.setHorizontalHeaderLabels([label for _, label in COLUMNS])
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            layout.addWidget(table)
            self.tables.append(table)
        layout.addWidget(QLabel("— = unavailable · 수익률 기준시점이 없으면 NULL · HOD 거리=(가격/HOD−1)×100 · 범위는 공급원 universe"))
        self.start_button.clicked.connect(lambda: self.start(create_live_provider(self.config or CONFIG)))
        self.replay_button.clicked.connect(self.open_replay)
        self.stop_button.clicked.connect(self.stop)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)

    def open_replay(self):
        path, _ = QFileDialog.getOpenFileName(self, "기록 데이터 replay (실시간 아님)", "", "JSONL (*.jsonl)")
        if path:
            self.start(JsonlReplayProvider(Path(path)))

    def start(self, provider):
        if self.worker and self.worker.running:
            return
        for table in self.tables:
            table.setRowCount(0)
        self.mode.setText("REPLAY · 기록/테스트 데이터 · 실시간 아님 · 별도 replay DB" if provider.mode == "replay"
                          else f"LIVE provider: {provider.name} · 정규장" +
                          (" · RVOL/return: 완료 1분 · VWAP 미확정" if provider.name == "kiwoom" else ""))
        self.worker = RadarWorker(provider, self.config or CONFIG)
        self.worker.start()
        self.start_button.setEnabled(False)
        self.replay_button.setEnabled(False)

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.status.setText("중지 요청 · 작업 및 CSV export 종료 대기")

    def poll(self):
        if not self.worker:
            return
        try:
            message = self.worker.messages.get_nowait()
        except queue.Empty:
            message = None
        if message:
            self.status.setText(message["status"])
            if message["rows"] is not None:
                self.render(message["rows"])
        self.start_button.setEnabled(not self.worker.running)
        self.replay_button.setEnabled(not self.worker.running)

    def render(self, rows):
        for table, rank_key in zip(self.tables, ("current_leader_rank", "emerging_rank")):
            selected = sorted((r for r in rows if r[rank_key] is not None and r[rank_key] <= 10), key=lambda r: r[rank_key])
            table.setRowCount(len(selected))
            for i, row in enumerate(selected):
                for j, (key, _) in enumerate(COLUMNS):
                    value = row.get(key)
                    if key == "candidate_sources" and value:
                        labels = {"day_change": "CHG", "5m_spike": "SPIKE", "volume": "VOL", "value": "VALUE"}
                        value = "+".join(labels.get(s, s) for s in json.loads(value))
                    text = "—" if value is None else ("위" if value else "이하") if key == "above_vwap" else f"{value:.4f}" if isinstance(value, float) else str(value)
                    table.setItem(i, j, QTableWidgetItem(text))

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


def show_radar(owner):
    # Import/create only when requested; OCR startup does not initialize Radar.
    panel = getattr(owner, "_market_radar_panel", None)
    if panel is None:
        panel = RadarPanel()
        owner._market_radar_panel = panel
    panel.show()
    panel.raise_()
    panel.activateWindow()
