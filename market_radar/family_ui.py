"""Photograph-friendly daily launch screen; no OCR/GPU or account imports."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import uuid

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import __version__
from .config import CONFIG
from .family_mode import FamilyRunner
from .models import ET, instant, utc
from .ui import RadarPanel


class FamilyDashboard(RadarPanel):
    """The full manual dashboard remains available in an ordinary EXE launch."""
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
    def __init__(self, config, *, runner_factory=FamilyRunner):
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
        settings = QSettings(str(self.root / 'family_ui.ini'), QSettings.Format.IniFormat)
        self.dashboard = FamilyDashboard(config=replace(CONFIG, data_dir=self.root,
            historical_cache_path=config.cache, collection_mode='Regular Session', max_ws_symbols=150),
            master_path=self.root / 'master.db', settings=settings)
        self.dashboard.one_hour_enable.setChecked(True)
        self.dashboard.one_hour_no_shutdown.setChecked(not config.normal_shutdown)
        self.setWindowTitle(f'Market Radar 자동 수집 · v{__version__}')
        self.resize(1020, 730)
        self.setStyleSheet('QLabel { font-size:14px; } QPushButton { font-size:14px; min-height:32px; }')
        layout = QVBoxLayout(self)
        self.header = self.label(layout, f'Market Radar v{__version__} · 가족용 자동 수집 · 실제 주문 OFF')
        self.header.setStyleSheet('font-size:22px;font-weight:600')
        self.schedule = self.label(layout, '오늘 미국장 확인 중 · 첫 65분 · Max WS 150 · Paper / Rotation / Opening ON')
        self.phase = self.label(layout, '자동 예약 준비 중…')
        self.phase.setStyleSheet('font-size:18px;font-weight:600')
        self.detail = self.label(layout, '')
        self.paper = self.label(layout, '')
        self.result = self.label(layout, '')
        self.power = self.label(layout, '')
        self.warning = self.label(layout, '90분 비상 강제 종료는 실행 중인 DB/ZIP과 다른 앱의 저장 전 작업을 손상시킬 수 있습니다.\n'
            'dry-run은 종료하지 않습니다. Windows 자체 정지·정전·로그아웃 후 작동은 보장하지 않습니다.')
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
            self.arm_attempted = True
            return
        self.arm_attempted = True
        try:
            self.runner = self.runner_factory(self.dashboard.controller, self.root, self.config,
                ready_to_shutdown=lambda: not self.dashboard.jobs)
            self.dashboard.one_hour = self.runner
            self.runner.launch(cache=self.config.cache)
        except Exception as exc:
            self.failure = 'family_launch_failed:' + type(exc).__name__
        self.refresh()

    def refresh(self):
        r = self.runner
        if r:
            r.gui_heartbeat = utc(datetime.now(timezone.utc))
            r.refresh_watchdog()
            plan = r.plan or {}
            if plan:
                local = lambda key: instant(plan[key]).astimezone().strftime('%m/%d %H:%M:%S')
                self.schedule.setText(f"미국장 {plan['session_date']} · 예정 수집 {local('open')} ~ {local('stop')} (PC 현지 시각)\n"
                    '첫 65분 · Max WS 150 · Paper v1 / Rotation v2 / Opening ON · 실제 주문 OFF')
            self.phase.setText(f'{r.state} · {self.dashboard.controller.session_phase} · {self.failure or r.reason or "자동 운용 중"}')
            self.detail.setText(self.dashboard.collection_test.phase.text())
            self.paper.setText(self.dashboard.collection_test.paper_status.text())
            self.result.setText(self.dashboard.collection_test.result.text())
            wd = r.watchdog.view if r.watchdog else {'status': 'NOT_REGISTERED'}
            due = instant(wd['deadline']).astimezone().strftime('%m/%d %H:%M:%S') if wd.get('deadline') else '미확인'
            self.power.setText(f"정상 자동 종료: {'허용 (개장+70분 이후 보존 검증 필수)' if not r.no_shutdown else 'OFF / 취소됨'}\n"
                f"독립 90분 감시: {wd['status']} · 예정 {due} · 실제 비상 종료 {'허용' if self.config.emergency_shutdown else 'OFF (dry-run)'}\n"
                f"감시 오류: {r.watchdog_error or wd.get('error') or '없음'}")
        elif self.failure:
            self.phase.setText('예약 실패 · ' + self.failure)
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
        event.accept()
