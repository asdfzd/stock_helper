"""Photograph-friendly daily launch screen; no OCR/GPU or account imports."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import __version__
from .config import CONFIG
from .family_mode import FamilyRunner
from .models import ET, instant, utc
from .ui import RadarPanel
from .audit_runtime import AuditSession
from .family_mode import executable_command


class FamilyDashboard(RadarPanel):
    """The full manual dashboard remains available through --manual."""
    def start(self, *args, **kwargs):
        pass  # This daily instance can only arm once, through FamilyWindow.

    def poll(self):
        super().poll()
        self.start_button.setEnabled(False)
        self.replay_button.setEnabled(False)
        self.collection_test.start.setEnabled(False)
        for widget in self.mutation_buttons:
            widget.setEnabled(False)


class FamilyWindow(QWidget):
    def __init__(self, config, *, runner_factory=FamilyRunner, audit_factory=AuditSession):
        super().__init__()
        self.config = config
        self.runner_factory = runner_factory
        self.runner = None
        self.failure = None
        self.arm_attempted = False
        self.closing = False
        self.arm_waits = 0
        day = datetime.now(timezone.utc).astimezone(ET).date().isoformat()
        self.root = config.data_root / 'market_radar_family' / day / ('attempt_' + uuid.uuid4().hex)
        self.root.mkdir(parents=True, exist_ok=False)
        try:
            self.audit = audit_factory(self.root, command=executable_command(), recover_base=config.data_root/'market_radar_family')
        except Exception:
            self.audit = None
        settings = QSettings(str(self.root / 'family_ui.ini'), QSettings.Format.IniFormat)
        self.dashboard = FamilyDashboard(config=replace(CONFIG, data_dir=self.root,
            historical_cache_path=config.cache, collection_mode='Regular Session', max_ws_symbols=150),
            master_path=self.root / 'master.db', settings=settings)
        self.dashboard.one_hour_enable.setChecked(True)
        self.dashboard.one_hour_no_shutdown.setChecked(not config.normal_shutdown)
        self.setWindowTitle(f'Market Radar 자동 수집 · v{__version__}')
        self.resize(1020, 780)
        self.setStyleSheet('QLabel { font-size:14px; } QPushButton { font-size:14px; min-height:32px; }')
        layout = QVBoxLayout(self)
        self.header = self.label(layout, f'Market Radar v{__version__} · 가족용 자동 수집 · 실제 주문 OFF')
        self.header.setStyleSheet('font-size:22px;font-weight:600')
        self.schedule = self.label(layout, '오늘 미국장 확인 중 · 첫 65분 · Max WS 150 · Paper / Rotation / Opening ON')
        self.phase = self.label(layout, '자동 예약 준비 중…')
        self.phase.setStyleSheet('font-size:24px;font-weight:600')
        self.detail = self.label(layout, '')
        self.paper = self.label(layout, '')
        self.result = self.label(layout, '')
        self.power = self.label(layout, '')
        self.audit_label = self.label(layout, '자동 진단 준비 중 · 외부 전송 OFF · AI 분석 OFF')
        self.warning = self.label(layout, '예약 완료는 인증·수집 성공이 아닙니다. 실제 Kiwoom 연결은 아래 상태에서 확인하세요.\n'
            '비상 종료 OFF이면 오류·멈춤 때 PC가 계속 켜져 있을 수 있습니다.\n'
            '이 창을 닫으면 수집과 종료 예약을 취소합니다. 절전·로그아웃·업데이트는 수집을 중단시킬 수 있습니다.')
        self.warning.setStyleSheet('color:#a04813')
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton('PC 종료만 취소')
        self.cancel_button.clicked.connect(self.cancel_power)
        buttons.addWidget(self.cancel_button)
        self.stop_button = QPushButton('무인 모드 / 수집 중지')
        self.stop_button.clicked.connect(self.stop)
        buttons.addWidget(self.stop_button)
        details = QPushButton('전체 상태 / 진단 보기')
        details.clicked.connect(self.dashboard.show)
        buttons.addWidget(details)
        layout.addLayout(buttons)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)
        QTimer.singleShot(0, self.arm)
        QApplication.instance().aboutToQuit.connect(self.stop)

    @staticmethod
    def label(layout, text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        return label

    def arm(self):
        if self.arm_attempted or self.closing:
            return
        if self.dashboard.jobs:
            self.arm_waits += 1
            if self.arm_waits < 120:
                QTimer.singleShot(250, self.arm)
                return
            self.failure = 'startup_history_not_ready'
            if self.audit: self.audit.failure(self.failure)
            self.arm_attempted = True
            return
        self.arm_attempted = True
        try:
            self.runner = self.runner_factory(self.dashboard.controller, self.root, self.config,
                ready_to_shutdown=lambda: not self.dashboard.jobs, audit=self.audit)
            self.dashboard.one_hour = self.runner
            self.runner.launch(cache=self.config.cache)
        except Exception as exc:
            self.failure = 'family_launch_failed:' + type(exc).__name__
            if self.audit: self.audit.failure('family_launch_failed')
        self.refresh()

    def refresh(self):
        r = self.runner
        audit = getattr(r, 'audit', None) or self.audit
        if audit:
            verdict = (audit.result or {}).get('severity', 'UNKNOWN')
            self.audit_label.setText(f'자동 진단: {verdict} · 저장: 이번 실행/audit/postrun_audit.txt · 외부 전송 OFF · AI OFF'
                + (' · 진단 저장/등록 오류' if audit.error else ''))
        else:
            self.audit_label.setText('자동 진단 시작 실패 · 수집/전원 정책은 기존 조건을 따릅니다')
        if r:
            r.gui_heartbeat = utc(datetime.now(timezone.utc))
            r.refresh_watchdog()
            plan = r.plan or {}
            if plan:
                local = lambda key: instant(plan[key]).astimezone(ZoneInfo('Asia/Seoul')).strftime('%m/%d %H:%M:%S')
                self.schedule.setText(f"미국장 {plan['session_date']} · 수집 예정 {local('open')} ~ {local('stop')} KST\n"
                    f"정규장 개장 {local('open')} / 폐장 {local('official_close')} KST\n"
                    '첫 65분 · Max WS 150 · Paper v1 / Rotation v2 / Opening ON · 실제 주문 OFF')
            self.phase.setText(f'{family_phase(r, self.dashboard.controller.session_phase, self.failure)}\n'
                f'{r.state} · {self.dashboard.controller.session_phase} · {self.failure or r.reason or "자동 운용 중"}')
            self.detail.setText(self.dashboard.collection_test.phase.text())
            self.paper.setText(self.dashboard.collection_test.paper_status.text())
            self.result.setText(self.dashboard.collection_test.result.text())
            wd = r.watchdog.view if r.watchdog else {'status': 'NOT_REGISTERED'}
            due = instant(wd['deadline']).astimezone(ZoneInfo('Asia/Seoul')).strftime('%m/%d %H:%M:%S KST') if wd.get('deadline') else '미확인'
            earliest = ((instant(plan['open']) + timedelta(minutes=71)).astimezone(ZoneInfo('Asia/Seoul')).strftime('%m/%d %H:%M:%S KST')
                if plan else '미확인')
            self.power.setText(f"정상 자동 종료: {'허용 (개장+70분 이후 보존 검증 필수)' if not r.no_shutdown else 'OFF / 취소됨'}\n"
                f"PC 종료 가능 가장 이른 시각: {earliest} · 후처리/시계 확인에 따라 늦어질 수 있음\n"
                f"독립 90분 감시: {wd['status']} · 예정 {due} · 실제 비상 종료 {'허용' if self.config.emergency_shutdown else 'OFF (dry-run)'}\n"
                f"감시 오류: {r.watchdog_error or wd.get('error') or '없음'}")
        elif self.failure:
            self.phase.setText('오류 · 예약 실패 · ' + self.failure)
        if self.closing and (not r or not r.active) and not self.dashboard.controller.busy:
            future = getattr(r, 'cancel_future', None)
            if future is None or future.done():
                if r and r.watchdog_error:
                    self.closing = False
                    self.failure = r.watchdog_error
                else:
                    self.close()

    def cancel_power(self):
        if self.runner:
            self.runner.request_cancel_shutdown()
        self.refresh()

    def stop(self):
        self.closing = True
        if self.runner:
            self.runner.request_cancel(source='family_window_stop')

    def closeEvent(self, event):
        r = self.runner
        if not self.closing:
            self.stop()
            event.ignore()
            return
        if (r and (r.active or (r.cancel_future and not r.cancel_future.done()) or r.watchdog_error)) or self.dashboard.controller.busy:
            event.ignore()
            return
        if not self.dashboard.close():
            event.ignore()
            return
        self.timer.stop()
        QApplication.instance().aboutToQuit.disconnect(self.stop)
        if r:
            r.dispose()
        elif self.audit:
            self.audit.close()
        event.accept()


def family_phase(runner, controller_phase, failure=None):
    """Human status without claiming authentication or data-quality success."""
    watchdog_state = runner.watchdog.view.get('status') if runner.watchdog else None
    if failure or runner.watchdog_error or runner.state in {'FAILED', 'ERROR'} or watchdog_state in {
            'STATE_UNKNOWN', 'INVOCATION_FAILED_OR_UNKNOWN', 'REGISTRATION_FAILED',
            'NORMAL_REQUEST_FAILED', 'EMERGENCY_REQUEST_FAILED_OR_UNKNOWN', 'EMERGENCY_BLOCKED'}:
        return '오류 · 화면을 사진으로 보내 주세요'
    if runner.state in {'SHUTDOWN_PENDING', 'WAITING_SHUTDOWN'} or runner.shutdown_may_be_pending:
        return '종료 대기'
    if controller_phase == 'FINALIZING' or runner.state == 'FINALIZING':
        return '후처리 중'
    if controller_phase == 'REGULAR_LIVE':
        return '수집 중'
    if runner.state == 'ARMED' and runner.watchdog and runner.watchdog.view.get('status') == 'ARMED':
        return '예약 완료 · PC와 이 창을 켜 두세요'
    if runner.finalized:
        return '수집 종료 · PC 종료 OFF / 취소 / 차단 상태를 확인하세요'
    return '자동 예약 준비 중'
