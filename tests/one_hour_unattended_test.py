"""Clock interruptions, power ownership and fail-closed unattended boundaries.

Synthetic clocks/isolated SQLite only. All Windows power/shutdown calls mocked.
"""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import Mock, patch

from market_radar.one_hour import DashboardOneHour
from market_radar.one_hour_archive import validate_archive
from market_radar.power import WindowsSleepInhibitor
from market_radar.run_diagnostics import RunDiagnostics
from tools.one_hour_runner.core import threads_finished
from tools.one_hour_runner.system import RunBlocked
from tests import one_hour_gui_test as gui
from tests.one_hour_runner_test import Clock


class InterruptedEvidenceTests(unittest.TestCase):
    setUp = gui.IntegratedEvidenceTests.setUp

    def test_paper_error_does_not_stop_live_collector(self):
        while self.clock.now() < self.c.schedule.open:
            self.c.schedule.tick(True); self.r.tick(); self.clock.advance(1)
        self.c.paper = SimpleNamespace(error=True, sidecar_error=False, thread=None,
                                      view=lambda: {'rotation_v2': {'status':'ERROR'}})
        for _ in range(10):
            self.c.schedule.tick(True); self.r.tick(); self.clock.advance(1)
        self.assertTrue(self.c.busy)
        self.assertEqual(self.r.state, 'COLLECTING')
        self.assertIsNone(self.r.requested_stop_at)
        self.assertEqual(self.r.failures, [])
        saved = json.loads((self.r.folder/'diagnostics/runtime.json').read_text())
        self.assertEqual(saved['first_error']['code'], 'paper_or_rotation_error')
        self.shutdown.request.assert_not_called()

    def interrupt(self, offset):
        # Observe every simulated second through open; first five minutes only.
        while self.clock.now() < self.c.schedule.open + timedelta(minutes=5):
            self.c.schedule.tick(True)
            self.r.tick()
            self.clock.advance(1)
        self.c.observation_seconds = 300
        self.clock.wall += timedelta(seconds=offset)
        self.c.schedule.tick(True)
        self.r.tick()
        self.assertFalse(self.c.busy)
        self.r.tick()
        return self.r

    def test_clock_jump_without_paper_fail_interface_stops_and_preserves(self):
        r = self.interrupt(3.5324284)
        self.assertEqual(r.state, 'WAITING_SHUTDOWN', r.summary)
        self.assertEqual(r.summary['research_status'], 'FAILED')
        self.assertEqual(r.summary['preservation_status'], 'VERIFIED')
        self.assertIn('clock_jump', r.summary['operational_failures'])
        self.assertEqual(r.summary['shutdown_blockers'], [])
        self.assertTrue(r.summary['shutdown_eligible'])
        self.assertEqual(r.stop_context['reason'], 'clock_jump')
        validate_archive(r.archive, r.summary['run_id'], r.summary['session_date'], r.folder)
        self.shutdown.request.assert_not_called()
        self.clock.advance(3900)
        r.tick()
        self.shutdown.request.assert_called_once()

    def test_clock_rollback_never_advances_original_deadline(self):
        original_deadline = self.r.minimum_shutdown_mono
        r = self.interrupt(-20)
        self.assertEqual(r.state, 'WAITING_SHUTDOWN', r.summary)
        self.assertGreaterEqual(r.minimum_shutdown_mono, original_deadline+20)
        self.clock.advance(original_deadline-self.clock.monotonic())
        r.tick()
        self.shutdown.request.assert_not_called()
        self.clock.advance(20)
        r.tick()
        self.shutdown.request.assert_called_once()

    def test_clock_interrupt_does_not_allow_active_db_writer(self):
        r = self.interrupt(3.6)
        self.c.worker.running = True
        self.clock.advance(4000)
        r.tick()
        self.assertEqual(r.state, 'ERROR')
        self.shutdown.request.assert_not_called()

    def test_frozen_diagnostics_cannot_be_removed_from_verified_zip(self):
        r = self.interrupt(3.6)
        with zipfile.ZipFile(r.archive) as z:
            entries = {n:z.read(n) for n in z.namelist()}
        key = 'diagnostics/collection.json'
        del entries[key]
        manifest = json.loads(entries['manifest.json'])
        del manifest['files'][key]; del manifest['provenance'][key]
        del manifest['local_originals'][key]
        entries['manifest.json'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(r.archive, 'w') as z:
            for name, data in entries.items(): z.writestr(name, data)
        with self.assertRaisesRegex(RunBlocked, 'runtime_diagnostics_missing'):
            validate_archive(r.archive, r.summary['run_id'], r.summary['session_date'], r.folder)


class BoundaryTests(gui.BoundaryTests):
    # Inherit existing mocked OS race/disk/worker/GUI tests with new gates too.
    def test_shutdown_only_cancel_does_not_cancel_collection(self):
        self.r.state = 'COLLECTING'
        self.r.finalized = False
        self.r.request_cancel_shutdown()
        self.r.tick()
        self.assertFalse(self.r.cancel_event.is_set())
        self.assertTrue(self.r.no_shutdown)
        self.assertFalse(self.r.finalized)
        self.execute.assert_not_called()

    def test_waiting_shutdown_only_cancel_preserves_research_verdict(self):
        self.r.summary.update(research_status='FAILED', preservation_status='VERIFIED')
        self.r.request_cancel_shutdown()
        self.r.tick()
        self.assertEqual(self.r.state, 'SHUTDOWN_CANCELLED')
        self.assertEqual(self.r.summary['preservation_status'], 'VERIFIED')
        self.assertFalse(self.r.cancel_event.is_set())
        self.execute.assert_not_called()

    def test_monotonic_rollback_remains_blocked(self):
        self.clock.mono -= 1
        self.r.tick()
        self.assertEqual(self.r.reason, 'monotonic_clock_rollback')
        self.execute.assert_not_called()

    def test_worker_started_during_flush_blocks_at_os_boundary(self):
        self.r._flush_outputs = lambda: setattr(self.r.controller.worker, 'running', True)
        self.r.tick()
        self.execute.assert_not_called()

    def test_clock_rollback_during_final_io_defers_submission(self):
        self.r._flush_outputs = lambda: setattr(self.clock, 'wall', self.clock.wall-timedelta(minutes=10))
        self.r.tick()
        self.assertEqual(self.r.state, 'WAITING_SHUTDOWN')
        self.execute.assert_not_called()

    def test_telemetry_writer_still_running_blocks(self):
        self.r.controller.paper = SimpleNamespace(io_thread=Mock(is_alive=lambda: True))
        self.assertFalse(threads_finished(self.r.controller))
        self.r.tick()
        self.execute.assert_not_called()


class DiagnosticsAndPowerTests(unittest.TestCase):
    def test_preopen_cancel_releases_power_without_provider_or_shutdown(self):
        from market_radar.controller import CollectorController
        with tempfile.TemporaryDirectory() as folder:
            clock = Clock('2026-10-12T12:00:00+00:00')
            provider = Mock(side_effect=AssertionError('provider_must_not_start'))
            c = CollectorController(Path(folder)/'master.db', provider_factory=provider, clock=clock)
            power, shutdown = Mock(), Mock()
            r = DashboardOneHour(c, folder, power=power, shutdown=shutdown)
            r.launch(cache=Path(folder)/'cache.db')
            deadline = time.monotonic()+5
            while not c.busy and r.active and time.monotonic() < deadline:
                time.sleep(.01)
            r.request_cancel(source='test_preopen_cancel')
            r.thread.join(10)
            self.assertFalse(r.active)
            provider.assert_not_called(); shutdown.request.assert_not_called()
            power.acquire.assert_called_once(); power.close.assert_called_once()
            self.assertIn('test_preopen_cancel', (r.folder/'diagnostics/runtime.json').read_text())

    def test_power_acquire_release_only_scoped_flags(self):
        api = Mock(return_value=0x80000000)
        guard = WindowsSleepInhibitor(api)
        guard.acquire(); guard.close(); guard.close()
        self.assertEqual([c.args[0] for c in api.call_args_list], [0x80000001, 0x80000000])

    def test_power_failure_and_wrong_thread(self):
        with self.assertRaisesRegex(RunBlocked, 'sleep_inhibition_failed'):
            WindowsSleepInhibitor(lambda flags: 0).acquire()
        guard = WindowsSleepInhibitor(lambda flags: 1)
        guard.acquire()
        with patch('market_radar.power.threading.get_ident', return_value=-1):
            with self.assertRaisesRegex(RunBlocked, 'wrong_thread'):
                guard.close()
        guard.close()

    def test_supervisor_exception_releases_power_and_leaves_diagnostics(self):
        with tempfile.TemporaryDirectory() as folder:
            power = Mock()
            c = SimpleNamespace(clock=Clock(), calendar=None, busy=False)
            r = DashboardOneHour(c, folder, power=power, shutdown=Mock())
            r.start = Mock(side_effect=ValueError('DO_NOT_PERSIST_PRIVATE_MESSAGE'))
            r._drive(Path(folder)/'cache.db')
            power.acquire.assert_called_once(); power.close.assert_called_once()
            saved = (r.folder/'diagnostics/runtime.json').read_text(encoding='utf-8')
            self.assertIn('supervisor_exception', saved)
            self.assertNotIn('DO_NOT_PERSIST_PRIVATE_MESSAGE', saved)
            r.shutdown.request.assert_not_called()

    def test_power_acquisition_failure_never_starts_collection(self):
        with tempfile.TemporaryDirectory() as folder:
            power = Mock(acquire=Mock(side_effect=RunBlocked('sleep_inhibition_failed')))
            c = SimpleNamespace(clock=Clock(), calendar=None, busy=False)
            r = DashboardOneHour(c, folder, power=power, shutdown=Mock())
            r.start = Mock()
            r._drive(Path(folder)/'cache.db')
            r.start.assert_not_called(); power.close.assert_called_once()
            self.assertTrue((r.folder/'diagnostics/runtime.json').exists())

    def test_diagnostic_write_failure_sticky_and_atomic_original_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = RunDiagnostics(folder, Clock())
            journal.record('initial_error', 'COLLECTING', error=True)
            old = (Path(folder)/'runtime.json').read_bytes()
            with patch('market_radar.failure_evidence.os.replace', side_effect=OSError(28, 'private message')):
                self.assertFalse(journal.record('secondary_error', 'FINALIZING', error=True))
            self.assertEqual((Path(folder)/'runtime.json').read_bytes(), old)
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])
            journal.record('recovered_storage', 'FINALIZING')
            saved = json.loads((Path(folder)/'runtime.json').read_text())
            self.assertEqual(saved['first_error']['code'], 'initial_error')
            self.assertEqual(saved['persistence_errors'][0]['errno'], 28)
            self.assertNotIn('private message', json.dumps(saved))
            with self.assertRaisesRegex(RunBlocked, 'diagnostics_write_failed'):
                journal.freeze()

    def test_event_stage_timings_preserve_fifo_and_consumer_attribution(self):
        from market_radar.paper.telemetry import Timings, TimedQueue
        with tempfile.TemporaryDirectory() as folder:
            t = Timings(Path(folder)/'performance.json'); q = TimedQueue(10, t)
            q.put(('tick', 'SYNTHETIC', 1, Clock().now().isoformat(), True))
            q.put(('tick', 'SYNTHETIC', 2, Clock().now().isoformat(), True))
            self.assertEqual(q.get()[2], 1)
            t.observe('rotation.commit', 246.44)
            writer = threading.Thread(target=lambda: t.observe('producer_only', 100))
            writer.start(); writer.join()
            t.complete(True)
            event = t.snapshot()['first_failure_events'][-1]
            self.assertEqual(event['stages']['rotation.commit']['max_ms'], 246.44)
            self.assertNotIn('producer_only', event['stages'])
            self.assertNotIn('_consumer_thread', event)
            self.assertEqual(q.get()[2], 2)

    def test_default_is_no_actual_shutdown(self):
        c = SimpleNamespace(clock=Clock(), calendar=None)
        self.assertTrue(DashboardOneHour(c, 'unused').no_shutdown)


if __name__ == '__main__':
    unittest.main()
