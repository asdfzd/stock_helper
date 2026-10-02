"""Presentation helpers, without IO."""
import json
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

LIVE_COLUMNS = [("rank", "Rank"), ("ticker", "Ticker"), ("price", "Price"),
    ("day_change_pct", "Day %"), ("return_1m", "1m %"), ("return_5m", "5m %"),
    ("rvol", "RVOL"), ("hod_distance_pct", "HOD Distance %"), ("or_state", "OR1 / OR5"),
    ("candidate_sources", "Source"), ("tracking", "Tracking"), ("data_status", "Data Status")]
HISTORY_COLUMNS = [("session_date_et", "Date (ET)"), ("max_ws_symbols", "Max WS"),
    ("candidate_count", "Candidates"), ("snapshot_count", "Snapshots"),
    ("max_slot_saturation_pct", "Max Saturation %"), ("slot_unavailable_count", "Slot unavailable events"),
    ("rest_error_count", "REST Errors"), ("rvol_available_pct", "RVOL %"), ("clean_shutdown", "Clean Shutdown"),
    ("duration_seconds", "Duration (s)"), ("test_version", "Version"), ("missing_results", "Missing Results"),
    ("timing_basis", "Duration Basis"), ("run_id", "Session ID")]


def display(value):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class DataTable(QTableWidget):
    def __init__(self, columns=(), parent=None):
        super().__init__(parent)
        self.columns = list(columns)
        self.rows = []
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels([v for _, v in columns])

    def render_rows(self, rows, *, automatic_columns=False):
        self.rows = rows
        if automatic_columns:
            keys = list(dict.fromkeys(k for row in rows for k in row))
            self.columns = [(k, k) for k in keys]
            self.setColumnCount(len(keys))
            self.setHorizontalHeaderLabels(keys)
        self.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, (key, _) in enumerate(self.columns):
                item = QTableWidgetItem(display(row.get(key)))
                item.setToolTip(display(row.get(key)))
                if isinstance(row.get(key), (int, float)):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(i, j, item)


def live_rows(rows, rank_key):
    output = []
    for r in sorted((r for r in rows if r.get(rank_key) is not None and r[rank_key] <= 10), key=lambda r: r[rank_key]):
        row = dict(r, rank=r[rank_key])
        sources = r.get("candidate_sources")
        if isinstance(sources, str):
            try:
                sources = json.loads(sources)
            except ValueError:
                sources = [sources]
        labels = {"day_change": "CHG", "5m_spike": "SPIKE", "volume": "VOL", "value": "VALUE"}
        row["candidate_sources"] = "+".join(labels.get(s, s) for s in sources) if sources else None
        states = []
        for minutes in (1, 5):
            high, low, price = r.get(f"opening_{minutes}m_high"), r.get(f"opening_{minutes}m_low"), r.get("price")
            states.append("-" if high is None or low is None or price is None else
                          "above" if price > high else "below" if price < low else "inside")
        row["or_state"] = " / ".join(states)
        row["tracking"] = r.get("candidate_tracking_state") or "unknown"
        row["data_status"] = "RVOL: " + (r.get("rvol_status") or "unknown")
        if r.get("price_age_ms") is not None:
            row["data_status"] += f" · age {display(r['price_age_ms'])}ms"
        if r.get("ws_gap_detected"):
            row["data_status"] += " · WS gap"
        output.append(row)
    return output


def health_rows(summary, max_ws, *, state="STOPPED", connection=None):
    t, m, r = (summary.get(k, {}) for k in ("tracking", "market_data", "runtime"))
    rest = summary.get("rest_errors", {})
    connection = connection or {}
    stopped = state == "STOPPED"
    connected = False if stopped else connection.get("connected")
    active = 0 if stopped or connected is False else connection.get("active_ws")
    values = [("Collector State", state), ("Current Connected", "Yes" if connected is True else "No" if connected is False else None),
        ("Current Active WS", active), ("Max WS", max_ws)]
    if state == "RUNNING":
        values += [("Current Protected", t.get("protected_count")), ("Current Discovery", t.get("discovery_count")),
            ("Current Slot unavailable", t.get("tracking_slot_unavailable_count")),
            ("Current Saturation %", 100 * active / max_ws if active is not None and max_ws else None)]
    values += [("Last Active WS", t.get("active_subscription_count")),
        ("Last Protected", t.get("protected_count")), ("Last Discovery", t.get("discovery_count")),
        ("Last Slot unavailable", t.get("tracking_slot_unavailable_count")), ("Last Slot saturation %", t.get("slot_saturation_pct")),
        ("Snapshot count", summary.get("snapshot_count")), ("Candidate count", summary.get("candidate_count")),
        ("RVOL available %", summary.get("rvol", {}).get("available_pct")),
        ("1m return available %", summary.get("returns", {}).get("return_1m_available_pct")),
        ("5m return available %", summary.get("returns", {}).get("return_5m_available_pct")),
        ("WS gaps", m.get("ws_gap_count")), ("Stale count", m.get("stale_count")),
        ("REST errors", rest.get("total")), ("Deterministic suppressed", rest.get("suppressed_count")),
        ("Backfill incomplete", summary.get("backfill", {}).get("incomplete")),
        ("Worker errors", r.get("worker_error_count")), ("DB errors", r.get("db_error_count")),
        ("Export errors", r.get("export_error_count"))]
    return [{"metric": k, "value": v} for k, v in values]
