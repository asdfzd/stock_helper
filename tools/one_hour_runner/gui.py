"""Small truthful Korean status window; collector and ZIP never block Qt."""
from datetime import datetime
import math
import threading
import time

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

TEXT = {
    "STARTING": ("자동 수집기를 준비 중입니다.", "실행 환경과 거래일을 확인합니다."),
    "ARMED": ("미국장 개장 전 대기 중입니다.", "정규장 시작 시 자동 수집합니다."),
    "COLLECTING": ("프로그램 실행 중입니다.", "정규장 첫 1시간 데이터를 수집하고 있습니다."),
    "FINALIZING": ("데이터 저장 및 ZIP 생성 중입니다.", "컴퓨터 전원을 끄지 마세요."),
    "SHUTDOWN_PENDING": ("수집 및 ZIP 검증 완료", "60초 후 컴퓨터가 종료됩니다."),
    "COMPLETE_NO_SHUTDOWN": ("수집 및 ZIP 검증 완료", "자동 종료가 비활성화되어 있습니다."),
    "SHUTDOWN_CANCELLED": ("수집 및 ZIP 검증 완료", "컴퓨터 자동 종료를 취소했습니다."),
    "ERROR": ("자동 수집 처리에 문제가 발생했습니다.", "데이터 보존을 위해 자동 종료하지 않습니다."),
}


class StatusWindow(QWidget):
    def __init__(self, runner):
        super().__init__()
        self.runner = runner
        from . import __version__
        self.setWindowTitle("One Hour Research · v"+__version__)
        self.setMinimumSize(580, 300)
        self.setStyleSheet("QWidget { background: #f6f8fb; color: #182338; font-family: 'Malgun Gothic'; }"
                          "QPushButton { background: #e5ebf5; padding: 12px; border-radius: 6px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        self.title, self.body, self.detail = QLabel(), QLabel(), QLabel()
        self.title.setStyleSheet("font-size: 22px; font-weight: 600;")
        self.body.setStyleSheet("font-size: 15px;")
        self.detail.setStyleSheet("font-size: 12px; color: #556278;")
        for label in (self.title, self.body, self.detail):
            label.setWordWrap(True)
            layout.addWidget(label)
        self.cancel = QPushButton("수집 취소 · 저장 후 중지")
        self.cancel.clicked.connect(self.cancel_action)
        layout.addWidget(self.cancel)
        self.results = QPushButton("결과 폴더 열기")
        self.results.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.runner.folder.resolve()))))
        layout.addWidget(self.results)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(200)
        self.refresh()

    def refresh(self):
        r = self.runner
        title, body = TEXT.get(r.state, TEXT["STARTING"])
        if r.simulated:
            title = "[오프라인 모의 실행] "+title
        if r.state == "COLLECTING":
            body += ("\n완료 후 ZIP을 생성합니다. PC 자동 종료는 꺼져 있습니다." if r.no_shutdown
                     else "\n완료 후 ZIP을 생성하고 컴퓨터를 자동 종료합니다.")
        if r.state == "SHUTDOWN_PENDING":
            body = "Windows 종료 예약이 유지될 수 있습니다. 남은 시간은 확인할 수 없습니다."
            try:
                if r.shutdown_deadline_mono is not None:
                    seconds = max(0, math.ceil(r.shutdown_deadline_mono-r.clock.monotonic()))
                    body = f"{seconds}초 후 컴퓨터가 종료됩니다." if seconds else "Windows에 종료를 요청했습니다."
            except Exception:
                pass
            if r.reason == "shutdown_cancel_failed":
                body += "\n종료 취소에 실패했습니다. 종료 예약이 유지될 수 있습니다."
        self.title.setText(title)
        self.body.setText(body)
        details = ["상태: "+r.state]
        if r.plan:
            stop = datetime.fromisoformat(r.plan["stop"]).astimezone()
            details.append("수집 종료 예정: "+stop.strftime("%Y-%m-%d %H:%M:%S %Z"))
            details.append("PC 자동 종료: 비활성화" if r.no_shutdown else
                           "PC 종료: 저장·검증 완료 뒤 60초 (완료 시각에 따라 달라짐)")
        if r.archive:
            details.append("실패 진단 ZIP 저장 완료 · 수집 성공을 뜻하지 않습니다." if r.summary.get("archive_kind") == "diagnostic"
                           else "결과 ZIP 저장 완료 · 아래 버튼으로 폴더를 여세요.")
        if r.reason:
            details.append("사유: "+r.reason)
        if r.simulated:
            details.append("합성 자료만 사용 · 시장 연결 0 · PC 종료 0")
        self.detail.setText("\n".join(details))
        self.cancel.setText("컴퓨터 종료 취소" if r.state == "SHUTDOWN_PENDING" else "수집 취소 · 저장 후 중지")
        self.cancel.setEnabled(r.state == "SHUTDOWN_PENDING" or not r.finalized)
        self.cancel.setVisible(r.state == "SHUTDOWN_PENDING" or not r.finalized)
        self.results.setVisible(r.finalized and r.folder is not None)

    def cancel_action(self):
        if self.runner.state == "SHUTDOWN_PENDING":
            self.runner.cancel_shutdown()
        else:
            self.runner.request_cancel()
        self.refresh()

    def closeEvent(self, event):
        if self.runner.state == "SHUTDOWN_PENDING":
            self.runner.cancel_shutdown()
            if self.runner.state == "SHUTDOWN_PENDING":
                event.ignore()
                return
        if not self.runner.finalized:
            self.runner.request_cancel()
            event.ignore()
            return
        event.accept()


def launch(runner, *, dry_run=False, smoke_dir=None):
    app = QApplication.instance() or QApplication([])
    window = StatusWindow(runner)
    window.show()
    def work():
        try:
            if dry_run:
                from .dry_run import drive
                # Let a human see the initial/preopen state in the offline demo.
                drive(runner, lambda state: time.sleep(.5))
            else:
                runner.start()
                while not runner.finalized:
                    runner.tick()
                    time.sleep(.05)
        except Exception as exc:
            runner.fail("supervisor_"+type(exc).__name__)
            if runner.controller and runner.controller.busy:
                runner.state = "FINALIZING"
                try:
                    runner.controller.stop()
                except Exception:
                    runner.fail("stop_request_failed")
                # Join without a kill timeout; all cleanup remains with the controller.
                thread = getattr(runner.controller, "thread", None)
                if thread and thread.is_alive():
                    thread.join()
            c = runner.controller
            worker = getattr(c, "worker", None)
            provider = getattr(worker, "provider", None)
            for obj in (worker, getattr(c, "paper", None), provider, getattr(provider, "ws", None)):
                for name in ("thread", "io_thread"):
                    thread = getattr(obj, name, None)
                    if thread is not None and thread.is_alive():
                        runner.state = "FINALIZING"
                        thread.join()
            runner._failure_summary()
    thread = threading.Thread(target=work, name="one-hour-supervisor", daemon=False)
    thread.start()
    if smoke_dir is not None:
        from .core import atomic_json
        from runtime_paths import APP_ROOT
        import sys
        smoke_dir.mkdir(parents=True, exist_ok=True)
        observed = []
        def inspect():
            window.refresh()
            if not observed or observed[-1]["state"] != runner.state:
                observed.append(dict(state=runner.state, title=window.title.text(), body=window.body.text()))
                window.grab().save(str(smoke_dir/("state_"+runner.state+".png")))
            if not runner.finalized or thread.is_alive():
                return
            window.grab().save(str(smoke_dir/"window.png"))
            atomic_json(smoke_dir/"ui_smoke.json", dict(state=runner.state, title=window.title.text(),
                body=window.body.text(), simulated=runner.simulated, archive=str(runner.archive),
                window_visible=window.isVisible(), observed_states=observed,
                frozen=bool(getattr(sys, "frozen", False)), runtime_root=str(APP_ROOT),
                executable=sys.executable, module_search_paths=sys.path))
            window.close()
        timer = QTimer(window)
        timer.timeout.connect(inspect)
        timer.start(100)
    result = app.exec()
    thread.join()
    runner.close()
    return 1 if runner.state == "ERROR" else result
