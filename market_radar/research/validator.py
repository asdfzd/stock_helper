"""Offline collection-quality validation. Never produces trading signals or edits evidence."""
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .importer import open_source, source_metadata, summarize
from ..engine import HORIZONS
from ..models import ET, instant, utc
from ..scheduling import START_STOP_TOLERANCE_MS, CAPACITY_MINUTES

VALIDATOR_VERSION = 1


@dataclass(frozen=True)
class ValidatorPolicy:
    # Coverage alerts, not profitability thresholds. Missing observations remain unknown.
    min_rvol_available_pct: float = 50.0
    max_stale_opportunity_pct: float = 5.0
    missing_results_warn_count: int = 1
    timing_consistency_seconds: float = 0.000001


@contextmanager
def evidence_snapshot(source):
    """Read copied DB/WAL so SQLite cannot even update the original SHM file.

    For finalized sessions only. Reject files changing during the copy; never checkpoint
    a source database. Committed WAL is included by SQLite in the temporary directory.
    """
    source = Path(source).resolve()
    paths = [source, Path(str(source) + '-wal')]
    def stamps():
        return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in paths]
    before = stamps()
    with tempfile.TemporaryDirectory(prefix='radar_validation_') as folder:
        target = Path(folder) / source.name
        shutil.copyfile(source, target)
        if paths[1].exists():
            shutil.copyfile(paths[1], Path(str(target) + '-wal'))
        if before != stamps():
            raise ValueError('Evidence changed during validation')
        with closing(open_source(target)) as db:
            yield db


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def evidence_fingerprint(source, day):
    source = Path(source)
    # SQLite checkpoints change DB/WAL bytes without changing evidence. Fingerprint
    # committed logical content so a later reader/checkpoint cannot stale a valid result.
    paths = [source.parent / 'dashboard_run.json']
    paths += [source.parent / 'export' / day / name for name in
              ('metadata.json', 'quality_summary.json', 'candidates.csv', 'snapshots.csv', 'events.csv', 'results.csv')]
    result = {}
    if source.is_file():
        digest = hashlib.sha256()
        with evidence_snapshot(source) as db:
            for statement in db.iterdump():
                digest.update(statement.encode('utf-8'))
                digest.update(b'\n')
        result['research_logical_sha256'] = digest.hexdigest()
    for path in paths:
        if path.is_file():
            with path.open('rb') as stream:
                result[str(path.relative_to(source.parent)).replace('\\', '/')] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def validate_session(source, session_date, *, run_id=None, lifecycle=None, policy=None, calendar=None):
    """Return a JSON-safe summary, including FAIL for corrupt/missing evidence."""
    if date.fromisoformat(session_date).isoformat() != session_date:
        raise ValueError('Expected ISO session date')
    source, policy = Path(source).resolve(), policy or ValidatorPolicy()
    out = dict(validator_version=VALIDATOR_VERSION, session_date=session_date, run_id=run_id,
        validated_at=utc(datetime.now(timezone.utc)), overall='PASS', policy=asdict(policy),
        protocol={}, integrity={}, capacity={}, data_quality={}, results={}, errors=[], warnings=[])
    def issue(section, code, fail=False):
        out['errors' if fail else 'warnings'].append(dict(section=section, code=code))
    try:
        if lifecycle is None:
            path = source.parent / 'dashboard_run.json'
            lifecycle = _json(path) if path.exists() else {}
        out['run_id'] = run_id or lifecycle.get('run_id')
        out['evidence_sha256'] = evidence_fingerprint(source, session_date)
        with evidence_snapshot(source) as db:
            integrity = out['integrity']
            integrity['sqlite_integrity'] = [r[0] for r in db.execute('PRAGMA integrity_check')]
            if integrity['sqlite_integrity'] != ['ok']:
                issue('integrity', 'sqlite_integrity_failure', True)
                return _finish(out)
            required = {'radar_metadata', 'radar_batches', 'radar_snapshots', 'radar_candidates',
                        'radar_events', 'radar_results', 'market_sessions'}
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            integrity['missing_tables'] = sorted(required - tables)
            if integrity['missing_tables']:
                issue('integrity', 'missing_tables', True)
                return _finish(out)
            identity, version = source_metadata(db)
            out.update(test_version=identity.get('test_version'), schema_version=version)
            if identity.get('schema_version', version) != version:
                issue('integrity', 'research_schema_mismatch', True)
            first = db.execute('SELECT MIN(timestamp_utc) FROM radar_batches').fetchone()[0]
            origin = json.dumps(identity, sort_keys=True) + '|' + (first or lifecycle.get('run_id') or str(source))
            expected_id = hashlib.sha256((origin + '|' + session_date).encode()).hexdigest()[:32]
            if run_id is not None and run_id != expected_id:
                issue('integrity', 'run_id_mismatch', True)
            out['run_id'] = expected_id
            if version < 4:
                issue('integrity', 'legacy_evidence_limited')
            integrity['foreign_key_errors'] = len(list(db.execute('PRAGMA foreign_key_check')))
            if integrity['foreign_key_errors']:
                issue('integrity', 'foreign_key_failure', True)
            q = summarize(db, version, session_date)
            p_row = db.execute("SELECT value FROM radar_metadata WHERE key='collection_protocol'").fetchone()
            p = json.loads(p_row[0]) if p_row else {}
            out['protocol']['collection_protocol'] = p
            if lifecycle.get('collection_protocol') != p and (p or lifecycle.get('collection_protocol')):
                issue('integrity', 'lifecycle_protocol_mismatch', True)
            _protocol(db, session_date, p, lifecycle, identity, calendar, out, issue)
            _exports(db, source.parent, session_date, identity, version, q, p, integrity, issue)
            orphan_candidates = db.execute('''SELECT COUNT(*) FROM radar_candidates c WHERE c.session_date=? AND
                NOT EXISTS(SELECT 1 FROM radar_snapshots s WHERE s.session_date=c.session_date
                    AND s.ticker=c.ticker AND s.timestamp_utc=c.first_detected_utc)
                ''', (session_date,)).fetchone()[0]
            bad_events = db.execute('''SELECT COUNT(*) FROM radar_events e JOIN radar_snapshots s ON s.id=e.snapshot_id
                WHERE e.session_date=? AND (e.ticker!=s.ticker OR e.session_date!=s.session_date
                    OR e.timestamp_utc!=s.timestamp_utc)''', (session_date,)).fetchone()[0]
            missing_first = db.execute('''SELECT COUNT(*) FROM radar_candidates c WHERE c.session_date=? AND
                NOT EXISTS(SELECT 1 FROM radar_events e WHERE e.session_date=c.session_date AND e.ticker=c.ticker
                    AND e.timestamp_utc=c.first_detected_utc AND e.event_type='FIRST_DETECTED')''', (session_date,)).fetchone()[0]
            integrity.update(candidate_snapshot_errors=orphan_candidates, event_snapshot_errors=bad_events,
                             missing_first_detected_events=missing_first)
            if orphan_candidates or bad_events or missing_first:
                issue('integrity', 'research_reference_mismatch', True)
            for table, stamp in (('radar_snapshots', 'timestamp_utc'), ('radar_events', 'timestamp_utc'),
                                 ('radar_candidates', 'first_detected_utc')):
                for r in db.execute(f'SELECT {stamp},test_version FROM {table} WHERE session_date=?', (session_date,)):
                    if instant(r[0]).astimezone(ET).date().isoformat() != session_date or r[1] != identity.get('test_version'):
                        issue('integrity', 'row_date_or_version_mismatch', True)
                        break
            _quality_capacity(db, session_date, identity, q, out, issue, policy)
            _results(db, session_date, identity, version, q, out, issue, policy)
        if out['evidence_sha256'] != evidence_fingerprint(source, session_date):
            issue('integrity', 'evidence_changed_during_validation', True)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, csv.Error, OverflowError):
        # No raw exception text: evidence may contain credentials or arbitrary external text.
        issue('integrity', 'unreadable_or_invalid_evidence', True)
    return _finish(out)


def _finish(out):
    for section in ('protocol', 'integrity', 'capacity', 'data_quality', 'results'):
        out[section]['status'] = ('FAIL' if any(i['section'] == section for i in out['errors']) else
            'WARN' if any(i['section'] == section for i in out['warnings']) else
            'PASS' if out[section] else 'NOT_CHECKED')
    out['overall'] = 'FAIL' if out['errors'] else 'WARN' if out['warnings'] else 'PASS'
    return out


def _protocol(db, day, p, lifecycle, identity, calendar, out, issue):
    target = out['protocol']
    target['clean_shutdown'] = lifecycle.get('clean_shutdown')
    scheduled = p.get('collection_mode') in ('Capacity Test', 'Regular Session')
    target['standardized_comparison'] = scheduled
    if not scheduled:
        issue('protocol', 'manual_or_unknown_protocol_diagnostic_only')
    if lifecycle.get('clean_shutdown') is not True:
        issue('protocol', 'unclean_or_unknown_shutdown', scheduled or lifecycle.get('clean_shutdown') is False)
    if not scheduled:
        return
    if identity.get('test_version') != 'kiwoom_radar_v2_1' or identity.get('schema_version') != 4:
        issue('protocol', 'unsupported_scheduled_research_identity', True)
    if p.get('protocol_compliant') is not True:
        issue('protocol', 'protocol_non_compliant', True)
    if calendar is None:
        from ..providers.kiwoom_calendar import ExchangeCalendar
        calendar = ExchangeCalendar()
    session = calendar.session(day)
    target['xnys_trading_day'] = session is not None
    if not session:
        issue('protocol', 'not_xnys_trading_day', True)
        return
    opened, closed = instant(session.open_utc), instant(session.close_utc)
    expected_stop = opened + timedelta(minutes=CAPACITY_MINUTES) if p['collection_mode'] == 'Capacity Test' else closed
    required = ('armed_at', 'scheduled_start', 'actual_start', 'scheduled_stop', 'actual_stop',
                'connection_ready_at', 'first_regular_batch_utc')
    if any(not p.get(k) for k in required):
        issue('protocol', 'missing_regular_timing_evidence', True)
    else:
        if (p.get('session_date_et') != day or instant(p['scheduled_start']) != opened or
            instant(p['scheduled_stop']) != expected_stop or instant(p['scheduled_open_utc']) != opened or
            instant(p['scheduled_close_utc']) != closed):
            issue('protocol', 'calendar_schedule_mismatch', True)
        if (p.get('armed_before_open') is not True or instant(p['armed_at']) >= opened or
            p.get('ready_before_open') is not True or instant(p['connection_ready_at']) >= opened):
            issue('protocol', 'not_ready_before_open', True)
        for kind in ('start', 'stop'):
            delay = (instant(p['actual_' + kind]) - instant(p['scheduled_' + kind])).total_seconds()*1000
            target[kind + '_delay_ms_recomputed'] = delay
            reported = p.get(kind + '_delay_ms')
            if reported is None or not math.isclose(delay, reported, abs_tol=0.001):
                issue('protocol', kind + '_delay_evidence_mismatch', True)
            if delay < 0 or delay > START_STOP_TOLERANCE_MS:
                issue('protocol', kind + '_outside_tolerance', True)
    expected_reason = 'scheduled_capacity_end' if p['collection_mode'] == 'Capacity Test' else 'scheduled_close'
    if p.get('stop_reason') != expected_reason:
        issue('protocol', 'unexpected_stop_reason', True)
    if p.get('max_ws_symbols') != identity.get('max_ws_symbols'):
        issue('protocol', 'max_ws_mismatch', True)
    from ..config import CONFIG
    if (p.get('snapshot_seconds') != CONFIG.snapshot_seconds or
        p.get('result_pre_target_grace_seconds') != CONFIG.result_pre_target_grace_seconds):
        issue('protocol', 'collection_policy_mismatch', True)
    for key in ('snapshot_seconds', 'result_tolerance_seconds', 'result_pre_target_grace_seconds',
                'rvol_lookback_days', 'rvol_backfill_enabled', 'rvol_backfill_min_day_volume',
                'rvol_backfill_min_price', 'result_tracking_hold_minutes', 'rest_negative_ttl_seconds',
                'stock_type_policy', 'bar_label_policy', 'bar_finalization_seconds', 'candidate_pages',
                'include_extended_hours', 'exact_vwap_enabled', 'session'):
        if key in identity and identity[key] != getattr(CONFIG, key):
            issue('protocol', 'identity_policy_mismatch', True)
            break
    counts = {}
    for table, field in (('radar_snapshots', 'timestamp_utc'), ('radar_events', 'timestamp_utc'),
                         ('radar_candidates', 'first_detected_utc')):
        counts[table] = sum(instant(r[0]) < opened for r in db.execute(
            f'SELECT {field} FROM {table} WHERE session_date=?', (day,)))
        if db.execute(f'SELECT COUNT(*) FROM {table} WHERE session_date!=?', (day,)).fetchone()[0]:
            issue('integrity', 'scheduled_research_date_mismatch', True)
    target['preopen_counts'] = counts
    target['preopen_event_types'] = dict(Counter(r[1] for r in db.execute(
        'SELECT timestamp_utc,event_type FROM radar_events WHERE session_date=?', (day,)) if instant(r[0]) < opened))
    target['premarket_leak'] = 'FAIL' if any(counts.values()) else 'PASS'
    if any(counts.values()):
        issue('protocol', 'preopen_research_contamination', True)
    if not db.execute('SELECT 1 FROM radar_snapshots WHERE session_date=? LIMIT 1', (day,)).fetchone():
        issue('protocol', 'regular_research_not_started', True)
    if p.get('first_regular_batch_utc'):
        first = db.execute('SELECT MIN(timestamp_utc) FROM radar_batches').fetchone()[0]
        if not first or instant(first) != instant(p['first_regular_batch_utc']):
            issue('protocol', 'first_batch_evidence_mismatch', True)


def _exports(db, root, day, identity, version, q, p, integrity, issue):
    folder = root / 'export' / day
    metadata, quality = _json(folder / 'metadata.json'), _json(folder / 'quality_summary.json')
    integrity['metadata_present'] = integrity['quality_summary_present'] = True
    if metadata != dict(identity, **({'collection_protocol': p} if p else {})):
        issue('integrity', 'metadata_mismatch', True)
    if version >= 3 and quality != q:
        issue('integrity', 'quality_summary_mismatch', True)
    counts = {}
    for name in ('candidates', 'snapshots', 'events', 'results'):
        query = (f'SELECT * FROM radar_{name} WHERE session_date=? ORDER BY id' if name != 'results' else
            'SELECT c.session_date,c.ticker,c.first_detected_utc,c.first_price,c.test_version,r.* '
            'FROM radar_results r JOIN radar_candidates c ON c.id=r.candidate_id WHERE c.session_date=? ORDER BY r.id')
        cursor = db.execute(query, (day,))
        expected_header = [c[0] for c in cursor.description]
        mismatch, count = False, 0
        from itertools import zip_longest
        with (folder / (name + '.csv')).open(encoding='utf-8-sig', newline='') as stream:
            rows = csv.reader(stream)
            mismatch = next(rows, None) != expected_header
            db_count = 0
            for exported, row in zip_longest(rows, cursor):
                count += exported is not None
                db_count += row is not None
                if exported != ([str(v) if v is not None else '' for v in row] if row is not None else None):
                    mismatch = True
        counts[name] = dict(db=db_count, csv=count, contents_match=not mismatch)
        if mismatch:
            issue('integrity', name + '_export_mismatch', True)
    integrity['exports'] = counts


def _quality_capacity(db, day, identity, q, out, issue, policy):
    metrics = {k: q.get(k) for k in ('snapshot_count', 'candidate_count', 'unique_tickers', 'rvol', 'returns',
        'market_data', 'rest_errors', 'runtime', 'backfill')}
    out['data_quality'].update(metrics)
    start = datetime.fromisoformat(day).replace(tzinfo=ET)
    batches = [json.loads(r[0]).get('metadata', {}) for r in db.execute(
        'SELECT raw_json FROM radar_batches WHERE timestamp_utc>=? AND timestamp_utc<? ORDER BY timestamp_utc',
        (utc(start), utc(start + timedelta(days=1))))]
    gauges = [b.get('tracking_counts', {}) for b in batches]
    cap = out['capacity']
    cap['configured_max_ws'] = identity.get('max_ws_symbols')
    for key in ('active_subscription_count', 'protected_count', 'discovery_count', 'slot_saturation_pct', 'tracking_slot_unavailable_count'):
        values = [g[key] for g in gauges if g.get(key) is not None]
        cap['peak_' + key] = max(values) if values else None
        cap['last_' + key] = gauges[-1].get(key) if gauges else None
    cap['slot_unavailable_event_count'] = q.get('market_data', {}).get('tracking_slot_unavailable_count')
    cap['unavailable_batches'] = sum(g.get('tracking_slot_unavailable_count', 0) > 0 for g in gauges)
    if cap['slot_unavailable_event_count'] or cap['unavailable_batches']:
        issue('capacity', 'slot_unavailable')
    if cap['peak_active_subscription_count'] is None or cap['peak_slot_saturation_pct'] is None:
        issue('capacity', 'capacity_evidence_unknown')
    stale = [ticker for b in batches for ticker, reason in b.get('missing_reasons', {}).items() if reason == 'stale_price']
    total = q['snapshot_count'] + len(stale)
    stale_pct = 100*len(stale)/total if total else None
    out['data_quality'].update(stale_batch_observations=len(stale), stale_unique_tickers=len(set(stale)),
        stale_opportunity_pct=stale_pct, stale_ratio_basis='stale batch observations / (snapshots + stale batch observations)')
    if stale_pct is not None and stale_pct > policy.max_stale_opportunity_pct:
        issue('data_quality', 'stale_coverage_high')
    rv = q.get('rvol', {}).get('available_pct')
    if rv is None or rv < policy.min_rvol_available_pct:
        issue('data_quality', 'rvol_coverage_low')
    for key in ('worker_error_count', 'db_error_count', 'export_error_count'):
        if q.get('runtime', {}).get(key, 0):
            issue('data_quality', key, True)
    for section, key, code in (('market_data', 'ws_gap_count', 'ws_gaps'),
        ('rest_errors', 'total', 'rest_errors'), ('rest_errors', 'suppressed_count', 'deterministic_suppressed'),
        ('backfill', 'incomplete', 'incomplete_history')):
        if q.get(section, {}).get(key, 0):
            issue('data_quality', code)


def _results(db, day, identity, version, q, out, issue, policy):
    target = out['results']
    target.update(status_distribution=q.get('results', {}), missing_reasons=q.get('missing_reasons', {}))
    missing_horizons, invalid_samples, overdue = 0, 0, 0
    selections = Counter()
    last = db.execute('SELECT MAX(timestamp_utc) FROM radar_snapshots WHERE session_date=?', (day,)).fetchone()[0]
    tolerance = identity.get('result_tolerance_seconds', 30)
    grace = identity.get('result_pre_target_grace_seconds', .25)
    for c in db.execute('SELECT * FROM radar_candidates WHERE session_date=?', (day,)):
        rows = {r['horizon']: dict(r) for r in db.execute('SELECT * FROM radar_results WHERE candidate_id=?', (c['id'],))}
        missing_horizons += len((set(HORIZONS) | {'EOD', 'D+1', 'D+2', 'D+3'}) - rows.keys())
        for horizon, seconds in HORIZONS.items():
            if horizon not in rows:
                continue
            r = rows[horizon]
            expected = instant(c['first_detected_utc']) + timedelta(seconds=seconds)
            if not r['target_utc'] or instant(r['target_utc']) != expected:
                invalid_samples += 1
                continue
            outside = expected > instant(c['session_close_utc'])
            if (r['status'] == 'outside_session') != outside:
                invalid_samples += 1
            if r['status'] not in ('complete', 'missing', 'pending', 'outside_session'):
                invalid_samples += 1
            if r['status'] == 'pending' and last and instant(last) > expected + timedelta(seconds=tolerance):
                overdue += 1
            if r['status'] != 'complete':
                continue
            if version < 4:
                continue
            selection = r.get('sample_selection')
            selections[selection or 'unknown'] += 1
            stamp, offset = r.get('sample_timestamp'), r.get('sample_offset_seconds')
            if not stamp or offset is None:
                invalid_samples += 1
                continue
            actual_offset = (instant(stamp) - expected).total_seconds()
            snap = db.execute('SELECT price FROM radar_snapshots WHERE session_date=? AND ticker=? AND timestamp_utc=?',
                              (day, c['ticker'], stamp)).fetchone()
            if (not math.isclose(actual_offset, offset, abs_tol=policy.timing_consistency_seconds) or
                r.get('observed_utc') != stamp or r.get('timing_error_seconds') != offset or
                not snap or snap[0] != r['price'] or
                not ((selection == 'post_target' and 0 <= offset <= tolerance) or
                     (selection == 'pre_target_grace' and -grace <= offset < 0))):
                invalid_samples += 1
    target.update(missing_expected_horizons=missing_horizons, invalid_sample_count=invalid_samples,
                  sampling=dict(selections), overdue_pending_count=overdue)
    if missing_horizons or invalid_samples:
        issue('results', 'result_integrity_failure', True)
    if overdue:
        issue('results', 'overdue_pending')
    missing = sum(v.get('missing', 0) for v in q.get('results', {}).values())
    if missing >= policy.missing_results_warn_count:
        issue('results', 'missing_results')


def write_validation(summary, export_path):
    folder = Path(export_path)
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='validation_', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(summary, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, folder / 'validation_summary.json')
    finally:
        Path(temporary).unlink(missing_ok=True)
    return folder / 'validation_summary.json'


def revalidate_session(session, master_path=None):
    lifecycle_path = Path(session['source_db_path']).parent / 'dashboard_run.json'
    if lifecycle_path.exists() and not _json(lifecycle_path).get('stopped_at'):
        raise ValueError('Session has not finalized')
    summary = validate_session(session['source_db_path'], session['session_date_et'], run_id=session.get('run_id'))
    write_validation(summary, session['export_path'])
    if master_path is not None:
        from .master_db import MasterDB
        MasterDB(master_path).save_validation(summary['run_id'], summary)
    return summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Read-only session quality validation; no trading signals')
    parser.add_argument('source', type=Path, help='Session research SQLite file')
    parser.add_argument('--date', required=True, help='ET session date YYYY-MM-DD')
    args = parser.parse_args()
    lifecycle_path = args.source.parent / 'dashboard_run.json'
    if lifecycle_path.exists() and not _json(lifecycle_path).get('stopped_at'):
        parser.error('Session has not finalized')
    summary = validate_session(args.source, args.date)
    write_validation(summary, args.source.parent / 'export' / args.date)
    print(json.dumps({k: summary[k] for k in ('validator_version', 'session_date', 'overall', 'errors', 'warnings')}))
    return 1 if summary['overall'] == 'FAIL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
