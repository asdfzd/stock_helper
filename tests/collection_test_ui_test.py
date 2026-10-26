"""Offline GUI views: shared controls, actual states, no additional SQLite polling."""
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings, QPoint, QEvent
from PySide6.QtWidgets import QApplication
from market_radar.config import CONFIG
from market_radar.collection_ui import read_diagnostics
from market_radar.one_hour import DashboardOneHour
from market_radar.run_diagnostics import RunDiagnostics
from market_radar.ui import RadarPanel
from tests.one_hour_runner_test import Clock


class CollectionViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root/'ui.ini'), QSettings.IniFormat)
        self.settings.setValue('data_root', str(self.root/'data'))
        self.panel = RadarPanel(master_path=self.root/'master.db', settings=self.settings)
        self.panel.timer.stop()
        self.drain()
        self.view = self.panel.collection_test
        self.c = self.panel.controller

    def drain(self):
        for future, _ in list(self.panel.jobs.values()): future.result(timeout=10)
        self.panel.poll()

    def tearDown(self):
        self.c.thread = None
        self.c.schedule = None
        self.c.worker = None
        self.c.paper = None
        self.panel.one_hour = None
        self.drain()
        self.panel.close()
        self.panel.executor.shutdown(wait=True)
        self.settings.sync()
        self.panel.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def runner(self):
        r = DashboardOneHour(self.c, self.root, clock=Clock(), power=Mock(), shutdown=Mock())
        r.folder = self.root/'run'
        r.folder.mkdir(exist_ok=True)
        r.summary = {}
        self.panel.one_hour = r
        return r

    def test_default_tab_existing_tabs_and_safe_defaults(self):
        p = self.panel
        self.assertIs(p.tabs.currentWidget(), self.view)
        names = [p.tabs.tabText(i) for i in range(p.tabs.count())]
        for name in ('Live Radar', 'Candidate History', 'Session History', 'Paper Trading', '수집 상세 설정'):
            index = names.index(name)
            p.tabs.setCurrentIndex(index)
            self.assertEqual(p.tabs.tabText(p.tabs.currentIndex()), name)
        self.assertEqual(self.view.max_ws.value(), 150)
        self.assertEqual(p.collection_mode.currentText(), 'Regular Session')
        self.assertFalse(p.one_hour_enable.isChecked())
        self.assertTrue(self.view.no_shutdown.isChecked())
        self.assertFalse(self.view.no_shutdown.isEnabled())
        self.assertFalse(self.view.paper.isChecked())
        self.assertIn('OFF', self.view.result.text())
        self.assertEqual(self.c.connection, dict(connected=False, active_ws=0))

    def test_bidirectional_settings_share_original_controls(self):
        v, p = self.view, self.panel
        v.max_ws.setValue(125)
        self.assertEqual(p.max_ws.value(), 125)
        p.max_ws.setValue(150)
        self.assertEqual(v.max_ws.value(), 150)
        v.rotation.setChecked(True)
        self.assertTrue(p.paper_panel.enable.isChecked())
        self.assertTrue(v.paper.isChecked())
        p.paper_panel.enable.setChecked(False)
        self.assertFalse(v.rotation.isChecked())
        v.opening.setChecked(True)
        self.assertTrue(p.opening_hour_enable.isChecked())

    def test_one_hour_selection_and_return_regular(self):
        v, p = self.view, self.panel
        v.select_mode(0)
        self.assertTrue(p.one_hour_enable.isChecked())
        self.assertTrue(all(x.isChecked() for x in (v.paper,v.rotation,v.opening)))
        self.assertEqual(v.max_ws.value(),150)
        self.assertFalse(v.max_ws.isEnabled())
        self.assertTrue(v.no_shutdown.isEnabled())
        v.select_mode(1)
        self.assertFalse(p.one_hour_enable.isChecked())
        self.assertEqual(p.collection_mode.currentText(),'Regular Session')
        self.assertTrue(v.max_ws.isEnabled())

    def test_regular_start_stop_and_duplicate_start_guard(self):
        p, c = self.panel, self.c
        previous = self.runner()
        def started(*args, **kwargs):
            c.thread = Mock(is_alive=lambda: True)
            c.state = 'RUNNING'
        with patch.object(c,'start',side_effect=started) as start, patch.object(c,'stop') as stop:
            self.view.start.click()
            self.view.start.click()
            start.assert_called_once()
            self.assertEqual(start.call_args.args[0].max_ws_symbols,150)
            self.assertIsNone(p.one_hour)
            self.view.stop.click()
            stop.assert_called_once()
        self.assertFalse(previous.cancel_event.is_set())

    def test_one_hour_start_uses_same_controller_once(self):
        self.view.select_mode(0)
        def launch(r, **kwargs): r.thread=Mock(is_alive=lambda: True)
        with patch.object(DashboardOneHour,'launch',autospec=True,side_effect=launch) as start:
            self.view.start.click()
            self.view.start.click()
            start.assert_called_once()
            self.assertIs(self.panel.one_hour.existing_controller,self.c)
            self.assertFalse(self.view.collection_mode.isEnabled())
            self.assertTrue(self.view.stop.isEnabled())
            self.assertTrue(self.panel.one_hour.no_shutdown)

    def test_cancel_shutdown_and_collection_are_separate(self):
        r = self.runner()
        r.thread=Mock(is_alive=lambda: True)
        self.panel.poll()
        self.view.cancel_shutdown.click()
        self.assertTrue(r.shutdown_cancel_event.is_set())
        self.assertFalse(r.cancel_event.is_set())
        self.view.stop.click()
        self.assertTrue(r.cancel_event.is_set())
        r.shutdown.request.assert_not_called()
        r.shutdown.cancel.assert_not_called()

    def test_running_settings_locked(self):
        self.c.thread=Mock(is_alive=lambda: True)
        self.c.state='RUNNING'
        self.panel.poll()
        for widget in (self.view.collection_mode,self.view.max_ws,self.view.paper,self.view.rotation,self.view.opening,self.view.no_shutdown):
            self.assertFalse(widget.isEnabled())
        self.view.select_mode(0)
        self.assertFalse(self.panel.one_hour_enable.isChecked())

    def test_existing_candidate_search_opens_correct_tab(self):
        self.panel.ticker.setText('SYNTHETIC')
        self.panel.search_ticker()
        self.assertIs(self.panel.tabs.currentWidget(),self.panel.ticker_tab)

    def test_state_labels_do_not_invent_complete_or_full_session(self):
        for phase in ('STOPPED','ARMED','PREOPEN_WAIT','REGULAR_LIVE','FINALIZING','ERROR'):
            self.c.state=phase
            self.view.render(None,busy=phase not in ('STOPPED','ERROR'))
            self.assertIn('현재 단계: '+phase,self.view.phase.text())
            self.assertNotIn('VERIFIED',self.view.result.text())
            self.assertNotIn('FULL SESSION',self.view.result.text())

    def test_failed_research_preserved_zip_stays_failed(self):
        r=self.runner()
        r.finalized=True
        r.state='COMPLETE_NO_SHUTDOWN'
        r.summary=dict(research_status='FAILED',preservation_status='VERIFIED',shutdown_eligible=True)
        r.archive=r.folder/'one_hour_diagnostic_synthetic.zip'
        self.view.render(None,busy=False)
        self.assertIn('연구 FAILED',self.view.result.text())
        self.assertIn('(기록상): VERIFIED',self.view.result.text())
        self.assertIn('OFF · 테스트',self.view.result.text())
        r.summary['preservation_status']='BLOCKED'
        r.state='ERROR'
        self.view.render(None,busy=False)
        self.assertIn('BLOCKED',self.view.result.text())
        self.assertIn('차단',self.view.result.text())

    def test_first_error_delay_clock_and_write_failure_display(self):
        r=self.runner()
        r.diagnostics=RunDiagnostics(r.folder/'diagnostics',r.clock)
        r.diagnostics.record('paper_processing_delay','REGULAR_LIVE',error=True,
            first_failure_event=dict(queue_wait_ms=2009.58))
        r.failures=['clock_jump']
        self.c.paper=SimpleNamespace(error=True,sidecar_error=False)
        self.view.render(dict(status='ERROR',rotation_v2=dict(status='INVALIDATED')),busy=True)
        self.assertIn('Paper v1: ERROR · Rotation v2: INVALIDATED',self.view.paper_status.text())
        self.assertIn('2009.58',self.view.paper_status.text())
        self.assertIn('clock_jump',self.view.result.text())
        self.assertIn(r.clock.now().isoformat(),self.view.result.text())
        r.diagnostics.write_errors.append(dict(type='OSError'))
        self.view.render(None,busy=True)
        self.assertIn('쓰기 실패',self.view.result.text())
        self.c.paper=None

    def test_poll_reuses_one_cached_paper_view_without_sql(self):
        view=Mock(return_value=dict(status='WATCHING',rotation_v2=dict(status='CLOSED')))
        self.c.paper=SimpleNamespace(view=view,error=False,sidecar_error=False)
        with patch('sqlite3.connect',side_effect=AssertionError('GUI must not query Paper SQLite')) as sql:
            for _ in range(3): self.panel.poll()
            sql.assert_not_called()
        self.assertEqual(view.call_count,3)
        self.assertIn('WATCHING',self.view.paper_status.text())
        self.assertIn('CLOSED',self.view.paper_status.text())

    def test_elapsed_and_remaining_use_collector_monotonic(self):
        r=self.runner()
        r.plan=dict(open=r.clock.now().isoformat(),stop=(r.clock.now()+timedelta(seconds=3900)).isoformat())
        start=r.clock.monotonic()
        self.c.schedule=SimpleNamespace(phase='REGULAR_LIVE',clock=r.clock,started_mono=start,snapshot=lambda:{})
        self.c.state='RUNNING'
        r.clock.advance(300)
        r.clock.wall+=timedelta(seconds=3.5)
        self.view.render(None,busy=True)
        self.assertIn('수집 경과 0:05:00 · 종료까지 1:00:00',self.view.phase.text())
        r.continue_after_clock_jump=True
        self.view.render(None,busy=True)
        self.assertIn('연구 FAILED',self.view.notice.text())

    def test_late_launch_and_state_mismatch_are_visible(self):
        r=self.runner()
        r.reason='late_launch'
        self.view.render(None,busy=False)
        self.assertIn('late_launch',self.view.notice.text())
        self.assertEqual(self.c.state,'STOPPED')
        r.finalized=True
        self.c.thread=Mock(is_alive=lambda:True)
        self.view.render(None,busy=True)
        self.assertIn('상태 불일치 / 미확인',self.view.notice.text())

    def test_result_and_zip_folder_buttons_use_existing_paths(self):
        r=self.runner()
        r.archive=r.folder/'synthetic.zip'
        with patch('market_radar.collection_ui.QDesktopServices.openUrl',return_value=True) as open_url:
            self.view.folder.click()
            self.view.zip_folder.click()
            self.assertEqual(open_url.call_count,2)
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()),r.folder)

    def test_diagnostics_are_read_only_on_demand(self):
        r=self.runner()
        path=r.folder/'diagnostics/runtime.json'
        path.parent.mkdir()
        payload=dict(first_error=dict(code='clock_jump',phase='REGULAR_LIVE'),raw_payload='MUST_NOT_DISPLAY')
        path.write_text(json.dumps(payload),encoding='utf-8')
        before=path.read_bytes()
        self.view.diagnostics_button.click()
        self.drain()
        from PySide6.QtWidgets import QTextEdit
        text=self.view.diagnostic_dialog.findChild(QTextEdit).toPlainText()
        self.assertIn('clock_jump',text)
        self.assertIn('NOT_CREATED',text)
        self.assertNotIn('MUST_NOT_DISPLAY',text)
        self.assertEqual(before,path.read_bytes())
        self.view.diagnostic_dialog.close()

    def test_core_controls_fit_target_resolutions(self):
        for width,height in ((1366,700),(1920,1000)):
            self.panel.resize(width,height)
            self.panel.show()
            self.app.processEvents()
            self.assertLessEqual(self.panel.width(),width)
            self.assertLessEqual(self.panel.height(),height)
            for widget in (self.view.start,self.view.stop,self.view.diagnostics_button,self.view.result):
                point=widget.mapTo(self.panel,QPoint(widget.width()-1,widget.height()-1))
                self.assertTrue(self.panel.rect().contains(point),(width,height,widget.text(),point))


class DiagnosticReadTests(unittest.TestCase):
    def test_shutdown_blockers_and_first_error_details_survive_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            summary=root/'one_hour_run_summary.json'
            summary.write_text(json.dumps(dict(shutdown_blockers=['workers_not_finished'],
                archive_exceptions=[dict(phase='finalization',type='OSError')],raw_payload='UNSAFE')),encoding='utf-8')
            shutdown=root/'shutdown_status.json'
            shutdown.write_text(json.dumps(dict(status='WAITING_SHUTDOWN',not_before='2026-10-12T14:40:00+00:00')),encoding='utf-8')
            data=read_diagnostics([summary,shutdown])
            self.assertEqual(data[0]['details']['shutdown_blockers'],['workers_not_finished'])
            self.assertEqual(data[1]['details']['not_before'],'2026-10-12T14:40:00+00:00')
            self.assertNotIn('UNSAFE',json.dumps(data))

    def test_invalid_missing_large_and_unapproved_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            path=root/'runtime.json'
            path.write_text('{',encoding='utf-8')
            self.assertEqual(read_diagnostics([path])[0]['state'],'UNAVAILABLE')
            path.write_bytes(b' '*2_000_001)
            self.assertEqual(read_diagnostics([path])[0]['state'],'UNAVAILABLE')
            self.assertEqual(read_diagnostics([root/'collection.json'])[0]['state'],'NOT_CREATED')
            self.assertEqual(read_diagnostics([root/'secret.log']),[])


if __name__ == '__main__': unittest.main()
