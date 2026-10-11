"""Separate paper schema. Never opens a research/cache/account database."""
import csv
import json
from pathlib import Path
import sqlite3
from statistics import mean, median

from ..engine import HORIZONS
from ..models import instant, utc
from datetime import timedelta
from . import PAPER_SCHEMA_VERSION


FLOAT_FIELDS = set("watch_reference_price entry_price target_price stop_price exit_price return_pct mfe_pct mae_pct max_price min_price current_price current_return_pct".split())
INTEGER_FIELDS = {"current_rank", "emerging_rank"}
FIELDS = ("trade_id radar_run_id session_date_et strategy_version ticker state watch_started_at watch_reference_price "
          "watch_completed_bar_at candidate_detected_at entry_signal_at entry_price_at entry_price entry_price_source "
          "target_price stop_price exit_signal_at exit_price_at exit_price exit_reason return_pct mfe_pct mae_pct mfe_at mae_at "
          "max_price min_price current_price current_return_pct last_price_at candidate_class current_rank emerging_rank "
          "candidate_sources entry_reason entry_evidence_json exit_reason_detail exit_evidence_json data_quality_status "
          "created_at updated_at").split()


class PaperStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.row_factory = sqlite3.Row
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if tables and (version != PAPER_SCHEMA_VERSION or "paper_trades" not in tables):
            self.db.close()
            raise ValueError("Not a supported paper database")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS paper_sessions(
                radar_run_id TEXT, session_date_et TEXT, strategy_version TEXT,
                status TEXT, state_json TEXT NOT NULL, summary_json TEXT, updated_at TEXT,
                PRIMARY KEY(radar_run_id,session_date_et));
            CREATE TABLE IF NOT EXISTS paper_trades({','.join(f'{k} REAL' if k in FLOAT_FIELDS else f'{k} INTEGER' if k in INTEGER_FIELDS else f'{k} TEXT' for k in FIELDS)},
                PRIMARY KEY(trade_id));
            CREATE TABLE IF NOT EXISTS paper_trade_events(
                event_id INTEGER PRIMARY KEY, trade_id TEXT, timestamp_utc TEXT, event_type TEXT, evidence_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_trade_marks(
                ticker TEXT, timestamp_utc TEXT, price REAL NOT NULL, session_date_et TEXT,
                PRIMARY KEY(ticker,timestamp_utc));
            CREATE TABLE IF NOT EXISTS paper_horizons(
                trade_id TEXT, horizon TEXT, target_utc TEXT, price REAL, return_pct REAL, missing_reason TEXT,
                PRIMARY KEY(trade_id,horizon));
            CREATE UNIQUE INDEX IF NOT EXISTS paper_one_open ON paper_trades((1)) WHERE state='OPEN';
            CREATE UNIQUE INDEX IF NOT EXISTS paper_no_reentry ON paper_trades(session_date_et,ticker) WHERE entry_price IS NOT NULL;
            CREATE TRIGGER IF NOT EXISTS paper_daily_limit BEFORE UPDATE OF entry_price ON paper_trades
            WHEN NEW.entry_price IS NOT NULL AND OLD.entry_price IS NULL AND
              (SELECT COUNT(*) FROM paper_trades WHERE session_date_et=NEW.session_date_et AND entry_price IS NOT NULL)>=4
            BEGIN SELECT RAISE(ABORT,'paper daily limit'); END;
            PRAGMA user_version={PAPER_SCHEMA_VERSION};
        """)

    def save(self, trade):
        self.db.execute(f"INSERT INTO paper_trades ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)}) "
                        f"ON CONFLICT(trade_id) DO UPDATE SET {','.join(f'{k}=excluded.{k}' for k in FIELDS if k != 'trade_id')}",
                        [trade.get(k) for k in FIELDS])

    def event(self, trade, at, kind, evidence):
        self.db.execute("INSERT INTO paper_trade_events(trade_id,timestamp_utc,event_type,evidence_json) VALUES(?,?,?,?)",
                        (trade["trade_id"], at, kind, json.dumps(evidence, sort_keys=True, allow_nan=False)))

    def trades(self, day=None):
        return [dict(r) for r in self.db.execute("SELECT * FROM paper_trades" +
                (" WHERE session_date_et=?" if day else "") + " ORDER BY created_at,trade_id", (day,) if day else ())]

    def entered(self, day):
        return [r for r in self.trades(day) if r["entry_price"] is not None]

    def summary(self, day):
        entries = self.entered(day)
        valid = [r for r in entries if r["exit_reason"] in {"TAKE_PROFIT_15", "STOP_LOSS_10", "SESSION_END"}]
        tp = sum(r["exit_reason"] == "TAKE_PROFIT_15" for r in entries)
        sl = sum(r["exit_reason"] == "STOP_LOSS_10" for r in entries)
        result = dict(total_entries=len(entries), take_profit_count=tp, stop_loss_count=sl,
                      session_end_count=sum(r["exit_reason"] == "SESSION_END" for r in entries),
                      invalid_count=sum(r["exit_reason"] in {"DATA_INTEGRITY_INVALID", "INTERRUPTED"} for r in entries),
                      tp_sl_win_rate=100*tp/(tp+sl) if tp+sl else None,
                      statistics_population="closed valid trades; TP/SL win rate uses TP/SL only",
                      return_model="observed prices; fees, spread and slippage not modeled")
        for field in ("return_pct", "mfe_pct", "mae_pct"):
            values = [r[field] for r in valid if r[field] is not None]
            result["average_"+field] = mean(values) if values else None
            result["median_"+field] = median(values) if values else None
        capital = 1.0
        for r in valid:
            capital *= 1+r["return_pct"]/100
        result["compounded_return_pct"] = (capital-1)*100 if valid else None
        result["compounding_assumption"] = "all capital reinvested in each valid closed trade; excludes invalid trades and costs"
        return result

    def horizons(self, day):
        """Post-session only, exact received-time match. No interpolation/grace/proxy.

        Reuse Radar horizon durations but not FIRST_DETECTED-anchored result prices.
        Marks after exit are observation evidence only, never fed back into decisions.
        """
        for trade in self.entered(day):
            if not trade["exit_reason"]:
                continue
            for label, seconds in HORIZONS.items():
                if seconds > 1800:
                    continue
                target = utc(instant(trade["entry_price_at"])+timedelta(seconds=seconds))
                row = self.db.execute("SELECT price FROM paper_trade_marks WHERE ticker=? AND timestamp_utc=?",
                                      (trade["ticker"], target)).fetchone()
                self.db.execute("INSERT OR REPLACE INTO paper_horizons VALUES(?,?,?,?,?,?)",
                    (trade["trade_id"], label, target, row[0] if row else None,
                     (row[0]/trade["entry_price"]-1)*100 if row else None, None if row else "no_exact_observation"))

    def export(self, day, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for table in ("paper_trades", "paper_trade_events", "paper_horizons"):
            query = (f"SELECT * FROM {table} WHERE session_date_et=?" if table == "paper_trades" else
                     f"SELECT * FROM {table} WHERE trade_id IN (SELECT trade_id FROM paper_trades WHERE session_date_et=?)")
            cursor = self.db.execute(query, (day,))
            target = folder/(table+".csv")
            temporary = target.with_suffix(".csv.tmp")
            with temporary.open("w", newline="", encoding="utf-8") as out:
                writer = csv.writer(out)
                writer.writerow([d[0] for d in cursor.description])
                writer.writerows(cursor)
            temporary.replace(target)
        target = folder/"summary.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.summary(day), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(target)

    def close(self):
        self.db.close()
