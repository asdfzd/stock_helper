"""Separate, versioned research datasets. Source schemas never migrate here."""
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from runtime_paths import APP_ROOT
from . import SCHEMA_VERSION
from .source import copied_database, encoded

DEFAULT_DB = APP_ROOT/"data"/"opening_hour_research"/"opening_hour.db"


class OpeningHourStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        # Reject source/Paper/unknown databases before any DDL or journal action.
        if self.path.exists() and self.path.stat().st_size:
            with copied_database(self.path) as check:
                version = check.execute("PRAGMA user_version").fetchone()[0]
                names = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if version != SCHEMA_VERSION or "opening_hour_sessions" not in names:
                    raise ValueError("Not an Opening Hour Research database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(f"""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS opening_hour_sessions(
                research_id TEXT PRIMARY KEY, feature_version TEXT NOT NULL, opening_hour_schema_version INTEGER NOT NULL,
                session_date_et TEXT NOT NULL, radar_run_id TEXT NOT NULL, test_version TEXT,
                source_cache_path TEXT, bar_policy TEXT, calendar_source TEXT, created_at TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL, metadata_json TEXT NOT NULL, quality_json TEXT NOT NULL, summary_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS opening_hour_candidates(
                research_id TEXT REFERENCES opening_hour_sessions(research_id), ticker TEXT, exchange TEXT,
                candidate_first_detected_at TEXT, detail_json TEXT NOT NULL, PRIMARY KEY(research_id,ticker));
            CREATE TABLE IF NOT EXISTS opening_hour_bars(
                research_id TEXT REFERENCES opening_hour_sessions(research_id), ticker TEXT, timeframe TEXT,
                bar_start_utc TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
                aggregate_complete INTEGER NOT NULL, detail_json TEXT NOT NULL,
                PRIMARY KEY(research_id,ticker,timeframe,bar_start_utc));
            CREATE TABLE IF NOT EXISTS opening_hour_events(
                research_id TEXT REFERENCES opening_hour_sessions(research_id), ticker TEXT, event_sequence INTEGER,
                event_type TEXT, event_at TEXT, detail_json TEXT NOT NULL, PRIMARY KEY(research_id,ticker,event_sequence));
            CREATE TABLE IF NOT EXISTS opening_hour_snapshots(
                research_id TEXT REFERENCES opening_hour_sessions(research_id), ticker TEXT, kind TEXT,
                horizon_minutes INTEGER, target_utc TEXT, detail_json TEXT NOT NULL,
                PRIMARY KEY(research_id,ticker,kind,horizon_minutes));
            CREATE TABLE IF NOT EXISTS opening_hour_outcomes(
                research_id TEXT REFERENCES opening_hour_sessions(research_id), ticker TEXT, detail_json TEXT NOT NULL,
                PRIMARY KEY(research_id,ticker));
            CREATE TABLE IF NOT EXISTS opening_hour_segments(
                research_id TEXT REFERENCES opening_hour_sessions(research_id), ticker TEXT,
                start_minute INTEGER, end_minute INTEGER, detail_json TEXT NOT NULL,
                PRIMARY KEY(research_id,ticker,start_minute,end_minute));
            PRAGMA user_version={SCHEMA_VERSION};
        """)

    def save(self, result):
        meta = result["metadata"]
        rid = meta["research_id"]
        with self.db:
            old = self.db.execute("SELECT metadata_json FROM opening_hour_sessions WHERE research_id=?", (rid,)).fetchone()
            if old:
                if old[0] != encoded(meta):
                    raise ValueError("Dataset identity collision")
                return rid  # Immutable, idempotent dataset; retain actual first creation time.
            self.db.execute("INSERT INTO opening_hour_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                rid, meta["feature_version"], SCHEMA_VERSION, meta["session_date_et"], meta["radar_run_id"], meta.get("test_version"),
                meta.get("source_cache_path"), meta["bar_policy"], meta["calendar_source"], datetime.now(timezone.utc).isoformat(),
                meta["source_fingerprint"], encoded(meta), encoded(result["quality"]), encoded(result["summary"])))
            for item in result["candidates"]:
                c = item["candidate"]
                ticker = c["ticker"]
                self.db.execute("INSERT INTO opening_hour_candidates VALUES(?,?,?,?,?)", (rid,ticker,c.get("exchange"),c["candidate_first_detected_at"],encoded(c)))
                for r in item["bars"]:
                    self.db.execute("INSERT INTO opening_hour_bars VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid,ticker,r["timeframe"],r["bar_start_utc"],
                        r["open"],r["high"],r["low"],r["close"],r["volume"],int(r["aggregate_complete"]),encoded(r)))
                for index, e in enumerate(item["events"]):
                    self.db.execute("INSERT INTO opening_hour_events VALUES(?,?,?,?,?,?)", (rid,ticker,index,e["event_type"],e["event_at"],encoded(e)))
                for s in item["snapshots"]:
                    self.db.execute("INSERT INTO opening_hour_snapshots VALUES(?,?,?,?,?,?)", (rid,ticker,s["kind"],s["horizon_minutes"],s["target_utc"],encoded(s)))
                self.db.execute("INSERT INTO opening_hour_outcomes VALUES(?,?,?)", (rid,ticker,encoded(item["outcome"])))
                for s in item["segments"]:
                    self.db.execute("INSERT INTO opening_hour_segments VALUES(?,?,?,?,?)", (rid,ticker,s["start_minute"],s["end_minute"],encoded(s)))
        return rid

    def load(self, rid):
        session = self.db.execute("SELECT * FROM opening_hour_sessions WHERE research_id=?", (rid,)).fetchone()
        if session is None:
            raise ValueError("Unknown dataset")
        result = dict(metadata=json.loads(session["metadata_json"]), quality=json.loads(session["quality_json"]),
                      summary=json.loads(session["summary_json"]), candidates=[])
        for r in self.db.execute("SELECT * FROM opening_hour_candidates WHERE research_id=? ORDER BY ticker", (rid,)):
            item = dict(candidate=json.loads(r["detail_json"]))
            for key, table, order in (("bars", "opening_hour_bars", "CAST(timeframe AS INTEGER),bar_start_utc"),
                ("events", "opening_hour_events", "event_sequence"), ("snapshots", "opening_hour_snapshots", "CASE kind WHEN 'market_open' THEN 0 ELSE 1 END,horizon_minutes"),
                ("segments", "opening_hour_segments", "start_minute,end_minute"), ("outcome", "opening_hour_outcomes", "ticker")):
                rows = [json.loads(d[0]) for d in self.db.execute(f"SELECT detail_json FROM {table} WHERE research_id=? AND ticker=? ORDER BY {order}", (rid,r["ticker"]))]
                item[key] = rows[0] if key == "outcome" else rows
            result["candidates"].append(item)
        return result

    def export(self, rid, export_root=None):
        result = self.load(rid)
        meta = result["metadata"]
        folder = Path(export_root or self.path.parent/"export")/meta["session_date_et"]/rid
        folder.mkdir(parents=True, exist_ok=True)
        common = dict(research_id=rid, radar_run_id=meta["radar_run_id"], session_date_et=meta["session_date_et"], feature_version=meta["feature_version"])
        groups = {k: [] for k in ("candidates", "bars_1m", "bars_3m", "bars_5m", "events", "time_snapshots", "outcomes", "volume_segments")}
        for item in result["candidates"]:
            c = item["candidate"]
            common_ticker = dict(common, ticker=c["ticker"])
            groups["candidates"].append(dict(c, **common))
            for r in item["bars"]:
                groups["bars_"+r["timeframe"]].append(dict(r, **common))
            for key, name in (("events", "events"), ("snapshots", "time_snapshots"), ("segments", "volume_segments")):
                groups[name].extend(dict(r, **common_ticker) for r in item[key])
            groups["outcomes"].append(dict(item["outcome"], **common_ticker))
        for name, rows in groups.items():
            fields = sorted(set(common) | {"ticker"} | set().union(*(r.keys() for r in rows)))
            path = folder/(name+".csv")
            tmp = path.with_suffix(".csv.tmp")
            with tmp.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows({k: encoded(v) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows)
            tmp.replace(path)
        for name in ("metadata", "quality", "summary"):
            path = folder/(name+".json")
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(encoded(result[name])+"\n", encoding="utf-8")
            tmp.replace(path)
        return folder

    def close(self):
        self.db.close()
