"""Market Radar dashboard; IO runs on collector/service threads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import json
import queue
import time

from PySide6.QtCore import QSettings, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QComboBox, QCheckBox, QScrollArea)

from . import __version__
from .config import CONFIG, DEFAULT_SHARED_CACHE_PATH
from .controller import CollectorController
from .dashboard_widgets import DataTable, LIVE_COLUMNS, HISTORY_COLUMNS, health_rows, live_rows, display
from .models import ET, instant
from .scheduling import MODES
from .provider import JsonlReplayProvider
from .research.importer import import_session, live_health
from .research.master_db import DEFAULT_MASTER_PATH, MasterDB
from .research.review_export import create_review_zip
from .research.validator import revalidate_session

COLUMNS = LIVE_COLUMNS


class RadarPanel(QWidget):
    radar_ticker_selected = Signal(str)

    def __init__(self, parent=None, config=None, *, master_path=DEFAULT_MASTER_PATH, settings=None):
        super().__init__(parent)
        self.config = config
        self.one_hour = None
        self.shutdown_test = None
        self._one_hour_preferences = None
        self.mutation_buttons = []
        base = config or CONFIG
        self.master = MasterDB(master_path)
        self.controller = CollectorController(master_path)
        self.settings = settings or QSettings("StockHelper", "MarketRadarDashboard")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="radar-research")
        self.jobs = {}
        self._health_at = 0
        self._summary = {}
        self._tracking = {}
        self._selected_session = None
        self._current_session = None
        self._selection_generation = 0
        self._history_offset = 0
        self._refresh_after_history = False
        self._closing = self.closed = False
        self.setWindowTitle(f"Market Radar Dashboard · v{__version__}")
        self.resize(1400, 850)
        outer = QVBoxLayout(self)
        advanced = QWidget()
        layout = QVBoxLayout(advanced)
        self.mode = QLabel(f"Market Radar v{__version__} · 미연결 · Collector v2.1 · research schema 4 · master schema 1")
        layout.addWidget(self.mode)
        self.status = QLabel("STOPPED · Start Collection을 눌러 수집을 시작하세요.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.run_status = QLabel()
        self.run_status.setWordWrap(True)
        layout.addWidget(self.run_status)
        self.validation_status = QLabel()
        self.validation_status.setWordWrap(True)
        layout.addWidget(self.validation_status)
        self.cache_label = QLabel()
        layout.addWidget(self.cache_label)
        self.shutdown_label = QLabel()
        layout.addWidget(self.shutdown_label)
        self.schedule_label = QLabel("Session: STOPPED · Manual")
        self.schedule_label.setWordWrap(True)
        layout.addWidget(self.schedule_label)
        controls = QHBoxLayout()
        self.collection_mode = QComboBox()
        self.collection_mode.addItems(MODES)
        self.collection_mode.setCurrentText(base.collection_mode if config else "Regular Session")
        controls.addWidget(QLabel("Collection Mode"))
        controls.addWidget(self.collection_mode)
        self.max_ws = QSpinBox()
        self.max_ws.setRange(1, 200)
        self.max_ws.setValue(base.max_ws_symbols if config else int(self.settings.value("max_ws", 150)))
        self.data_root = QLineEdit(str(base.data_dir if config else self.settings.value("data_root", str(base.data_dir))))
        self.grace = QDoubleSpinBox()
        self.grace.setDecimals(3)
        self.grace.setRange(0, 3600)
        self.grace.setValue(base.result_pre_target_grace_seconds)
        for label, widget in (("Max WS", self.max_ws), ("Data root", self.data_root), ("Pre-target grace (s)", self.grace)):
            controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        self.add_button(controls, "Browse", self.browse_root)
        self.start_button = self.add_button(controls, "Start Collection", lambda: self.start())
        self.stop_button = self.add_button(controls, "Stop Collection", self.stop)
        layout.addLayout(controls)
        opening_controls = QHBoxLayout()
        self.opening_hour_enable = QCheckBox("Opening Hour Research · 종료 후 분석")
        self.opening_hour_enable.setToolTip("기본 OFF · Regular Session 전용 · 기존 자료만 사후 분석")
        self.opening_hour_status = QLabel("OFF · candidates - · full coverage -")
        opening_controls.addWidget(self.opening_hour_enable)
        opening_controls.addWidget(self.opening_hour_status)
        opening_controls.addStretch()
        layout.addLayout(opening_controls)
        self.collection_mode.currentTextChanged.connect(self._render_opening_hour)
        self.opening_hour_enable.toggled.connect(self._render_opening_hour)
        auto_controls = QHBoxLayout()
        self.one_hour_enable = QCheckBox("첫 1시간 수집 + ZIP 검증 + PC 자동 종료")
        self.one_hour_no_shutdown = QCheckBox("PC 자동 종료 없이 테스트")
        self.one_hour_no_shutdown.setChecked(True)
        self.one_hour_no_shutdown.setToolTip("실제 수집·ZIP 검증은 수행하고 Windows 종료만 비활성화합니다.")
        auto_controls.addWidget(self.one_hour_enable)
        auto_controls.addWidget(self.one_hour_no_shutdown)
        self.one_hour_cancel = self.add_button(auto_controls, "PC 종료만 취소", self.cancel_one_hour)
        self.add_button(auto_controls, "첫 1시간 결과 폴더", self.open_one_hour_folder)
        layout.addLayout(auto_controls)
        self.one_hour_status = QLabel("첫 1시간 자동 실행: OFF · 실제 주문 OFF")
        self.one_hour_status.setWordWrap(True)
        layout.addWidget(self.one_hour_status)
        buttons = QHBoxLayout()
        self.replay_button = self.add_button(buttons, "Open Replay", self.open_replay)
        for label, action in (("Import Existing Session", self.import_existing), ("Refresh", self.refresh),
                ("Create Review ZIP", self.review), ("Open Session Folder", lambda: self.open_folder("data_dir")),
                ("Open Export Folder", lambda: self.open_folder("export_path")),
                ("Open Review ZIP Folder", lambda: self.open_folder("reviews"))):
            button = self.add_button(buttons, label, action)
            if label in {"Import Existing Session", "Create Review ZIP"}:
                self.mutation_buttons.append(button)
        layout.addLayout(buttons)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        live = QWidget()
        live_layout = QHBoxLayout(live)
        split = QSplitter(Qt.Orientation.Vertical)
        self.tables = []
        for title in ("Current Leaders · Day % descending", "Emerging Candidates · completed 1m return_5m descending"):
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(QLabel(title))
            table = DataTable(LIVE_COLUMNS)
            table.cellDoubleClicked.connect(lambda row, col, t=table: self.select_ticker(t, row))
            box_layout.addWidget(table)
            split.addWidget(box)
            self.tables.append(table)
        live_layout.addWidget(split, 4)
        health_box = QWidget()
        health_layout = QVBoxLayout(health_box)
        self.health_label = QLabel("System Health · 운영/데이터 품질")
        health_layout.addWidget(self.health_label)
        self.health = DataTable((("metric", "Metric"), ("value", "Value")))
        self.health.setColumnWidth(0, 185)
        self.health.render_rows(health_rows({}, base.max_ws_symbols))
        health_layout.addWidget(self.health)
        live_layout.addWidget(health_box, 2)
        self.tabs.addTab(live, "Live Radar")
        history_box = QWidget()
        history_layout = QVBoxLayout(history_box)
        paging = QHBoxLayout()
        self.page_label = QLabel("Sessions · 200 rows/page")
        paging.addWidget(self.page_label)
        for label, delta in (("Previous", -200), ("Next", 200)):
            self.add_button(paging, label, lambda checked=False, n=delta: self.history_page(n))
        history_layout.addLayout(paging)
        self.mutation_buttons.append(self.add_button(paging, "Revalidate Session", self.revalidate))
        self.history = DataTable(HISTORY_COLUMNS)
        for i, width in enumerate((100, 70, 85, 85, 115, 150, 90, 75, 115)):
            self.history.setColumnWidth(i, width)
        self.history.itemSelectionChanged.connect(self.session_selected)
        history_layout.addWidget(self.history)
        self.details_tabs = QTabWidget()
        self.candidates, self.results = DataTable(), DataTable()
        self.candidates.cellDoubleClicked.connect(lambda row, col: self.select_ticker(self.candidates, row))
        self.quality_text, self.source_text = QTextEdit(), QTextEdit()
        self.quality_text.setReadOnly(True)
        self.source_text.setReadOnly(True)
        self.validation_text = QTextEdit()
        self.validation_text.setReadOnly(True)
        for widget, label in ((self.candidates, "Candidates"), (self.results, "Results"),
                              (self.quality_text, "Quality"), (self.validation_text, "Validation Summary"),
                              (self.source_text, "Source files")):
            self.details_tabs.addTab(widget, label)
        history_layout.addWidget(self.details_tabs)
        self.tabs.addTab(history_box, "Session History")
        ticker_box = QWidget()
        ticker_layout = QVBoxLayout(ticker_box)
        ticker_controls = QHBoxLayout()
        self.ticker = QLineEdit()
        self.ticker.setPlaceholderText("Ticker · newest 1000 horizon rows")
        self.ticker.returnPressed.connect(self.search_ticker)
        ticker_controls.addWidget(self.ticker)
        self.add_button(ticker_controls, "Candidate History", self.search_ticker)
        self.add_button(ticker_controls, "Open in Stock Helper", self.analyze_ticker)
        ticker_layout.addLayout(ticker_controls)
        self.ticker_table = DataTable()
        ticker_layout.addWidget(self.ticker_table)
        self.tabs.addTab(ticker_box, "Candidate History")
        self.ticker_tab = ticker_box
        from .paper_rotation_v2.ui import PairedPaperPanel
        self.paper_panel = PairedPaperPanel()
        self.tabs.addTab(self.paper_panel, "Paper Trading")
        self.one_hour_enable.toggled.connect(self.configure_one_hour)
        self.collection_mode.currentTextChanged.connect(lambda: self.paper_panel.render(
            None, busy=self.controller.busy, collection_mode=self.collection_mode.currentText()))
        layout.addWidget(QLabel("- = unavailable · 더블클릭: 종목 선택 · 새 수집은 data root 아래 별도 폴더 · 폴더/ZIP은 선택 세션 기준"))
        layout.addStretch()
        advanced_scroll = QScrollArea()
        advanced_scroll.setWidgetResizable(True)
        advanced_scroll.setWidget(advanced)
        self.tabs.addTab(advanced_scroll, "수집 상세 설정")
        from .collection_ui import CollectionTestPanel
        self.collection_test = CollectionTestPanel(self)
        self.tabs.insertTab(0, self.collection_test, "수집 테스트")
        self.tabs.setCurrentWidget(self.collection_test)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        for i, table in enumerate([*self.tables, self.history]):
            key = f"columns_{i}" if i < 2 else "history_columns_v2_3_0"
            state = self.settings.value(key)
            if state:
                table.horizontalHeader().restoreState(state)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        QApplication.instance().aboutToQuit.connect(self.stop)
        self.refresh()
        self.poll()

    @staticmethod
    def add_button(layout, label, action):
        button = QPushButton(label)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    @property
    def worker(self):
        return self.controller if self.controller.thread is not None else None

    def submit(self, key, function, callback):
        if self.shutdown_test and self.shutdown_test.active:
            return
        if key not in self.jobs and not self.closed:
            self.jobs[key] = (self.executor.submit(function), callback)

    def browse_root(self):
        path = QFileDialog.getExistingDirectory(self, "Data root", self.data_root.text())
        if path:
            self.data_root.setText(path)

    def _render_opening_hour(self, *_):
        regular = self.collection_mode.currentText() == "Regular Session"
        if not regular:
            self.opening_hour_enable.setChecked(False)
        self.opening_hour_enable.setEnabled(regular and not self.controller.busy)
        result = self.controller.opening_hour_result if regular else {"status": "OFF"}
        status = result["status"]
        if status == "OFF" and self.opening_hour_enable.isChecked():
            status = "ENABLED"
        quality = result.get("quality", {})
        self.opening_hour_status.setText(f"{status} · all {quality.get('all_candidate_count', '-')} / "
            f"eligible {quality.get('eligible_candidate_count', '-')} · full coverage all "
            f"{quality.get('all_coverage', {}).get('candidates_with_full_60m', '-')} / eligible "
            f"{quality.get('eligible_coverage', {}).get('candidates_with_full_60m', '-')}")
        self.opening_hour_status.setToolTip(result.get("export_path") or result.get("error", "세션 종료 후 생성"))

    def start(self, provider=None, *, fresh_directory=None):
        if self.shutdown_test and self.shutdown_test.active:
            return
        if self.controller.busy or (self.one_hour and self.one_hour.active):
            return
        if self.one_hour_enable.isChecked() and provider is None:
            self.start_one_hour()
            return
        try:
            base = self.config or CONFIG
            config = base if provider and fresh_directory is None else replace(base, max_ws_symbols=self.max_ws.value(),
                data_dir=Path(self.data_root.text()).expanduser().resolve(), result_pre_target_grace_seconds=self.grace.value(),
                collection_mode=self.collection_mode.currentText() if provider is None else "Manual")
            self.controller.start(config, provider, fresh_directory=(provider is None if fresh_directory is None else fresh_directory),
                                  paper_enabled=self.paper_panel.enable.isChecked() and provider is None,
                                  paper_rotation_enabled=self.paper_panel.rotation_enable.isChecked() and provider is None,
                                  opening_hour_enabled=self.opening_hour_enable.isChecked() and provider is None)
            self.one_hour = None  # A new regular/replay run must not show the prior run's result.
            for table in self.tables:
                table.render_rows([])
            self._summary = {}
            self._tracking = {}
            self._current_session = None
            self.shutdown_label.clear()
            self._selected_session = None
            self.history.clearSelection()
            self.validation_status.clear()
            self.status.setText("STARTING · 연결 및 session DB 준비 중")
            self.mode.setText(("REPLAY · 기록/테스트 데이터" if provider else "LIVE · Kiwoom") + f" · {config.test_version}")
            self.poll()
        except ValueError as exc:
            self.status.setText(str(exc))
        except OSError:
            self.status.setText("설정 오류: 경로와 수집 설정을 확인하세요.")

    def configure_one_hour(self, enabled):
        if enabled:
            self._one_hour_preferences = (self.collection_mode.currentText(), self.max_ws.value(),
                self.grace.value(), self.paper_panel.enable.isChecked(),
                self.paper_panel.rotation_enable.isChecked(), self.opening_hour_enable.isChecked())
            self.collection_mode.setCurrentText("Regular Session")
            self.max_ws.setValue(150)
            self.grace.setValue(CONFIG.result_pre_target_grace_seconds)
            self.paper_panel.enable.setChecked(True)
            self.paper_panel.rotation_enable.setChecked(True)
            self.opening_hour_enable.setChecked(True)
        elif self._one_hour_preferences:
            mode, slots, grace, paper, rotation, opening = self._one_hour_preferences
            self.collection_mode.setCurrentText(mode)
            self.max_ws.setValue(slots)
            self.grace.setValue(grace)
            self.paper_panel.enable.setChecked(paper)
            self.paper_panel.rotation_enable.setChecked(rotation)
            self.opening_hour_enable.setChecked(opening)
            self._one_hour_preferences = None
        self.poll()

    def start_one_hour(self):
        if self.shutdown_test and self.shutdown_test.active:
            return
        if self.jobs:
            self.status.setText("기존 조회·저장 작업이 끝난 뒤 Start Collection을 눌러 주세요.")
            return
        try:
            from .one_hour import DashboardOneHour, DashboardGuard
            base = self.config or CONFIG
            root = Path(self.data_root.text()).expanduser().resolve()/"market_radar_one_hour"
            self.one_hour = DashboardOneHour(self.controller, root,
                no_shutdown=self.one_hour_no_shutdown.isChecked(), guard=DashboardGuard(),
                ready_to_shutdown=lambda: not self.jobs)
            self.one_hour.launch(cache=base.historical_cache_path or DEFAULT_SHARED_CACHE_PATH)
            self._summary, self._tracking = {}, {}
            self._current_session = self._selected_session = None
            self.history.clearSelection()
            for table in self.tables:
                table.render_rows([])
            self.shutdown_label.clear()
            self.validation_status.clear()
            self.status.setText("첫 1시간 준비 · 기존 Collector 1개 · 실제 주문 OFF")
        except Exception as exc:
            self.status.setText(f"첫 1시간 시작 차단 ({type(exc).__name__}) · 기존 수집 여부와 설정을 확인하세요.")
        self.poll()

    def cancel_one_hour(self):
        if self.one_hour and self.one_hour.active:
            self.one_hour.request_cancel_shutdown()
            self.one_hour_status.setText("PC 종료만 취소 요청 중 · 수집과 후처리는 계속합니다.")

    def open_one_hour_folder(self):
        if self.one_hour and self.one_hour.folder and self.one_hour.folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.one_hour.folder)))

    def render_one_hour(self, busy):
        enabled = self.one_hour_enable.isChecked()
        self.one_hour_enable.setEnabled(not busy)
        self.one_hour_no_shutdown.setEnabled(enabled and not busy)
        active = bool(self.one_hour and self.one_hour.active)
        self.one_hour_cancel.setEnabled(active)
        for button in self.mutation_buttons:
            button.setEnabled(not active)
        if enabled:
            for widget in (self.collection_mode, self.max_ws, self.grace, self.opening_hour_enable,
                           self.paper_panel.enable, self.paper_panel.rotation_enable):
                widget.setEnabled(False)
        runner = self.one_hour
        if not enabled and not active:
            self.one_hour_status.setText("첫 1시간 자동 실행: OFF · 실제 주문 OFF")
            return
        if runner is None:
            self.one_hour_status.setText("첫 1시간: 선택됨 · 개장 전 Start Collection · 실제 주문 OFF" if enabled else
                                        "첫 1시간 자동 실행: OFF · 실제 주문 OFF")
            return
        from zoneinfo import ZoneInfo
        def kst(stamp):
            return instant(stamp).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST") if stamp else "-"
        plan = runner.plan or {}
        archive = str(runner.archive) if runner.archive else "아직 생성되지 않음"
        shutdown = "비활성화 (테스트)" if runner.no_shutdown else kst(plan.get("shutdown_not_before"))+" 이후 안전한 보존 검증 시 60초 뒤 종료 예약"
        if runner.shutdown_at:
            shutdown = kst(runner.shutdown_at)+" · 자동 종료 취소 가능"
        if runner.finalized and not runner.shutdown_may_be_pending and runner.state != "COMPLETE_NO_SHUTDOWN":
            shutdown = "차단 또는 취소됨"
        warning = ("경고: 취소 실패. Windows 종료 예약이 남아 있을 수 있습니다. 자동 종료 취소를 다시 누르거나 shutdown.exe /a를 실행하세요."
                   if runner.reason == "shutdown_cancel_failed" else (runner.reason or ""))
        summary = runner.summary or {}
        result = summary.get("research_status")
        safe = summary.get("shutdown_eligible") is True
        if safe and result == "FAILED":
            verdict = "연구 오류 / 검증된 진단 ZIP 저장 / " + ("PC 종료 비활성화 (테스트)" if runner.no_shutdown else "PC 종료 가능")
        elif safe and result == "VERIFIED":
            verdict = "연구 정상 / 정상 ZIP 저장 / " + ("PC 종료 비활성화 (테스트)" if runner.no_shutdown else "PC 종료 가능")
        elif runner.finalized:
            verdict = "저장·검증 오류 또는 취소 / PC 종료 차단"
        else:
            verdict = "연구·보존 결과 확인 중"
        if runner.shutdown_may_be_pending and result == "FAILED":
            verdict += " · 연구 실패 기록을 보존하고 Windows 종료 예약됨"
        collecting = self.controller.busy and self.controller.session_phase == "REGULAR_LIVE"
        if collecting and getattr(runner, 'continue_after_clock_jump', False):
            verdict = "시계 전진 보정 기록 · 연구 무효 / 진단용 원본 수집 지속 · 65분 경과 후 종료"
        self.one_hour_status.setText(f"첫 1시간 {runner.state} · 수집 {'진행 중' if collecting else '대기/후처리/종료'} · 실제 주문 OFF\n"
            f"개장 {kst(plan.get('open'))} · 수집 종료 기준 {kst(plan.get('stop'))} · PC 종료 {shutdown}\n"
            f"{verdict}\nZIP: {archive} · {warning}")
        if active and self.controller.schedule:
            self.schedule_label.setText(self.schedule_label.text()+"\n첫 1시간 관리: 개장+65분 정상 중지. 위 Regular 공식 종가 기준 Validator는 변경하지 않습니다.")

    def open_replay(self):
        path, _ = QFileDialog.getOpenFileName(self, "Replay", "", "JSONL (*.jsonl)")
        if path:
            self.start(JsonlReplayProvider(Path(path)), fresh_directory=True)

    def stop(self, *, source='gui_stop_collection'):
        if self.one_hour and self.one_hour.active:
            self.one_hour.request_cancel(source=source)
        else:
            self.controller.stop()

    def refresh(self):
        if "history" in self.jobs:
            self._refresh_after_history = True
        offset = self._history_offset
        self.submit("history", lambda: self.master.sessions(offset=offset), self.render_history)
        self._health_at = 0

    def history_page(self, delta):
        if "history" not in self.jobs:
            self._history_offset = max(0, self._history_offset + delta)
            self.refresh()

    def render_history(self, rows):
        selected = self._selected_session.get("id") if self._selected_session else None
        self.history.blockSignals(True)
        self.history.render_rows(rows)
        self.history.blockSignals(False)
        self.page_label.setText(f"Sessions {self._history_offset + 1}–{self._history_offset + len(rows)} · 200 rows/page")
        if selected:
            for i, row in enumerate(rows):
                if row["id"] == selected:
                    self.history.selectRow(i)
                    break
        if self._refresh_after_history:
            self._refresh_after_history = False
            self.refresh()

    def session_selected(self):
        row = self.history.currentRow()
        if row < 0 or not self.history.selectedItems():
            self._selected_session = None
            return
        session = self.history.rows[row]
        self._selected_session = session
        self._selection_generation += 1
        generation = self._selection_generation
        self.submit(f"details_{generation}", lambda: self.master.details(session["id"]),
            lambda value: self.render_details(value) if generation == self._selection_generation else None)

    def render_details(self, data):
        self.candidates.render_rows(data["candidates"], automatic_columns=True)
        self.results.render_rows(data["results"], automatic_columns=True)
        quality = data["quality"]
        for row in quality:
            row["summary_json"] = json.loads(row["summary_json"])
        self.quality_text.setPlainText(json.dumps(quality, ensure_ascii=False, indent=2))
        validation = quality[0]["summary_json"].get("validation") if quality else None
        self.validation_text.setPlainText(json.dumps(validation, ensure_ascii=False, indent=2) if validation else
            "Not validated · Revalidate Session을 실행하세요. PASS는 수익성 판단이 아닙니다.")
        self.source_text.setPlainText(json.dumps(data["session"], ensure_ascii=False, indent=2))

    def select_ticker(self, table, row):
        ticker = table.rows[row].get("ticker")
        if ticker:
            self.ticker.setText(ticker)
            self.search_ticker()
            self.radar_ticker_selected.emit(ticker)

    def analyze_ticker(self):
        ticker = self.ticker.text().strip().upper()
        if ticker:
            self.radar_ticker_selected.emit(ticker)

    def search_ticker(self):
        ticker = self.ticker.text().strip().upper()
        if ticker:
            self.submit("ticker_" + ticker, lambda: self.master.ticker_history(ticker),
                lambda rows: self.ticker_table.render_rows(rows, automatic_columns=True) if self.ticker.text().strip().upper() == ticker else None)
            self.tabs.setCurrentWidget(self.ticker_tab)

    def import_existing(self):
        path = QFileDialog.getExistingDirectory(self, "Import Existing Session · read-only", self.data_root.text())
        if path:
            self.status.setText("Master DB import 중…")
            self.submit("import", lambda: import_session(path, self.master.path), self.import_done)

    def revalidate(self):
        if self.controller.busy:
            self.status.setText("수집 종료 후 Revalidate Session을 실행하세요.")
            return
        try:
            session = self.target_session()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.submit("validation", lambda: revalidate_session(session, self.master.path), self.validation_done)

    def validation_done(self, summary):
        reasons = summary.get("errors") or summary.get("warnings") or []
        text = "Session validation: " + summary["overall"]
        if reasons:
            text += " · " + reasons[0]["code"]
        self.validation_status.setText(text)
        self.refresh()

    def import_done(self, rows):
        self.status.setText(f"Master 등록/갱신 완료 · {len(rows)} session/day")
        if rows and self.controller.worker:
            sid = rows[-1]["id"]
            self.submit("current_session", lambda: self.master.details(sid), self.remember_current_session)
        self.refresh()

    def remember_current_session(self, data):
        if self.controller.worker and Path(data["session"]["source_db_path"]) == self.controller.worker.config.db_path.resolve():
            self._current_session = data["session"]

    def target_session(self):
        if self._selected_session:
            return dict(self._selected_session)
        if self._current_session and not self.controller.busy:
            return dict(self._current_session)
        worker = self.controller.worker
        if not worker:
            raise ValueError("Session History에서 세션을 선택하세요.")
        day = self._summary.get("session_date") or datetime.now(ET).date().isoformat()
        return dict(data_dir=str(worker.config.data_dir), export_path=str(worker.config.data_dir / "export" / day),
            log_path=str(worker.config.data_dir / "logs" / f"{day}.log"), session_date_et=day,
            run_id=self.controller.lifecycle["run_id"], test_version=worker.config.test_version,
            research_schema_version=4 if worker.provider.name == "kiwoom" else 1,
            max_ws_symbols=worker.config.max_ws_symbols, clean_shutdown=self.controller.lifecycle.get("clean_shutdown"))

    def review(self):
        try:
            session = self.target_session()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText("Review ZIP 생성/민감정보 검사 중…")
        self.submit("zip", lambda: create_review_zip(session), lambda path: self.status.setText(f"Review ZIP 완료: {path}"))

    def open_folder(self, key):
        try:
            session = self.target_session()
            path = Path(session["data_dir"]) / "reviews" if key == "reviews" else Path(session[key])
            if not path.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.status.setText("폴더가 아직 없거나 열 수 없습니다.")
        except (ValueError, KeyError):
            self.status.setText("Session History에서 세션을 선택하세요.")

    def render(self, rows):
        rows = [dict(row, candidate_tracking_state=self._tracking.get(row["ticker"], row.get("candidate_tracking_state"))) for row in rows]
        for table, key in zip(self.tables, ("current_leader_rank", "emerging_rank")):
            table.render_rows(live_rows(rows, key))

    def render_health(self, data):
        self._summary = data["summary"]
        self._tracking = {row["ticker"]: row.get("candidate_tracking_state") for row in data.get("rows", [])}
        if data.get("rows"):
            self.render(data["rows"])
        self.health_label.setText(f"System Health · {data['day']} ET · {datetime.now().strftime('%H:%M:%S')}")
        # Current connection is sampled separately; persisted gauges remain unchanged.

    def poll(self):
        if self.shutdown_test:
            self.shutdown_test.render()
        worker = self.controller.worker
        if worker:
            try:
                message = worker.messages.get_nowait()
                status = message["status"]
                if worker.error:
                    status = ("다른 Kiwoom collector가 실행 중입니다. CLI/UI 수집 종료 후 다시 시작하세요."
                              if "Another local Kiwoom collector" in worker.error else "Collector 오류 · 로컬 session 로그 확인")
                self.status.setText(status)
                if message["rows"] is not None:
                    self.render(message["rows"])
            except queue.Empty:
                pass
            finalizing_one_hour = self.one_hour and self.one_hour.active and self.one_hour.state in {
                "FINALIZING", "WAITING_SHUTDOWN", "SHUTDOWN_PENDING"}
            if not self._closing and not finalizing_one_hour and time.monotonic() >= self._health_at and worker.phase != "STARTING":
                self._health_at = time.monotonic() + 5
                path = worker.config.db_path
                latest = worker.phase in {"STOPPED", "ERROR"}
                self.submit("health", lambda: live_health(path, latest_session=latest),
                    lambda data: self.render_health(data) if self.controller.worker is worker else None)
        while not self.controller.events.empty():
            kind, value = self.controller.events.get_nowait()
            if kind == "imported":
                self.import_done(value)
            elif kind == "validated":
                self.validation_done(value)
            elif kind == "validation_failed":
                self.validation_status.setText(value)
            elif kind == "collector_finished" and worker and worker.error:
                pass
            else:
                self.status.setText(str(value))
        for key, (future, callback) in list(self.jobs.items()):
            if future.done():
                del self.jobs[key]
                try:
                    callback(future.result())
                except Exception as exc:
                    if key == "health":
                        self.health_label.setText("System Health · 갱신 대기/읽기 오류 · 마지막 표시값")
                    else:
                        self.status.setText(f"{key} 작업 실패 ({type(exc).__name__}) · 원본/권한/민감정보를 확인하고 재시도하세요.")
        busy = self.controller.busy or bool(self.one_hour and self.one_hour.active)
        self._render_opening_hour()
        paper_view = self.controller.paper.view() if self.controller.paper else None
        self.paper_panel.render(paper_view,
                                busy=busy, collection_mode=self.collection_mode.currentText())
        self.start_button.setEnabled(not busy)
        self.replay_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy and self.controller.state in ("STARTING", "RUNNING"))
        for widget in (self.max_ws, self.data_root, self.grace, self.collection_mode):
            widget.setEnabled(not busy)
        elapsed = time.monotonic() - self.controller.started_monotonic if busy and self.controller.started_monotonic else None
        tracking = self._summary.get("tracking", {})
        config = worker.config if worker else (self.config or CONFIG)
        config = self.controller.config if not worker and self.controller.config else config
        values = health_rows(self._summary, config.max_ws_symbols, state=self.controller.state, connection=self.controller.connection)
        self.health.render_rows(values)
        current = {r["metric"]: r["value"] for r in values}
        self.run_status.setText(f"{self.controller.state} · {config.test_version} · {datetime.now(ET).date()} ET · "
            f"elapsed {display(elapsed)} s · Max WS {config.max_ws_symbols} · Current Active WS {display(current['Current Active WS'])} · "
            f"Current Connected {display(current['Current Connected'])} · "
            f"Last Active {display(tracking.get('active_subscription_count'))} · Last Saturation {display(tracking.get('slot_saturation_pct'))}% · {config.data_dir}")
        cache_path = config.cache_path if self.controller.config or config.historical_cache_path else DEFAULT_SHARED_CACHE_PATH
        replay = worker is not None and worker.provider.mode == "replay"
        self.cache_label.setText("Historical Cache: not used (REPLAY)" if replay else f"Historical Cache: {cache_path.parent.name}/{cache_path.name} · 전체 경로: tooltip")
        self.cache_label.setToolTip("" if replay else str(cache_path.resolve()))
        schedule = self.controller.schedule
        if schedule:
            p = schedule.snapshot()
            target = p["scheduled_stop"] if p["actual_start"] else p["scheduled_start"]
            remaining = max(0, (instant(target) - schedule.clock.now()).total_seconds()) if target else None
            countdown = str(timedelta(seconds=int(remaining))) if remaining is not None else "-"
            protocol_status = ("COMPLIANT" if p["protocol_compliant"] else
                               "IN PROGRESS" if p["protocol_reason"] == "in_progress" else "NON-COMPLIANT")
            self.schedule_label.setText(f"{p['collection_mode']} · Session {self.controller.session_phase} · Collector {self.controller.state} · "
                f"XNYS {p['session_date_et']} · Open {p['scheduled_open_et']} · Close {p['scheduled_close_et']} · Countdown {countdown}\n"
                f"Scheduled UTC {p['scheduled_start']} → {p['scheduled_stop']} · Actual {p['actual_start']} → {p['actual_stop']} · "
                f"Start delay {display(p['start_delay_ms'])} ms · Protocol {protocol_status} ({p['protocol_reason']})")
        if self.controller.state == "STOPPED" and self.controller.lifecycle.get("clean_shutdown"):
            self.shutdown_label.setText(f"Stopped cleanly · shutdown {display(self.controller.lifecycle.get('shutdown_elapsed_ms'))} ms (final CSV/첫 quality export까지)")
        self.render_one_hour(busy)
        if self.shutdown_test and self.shutdown_test.active:
            for widget in (self.start_button, self.replay_button, self.one_hour_enable,
                           *self.mutation_buttons):
                widget.setEnabled(False)
        self.collection_test.render(paper_view, busy=busy)
        if self._closing and not busy and not self.jobs:
            self._closing = False
            self.close()

    def closeEvent(self, event):
        if self.shutdown_test and not self.shutdown_test.prepare_close():
            event.ignore()
            return
        self.stop(source='gui_close')
        self.settings.setValue("max_ws", self._one_hour_preferences[1] if self._one_hour_preferences else self.max_ws.value())
        self.settings.setValue("data_root", self.data_root.text())
        self.settings.setValue("geometry", self.saveGeometry())
        for i, table in enumerate([*self.tables, self.history]):
            key = f"columns_{i}" if i < 2 else "history_columns_v2_3_0"
            self.settings.setValue(key, table.horizontalHeader().saveState())
        if self.controller.busy or (self.one_hour and self.one_hour.active):
            self._closing = True
            self.status.setText("STOPPING · 정상 종료/export/Master 작업 완료 후 닫습니다.")
            event.ignore()
            return
        self.timer.stop()
        self.closed = True
        self.shutdown_test.dispose()
        self.executor.shutdown(wait=False)
        super().closeEvent(event)


def show_radar(owner):
    panel = getattr(owner, "_market_radar_panel", None)
    if panel is None or panel.closed:
        if panel is not None:
            panel.deleteLater()
        panel = RadarPanel()
        if hasattr(owner, "_on_radar_ticker_selected"):
            panel.radar_ticker_selected.connect(owner._on_radar_ticker_selected)
        owner._market_radar_panel = panel
    panel.show()
    panel.raise_()
    panel.activateWindow()
