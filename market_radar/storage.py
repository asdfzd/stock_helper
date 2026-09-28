from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS radar_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS radar_batches(
    timestamp_utc TEXT PRIMARY KEY, provider TEXT NOT NULL, mode TEXT NOT NULL,
    universe TEXT NOT NULL, raw_json TEXT NOT NULL, received_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS radar_snapshots(
    id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL REFERENCES radar_batches(timestamp_utc),
    timestamp_et TEXT NOT NULL, session_date TEXT NOT NULL, ticker TEXT NOT NULL,
    price REAL NOT NULL, day_change_pct REAL, volume REAL, rvol REAL,
    return_1m REAL, return_5m REAL, vwap REAL, price_vs_vwap_pct REAL, above_vwap INTEGER,
    opening_1m_high REAL, opening_1m_low REAL, opening_5m_high REAL, opening_5m_low REAL,
    hod REAL, hod_distance_pct REAL, current_leader_rank INTEGER, emerging_rank INTEGER,
    test_version TEXT NOT NULL, session_type TEXT NOT NULL, coverage_complete INTEGER NOT NULL,
    in_universe INTEGER NOT NULL,
    UNIQUE(timestamp_utc,ticker));
CREATE INDEX IF NOT EXISTS snapshot_ticker_time ON radar_snapshots(ticker,timestamp_utc);
CREATE INDEX IF NOT EXISTS snapshot_day ON radar_snapshots(session_date);
CREATE TABLE IF NOT EXISTS radar_candidates(
    id INTEGER PRIMARY KEY, session_date TEXT NOT NULL, ticker TEXT NOT NULL,
    first_detected_utc TEXT NOT NULL, first_detected_et TEXT NOT NULL, first_price REAL NOT NULL,
    session_close_utc TEXT NOT NULL, test_version TEXT NOT NULL,
    UNIQUE(session_date,ticker));
CREATE TABLE IF NOT EXISTS radar_events(
    id INTEGER PRIMARY KEY, timestamp_utc TEXT NOT NULL, timestamp_et TEXT NOT NULL,
    session_date TEXT NOT NULL, ticker TEXT NOT NULL, event_type TEXT NOT NULL,
    price REAL NOT NULL, snapshot_id INTEGER NOT NULL REFERENCES radar_snapshots(id),
    test_version TEXT NOT NULL, UNIQUE(snapshot_id,event_type));
CREATE INDEX IF NOT EXISTS event_day ON radar_events(session_date);
CREATE TABLE IF NOT EXISTS radar_state(
    ticker TEXT PRIMARY KEY, session_date TEXT NOT NULL, snapshot_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_sessions(
    session_date TEXT PRIMARY KEY, open_utc TEXT NOT NULL, close_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS market_closes(
    ticker TEXT NOT NULL, session_date TEXT NOT NULL, timestamp_utc TEXT NOT NULL,
    price REAL NOT NULL, PRIMARY KEY(ticker,session_date));
CREATE TABLE IF NOT EXISTS radar_results(
    id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES radar_candidates(id),
    horizon TEXT NOT NULL, target_utc TEXT, observed_utc TEXT, price REAL, return_pct REAL,
    status TEXT NOT NULL DEFAULT 'pending', timing_error_seconds REAL,
    mfe_pct REAL, mae_pct REAL, excursion_basis TEXT NOT NULL DEFAULT 'sampled_prices',
    UNIQUE(candidate_id,horizon));
"""

# Separate creation schema; never ALTER or silently migrate a v1 database.
SCHEMA_V2 = SCHEMA.replace("    UNIQUE(timestamp_utc,ticker));", """
    previous_close REAL, candidate_sources TEXT, source_ranks TEXT, symbol_type TEXT,
    rvol_basis TEXT, rvol_cutoff_et TEXT, bar_cutoff_et TEXT, price_received_utc TEXT,
    provider_latency_ms REAL, price_age_ms REAL, ws_gap_detected INTEGER,
    provider TEXT, exchange TEXT, bar_label_policy TEXT,
    UNIQUE(timestamp_utc,ticker));""").replace("    UNIQUE(session_date,ticker));", """
    candidate_sources TEXT, provider TEXT, exchange TEXT,
    UNIQUE(session_date,ticker));""").replace("price REAL NOT NULL, PRIMARY KEY(ticker,session_date));", """
    price REAL NOT NULL, source TEXT NOT NULL DEFAULT 'official', status TEXT NOT NULL DEFAULT 'complete',
    PRIMARY KEY(ticker,session_date));""").replace("    UNIQUE(candidate_id,horizon));", """
    result_source TEXT, close_status TEXT,
    UNIQUE(candidate_id,horizon));""")


class RadarStore:
    """One connection owned by the collecting thread, WAL for external readers."""
    def __init__(self, path: Path, identity: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.schema_version = 2 if identity.get("provider") == "kiwoom" else 1
        try:
            encoded = json.dumps(identity, sort_keys=True)
            exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='radar_metadata'").fetchone()
            prior = self.db.execute("SELECT value FROM radar_metadata WHERE key='identity'").fetchone() if exists else None
            if prior and prior[0] != encoded:
                raise ValueError("DB provider/mode/config mismatch: use a separate data directory")
            version = self.db.execute("SELECT value FROM radar_metadata WHERE key='schema_version'").fetchone() if exists else None
            if exists and (not version or version[0] != str(self.schema_version)):
                raise ValueError("DB schema mismatch: use a new experimental database")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA_V2 if self.schema_version == 2 else SCHEMA)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO radar_metadata VALUES('identity',?)", (encoded,))
                self.db.execute("INSERT OR IGNORE INTO radar_metadata VALUES('schema_version',?)", (str(self.schema_version),))
        except Exception:
            self.db.close()
            raise

    def insert(self, table: str, row: dict) -> int:
        if table not in {"radar_snapshots", "radar_events", "radar_candidates", "radar_results"}:
            raise ValueError("Unsupported table")
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        return self.db.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(row.values())).lastrowid

    def tracked_tickers(self) -> tuple[str, ...]:
        return tuple(r[0] for r in self.db.execute(
            "SELECT DISTINCT c.ticker FROM radar_candidates c JOIN radar_results r ON c.id=r.candidate_id "
            "WHERE r.status IN ('pending','provisional') ORDER BY c.ticker"))

    def export_day(self, day: str, root: Path) -> Path:
        # Validate path component before creating a directory.
        from datetime import date
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("Expected ISO date")
        folder = root / day
        folder.mkdir(parents=True, exist_ok=True)
        queries = {
            "candidates": "SELECT * FROM radar_candidates WHERE session_date=? ORDER BY id",
            "snapshots": "SELECT * FROM radar_snapshots WHERE session_date=? ORDER BY id",
            "events": "SELECT * FROM radar_events WHERE session_date=? ORDER BY id",
            "results": "SELECT c.session_date,c.ticker,c.first_detected_utc,c.first_price,c.test_version,r.* "
                       "FROM radar_results r JOIN radar_candidates c ON c.id=r.candidate_id "
                       "WHERE c.session_date=? ORDER BY r.id",
        }
        for name, query in queries.items():
            cursor = self.db.execute(query, (day,))
            temporary = folder / f"{name}.csv.tmp"
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow([c[0] for c in cursor.description])
                writer.writerows(cursor)
            os.replace(temporary, folder / f"{name}.csv")
        identity = self.db.execute("SELECT value FROM radar_metadata WHERE key='identity'").fetchone()[0]
        temporary = folder / "metadata.json.tmp"
        temporary.write_text(identity, encoding="utf-8")
        os.replace(temporary, folder / "metadata.json")
        return folder

    def export_all(self, root: Path) -> list[Path]:
        days = [r[0] for r in self.db.execute(
            "SELECT DISTINCT session_date FROM radar_snapshots UNION SELECT DISTINCT session_date FROM radar_candidates ORDER BY 1")]
        return [self.export_day(day, root) for day in days]

    def close(self):
        self.db.close()
