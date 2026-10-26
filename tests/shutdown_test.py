"""Every OS boundary is injected: these tests cannot request Windows shutdown."""
from concurrent.futures import Future
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from market_radar.shutdown_test import ShutdownExperiment, idle_for_experiment, system_shutdown_path


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.clock = SimpleNamespace(mono=100., wall=datetime(2026, 10, 26, tzinfo=timezone.utc))
        self.execute = Mock(return_value=SimpleNamespace(returncode=0))
        self.path = Path('C:/Windows/System32/shutdown.exe')
        self.exp = ShutdownExperiment(execute=self.execute, path_resolver=lambda: self.path,
            monotonic=lambda: self.clock.mono, now=lambda: self.clock.wall)

    def test_idle_never_calls_os_or_aborts_other_reservation(self):
        self.assertFalse(self.exp.active)
        self.assertFalse(self.exp.cancel())
        self.execute.assert_not_called()

    def test_exact_request_and_abort_arguments(self):
        self.assertTrue(self.exp.request())
        self.assertEqual(self.execute.call_args.args[0], [str(self.path), '/s', '/f', '/t', '30'])
        self.assertEqual(self.execute.call_args.kwargs['timeout'], 5)
        self.assertEqual(self.exp.state, 'SCHEDULED')
        self.assertTrue(self.exp.cancel())
        self.assertEqual(self.execute.call_args.args[0], [str(self.path), '/a'])
        self.assertEqual(self.exp.state, 'CANCELLED')

    def test_monotonic_countdown_ignores_forward_and_backward_wall_changes(self):
        self.exp.request()
        scheduled = self.exp.scheduled_at
        self.assertEqual(self.exp.remaining(), 30)
        for delta, expected in [(0.1, 30), (1., 29), (29., 1), (30., 0), (80., 0)]:
            self.clock.mono = 100+delta
            self.clock.wall += timedelta(hours=-24 if expected % 2 else 24)
            self.assertEqual(self.exp.remaining(), expected)
            self.assertEqual(self.exp.scheduled_at, scheduled)
        self.assertTrue(self.exp.owned)  # Zero countdown does not prove OS shutdown.

    def test_duplicate_request_does_not_extend_deadline(self):
        self.exp.request()
        self.clock.mono += 10
        self.assertFalse(self.exp.request())
        self.assertEqual(self.exp.remaining(), 20)
        self.assertEqual(self.execute.call_count, 1)

    def test_rejections_do_not_own_or_abort_existing_os_schedule(self):
        for code, kind in [(5, 'access_denied'), (1314, 'privilege_not_held'),
                           (1115, 'shutdown_in_progress'), (1190, 'shutdown_already_scheduled'), (1, 'os_rejected')]:
            with self.subTest(code=code):
                self.execute.reset_mock()
                self.execute.return_value.returncode = code
                self.assertFalse(self.exp.request())
                self.assertFalse(self.exp.owned)
                self.assertEqual((self.exp.error, self.exp.code), (kind, code))
                self.assertFalse(self.exp.cancel())
                self.assertEqual(self.execute.call_count, 1)

    def test_abort_failure_keeps_countdown_and_allows_retry(self):
        self.exp.request()
        self.execute.return_value.returncode = 5
        self.assertFalse(self.exp.cancel())
        self.assertEqual(self.exp.state, 'CANCEL_FAILED')
        self.assertTrue(self.exp.owned)
        self.execute.return_value.returncode = 0
        self.assertTrue(self.exp.cancel())

    def test_no_pending_is_distinct_from_successful_abort(self):
        self.exp.request()
        self.execute.return_value.returncode = 1116
        self.assertFalse(self.exp.cancel())
        self.assertEqual(self.exp.state, 'NOT_PENDING')
        self.assertFalse(self.exp.active)

    def test_unknown_request_never_claims_success_or_aborts_unowned(self):
        for error in (subprocess.TimeoutExpired('mock', 5), RuntimeError('mock')):
            self.setUp()
            self.execute.side_effect = error
            self.assertFalse(self.exp.request())
            self.assertEqual(self.exp.state, 'UNKNOWN')
            self.assertTrue(self.exp.active)
            self.assertFalse(self.exp.cancel())
            self.assertFalse(self.exp.request())
            self.assertEqual(self.execute.call_count, 1)

    def test_start_failure_and_missing_system_binary(self):
        self.execute.side_effect = PermissionError(13, 'hidden')
        self.assertFalse(self.exp.request())
        self.assertEqual(self.exp.error, 'process_start_failed')
        self.assertFalse(self.exp.active)
        self.exp.path_resolver = Mock(side_effect=FileNotFoundError(2, 'hidden'))
        self.execute.reset_mock()
        self.assertFalse(self.exp.request())
        self.execute.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows path inspection')
    def test_native_absolute_system_path_ignores_environment_and_cwd(self):
        with patch.dict(os.environ, {'SystemRoot': 'C:/untrusted'}):
            p = system_shutdown_path()
        self.assertTrue(p.is_absolute())
        self.assertTrue(p.is_file())
        self.assertEqual(p.parent.name.lower(), 'system32')
        self.assertEqual(p.name, 'shutdown.exe')
        self.assertNotIn('untrusted', str(p))


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtCore import QSettings
        from market_radar.config import CONFIG
        from market_radar.ui import RadarPanel
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.panel = RadarPanel(config=replace(CONFIG, data_dir=self.root/'data'),
            master_path=self.root/'master.db', settings=QSettings(str(self.root/'settings.ini'), QSettings.Format.IniFormat))
        self.box = self.panel.shutdown_test
        self.execute = Mock(return_value=SimpleNamespace(returncode=0))
        self.box.experiment = ShutdownExperiment(execute=self.execute,
            path_resolver=lambda: Path('C:/Windows/System32/shutdown.exe'))
        self.guard = Mock()
        self.box.guard_factory = Mock(return_value=self.guard)
        self.drain()

    def drain(self):
        end = time.monotonic()+5
        while self.panel.jobs or self.box.future:
            self.panel.poll()
            self.app.processEvents()
            if time.monotonic() > end:
                self.fail('GUI jobs did not drain')
            time.sleep(.005)
        self.panel.poll()

    def tearDown(self):
        self.execute.side_effect = None
        self.execute.return_value = SimpleNamespace(returncode=0)
        self.box.cleanup()
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def accept(self):
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Yes):
            self.box.request()
        self.drain()

    def test_normal_start_defaults_and_cancel_dialog_issue_zero_commands(self):
        from PySide6.QtWidgets import QMessageBox
        self.panel.show()
        self.assertEqual(self.panel.tabs.tabText(0), '수집 테스트')
        self.assertTrue(self.box.start.isVisible())
        self.assertTrue(self.box.start.isEnabled())
        self.assertTrue(self.panel.one_hour_no_shutdown.isChecked())
        self.assertIsNone(self.panel.one_hour)
        with patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.No):
            self.box.start.click()
        self.execute.assert_not_called()
        self.guard.acquire.assert_not_called()

    def test_every_busy_state_blocks_without_stopping_controller(self):
        with patch.object(self.panel.controller, 'stop') as stop:
            for state in ['ARMED', 'PREPARING', 'PREOPEN_WAIT', 'RUNNING', 'FINALIZING', 'STARTING', 'ERROR']:
                self.panel.controller.state = state
                self.box.render()
                self.assertFalse(self.box.start.isEnabled(), state)
                self.box.request()
            self.panel.controller.state = 'STOPPED'
            stop.assert_not_called()
        self.execute.assert_not_called()

    def test_supervisor_and_file_jobs_and_lingering_writer_block(self):
        self.panel.one_hour = SimpleNamespace(active=True, shutdown_may_be_pending=False)
        self.assertFalse(idle_for_experiment(self.panel))
        self.panel.one_hour = None
        self.panel.jobs['zip'] = (Future(), lambda x: None)
        self.assertFalse(idle_for_experiment(self.panel))
        self.panel.jobs.clear()
        self.panel.controller.worker = SimpleNamespace(running=True)
        self.assertFalse(idle_for_experiment(self.panel))
        self.panel.controller.worker = SimpleNamespace(running=False, provider=SimpleNamespace())
        self.panel.controller.paper = SimpleNamespace(io_thread=SimpleNamespace(is_alive=lambda: True))
        self.assertFalse(idle_for_experiment(self.panel))
        self.panel.controller.worker = self.panel.controller.paper = None

    def test_confirm_rechecks_state_after_nested_dialog(self):
        from PySide6.QtWidgets import QMessageBox
        def change(*args):
            self.panel.controller.state = 'ARMED'
            return QMessageBox.StandardButton.Yes
        with patch.object(QMessageBox, 'warning', side_effect=change):
            self.box.request()
        self.panel.controller.state = 'STOPPED'
        self.execute.assert_not_called()

    def test_confirm_schedule_and_abort_keep_controller_unchanged(self):
        c = self.panel.controller
        with patch.object(c, 'start') as start, patch.object(c, 'stop') as stop:
            self.accept()
            self.assertTrue(self.box.experiment.owned)
            self.assertIn('종료 예약 성공', self.box.status.text())
            self.assertIn('남은 30초', self.box.status.text())
            self.assertFalse(self.panel.start_button.isEnabled())
            self.panel.start()
            self.panel.start_one_hour()
            self.panel.submit('forbidden', lambda: self.fail('job during shutdown'), lambda x: None)
            self.box.request()
            self.assertEqual(self.execute.call_count, 1)
            self.box.abort()
            self.drain()
            self.assertIn('취소 성공', self.box.status.text())
            start.assert_not_called()
            stop.assert_not_called()
        self.assertEqual(c.state, 'STOPPED')
        self.assertIsNone(c.worker)
        self.assertIsNone(self.panel.one_hour)
        self.assertTrue(self.panel.one_hour_no_shutdown.isChecked())
        self.guard.close.assert_called_once()

    def test_os_rejection_does_not_show_countdown_or_cancel_other_schedule(self):
        self.execute.return_value.returncode = 1190
        self.accept()
        self.assertIn('1190', self.box.status.text())
        self.assertNotIn('예약 성공', self.box.status.text())
        self.box.abort()
        self.box.cleanup()
        self.assertEqual(self.execute.call_count, 1)

    def test_close_attempts_abort_and_failure_keeps_window(self):
        from PySide6.QtWidgets import QMessageBox
        self.accept()
        self.execute.return_value.returncode = 5
        with patch.object(QMessageBox, 'critical') as warning:
            self.panel.close()
            warning.assert_called_once()
        self.assertFalse(self.panel.closed)
        self.assertTrue(self.box.experiment.owned)
        self.execute.return_value.returncode = 0
        self.panel.close()
        self.assertTrue(self.panel.closed)
        self.assertEqual(self.execute.call_args.args[0][-1], '/a')

    def test_application_quit_and_atexit_cleanup_are_idempotent(self):
        self.accept()
        self.box.on_quit()
        self.box.cleanup()
        self.assertEqual(self.execute.call_count, 2)
        self.assertFalse(self.box.experiment.active)

    def test_other_process_guard_failure_never_calls_shutdown(self):
        from tools.one_hour_runner.system import RunBlocked
        self.guard.acquire.side_effect = RunBlocked('other_collection_application')
        self.accept()
        self.execute.assert_not_called()
        self.assertIn('other_collection_application', self.box.status.text())

    def test_close_while_request_finishes_aborts_accepted_command(self):
        from PySide6.QtWidgets import QMessageBox
        with patch.object(QMessageBox, 'warning', return_value=QMessageBox.StandardButton.Yes):
            self.box.request()
        self.panel.close()
        self.assertFalse(self.box.experiment.active)
        self.assertEqual(self.execute.call_count, 2)


if __name__ == '__main__':
    unittest.main()
