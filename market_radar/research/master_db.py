from contextlib import closing
from pathlib import Path
import sqlite3
import json

from runtime_paths import APP_ROOT

MASTER_SCHEMA_VERSION = 1
DEFAULT_MASTER_PATH = APP_ROOT / "data" / "market_radar" / "master.db"

SCHEMA = """
CREATE TABLE sessions(
 id INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, session_date_et TEXT NOT NULL,
 started_at TEXT, stopped_at TEXT, duration_seconds REAL, timing_basis TEXT,
 test_version TEXT, research_schema_version INTEGER, max_ws_symbols INTEGER,
 data_dir TEXT NOT NULL, source_db_path TEXT NOT NULL, export_path TEXT, log_path TEXT,
 clean_shutdown INTEGER, shutdown_elapsed_ms REAL, imported_at TEXT NOT NULL,
 source_identity TEXT NOT NULL);
CREATE INDEX sessions_date ON sessions(session_date_et);
CREATE TABLE candidates(
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 ticker TEXT NOT NULL, first_detected_at TEXT, initial_price REAL, initial_day_change_pct REAL,
 initial_return_1m REAL, initial_return_5m REAL, initial_rvol REAL, rvol_status TEXT,
 initial_hod_distance REAL, candidate_sources TEXT, first_current_rank INTEGER,
 first_emerging_rank INTEGER, symbol_type TEXT, tracking_state TEXT,
 UNIQUE(session_id,ticker));
CREATE INDEX candidates_ticker ON candidates(ticker);
CREATE TABLE results(
 session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 ticker TEXT NOT NULL, horizon TEXT NOT NULL, status TEXT, missing_reason TEXT,
 entry_price REAL, result_price REAL, return_pct REAL, mfe REAL, mae REAL,
 sample_timestamp TEXT, sample_offset_seconds REAL, sample_selection TEXT,
 PRIMARY KEY(session_id,ticker,horizon));
CREATE INDEX results_ticker ON results(ticker);
CREATE INDEX results_horizon ON results(horizon);
CREATE TABLE quality(
 session_id INTEGER PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 snapshot_count INTEGER, unique_tickers INTEGER, candidate_count INTEGER,
 rvol_available_pct REAL, return_1m_available_pct REAL, return_5m_available_pct REAL,
 ws_gap_count INTEGER, stale_count INTEGER, rest_error_count INTEGER,
 worker_error_count INTEGER, db_error_count INTEGER, export_error_count INTEGER,
 slot_unavailable_count INTEGER, max_slot_saturation_pct REAL, summary_json TEXT);
PRAGMA user_version=1;
"""


class MasterDB:
    def __init__(self, path=DEFAULT_MASTER_PATH):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise ValueError("Not an empty Master DB; choose another path")
                # Explicit initial migration; future versions must add migrations here.
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        db.execute(statement)
            elif version != MASTER_SCHEMA_VERSION:
                raise ValueError("Unsupported Master schema version")
            db.commit()
            return db
        except Exception:
            db.close()
            raise

    def sessions(self, limit=200, offset=0):
        with closing(self.connect()) as db:
            rows = [dict(r) for r in db.execute("""SELECT s.*,q.*,
                (SELECT COUNT(*) FROM results r WHERE r.session_id=s.id AND r.status='missing') missing_results
                FROM sessions s LEFT JOIN quality q ON q.session_id=s.id
                ORDER BY session_date_et DESC,s.id DESC LIMIT ? OFFSET ?""", (limit, offset))]
            for row in rows:
                summary = json.loads(row.get("summary_json") or "{}")
                validation = summary.get("validation", {})
                row.update(validation_overall=validation.get("overall"), validator_version=validation.get("validator_version"),
                           validated_at=validation.get("validated_at"))
                protocol = summary.get("collection_protocol", {})
                row.update({key: protocol.get(key) for key in ("collection_mode", "protocol_compliant",
                    "protocol_reason", "scheduled_start", "actual_start", "start_delay_ms", "scheduled_stop", "actual_stop", "stop_delay_ms")})
            return rows

    def save_validation(self, run_id, validation):
        if validation.get("run_id") != run_id:
            raise ValueError("Validation identity mismatch")
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT q.session_id,q.summary_json,s.session_date_et FROM quality q JOIN sessions s "
                             "ON s.id=q.session_id WHERE s.run_id=?", (run_id,)).fetchone()
            if row is None or row["session_date_et"] != validation.get("session_date"):
                raise ValueError("Validation session not found")
            summary = json.loads(row["summary_json"])
            summary["validation"] = validation
            db.execute("UPDATE quality SET summary_json=? WHERE session_id=?",
                       (json.dumps(summary, ensure_ascii=False), row["session_id"]))

    def details(self, session_id):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise ValueError("Session no longer exists; refresh history")
            return dict(session=dict(row), **{table: [dict(r) for r in db.execute(
                f"SELECT * FROM {table} WHERE session_id=?", (session_id,))]
                for table in ("candidates", "results", "quality")})

    def ticker_history(self, ticker, limit=1000):
        with closing(self.connect()) as db:
            return [dict(r) for r in db.execute("""SELECT s.session_date_et,s.run_id,c.*,r.horizon,
                r.status,r.missing_reason,r.return_pct,r.mfe,r.mae,r.sample_timestamp,
                r.sample_offset_seconds,r.sample_selection FROM candidates c
                JOIN sessions s ON s.id=c.session_id LEFT JOIN results r
                ON r.session_id=c.session_id AND r.ticker=c.ticker
                WHERE c.ticker=? ORDER BY s.session_date_et DESC,s.id DESC,r.horizon LIMIT ?""",
                (ticker.strip().upper(), limit))]
