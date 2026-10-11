"""Sidecar database: baseline DB/schema/files are never migrated or rewritten."""
import csv
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from ..paper.engine import encode
from ..paper.storage import PaperStore, FIELDS
from . import ROTATION_SCHEMA_VERSION, STRATEGY_VERSION, SWITCH_REASON


class RotationStore(PaperStore):
    def __init__(self, path):
        path = Path(path)
        if path.exists() and path.stat().st_size:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as check:
                tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "rotation_metadata" not in tables:
                    raise ValueError("Rotation requires its own sidecar database")
                version = check.execute("SELECT version FROM rotation_metadata").fetchone()
                if not version or version[0] != ROTATION_SCHEMA_VERSION:
                    raise ValueError("Unsupported rotation schema")
        super().__init__(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS rotation_metadata(version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rotation_details(trade_id TEXT PRIMARY KEY, detail_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS rotation_switches(switch_id TEXT PRIMARY KEY, detail_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS review_trades(review_id TEXT PRIMARY KEY, detail_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_post_exit(review_id TEXT PRIMARY KEY, detail_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS paper_post_exit_marks(
                observation_id INTEGER, review_id TEXT, timestamp_utc TEXT, price REAL,
                PRIMARY KEY(observation_id,review_id));
        """)
        with self.db:
            if not self.db.execute("SELECT 1 FROM rotation_metadata").fetchone():
                self.db.execute("INSERT INTO rotation_metadata VALUES(?)", (ROTATION_SCHEMA_VERSION,))

    def put(self, table, key, value):
        if table not in {"rotation_details", "rotation_switches", "review_trades", "paper_post_exit"}:
            raise ValueError("Unknown sidecar table")
        self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (key, encode(value)))

    def documents(self, table):
        if table not in {"rotation_details", "rotation_switches", "review_trades", "paper_post_exit"}:
            raise ValueError("Unknown sidecar table")
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT detail_json FROM {table} ORDER BY 1")]

    def save(self, trade):
        super().save(trade)
        self.put("rotation_details", trade["trade_id"], {k: v for k, v in trade.items() if k not in FIELDS})

    def trades(self, day=None):
        rows = super().trades(day)
        for row in rows:
            detail = self.db.execute("SELECT detail_json FROM rotation_details WHERE trade_id=?", (row["trade_id"],)).fetchone()
            if detail:
                row.update(json.loads(detail[0]))
        return rows

    def summary(self, day):
        result = super().summary(day)
        valid = [t for t in self.entered(day) if t.get("return_pct") is not None and t["data_quality_status"] == "valid"]
        result["switch_count"] = sum(t["exit_reason"] == SWITCH_REASON for t in valid)
        result["valid_closed_count"] = len(valid)
        result["statistics_population"] = "closed valid trades including switches; TP/SL win rate uses TP/SL only"
        from statistics import mean, median
        for key in ("return_pct", "mfe_pct", "mae_pct"):
            values = [t[key] for t in valid if t[key] is not None]
            result["average_" + key] = mean(values) if values else None
            result["median_" + key] = median(values) if values else None
        capital = 1.0
        for t in valid:
            capital *= 1 + t["return_pct"] / 100
        result["compounded_return_pct"] = (capital - 1) * 100 if valid else None
        return result

    def export_review(self, day, folder, baseline_summary):
        from .comparison import comparisons
        from .tracking import POST_FIELDS
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        trades = [t for t in self.documents("review_trades") if t["session_date_et"] == day]
        posts = [t for t in self.documents("paper_post_exit") if t["session_date_et"] == day]
        switches = [t for t in self.documents("rotation_switches") if t["session_date_et"] == day]
        marks = [dict(r) for r in self.db.execute("SELECT * FROM paper_post_exit_marks ORDER BY observation_id")]
        pairs = comparisons(trades, posts, switches, marks)
        for name, rows, minimum in (
            ("paper_trades", trades, ["strategy_version", "trade_id", "entry_reason", "exit_reason", "mfe_pct", "mae_pct"]),
            ("paper_post_exit", posts, POST_FIELDS + ["strategy_version", "trade_id", "tracking_status",
                "original_entry_price", "original_tp_price", "original_sl_price", "original_tp_hit_after_exit",
                "original_sl_hit_after_exit", "recovered_entry_after_sl", "original_tp_hit_after_sl"]),
            ("paper_rotation_comparison", pairs, ["switch_id", "baseline_v1_return", "rotation_v2_return"]),
            ("paper_rotation_switches", switches, ["switch_id", "stage", "switch_signal_at", "evidence"]),
        ):
            fields = sorted(set(minimum).union(*(r.keys() for r in rows)))
            target = folder / (name + ".csv")
            tmp = target.with_suffix(".csv.tmp")
            with tmp.open("w", encoding="utf-8", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=fields)
                writer.writeheader()
                writer.writerows({k: encode(v) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows)
            tmp.replace(target)
        target = folder / "paper_post_exit_marks.csv"
        tmp = target.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            writer.writerow(["observation_id", "review_id", "timestamp_utc", "price"])
            ids = {p["review_id"] for p in posts}
            writer.writerows(r for r in self.db.execute("SELECT * FROM paper_post_exit_marks ORDER BY observation_id,review_id") if r[1] in ids)
        tmp.replace(target)
        target = folder / "paper_daily_summary.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(encode(dict(session_date_et=day, baseline_v1=baseline_summary,
            rotation_v2=self.summary(day), rotation_schema_version=ROTATION_SCHEMA_VERSION,
            strategy=STRATEGY_VERSION, actual_order_calls=0, post_exit_count=len(posts),
            comparison_count=len(pairs), return_model="observed prices; no costs or fill simulation")), encoding="utf-8")
        tmp.replace(target)
