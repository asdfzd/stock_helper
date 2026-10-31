"""Offline watchdog/launcher boundaries; all OS submissions injected."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from market_radar.family_config import FamilyConfig, FamilyBlocked
from market_radar.family_mode import FamilyRunner, family_plan
from market_radar.watchdog import Watchdog, WatchdogError, read, save, task_xml, verify_task
from tools.family_smoke import MemoryScheduler
from tools.one_hour_runner.dry_run import SyntheticController
from tools.one_hour_runner.system import RunBlocked
from tests.one_hour_runner_test import Clock
from tests import one_hour_runner_test as original


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clock = Clock('2026-10-27T12:50:00+00:00')
        self.scheduler = MemoryScheduler()
        self.w = Watchdog(self.root / 'watchdog.json', self.scheduler, now=self.clock.now, uptime=self.clock.monotonic)
        self.plan = {'open': '2026-10-27T13:30:00+00:00', 'session_date': '2026-10-27'}
        self.calls = []

    def arm(self, allowed=False):
        return self.w.arm(self.plan, 'a'*32, ['C:/safe/MarketRadarFamily.exe'], emergency_allowed=allowed)

    def due(self):
        self.clock.advance((__import__('datetime').datetime.fromisoformat(self.w.view['deadline'])-self.clock.now()).total_seconds())

    def fire(self):
        return self.w.fire(self.w.view['nonce'], submit=lambda: self.calls.append(self.clock.now().isoformat()))

    def test_deadline_is_open_plus_90_not_launch_plus_90(self):
        s = self.arm()
        self.assertEqual(s['deadline'], '2026-10-27T15:00:00+00:00')
        self.assertEqual(s['uptime_due'], 130*60)
        self.assertFalse(s['emergency_allowed'])

    def test_dry_run_no_shutdown_and_no_verified_without_zip(self):
        self.arm(); self.due()
        self.assertEqual(self.fire(), 'DRY_RUN_FIRED')
        self.assertEqual(self.calls, [])
        self.assertEqual(read(self.w.path)['preservation'], 'INCOMPLETE')

    def test_explicit_permission_calls_once(self):
        self.arm(True); self.due()
        self.assertEqual(self.fire(), 'EMERGENCY_REQUEST_ACCEPTED')
        self.fire()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read(self.w.path)['emergency_result'], 'EMERGENCY_FORCED')

    def test_before_due_both_clocks(self):
        self.arm(True)
        self.assertEqual(self.fire(), 'TOO_EARLY')
        self.clock.wall += timedelta(minutes=130)
        self.assertEqual(self.fire(), 'TOO_EARLY')
        self.assertFalse(self.calls)

    def test_reboot_rejected(self):
        self.clock.mono = 10000
        self.arm(True); self.due()
        self.clock.mono = 1
        self.assertEqual(self.fire(), 'EXPIRED')
        self.assertFalse(self.calls)

    def test_previous_date_expired(self):
        self.arm(True); self.due(); self.clock.advance(86400)
        self.assertEqual(self.fire(), 'EXPIRED')
        self.assertFalse(self.calls)

    def test_wrong_nonce_rejected(self):
        self.arm(True); self.due()
        with self.assertRaises(WatchdogError): self.w.fire('b'*32, submit=lambda: self.calls.append('bad'))
        self.assertFalse(self.calls)

    def test_wrong_run_date_rejected(self):
        self.arm(True); self.due()
        s = read(self.w.path); s['session_date'] = '2026-10-28'; save(self.w.path, s)
        with self.assertRaises(WatchdogError): self.fire()

    def test_tampered_deadline_rejected(self):
        self.arm(True)
        s = read(self.w.path); s['deadline'] = s['open']; save(self.w.path, s)
        with self.assertRaises(WatchdogError): self.fire()

    def test_cancel_removes_only_own_task(self):
        self.scheduler.tasks['OtherSoftware'] = 'unchanged'
        self.arm(True); self.w.cancel(); self.due()
        self.assertEqual(self.fire(), 'CANCELLED')
        self.assertEqual(self.scheduler.tasks, {'OtherSoftware': 'unchanged'})
        self.assertFalse(self.calls)

    def test_cancel_failure_durable_revocation(self):
        self.arm(True)
        with patch.object(self.scheduler, 'delete', side_effect=OSError):
            with self.assertRaises(WatchdogError): self.w.cancel()
        self.due()
        self.assertEqual(self.fire(), 'CANCELLED')
        self.assertIn('cancel_failed', read(self.w.path)['error'])

    def test_registration_failure_never_armed(self):
        with patch.object(self.scheduler, 'register', side_effect=WatchdogError('denied')):
            with self.assertRaises(WatchdogError): self.arm(True)
        self.assertEqual(read(self.w.path)['status'], 'REGISTRATION_FAILED')

    def test_existing_task_not_overwritten(self):
        name = 'MarketRadarFamily_20261027_' + 'a'*32
        self.scheduler.tasks[name] = 'foreign'
        with self.assertRaises(WatchdogError): self.arm()
        self.assertEqual(self.scheduler.tasks[name], 'foreign')

    def test_normal_removes_before_submission(self):
        self.arm(True)
        def submit():
            self.assertFalse(self.scheduler.tasks)
            self.assertEqual(read(self.w.path)['status'], 'NORMAL_REQUESTING')
            self.calls.append('normal')
        self.w.normal_request(submit)
        self.due()
        self.assertEqual(self.fire(), 'NORMAL_REQUEST_ACCEPTED')
        self.assertEqual(self.calls, ['normal'])

    def test_normal_delete_failure_blocks_submission(self):
        self.arm(True)
        with patch.object(self.scheduler, 'delete', side_effect=WatchdogError('denied')):
            with self.assertRaises(WatchdogError): self.w.normal_request(lambda: self.calls.append('normal'))
        self.assertFalse(self.calls)
        self.assertEqual(read(self.w.path)['status'], 'ARMED')

    def test_normal_request_failure_not_poweroff_success(self):
        self.arm(True)
        with self.assertRaises(OSError): self.w.normal_request(Mock(side_effect=OSError))
        self.assertEqual(read(self.w.path)['status'], 'NORMAL_REQUEST_FAILED')

    def test_unknown_task_blocks_emergency(self):
        self.arm(True); self.due()
        self.scheduler.tasks.clear()
        with self.assertRaises(WatchdogError): self.fire()
        self.assertFalse(self.calls)

    def test_foreign_xml_never_deleted(self):
        self.arm(True)
        name = self.w.view['task_name']
        self.scheduler.tasks[name] = self.scheduler.tasks[name].replace(self.w.view['owner'], 'foreign')
        with self.assertRaises(WatchdogError): self.w.cancel()
        self.assertIn(name, self.scheduler.tasks)

    def test_xml_no_repeat_no_missed_start_has_expiry(self):
        s = self.arm()
        xml = task_xml(s); verify_task(xml, s)
        self.assertIn('<StartWhenAvailable>false</StartWhenAvailable>', xml)
        self.assertIn('<DeleteExpiredTaskAfter>PT1H</DeleteExpiredTaskAfter>', xml)
        self.assertNotIn('Repetition', xml)
        with self.assertRaises(WatchdogError): verify_task(xml.replace('<StartWhenAvailable>false', '<StartWhenAvailable>true'), s)

    def test_dead_process_detected(self):
        self.arm(); self.due()
        import psutil
        with patch('psutil.Process', side_effect=psutil.NoSuchProcess(123)):
            self.fire()
        self.assertEqual(read(self.w.path)['reason'], 'process_not_running')

    def test_gui_hang_detected_independently(self):
        self.arm(); self.due()
        self.w.heartbeat('FINALIZING', '2026-10-27T13:30:00+00:00')
        with patch('psutil.Process', return_value=Mock(create_time=lambda: 0)):
            self.fire()
        self.assertEqual(read(self.w.path)['reason'], 'gui_unresponsive')

    def test_postprocessing_hang_detected(self):
        self.arm(); self.w.heartbeat('FINALIZING', self.clock.now().isoformat()); self.due()
        with patch('psutil.Process', return_value=Mock(create_time=lambda: 0)):
            self.fire()
        self.assertEqual(read(self.w.path)['reason'], 'supervisor_unresponsive')

    def test_reused_pid_is_not_the_registered_process(self):
        self.arm(); self.due()
        with patch('psutil.Process', return_value=Mock(create_time=lambda: self.clock.now().timestamp())):
            self.fire()
        self.assertEqual(read(self.w.path)['reason'], 'process_not_running')

    def test_cancel_emergency_race_serialized(self):
        self.arm(True); self.due()
        started, release = threading.Event(), threading.Event()
        def submit(): started.set(); release.wait(2); self.calls.append('emergency')
        thread = threading.Thread(target=lambda: self.w.fire(self.w.view['nonce'], submit=submit))
        thread.start(); self.assertTrue(started.wait(2))
        cancel = threading.Thread(target=self.w.cancel); cancel.start()
        time.sleep(.05); release.set(); thread.join(3); cancel.join(3)
        self.assertEqual(self.calls, ['emergency'])
        self.assertEqual(read(self.w.path)['error'], 'emergency_already_submitted')

    def test_evidence_flush_failure_prevents_shutdown(self):
        self.arm(True); self.due()
        with patch('market_radar.watchdog.save', side_effect=OSError):
            with self.assertRaises(OSError): self.fire()
        self.assertFalse(self.calls)

    def test_cli_dry_run_cannot_be_overridden_by_state(self):
        self.arm(True); self.due()
        result = self.w.fire(self.w.view['nonce'], submit=lambda: self.calls.append('bad'), force_dry_run=True)
        self.assertEqual(result, 'DRY_RUN_FIRED')
        self.assertFalse(self.calls)

    def test_boot_counter_rejects_new_boot_even_with_larger_uptime(self):
        self.arm(True); self.due()
        self.w.boot = lambda: -1
        self.assertEqual(self.fire(), 'EXPIRED')
        self.assertFalse(self.calls)

    def test_heartbeat_observes_external_dry_run_result(self):
        self.arm(); self.due(); self.fire()
        self.w.view = {'status': 'ARMED'}
        self.w.heartbeat('FINALIZING', self.clock.now().isoformat())
        self.assertEqual(self.w.view['status'], 'DRY_RUN_FIRED')

    def test_no_zip_never_preserves_verified_claim(self):
        self.arm(); self.due()
        self.w.heartbeat('FINALIZING', self.clock.now().isoformat(), 'VERIFIED', archive=self.root/'absent.zip')
        self.fire()
        self.assertEqual(read(self.w.path)['preservation'], 'INCOMPLETE')

    def test_normal_can_finish_after_dry_run_deadline(self):
        self.arm(); self.due(); self.fire()
        self.w.normal_request(lambda: self.calls.append('normal'))
        self.assertEqual(self.calls, ['normal'])

    def test_expiry_during_task_query_blocks_final_os_boundary(self):
        self.arm(True); self.due()
        query = self.scheduler.query
        def delayed(state):
            result = query(state); self.clock.advance(301); return result
        with patch.object(self.scheduler, 'query', side_effect=delayed):
            self.assertEqual(self.fire(), 'EMERGENCY_BLOCKED')
        self.assertFalse(self.calls)

    def test_independent_invocation_error_visible_after_supervisor_exit(self):
        self.arm()
        save(self.w.path.with_name('watchdog_invocation_error.json'), {'error': 'WatchdogError'})
        self.w.refresh()
        self.assertEqual(self.w.view['status'], 'INVOCATION_FAILED_OR_UNKNOWN')

    def test_unreadable_state_never_displayed_as_armed(self):
        self.arm()
        with patch('market_radar.watchdog.read', side_effect=OSError):
            self.w.refresh()
        self.assertEqual(self.w.view['status'], 'STATE_UNKNOWN')

    def test_transient_windows_replace_denial_retries_atomically(self):
        import os
        original_replace = os.replace
        denied = PermissionError('synthetic sharing denial'); denied.winerror = 5
        calls = []
        def replace_once(source, destination):
            calls.append(1)
            if len(calls) == 1:
                raise denied
            return original_replace(source, destination)
        with patch('market_radar.watchdog.os.replace', side_effect=replace_once):
            save(self.root/'probe.json', {'value': 'new'})
        self.assertEqual(len(calls), 2)
        self.assertEqual(read(self.root/'probe.json'), {'value': 'new'})

    def test_permanent_replace_denial_keeps_old_journal(self):
        path = self.root/'probe.json'; save(path, {'value': 'old'})
        denied = PermissionError('synthetic permanent denial'); denied.winerror = 5
        with patch('market_radar.watchdog.os.replace', side_effect=denied) as replace_call:
            with self.assertRaises(PermissionError): save(path, {'value': 'new'})
        self.assertEqual(replace_call.call_count, 4)
        self.assertEqual(read(path), {'value': 'old'})


class FamilyPlanTests(unittest.TestCase):
    def setUp(self):
        self.cfg = FamilyConfig(Path.cwd(), Path.cwd()/'data', Path.cwd()/'unused-cache')

    def test_holiday_weekend_after_close_rejected(self):
        for at in ('2026-12-25T10:00:00+00:00', '2026-10-31T10:00:00+00:00', '2026-10-27T21:00:00+00:00'):
            with self.subTest(at=at), self.assertRaises(FamilyBlocked): family_plan(self.cfg, Clock(at), None)

    def test_after_open_rejected(self):
        with self.assertRaises(RunBlocked): family_plan(self.cfg, Clock('2026-10-27T13:30:00+00:00'), None)

    def test_dst_and_early_close(self):
        for at, hour in [('2026-10-30T10:00:00+00:00', 13), ('2026-11-02T10:00:00+00:00', 14), ('2026-11-27T10:00:00+00:00', 14)]:
            p = family_plan(self.cfg, Clock(at), None)
            self.assertEqual(__import__('datetime').datetime.fromisoformat(p['open']).hour, hour)
            if '11-27' in at: self.assertEqual(p['official_close'], '2026-11-27T18:00:00+00:00')


class FamilyPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.data = self.root/'data'; self.data.mkdir()
        self.cache = self.data/'cache.db'; self.cache.write_bytes(b'SQLite format 3\x00'+bytes(84))
        self.env = self.root/'.env'; self.env.write_text('KIWOOM_MODE=real\nAPP_KEY=synthetic\nAPP_SECRET=synthetic\n')
        self.cfg = FamilyConfig(self.root, self.data, self.cache, minimum_free_gib=0)

    def test_paths_permission_probe_without_changing_originals(self):
        before = [(p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (self.cache, self.env)]
        result = self.cfg.inspect_paths()
        self.assertEqual(result['authentication'], 'NOT_TESTED')
        self.assertEqual(result['shared_cache'], str(self.cache))
        self.assertEqual(before, [(p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (self.cache, self.env)])
        self.assertEqual(list(self.data.iterdir()), [self.cache])

    def test_missing_credentials_fails_without_values(self):
        self.env.unlink()
        with self.assertRaisesRegex(FamilyBlocked, 'credentials_file_missing'): self.cfg.inspect_paths()

    def test_incomplete_credentials_rejected(self):
        self.env.write_text('KIWOOM_MODE=real\nAPP_KEY=synthetic')
        with self.assertRaisesRegex(FamilyBlocked, 'credentials_missing'): self.cfg.inspect_paths()

    def test_missing_cache_never_created(self):
        self.cache.unlink()
        with self.assertRaisesRegex(FamilyBlocked, 'shared_cache_missing'): self.cfg.inspect_paths()
        self.assertFalse(self.cache.exists())

    def test_disk_space_failure(self):
        with patch('shutil.disk_usage', return_value=Mock(free=0)):
            with self.assertRaisesRegex(FamilyBlocked, 'insufficient_disk'): replace(self.cfg, minimum_free_gib=1).inspect_paths()

    def test_config_defaults_and_dry_run_revoke_both(self):
        path = self.root/'family.json'
        config = dict(config_version=1, project_root=str(self.root), data_root=str(self.data), cache=str(self.cache))
        path.write_text(json.dumps(config))
        loaded = FamilyConfig.load(path)
        self.assertFalse(loaded.normal_shutdown or loaded.emergency_shutdown)
        config.update(normal_shutdown=True, emergency_shutdown=True); path.write_text(json.dumps(config))
        loaded = FamilyConfig.load(path, dry_run=True)
        self.assertFalse(loaded.normal_shutdown or loaded.emergency_shutdown)


class FamilyEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.clock = Clock()
        self.cfg = FamilyConfig(self.root, self.root, self.root/'cache.db', normal_shutdown=True)
        self.scheduler = MemoryScheduler(); self.shutdown = Mock()
        self.c = SyntheticController(self.root/'master.db', clock=self.clock)
        self.r = FamilyRunner(self.c, self.root/'runs', self.cfg, clock=self.clock, guard=Mock(), power=Mock(), shutdown=self.shutdown,
            clock_check=lambda _: {'synthetic': True},
            watchdog_factory=lambda path: Watchdog(path, self.scheduler, now=self.clock.now, uptime=self.clock.monotonic))
        self.addCleanup(self.r.dispose)
        with patch.object(FamilyConfig, 'inspect_paths', return_value={'synthetic': True}):
            self.r.start(cache=self.cfg.cache)
        # The separate watchdog tests cover persistence; avoid thousands of fsyncs
        # while replaying each synthetic second through the unchanged pipeline.
        self.r.watchdog.heartbeat = Mock()

    prepare = original.FixtureTests.prepare

    def test_same_collector_65_archive_70_normal_delete_order(self):
        self.assertIs(self.r.controller, self.c)
        self.assertEqual(self.c.config.max_ws_symbols, 150)
        self.assertEqual(self.r.watchdog.view['status'], 'ARMED')
        self.prepare(); self.r.tick()
        self.assertEqual(self.r.summary['preservation_status'], 'VERIFIED')
        self.assertTrue(self.r.archive.is_file())
        self.clock.advance(299); self.r.tick(); self.shutdown.request.assert_not_called()
        self.clock.advance(1); self.r.tick(); self.shutdown.request.assert_called_once()
        self.assertEqual(self.r.watchdog.view['status'], 'NORMAL_REQUEST_ACCEPTED')
        self.assertFalse(self.scheduler.tasks)

    def test_shutdown_only_cancel_independent_of_hung_supervisor(self):
        self.r.request_cancel_shutdown()
        self.r.cancel_future.result(3)
        self.assertEqual(self.r.watchdog.view['status'], 'CANCELLED')
        self.assertTrue(self.c.busy)
        self.shutdown.request.assert_not_called()

    def test_collection_cancel_removes_watchdog(self):
        self.r.request_cancel(); self.r.cancel_future.result(3)
        self.assertEqual(self.r.watchdog.view['status'], 'CANCELLED')

    def test_auth_or_early_failure_cannot_claim_success(self):
        self.c.busy = False; self.c.state = 'ERROR'; self.c.worker = None
        self.r.tick()
        self.assertFalse(self.r.summary['shutdown_eligible'])
        self.assertEqual(self.r.watchdog.view['status'], 'CANCELLED')
        self.shutdown.request.assert_not_called()

    def test_duplicate_controller_rejected(self):
        with self.assertRaisesRegex(RunBlocked, 'collector_already_running'):
            self.r.start(cache=self.cfg.cache)
        # Only our actual helper entry can bypass legacy process detection;
        # adding a helper-like argument to another collector must not hide it.
        import os
        from market_radar.one_hour import DashboardGuard
        process = Mock(pid=os.getpid()+100000)
        process.name.return_value = 'python.exe'
        process.cmdline.return_value = ['python.exe', 'C:/old/live_ui.py', '--watchdog-run', 'unused']
        with patch('psutil.process_iter', return_value=[process]), patch('psutil.Process', return_value=Mock(parents=lambda: [])):
            with self.assertRaisesRegex(RunBlocked, 'other_collection_application'):
                DashboardGuard().check_legacy()
            process.cmdline.return_value = ['python.exe', 'C:/app/tools/family_collect.py', '--watchdog-run', 'unused']
            DashboardGuard().check_legacy()

    def test_registration_only_after_start_accepted(self):
        self.assertTrue(self.c.lifecycle['run_id'])
        self.assertEqual(self.r.watchdog.view['run_id'], self.c.lifecycle['run_id'])
