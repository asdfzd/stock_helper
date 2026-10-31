"""Bounded, read-only interpretation of existing evidence. Never opens SQLite/ZIP.

This is an operational summary, not another validator or a shutdown permission.
Only explicitly selected numeric/enumerated fields leave the evidence reader.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from . import __version__
from .models import instant, utc
from tools.one_hour_runner.quality import POLICY

SCHEMA = 1
MAX_INPUT = 2 * 1024 * 1024
MAX_OUTPUT = 128 * 1024
OWNER = 'MarketRadarAudit/1'
ID = re.compile(r'[a-f0-9]{32}\Z')
RUN = re.compile(r'run_[a-f0-9]{32}\Z')
SEVERITIES = {'PASS', 'WARNING', 'FAIL', 'UNKNOWN'}
CODES = frozenset('''paper_processing_delay paper_processing_error paper_queue_overflow
clock_jump scheduler_gap_or_resume operator_cancelled user_requested late_launch
no_remaining_preopen_session_today collector_already_running other_collection_application
credentials_file_missing credentials_missing credentials_missing_or_wrong_mode
data_or_project_root_missing shared_cache_header_invalid insufficient_disk_space
path_access_failed windows_timezone_mismatch unstable_system_clock windows_required
family_config_invalid shared_cache_missing
insufficient_disk startup_history_not_ready wrong_windows_timezone clock_unstable
supervisor_exception finalization_exception watchdog_registration_failed
paper_or_rotation_error worker_still_active diagnostic_zip_failed
first_hour_batch_coverage first_hour_batch_gap first_hour_ws_connected_coverage
first_hour_ws_connected_gap first_hour_ws_subscribed_coverage first_hour_ws_subscribed_gap
first_hour_fresh_observation_coverage first_hour_fresh_observation_gap
first_hour_subscribed_observation_coverage first_hour_post_detection_ohlcv_coverage
opening_hour_empty raw_dropped_count ws_gap_count stale_count
process_not_running supervisor_unresponsive gui_unresponsive
shutdown_inhibited shutdown_cancel_failed finalization_AttributeError
family_launch_failed audit_registration_failed audit_write_failed'''.split())


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def boolean(value):
    return value if isinstance(value, bool) else None


def enum(value, choices, default='UNKNOWN'):
    return value if isinstance(value, str) and value in choices else default


def stamp(value):
    try:
        return utc(instant(value)) if isinstance(value, str) and len(value) <= 40 else None
    except (ValueError, TypeError, OverflowError):
        return None


def failure_code(value):
    code = value.split(':', 1)[0] if isinstance(value, str) else None
    return code if code in CODES else 'unclassified_source_failure'


def confined(root, relative):
    root, relative = Path(root).absolute(), Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('foreign_audit_path')
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('foreign_audit_path')
    for path in (root, *[root.joinpath(*relative.parts[:n]) for n in range(1, len(relative.parts)+1)]):
        if path.is_symlink() or (path.exists() and getattr(path.stat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('reparse_audit_path')
    return target


def read_json(root, relative, *, limit=MAX_INPUT):
    path = confined(root, relative)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('audit_input_too_large')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('audit_input_not_object')
    return value


def write_output(folder, name, value):
    if name not in {'state.json', 'task.json', 'postrun_audit.json', 'postrun_audit.txt',
                    'outbox.json', 'delivery.json', 'ai_review_request.json', 'task_result.json'}:
        raise ValueError('not_audit_output')
    path = confined(folder, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)).encode('utf-8')
    if len(data) > MAX_OUTPUT:
        raise ValueError('audit_output_too_large')
    temp = path.with_name('.audit_' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def validate_state(state):
    if state.get('schema') != SCHEMA or state.get('owner') != OWNER or not ID.fullmatch(str(state.get('audit_id', ''))):
        raise ValueError('invalid_audit_identity')
    if not stamp(state.get('started_at')):
        raise ValueError('invalid_audit_start')
    run = state.get('run_folder')
    if run is not None and not RUN.fullmatch(str(run)):
        raise ValueError('invalid_audit_run')
    return state


def process_alive(state):
    import psutil
    try:
        p = psutil.Process(state['pid'])
        return abs(p.create_time() - state['process_created']) < .01
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, KeyError, TypeError):
        return None


def analyze(state, documents, *, now=None, alive=None, archive_present=None, read_errors=()):
    """Pure verdict; missing evidence never becomes PASS. Inputs may be untrusted."""
    now = now or datetime.now(timezone.utc)
    summary = documents.get('summary', {})
    quality = documents.get('quality', {})
    runtime = documents.get('runtime', {})
    performance = documents.get('performance', {})
    wd = documents.get('watchdog', {})
    power = documents.get('power', {})
    collection = summary.get('collection_quality') or {}
    protocol = quality.get('collection_protocol') or documents.get('lifecycle', {}).get('collection_protocol') or {}
    telemetry = state.get('telemetry') or {}
    totals = state.get('continuity_totals') or {}
    research = enum(summary.get('research_status'), {'VERIFIED', 'FAILED'})
    preservation = enum(summary.get('preservation_status'), {'VERIFIED', 'BLOCKED', 'INCOMPLETE'})
    terminal = state.get('terminal') is True
    issues = []
    def issue(level, code):
        if len(issues) < 40 and not any(i['code'] == code for i in issues):
            issues.append(dict(severity=level, code=code))
    if research == 'FAILED': issue('FAIL', 'research_failed')
    if preservation in {'BLOCKED', 'INCOMPLETE'}: issue('FAIL', 'preservation_not_verified')
    if research == 'UNKNOWN' or preservation == 'UNKNOWN': issue('UNKNOWN', 'final_verdict_unavailable')
    if read_errors: issue('UNKNOWN', 'evidence_read_incomplete')
    if preservation == 'VERIFIED' and archive_present is False: issue('FAIL', 'expected_archive_missing')
    archive_kind = enum(summary.get('archive_kind'), {'research', 'diagnostic'})
    archive_status = ('RECORDED_VERIFIED_PRESENT' if preservation == 'VERIFIED' and archive_present is True and archive_kind != 'UNKNOWN' else
                      'MISSING' if archive_present is False else 'NOT_VERIFIED')
    if preservation == 'VERIFIED' and archive_status == 'NOT_VERIFIED':
        issue('UNKNOWN', 'archive_evidence_unconfirmed')
    task_status = enum(state.get('audit_task_status'), {'REGISTERED', 'REGISTRATION_FAILED', 'NOT_REGISTERED', 'CHECKED', 'CANCELLED', 'CANCEL_FAILED'})
    if task_status in {'REGISTRATION_FAILED', 'CANCEL_FAILED'}: issue('WARNING', 'independent_audit_task_failed')
    watchdog_status = enum(wd.get('status'), {'ARMED', 'PREPARED', 'CANCELLED', 'NORMAL_REQUEST_ACCEPTED',
        'NORMAL_REQUESTING', 'NORMAL_REQUEST_FAILED', 'DRY_RUN_FIRED', 'REGISTRATION_FAILED',
        'STATE_UNKNOWN', 'INVOCATION_FAILED_OR_UNKNOWN', 'EMERGENCY_BLOCKED', 'EMERGENCY_REQUEST_FAILED_OR_UNKNOWN',
        'EMERGENCY_REQUEST_ACCEPTED', 'NOT_REGISTERED'})
    if watchdog_status == 'DRY_RUN_FIRED': issue('WARNING', 'watchdog_dry_run_fired')
    if watchdog_status in {'REGISTRATION_FAILED', 'NORMAL_REQUEST_FAILED', 'STATE_UNKNOWN',
                          'INVOCATION_FAILED_OR_UNKNOWN', 'EMERGENCY_BLOCKED', 'EMERGENCY_REQUEST_FAILED_OR_UNKNOWN'} or wd.get('error'):
        issue('WARNING', 'watchdog_operation_unconfirmed')
    started = stamp(state.get('started_at'))
    heartbeat = stamp(state.get('heartbeat'))
    due = stamp(state.get('audit_due'))
    overdue = bool(due and now >= instant(due))
    liveness = 'FINAL_RECORDED' if terminal and research != 'UNKNOWN' else 'UNKNOWN'
    if not started:
        issue('UNKNOWN', 'program_start_not_recorded')
    elif not terminal and overdue:
        if alive is False:
            liveness = 'PROCESS_MISSING'; issue('FAIL', 'process_missing_without_final_result')
        elif heartbeat is None or (now-instant(heartbeat)).total_seconds() > 15:
            liveness = 'HEARTBEAT_STALE'; issue('FAIL', 'supervisor_heartbeat_stale')
        elif not stamp(state.get('gui_heartbeat')) or (now-instant(state['gui_heartbeat'])).total_seconds() > 15:
            liveness = 'GUI_HEARTBEAT_STALE'; issue('FAIL', 'gui_heartbeat_stale')
        else:
            liveness = 'RUNNING_AFTER_AUDIT_DEADLINE'; issue('WARNING', 'result_not_final_at_audit_deadline')
    if not state.get('armed_at'):
        issue('FAIL' if terminal else 'UNKNOWN', 'reservation_not_recorded')
    failure = state.get('first_failure') or {}
    firsts = []
    for item in (failure, runtime.get('first_error') or {}, performance.get('first_error') or {}):
        if item:
            firsts.append(dict(code=failure_code(item.get('code')), occurred_at=stamp(item.get('wall_utc') or item.get('occurred_at_utc'))))
    firsts.sort(key=lambda v: v['occurred_at'] or '9999')
    first_failure = firsts[0] if firsts else None
    if first_failure and failure:
        # A generic supervisor wrapper is often recorded immediately before the
        # specific RunBlocked reason. Preserve both times; never backdate either.
        first_failure.update(primary_reason=failure_code(failure.get('code')),
            primary_reason_recorded_at=stamp(failure.get('occurred_at_utc') or failure.get('wall_utc')))
    if first_failure: issue('FAIL', 'recorded_first_failure')
    stop = summary.get('collection_stop') or {}
    stop_reason = enum(stop.get('reason'), {'planned_65_minute_stop', 'operator_cancelled', 'user_requested', 'startup_failure', 'supervisor_exception', 'clock_jump'})
    if state.get('user_stopped') is True or stop_reason in {'operator_cancelled', 'user_requested'}:
        issue('WARNING', 'user_stopped_collection')
    collection_status = enum(collection.get('status'), {'PASS', 'FAIL'})
    if collection_status == 'FAIL': issue('FAIL', 'existing_collection_quality_failed')
    if collection_status == 'UNKNOWN': issue('UNKNOWN', 'collection_quality_unavailable')
    first_batch = stamp(summary.get('first_batch') or protocol.get('first_regular_batch_utc'))
    if terminal and first_batch is None: issue('UNKNOWN', 'first_batch_not_recorded')
    receipt_age = number(telemetry.get('last_fe_age_seconds'))
    connected = boolean(telemetry.get('ws_connected'))
    sticky = boolean(telemetry.get('sticky_gap'))
    if connected is False:
        ws_status = 'DISCONNECTED_OBSERVED'; issue('WARNING', 'ws_disconnected_observed')
    elif (receipt_age is not None and receipt_age > POLICY['maximum_price_age_seconds']) or (number(totals.get('maximum_fe_age_seconds')) or 0) > POLICY['maximum_price_age_seconds']:
        ws_status = 'RECEIPT_GAP_OBSERVED'; issue('WARNING', 'fe_receipt_age_exceeded')
    elif connected is True and receipt_age is not None and receipt_age >= 0:
        ws_status = 'STICKY_GAP_WITH_RECENT_RECEIPT' if sticky else 'RECENT_RECEIPT_OBSERVED'
        if sticky: issue('WARNING', 'sticky_gap_with_recent_fe')
    else:
        ws_status = 'UNKNOWN'
        if terminal: issue('UNKNOWN', 'fe_receipt_unavailable')
    if (number(telemetry.get('raw_dropped')) or 0) > 0: issue('FAIL', 'raw_data_dropped')
    if (number(totals.get('disconnected_samples')) or 0) > 0: issue('WARNING', 'ws_disconnected_observed')
    first_delay = None
    for item in performance.get('first_failure_events') or []:
        if isinstance(item, dict) and (number(item.get('total_receipt_to_finish_ms')) or 0) > 2000:
            first_delay = {k: number(item.get(k)) for k in ('queue_wait_ms', 'queue_length', 'processing_ms', 'total_receipt_to_finish_ms')}
            first_delay['received_at'] = stamp(item.get('received_utc'))
            break
    paper_failed = boolean(telemetry.get('paper_error'))
    rotation_failed = boolean(telemetry.get('rotation_error'))
    paper_status = 'FAILED' if paper_failed or (performance.get('first_error') or {}).get('code') == 'paper_processing_delay' else 'VERIFIED' if research == 'VERIFIED' else 'UNKNOWN'
    rotation_status = 'FAILED' if rotation_failed else 'VERIFIED' if research == 'VERIFIED' else 'UNKNOWN'
    if paper_status == 'FAILED' or rotation_status == 'FAILED': issue('FAIL', 'paper_or_rotation_failed')
    opening_status = enum(summary.get('opening_hour_status'), {'COMPLETE', 'PARTIAL', 'EMPTY', 'INCOMPLETE'})
    if opening_status in {'EMPTY', 'INCOMPLETE'}: issue('WARNING', 'opening_evidence_incomplete')
    rvol = quality.get('rvol') or {}
    from .research.validator import ValidatorPolicy
    rvol_pct = number(rvol.get('available_pct'))
    if rvol_pct is not None and rvol_pct < ValidatorPolicy().min_rvol_available_pct: issue('WARNING', 'rvol_availability_below_existing_policy')
    shutdown = enum(power.get('status'), {'REQUESTING', 'SCHEDULED', 'WAITING_SHUTDOWN', 'CANCELLED', 'CANCEL_FAILED', 'CANCEL_REQUESTED'})
    if watchdog_status == 'NORMAL_REQUEST_ACCEPTED' and shutdown == 'SCHEDULED':
        shutdown = 'REQUEST_ACCEPTED_POWER_OFF_NOT_VERIFIED'
    elif summary.get('shutdown_disabled') is True or state.get('shutdown_disabled') is True:
        shutdown = 'DISABLED'
    elif terminal and shutdown in {'UNKNOWN', 'REQUESTING'}:
        issue('UNKNOWN', 'shutdown_request_unconfirmed')
    if overdue and terminal and shutdown == 'WAITING_SHUTDOWN':
        issue('WARNING', 'shutdown_not_requested_at_audit_deadline')
    if not issues and (first_batch is None or opening_status == 'UNKNOWN'):
        issue('UNKNOWN', 'required_evidence_unavailable')
    severity = next((s for s in ('FAIL', 'UNKNOWN', 'WARNING') if any(i['severity'] == s for i in issues)), 'PASS')
    measurements = {}
    for key, item in (performance.get('measurements') or {}).items():
        if re.fullmatch(r'(?:v1|v2|paper|baseline|rotation|frame|tick)(?:\.[a-z_]+){1,3}', key) and len(measurements) < 32 and isinstance(item, dict):
            measurements[key] = {k: number(item.get(k)) for k in ('count', 'mean_ms', 'p95_ms', 'p99_ms', 'max_ms')}
    timelines = {key: {k: number(value.get(k)) for k in ('expected_slots', 'observed_slots', 'coverage', 'maximum_gap_seconds')}
        for key, value in (collection.get('timelines') or {}).items() if key in {'batch', 'ws_connected', 'ws_subscribed', 'fresh_observation'} and isinstance(value, dict)}
    run_id = state.get('collector_run_id')
    result = dict(audit_schema=SCHEMA, et_date=state.get('et_date') if re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(state.get('et_date'))) else None,
        attempt_id=state.get('audit_id') if ID.fullmatch(str(state.get('audit_id', ''))) else None,
        collector_run_id=run_id if ID.fullmatch(str(run_id or '')) else None, code_version=__version__,
        detected_at_utc=utc(now), last_heartbeat_at=heartbeat, started_at=started, armed_at=stamp(state.get('armed_at')),
        collector_status=collection_status, research_status=research, preservation_status=preservation,
        paper_status=paper_status, rotation_status=rotation_status, opening_status=opening_status,
        ws_status=ws_status, archive_status=archive_status, archive_kind=archive_kind,
        shutdown_request_status=shutdown, actual_power_off='NOT_VERIFIED', watchdog_status=watchdog_status,
        independent_audit_status=task_status, process_status=liveness, severity=severity, first_failure=first_failure,
        findings=issues, upload_status='DISABLED', ai_status='DISABLED',
        evidence_paths=[], evidence_read_errors=len(read_errors),
        execution=dict(first_batch=first_batch, actual_start=stamp(summary.get('actual_start') or protocol.get('actual_start')),
            actual_stop=stamp(summary.get('actual_stop') or protocol.get('actual_stop')),
            stop_requested_at=stamp(summary.get('stop_requested_at') or stop.get('requested_at')), stop_reason=stop_reason,
            regular_duration_seconds=number(protocol.get('regular_duration_seconds')), batch_count=number(summary.get('batch_count')),
            snapshot_count=number(quality.get('snapshot_count') if quality.get('snapshot_count') is not None else summary.get('snapshot_count')),
            candidate_count=number(quality.get('candidate_count'))),
        continuity=dict(last_live_sample_at=stamp(telemetry.get('sampled_at')), last_fe_at=stamp(telemetry.get('last_fe_at')),
            last_fe_age_seconds=receipt_age, connected=connected, sticky_gap=sticky,
            generation=number(telemetry.get('generation')), fe_count=number(telemetry.get('fe_count')),
            raw_dropped=number(telemetry.get('raw_dropped')), timelines=timelines,
            sampled_totals={k: number(totals.get(k)) for k in ('samples', 'disconnected_samples', 'sticky_gap_samples',
                'maximum_fe_age_seconds', 'maximum_generation')}, first_receipt_gap_at=stamp(totals.get('first_receipt_gap_at')),
            scope='sampled_local_receipts_not_continuous_exchange_delivery'),
        research=dict(first_delay_over_2000ms=first_delay, maximum_queue=number(performance.get('maximum_queue_length')),
            measurements=measurements, rvol_available_pct=rvol_pct,
            rvol_missing={k: number(rvol.get(k)) for k in ('warming', 'incomplete_history', 'not_eligible', 'zero_denominator', 'unknown')},
            opening_coverage={k: number((collection.get('opening_coverage') or {}).get(k)) for k in
                ('candidate_count', 'eligible_candidate_count', 'expected_post_detection_minutes', 'complete_post_detection_minutes', 'complete_post_detection_coverage', 'subscribed_ohlcv_coverage')}),
        preservation=dict(shutdown_eligible=boolean(summary.get('shutdown_eligible')), archive_reverified_by_audit=False,
            source='existing_one_hour_preservation_verdict', original_validator=enum(summary.get('original_validation_overall'), {'PASS', 'WARN', 'FAIL'})),
        recommended_manual_action=('보존된 최초 오류와 관련 JSON을 확인하고 다음 수집 전에 점검하세요. 자동 수정은 수행하지 않습니다.' if severity != 'PASS' else
            '기록된 범위에서 추가 실패가 발견되지 않았습니다. 실제 PC 종료와 전략 수익성은 별도 검증입니다.'))
    return result


def inspect_attempt(attempt, *, state=None, now=None, alive=None):
    started = time.monotonic()
    state = validate_state(state or read_json(attempt, 'audit/state.json', limit=MAX_OUTPUT))
    docs, paths, errors = {}, [], []
    state = dict(state)
    try:
        task = read_json(attempt, 'audit/task.json', limit=MAX_OUTPUT)
        if task.get('audit_id') != state['audit_id']: raise ValueError('audit_task_identity')
        state['audit_task_status'] = task.get('status')
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError):
        errors.append('audit_task')
    folder = state.get('run_folder')
    day, run_id = state.get('et_date'), state.get('collector_run_id')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(day)):
        day = None
    if folder:
        inputs = dict(summary=f'{folder}/one_hour_run_summary.json', runtime=f'{folder}/diagnostics/runtime.json',
            performance=f'{folder}/paper_v1/paper_performance.json', lifecycle=f'{folder}/radar/dashboard_run.json', power=f'{folder}/shutdown_status.json')
        if day: inputs['quality'] = f'{folder}/radar/export/{day}/quality_summary.json'
        if ID.fullmatch(str(run_id or '')): inputs['watchdog'] = f'watchdogs/{run_id}/watchdog.json'
        for key, relative in inputs.items():
            if time.monotonic()-started > 1.5:
                errors.append('read_budget'); break
            try:
                docs[key] = read_json(attempt, relative)
                paths.append(relative)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, RecursionError):
                errors.append(key)
        s = docs.get('summary', {})
        if s and (s.get('run_id') != run_id or s.get('session_date') != day):
            docs.pop('summary'); errors.append('identity')
    archive_present = None
    kind = docs.get('summary', {}).get('archive_kind')
    if folder and day and ID.fullmatch(str(run_id or '')) and kind in {'research', 'diagnostic'}:
        relative = f'{folder}/one_hour_{kind}_{day}_{run_id}_v2.zip'
        try:
            archive_present = confined(attempt, relative).is_file()
            if archive_present: paths.append(relative)
        except (OSError, ValueError):
            errors.append('archive')
    try:
        result = analyze(state, docs, now=now, alive=process_alive(state) if alive is None else alive,
                         archive_present=archive_present, read_errors=errors)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        result = analyze(state, {}, now=now, alive=alive, read_errors=['invalid_evidence_shape'])
    result['evidence_paths'] = paths
    return result


def publish(attempt, *, state=None, now=None, alive=None):
    result = inspect_attempt(attempt, state=state, now=now, alive=alive)
    folder = confined(attempt, 'audit')
    write_output(folder, 'postrun_audit.json', result)
    lines = [f"Market Radar {result['code_version']} / 자동 사후 감사 {SCHEMA}",
        f"거래일 {result['et_date']} · 판정 {result['severity']}",
        f"수집 {result['collector_status']} / 연구 {result['research_status']} / 보존 {result['preservation_status']}",
        f"Paper {result['paper_status']} / Rotation {result['rotation_status']} / Opening {result['opening_status']}",
        f"시세 {result['ws_status']} / ZIP {result['archive_status']} ({result['archive_kind']})",
        f"정상 종료 {result['shutdown_request_status']} / 실제 PC 종료 NOT_VERIFIED",
        f"90분 감시 {result['watchdog_status']} / 독립 감사 {result['independent_audit_status']}",
        '외부 전송 DISABLED / AI 분석 DISABLED',
        f"최초 오류 {result['first_failure']}", result['recommended_manual_action'],
        '연구 FAILED + 보존 VERIFIED는 연구 실패입니다. Fresh 저하는 실제 FE 수신 공백과 다를 수 있습니다.',
        '미실행·정전·OS 정지·로그아웃은 로컬 감사만으로 원격 통보할 수 없습니다.',
        *[f"[{i['severity']}] {i['code']}" for i in result['findings']],
        '근거(이번 attempt 기준 상대 경로):', *result['evidence_paths']]
    write_output(folder, 'postrun_audit.txt', '\n'.join(lines)+'\n')
    return result
