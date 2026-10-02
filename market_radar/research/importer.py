"""Read-only, transactional session/day imports; no source DDL or exports."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from .master_db import MasterDB
from ..models import ET, instant, utc
from ..quality import daily_summary


def open_source(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")  # One consistent view, including committed WAL records.
    return db


def source_metadata(db):
    metadata = dict(db.execute("SELECT key,value FROM radar_metadata"))
    version = int(metadata["schema_version"])
    if version not in (1, 2, 3, 4):
        raise ValueError("Unsupported research schema")
    return json.loads(metadata["identity"]), version


def source_days(db, version):
    query = "SELECT session_date FROM radar_snapshots UNION SELECT session_date FROM radar_candidates"
    if version >= 3:
        query += " UNION SELECT session_date FROM radar_discoveries UNION SELECT session_date FROM quality_counters"
    return [r[0] for r in db.execute(query + " ORDER BY 1")]


def summarize(db, version, day):
    if version >= 3:
        return daily_summary(SimpleNamespace(db=db, schema_version=version), day)
    n, tickers, rv, r1, r5 = db.execute("SELECT COUNT(*),COUNT(DISTINCT ticker),COUNT(rvol),"
        "COUNT(return_1m),COUNT(return_5m) FROM radar_snapshots WHERE session_date=?", (day,)).fetchone()
    return dict(snapshot_count=n, unique_tickers=tickers,
        candidate_count=db.execute("SELECT COUNT(*) FROM radar_candidates WHERE session_date=?", (day,)).fetchone()[0],
        rvol={"available_pct": 100 * rv / n if n else None},
        returns={"return_1m_available_pct": 100 * r1 / n if n else None,
                 "return_5m_available_pct": 100 * r5 / n if n else None})


def insert(db, table, values):
    db.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def import_session(source, master_path, *, lifecycle=None):
    source = Path(source).resolve()
    if not source.exists():
        raise ValueError("Session source does not exist")
    if source.is_dir():
        paths = sorted(source.glob("stock_helper_market*.db"))
        if not paths:
            raise ValueError("No research DB in selected session folder")
        return [item for path in paths for item in import_session(path, master_path, lifecycle=lifecycle)]
    if source == Path(master_path).resolve():
        raise ValueError("Master DB cannot be its own source")
    root = source.parent
    if lifecycle is None and (root / "dashboard_run.json").is_file():
        lifecycle = json.loads((root / "dashboard_run.json").read_text(encoding="utf-8"))
    lifecycle = lifecycle or {}
    imported = []
    with closing(open_source(source)) as src, closing(MasterDB(master_path).connect()) as dst:
        identity, version = source_metadata(src)
        encoded = json.dumps(identity, sort_keys=True)
        first = src.execute("SELECT MIN(timestamp_utc) FROM radar_batches").fetchone()[0]
        # Content identity survives directory moves/copies and later D+n updates.
        origin = encoded + "|" + (first or lifecycle.get("run_id") or str(source))
        with dst:
            for day in source_days(src, version):
                datetime.strptime(day, "%Y-%m-%d")
                run_id = hashlib.sha256((origin + "|" + day).encode()).hexdigest()[:32]
                prior = dst.execute("SELECT id FROM sessions WHERE run_id=?", (run_id,)).fetchone()
                q = summarize(src, version, day)
                observed = src.execute("SELECT MIN(timestamp_utc),MAX(timestamp_utc) FROM radar_snapshots WHERE session_date=?", (day,)).fetchone()
                start, stop = lifecycle.get("started_at") or observed[0], lifecycle.get("stopped_at") or observed[1]
                duration = (instant(stop)-instant(start)).total_seconds() if start and stop else None
                session = dict(run_id=run_id, session_date_et=day, started_at=start, stopped_at=stop,
                    duration_seconds=duration, timing_basis="collector_run" if lifecycle else "observed_snapshots",
                    test_version=identity.get("test_version"), research_schema_version=version,
                    max_ws_symbols=identity.get("max_ws_symbols"), data_dir=str(root), source_db_path=str(source),
                    export_path=str(root / "export" / day), log_path=str(root / "logs" / f"{day}.log"),
                    clean_shutdown=lifecycle.get("clean_shutdown"),
                    shutdown_elapsed_ms=lifecycle.get("shutdown_elapsed_ms", q.get("shutdown", {}).get("last_shutdown_elapsed_ms")),
                    imported_at=utc(datetime.now(timezone.utc)), source_identity=encoded)
                if prior:
                    sid = prior[0]
                    dst.execute("UPDATE sessions SET " + ",".join(f"{k}=?" for k in session) + " WHERE id=?", (*session.values(), sid))
                    for table in ("candidates", "results", "quality"):
                        dst.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
                else:
                    insert(dst, "sessions", session)
                    sid = dst.execute("SELECT last_insert_rowid()").fetchone()[0]
                for row in src.execute("SELECT * FROM radar_candidates WHERE session_date=?", (day,)):
                    c = dict(row)
                    snap = src.execute("SELECT * FROM radar_snapshots WHERE session_date=? AND ticker=? AND timestamp_utc=?",
                        (day, c["ticker"], c["first_detected_utc"])).fetchone()
                    s = dict(snap) if snap else {}
                    ranks = {}
                    for key in ("current_leader_rank", "emerging_rank"):
                        ranked = src.execute(f"SELECT {key} FROM radar_snapshots WHERE session_date=? AND ticker=? AND {key}<=10 ORDER BY timestamp_utc LIMIT 1", (day, c["ticker"])).fetchone()
                        ranks[key] = ranked[0] if ranked else None
                    insert(dst, "candidates", dict(session_id=sid, ticker=c["ticker"], first_detected_at=c["first_detected_utc"],
                        initial_price=c["first_price"], initial_day_change_pct=s.get("day_change_pct"),
                        initial_return_1m=s.get("return_1m"), initial_return_5m=s.get("return_5m"), initial_rvol=s.get("rvol"),
                        rvol_status=s.get("rvol_status") or "unknown", initial_hod_distance=s.get("hod_distance_pct"),
                        candidate_sources=c.get("candidate_sources"), first_current_rank=ranks["current_leader_rank"],
                        first_emerging_rank=ranks["emerging_rank"], symbol_type=c.get("symbol_type", s.get("symbol_type")),
                        tracking_state=c.get("candidate_tracking_state")))
                    for result in src.execute("SELECT * FROM radar_results WHERE candidate_id=?", (c["id"],)):
                        r = dict(result)
                        insert(dst, "results", dict(session_id=sid, ticker=c["ticker"], horizon=r["horizon"],
                            status=r["status"], missing_reason=r.get("missing_reason"), entry_price=c["first_price"],
                            result_price=r["price"], return_pct=r["return_pct"], mfe=r["mfe_pct"], mae=r["mae_pct"],
                            sample_timestamp=r.get("sample_timestamp", r.get("observed_utc")),
                            sample_offset_seconds=r.get("sample_offset_seconds", r.get("timing_error_seconds")),
                            sample_selection=r.get("sample_selection")))
                saturations = []
                from datetime import timedelta
                begin = datetime.fromisoformat(day).replace(tzinfo=ET)
                for batch in src.execute("SELECT raw_json FROM radar_batches WHERE timestamp_utc>=? AND timestamp_utc<?", (utc(begin), utc(begin+timedelta(days=1)))):
                    tracking = json.loads(batch[0]).get("metadata", {}).get("tracking_counts", {})
                    value = tracking.get("slot_saturation_pct")
                    if value is None and tracking.get("active_subscription_count") is not None and identity.get("max_ws_symbols"):
                        value = 100 * tracking["active_subscription_count"] / identity["max_ws_symbols"]
                    if value is not None:
                        saturations.append(value)
                runtime, market = q.get("runtime", {}), q.get("market_data", {})
                insert(dst, "quality", dict(session_id=sid, snapshot_count=q["snapshot_count"], unique_tickers=q["unique_tickers"],
                    candidate_count=q["candidate_count"], rvol_available_pct=q["rvol"]["available_pct"], **q["returns"],
                    ws_gap_count=market.get("ws_gap_count"), stale_count=market.get("stale_count"),
                    rest_error_count=q.get("rest_errors", {}).get("total") if version >= 4 else None,
                    worker_error_count=runtime.get("worker_error_count"), db_error_count=runtime.get("db_error_count"),
                    export_error_count=runtime.get("export_error_count"), slot_unavailable_count=market.get("tracking_slot_unavailable_count"),
                    max_slot_saturation_pct=max(saturations) if saturations else None, summary_json=json.dumps(q, ensure_ascii=False)))
                imported.append(dict(id=sid, run_id=run_id, day=day, updated=bool(prior)))
    return imported


def live_health(path, *, latest_session=False):
    with closing(open_source(path)) as db:
        identity, version = source_metadata(db)
        day = datetime.now(ET).date().isoformat()
        if latest_session or identity.get("mode") == "replay":
            days = source_days(db, version)
            day = days[-1] if days else day
        tracking = "c.candidate_tracking_state" if version >= 3 else "NULL AS candidate_tracking_state"
        rows = [dict(r) for r in db.execute(f"SELECT s.*,{tracking} FROM radar_snapshots s LEFT JOIN radar_candidates c "
            "ON c.session_date=s.session_date AND c.ticker=s.ticker WHERE s.session_date=? AND "
            "s.timestamp_utc=(SELECT MAX(timestamp_utc) FROM radar_snapshots WHERE session_date=?)", (day, day))]
        return dict(summary=summarize(db, version, day), identity=identity, day=day, rows=rows)
