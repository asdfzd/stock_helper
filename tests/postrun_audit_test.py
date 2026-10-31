"""Synthetic evidence only. No API, OS power, real task registration or source DB."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from market_radar.postrun_audit import (analyze, publish, read_json, write_output, OWNER,
    confined, process_alive, MAX_INPUT)
from market_radar.audit_task import AuditTask, AuditTaskScheduler, run_helper, recover_pending
from market_radar.audit_runtime import AuditSession
from market_radar.audit_delivery import DeliveryQueue, ai_review_request, external_summary
from market_radar.watchdog import TaskScheduler, verify_task, task_xml

NOW = datetime(2026, 11, 2, 16, 10, tzinfo=timezone.utc)
RID, AID = 'a'*32, 'b'*32


def state():
    return dict(schema=1, owner=OWNER, audit_id=AID, started_at='2026-11-02T13:00:00+00:00',
        et_date='2026-11-02', pid=99, process_created=1, heartbeat=NOW.isoformat(),
        gui_heartbeat=NOW.isoformat(), collector_run_id=RID, run_folder='run_'+'c'*32,
        armed_at='2026-11-02T13:00:01+00:00', audit_due=NOW.isoformat(), terminal=True,
        audit_task_status='REGISTERED', plan=dict(open='2026-11-02T14:30:00+00:00'),
        telemetry=dict(sampled_at='2026-11-02T15:34:59+00:00', last_fe_age_seconds=1,
            last_fe_at='2026-11-02T15:34:58+00:00', ws_connected=True, sticky_gap=False,
            generation=1, fe_count=1234, raw_dropped=0, paper_error=False, rotation_error=False))


def documents():
    return dict(summary=dict(run_id=RID, session_date='2026-11-02', research_status='VERIFIED',
        preservation_status='VERIFIED', shutdown_eligible=True, archive_kind='research',
        shutdown_disabled=True, opening_hour_status='COMPLETE', original_validation_overall='FAIL',
        first_batch='2026-11-02T14:30:00+00:00', first_snapshot='2026-11-02T14:30:00+00:00',
        actual_start='2026-11-02T14:30:00+00:00', actual_stop='2026-11-02T15:35:00+00:00',
        collection_stop=dict(reason='planned_65_minute_stop', requested_at='2026-11-02T15:35:00+00:00'),
        collection_quality=dict(status='PASS', timelines={'batch':dict(coverage=1, observed_slots=120,
            expected_slots=120, maximum_gap_seconds=30)}, opening_coverage={})),
        quality=dict(snapshot_count=900, candidate_count=21, rvol=dict(available_pct=100),
            collection_protocol=dict(regular_duration_seconds=3900)),
        watchdog=dict(status='CANCELLED'), runtime={}, performance={}, power={})


class VerdictTests(unittest.TestCase):
    def result(self, s=None, d=None, **kw):
        return analyze(s or state(), d or documents(), now=NOW, alive=True, archive_present=kw.pop('archive_present', True), **kw)

    def test_normal_65_minutes_is_not_full_session_or_power_off(self):
        r = self.result()
        self.assertEqual(r['severity'], 'PASS')
        self.assertEqual(r['execution']['regular_duration_seconds'], 3900)
        self.assertEqual(r['preservation']['original_validator'], 'FAIL')  # expected shortened run
        self.assertEqual(r['actual_power_off'], 'NOT_VERIFIED')
        self.assertFalse(r['preservation']['archive_reverified_by_audit'])

    def test_failed_research_preserved_diagnostic_is_never_success(self):
        d=documents(); d['summary'].update(research_status='FAILED', archive_kind='diagnostic')
        r=self.result(d=d)
        self.assertEqual(r['severity'], 'FAIL'); self.assertEqual(r['preservation_status'], 'VERIFIED')
        self.assertEqual(r['archive_kind'], 'diagnostic')

    def test_missing_normal_zip_fails_even_when_record_says_verified(self):
        self.assertEqual(self.result(archive_present=False)['archive_status'], 'MISSING')
        self.assertEqual(self.result(archive_present=False)['severity'], 'FAIL')

    def test_unconfirmed_archive_evidence_cannot_be_pass(self):
        r=self.result(archive_present=None)
        self.assertEqual(r['archive_status'], 'NOT_VERIFIED')
        self.assertEqual(r['severity'], 'UNKNOWN')
        d=documents(); d['summary'].pop('archive_kind')
        r=self.result(d=d)
        self.assertEqual(r['archive_status'], 'NOT_VERIFIED')
        self.assertEqual(r['severity'], 'UNKNOWN')

    def test_missing_summary_unknown(self):
        d=documents(); d.pop('summary')
        self.assertEqual(self.result(d=d)['severity'], 'UNKNOWN')

    def test_zero_snapshots_remains_measured_zero(self):
        d=documents(); d['quality']['snapshot_count']=0; d['summary']['snapshot_count']=900
        self.assertEqual(self.result(d=d)['execution']['snapshot_count'],0)

    def test_wrapper_and_specific_primary_failure_keep_separate_times(self):
        s=state(); s['first_failure']=dict(code='credentials_file_missing',occurred_at_utc='2026-11-02T13:00:02+00:00')
        d=documents(); d['runtime']['first_error']=dict(code='supervisor_exception',wall_utc='2026-11-02T13:00:01+00:00')
        r=self.result(s=s,d=d)['first_failure']
        self.assertEqual(r['code'],'supervisor_exception'); self.assertEqual(r['primary_reason'],'credentials_file_missing')
        self.assertLess(r['occurred_at'],r['primary_reason_recorded_at'])

    def test_sticky_gap_not_a_disconnection(self):
        s=state(); s['telemetry']['sticky_gap']=True
        r=self.result(s=s)
        self.assertEqual(r['ws_status'], 'STICKY_GAP_WITH_RECENT_RECEIPT')
        self.assertEqual(r['severity'], 'WARNING')

    def test_actual_fe_receipt_gap_even_when_socket_connected(self):
        s=state(); s['telemetry']['last_fe_age_seconds']=91
        self.assertEqual(self.result(s=s)['ws_status'], 'RECEIPT_GAP_OBSERVED')

    def test_recovered_receipt_keeps_observed_earlier_gap(self):
        s=state(); s['continuity_totals']=dict(maximum_fe_age_seconds=110,disconnected_samples=1)
        self.assertEqual(self.result(s=s)['ws_status'],'RECEIPT_GAP_OBSERVED')

    def test_no_fe_evidence_unknown_not_disconnect(self):
        s=state(); s['telemetry'].pop('last_fe_age_seconds')
        self.assertEqual(self.result(s=s)['ws_status'], 'UNKNOWN')

    def test_raw_dropped_is_failure(self):
        s=state(); s['telemetry']['raw_dropped']=1
        self.assertEqual(self.result(s=s)['severity'], 'FAIL')

    def test_paper_delay_first_error_queue_and_sql_are_preserved(self):
        d=documents(); d['performance']=dict(first_error=dict(code='paper_processing_delay', occurred_at_utc='2026-11-02T14:31:01+00:00'),
            maximum_queue_length=31, measurements={'v1.sql_write':dict(max_ms=1510, count=4)},
            first_failure_events=[dict(received_utc='2026-11-02T14:30:58+00:00', queue_wait_ms=2001,
                                      total_receipt_to_finish_ms=2500, queue_length=30, processing_ms=499)])
        r=self.result(d=d)
        self.assertEqual(r['paper_status'],'FAILED'); self.assertEqual(r['severity'],'FAIL')
        self.assertEqual(r['first_failure']['code'],'paper_processing_delay')
        self.assertEqual(r['research']['first_delay_over_2000ms']['queue_wait_ms'],2001)
        self.assertEqual(r['research']['measurements']['v1.sql_write']['max_ms'],1510)

    def test_opening_and_rvol_missing_are_independent(self):
        d=documents(); d['summary']['opening_hour_status']='INCOMPLETE'; d['quality']['rvol']=dict(available_pct=0, incomplete_history=100)
        r=self.result(d=d)
        self.assertEqual(r['opening_status'],'INCOMPLETE')
        self.assertEqual(r['research']['rvol_missing']['incomplete_history'],100)
        self.assertNotEqual(r['severity'],'PASS')

    def test_shutdown_request_unknown_and_accepted_are_distinct(self):
        d=documents(); d['summary']['shutdown_disabled']=False
        self.assertEqual(self.result(d=d)['severity'],'UNKNOWN')
        d['power']['status']='SCHEDULED'; d['watchdog']['status']='NORMAL_REQUEST_ACCEPTED'
        r=self.result(d=d)
        self.assertEqual(r['shutdown_request_status'],'REQUEST_ACCEPTED_POWER_OFF_NOT_VERIFIED')
        self.assertEqual(r['actual_power_off'],'NOT_VERIFIED')

    def test_watchdog_dry_run_and_registration_cancel_failures(self):
        for status,error in [('DRY_RUN_FIRED',None),('REGISTRATION_FAILED',None),('STATE_UNKNOWN',None),('ARMED','normal_disarm_failed')]:
            d=documents(); d['watchdog']=dict(status=status,error=error)
            with self.subTest(status=status): self.assertNotEqual(self.result(d=d)['severity'],'PASS')

    def test_missing_process_heartbeat_and_gui_heartbeat(self):
        for alive,hb,gui,expected in [(False,None,None,'PROCESS_MISSING'),(True,None,None,'HEARTBEAT_STALE'),
                                    (True,NOW.isoformat(),None,'GUI_HEARTBEAT_STALE')]:
            s=state(); s.update(terminal=False,heartbeat=hb,gui_heartbeat=gui)
            r=analyze(s,{},now=NOW,alive=alive)
            self.assertEqual(r['process_status'],expected); self.assertEqual(r['severity'],'FAIL')

    def test_preopen_has_no_false_hang_alarm(self):
        s=state(); s.update(terminal=False,heartbeat=None)
        r=analyze(s,{},now=NOW-timedelta(hours=2),alive=True)
        self.assertEqual(r['severity'],'UNKNOWN')

    def test_missing_start_and_unexecuted_day_are_not_pass(self):
        s=state(); s.update(started_at=None,armed_at=None,terminal=False)
        self.assertNotEqual(self.result(s=s)['severity'],'PASS')
        self.assertEqual(analyze({}, {}, now=NOW)['severity'],'UNKNOWN')

    def test_user_stop_and_audit_registration_fail(self):
        s=state(); s.update(user_stopped=True,audit_task_status='REGISTRATION_FAILED')
        self.assertEqual(self.result(s=s)['severity'],'WARNING')

    def test_private_values_not_copied(self):
        s=state(); s['first_failure']=dict(code='secret-token-123456', occurred_at_utc='C:/Users/Private/.env')
        d=documents(); d['summary']['api_key']='secret-token-123456'; d['runtime']['request_body']='account-99999'
        result=json.dumps(self.result(s=s,d=d))
        self.assertNotIn('secret-token',result); self.assertNotIn('Private',result); self.assertNotIn('account-99999',result)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/('attempt_'+AID); self.root.mkdir()
        self.s=state(); self.docs=documents()
        write_output(self.root/'audit','state.json',self.s)
        self.run=self.root/self.s['run_folder']; self.run.mkdir()
        self.paths=dict(summary='one_hour_run_summary.json',runtime='diagnostics/runtime.json', performance='paper_v1/paper_performance.json',
                        quality='radar/export/2026-11-02/quality_summary.json',power='shutdown_status.json')
        for key,name in self.paths.items():
            p=self.run/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(self.docs[key]),encoding='utf-8')
        (self.run/f'one_hour_research_2026-11-02_{RID}_v2.zip').write_bytes(b'NOT_OPENED_BY_AUDIT')
        for name in ('data.db','data.db-wal','data.db-shm','old.log'):
            (self.run/name).write_bytes(b'original')

    def test_reads_json_only_preserves_every_original_byte_and_mtime(self):
        before={p:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).digest()) for p in self.run.rglob('*') if p.is_file()}
        with patch('sqlite3.connect',side_effect=AssertionError('source DB')), patch('zipfile.ZipFile',side_effect=AssertionError('ZIP')):
            publish(self.root,now=NOW,alive=False)
        self.assertEqual(before,{p:(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).digest()) for p in before})
        self.assertTrue((self.root/'audit/postrun_audit.txt').exists())

    def test_oversize_malformed_and_foreign_summary_fail_closed(self):
        for raw in ('[',json.dumps({'run_id':'d'*32}),json.dumps({'run_id':RID,'session_date':'2026-11-02','collection_quality':[]}),'x'*(MAX_INPUT+1)):
            (self.run/'one_hour_run_summary.json').write_text(raw)
            r=publish(self.root,now=NOW,alive=False)
            self.assertNotEqual(r['severity'],'PASS')

    def test_path_escape_and_foreign_state_rejected(self):
        with self.assertRaises(ValueError): confined(self.root,'../private.env')
        self.s['run_folder']='../../data'
        with self.assertRaises(ValueError): publish(self.root,state=self.s)

    def test_output_size_bounded(self):
        with self.assertRaises(ValueError): write_output(self.root/'audit','postrun_audit.txt','x'*200000)

    def test_pid_reuse_unknown_access_and_missing(self):
        import psutil
        with patch('psutil.Process',return_value=Mock(create_time=lambda:2)):
            self.assertFalse(process_alive(self.s))
        with patch('psutil.Process',side_effect=psutil.AccessDenied()): self.assertIsNone(process_alive(self.s))
        with patch('psutil.Process',side_effect=psutil.NoSuchProcess(99)): self.assertFalse(process_alive(self.s))

    def test_independent_check_no_db_no_network_no_power(self):
        scheduler=MemoryScheduler(); clock=Mock(return_value=NOW-timedelta(hours=2))
        task=AuditTask(self.root,scheduler=scheduler,now=clock)
        task.register(self.s,['C:/program/MarketRadarFamily.exe'],test=True)
        value=read_json(self.root,'audit/task.json'); verify_task(task_xml(value),value)
        clock.return_value=NOW
        with patch('socket.socket',side_effect=AssertionError('network')), patch('subprocess.run',side_effect=AssertionError('OS')):
            task.run(value['nonce'])
        self.assertFalse(scheduler.tasks)
        self.assertEqual(read_json(self.root,'audit/task_result.json')['shutdown_commands'],0)

    def test_duplicate_invalid_nonce_stale_and_foreign_task(self):
        task=AuditTask(self.root,scheduler=MemoryScheduler(),now=lambda:NOW-timedelta(hours=2))
        task.register(self.s,['C:/program/MarketRadarFamily.exe'])
        value=read_json(self.root,'audit/task.json')
        with self.assertRaises(ValueError): task.register(self.s,['C:/program/MarketRadarFamily.exe'])
        for when,nonce in [(NOW,'wrong'),(NOW-timedelta(seconds=1),value['nonce']),(NOW+timedelta(days=1),value['nonce'])]:
            task.now=lambda:when
            with self.assertRaises(ValueError): task.run(nonce)
        self.assertEqual(run_helper(self.root/'foreign.json',value['nonce']),2)

    def test_task_registration_and_removal_failure_recorded(self):
        scheduler=MemoryScheduler(); scheduler.fail=True
        task=AuditTask(self.root,scheduler=scheduler,now=lambda:NOW-timedelta(hours=2))
        with self.assertRaises(RuntimeError): task.register(self.s,['C:/program/MarketRadarFamily.exe'])
        self.assertEqual(read_json(self.root,'audit/task.json')['status'],'REGISTRATION_FAILED')
        with self.assertRaises(RuntimeError): task.cancel()
        self.assertEqual(read_json(self.root,'audit/task.json')['status'],'CANCEL_FAILED')

    def test_scheduler_prefixes_cannot_cross(self):
        for scheduler,name in [(TaskScheduler(),'MarketRadarAudit_20261102_'+AID),
                               (AuditTaskScheduler(),'MarketRadarFamily_20261102_'+AID)]:
            with self.assertRaises(RuntimeError): scheduler.call('delete',name)

    def test_dst_audit_deadline_and_early_close_use_utc_open(self):
        for opened, expected in [('2026-10-30T13:30:00+00:00','2026-10-30T15:10:00+00:00'),
                                 ('2026-11-02T14:30:00+00:00','2026-11-02T16:10:00+00:00')]:
            with tempfile.TemporaryDirectory() as tmp:
                s=state(); s['plan']['open']=opened; s['et_date']=opened[:10]
                write_output(Path(tmp)/'audit','state.json',s)
                t=AuditTask(tmp,scheduler=MemoryScheduler(),now=lambda:datetime(2026,10,1,tzinfo=timezone.utc))
                self.assertEqual(t.register(s,['C:/program/MarketRadarFamily.exe'])['deadline'],expected)

    def test_delivery_off_failure_retry_and_redaction(self):
        q=DeliveryQueue(self.root/'audit'); report=analyze(self.s,self.docs,now=NOW,archive_present=True)
        report.update(token='SECRET',evidence_paths=['C:/Users/Private/data.db'],sha256='a'*64)
        q.prepare(report); self.assertEqual(q.attempt(),'DISABLED')
        payload=read_json(self.root,'audit/outbox.json')
        raw=json.dumps(payload); self.assertNotIn('SECRET',raw); self.assertNotIn('Private',raw); self.assertNotIn('sha256',raw)
        adapter=Mock(user_approved=True,private_destination_verified=True,destination='approved-private')
        adapter.send.return_value=False
        self.assertEqual(q.attempt(approved_adapter=adapter),'PENDING_RETRY')
        original_id=read_json(self.root,'audit/delivery.json')['delivery_id']
        q.prepare(report); adapter.send.return_value=True
        self.assertEqual(DeliveryQueue(self.root/'audit').attempt(approved_adapter=adapter),'SENT')
        self.assertEqual(adapter.send.call_args.kwargs['idempotency_key'],original_id)
        self.assertEqual(q.attempt(approved_adapter=adapter),'SENT'); self.assertEqual(adapter.send.call_count,2)

    def test_delivery_timeout_and_retry_limit(self):
        q=DeliveryQueue(self.root/'audit'); q.prepare({})
        adapter=Mock(user_approved=True,private_destination_verified=True,destination='private')
        adapter.send.return_value=False
        self.assertEqual([q.attempt(approved_adapter=adapter) for _ in range(4)],['PENDING_RETRY','PENDING_RETRY','FAILED','FAILED'])

    def test_ai_off_and_unverified_budget_cannot_execute(self):
        self.assertEqual(ai_review_request({'severity':'FAIL'})['status'],'DISABLED')
        self.assertTrue(ai_review_request({'severity':'FAIL'},enabled=True)['status'].startswith('BLOCKED'))
        r=ai_review_request({'severity':'FAIL'},enabled=True,environment_verified=True,usage_limit_verified=True)
        self.assertFalse(r['code_changes_allowed'] or r['subprocess_actions_allowed'])
        self.assertEqual(r['execution'],'not_implemented_in_collector')


class RuntimeTests(unittest.TestCase):
    def test_configuration_failure_has_local_report_without_a_schedule(self):
        from tools.family_collect import record_startup_failure
        with tempfile.TemporaryDirectory() as tmp:
            record_startup_failure('family_config_invalid', root=Path(tmp))
            paths=list(Path(tmp).glob('*/attempt_*/audit/postrun_audit.json'))
            self.assertEqual(len(paths),1)
            r=json.loads(paths[0].read_text(encoding='utf-8'))
            self.assertEqual(r['severity'],'FAIL')
            self.assertEqual(r['first_failure']['code'],'family_config_invalid')
            self.assertFalse(list(Path(tmp).rglob('task.json')))

    def test_slow_audit_never_blocks_flush_beyond_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('market_radar.audit_runtime.publish',side_effect=lambda *a,**kw: time.sleep(.3)):
                a=AuditSession(Path(tmp)/('attempt_'+AID),scheduler=MemoryScheduler(),command=['C:/fake.exe'])
                start=time.monotonic(); self.assertFalse(a.flush(.02)); self.assertLess(time.monotonic()-start,.2)
                a.flush(2); a.close()

    def test_write_failure_is_visible_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp, patch('market_radar.audit_runtime.write_output',side_effect=OSError()):
            a=AuditSession(tmp,scheduler=MemoryScheduler(),command=['C:/fake.exe'])
            a.flush(2); self.assertEqual(a.error,'audit_write_failed'); a.close()


class MemoryScheduler:
    def __init__(self): self.tasks={}; self.fail=False
    def register(self,state,path):
        if self.fail: raise RuntimeError('synthetic')
        if state['task_name'] in self.tasks: raise RuntimeError('duplicate')
        self.tasks[state['task_name']]=task_xml(state)
    def query(self,state): return self.tasks.get(state['task_name'])
    def delete(self,state):
        if self.fail: raise RuntimeError('synthetic')
        self.tasks.pop(state['task_name'],None)
