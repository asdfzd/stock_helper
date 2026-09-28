"""Thread-safe local raw evidence and per-session minute cache, separate identity."""
from datetime import datetime, timedelta
import json
import math
import sqlite3
import threading

from ..models import Bar, ET, instant, utc

POLICY = "assumed_et_start_v1"


def number(value, *, price=False):
    if value is None or value == "":
        return None
    result = float(str(value).replace(",", ""))
    if not math.isfinite(result):
        raise ValueError("Nonfinite Kiwoom number")
    return abs(result) if price else result


def completed_cutoff(now, session, grace=2):
    at = (now-timedelta(seconds=grace)).replace(second=0, microsecond=0)
    return max(instant(session.open_utc), min(at, instant(session.close_utc)))


def normalize_bars(rows, session, cutoff):
    """Research policy, not confirmed vendor timestamp semantics. Never fabricate gaps."""
    bars = {}
    for row in rows:
        stamp = str(row.get("cntr_tm", ""))
        if len(stamp) != 14 or not stamp.isdigit() or stamp[:8] != session.date.replace("-", ""):
            continue
        # 24+ hour records are retained raw but excluded from regular-session conversion.
        if int(stamp[8:10]) >= 24:
            continue
        start = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=ET)
        end = start+timedelta(minutes=1)
        if row.get("_received_utc") and end > instant(row["_received_utc"]):
            continue  # cached partial candles never become final merely by waiting
        if start < instant(session.open_utc) or end > cutoff or end > instant(session.close_utc):
            continue
        o, h, l, c = (number(row.get(k), price=True) for k in ("open_pric", "high_pric", "low_pric", "cur_prc"))
        volume = number(row.get("trde_qty"))
        if None in (o, h, l, c, volume) or not 0 < l <= min(o, c) <= max(o, c) <= h or volume < 0:
            continue
        bars[utc(start)] = Bar(utc(start), utc(end), h, l, c, volume, None)
    return tuple(bars[k] for k in sorted(bars))


def contiguous(bars, session, cutoff):
    cursor = instant(session.open_utc)
    for bar in bars:
        if instant(bar.start_utc) != cursor:
            return False
        cursor = instant(bar.end_utc)
    return bool(bars) and cursor == cutoff


class KiwoomCache:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS cache_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS minute_bars(ticker TEXT,exchange TEXT,session_date TEXT,
            raw_timestamp TEXT,raw_json TEXT NOT NULL,received_utc TEXT NOT NULL,
            PRIMARY KEY(ticker,exchange,session_date,raw_timestamp));
        CREATE TABLE IF NOT EXISTS cache_days(ticker TEXT,exchange TEXT,session_date TEXT,
            complete INTEGER NOT NULL,updated_utc TEXT NOT NULL,PRIMARY KEY(ticker,exchange,session_date));
        CREATE TABLE IF NOT EXISTS daily_bars(ticker TEXT,exchange TEXT,session_date TEXT,
            raw_json TEXT NOT NULL,received_utc TEXT NOT NULL,PRIMARY KEY(ticker,exchange,session_date));
        CREATE TABLE IF NOT EXISTS raw_records(id INTEGER PRIMARY KEY,kind TEXT,ticker TEXT,
            received_utc TEXT NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS raw_time ON raw_records(received_utc);
        CREATE TABLE IF NOT EXISTS symbol_catalog(ticker TEXT PRIMARY KEY,exchange TEXT,raw_json TEXT,updated_utc TEXT);
        """)
        identity = json.dumps({"provider": "kiwoom", "mode": "real", "schema": 1, "bar_policy": POLICY}, sort_keys=True)
        prior = self.db.execute("SELECT value FROM cache_metadata WHERE key='identity'").fetchone()
        if prior and prior[0] != identity:
            self.db.close()
            raise ValueError("Kiwoom cache identity mismatch")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO cache_metadata VALUES('identity',?)", (identity,))

    def raw(self, kind, ticker, received, payload):
        with self.lock, self.db:
            self.db.execute("INSERT INTO raw_records(kind,ticker,received_utc,payload) VALUES(?,?,?,?)",
                            (kind, ticker, received, json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def raw_many(self, rows):
        with self.lock, self.db:
            self.db.executemany("INSERT INTO raw_records(kind,ticker,received_utc,payload) VALUES(?,?,?,?)",
                [(k, t, at, json.dumps(p, ensure_ascii=False, allow_nan=False)) for k, t, at, p in rows])

    def put_minutes(self, ticker, exchange, rows, received):
        with self.lock, self.db:
            for row in rows:
                stamp = str(row.get("cntr_tm", ""))
                if len(stamp) != 14 or not stamp.isdigit():
                    continue
                day = stamp[:4]+"-"+stamp[4:6]+"-"+stamp[6:8]
                self.db.execute("INSERT OR REPLACE INTO minute_bars VALUES(?,?,?,?,?,?)", (
                    ticker, exchange, day, stamp, json.dumps(row), received))

    def rows(self, ticker, exchange, day):
        with self.lock:
            return [dict(json.loads(r[0]), _received_utc=r[1]) for r in self.db.execute(
                "SELECT raw_json,received_utc FROM minute_bars WHERE ticker=? AND exchange=? AND session_date=? ORDER BY raw_timestamp",
                (ticker, exchange, day))]

    def mark_complete(self, ticker, exchange, session, received):
        bars = normalize_bars(self.rows(ticker, exchange, session.date), session, instant(session.close_utc))
        complete = contiguous(bars, session, instant(session.close_utc))
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO cache_days VALUES(?,?,?,?,?)", (ticker, exchange, session.date, int(complete), received))
        return complete

    def is_complete(self, ticker, exchange, day):
        with self.lock:
            row = self.db.execute("SELECT complete FROM cache_days WHERE ticker=? AND exchange=? AND session_date=?",
                                  (ticker, exchange, day)).fetchone()
            return bool(row and row[0])

    def put_daily(self, ticker, exchange, rows, received):
        with self.lock, self.db:
            for r in rows:
                stamp = str(r.get("dt", ""))
                if len(stamp) == 8 and stamp.isdigit():
                    day = stamp[:4]+"-"+stamp[4:6]+"-"+stamp[6:8]
                    self.db.execute("INSERT OR REPLACE INTO daily_bars VALUES(?,?,?,?,?)",
                                    (ticker, exchange, day, json.dumps(r), received))

    def daily(self, ticker, exchange):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM daily_bars WHERE ticker=? AND exchange=? ORDER BY session_date DESC",
                                                    (ticker, exchange))]

    def put_catalog(self, rows, received):
        with self.lock, self.db:
            for r in rows:
                if r.get("stk_cd") and r.get("stex_tp") in {"ND", "NY", "NA"}:
                    self.db.execute("INSERT OR REPLACE INTO symbol_catalog VALUES(?,?,?,?)",
                        (r["stk_cd"], r["stex_tp"], json.dumps(r), received))

    def catalog(self):
        with self.lock:
            return {r["ticker"]: dict(r) for r in self.db.execute("SELECT * FROM symbol_catalog")}

    def close(self):
        with self.lock:
            self.db.close()
