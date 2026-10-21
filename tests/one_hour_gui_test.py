"""Integrated Dashboard: synthetic evidence, mocked OS, no Kiwoom calls."""
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.config import CONFIG
from market_radar.models import instant
from market_radar.one_hour import DashboardOneHour, DashboardGuard
from market_radar.one_hour_archive import validate_archive
from tools.one_hour_runner.diagnostics import validate_diagnostic_zip
from tools.one_hour_runner.dry_run import SyntheticController
from tools.one_hour_runner.system import RunBlocked, WindowsShutdown
from tests import one_hour_runner_test as original


class IntegratedEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock = original.Clock()
        self.shutdown = Mock(spec=WindowsShutdown)
        self.c = SyntheticController(self.root/"master.db", clock=self.clock)
        self.r = DashboardOneHour(self.c, self.root/"runs", clock=self.clock, shutdown=self.shutdown, no_shutdown=False)
        blocker = patch.object(socket.socket, "connect", side_effect=AssertionError("network_forbidden"))
        self.network = blocker.start()
        self.addCleanup(blocker.stop)
        self.addCleanup(self.network.assert_not_called)
        self.r.start(cache=self.root/"cache.db")

    prepare = original.FixtureTests.prepare

    def finish(self):
        self.prepare()
        self.r.tick()
        return self.r

    def test_uses_same_controller_and_preserves_regular_validator(self):
        r = self.finish()
        self.assertIs(r.controller, self.c)
        self.assertEqual(r.requested_stop_at, r.plan["stop"])
        self.assertEqual(r.summary["original_validation_overall"], "FAIL")
        self.assertFalse(r.summary["full_session"])
        self.assertEqual(self.c.schedule.stop_at, instant(r.plan["official_close"]))
        validate_archive(r.archive, r.summary["run_id"], r.summary["session_date"], r.folder)
        self.assertEqual(r.summary["collection_quality"]["policy"]["version"], 2)
        self.assertEqual(r.state, "WAITING_SHUTDOWN")
        self.shutdown.request.assert_not_called()
        self.clock.advance(299)
        r.tick()
        self.shutdown.request.assert_not_called()
        self.clock.advance(1)
        r.tick()
        self.shutdown.request.assert_called_once_with()
        self.assertEqual(r.shutdown_at, instant(r.plan["open"])+timedelta(minutes=71))
        r.tick()
        self.shutdown.request.assert_called_once()

    def test_no_shutdown_still_publishes_verified_zip(self):
        self.r.no_shutdown = True
        r = self.finish()
        validate_archive(r.archive, r.summary["run_id"], r.summary["session_date"], r.folder)
        self.assertEqual(r.state, "COMPLETE_NO_SHUTDOWN")
        self.shutdown.request.assert_not_called()

    def test_slow_postprocessing_waits_without_killing(self):
        self.prepare()
        self.clock.advance(600)
        self.r.tick()
        self.shutdown.request.assert_not_called()
        self.r.tick()
        self.shutdown.request.assert_called_once()
        self.assertGreater(self.r.shutdown_at, self.r.minimum_shutdown_at)

    def test_paper_error_preserves_diagnostic_and_shuts_down(self):
        self.prepare()
        self.c.paper.error = "synthetic_error"
        self.r.tick()
        self.assertEqual(self.r.state, "WAITING_SHUTDOWN")
        self.assertIn("one_hour_diagnostic_", self.r.archive.name)
        validate_archive(self.r.archive, self.r.summary["run_id"], self.r.summary["session_date"], self.r.folder)
        self.assertTrue(self.r.summary["shutdown_eligible"])
        self.assertEqual(self.r.summary["status"], "FAILED")
        self.clock.advance(600)
        self.r.tick()
        self.shutdown.request.assert_called_once()

    def test_rotation_public_error_preserves_diagnostic(self):
        self.prepare()
        self.c.paper.view = lambda: {"rotation_v2": {"status": "ERROR"}}
        self.r.tick()
        self.assertEqual(self.r.state, "WAITING_SHUTDOWN")
        self.assertIn("one_hour_diagnostic_", self.r.archive.name)
        self.shutdown.request.assert_not_called()

    def test_waiting_can_be_cancelled(self):
        r = self.finish()
        r.request_cancel()
        r.tick()
        self.assertEqual(r.state, "ERROR")
        self.clock.advance(600)
        r.tick()
        self.shutdown.request.assert_not_called()

    def test_tampered_published_zip_blocks_final_os_boundary(self):
        r = self.finish()
        r.archive.write_bytes(b"not a ZIP")
        self.clock.advance(300)
        r.tick()
        self.assertEqual(r.state, "ERROR")
        self.shutdown.request.assert_not_called()

    def test_clock_jump_while_waiting_extends_safe_deadline(self):
        r = self.finish()
        self.clock.wall += timedelta(minutes=6)
        r.tick()
        self.shutdown.request.assert_not_called()
        self.assertEqual(r.state, 'WAITING_SHUTDOWN')
        self.clock.advance(300)
        r.tick()
        self.shutdown.request.assert_called_once()

    def test_cancel_before_stop_is_failure_zip(self):
        # With complete synthetic files, an operator cancellation must still fail.
        self.prepare()
        self.r.request_cancel()
        self.r.tick()
        self.assertFalse(self.r.summary["shutdown_eligible"])
        self.shutdown.request.assert_not_called()


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.clock = original.Clock()
        self.clock.advance(3600)
        self.execute = Mock(return_value=SimpleNamespace(returncode=0))
        c = SimpleNamespace(clock=self.clock, calendar=None, busy=False,
                            worker=SimpleNamespace(running=False, provider=None), paper=None)
        self.r = DashboardOneHour(c, self.root, clock=self.clock, shutdown=WindowsShutdown(self.execute), no_shutdown=False)
        self.r.controller = c
        self.r.folder = self.root
        self.r.summary = dict(shutdown_eligible=True, run_id="synthetic", session_date="2026-10-12")
        self.r.archive = self.root/"synthetic.zip"
        self.r.minimum_shutdown_at = self.clock.now()
        self.r.minimum_shutdown_mono = self.clock.monotonic()
        self.r._clock_origin = self.clock.now(), self.clock.monotonic()
        self.r.state = "WAITING_SHUTDOWN"
        validator = patch.object(self.r, "_validate_preserved_archive")
        validator.start()
        self.addCleanup(validator.stop)

    def test_actual_command_arguments_mocked_and_abort(self):
        self.r.tick()
        self.assertEqual(self.execute.call_args.args[0][1:], ["/s", "/f", "/t", "60"])
        self.r.request_cancel()
        self.r.tick()
        self.assertEqual(self.execute.call_args.args[0][1:], ["/a"])
        self.assertFalse(self.r.shutdown_may_be_pending)
        self.assertFalse(self.r.summary["shutdown_eligible"])

    def test_cancel_failure_retains_pending_and_can_retry(self):
        self.r.tick()
        self.execute.return_value.returncode = 5
        self.r.request_cancel()
        self.r.tick()
        self.assertTrue(self.r.shutdown_may_be_pending)
        self.assertEqual(self.r.reason, "shutdown_cancel_failed")
        calls = self.execute.call_count
        self.r.tick()
        self.assertEqual(self.execute.call_count, calls)
        self.execute.return_value.returncode = 0
        self.r.request_cancel()
        self.r.tick()
        self.assertFalse(self.r.shutdown_may_be_pending)
        self.assertFalse(self.r.summary["shutdown_eligible"])

    def test_uncertain_request_is_cancelled(self):
        self.execute.side_effect = [OSError("uncertain OS result"), SimpleNamespace(returncode=0)]
        self.r.tick()
        self.assertEqual([x.args[0][1:] for x in self.execute.call_args_list], [["/s", "/f", "/t", "60"], ["/a"]])
        self.assertFalse(self.r.shutdown_may_be_pending)
        self.assertFalse(self.r.summary["shutdown_eligible"])

    def test_clock_exception_after_request_triggers_abort(self):
        def execute(args, **kwargs):
            if args[1] == "/s":
                self.clock.now = Mock(side_effect=ValueError("clock failed"))
            return SimpleNamespace(returncode=0)
        self.execute.side_effect = execute
        self.r.tick()
        self.assertEqual(self.execute.call_count, 2)
        self.assertEqual(self.execute.call_args.args[0][1:], ["/a"])
        self.assertFalse(self.r.shutdown_may_be_pending)
        self.assertFalse(self.r.summary["shutdown_eligible"])

    def test_disk_failure_never_reaches_os(self):
        with patch.object(self.r, "_flush_outputs", side_effect=OSError("disk full")):
            self.r.tick()
        self.execute.assert_not_called()

    def test_alive_worker_blocks_shutdown(self):
        self.r.controller.worker.running = True
        self.r.tick()
        self.execute.assert_not_called()

    def test_no_shutdown_guard_at_os_boundary(self):
        self.r.no_shutdown = True
        self.r.tick()
        self.execute.assert_not_called()

    def test_utc_ready_but_monotonic_too_early(self):
        self.r.minimum_shutdown_mono += 60
        self.r.tick()
        self.execute.assert_not_called()

    def test_gui_file_job_must_finish_before_shutdown(self):
        self.r.ready_to_shutdown = lambda: False
        self.r.tick()
        self.execute.assert_not_called()
        self.assertEqual(self.r.state, "WAITING_SHUTDOWN")
        self.r.ready_to_shutdown = lambda: True
        self.r.tick()
        self.assertEqual(self.execute.call_count, 1)

    def test_stop_after_cancel_does_not_reschedule(self):
        self.r.tick()
        self.r.request_cancel()
        self.r.tick()
        for _ in range(3):
            self.r.tick()
        self.assertEqual(self.execute.call_count, 2)


class StartAndUiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_busy_controller_is_not_started_or_stopped(self):
        c = SimpleNamespace(busy=True, clock=original.Clock(), calendar=None, start=Mock(), stop=Mock())
        r = DashboardOneHour(c, self.root, shutdown=Mock())
        with self.assertRaisesRegex(RunBlocked, "collector_already_running"):
            r.launch(cache=self.root/"cache.db")
        c.start.assert_not_called()
        c.stop.assert_not_called()
        self.assertFalse(self.root.joinpath("runs").exists())

    def test_default_shutdown_adapter_has_bounded_os_calls(self):
        c = SimpleNamespace(clock=original.Clock(), calendar=None)
        with patch("market_radar.one_hour.subprocess.run", return_value=SimpleNamespace(returncode=0)) as execute:
            r = DashboardOneHour(c, self.root)
            r.shutdown.request()
            r.shutdown.cancel()
        self.assertEqual(execute.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 10 for call in execute.call_args_list))

    def test_late_start_is_rejected_without_collector(self):
        clock = original.Clock("2026-10-12T13:30:01+00:00")
        c = SyntheticController(self.root/"master.db", clock=clock)
        r = DashboardOneHour(c, self.root/"runs", shutdown=Mock())
        r.start(cache=self.root/"cache.db")
        self.assertEqual(r.reason, "late_launch")
        self.assertFalse(c.busy)
        r.shutdown.request.assert_not_called()

    def test_cancel_during_preparation_does_not_start_collector(self):
        c = SyntheticController(self.root/"master.db", clock=original.Clock())
        r = DashboardOneHour(c, self.root/"runs", shutdown=Mock())
        r.request_cancel()
        r.start(cache=self.root/"cache.db")
        self.assertEqual(r.reason, "operator_cancelled")
        self.assertFalse(c.busy)
        self.assertIsNone(r.controller)

    def test_guard_allows_containing_gui_and_venv_parent_only(self):
        current = SimpleNamespace(pid=os.getpid(), name=lambda: "pythonw.exe", cmdline=lambda: ["live_ui.py"])
        parent = SimpleNamespace(pid=98765, name=lambda: "pythonw.exe", cmdline=lambda: ["live_ui.py"])
        process = Mock()
        process.parents.return_value = [parent]
        with patch("psutil.Process", return_value=process), patch("psutil.process_iter", return_value=[current, parent]):
            DashboardGuard().check_legacy()
        other = SimpleNamespace(pid=98766, name=lambda: "StockHelper.exe")
        with patch("psutil.Process", return_value=process), patch("psutil.process_iter", return_value=[current, parent, other]):
            with self.assertRaisesRegex(RunBlocked, "other_collection_application"):
                DashboardGuard().check_legacy()

    def test_guard_blocks_standalone_exe_and_unknown_inventory(self):
        import psutil
        process = Mock()
        process.parents.return_value = []
        other = SimpleNamespace(pid=98766, name=lambda: "OneHourResearch.exe")
        with patch("psutil.Process", return_value=process), patch("psutil.process_iter", return_value=[other]):
            with self.assertRaises(RunBlocked):
                DashboardGuard().check_legacy()
        with patch("psutil.Process", side_effect=psutil.AccessDenied()):
            with self.assertRaisesRegex(RunBlocked, "process_inventory_unavailable"):
                DashboardGuard().check_legacy()

    def test_gui_start_uses_existing_controller_and_disables_writes(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from market_radar.ui import RadarPanel
        app = QApplication.instance() or QApplication([])
        panel = RadarPanel(config=replace(CONFIG, data_dir=self.root/"data"), master_path=self.root/"master.db",
                           settings=QSettings(str(self.root/"ui.ini"), QSettings.Format.IniFormat))
        try:
            panel.one_hour_enable.setChecked(True)
            panel.one_hour_no_shutdown.setChecked(True)
            for future, _ in list(panel.jobs.values()):
                future.result(timeout=10)
            panel.poll()
            controller = panel.controller
            with patch.object(DashboardOneHour, "launch") as launch:
                panel.start()
                launch.assert_called_once()
            self.assertIs(panel.one_hour.existing_controller, controller)
            self.assertTrue(panel.one_hour.no_shutdown)
            panel.one_hour.shutdown_may_be_pending = True
            panel.one_hour.reason = "shutdown_cancel_failed"
            panel.poll()
            self.assertFalse(panel.start_button.isEnabled())
            self.assertTrue(panel.one_hour_cancel.isEnabled())
            self.assertTrue(all(not b.isEnabled() for b in panel.mutation_buttons))
            self.assertIn("종료 예약이 남아", panel.one_hour_status.text())
            panel.close()
            self.assertTrue(panel._closing)
            self.assertFalse(panel.closed)
            self.assertTrue(panel.one_hour.cancel_event.is_set())
            panel.one_hour.shutdown_may_be_pending = False
        finally:
            panel.close()
            app.processEvents()

    def test_dashboard_defaults_off_and_restores_capacity_preferences(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from market_radar.ui import RadarPanel
        app = QApplication.instance() or QApplication([])
        panel = RadarPanel(config=replace(CONFIG, data_dir=self.root/"data", collection_mode="Capacity Test", max_ws_symbols=87),
                           master_path=self.root/"master.db", settings=QSettings(str(self.root/"ui.ini"), QSettings.Format.IniFormat))
        try:
            self.assertFalse(panel.one_hour_enable.isChecked())
            self.assertTrue(panel.one_hour_no_shutdown.isChecked())
            self.assertFalse(panel.one_hour_cancel.isEnabled())
            panel.one_hour_enable.setChecked(True)
            self.assertEqual(panel.collection_mode.currentText(), "Regular Session")
            self.assertEqual(panel.max_ws.value(), 150)
            self.assertTrue(panel.paper_panel.enable.isChecked())
            self.assertTrue(panel.paper_panel.rotation_enable.isChecked())
            self.assertTrue(panel.opening_hour_enable.isChecked())
            self.assertFalse(panel.controller.busy)
            panel.one_hour_enable.setChecked(False)
            self.assertEqual(panel.collection_mode.currentText(), "Capacity Test")
            self.assertEqual(panel.max_ws.value(), 87)
            self.assertFalse(panel.paper_panel.enable.isChecked())
            self.assertFalse(panel.paper_panel.rotation_enable.isChecked())
        finally:
            for future, _ in list(panel.jobs.values()):
                future.result(timeout=10)
            panel.close()
            app.processEvents()

    def test_launch_failure_restores_original_controller_paths(self):
        clock = original.Clock("2026-10-12T13:30:01+00:00")
        c = SyntheticController(self.root/"master.db", clock=clock)
        c.paper_path, c.paper_review_path, c.opening_hour_path = "original-paper", "original-rotation", "original-opening"
        r = DashboardOneHour(c, self.root/"runs", shutdown=Mock(), power=Mock())
        r.launch(cache=self.root/"cache.db")
        r.thread.join(10)
        self.assertFalse(r.active)
        self.assertEqual((c.paper_path, c.paper_review_path, c.opening_hour_path),
                         ("original-paper", "original-rotation", "original-opening"))


if __name__ == "__main__":
    unittest.main()
