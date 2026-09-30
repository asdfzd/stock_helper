"""v2.1 failures, cancellation, audit and cadence boundaries; offline only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from tests.kiwoom_provider_test import FakeClock, observation, SESSION, raw_bars
from market_radar.config import CONFIG
from market_radar.console import collect_until_stopped
from market_radar.engine import HORIZONS, RadarEngine
from market_radar.models import MarketBatch, instant, utc
from market_radar.providers.kiwoom import KiwoomProvider
from market_radar.providers.kiwoom_cache import KiwoomCache
from market_radar.providers.kiwoom_failures import StopRequested, RestFailure, classify
from market_radar.providers.kiwoom_rest import KiwoomRest, RateLimiter
from market_radar.providers.kiwoom_ws import KiwoomWebSocket
from market_radar.quality import QualityCounters, QualityLogHandler, daily_summary
from market_radar.runtime import RadarWorker
from market_radar.storage import RadarStore


def response(code=0, status=200, message='', **payload):
    return Mock(status_code=status, headers={}, json=Mock(return_value=dict(
        return_code=code, return_msg=message, **payload)))


class RestStabilityTests(unittest.TestCase):
    def setUp(self):
        self.http, self.raw = Mock(), Mock()
        self.counters = QualityCounters()
        self.logger = logging.Logger('stability')
        self.logger.addHandler(QualityLogHandler(self.counters))
        self.rest = KiwoomRest(CONFIG, FakeClock(), logger=self.logger, session=self.http, raw_sink=self.raw)
        self.rest._token = 'TEST_TOKEN'
        self.rest._credentials = ('TEST_KEY', 'TEST_SECRET')
        self.rest.limiter = Mock()

    def reject(self):
        self.http.post.return_value = response(7, message='service [1903:symbol absent]')

    def test_deterministic_embedded_code_and_generic_code_not_guessed(self):
        self.assertEqual(classify(200, 7, 'service [1903:symbol absent]'), ('deterministic', 'unsupported', 1903))
        self.assertEqual(classify(200, 7, 'service error')[0], 'unclassified')

    def test_transient_classification(self):
        for args in [(503, None, ''), (429, None, ''), (200, 7, '[1700:limit]')]:
            with self.subTest(args=args): self.assertEqual(classify(*args)[0], 'transient')
        self.assertEqual(classify(category='timeout')[:2], ('transient', 'timeout'))
        self.assertEqual(classify(category='connection_error')[0], 'transient')

    def test_deterministic_storm_suppressed_arbitrary_symbols(self):
        self.reject()
        for ticker in ('ALPHA', 'BETA'):
            for _ in range(20):
                with self.assertRaises(RestFailure): self.rest.quote(ticker, 'ND')
        self.assertEqual(self.http.post.call_count, 2)
        self.assertEqual(self.raw.call_count, 2)
        self.assertNotIn('FFR', Path('market_radar/providers/kiwoom_rest.py').read_text())
        self.assertNotIn('FFR', Path('market_radar/providers/kiwoom_failures.py').read_text())

    def test_query_and_tr_are_independent(self):
        self.reject()
        for day in ('2026-09-29', '2026-09-30'):
            with self.assertRaises(RestFailure): self.rest.minutes_page('ALPHA', 'ND', day)
        with self.assertRaises(RestFailure): self.rest.quote('ALPHA', 'ND')
        self.assertEqual(self.http.post.call_count, 3)

    def test_negative_ttl_recovery(self):
        self.reject()
        with patch('market_radar.providers.kiwoom_rest.time.monotonic', return_value=100):
            with self.assertRaises(RestFailure): self.rest.quote('ALPHA', 'ND')
        self.http.post.return_value = response(cur_prc='10')
        with patch('market_radar.providers.kiwoom_rest.time.monotonic', return_value=100+CONFIG.rest_negative_ttl_seconds+1):
            self.assertEqual(self.rest.quote('ALPHA', 'ND')[0]['cur_prc'], '10')
        self.assertEqual(self.http.post.call_count, 2)
        self.assertEqual(self.rest.failures, {})

    def test_next_trading_day_revalidates(self):
        self.reject()
        with patch('market_radar.providers.kiwoom_rest.datetime') as clock:
            clock.now.return_value = datetime(2026, 9, 29, 15, tzinfo=timezone.utc)
            with self.assertRaises(RestFailure): self.rest.quote('ALPHA', 'ND')
            clock.now.return_value = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)
            with self.assertRaises(RestFailure): self.rest.quote('ALPHA', 'ND')
        self.assertEqual(self.http.post.call_count, 2)

    def test_transient_retry_budget_and_cooldown(self):
        self.http.post.side_effect = requests.Timeout('TEST_TOKEN')
        for _ in range(2):
            with self.assertRaises(RestFailure): self.rest.quote('ALPHA', 'ND')
        self.assertEqual(self.http.post.call_count, CONFIG.rest_retries+1)
        self.assertNotIn('TEST_TOKEN', str(self.raw.call_args_list))

    def test_successful_retry_clears_failure(self):
        self.http.post.side_effect = [requests.ConnectionError(), response(cur_prc='10')]
        self.rest.quote('ALPHA', 'ND')
        self.assertFalse(self.rest.failures)

    def test_unknown_parse_not_retried_or_deterministic(self):
        self.http.post.return_value = Mock(status_code=200, json=Mock(side_effect=ValueError('TEST_SECRET')))
        with self.assertRaises(RestFailure) as cm: self.rest.quote('ALPHA', 'ND')
        self.assertEqual(cm.exception.failure_class, 'unclassified')
        self.assertEqual(cm.exception.details['category'], 'parse_error')
        self.assertEqual(self.http.post.call_count, 1)

    def test_no_data_initial_chart_is_deterministic(self):
        self.http.post.return_value = response(result_list=[])
        with self.assertRaises(RestFailure) as cm: self.rest.minutes_page('ALPHA', 'ND', SESSION.date)
        self.assertEqual(cm.exception.details['category'], 'no_data')
        counts = next(iter(self.counters.drain().values()))
        self.assertEqual(counts.get('api_error_count', 0), 0)

    def test_empty_continuation_is_successful_end(self):
        self.http.post.return_value = response(result_list=[])
        self.assertEqual(self.rest.minutes_page('ALPHA', 'ND', SESSION.date, 'public-cursor')[0]['result_list'], [])

    def test_public_message_sanitized_and_counted_once(self):
        self.http.post.return_value = response(7, message='[1903:TEST_SECRET TEST_TOKEN TEST_KEY]')
        with self.assertRaises(RestFailure): self.rest.quote('ALPHA', 'ND')
        details = self.raw.call_args.args[-1]
        self.assertNotIn('TEST_SECRET', json.dumps(details))
        counts = next(iter(self.counters.drain().values()))
        self.assertEqual(counts['api_error_count'], 1)
        self.assertEqual(counts['rest_deterministic'], 1)

    def test_stop_during_request_prevents_retry(self):
        self.rest.stop = threading.Event()
        def request(*a, **kw):
            self.rest.stop.set()
            raise requests.Timeout()
        self.http.post.side_effect = request
        with self.assertRaises(StopRequested): self.rest.quote('ALPHA', 'ND')
        self.assertEqual(self.http.post.call_count, 1)

    def test_stop_before_request_or_rate_wait(self):
        stop = threading.Event(); stop.set(); self.rest.stop = stop
        with self.assertRaises(StopRequested): self.rest.quote('ALPHA', 'ND')
        self.http.post.assert_not_called()
        with self.assertRaises(StopRequested): RateLimiter(CONFIG, stop).acquire('usa10099')

    def test_stop_interrupts_retry_sleep(self):
        stop = threading.Event(); self.rest.stop = stop
        self.http.post.side_effect = requests.Timeout()
        timer = threading.Timer(.05, stop.set); timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(StopRequested): self.rest.quote('ALPHA', 'ND')
        finally:
            timer.join()
        self.assertLess(time.monotonic()-started, 1)
        self.assertEqual(self.http.post.call_count, 1)


class SamplingTests(unittest.TestCase):
    def check_samples(self, offsets, expected, selection='post_target', horizon='30s', grace=.25):
        with tempfile.TemporaryDirectory() as tmp:
            config = replace(CONFIG, data_dir=Path(tmp), result_pre_target_grace_seconds=grace)
            store = RadarStore(config.db_path, {'provider': 'kiwoom', 'test_version': CONFIG.test_version})
            try:
                engine = RadarEngine(store, config, 'kiwoom', 'live')
                initial = observation()
                engine.process(MarketBatch(initial.asof_utc, (initial,), (SESSION,)), initial.asof_utc)
                target = instant(initial.asof_utc)+timedelta(seconds=HORIZONS[horizon])
                for index, offset in enumerate(offsets):
                    at = utc(target+timedelta(seconds=offset))
                    o = replace(initial, asof_utc=at, price=201+index)
                    engine.process(MarketBatch(at, (o,), (SESSION,)), at)
                at = utc(target+timedelta(seconds=31))
                engine.process(MarketBatch(at, (), (SESSION,)), at)
                row = dict(store.db.execute('SELECT * FROM radar_results WHERE horizon=?', (horizon,)).fetchone())
                if expected is None:
                    self.assertEqual(row['status'], 'missing')
                    self.assertIsNone(row['sample_timestamp'])
                else:
                    self.assertEqual(row['status'], 'complete')
                    self.assertEqual(row['sample_offset_seconds'], expected)
                    self.assertEqual(row['sample_selection'], selection)
                    self.assertEqual(row['sample_timestamp'], row['observed_utc'])
                    self.assertEqual(row['timing_error_seconds'], expected)
                    folder = store.export_day(SESSION.date, Path(tmp)/'export')
                    self.assertIn('sample_selection', (folder/'results.csv').read_text(encoding='utf-8-sig'))
                    summary = daily_summary(store, SESSION.date)
                    self.assertGreater(summary['result_sampling']['pre_target_grace_used_count' if selection == 'pre_target_grace' else 'post_target_count'], 0)
            finally:
                store.close()

    def test_a_post_target(self): self.check_samples([5], 5)
    def test_b_three_ms_fallback(self): self.check_samples([-.003, 30.020], -.003, 'pre_target_grace')
    def test_c_grace_exceeded(self): self.check_samples([-.300, 30.020], None)
    def test_d_post_target_priority(self): self.check_samples([-.010, 10], 10)
    def test_e_thirty_second_tolerance_unchanged(self): self.check_samples([30.001], None)
    def test_f_exact_target(self): self.check_samples([0], 0)
    def test_grace_config_disabled(self): self.check_samples([-.003, 30.020], None, grace=0)
    def test_grace_exact_boundary(self): self.check_samples([-.25, 30.020], -.25, 'pre_target_grace')
    def test_other_intraday_horizons(self):
        for horizon in list(HORIZONS)[1:]:
            with self.subTest(horizon=horizon): self.check_samples([-.003, 30.020], -.003, 'pre_target_grace', horizon)


class ShutdownQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = replace(CONFIG, data_dir=Path(self.tmp.name), snapshot_seconds=.01)
        self.provider = KiwoomProvider(self.config)
        self.provider.cache = KiwoomCache(Path(self.tmp.name)/'cache.db')
        self.provider.rest, self.provider.ws = Mock(), Mock()
        self.counters = QualityCounters()
        self.logger = logging.Logger('cancel-test')
        self.logger.addHandler(QualityLogHandler(self.counters))
        self.provider.set_logger(self.logger)

    def tearDown(self):
        self.provider.close()
        self.tmp.cleanup()

    def test_no_enqueue_after_stop(self):
        self.provider.request_stop()
        self.provider._chart_step('TEST', 'ND', SESSION, True, instant(SESSION.close_utc))
        self.assertFalse(self.provider.jobs)
        self.provider.rest.minutes_page.assert_not_called()

    def test_pagination_cancel_pending_and_idempotent_close(self):
        p = self.provider
        p.rest.minutes_page.return_value = ({'result_list': raw_bars(minutes=3)[1:]}, 'next', SESSION.close_utc)
        p._chart_step('TEST', 'ND', SESSION, True, instant(SESSION.close_utc))
        self.assertEqual(len(p.jobs), 1)
        p.request_stop()
        p._chart_step('TEST', 'ND', SESSION, True, instant(SESSION.close_utc))
        self.assertEqual(p.rest.minutes_page.call_count, 1)
        p.close(); p.close()
        self.assertEqual(p.cancelled_backfill_tasks, 1)
        p.ws.close.assert_called_once(); p.rest.close.assert_called_once()
        self.assertFalse(p.jobs)

    def test_universe_and_subscriptions_stop(self):
        p = self.provider; p.request_stop()
        with self.assertRaises(StopRequested): p._refresh_universe()
        p._select_symbols(datetime.now(timezone.utc))
        p.ws.set_symbols.assert_not_called(); p.rest.request.assert_not_called()
        ws = KiwoomWebSocket(p.rest, self.config, p.stop_event, Mock(), self.logger)
        ws.desired = {'TEST': 'ND'}
        socket = Mock(); ws.sync_symbols(socket); socket.send.assert_not_called()

    def test_incomplete_is_quality_not_api_error(self):
        p = self.provider
        p.rest.minutes_page.return_value = ({'result_list': raw_bars(minutes=1)}, None, SESSION.close_utc)
        p._chart_step('TEST', 'ND', SESSION, True, instant(SESSION.close_utc))
        counts = next(iter(self.counters.drain().values()))
        self.assertEqual(counts['backfill_incomplete'], 1)
        self.assertEqual(counts.get('api_error_count', 0), 0)
        self.assertEqual(counts.get('worker_error_count', 0), 0)

    def test_api_failure_and_backfill_unavailable_separate(self):
        p = self.provider
        p.rest = KiwoomRest(self.config, FakeClock(), self.logger, session=Mock())
        p.rest._token = 'TEST_TOKEN'; p.rest.limiter = Mock()
        p.rest.http.post.return_value = response(7, message='[1903:absent]')
        p._chart_step('TEST', 'ND', SESSION, True, instant(SESSION.close_utc))
        counts = next(iter(self.counters.drain().values()))
        self.assertEqual(counts['api_error_count'], 1)
        self.assertEqual(counts['backfill_deterministic_unavailable'], 1)
        self.assertEqual(counts.get('worker_error_count', 0), 0)

    def test_bounded_active_request_shutdown_and_final_export(self):
        entered, release = threading.Event(), threading.Event()
        p = self.provider
        def connect():
            def io():
                entered.set()
                release.wait(1)  # bounded fake in-flight network call
            p.io_thread = threading.Thread(target=io)
            p.io_thread.start()
        p.connect = connect
        o = observation()
        p.fetch = Mock(return_value=MarketBatch(o.asof_utc, (o,), (SESSION,)))
        config = replace(self.config, max_data_delay_seconds=1e9)
        worker = RadarWorker(p, config)
        worker.start(); self.assertTrue(entered.wait(2))
        deadline = time.monotonic()+2
        while worker._last_rows is None and time.monotonic() < deadline: time.sleep(.01)
        p.jobs.update({('T'+str(i), 'ND', SESSION.date, True): {} for i in range(300)})
        start = time.monotonic()
        worker.stop(reason='KeyboardInterrupt'); worker.stop(reason='KeyboardInterrupt')
        release.set(); worker.thread.join(timeout=3)
        elapsed = time.monotonic()-start
        self.assertFalse(worker.running)
        self.assertLess(elapsed, 3)
        self.assertIsNone(worker.error)
        self.assertEqual(p.cancelled_backfill_tasks, 300)
        logs = '\n'.join(f.read_text(encoding='utf-8') for f in Path(self.tmp.name).glob('logs/*.log'))
        phases = ['shutdown_requested', 'shutdown_stop_scheduling', 'shutdown_backfill_cancelled',
                  'shutdown_ws_closing', 'shutdown_export_start', 'shutdown_export_complete', 'shutdown_complete', 'program_stop']
        positions = [logs.index(phase) for phase in phases]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('Traceback', logs)
        with sqlite3.connect(config.db_path) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        db.close()
        summaries = [json.loads(f.read_text()) for f in Path(self.tmp.name).glob('export/*/quality_summary.json')]
        self.assertTrue(summaries)
        self.assertEqual(sum(s['shutdown']['requested_count'] for s in summaries), 2)
        self.assertEqual(sum(s['shutdown']['cancelled_backfill_tasks'] for s in summaries), 300)
        print('mock_shutdown_elapsed_ms=%.3f' % (elapsed*1000))

    def test_actual_keyboardinterrupt_handler_and_repeated_signal(self):
        worker = Mock()
        worker.stop_event = threading.Event()
        worker.running = True
        def stop(reason): worker.stop_event.set()
        worker.stop.side_effect = stop
        calls = []
        def sleep(_):
            calls.append(1)
            if len(calls) == 1: signal.raise_signal(signal.SIGINT)
            else:
                signal.raise_signal(signal.SIGINT)
                worker.running = False
        with patch('market_radar.console.time.sleep', side_effect=sleep):
            collect_until_stopped(worker)
        self.assertEqual(worker.stop.call_count, 2)
        self.assertTrue(all(c.kwargs['reason'] == 'KeyboardInterrupt' for c in worker.stop.call_args_list))

    def test_schema_three_refused_without_mutation(self):
        path = Path(self.tmp.name)/'legacy.db'
        store = RadarStore(path, {'provider':'kiwoom', 'test_version':'kiwoom_radar_v2'})
        self.assertEqual(store.schema_version, 3); store.close()
        original = path.read_bytes()
        with self.assertRaises(ValueError): RadarStore(path, {'provider':'kiwoom', 'test_version':CONFIG.test_version})
        self.assertEqual(path.read_bytes(), original)

    def test_nested_ctrl_c_during_stop_is_ignored(self):
        worker = Mock()
        worker.stop_event = threading.Event()
        worker.running = True
        def stop(reason):
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            worker.stop_event.set()
            worker.running = False
        worker.stop.side_effect = stop
        with patch('market_radar.console.time.sleep', side_effect=lambda _: signal.raise_signal(signal.SIGINT)):
            collect_until_stopped(worker)
        worker.stop.assert_called_once_with(reason='KeyboardInterrupt')

    def test_provider_close_failure_is_reported_with_export_preserved(self):
        p = Mock(); p.name = 'kiwoom'; p.mode = 'live'
        p.fetch.side_effect = StopIteration()
        p.close.side_effect = RuntimeError('bounded close failure')
        worker = RadarWorker(p, replace(self.config, data_dir=Path(self.tmp.name)/'failed-close'))
        worker._run()
        self.assertIsNotNone(worker.error)
        self.assertTrue(list(worker.config.data_dir.glob('export/*/quality_summary.json')))

    def test_rest_summary_top_ten_and_last_duration(self):
        store = RadarStore(self.config.db_path, {'provider':'kiwoom', 'test_version':CONFIG.test_version})
        try:
            for i in range(20): self.counters.add('rest_dimension:ticker:T'+str(i), i+1, SESSION.date)
            self.counters.set_gauge('shutdown_elapsed_ms', 5, SESSION.date); self.counters.persist(store)
            self.counters.set_gauge('shutdown_elapsed_ms', 7, SESSION.date); self.counters.persist(store)
            summary = daily_summary(store, SESSION.date)
            self.assertEqual(len(summary['rest_errors']['top_tickers']), 10)
            self.assertEqual(summary['shutdown']['last_shutdown_elapsed_ms'], 7)
        finally:
            store.close()


if __name__ == '__main__':
    unittest.main()
