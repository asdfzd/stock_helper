"""Bounded forward clock correction: synthetic wall clocks, real offline stores.

No network, provider connection, power or shutdown system call is permitted.
"""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from market_radar.config import CONFIG
from market_radar.evidence_io import copied_database
from market_radar.models import instant, utc
from market_radar.one_hour_archive import validate_archive, validate_continuation
from market_radar.paper_rotation_v2.runtime import PairedRuntime
from market_radar.providers.kiwoom import KiwoomProvider
from market_radar.providers.kiwoom_cache import KiwoomCache
from tools.one_hour_runner.system import RunBlocked
from tests import one_hour_gui_test as gui
from tests.one_hour_runner_test import Clock
from tests.paper_trading_test import SESSION, row, stamp


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        gui.IntegratedEvidenceTests.setUp(self)
        self.c.observation_stamps = []
        self.paper = SimpleNamespace(error=False, sidecar_error=False, thread=None)
        def fail():
            self.paper.error = True
            if not hasattr(self.c, 'paper_failure'):
                self.c.paper_failure = dict(at=utc(self.clock.now()), reason='application_gap_or_clock_jump')
        self.paper.fail = Mock(side_effect=fail)
        self.paper.view = lambda: {'status': 'ERROR' if self.paper.error else 'WAITING'}
        self.c.paper = self.paper
        while self.c.schedule.started_mono is None:
            self.step()
            if self.c.schedule.started_mono is None:
                self.clock.advance(1)
        self.start = self.c.schedule.started_mono
        self.assertEqual(self.r.collection_clock['deadline_monotonic'], self.start+3900)

    def step(self):
        self.c.schedule.tick(True)
        start = self.c.schedule.started_mono
        if start is not None:
            elapsed = self.clock.monotonic()-start
            if elapsed < 3900 and elapsed % 30 == 0:
                at = utc(self.clock.now())
                if not self.c.observation_stamps or self.c.observation_stamps[-1] != at:
                    self.c.observation_stamps.append(at)
        self.r.tick()

    def until(self, elapsed):
        while self.clock.monotonic()-self.start < elapsed and self.c.busy:
            self.clock.advance(1)
            self.step()

    def correction(self, seconds=3.5324284, at=300):
        self.until(at-1)
        self.clock.advance(1)
        self.clock.wall += timedelta(seconds=seconds)
        self.step()

    def finish(self):
        self.until(3900)
        self.assertFalse(self.c.busy)
        self.r.tick()
        return self.r

    def test_forward_3532ms_continues_full_elapsed_65_and_preserves_failed_zip(self):
        self.c.paper_failure = dict(at=utc(self.c.schedule.open+timedelta(seconds=290)), reason='paper_processing_delay')
        self.paper.error = True  # An earlier research failure cannot become success.
        deadline = self.r.collection_clock['deadline_monotonic']
        self.caller_checks_after_correction()
        self.assertEqual(self.r.collection_clock['deadline_monotonic'], deadline)
        self.until(3899)
        self.assertTrue(self.c.busy)  # Wall time has passed the original +65.
        r = self.finish()
        self.assertEqual(r.state, 'WAITING_SHUTDOWN', r.summary)
        self.assertEqual(r.summary['research_status'], 'FAILED')
        self.assertEqual(r.summary['preservation_status'], 'VERIFIED')
        self.assertEqual(r.collection_clock['stopped_monotonic']-self.start, 3900)
        self.assertAlmostEqual((instant(r.requested_stop_at)-instant(r.plan['stop'])).total_seconds(), 3.532428, places=6)
        self.assertEqual(r.stop_context['reason'], 'planned_65_minute_stop')
        self.assertIn('clock_jump', r.summary['operational_failures'])
        validate_archive(r.archive, r.summary['run_id'], r.summary['session_date'], r.folder)
        with copied_database(self.c.config.db_path) as db:
            stored = [r[0] for r in db.execute('SELECT timestamp_utc FROM radar_snapshots ORDER BY rowid')]
        self.assertEqual(stored, self.c.observation_stamps)
        self.assertEqual(len(stored), 130)
        # Opening stays at official 60m; correction does not move its window.
        self.assertEqual(instant(r.plan['opening_hour_end'])-instant(r.plan['open']), timedelta(minutes=60))
        frozen = json.loads((r.folder/'diagnostics/collection.json').read_text())
        self.assertEqual(frozen['collection_clock'], r.collection_clock)
        bad = deepcopy(r.collection_clock); bad['deadline_monotonic'] -= 3.5
        with self.assertRaisesRegex(RunBlocked, 'clock_continuation_deadline'):
            validate_continuation(bad, r.plan, self.c.schedule.snapshot(), r.stop_context)
        self.clock.advance(296); r.tick()
        self.shutdown.request.assert_not_called()  # UTC ready, monotonic <70.
        self.clock.advance(4); r.tick()
        self.assertEqual(r.state, 'SHUTDOWN_PENDING', (r.reason, r.summary.get('shutdown_blockers')))
        self.shutdown.request.assert_called_once()

    def caller_checks_after_correction(self):
        self.correction()
        self.assertTrue(self.c.busy)
        self.assertTrue(self.r.continue_after_clock_jump)
        self.assertFalse(self.r.clock_abort)
        self.assertEqual(self.r.failures, ['clock_jump'])
        self.assertIsNone(self.r.requested_stop_at)
        self.paper.fail.assert_called_once()
        self.assertFalse(self.c.schedule.snapshot()['protocol_compliant'])
        event = self.r.collection_clock['anomalies'][0]
        self.assertEqual(event['action'], 'continue_raw')
        self.assertEqual(event['classification'], 'bounded_forward_adjustment_candidate')
        self.assertEqual(event['research_action'], 'fail_closed')

    def test_repeated_forward_corrections_do_not_rebase_elapsed_deadline(self):
        self.correction(at=300)
        self.correction(at=600)
        self.assertTrue(self.c.busy)
        self.assertEqual(len(self.r.collection_clock['anomalies']), 2)
        self.assertEqual(self.r.collection_clock['deadline_monotonic'], self.start+3900)
        self.r.no_shutdown = True
        r = self.finish()
        self.assertEqual(r.state, 'COMPLETE_NO_SHUTDOWN', r.summary)
        self.assertEqual(r.summary['preservation_status'], 'VERIFIED')
        self.assertAlmostEqual((instant(r.requested_stop_at)-instant(r.plan['stop'])).total_seconds(), 7.064856, places=6)
        self.shutdown.request.assert_not_called()

    def test_forward_correction_at_65_boundary_does_not_stop_early(self):
        self.correction(at=3899)
        self.assertTrue(self.c.busy)
        self.r.no_shutdown = True
        r = self.finish()
        self.assertEqual(r.state, 'COMPLETE_NO_SHUTDOWN', r.summary)
        self.assertEqual(r.collection_clock['stopped_monotonic'], self.start+3900)

    def test_rollback_and_large_forward_stall_monotonic_fault_are_distinct(self):
        # Each case has a fresh supervised run. Avoid unnecessary fixture exports.
        for wall, mono, category in [(-3.5, 1, 'wall_rollback'), (61, 1, 'large_forward_or_unobserved_gap'),
                                    (12, 12, 'execution_gap_or_resume'), (1, -1, 'monotonic_rollback')]:
            with self.subTest(category=category):
                self.r.failures.clear(); self.r.clock_abort=False; self.r.continue_after_clock_jump=False
                self.r.requested_stop_at=None; self.r.last_wall=self.clock.now(); self.r.last_mono=self.clock.monotonic()
                self.c.stop = Mock()
                self.clock.wall += timedelta(seconds=wall); self.clock.mono += mono
                self.r.tick()
                self.c.stop.assert_called_once()
                self.assertFalse(self.r.continue_after_clock_jump)
                self.assertEqual(self.r.collection_clock['anomalies'][-1]['classification'], category)
                self.shutdown.request.assert_not_called()

    def test_explicit_user_stop_after_correction_is_not_filtered(self):
        self.correction()
        self.c.stop = Mock()
        self.r.request_cancel(source='gui_stop_collection')
        self.r.tick()
        self.c.stop.assert_called_once()
        self.assertEqual(self.r.stop_context['reason'], 'operator_cancelled')
        self.assertIn('gui_stop_collection', (self.r.folder/'diagnostics/runtime.json').read_text())

    def test_raw_loss_and_existing_schedule_gap_do_not_qualify(self):
        self.c.worker.provider.raw_dropped = 1
        self.c.stop = Mock()
        self.correction()
        self.assertTrue(self.r.clock_abort)
        self.c.stop.assert_called_once()
        self.assertEqual(self.r.stop_context['reason'], 'clock_jump')

    def test_diagnostics_write_failure_blocks_continuation(self):
        self.until(299)
        self.c.stop = Mock()
        with patch('market_radar.run_diagnostics.write_diagnostics', side_effect=OSError(28, 'synthetic_disk_full')):
            self.correction()
        self.c.stop.assert_called_once()
        self.assertIn('diagnostics_write_failed', self.r.failures)
        self.assertFalse(self.r.continue_after_clock_jump)
        self.shutdown.request.assert_not_called()

    def test_continued_run_with_worker_or_writer_still_alive_cannot_shutdown(self):
        self.correction()
        r = self.finish()
        self.assertEqual(r.state, 'WAITING_SHUTDOWN', r.summary)
        self.c.paper.io_thread = Mock(is_alive=lambda: True)
        self.clock.advance(300); r.tick()
        self.assertEqual(r.state, 'ERROR')
        self.shutdown.request.assert_not_called()

    def test_continued_run_with_zip_write_failure_cannot_shutdown(self):
        self.correction()
        self.until(3900)
        with patch('market_radar.one_hour_archive.zipfile.ZipFile', side_effect=OSError(28, 'synthetic_disk_full')):
            self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.assertFalse(self.r.summary['shutdown_eligible'])
        self.assertIn('diagnostic_zip_failed', self.r.failures)
        self.shutdown.request.assert_not_called()


class RealStoreTests(unittest.TestCase):
    def test_real_radar_worker_persists_full_65_minutes_across_correction(self):
        from market_radar.one_hour import DashboardOneHour
        from market_radar.runtime import RadarWorker
        from tools.one_hour_runner.dry_run import SyntheticController
        from tests.market_radar_schedule_test import SyntheticLive
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clock = Clock('2026-09-28T13:29:58+00:00')
            c = SyntheticController(root/'master.db', clock=clock)
            shutdown = Mock()
            runner = DashboardOneHour(c, root/'runs', no_shutdown=True, shutdown=shutdown)
            runner.start(cache=root/'cache.db')
            while c.schedule.started_mono is None:
                c.schedule.tick(True); runner.tick()
                if c.schedule.started_mono is None:
                    clock.advance(1)
            start = c.schedule.started_mono
            paper = Mock(error=False, sidecar_error=False)
            paper.metrics = None
            paper.thread = paper.io_thread = None
            paper.view.return_value = {'status': 'WATCHING'}
            paper.fail.side_effect = lambda: setattr(paper, 'error', True)
            c.paper = paper
            provider = SyntheticLive(clock)
            worker = RadarWorker(provider, c.config, schedule=c.schedule, paper=paper)
            c.worker = worker
            c.stop = lambda: worker.stop(reason='user_requested')
            expected = []
            fetch = provider.fetch
            def observed(tracked):
                batch = fetch(tracked)
                expected.append(batch.timestamp_utc)
                return batch
            provider.fetch = observed
            case = self
            class ClockWait:
                stopped = False
                def is_set(self): return self.stopped
                def set(self): self.stopped = True
                def wait(self, seconds):
                    for _ in range(round(seconds)):
                        clock.advance(1)
                        if clock.monotonic()-start == 300:
                            clock.wall += timedelta(seconds=3.5324284)
                        c.schedule.tick(True); runner.tick()
                        if clock.monotonic()-start == 300:
                            case.assertTrue(runner.continue_after_clock_jump)
                            case.assertFalse(self.stopped)
                        if self.stopped: return True
                    return self.stopped
            worker.stop_event = ClockWait()
            worker.thread = SimpleNamespace(is_alive=lambda: not worker.stop_event.is_set())
            with patch('market_radar.runtime.time', SimpleNamespace(monotonic=clock.monotonic, gmtime=time.gmtime)), \
                 patch('socket.socket.connect', side_effect=AssertionError('offline_only')):
                worker._run()
            self.assertTrue(provider.closed)
            self.assertIsNone(worker.error)
            self.assertEqual(worker.failed_batches, 0)
            self.assertEqual(len(expected), 130)
            self.assertEqual(clock.monotonic()-start, 3900)
            self.assertTrue(paper.error)
            self.assertEqual(runner.stop_context['reason'], 'planned_65_minute_stop')
            with copied_database(c.config.db_path) as db:
                self.assertEqual([r[0] for r in db.execute('PRAGMA quick_check')], ['ok'])
                actual = [r[0] for r in db.execute('SELECT timestamp_utc FROM radar_batches ORDER BY rowid')]
                self.assertEqual(actual, expected)
            shutdown.request.assert_not_called()

    def test_original_raw_fifo_and_paired_invalidation_with_forward_adjustment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clock = Clock(stamp(360))
            provider = KiwoomProvider(replace(CONFIG, data_dir=root/'radar'))
            provider.cache = KiwoomCache(root/'cache.db')
            runtime = PairedRuntime(replace(CONFIG, collection_mode='Regular Session'), 'clock-test', root/'paper.db',
                clock=clock.now, review_path=root/'review.db', rotation_enabled=True)
            fake_time = SimpleNamespace(monotonic=clock.monotonic)
            with patch('market_radar.paper_rotation_v2.runtime.time', fake_time), \
                 patch('socket.socket.connect', side_effect=AssertionError('offline_only')):
                runtime.start()
                try:
                    runtime._put(('frame', SESSION, [row(360)], stamp(360), True, False))
                    limit = time.monotonic()+5
                    while runtime.view()['status'] != 'WATCHING' and time.monotonic()<limit:
                        time.sleep(.005)
                    self.assertEqual(runtime.view()['status'], 'WATCHING')
                    expected = []
                    for offset, price in [(0, 10.1), (0, 10.2), (3.5324284, 10.3), (0, 10.4)]:
                        clock.wall += timedelta(seconds=offset)
                        at = utc(clock.now())
                        provider._raw('FE', 'TEST', at, {'values': {'10': str(price)}})
                        expected.append((at, str(price)))
                        runtime._put(('tick', 'TEST', price, at, True))
                    runtime.fail()  # Same public flag used by the supervisor.
                    limit = time.monotonic()+5
                    while not runtime.error and time.monotonic()<limit:
                        time.sleep(.005)
                    self.assertTrue(runtime.error)
                    provider._flush_raw()
                    rows = provider.cache.db.execute('SELECT received_utc,payload FROM raw_records ORDER BY id').fetchall()
                    self.assertEqual([(r[0], json.loads(r[1])['values']['10']) for r in rows], expected)
                    self.assertFalse(provider.stop_event.is_set())
                    self.assertEqual(provider.raw_dropped, 0)
                finally:
                    runtime.finish(); provider.cache.close()
            for name in ('paper.db', 'review.db'):
                with closing(sqlite3.connect(root/name)) as db:
                    self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                    states = [r[0] for r in db.execute('SELECT state FROM paper_trades')]
                    self.assertTrue(states)
                    self.assertTrue(all(s == 'INVALIDATED' for s in states))
                    self.assertGreater(db.execute("SELECT COUNT(*) FROM paper_trade_events WHERE event_type='DATA_INTEGRITY_INVALID'").fetchone()[0], 0)


class ShutdownClockTests(unittest.TestCase):
    setUp = gui.BoundaryTests.setUp

    def test_small_forward_at_70_boundary_requires_new_stable_minute(self):
        self.r.minimum_shutdown_at += timedelta(seconds=1)
        self.r.minimum_shutdown_mono += 1
        self.clock.wall += timedelta(seconds=3.5324284)
        self.r.tick()
        self.execute.assert_not_called()
        self.clock.advance(59); self.r.tick()
        self.execute.assert_not_called()
        self.clock.advance(1); self.r.tick()
        self.execute.assert_called_once()


if __name__ == '__main__':
    unittest.main()
