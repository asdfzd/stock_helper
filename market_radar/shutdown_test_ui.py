"""GUI-only shutdown experiment. Refreshed by the existing Dashboard poll."""
import atexit
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from .shutdown_test import ShutdownExperiment, idle_for_experiment

CONFIRMATION = ('30초 후 Windows 컴퓨터가 실제로 종료됩니다. 실행 중인 모든 프로그램이 '
                '강제로 닫힐 수 있으며 저장하지 않은 작업이 손실될 수 있습니다. 계속하시겠습니까?')
NOTICE = ('Windows에 전달된 예약은 GUI가 비정상 종료되어도 남아 있을 수 있습니다. '
          '모든 작업을 저장한 뒤 시험하세요. 취소 실패 시 Windows에서 shutdown.exe /a로 확인하세요.')


class ShutdownTestBox(QGroupBox):
    def __init__(self, dashboard, *, experiment=None, guard_factory=None):
        super().__init__('Windows 종료 시험 · 수집과 별도', dashboard)
        self.dashboard = dashboard
        self.experiment = experiment or ShutdownExperiment()
        if guard_factory is None:
            from .one_hour import DashboardGuard
            guard_factory = DashboardGuard
        self.guard_factory = guard_factory
        self.guard = None
        self.future = None
        self.confirming = self.disposed = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shutdown-test')
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.start = QPushButton('30초 강제 종료 테스트')
        self.cancel = QPushButton('종료 예약 취소')
        self.start.clicked.connect(self.request)
        self.cancel.clicked.connect(self.abort)
        row.addWidget(self.start)
        row.addWidget(self.cancel)
        row.addStretch()
        layout.addLayout(row)
        self.status = QLabel('대기 · 직접 확인하기 전 Windows 종료 명령 없음')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        notice = QLabel(NOTICE)
        notice.setWordWrap(True)
        layout.addWidget(notice)
        QApplication.instance().aboutToQuit.connect(self.on_quit)
        atexit.register(self.cleanup)
        self.render()

    @property
    def active(self):
        return self.confirming or self.future is not None or self.experiment.active

    def request(self):
        if self.active or not idle_for_experiment(self.dashboard):
            return
        self.confirming = True
        self.render()
        try:
            answer = QMessageBox.warning(self, '실제 Windows 강제 종료 확인', CONFIRMATION,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            # The dialog runs a nested event loop; recheck all owners afterwards.
            if not idle_for_experiment(self.dashboard):
                self.status.setText('차단 · 수집/저장 작업 상태가 변경되었습니다.')
                return
            self.status.setText('Windows 종료 예약 요청 중…')
            self.future = self.executor.submit(self._request)
        finally:
            self.confirming = False
            self.dashboard.poll()

    def _request(self):
        try:
            self.guard = self.guard_factory()
            self.guard.acquire()
            self.guard.hold_after_collection()
            self.experiment.request()
        except Exception as exc:
            from tools.one_hour_runner.system import RunBlocked
            self.experiment.state = 'REJECTED'
            self.experiment.error = str(exc) if isinstance(exc, RunBlocked) else 'collection_guard_failed'
        finally:
            if not self.experiment.active:
                self._release_guard()

    def _release_guard(self):
        if self.guard:
            self.guard.close()
            self.guard = None

    def _abort(self):
        self.experiment.cancel()
        if not self.experiment.active:
            self._release_guard()

    def abort(self):
        if self.future is None and self.experiment.owned:
            self.status.setText('종료 예약 취소 요청 중…')
            self.future = self.executor.submit(self._abort)
            self.render()

    def render(self):
        if self.future is not None and self.future.done():
            try:
                self.future.result()
            except Exception:
                self.status.setText('종료 시험 처리 오류 · Windows 예약 상태를 확인하세요.')
            self.future = None
        e = self.experiment
        self.start.setEnabled(not self.disposed and not self.active and idle_for_experiment(self.dashboard))
        self.cancel.setEnabled(e.owned and self.future is None)
        if self.future is not None:
            return
        detail = f'원인 {e.error} · 오류 코드 {e.code if e.code is not None else "없음"}'
        if e.owned:
            stamp = e.scheduled_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
            countdown = f'예정 {stamp} · 남은 {e.remaining()}초 (예상)'
            self.status.setText((f'종료 예약 성공 · {countdown}' if e.state == 'SCHEDULED' else
                f'경고: 취소 실패 · {detail} · {countdown}') + '\n' +
                '예약이 남아 있습니다. 0초 표시는 실제 종료 완료 확인이 아닙니다.')
        elif e.state == 'CANCELLED':
            self.status.setText('종료 예약 취소 성공 · Windows /a 수락')
        elif e.state == 'NOT_PENDING':
            self.status.setText(f'취소 실패 · {detail} · Windows에 진행 중인 예약 없음')
        elif e.state in {'REJECTED', 'UNKNOWN'}:
            self.status.setText(f'종료 예약 {"결과 불명 — 직접 Windows 상태 확인 필요" if e.uncertain else "실패"} · {detail}')

    def cleanup(self):
        """Bounded command wait; best effort on ordinary exit, never on hard kill."""
        if self.future is not None:
            self.future.result()
            self.future = None
        if self.experiment.owned:
            self._abort()
        return not self.experiment.active

    def prepare_close(self):
        if self.cleanup():
            return True
        self.render()
        QMessageBox.critical(self, 'Windows 종료 예약 경고',
            '종료 예약 취소를 확인하지 못했습니다. 창을 유지합니다.\n' + self.status.text() + '\n' + NOTICE)
        return False

    def on_quit(self):
        if not self.cleanup():
            QMessageBox.critical(self, 'Windows 종료 예약 경고', NOTICE + '\n취소 미확인: ' + str(self.experiment.error))

    def dispose(self):
        self.disposed = True
        self.executor.shutdown(wait=False)
        atexit.unregister(self.cleanup)
