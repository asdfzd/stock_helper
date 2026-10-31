"""Family executable routing and preopen behavior; no live IO or OS power."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from tools import family_collect as entry
from market_radar.family_config import FamilyBlocked, FamilyConfig
from market_radar.family_mode import family_plan
from market_radar.family_ui import family_phase
from market_radar.scheduling import SessionSchedule
from tools.one_hour_runner.core import runner_config
from tools.one_hour_runner.system import RunBlocked


class EntryTests(unittest.TestCase):
    def test_frozen_no_arguments_uses_only_adjacent_config(self):
        exe = Path(tempfile.gettempdir())/'package'/'MarketRadarFamily.exe'
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)):
            self.assertEqual(entry.launch_mode(), (True, exe.parent/'family_config.json'))
            self.assertEqual(entry.launch_mode(manual=True), (False, None))
            explicit = exe.parent/'explicit.json'
            self.assertEqual(entry.launch_mode(family=True, config=explicit), (True, explicit))

    def test_source_and_other_frozen_apps_keep_manual_default(self):
        for frozen, exe in ((False, 'python.exe'), (True, 'StockHelper.exe')):
            with self.subTest(exe=exe), patch.object(sys, 'frozen', frozen, create=True), patch.object(sys, 'executable', exe):
                self.assertEqual(entry.launch_mode(), (False, None))

    def test_actual_main_routes_frozen_default_and_manual(self):
        exe = Path(tempfile.gettempdir())/'package'/'MarketRadarFamily.exe'
        cfg = Mock()
        app = Mock()
        app.exec.return_value = 0
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(exe)), \
                patch('PySide6.QtWidgets.QApplication.instance', return_value=app), \
                patch.object(FamilyConfig, 'load', return_value=cfg) as load, patch.object(entry, 'create_window') as create:
            self.assertEqual(entry.main([]), 0)
            load.assert_called_once_with(exe.parent/'family_config.json', dry_run=False)
            cfg.bind_runtime.assert_called_once()
            create.assert_called_once_with(True, cfg)
            load.reset_mock(); create.reset_mock()
            entry.main(['--manual'])
            create.assert_called_once_with(False, None)
            load.assert_not_called()

    def test_missing_config_is_persistent_error_without_dashboard(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'MarketRadarFamily.exe'), \
                patch('PySide6.QtWidgets.QApplication.instance', return_value=Mock()), \
                patch.object(FamilyConfig, 'load', side_effect=FamilyBlocked('family_config_invalid')), \
                patch.object(entry, 'create_window') as create, patch.object(entry, 'startup_error') as error:
            entry.main([])
            create.assert_not_called()
            self.assertEqual(error.call_args.args[0], 'family_config_invalid')
            error.return_value.show.assert_called_once()

    def test_helpers_never_fall_through_to_family(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', 'MarketRadarFamily.exe'), \
                patch.object(entry, 'create_window', side_effect=AssertionError('unexpected GUI')), \
                patch.object(FamilyConfig, 'load', side_effect=AssertionError('unexpected config')), \
                patch('market_radar.watchdog.Watchdog') as wd:
            self.assertEqual(entry.main(['--watchdog-run','synthetic.json','--nonce','test','--watchdog-dry-run']),0)
            wd.return_value.fire.assert_called_once_with('test',force_dry_run=True)
            self.assertEqual(entry.main(['--watchdog-cancel','synthetic.json']),0)
            wd.return_value.cancel.assert_called_once_with('explicit_cli_cancel')

    def test_smoke_and_probe_dispatch_precede_family(self):
        with patch.object(entry,'create_window',side_effect=AssertionError('unexpected GUI')), \
                patch('tools.family_smoke.run',return_value=0) as smoke, \
                patch('tools.family_smoke.probe_action',return_value=0) as probe:
            self.assertEqual(entry.main(['--offline-family-smoke','scratch','--smoke-case','manual']),0)
            smoke.assert_called_once_with(Path('scratch'),case='manual')
            self.assertEqual(entry.main(['--scheduler-probe','probe.json','--probe-nonce','test']),0)
            probe.assert_called_once_with(Path('probe.json'),'test')

    def test_orphan_helper_flags_cannot_arm_collection(self):
        with patch.object(entry,'create_window',side_effect=AssertionError('unexpected GUI')), patch('sys.stderr',Mock()):
            for args in (['--watchdog-dry-run'],['--nonce','test'],['--probe-nonce','test'],
                         ['--smoke-case','manual'],['--family','--manual'],['--audit-nonce','test']):
                with self.subTest(args=args), self.assertRaises(SystemExit) as stopped:
                    entry.main(args)
                self.assertEqual(stopped.exception.code,2)

    def test_audit_helper_precedes_config_gui_and_collection(self):
        with patch.object(entry,'create_window',side_effect=AssertionError('GUI')), \
                patch.object(FamilyConfig,'load',side_effect=AssertionError('config')), \
                patch('market_radar.audit_task.run_helper',return_value=0) as run:
            self.assertEqual(entry.main(['--audit-run','synthetic/task.json','--audit-nonce','a'*32]),0)
            run.assert_called_once_with(Path('synthetic/task.json'),'a'*32)


class ConfigAndScheduleTests(unittest.TestCase):
    def test_invalid_json_shapes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'family_config.json'
            for value in ([],None,True,'secret-like-content',{'config_version':1}):
                p.write_text(json.dumps(value),encoding='utf-8')
                with self.subTest(value=value), self.assertRaisesRegex(FamilyBlocked,'^family_config_invalid$'):
                    FamilyConfig.load(p)
            p.write_bytes(b'\xff\xfe')
            with self.assertRaisesRegex(FamilyBlocked,'^family_config_invalid$'): FamilyConfig.load(p)

    def test_next_week_early_launch_waits_for_xnys_and_midnight_stop(self):
        root=Path(tempfile.gettempdir())
        cfg=FamilyConfig(root,root,root/'unused.db')
        kst=ZoneInfo('Asia/Seoul')
        for day in range(2,7):
            for hour,minute in ((18,0),(22,0),(22,29)):
                at=datetime(2026,11,day,hour,minute,tzinfo=kst)
                clock=SimpleNamespace(now=lambda:at,monotonic=lambda:1000.)
                plan=family_plan(cfg,clock,None)
                opened=datetime.fromisoformat(plan['open']).astimezone(kst)
                self.assertEqual(opened,datetime(2026,11,day,23,30,tzinfo=kst))
                self.assertEqual(datetime.fromisoformat(plan['stop']).astimezone(kst),datetime(2026,11,day+1,0,35,tzinfo=kst))
                self.assertEqual((opened+timedelta(minutes=71)).strftime('%H:%M'),'00:41')
                self.assertEqual((opened+timedelta(minutes=90)).strftime('%H:%M'),'01:00')
                schedule=SessionSchedule(runner_config(root, cfg.cache),None,clock)
                self.assertLess(clock.now(),schedule.open)
                self.assertIsNone(schedule.started_mono)

    def test_midnight_restart_is_late_same_us_session(self):
        root=Path(tempfile.gettempdir())
        clock=SimpleNamespace(now=lambda:datetime(2026,11,3,0,1,tzinfo=ZoneInfo('Asia/Seoul')),monotonic=lambda:1000.)
        with self.assertRaisesRegex(RunBlocked,'late_launch'):
            family_plan(FamilyConfig(root,root,root/'unused.db'),clock,None)


class FamilyStatusTests(unittest.TestCase):
    def runner(self, **values):
        return SimpleNamespace(**dict(dict(state='ARMED',watchdog_error=None,shutdown_may_be_pending=False,
            finalized=False,watchdog=SimpleNamespace(view={'status':'ARMED'})),**values))

    def test_reserved_requires_watchdog_and_is_not_auth_success(self):
        self.assertIn('예약 완료',family_phase(self.runner(),'PREOPEN_WAIT'))
        self.assertNotIn('성공',family_phase(self.runner(),'PREOPEN_WAIT'))
        self.assertNotIn('예약 완료',family_phase(self.runner(watchdog=None),'PREOPEN_WAIT'))

    def test_live_finalization_shutdown_and_failure_labels(self):
        self.assertEqual(family_phase(self.runner(state='COLLECTING'),'REGULAR_LIVE'),'수집 중')
        self.assertEqual(family_phase(self.runner(state='FINALIZING'),'FINALIZING'),'후처리 중')
        self.assertEqual(family_phase(self.runner(state='WAITING_SHUTDOWN'),'STOPPED'),'종료 대기')
        self.assertIn('오류',family_phase(self.runner(state='ERROR',finalized=True),'STOPPED'))
        self.assertIn('오류',family_phase(self.runner(watchdog_error='state_failed'),'PREOPEN_WAIT'))
        self.assertIn('오류',family_phase(self.runner(watchdog=SimpleNamespace(view={'status':'STATE_UNKNOWN'})),'PREOPEN_WAIT'))
