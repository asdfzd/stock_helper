"""Offline preservation policy v2. Real archives, isolated DBs, mocked OS."""
import csv
import hashlib
import io
import json
import queue
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tests import one_hour_gui_test as gui_tests
from market_radar.one_hour_archive import export_copy, validate_archive
from tools.one_hour_runner.system import RunBlocked


class PreservationTests(unittest.TestCase):
    setUp=gui_tests.IntegratedEvidenceTests.setUp
    prepare=gui_tests.IntegratedEvidenceTests.prepare
    finish=gui_tests.IntegratedEvidenceTests.finish

    def assert_shutdown(self):
        self.assertEqual(self.r.state,'WAITING_SHUTDOWN',self.r.reason)
        self.clock.advance(300)
        self.r.tick()
        self.shutdown.request.assert_called_once()

    def test_quality_fail_preserved_and_shutdown_allowed(self):
        self.prepare()
        quality=dict(status='FAIL',failures=['first_hour_candidate_coverage'],policy={'version':2})
        with patch('tools.one_hour_runner.artifacts.assess_collection',return_value=quality),patch('tools.one_hour_runner.quality.assess_collection',return_value=quality):
            self.r.tick()
        self.assertEqual(self.r.summary['status'],'FAILED')
        self.assertEqual(self.r.summary['collection_quality'],quality)
        self.assert_shutdown()

    def test_missing_essential_export_blocks(self):
        self.prepare()
        (self.c.config.data_dir/'export'/self.r.plan['session_date']/'snapshots.csv').unlink()
        self.r.tick()
        self.assertEqual(self.r.state,'ERROR')
        self.assertFalse(self.r.summary['shutdown_eligible'])
        self.shutdown.assert_not_called()
        self.assertIsNotNone(self.r.archive)
        validate_archive(self.r.archive,self.r.summary['run_id'],self.r.summary['session_date'],self.r.folder,require_safe=False)

    def test_original_modification_after_zip_blocks(self):
        self.finish()
        with (self.c.config.data_dir/'export'/self.r.plan['session_date']/'snapshots.csv').open('ab') as f: f.write(b'changed')
        self.clock.advance(300); self.r.tick()
        self.assertEqual(self.r.state,'ERROR')
        self.shutdown.request.assert_not_called()
        self.assertEqual(self.r.summary['research_status'],'VERIFIED')

    def test_original_missing_after_zip_blocks(self):
        self.finish()
        self.c.paper.path.unlink()
        self.clock.advance(300); self.r.tick()
        self.assertEqual(self.r.state,'ERROR')
        self.shutdown.request.assert_not_called()

    def test_file_preservation_io_failure_blocks(self):
        self.prepare()
        with patch('market_radar.one_hour_archive.local_inventory',side_effect=OSError('synthetic')): self.r.tick()
        self.assertEqual(self.r.state,'ERROR')
        self.shutdown.request.assert_not_called()

    def test_failure_no_shutdown_has_zero_os_calls(self):
        self.prepare(); self.c.paper.error=True; self.r.no_shutdown=True
        self.r.tick()
        self.assertEqual(self.r.state,'COMPLETE_NO_SHUTDOWN')
        self.assertEqual(self.r.summary['status'],'FAILED')
        self.clock.advance(600); self.r.tick()
        self.assertEqual(self.shutdown.mock_calls,[])

    def test_sidecar_error_can_shutdown_after_preservation(self):
        self.prepare(); self.c.paper.sidecar_error=True; self.r.tick()
        self.assertEqual(self.r.summary['status'],'FAILED')
        self.assert_shutdown()

    def test_cancelled_diagnostic_does_not_shutdown(self):
        self.prepare(); self.c.paper.error=True; self.r.request_cancel(); self.r.tick()
        self.assertEqual(self.r.state,'ERROR')
        self.clock.advance(600); self.r.tick()
        self.shutdown.request.assert_not_called()

    def test_coherently_rehashed_csv_tamper_still_blocks(self):
        self.finish()
        with zipfile.ZipFile(self.r.archive) as z: entries={n:z.read(n) for n in z.namelist()}
        key='radar/snapshots.csv'; entries[key]=entries[key].replace(b'DEMO',b'EVIL')
        m=json.loads(entries['manifest.json']); h=dict(sha256=hashlib.sha256(entries[key]).hexdigest(),size_bytes=len(entries[key]))
        m['files'][key]=h; m['provenance'][key]['included']=h
        entries['manifest.json']=json.dumps(m).encode()
        with zipfile.ZipFile(self.r.archive,'w') as z:
            for n,b in entries.items(): z.writestr(n,b)
        with self.assertRaisesRegex(RunBlocked,'derived_export_mismatch'):
            validate_archive(self.r.archive,self.r.summary['run_id'],self.r.summary['session_date'],self.r.folder)

    def test_no_started_evidence_blocks_even_if_files_exist(self):
        self.prepare()
        self.c.schedule.data['actual_start']=None
        self.c.lifecycle['collection_protocol']=self.c.schedule.snapshot()
        (self.c.config.data_dir/'dashboard_run.json').write_text(json.dumps(self.c.lifecycle),encoding='utf-8')
        self.r.tick()
        self.assertEqual(self.r.state,'ERROR')
        self.shutdown.request.assert_not_called()


class PrivacyTests(unittest.TestCase):
    def csv(self, value, column='symbol_type_evidence'):
        out=io.StringIO(newline=''); writer=csv.writer(out); writer.writerow(['ticker',column,'price']); writer.writerow(['TC',json.dumps(value),'7.3'])
        return out.getvalue().encode()

    def test_real_company_name_false_positive_is_redacted_only_in_description(self):
        raw=self.csv({'source':'kiwoom:usa10099','fields':{'stk_enm':'TOKEN CAT LIMITED SPON ADS EACH REP 4800'}})
        clean,edits=export_copy(raw,'candidates.csv','a'*32,'2026-10-14')
        row=next(csv.DictReader(io.StringIO(clean.decode())))
        self.assertEqual(row['ticker'],'TC'); self.assertEqual(row['price'],'7.3')
        self.assertNotIn(b'TOKEN',clean); self.assertIn(b'TOKEN',raw)
        self.assertEqual(edits[0]['column'],'symbol_type_evidence')
        self.assertEqual(edits[0]['row'],1)
        # A fresh process can have another set iteration order. Manifest edits
        # must have deterministic row/column correspondence across restarts.
        out=io.StringIO(newline='')
        writer=csv.writer(out)
        writer.writerow(['symbol_type_evidence','discovery_symbol_type_evidence'])
        writer.writerow(['TOKEN COMPANY','TOKEN COMPANY'])
        _, edits=export_copy(out.getvalue().encode(),'candidates.csv','a'*32,'2026-10-14')
        self.assertEqual([e['column'] for e in edits],sorted(e['column'] for e in edits))

    def test_actual_credential_in_description_is_removed_not_exempted(self):
        raw=self.csv({'fields':{'access_token':'SYNTHETIC_CREDENTIAL_VALUE'}})
        clean,edits=export_copy(raw,'candidates.csv','a'*32,'2026-10-14')
        self.assertNotIn(b'SYNTHETIC_CREDENTIAL_VALUE',clean); self.assertNotIn(b'access_token',clean)
        self.assertTrue(edits)

    def test_actual_credential_in_critical_or_unknown_field_is_blocked(self):
        with self.assertRaises(RunBlocked): export_copy(self.csv('Bearer SYNTHETIC_VALUE','price'),'snapshots.csv','a'*32,'2026-10-14')

    def test_foreign_identity_not_redacted_to_pass(self):
        with self.assertRaises(RunBlocked): export_copy(self.csv({'collector_run_id':'b'*32}),'candidates.csv','a'*32,'2026-10-14')


class TimingTests(unittest.TestCase):
    def test_fifo_bounded_stats_real_commit_and_receipt_timestamps(self):
        from market_radar.paper.telemetry import Timings,TimedQueue
        from market_radar.paper.storage import PaperStore
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); timing=Timings(root/'timing.json'); q=TimedQueue(100,timing)
            timing.start()
            for i in range(90): q.put(('tick','TEST',i,datetime.now(timezone.utc).isoformat(),True))
            store=PaperStore(root/'paper.db',metrics=timing)
            with store.db: store.db.execute("INSERT INTO paper_trade_marks(radar_run_id,ticker,timestamp_utc,price,session_date_et) VALUES('test','TEST','2026-10-14T13:30:00+00:00',1,'2026-10-14')")
            for i in range(90): self.assertEqual(q.get()[2],i); timing.complete(i==89)
            store.close(); timing.close()
            value=json.loads((root/'timing.json').read_text())
            self.assertEqual(value['maximum_queue_length'],90)
            self.assertEqual(len(value['recent_events']),64)
            self.assertTrue(value['first_failure_events'])
            self.assertIn('enqueued_utc',value['recent_events'][-1])
            self.assertIn('dequeued_utc',value['recent_events'][-1])
            self.assertGreater(value['measurements']['paper.commit']['count'],0)
            self.assertEqual(value['measurements']['tick.queue_wait']['count'],90)
            self.assertFalse(timing.thread.is_alive())

    def test_existing_runtime_diagnostics_default_off(self):
        from market_radar.paper_rotation_v2.runtime import PairedRuntime
        from market_radar.config import CONFIG
        r=PairedRuntime(CONFIG,'test')
        self.assertIsNone(r.metrics)
        self.assertIs(type(r.queue),queue.Queue)

    def test_telemetry_write_failure_does_not_raise(self):
        from market_radar.paper.telemetry import Timings
        with tempfile.TemporaryDirectory() as folder:
            t=Timings(Path(folder)/'timing.json')
            with patch('tools.one_hour_runner.core.atomic_json',side_effect=OSError('synthetic')): t.flush()
            self.assertEqual(t.write_error,'OSError')


if __name__=='__main__': unittest.main()
