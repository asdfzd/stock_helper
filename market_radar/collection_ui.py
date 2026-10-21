"""Small view of the existing Dashboard. No collector, timer, DB or settings store."""
from datetime import timedelta
import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSignalBlocker
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QTextEdit, QVBoxLayout, QWidget)

from .dashboard_widgets import display
from .models import instant


def duration(seconds):
    return str(timedelta(seconds=max(0, int(seconds)))) if seconds is not None else "미확인"


def read_diagnostics(paths):
    """On-demand bounded JSON read; never open SQLite or arbitrary raw log files."""
    from .failure_evidence import safe_path
    # These are existing structured operational files, not provider payloads.
    allowed = {
        'runtime.json': ('diagnostic_schema', 'first_error', 'events', 'persistence_errors', 'collection_clock'),
        'collection.json': ('diagnostic_schema', 'first_error', 'events', 'persistence_errors', 'collection_clock'),
        'one_hour_run_summary.json': ('status', 'research_status', 'preservation_status', 'shutdown_eligible',
            'operational_failures', 'research_failures', 'shutdown_blockers', 'archive_exceptions',
            'failures', 'collection_clock', 'reason'),
        'shutdown_status.json': ('state', 'reason', 'requested_at', 'shutdown_at', 'no_shutdown',
            'shutdown_may_be_pending', 'status', 'at', 'delay_seconds', 'research_status',
            'preservation_status', 'shutdown_eligible', 'error', 'not_before', 'warning'),
        'paper_performance.json': ('first_error', 'first_failure_events', 'maximum_queue_length', 'write_error'),
        'opening_hour_diagnostics.json': ('status', 'phase', 'error', 'exception', 'persistence_exception'),
        'dashboard_run.json': ('state', 'clean_shutdown', 'collection_protocol', 'error', 'stopped_at'),
        'validation_summary.json': ('overall', 'errors', 'warnings'),
    }
    def clean(value):
        if isinstance(value, dict): return {safe_path(k): clean(v) for k, v in value.items()}
        if isinstance(value, list): return [clean(v) for v in value]
        if isinstance(value, str):
            # Preserve valid ISO timestamps; other strings use existing redaction.
            if len(value) <= 40 and 'T' in value:
                try:
                    instant(value)
                    return value
                except (ValueError, TypeError): pass
            return safe_path(value)
        return value
    result = []
    for path in paths:
        path = Path(path)
        if path.name not in allowed: continue
        try:
            with path.open('rb') as stream:
                raw = stream.read(2_000_001)
            if len(raw) > 2_000_000: raise ValueError('diagnostic_too_large')
            data = json.loads(raw)
            if not isinstance(data, dict): raise ValueError('diagnostic_not_object')
            result.append(dict(file=path.name, state='READ', details=clean(
                {key: data[key] for key in allowed[path.name] if key in data})))
        except FileNotFoundError:
            result.append(dict(file=path.name, state='NOT_CREATED'))
        except (OSError, ValueError, RecursionError) as exc:
            result.append(dict(file=path.name, state='UNAVAILABLE', error=type(exc).__name__))
    return result


class CollectionTestPanel(QWidget):
    def __init__(self, dashboard):
        super().__init__(dashboard)
        self.dashboard = dashboard
        layout = QVBoxLayout(self)
        title = QLabel('수집 테스트 · 실제 주문 OFF')
        title.setStyleSheet('font-size: 18px; font-weight: bold')
        layout.addWidget(title)
        actions = QHBoxLayout()
        self.start = self.button(actions, '수집 시작', lambda: dashboard.start())
        self.start.setMinimumHeight(36)
        self.stop = self.button(actions, '수집 중지', dashboard.stop)
        self.stop.setToolTip('수집 예약 또는 진행 중인 수집을 취소하고 안전하게 정리합니다.')
        self.cancel_shutdown = self.button(actions, 'PC 종료만 취소', dashboard.cancel_one_hour)
        self.cancel_shutdown.setToolTip('수집과 후처리는 계속하며 PC 자동 종료만 취소합니다.')
        self.folder = self.button(actions, '결과 폴더 열기', self.open_result)
        self.diagnostics_button = self.button(actions, '진단 결과 확인', self.show_diagnostics)
        self.zip_folder = self.button(actions, 'ZIP 폴더 열기', self.open_zip)
        layout.addLayout(actions)

        settings = QGroupBox('수집 설정')
        grid = QGridLayout(settings)
        self.collection_mode = QComboBox()
        self.collection_mode.addItems(['첫 1시간 (65분)', '일반 Regular Session', '기존 상세 설정 (Manual / Capacity)'])
        self.collection_mode.activated.connect(self.select_mode)
        self.max_ws = QSpinBox()
        self.max_ws.setRange(dashboard.max_ws.minimum(), dashboard.max_ws.maximum())
        self.max_ws.valueChanged.connect(dashboard.max_ws.setValue)
        grid.addWidget(QLabel('Collection Mode'), 0, 0)
        grid.addWidget(self.collection_mode, 0, 1, 1, 3)
        grid.addWidget(QLabel('Max WS'), 0, 4)
        grid.addWidget(self.max_ws, 0, 5)
        self.paper = self.checkbox('Paper v1', dashboard.paper_panel.enable)
        self.rotation = self.checkbox('Rotation v2', dashboard.paper_panel.rotation_enable)
        self.opening = self.checkbox('Opening Hour Research', dashboard.opening_hour_enable)
        self.no_shutdown = self.checkbox('PC 자동 종료 없이 테스트', dashboard.one_hour_no_shutdown)
        for col, widget in enumerate((self.paper, self.rotation, self.opening)):
            grid.addWidget(widget, 1, col*2, 1, 2)
        grid.addWidget(self.no_shutdown, 2, 0, 1, 4)
        grid.addWidget(QLabel('실제 주문 OFF'), 2, 4, 1, 2)
        layout.addWidget(settings)
        self.phase = self.label_group(layout, '실시간 수집 상태')
        self.paper_status = self.label_group(layout, 'Paper / Rotation')
        self.result = self.label_group(layout, '오류 · 결과')
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        layout.addStretch()
        # Mirrored controls write to the original widgets. No second config exists.
        for source in (dashboard.paper_panel.enable, dashboard.paper_panel.rotation_enable,
                       dashboard.opening_hour_enable, dashboard.one_hour_no_shutdown,
                       dashboard.one_hour_enable):
            source.toggled.connect(self.sync_settings)
        dashboard.max_ws.valueChanged.connect(self.sync_settings)
        dashboard.collection_mode.currentTextChanged.connect(self.sync_settings)
        self.sync_settings()

    @staticmethod
    def button(layout, text, action):
        button = QPushButton(text)
        button.clicked.connect(action)
        layout.addWidget(button)
        return button

    @staticmethod
    def label_group(layout, title):
        group = QGroupBox(title)
        inner = QVBoxLayout(group)
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        inner.addWidget(label)
        layout.addWidget(group)
        return label

    @staticmethod
    def checkbox(text, source):
        widget = QCheckBox(text)
        widget.toggled.connect(source.setChecked)
        return widget

    def select_mode(self, index):
        d = self.dashboard
        if d.controller.busy or (d.one_hour and d.one_hour.active):
            self.sync_settings()
            return
        if index == 0:
            d.one_hour_enable.setChecked(True)
        elif index == 1:
            d.one_hour_enable.setChecked(False)
            d.collection_mode.setCurrentText('Regular Session')
        else:
            self.sync_settings()
            d.tabs.setCurrentIndex(d.tabs.count()-1)
        d.poll()

    def sync_settings(self, *_):
        d = self.dashboard
        index = 0 if d.one_hour_enable.isChecked() else 1 if d.collection_mode.currentText() == 'Regular Session' else 2
        with QSignalBlocker(self.collection_mode): self.collection_mode.setCurrentIndex(index)
        with QSignalBlocker(self.max_ws): self.max_ws.setValue(d.max_ws.value())
        pairs = ((self.paper, d.paper_panel.enable), (self.rotation, d.paper_panel.rotation_enable),
                 (self.opening, d.opening_hour_enable), (self.no_shutdown, d.one_hour_no_shutdown))
        for widget, source in pairs:
            with QSignalBlocker(widget): widget.setChecked(source.isChecked())
            widget.setEnabled(source.isEnabled())
        self.max_ws.setEnabled(d.max_ws.isEnabled())
        self.collection_mode.setEnabled(d.one_hour_enable.isEnabled())

    def render(self, paper_view, *, busy):
        d, c = self.dashboard, self.dashboard.controller
        self.sync_settings()
        self.start.setEnabled(d.start_button.isEnabled())
        # A pre-start supervisor can be active while the Controller is STOPPED.
        self.stop.setEnabled(bool((d.one_hour and d.one_hour.active) or d.stop_button.isEnabled()))
        self.cancel_shutdown.setEnabled(d.one_hour_cancel.isEnabled())
        r = d.one_hour
        schedule = c.schedule
        protocol = schedule.snapshot() if schedule else {}
        plan = (r.plan or {}) if r else {}
        begin = plan.get('open') or protocol.get('scheduled_start')
        end = plan.get('stop') or protocol.get('scheduled_stop')
        elapsed = remaining = None
        if schedule and busy and end:
            remaining = max(0, (instant(end)-schedule.clock.now()).total_seconds())
        if schedule and schedule.started_mono is not None:
            clock = r.clock if r else schedule.clock
            frozen = (getattr(r, 'collection_clock', {}) or {}).get('stopped_monotonic') if r else None
            if frozen is not None: elapsed = frozen - schedule.started_mono
            elif protocol.get('actual_stop') and protocol.get('actual_start'):
                elapsed = (instant(protocol['actual_stop'])-instant(protocol['actual_start'])).total_seconds()
            elif busy: elapsed = clock.monotonic() - schedule.started_mono
            if busy and end:
                remaining = max(0, (instant(end)-clock.now()).total_seconds())
                if r:
                    remaining = max(remaining, schedule.started_mono+3900-clock.monotonic())
        connection = c.connection or {}
        connected = connection.get('connected')
        link = '연결' if connected is True else '미연결' if connected is False else '미확인'
        phase = c.session_phase
        supervisor = r.state if r else '사용 안 함'
        config = c.config
        maximum = config.max_ws_symbols if config else d.max_ws.value()
        self.phase.setText(f'현재 단계: {phase} · Collector {c.state} · 첫 1시간 {supervisor}\n'
            f'Kiwoom: {link} · WS {display(connection.get("active_ws"))} / {maximum} · '
            f'Snapshot {display(d._summary.get("snapshot_count"))} · 후보 {display(d._summary.get("candidate_count"))} (최근 집계)\n'
            f'수집 경과 {duration(elapsed)} · 종료까지 {duration(remaining)}\n'
            f'예정 UTC: {begin or "미확인"} → {end or "미확인"}')
        state = paper_view or {}
        rotation = state.get('rotation_v2') or {}
        def status(view, enabled):
            if view.get('status'): return view['status']
            return ('미확인' if busy else 'WAITING') if enabled else 'OFF'
        journal = getattr(r, 'diagnostics', None)
        diagnostic = journal.snapshot() if journal else {}
        first = diagnostic.get('first_error') or {}
        first_at = (first.get('first_engine_error') or {}).get('occurred_at_utc') or first.get('wall_utc')
        failures = list(getattr(r, 'failures', []) or [])
        code = failures[-1] if failures else (first.get('code') or '미확인 / 기록 없음')
        error = bool(getattr(c.paper, 'error', None) or getattr(c.paper, 'sidecar_error', None)
                     or 'ERROR' in str(state.get('status', '')) or 'ERROR' in str(rotation.get('status', '')))
        paper_failure = first if first.get('code') == 'paper_processing_delay' else next(
            (event for event in diagnostic.get('events', []) if event.get('code') == 'paper_processing_delay'), {})
        event = paper_failure.get('first_failure_event') or {}
        delay = ('paper_processing_delay · queue wait ' + display(event.get('queue_wait_ms')) + ' ms'
                 if paper_failure else '지연 코드 미확인 · 진단 결과 확인')
        self.paper_status.setText(f'Paper v1: {status(state, d.paper_panel.enable.isChecked())} · '
            f'Rotation v2: {status(rotation, d.paper_panel.rotation_enable.isChecked())}\n'
            f'오류: {"있음" if error else "공개 상태에 오류 없음"} · {delay}')
        summary = (r.summary or {}) if r else {}
        preservation = summary.get('preservation_status', '미확인')
        archive = Path(r.archive).name if r and r.archive else '아직 생성되지 않음'
        diagnostic_state = '쓰기 실패' if diagnostic.get('persistence_errors') else '기록 요청됨 (파일 확인은 진단 버튼)' if journal else '미생성 / 이 모드에서 비활성'
        shutdown = ('OFF · 테스트' if r.no_shutdown else '조건부 ON') if r else 'OFF · 첫 1시간 실행 전'
        if r and r.finalized and not r.shutdown_may_be_pending and r.state != 'COMPLETE_NO_SHUTDOWN': shutdown = '차단 / 취소'
        if r and r.shutdown_may_be_pending: shutdown = '예약됨 · PC 종료만 취소 가능'
        self.result.setText(f'최근 오류: {code} · 최초 {first_at or "미확인 / 기록 없음"}\n'
            f'품질: {d.validation_status.text() or "미확인"} · 연구 {summary.get("research_status", "미확인")}\n'
            f'진단: {diagnostic_state} · ZIP 보존 검증 (기록상): {preservation}\n'
            f'ZIP: {archive}\nPC 종료: {shutdown}')
        message = d.status.text()
        if r and r.reason == 'late_launch': message = 'late_launch · 개장 전 예약이 필요합니다. 일반 Regular Session은 직접 선택하세요.'
        if r and getattr(r, 'continue_after_clock_jump', False) and c.session_phase == 'REGULAR_LIVE':
            message = 'clock_jump · 연구 FAILED / 진단용 원본 수집 지속 · monotonic 65분 기준'
        if (c.state == 'STOPPED' and phase == 'REGULAR_LIVE') or (r and r.finalized and c.busy):
            message = '상태 불일치 / 미확인 · Collector와 실행기 상태를 수집 상세 설정에서 확인하세요.'
        self.notice.setText(message)

    def result_path(self):
        d = self.dashboard
        if d.one_hour and d.one_hour.folder: return Path(d.one_hour.folder)
        return Path(d.target_session()['data_dir'])

    def open_result(self):
        try: self.open_directory(self.result_path())
        except (ValueError, KeyError): self.notice.setText('결과 없음 · 수집하거나 Session History에서 세션을 선택하세요.')

    def open_zip(self):
        d = self.dashboard
        if d.one_hour and d.one_hour.archive: self.open_directory(Path(d.one_hour.archive).parent)
        else:
            try: self.open_directory(self.result_path()/'reviews')
            except (ValueError, KeyError): self.notice.setText('ZIP 폴더 미확인')

    def open_directory(self, path):
        if not path.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.notice.setText('폴더가 아직 없거나 열 수 없습니다.')

    def diagnostic_paths(self):
        root = self.result_path()
        d = self.dashboard
        paths = [root/'diagnostics/runtime.json', root/'diagnostics/collection.json',
                 root/'one_hour_run_summary.json', root/'shutdown_status.json',
                 root/'paper_v1/paper_performance.json'] if d.one_hour and d.one_hour.folder else []
        if d.one_hour and d.one_hour.folder and d.controller.worker:
            source = d.controller.worker.config.data_dir
            paths += [source/'dashboard_run.json', source/'opening_hour_diagnostics.json']
        else:
            paths += [root/'dashboard_run.json', root/'opening_hour_diagnostics.json']
        return paths

    def show_diagnostics(self):
        try: paths = self.diagnostic_paths()
        except (ValueError, KeyError):
            self.notice.setText('진단 결과 없음 · 수집하거나 Session History에서 세션을 선택하세요.')
            return
        self.notice.setText('기존 진단 파일 읽는 중…')
        archive = self.dashboard.one_hour.archive if self.dashboard.one_hour else None
        def read():
            records = read_diagnostics(paths)
            if archive:
                try: present = Path(archive).is_file()
                except OSError: present = None
                records.append(dict(file=Path(archive).name, state='EXISTS' if present else 'MISSING' if present is False else 'UNAVAILABLE',
                                    verification='보존 당시 판정은 요약 참조 · 이 버튼은 ZIP 재검증을 실행하지 않음'))
            return records
        self.dashboard.submit('collection_diagnostics', read, self.display_diagnostics)

    def display_diagnostics(self, records):
        dialog = QDialog(self)
        dialog.setWindowTitle('기존 진단 기록 · 읽기 전용')
        dialog.resize(850, 580)
        layout = QVBoxLayout(dialog)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setPlainText(json.dumps(records, ensure_ascii=False, indent=2))
        layout.addWidget(text)
        self.diagnostic_dialog = dialog
        dialog.show()
        self.notice.setText('진단 파일 확인 완료 · NOT_CREATED / UNAVAILABLE는 생성·읽기 확인 실패입니다.')
