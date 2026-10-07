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
    QLabel, QLineEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget, QComboBox)

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
        layout = QVBoxLayout(self)
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
        self.collection_mode.setCurrentText(base.collection_mode)
        controls.addWidget(QLabel("Collection Mode"))
        controls.addWidget(self.collection_mode)
        self.max_ws = QSpinBox()
        self.max_ws.setRange(1, 200)
        self.max_ws.setValue(base.max_ws_symbols if config else int(self.settings.value("max_ws", base.max_ws_symbols)))
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
        buttons = QHBoxLayout()
        self.replay_button = self.add_button(buttons, "Open Replay", self.open_replay)
        for label, action in (("Import Existing Session", self.import_existing), ("Refresh", self.refresh),
                ("Create Review ZIP", self.review), ("Open Session Folder", lambda: self.open_folder("data_dir")),
                ("Open Export Folder", lambda: self.open_folder("export_path")),
                ("Open Review ZIP Folder", lambda: self.open_folder("reviews"))):
            self.add_button(buttons, label, action)
        layout.addLayout(buttons)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
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
        self.add_button(paging, "Revalidate Session", self.revalidate)
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
        layout.addWidget(QLabel("- = unavailable · 더블클릭: 종목 선택 · 새 수집은 data root 아래 별도 폴더 · 폴더/ZIP은 선택 세션 기준"))
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
        if key not in self.jobs and not self.closed:
            self.jobs[key] = (self.executor.submit(function), callback)

    def browse_root(self):
        path = QFileDialog.getExistingDirectory(self, "Data root", self.data_root.text())
        if path:
            self.data_root.setText(path)

    def start(self, provider=None, *, fresh_directory=None):
        if self.controller.busy:
            return
        try:
            base = self.config or CONFIG
            config = base if provider and fresh_directory is None else replace(base, max_ws_symbols=self.max_ws.value(),
                data_dir=Path(self.data_root.text()).expanduser().resolve(), result_pre_target_grace_seconds=self.grace.value(),
                collection_mode=self.collection_mode.currentText() if provider is None else "Manual")
            self.controller.start(config, provider, fresh_directory=(provider is None if fresh_directory is None else fresh_directory))
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

    def open_replay(self):
        path, _ = QFileDialog.getOpenFileName(self, "Replay", "", "JSONL (*.jsonl)")
        if path:
            self.start(JsonlReplayProvider(Path(path)), fresh_directory=True)

    def stop(self):
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
            self.tabs.setCurrentIndex(2)

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
            if not self._closing and time.monotonic() >= self._health_at and worker.phase != "STARTING":
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
        busy = self.controller.busy
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
        if self._closing and not busy and not self.jobs:
            self._closing = False
            self.close()

    def closeEvent(self, event):
        self.stop()
        self.settings.setValue("max_ws", self.max_ws.value())
        self.settings.setValue("data_root", self.data_root.text())
        self.settings.setValue("geometry", self.saveGeometry())
        for i, table in enumerate([*self.tables, self.history]):
            key = f"columns_{i}" if i < 2 else "history_columns_v2_3_0"
            self.settings.setValue(key, table.horizontalHeader().saveState())
        if self.controller.busy:
            self._closing = True
            self.status.setText("STOPPING · 정상 종료/export/Master 작업 완료 후 닫습니다.")
            event.ignore()
            return
        self.timer.stop()
        self.closed = True
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
