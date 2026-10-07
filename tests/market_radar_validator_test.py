"""Synthetic finalized sessions and corruption tests; no live provider or credentials."""
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from market_radar.config import CONFIG
from market_radar.engine import RadarEngine
from market_radar.models import MarketBatch, instant, utc
from market_radar.research.importer import import_session
from market_radar.research.master_db import MasterDB
from market_radar.research.review_export import create_review_zip
from market_radar.research.validator import validate_session, revalidate_session, write_validation, ValidatorPolicy
from market_radar.storage import RadarStore
from tests.kiwoom_provider_test import observation, SESSION


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = replace(CONFIG, data_dir=self.root/'source', max_ws_symbols=150)
        self.master = MasterDB(self.root/'master.db')
        self.identity = dict(provider='kiwoom', mode='live', test_version=CONFIG.test_version,
            schema_version=4, max_ws_symbols=150, result_tolerance_seconds=30, result_pre_target_grace_seconds=.25)
        self.store = RadarStore(self.config.db_path, self.identity)
        self.engine = RadarEngine(self.store, self.config, 'kiwoom', 'live')
        self.o = observation()
        self.batch(self.o)
        self.batch(replace(self.o, asof_utc=utc(instant(self.o.asof_utc)+timedelta(seconds=30))))
        self.p = dict(collection_mode='Capacity Test', protocol_compliant=True, session_date_et=SESSION.date,
            armed_at=utc(instant(SESSION.open_utc)-timedelta(minutes=30)), armed_before_open=True,
            scheduled_open_utc=SESSION.open_utc, scheduled_close_utc=SESSION.close_utc,
            scheduled_start=SESSION.open_utc, actual_start=utc(instant(SESSION.open_utc)+timedelta(milliseconds=50)),
            scheduled_stop=utc(instant(SESSION.open_utc)+timedelta(minutes=65)),
            actual_stop=utc(instant(SESSION.open_utc)+timedelta(minutes=65,milliseconds=50)),
            start_delay_ms=50, stop_delay_ms=50, stop_reason='scheduled_capacity_end',
            ready_before_open=True, connection_ready_at=utc(instant(SESSION.open_utc)-timedelta(minutes=29)),
            first_regular_batch_utc=self.o.asof_utc, max_ws_symbols=150, snapshot_seconds=30,
            result_pre_target_grace_seconds=.25)
        self.sync()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def batch(self, o, metadata=None):
        metadata = metadata or dict(tracking_counts=dict(active_subscription_count=1, protected_count=1,
            discovery_count=1, slot_saturation_pct=100/150, tracking_slot_unavailable_count=0))
        self.engine.process(MarketBatch(o.asof_utc, (o,), (SESSION,), metadata=metadata), o.asof_utc)

    def sync(self):
        with self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO radar_metadata VALUES('collection_protocol',?)", (json.dumps(self.p),))
        self.store.export_all(self.config.data_dir/'export')
        lifecycle = dict(run_id='synthetic', clean_shutdown=True, started_at=self.p['armed_at'],
            stopped_at=self.p['actual_stop'], collection_protocol=self.p)
        (self.config.data_dir/'dashboard_run.json').write_text(json.dumps(lifecycle), encoding='utf-8')

    def validate(self):
        return validate_session(self.config.db_path, SESSION.date)

    def codes(self, summary, severity='errors'):
        return {r['code'] for r in summary[severity]}

    def counter(self, name, count=1):
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO quality_counters VALUES(?,?,?)', (SESSION.date,name,count))
        self.sync()

    def test_pass_and_future_pending_normal(self):
        s = self.validate()
        self.assertEqual(s['overall'], 'PASS', s)
        self.assertEqual(s['results']['status_distribution']['+60m']['pending'], 1)
        self.assertEqual(s['results']['overdue_pending_count'], 0)

    def test_rvol_warn(self):
        with self.store.db:
            self.store.db.execute('UPDATE radar_snapshots SET rvol=NULL,rvol_status="incomplete_history"')
        self.sync()
        s = self.validate()
        self.assertEqual(s['overall'], 'WARN')
        self.assertIn('rvol_coverage_low', self.codes(s,'warnings'))

    def test_protocol_false_fails(self):
        self.p['protocol_compliant'] = False
        self.sync()
        self.assertIn('protocol_non_compliant', self.codes(self.validate()))

    def test_delay_recomputed(self):
        self.p['actual_start'] = utc(instant(SESSION.open_utc)+timedelta(seconds=2))
        self.sync()
        codes = self.codes(self.validate())
        self.assertTrue({'start_outside_tolerance','start_delay_evidence_mismatch'} <= codes)

    def test_calendar_mismatch(self):
        self.p['scheduled_start'] = utc(instant(SESSION.open_utc)+timedelta(minutes=1))
        self.sync()
        self.assertIn('calendar_schedule_mismatch', self.codes(self.validate()))

    def test_preopen_each_research_table(self):
        before = utc(instant(SESSION.open_utc)-timedelta(seconds=1))
        for table, field in [('radar_snapshots','timestamp_utc'),('radar_candidates','first_detected_utc'),('radar_events','timestamp_utc')]:
            with self.subTest(table=table):
                rows = self.store.db.execute(f'SELECT id,{field} FROM {table}').fetchall()
                self.store.db.execute('PRAGMA foreign_keys=OFF')
                with self.store.db:
                    self.store.db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (before,rows[0][0]))
                self.sync()
                self.assertIn('preopen_research_contamination', self.codes(self.validate()))
                with self.store.db:
                    self.store.db.execute(f'UPDATE {table} SET {field}=? WHERE id=?', (rows[0][1],rows[0][0]))

    def test_corrupt_db_fails_and_summary_writes(self):
        broken = self.root/'broken.db'
        broken.write_bytes(b'not sqlite')
        s = validate_session(broken, SESSION.date, run_id='broken')
        self.assertEqual(s['overall'], 'FAIL')
        self.assertEqual(s['capacity']['status'], 'NOT_CHECKED')
        path = write_validation(s, self.root/'broken-export')
        self.assertEqual(json.loads(path.read_text())['overall'], 'FAIL')

    def test_required_table_failure(self):
        with self.store.db:
            self.store.db.execute('DROP TABLE radar_events')
        self.assertIn('missing_tables', self.codes(self.validate()))

    def test_foreign_key_failure(self):
        self.store.db.execute('PRAGMA foreign_keys=OFF')
        with self.store.db:
            self.store.db.execute('UPDATE radar_results SET candidate_id=999')
        self.sync()
        self.assertIn('foreign_key_failure', self.codes(self.validate()))

    def test_csv_count_mismatch(self):
        path = self.config.data_dir/'export'/SESSION.date/'snapshots.csv'
        path.write_text(path.read_text(encoding='utf-8-sig').splitlines()[0]+'\n',encoding='utf-8-sig')
        self.assertIn('snapshots_export_mismatch', self.codes(self.validate()))

    def test_csv_same_count_content_mismatch(self):
        path = self.config.data_dir/'export'/SESSION.date/'snapshots.csv'
        path.write_text(path.read_text(encoding='utf-8-sig').replace('TEST','OTHER'),encoding='utf-8-sig')
        self.assertIn('snapshots_export_mismatch', self.codes(self.validate()))

    def test_quality_mismatch(self):
        path = self.config.data_dir/'export'/SESSION.date/'quality_summary.json'
        q = json.loads(path.read_text()); q['snapshot_count'] += 1
        path.write_text(json.dumps(q))
        self.assertIn('quality_summary_mismatch', self.codes(self.validate()))

    def test_slot_shortage_warn_only(self):
        self.counter('tracking_slot_unavailable_count',20)
        s = self.validate()
        self.assertEqual(s['overall'],'WARN')
        self.assertEqual(s['capacity']['status'],'WARN')

    def test_stale_high_warn(self):
        self.batch(replace(self.o,asof_utc=utc(instant(self.o.asof_utc)+timedelta(seconds=60))),
                   dict(missing_reasons={'STALE':'stale_price'}))
        self.sync()
        s = self.validate()
        self.assertIn('stale_coverage_high',self.codes(s,'warnings'))
        self.assertEqual(s['data_quality']['stale_unique_tickers'],1)

    def test_fatal_errors(self):
        for key in ('worker_error_count','db_error_count','export_error_count'):
            with self.subTest(key=key):
                self.counter(key)
                self.assertIn(key,self.codes(self.validate()))

    def test_missing_horizon_fails(self):
        with self.store.db:
            self.store.db.execute("DELETE FROM radar_results WHERE horizon='60m'")
        self.sync()
        self.assertIn('result_integrity_failure',self.codes(self.validate()))

    def test_sample_offset_fails(self):
        with self.store.db:
            self.store.db.execute("UPDATE radar_results SET sample_offset_seconds=999 WHERE status='complete'")
        self.sync()
        self.assertIn('result_integrity_failure',self.codes(self.validate()))

    def test_manual_and_unknown_never_standardized_pass(self):
        self.p['collection_mode'] = 'Manual'
        self.sync()
        s = self.validate()
        self.assertEqual(s['protocol']['status'],'WARN')
        self.assertFalse(s['protocol']['standardized_comparison'])

    def test_revalidation_master_zip_and_readonly_including_wal(self):
        import_session(self.config.db_path,self.master.path)
        session = self.master.sessions()[0]
        paths = [p for p in self.config.data_dir.rglob('*') if p.is_file()]
        hashes = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        s = revalidate_session(session,self.master.path)
        self.assertEqual(s['overall'],'PASS',s)
        self.assertEqual(hashes,{p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        self.assertEqual(self.master.sessions()[0]['validation_overall'],'PASS')
        s2 = revalidate_session(session,self.master.path)
        self.assertEqual(s['run_id'],s2['run_id'])
        self.assertEqual(len(self.master.sessions()),1)
        with closing(self.master.connect()) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
        with zipfile.ZipFile(create_review_zip(session)) as z:
            self.assertIn('validation_summary.json',z.namelist())
            manifest = json.loads(z.read('review_manifest.json'))
            self.assertEqual(manifest['validation_overall'],'PASS')
            self.assertEqual(manifest['validator_version'],1)
        import_session(self.config.db_path,self.master.path)
        self.assertEqual(self.master.sessions()[0]['validation_overall'],'PASS')

    def test_stale_validation_invalidated_on_import_and_zip(self):
        import_session(self.config.db_path,self.master.path)
        session = self.master.sessions()[0]
        revalidate_session(session,self.master.path)
        self.counter('tracking_slot_unavailable_count',1)
        import_session(self.config.db_path,self.master.path)
        self.assertIsNone(self.master.sessions()[0]['validation_overall'])
        with self.assertRaisesRegex(ValueError,'stale'):
            create_review_zip(session)
        self.assertEqual(revalidate_session(session,self.master.path)['overall'],'WARN')

    def test_atomic_failure_preserves_previous_summary(self):
        folder = self.config.data_dir/'export'/SESSION.date
        path = write_validation(self.validate(),folder)
        before = path.read_bytes()
        with patch('market_radar.research.validator.os.replace',side_effect=OSError):
            with self.assertRaises(OSError):
                write_validation(dict(overall='FAIL'),folder)
        self.assertEqual(before,path.read_bytes())

    def test_history_widgets(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from market_radar.ui import RadarPanel
        from market_radar.dashboard_widgets import HISTORY_COLUMNS
        app = QApplication.instance() or QApplication([])
        import_session(self.config.db_path,self.master.path)
        session = self.master.sessions()[0]
        revalidate_session(session,self.master.path)
        panel = RadarPanel(config=self.config,master_path=self.master.path,
            settings=QSettings(str(self.root/'ui.ini'),QSettings.Format.IniFormat))
        panel.render_details(self.master.details(session['id']))
        self.assertIn('"overall": "PASS"',panel.validation_text.toPlainText())
        self.assertIn(('validation_overall','Validation'),HISTORY_COLUMNS)
        panel.validation_done(self.validate())
        self.assertIn('Session validation: PASS',panel.validation_status.text())
        panel.close()
        app.processEvents()


if __name__ == '__main__':
    unittest.main()
