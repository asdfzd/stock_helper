"""Measured evidence only; ranking is separate from network slot allocation."""
import json
import math

from ..models import instant
from . import STRATEGY_VERSION


EVIDENCE_FIELDS = (
    "ticker", "current_leader_rank", "emerging_rank", "candidate_sources",
    "day_change_pct", "return_1m", "return_5m", "rvol", "rvol_status",
    "hod", "hod_distance_pct", "opening_1m_high", "opening_1m_low",
    "opening_5m_high", "opening_5m_low", "price_age_ms", "price_received_utc",
    "ws_gap_detected", "bar_label_policy", "bar_cutoff_utc", "completed_bar_at",
    "candidate_detected_at", "timestamp_utc",
)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def top(value):
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10


class ExperimentalBaselinePolicyV1:
    @staticmethod
    def priority(row):
        current, emerging = top(row.get("current_leader_rank")), top(row.get("emerging_rank"))
        return (0 if current and emerging else 1 if emerging else 2,
                row.get("emerging_rank") or 10**9, row.get("current_leader_rank") or 10**9, row["ticker"])

    @staticmethod
    def candidate_class(row):
        if top(row.get("current_leader_rank")) and top(row.get("emerging_rank")):
            return "current_and_emerging"
        return "emerging" if top(row.get("emerging_rank")) else "current"

    @staticmethod
    def fresh(row, at, max_age):
        try:
            age = (instant(at) - instant(row["price_received_utc"])).total_seconds()
            return (finite(row.get("price")) and row["price"] > 0 and 0 <= age <= max_age
                    and row.get("ws_gap_detected") in (False, 0)
                    and row.get("ws_gap_detected") is not None
                    and finite(row.get("price_age_ms")) and 0 <= row["price_age_ms"] <= max_age * 1000
                    and row.get("provider") == "kiwoom")
        except (KeyError, ValueError, TypeError):
            return False

    def select(self, rows, traded, at, max_age):
        eligible = [r for r in rows if r["ticker"] not in traded
                    and (top(r.get("current_leader_rank")) or top(r.get("emerging_rank")))
                    and self.fresh(r, at, max_age)]
        return min(eligible, key=self.priority) if eligible else None

    @staticmethod
    def confirmed(row, watch):
        bar = row.get("completed_bar_at")
        old = watch.get("watch_completed_bar_at")
        return (bar is not None and instant(bar) > instant(watch["watch_started_at"])
                and (old is None or instant(bar) > instant(old))
                and finite(row.get("return_1m")) and row["return_1m"] > 0
                and finite(row.get("return_5m")) and row["return_5m"] > 0
                and row["price"] > watch["watch_reference_price"])

    def evidence(self, row, watch):
        evidence = {key: row.get(key) for key in EVIDENCE_FIELDS}
        sources = evidence["candidate_sources"]
        if isinstance(sources, str):
            sources = json.loads(sources)
        evidence.update(policy=STRATEGY_VERSION, candidate_class=self.candidate_class(row),
                        candidate_sources=sorted(sources or []), priority=list(self.priority(row)),
                        watch_reference_price=watch["watch_reference_price"], signal_price=row["price"])
        return evidence


def reason(evidence):
    """Only persisted structured evidence; no LLM, score or participant narrative."""
    return (f"Emerging #{evidence.get('emerging_rank')} / Current #{evidence.get('current_leader_rank')}; "
            f"{'+'.join(evidence.get('candidate_sources', []))}; "
            "completed 1m/5m momentum positive; live price above watch reference; price fresh / WS normal")
