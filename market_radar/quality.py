"""Thread-safe ET-day counters persisted as structured events, never log parsing."""
from collections import Counter, defaultdict
from datetime import datetime
import logging
import threading

from .models import ET


class QualityCounters:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = defaultdict(Counter)

    def add(self, name, count=1, day=None):
        with self.lock:
            self.pending[day or datetime.now(ET).date().isoformat()][name] += count

    def set_gauge(self, name, value, day=None):
        with self.lock:
            self.pending[day or datetime.now(ET).date().isoformat()][name+'_gauge'] = value

    def drain(self):
        with self.lock:
            result, self.pending = self.pending, defaultdict(Counter)
            return result

    def persist(self, store):
        if store.schema_version < 3:
            return
        counts = self.drain()
        try:
            with store.db:
                for day, values in counts.items():
                    for name, count in values.items():
                        store.db.execute("INSERT INTO quality_counters VALUES(?,?,?) "
                            "ON CONFLICT(session_date,name) DO UPDATE SET count=" +
                            ("excluded.count" if name.endswith("_gauge") else "count+excluded.count"), (day, name, count))
        except Exception:
            with self.lock:
                for day, values in counts.items():
                    self.pending[day].update(values)
            raise


class QualityLogHandler(logging.Handler):
    """Only explicitly structured extras are counted; message text is irrelevant."""
    def __init__(self, counters):
        super().__init__()
        self.counters = counters

    def emit(self, record):
        if hasattr(record, 'quality_gauge'):
            self.counters.set_gauge(record.quality_gauge, record.quality_value)
        if hasattr(record, 'rest_error'):
            e = record.rest_error
            for key in ('total', e['failure_class'], e['category']):
                self.counters.add('rest_'+key)
            for key in ('api_id', 'ticker', 'endpoint', 'http_status', 'return_code', 'specific_code'):
                self.counters.add('rest_dimension:'+key+':'+str(e.get(key)))
            # Successful empty pages are availability evidence, not protocol failures.
            if e['category'] != 'no_data':
                self.counters.add('api_error_count')
            if e['category'] in {'timeout', 'connection_error', 'transport_error'}:
                self.counters.add('transport_error_count')
            if e['category'] == 'parse_error':
                self.counters.add('parse_error_count')
        if hasattr(record, "quality_event"):
            self.counters.add(record.quality_event, getattr(record, "quality_count", 1))


def event(name, count=1):
    return {"quality_event": name, "quality_count": count}


def daily_summary(store, day):
    import json
    from .engine import HORIZONS
    db = store.db
    n, tickers, rv, r1, r5 = db.execute("SELECT COUNT(*),COUNT(DISTINCT ticker),COUNT(rvol),"
        "COUNT(return_1m),COUNT(return_5m) FROM radar_snapshots WHERE session_date=?", (day,)).fetchone()
    counters = dict(db.execute("SELECT name,count FROM quality_counters WHERE session_date=?", (day,)))
    statuses = dict(db.execute("SELECT COALESCE(rvol_status,'unknown'),COUNT(*) FROM radar_snapshots "
                              "WHERE session_date=? GROUP BY 1", (day,)))
    ratio = lambda value: 100*value/n if n else None
    result = {"+"+h: dict.fromkeys(("complete", "missing", "pending", "outside_session"), 0) for h in HORIZONS}
    for h, status, count in db.execute("SELECT horizon,status,COUNT(*) FROM radar_results r JOIN radar_candidates c "
            "ON c.id=r.candidate_id WHERE c.session_date=? GROUP BY horizon,status", (day,)):
        result.setdefault("+"+h if h in HORIZONS else h, {})[status] = count
    sources, types = Counter(dict.fromkeys(("CHG", "SPIKE", "VOL", "VALUE", "multi_source"), 0)), Counter(
        dict.fromkeys(("common", "ETF", "ETN", "warrant", "unknown", "excluded"), 0))
    mapping = {"day_change": "CHG", "5m_spike": "SPIKE", "volume": "VOL", "value": "VALUE"}
    for row in db.execute("SELECT symbol_type,excluded_symbol_type,candidate_sources FROM radar_discoveries WHERE session_date=?", (day,)):
        types[row[0]] += 1
        types["excluded"] += bool(row[1])
        values = json.loads(row[2])
        sources.update(mapping[s] for s in values if s in mapping)
        sources["multi_source"] += len(values) > 1
    identity = json.loads(db.execute("SELECT value FROM radar_metadata WHERE key='identity'").fetchone()[0])
    from datetime import timedelta
    from .models import utc
    start = datetime.fromisoformat(day).replace(tzinfo=ET)
    latest = db.execute("SELECT raw_json FROM radar_batches WHERE timestamp_utc>=? AND timestamp_utc<? "
                        "ORDER BY timestamp_utc DESC LIMIT 1", (utc(start), utc(start+timedelta(days=1)))).fetchone()
    gauges = {}
    if latest:
        batch = json.loads(latest[0])
        from .models import instant
        if instant(batch['timestamp_utc']).astimezone(ET).date().isoformat() == day:
            gauges = batch.get("metadata", {}).get("tracking_counts", {})
    sampling = {}
    if store.schema_version >= 4:
        sampling = dict(db.execute("SELECT sample_selection,COUNT(*) FROM radar_results r JOIN radar_candidates c "
            "ON c.id=r.candidate_id WHERE c.session_date=? AND sample_selection IS NOT NULL GROUP BY sample_selection", (day,)))
    def top(dimension):
        prefix = 'rest_dimension:'+dimension+':'
        rows = [(k[len(prefix):], v) for k, v in counters.items() if k.startswith(prefix)]
        return [{'value': k, 'count': v} for k, v in sorted(rows, key=lambda x: (-x[1], x[0]))[:10]]
    rest = {k: counters.get('rest_'+k, 0) for k in ('total', 'transient', 'deterministic', 'unclassified',
        'rate_limited', 'no_data', 'unsupported', 'parse_error', 'connection_error', 'server_error')}
    rest.update(timeouts=counters.get('rest_timeout', 0), suppressed_count=counters.get('rest_suppressed_count', 0),
        top_tr=top('api_id'), top_tickers=top('ticker'), top_endpoints=top('endpoint'),
        http_statuses=top('http_status'), return_codes=top('return_code'), specific_codes=top('specific_code'))
    return {"session_date": day, "test_version": identity.get("test_version"), "schema_version": store.schema_version,
        "snapshot_count": n, "unique_tickers": tickers,
        "candidate_count": db.execute("SELECT COUNT(*) FROM radar_candidates WHERE session_date=?", (day,)).fetchone()[0],
        "rvol": dict(dict.fromkeys(("warming", "incomplete_history", "not_eligible", "zero_denominator", "unknown"), 0),
                     **statuses, available_count=rv, available_pct=ratio(rv)),
        "returns": {"return_1m_available_pct": ratio(r1), "return_5m_available_pct": ratio(r5)},
        "results": result,
        "missing_reasons": dict(db.execute("SELECT missing_reason,COUNT(*) FROM radar_results r JOIN radar_candidates c "
            "ON c.id=r.candidate_id WHERE c.session_date=? AND missing_reason IS NOT NULL GROUP BY missing_reason", (day,))),
        "market_data": {k: counters.get(k, 0) for k in ("ws_gap_count", "stale_count", "raw_dropped_count",
            "subscription_add_count", "subscription_remove_count", "tracking_slot_unavailable_count")},
        "tracking": gauges,
        "backfill": {k: counters.get("backfill_"+k, 0) for k in ("complete", "incomplete", "error", "skipped", "deterministic_unavailable", "transient_error", "unclassified_error")},
        "runtime": {k: counters.get(k, 0) for k in ("api_error_count", "transport_error_count", "parse_error_count", "db_error_count", "worker_error_count", "export_error_count")},
        "rest_errors": rest,
        "shutdown": {"requested_count": counters.get('shutdown_requested_count', 0),
            "last_shutdown_elapsed_ms": counters.get('shutdown_elapsed_ms_gauge'),
            "cancelled_backfill_tasks": counters.get('cancelled_backfill_tasks', 0)},
        "result_sampling": {"pre_target_grace_used_count": sampling.get('pre_target_grace', 0),
                            "post_target_count": sampling.get('post_target', 0)},
        "quality_summary_version": 2 if store.schema_version >= 4 else 1,
        "candidate_sources": dict(sources), "symbol_types": dict(types),
        "count_basis": {"sources_types": "unique daily discoveries; source union, latest official type",
            "market_data": "event counts; subscriptions count acknowledged symbols", "rvol_returns": "snapshot rows",
            "backfill": "historical completion attempts, not unique cache days"}}
