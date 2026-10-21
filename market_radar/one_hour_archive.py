"""GUI archive v2: research verdict and durable preservation are independent.

Originals are only read. Export copies may redact descriptive cells, with row/
column and original hashes recorded. SQLite is inspected through DB/WAL copies.
The standalone runner and its v1 success policy remain unchanged.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from .evidence_io import copied_database
from .failure_evidence import exception_evidence, safe_path
from .models import instant
from tools.one_hour_runner.core import atomic_json, paper_health, threads_finished
from tools.one_hour_runner.artifacts import (GROUPS, check_identity, collect_evidence,
    inspect_text, local_file, require, read_json, verify_research_exports, _paper_database)
from tools.one_hour_runner.system import RunBlocked

SCHEMA = 2
ESSENTIAL = {'radar/candidates.csv', 'radar/snapshots.csv', 'radar/metadata.json',
    'radar/quality_summary.json', 'radar/validation_summary.json',
    'paper_v1/paper_trades.csv', 'paper_v1/paper_trade_events.csv', 'paper_v1/paper_trade_marks.csv',
    'rotation_v2/paper_trades.csv', 'rotation_v2/paper_trade_marks.csv',
    'opening_hour/candidates.csv', 'opening_hour/bars_1m.csv', 'opening_hour/metadata.json',
    'opening_hour/quality.json', 'opening_hour/summary.json'}
REDACTABLE = {'symbol_type_evidence', 'discovery_symbol_type_evidence', 'raw_type_fields'}
EXTRA = {'diagnostics.json', 'one_hour_run_summary.json', 'lifecycle.json'}
OPENING_DB = 'opening_hour/opening_hour.db'
OPENING_DIAGNOSTICS = 'opening_hour/processing_diagnostics.json'
OPENING_DIAGNOSTICS_SOURCE = 'radar/opening_hour_diagnostics.json'
RUN_DIAGNOSTICS = 'diagnostics/collection.json'
CORE_ORIGINALS = {'radar/stock_helper_market_kiwoom_v2_1.db', 'radar/dashboard_run.json',
                  'paper_v1/paper.db', 'rotation_v2/review.db'}
# A narrow exception for failure BEFORE the derived Opening DB exists. No other
# missing DB/export can use it. Complete research retains all 15 essential files.
MISSING_OPENING_POLICY = 'opening_failed_before_database_v1'
ALLOWED = {f'{g}/{n}' for g, names in GROUPS.items() for n in names} | EXTRA | {'paper_v1/paper_performance.json', OPENING_DIAGNOSTICS, RUN_DIAGNOSTICS}


def essential_for(policy):
    require(policy in {'complete', MISSING_OPENING_POLICY}, 'archive_evidence_policy')
    return (ESSENTIAL - {n for n in ESSENTIAL if n.startswith('opening_hour/')} | {OPENING_DIAGNOSTICS}
            if policy == MISSING_OPENING_POLICY else ESSENTIAL)


def validate_opening_failure(record, root, run_id, day):
    require(not (root/OPENING_DB).exists(), 'opening_database_presence_changed')
    require(record.get('diagnostic_schema') == 1 and record.get('run_id') == run_id
            and record.get('session_date') == day, 'opening_failure_identity')
    error = record.get('exception', {})
    require(record.get('status') == 'INCOMPLETE' and record.get('output_database_present') is False
            and record.get('error') == error.get('type') and bool(error.get('phase'))
            and isinstance(error.get('traceback'), list) and bool(error['traceback'])
            and error.get('message_omitted') is True
            and record.get('paths', {}).get('output_db') == safe_path(root/OPENING_DB)
            and not record.get('persistence_exception'), 'opening_failure_evidence_invalid')


def opening_policy(runner):
    root, c = runner.folder.resolve(), runner.controller
    if (root/OPENING_DB).exists() or c.opening_hour_result.get('status') != 'INCOMPLETE':
        return 'complete'
    require(c.opening_hour_path is not None and Path(c.opening_hour_path).resolve() == root/OPENING_DB,
            'foreign_opening_database')
    record = read_json(local_file(root/OPENING_DIAGNOSTICS_SOURCE, root))
    validate_opening_failure(record, root, runner.summary['run_id'], runner.summary['session_date'])
    require(record == c.opening_hour_result.get('diagnostics'), 'opening_failure_memory_mismatch')
    return MISSING_OPENING_POLICY

def digest(data):
    return dict(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))

def file_digest(path):
    before = (path.stat().st_size, path.stat().st_mtime_ns)
    with path.open('rb') as stream:
        value = dict(sha256=hashlib.file_digest(stream, 'sha256').hexdigest(), size_bytes=before[0])
    require(before == (path.stat().st_size, path.stat().st_mtime_ns), 'source_changed_during_read')
    return value

def code(exc):
    value = str(exc) if isinstance(exc, RunBlocked) else type(exc).__name__
    return value if re.fullmatch(r'[A-Za-z0-9_]+', value) else 'evidence_error'

def safe_description(value):
    """Keep strict scanner; redact only leaf descriptions, including sensitive keys."""
    from market_radar.research.review_export import SENSITIVE
    from tools.one_hour_runner.artifacts import EXTRA_SENSITIVE
    def suspicious(text):
        return bool(SENSITIVE.search(text) or EXTRA_SENSITIVE.search(text))
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if suspicious(key):
                result['removed_description_'+str(len(result))] = '[REDACTED]'
            else:
                result[key] = safe_description(item)
        return result
    if isinstance(value, list):
        return [safe_description(v) for v in value]
    return '[REDACTED]' if isinstance(value, str) and suspicious(value) else value

def export_copy(data, name, run_id, day):
    """No exemptions to credential detection. Critical data is never redacted."""
    edits = []
    if name.endswith('.json'):
        check_identity(json.loads(data.decode('utf-8-sig')), run_id, day)
        inspect_text(io.BytesIO(data))
        return data, edits
    text = io.StringIO(data.decode('utf-8-sig'), newline='')
    reader = csv.DictReader(text)
    require(bool(reader.fieldnames) and len(set(reader.fieldnames)) == len(reader.fieldnames), 'invalid_csv_header')
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames, lineterminator='\n')
    writer.writeheader()
    for index, row in enumerate(reader, 1):
        require(None not in row and all(v is not None for v in row.values()), 'invalid_csv_row')
        check_identity(row, run_id, day)
        for column in sorted(REDACTABLE & row.keys()):
            value = row[column]
            try:
                inspect_text(io.BytesIO(value.encode('utf-8')))
            except RunBlocked:
                parsed = json.loads(value) if value.startswith(('{', '[')) else value
                clean = safe_description(parsed)
                row[column] = json.dumps(clean, ensure_ascii=False, sort_keys=True) if not isinstance(clean, str) else clean
                edits.append(dict(row=index, column=column, action='description_redacted', reason='privacy_scanner_match'))
        writer.writerow(row)
    result = out.getvalue().encode('utf-8') if edits else data
    inspect_text(io.BytesIO(result))  # Includes keys, values and unrecognized columns.
    return result, edits

def local_inventory(root, *, policy='complete'):
    result = {}
    if (root/RUN_DIAGNOSTICS).exists():
        result[RUN_DIAGNOSTICS] = file_digest(local_file(root/RUN_DIAGNOSTICS, root))
    for group in GROUPS:
        for path in (root/group).rglob('*'):
            require(not path.is_symlink() and path.resolve().is_relative_to(root), 'foreign_output_path')
            if path.is_file():
                result[path.relative_to(root).as_posix()] = file_digest(path)
    essential_for(policy)
    required = CORE_ORIGINALS | ({OPENING_DIAGNOSTICS_SOURCE} if policy == MISSING_OPENING_POLICY else {OPENING_DB})
    require(required <= set(result), 'local_original_missing')
    return result

def validate_local(root, inventory):
    root = Path(root).resolve()
    require(bool(inventory), 'local_inventory_missing')
    for name, expected in inventory.items():
        path = local_file(root/name, root)
        require(file_digest(path) == expected, 'local_original_changed')

def validate_continuation(clock, plan, protocol, stop):
    """Recheck the recorded exception; a FAILED clock run is never a normal run."""
    import math
    require(clock.get('policy') == 'bounded_forward_v1' and clock.get('research_valid') is False
            and clock.get('absolute_utc_verified') is False, 'clock_continuation_policy')
    anomalies = clock.get('anomalies', [])
    require(0 < len(anomalies) <= 64, 'clock_continuation_missing')
    start, deadline, end = (clock.get(k) for k in
                           ('start_monotonic', 'deadline_monotonic', 'stopped_monotonic'))
    require(all(isinstance(v, (int, float)) and math.isfinite(v) for v in (start, deadline, end)),
            'clock_continuation_deadline')
    require(deadline == start+3900 and 0 <= end-deadline <= 1, 'clock_continuation_deadline')
    previous = start
    for event in anomalies:
        sample = event.get('sample', {})
        wall, mono, drift = (sample.get(k) for k in
                            ('wall_delta_seconds', 'monotonic_delta_seconds', 'divergence_seconds'))
        require(event.get('classification') == 'bounded_forward_adjustment_candidate'
                and event.get('action') == 'continue_raw' and event.get('research_action') == 'fail_closed'
                and event.get('source_verified') is False, 'clock_continuation_not_safe')
        require(all(isinstance(v, (int, float)) and math.isfinite(v) for v in (wall, mono, drift))
                and 0 < mono <= 5 and 0 < wall <= 5 and drift > 2
                and abs(wall-mono-drift) < 1e-6, 'clock_continuation_sample')
        require(previous <= sample['previous_monotonic'] <= sample['monotonic'] <= end
                and abs(sample['monotonic']-sample['previous_monotonic']-mono) < 1e-6
                and abs((instant(sample['wall_utc'])-instant(sample['previous_wall_utc'])).total_seconds()-wall) < 1e-6,
                'clock_continuation_sample')
        previous = sample['monotonic']
    require(stop and stop.get('reason') == clock.get('stop_reason') == 'planned_65_minute_stop'
            and stop.get('monotonic') == end and stop.get('requested_at') == clock.get('requested_stop_utc')
            and clock.get('actual_start_utc') == protocol.get('actual_start')
            and clock.get('planned_stop_utc') == plan['stop'], 'clock_continuation_scope')
    require(instant(plan['stop']) <= instant(protocol['actual_stop']) < instant(plan['official_close']),
            'clock_continuation_scope')

def operational_evidence(runner):
    """Require clean write owners and original integrity, independent of quality.

    A documented interrupted or continued clock run still requires all evidence.
    User cancel, unknown abort, missing or still-writing DBs remain blocked.
    """
    c, root, plan = runner.controller, runner.folder.resolve(), runner.plan
    from .one_hour import PRESERVABLE_INTERRUPTS
    interrupted = (getattr(runner, 'stop_context', None) is not None
        and runner.stop_context['reason'] in PRESERVABLE_INTERRUPTS
        and bool(runner.failures) and not set(runner.failures)-PRESERVABLE_INTERRUPTS)
    continued = (getattr(runner, 'continue_after_clock_jump', False)
        and not getattr(runner, 'clock_abort', True) and set(runner.failures) == {'clock_jump'}
        and (getattr(runner, 'stop_context', None) or {}).get('reason') == 'planned_65_minute_stop')
    clock_affected = interrupted or continued
    require(not runner.cancel_event.is_set() and (not runner.failures or clock_affected), 'cancelled_or_interrupted')
    if getattr(runner, 'diagnostics', None) is not None:
        require(not runner.diagnostics.write_errors, 'diagnostics_write_failed')
        record = read_json(local_file(root/RUN_DIAGNOSTICS, root))
        require(record.get('run_id') == runner.summary['run_id'] and record.get('session_date') == runner.summary['session_date']
                and not record.get('persistence_errors'), 'runtime_diagnostics_invalid')
        if clock_affected:
            require(any(e.get('code') == 'collector_stop_requested' and e.get('stop') == runner.stop_context
                        for e in record.get('events', [])), 'stop_caller_not_preserved')
        if continued:
            require(record.get('collection_clock') == runner.collection_clock, 'clock_continuation_not_preserved')
    else:
        require(not clock_affected, 'stop_caller_not_preserved')
    require(threads_finished(c), 'worker_still_active')
    require(c.state == 'STOPPED' and c.lifecycle.get('clean_shutdown') is True and not c.worker.error, 'collector_not_clean')
    run_id, day = runner.summary['run_id'], runner.summary['session_date']
    require(c.lifecycle.get('run_id') == run_id and plan['session_date'] == day, 'controller_identity_changed')
    require(c.config.data_dir.resolve() == root/'radar', 'foreign_radar_root')
    require(Path(c.paper.path).resolve() == root/'paper_v1/paper.db' and
            Path(c.paper.review_path).resolve() == root/'rotation_v2/review.db', 'foreign_paper_root')
    policy = opening_policy(runner)
    if policy == 'complete':
        require(Path(c.opening_hour_result['db_path']).resolve() == root/OPENING_DB, 'foreign_opening_database')
    life = read_json(local_file(root/'radar/dashboard_run.json', root))
    require(life['run_id'] == run_id and life['clean_shutdown'] is True, 'lifecycle_identity')
    p = life['collection_protocol']
    require(p == c.schedule.snapshot(), 'protocol_not_finalized')
    require(p.get('collection_mode') == 'Regular Session' and p.get('max_ws_symbols') == 150, 'wrong_mode_or_slots')
    require(p.get('armed_before_open') is True and p.get('ready_before_open') is True, 'not_ready_before_open')
    require(p.get('actual_start') and p.get('actual_stop'), 'collection_not_started')
    require(0 <= (instant(p['actual_start'])-instant(plan['open'])).total_seconds() <= 1, 'start_outside_tolerance')
    if continued:
        validate_continuation(record['collection_clock'], plan, p, runner.stop_context)
        require(paper_health(c.paper)['failed'], 'clock_research_not_invalidated')
    if not clock_affected:
        require(0 <= (instant(p['actual_stop'])-instant(plan['stop'])).total_seconds() <= 1, 'early_or_late_stop')
    require(runner.requested_stop_at and abs((instant(runner.requested_stop_at)-instant(p['actual_stop'])).total_seconds()) <= 1, 'unplanned_stop')
    require(p.get('stop_reason') == 'user_requested', 'unplanned_stop')
    allowed = {'user_requested', 'stop_time_mismatch'} | (PRESERVABLE_INTERRUPTS if clock_affected else set())
    require(set(p.get('protocol_reason','').split(';')) <= allowed, 'interrupted_protocol')
    from .research.validator import evidence_fingerprint
    validation=read_json(local_file(root/'radar/export'/day/'validation_summary.json',root))
    require(validation.get('session_date')==day and validation.get('evidence_sha256')==evidence_fingerprint(c.config.db_path,day),'stale_validation')
    if policy == MISSING_OPENING_POLICY or clock_affected:
        require(validation.get('integrity', {}).get('status') == 'PASS'
                and not any(e.get('section') == 'integrity' for e in validation.get('errors', [])), 'radar_integrity_not_verified')
        for path in (c.paper.path, c.paper.review_path):
            _paper_database(path, root, run_id, day)
    databases = ['radar/stock_helper_market_kiwoom_v2_1.db','paper_v1/paper.db','rotation_v2/review.db']
    if policy == 'complete':
        databases.append(OPENING_DB)
    for name in databases:
        with copied_database(local_file(root/name, root)) as db:
            require([r[0] for r in db.execute('PRAGMA quick_check')] == ['ok'], 'original_database_integrity')
            if name.startswith('radar/'):
                require(db.execute('SELECT COUNT(*) FROM radar_snapshots').fetchone()[0] > 0, 'no_research_observations')
            if name.startswith(('paper_v1/','rotation_v2/')):
                require([tuple(r) for r in db.execute('SELECT radar_run_id,session_date_et FROM paper_sessions')] == [(run_id,day)], 'paper_session_identity')
                require(not db.execute("SELECT 1 FROM paper_trades WHERE state='OPEN'").fetchone(), 'paper_open_after_stop')
    return life

def publish_archive(runner):
    root, c = runner.folder.resolve(), runner.controller
    require(threads_finished(c), 'worker_still_active')
    run_id, day = runner.summary['run_id'], runner.summary['session_date']
    require(bool(re.fullmatch('[a-f0-9]{32}', run_id or '')), 'invalid_run_identity')
    research = dict(runner.summary)
    def inspect_export(path):
        data=Path(path).read_bytes()
        export_copy(data,Path(path).name,run_id,day)
        return digest(data)  # DB re-export equality still compares unmodified bytes.
    try:
        _, research = collect_evidence(runner,inspect=inspect_export,include_review=False)
    except Exception as exc:
        research.update(status='FAILED', failures=[code(exc)], shutdown_eligible=False,
                        research_exception=exception_evidence(exc, 'collect_research_evidence'))
    failed = research.get('status') != 'VERIFIED'
    kind = 'diagnostic' if failed else 'research'
    policy = opening_policy(runner)
    require(policy == 'complete' or failed, 'research_cannot_omit_opening')
    essential = essential_for(policy)
    folders = {g:root/g/'export'/day for g in GROUPS}
    opening = c.opening_hour_result.get('export_path')
    folders['opening_hour'] = Path(opening) if opening else root/'opening_hour/export'/day/'unavailable'
    payloads, provenance, omissions = {}, {}, {}
    for group, names in GROUPS.items():
        for name in names:
            key, path = group+'/'+name, folders[group]/name
            try:
                local_file(path, root)
                require(path.resolve().is_relative_to(root/group/'export'/day), 'foreign_export_root')
                data = path.read_bytes()
                source = file_digest(path)
                require(source == digest(data), 'export_changed_during_read')
                payloads[key], edits = export_copy(data, name, run_id, day)
                provenance[key] = dict(local_path=path.relative_to(root).as_posix(), original=source,
                                       transformations=edits, included=digest(payloads[key]))
            except Exception as exc:
                omissions[key] = dict(reason=code(exc), local_path=path.relative_to(root).as_posix() if path.is_relative_to(root) else None)
    if policy == MISSING_OPENING_POLICY:
        # Stale exports without their derived DB are not a substitute for a failure.
        require(not any(k.startswith('opening_hour/') for k in payloads), 'unexpected_opening_exports')
        for key, item in omissions.items():
            if key.startswith('opening_hour/'):
                require(item['local_path'] is not None and not (root/item['local_path']).exists(), 'unexpected_opening_exports')
                item['reason'] = 'opening_processing_failed_before_database'
        data = local_file(root/OPENING_DIAGNOSTICS_SOURCE, root).read_bytes()
        export_copy(data, OPENING_DIAGNOSTICS, run_id, day)
        payloads[OPENING_DIAGNOSTICS] = data
        provenance[OPENING_DIAGNOSTICS] = dict(local_path=OPENING_DIAGNOSTICS_SOURCE,
            original=digest(data), included=digest(data), transformations=[])
    runtime = dict(paper_health(c.paper), events={})
    for group, db_name in [('paper_v1','paper.db'),('rotation_v2','review.db')]:
        with copied_database(local_file(root/group/db_name,root)) as db:
            runtime['events'][group] = [dict(timestamp_utc=r[0], event_type=r[1], reason=json.loads(r[2]).get('reason'))
                for r in db.execute("SELECT timestamp_utc,event_type,evidence_json FROM paper_trade_events WHERE event_type IN ('DATA_INTEGRITY_INVALID','INTERRUPTED') ORDER BY event_id")]
    # Quality assessment uses originals on copies, never privacy-filtered populations.
    from tools.one_hour_runner.quality import assess_collection
    try:
        with copied_database(c.config.db_path) as db:
            research['collection_quality'] = assess_collection(db,folders['opening_hour']/'candidates.csv',
                folders['opening_hour']/'bars_1m.csv',instant(runner.plan['open']))
    except Exception as exc:
        research['quality_unavailable'] = code(exc)
    performance=root/'paper_v1/paper_performance.json'
    if performance.exists():
        data=local_file(performance,root).read_bytes()
        inspect_text(io.BytesIO(data))
        payloads['paper_v1/paper_performance.json']=data
        provenance['paper_v1/paper_performance.json']=dict(local_path='paper_v1/paper_performance.json',
            original=digest(data),included=digest(data),transformations=[])
    if getattr(runner, 'diagnostics', None) is not None:
        data = local_file(root/RUN_DIAGNOSTICS, root).read_bytes()
        export_copy(data, RUN_DIAGNOSTICS, run_id, day)
        payloads[RUN_DIAGNOSTICS] = data
        provenance[RUN_DIAGNOSTICS] = dict(local_path=RUN_DIAGNOSTICS,
            original=digest(data), included=digest(data), transformations=[])
    inventory = local_inventory(root, policy=policy)
    for item in omissions.values():
        item['local_preserved'] = item['local_path'] in inventory
    life = read_json(local_file(root/'radar/dashboard_run.json',root))
    blockers = []
    try:
        operational_evidence(runner)
        require(essential <= set(payloads), 'essential_evidence_missing')
        require(all(v['local_preserved'] or (policy == MISSING_OPENING_POLICY and k.startswith('opening_hour/'))
                    for k,v in omissions.items()), 'excluded_original_missing')
        # A Paper/clock failure may have short-circuited strict research validation.
        # Rebuild exports from committed copies before accepting preservation.
        files = {g+'/'+n: folders[g]/n for g in GROUPS for n in GROUPS[g]}
        verify_research_exports(c, root, day, files, inspect=inspect_export,
                                include_opening=policy != MISSING_OPENING_POLICY)
    except Exception as exc:
        blockers.append(code(exc))
    # The research verdict never becomes SUCCESS merely because shutdown is safe.
    summary = dict(research, archive_schema=SCHEMA, archive_kind=kind,
        status='FAILED' if failed else 'VERIFIED', research_status='FAILED' if failed else 'VERIFIED',
        research_failures=list(research.get('failures',[])), shutdown_eligible=not blockers,
        preservation_status='VERIFIED' if not blockers else 'BLOCKED',
        operational_failures=list(dict.fromkeys([*runner.failures, *blockers])), shutdown_blockers=blockers,
        collection_stop=getattr(runner, 'stop_context', None),
        collection_clock=getattr(runner, 'collection_clock', None),
        shutdown_disabled=runner.no_shutdown, original_validation_overall=read_json(folders['radar']/'validation_summary.json').get('overall'),
        opening_hour_status=c.opening_hour_result.get('status'), evidence_policy=policy)
    missing_originals = ({OPENING_DB: dict(reason='opening_processing_failed_before_database',
                         evidence=OPENING_DIAGNOSTICS)} if policy == MISSING_OPENING_POLICY else {})
    diagnostics = dict(archive_schema=SCHEMA, archive_kind=kind, run_id=run_id, session_date=day,
        research_status=summary['research_status'], failures=summary['research_failures'], runtime=runtime,
        collection_quality=research.get('collection_quality'), original_validator=read_json(folders['radar']/'validation_summary.json'),
        omissions=omissions, missing_originals=missing_originals,
        opening_hour=c.opening_hour_result.get('diagnostics'),
        usage='Failed research remains failed; observations retain original quality and missing values' if failed else 'Verified shortened research session; no profitability claim')
    payloads.update({'diagnostics.json':json.dumps(diagnostics,ensure_ascii=False,indent=2).encode(),
        'one_hour_run_summary.json':json.dumps(summary,ensure_ascii=False,indent=2).encode(),
        'lifecycle.json':json.dumps(life,ensure_ascii=False,indent=2).encode()})
    manifest = dict(manifest_schema=SCHEMA, archive_kind=kind, run_id=run_id, session_date=day,
        research_status=summary['research_status'], files={k:digest(v) for k,v in payloads.items()},
        provenance=provenance, omissions=omissions, local_originals=inventory,
        essential=sorted(essential), evidence_policy=policy, missing_originals=missing_originals,
        opening_hour_status=c.opening_hour_result.get('status'),
        validation=dict(local_integrity='PASS' if not blockers else 'BLOCKED',
                        operational_failures=blockers, archive_checks='required_on_read'),
        original_files_modified=False)
    for data in payloads.values(): inspect_text(io.BytesIO(data))
    encoded = json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    inspect_text(io.BytesIO(encoded))
    destination = root/f'one_hour_{kind}_{day}_{run_id}_v2.zip'
    require(not destination.exists(), 'archive_already_exists')
    fd, tmp = tempfile.mkstemp(prefix='preserve_',suffix='.tmp',dir=root)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED) as z:
            for name,data in payloads.items(): z.writestr(name,data)
            z.writestr('manifest.json',encoded)
        validate_archive(tmp,run_id,day,root,require_safe=not blockers)
        with open(tmp,'r+b') as f: os.fsync(f.fileno())
        os.replace(tmp,destination)
        validate_archive(destination,run_id,day,root,require_safe=not blockers)
    finally:
        Path(tmp).unlink(missing_ok=True)
    atomic_json(root/'one_hour_run_summary.json',summary)
    return destination,summary

def validate_archive(path,run_id,day,root,*,require_safe=True):
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        require(len(names)==len(set(names)) and set(names)<=ALLOWED|{'manifest.json'}, 'archive_allowlist')
        require(EXTRA|{'manifest.json'} <= set(names), 'archive_required')
        require(z.testzip() is None,'archive_crc')
        manifest=json.loads(z.read('manifest.json'))
        require(manifest.get('manifest_schema')==SCHEMA and manifest.get('run_id')==run_id and manifest.get('session_date')==day,'archive_identity')
        require(set(manifest['files'])==set(names)-{'manifest.json'},'archive_manifest_files')
        policy = manifest.get('evidence_policy', 'complete')
        essential = essential_for(policy)
        require(set(manifest['essential'])==essential,'archive_essential_policy')
        for name in names:
            data=z.read(name)
            inspect_text(io.BytesIO(data))
            if name!='manifest.json': require(digest(data)==manifest['files'][name],'archive_hash_mismatch')
            if name.endswith('.json'): check_identity(json.loads(data),run_id,day)
            else: export_copy(data,name,run_id,day)
        summary=json.loads(z.read('one_hour_run_summary.json'))
        diagnostic=json.loads(z.read('diagnostics.json'))
        if summary.get('integration_version') in {'3', '4'}:
            require(RUN_DIAGNOSTICS in names and RUN_DIAGNOSTICS in manifest['local_originals'],
                    'runtime_diagnostics_missing')
            runtime_record = json.loads(z.read(RUN_DIAGNOSTICS))
            require(runtime_record.get('run_id') == run_id and runtime_record.get('session_date') == day
                    and not runtime_record.get('persistence_errors'), 'runtime_diagnostics_invalid')
            if summary.get('integration_version') == '4':
                require(summary.get('collection_clock') == runtime_record.get('collection_clock'),
                        'clock_continuation_not_preserved')
                clock = summary.get('collection_clock') or {}
                if clock.get('anomalies'):
                    require(summary.get('research_status') == 'FAILED', 'clock_research_not_invalidated')
                    if summary.get('shutdown_eligible') and (summary.get('collection_stop') or {}).get('reason') == 'planned_65_minute_stop':
                        validate_continuation(clock, summary['plan'], json.loads(z.read('lifecycle.json'))['collection_protocol'],
                                              summary['collection_stop'])
        kind=manifest['archive_kind']
        status='FAILED' if kind=='diagnostic' else 'VERIFIED'
        require(kind in {'diagnostic','research'} and manifest['research_status']==summary['research_status']==summary['status']==diagnostic['research_status']==status,'archive_verdict')
        require(summary['run_id']==diagnostic['run_id']==run_id and summary['session_date']==diagnostic['session_date']==day,'archive_identity')
        if policy == MISSING_OPENING_POLICY:
            require(kind == 'diagnostic' and summary.get('evidence_policy') == policy
                    and summary.get('opening_hour_status') == manifest.get('opening_hour_status') == 'INCOMPLETE', 'archive_evidence_policy')
            require(OPENING_DIAGNOSTICS in names, 'opening_failure_evidence_missing')
            record = json.loads(z.read(OPENING_DIAGNOSTICS))
            validate_opening_failure(record, Path(root).resolve(), run_id, day)
            require(record == diagnostic.get('opening_hour'), 'opening_failure_evidence_mismatch')
            expected_missing = {OPENING_DB: dict(reason='opening_processing_failed_before_database', evidence=OPENING_DIAGNOSTICS)}
            require(manifest.get('missing_originals') == diagnostic.get('missing_originals') == expected_missing, 'archive_missing_policy')
            require(set(n for n in names if n.startswith('opening_hour/')) == {OPENING_DIAGNOSTICS}, 'unexpected_opening_exports')
            opening_omissions = {k:v for k,v in manifest['omissions'].items() if k.startswith('opening_hour/')}
            require(set(opening_omissions) == {'opening_hour/'+n for n in GROUPS['opening_hour']}, 'archive_missing_policy')
            for item in opening_omissions.values():
                relative = Path(item.get('local_path') or '')
                require(not relative.is_absolute() and '..' not in relative.parts and
                        (Path(root)/relative).resolve().is_relative_to(Path(root).resolve()/'opening_hour/export'/day)
                        and not (Path(root)/relative).exists() and item.get('local_preserved') is False
                        and item.get('reason') == 'opening_processing_failed_before_database', 'archive_missing_policy')
        else:
            require(not manifest.get('missing_originals'), 'archive_missing_policy')
        originals = CORE_ORIGINALS | ({OPENING_DIAGNOSTICS_SOURCE} if policy == MISSING_OPENING_POLICY else {OPENING_DB})
        require(originals <= set(manifest['local_originals']), 'local_original_missing')
        require(set(manifest['provenance']) == set(manifest['files'])-EXTRA, 'provenance_missing')
        require(manifest['omissions'] == diagnostic['omissions'], 'archive_omissions_mismatch')
        for key,item in manifest['provenance'].items():
            require(key in manifest['files'] and item['included']==manifest['files'][key],'provenance_mismatch')
            require(manifest['local_originals'].get(item['local_path'])==item['original'],'original_manifest_mismatch')
            original=local_file(Path(root)/item['local_path'],Path(root).resolve()).read_bytes()
            derived, edits=export_copy(original,key,run_id,day)
            order=lambda entry:(entry['row'],entry['column'])
            require(digest(derived)==item['included'] and sorted(edits,key=order)==sorted(item['transformations'],key=order),'derived_export_mismatch')
        require(json.loads(z.read('lifecycle.json'))==read_json(Path(root)/'radar/dashboard_run.json'),'lifecycle_mismatch')
        if require_safe:
            require(summary['shutdown_eligible'] is True and summary['preservation_status']=='VERIFIED'
                    and not summary.get('shutdown_blockers', summary['operational_failures']),'preservation_blocked')
            require(essential<=set(names),'essential_evidence_missing')
            for key,item in manifest['omissions'].items():
                if policy == MISSING_OPENING_POLICY and key.startswith('opening_hour/'):
                    continue
                require(item['local_preserved'] is True and item['local_path'] in manifest['local_originals'],'excluded_original_missing')
    validate_local(root,manifest['local_originals'])
    return manifest
