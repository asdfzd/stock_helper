from datetime import timedelta
from dataclasses import replace
import json

from .config import RadarConfig
from .models import ET, Observation, instant, utc


def pct(value, base):
    return None if value is None or base is None or base <= 0 else (value / base - 1) * 100


def calculate(o: Observation, config: RadarConfig) -> dict:
    o.validate()
    if o.metadata.get("rvol_basis") == "completed_1m":
        cutoff = instant(o.metadata["bar_cutoff_utc"])
        if cutoff > instant(o.asof_utc) or cutoff < instant(o.session.open_utc):
            raise ValueError("Invalid completed-bar cutoff")
        bars = tuple(b for b in o.bars if instant(b.end_utc) <= cutoff)
        final = next((b for b in reversed(bars) if instant(b.end_utc) == cutoff), None)
        reduced = replace(o, asof_utc=utc(cutoff), price=final.close if final else o.price, bars=bars, metadata={})
        row = calculate(reduced, config)
        if final is None:
            row["return_1m"] = row["return_5m"] = None
        row.update(timestamp_utc=utc(instant(o.asof_utc)), timestamp_et=instant(o.asof_utc).astimezone(ET).isoformat(),
                   price=o.price, previous_close=o.previous_close, day_change_pct=pct(o.price, o.previous_close),
                   hod_distance_pct=pct(o.price, row["hod"]), vwap=None, price_vs_vwap_pct=None, above_vwap=None,
                   provider="kiwoom", exchange=o.metadata["exchange"],
                   candidate_sources=json.dumps(o.metadata.get("candidate_sources", [])),
                   source_ranks=json.dumps(o.metadata.get("source_ranks", {})),
                   symbol_type=o.metadata.get("symbol_type", "type_unknown"),
                   rvol_basis="completed_1m", rvol_cutoff_et=o.metadata["rvol_cutoff_et"],
                   bar_cutoff_et=o.metadata["bar_cutoff_et"], price_received_utc=o.metadata.get("price_received_utc"),
                   provider_latency_ms=o.metadata.get("provider_latency_ms"), price_age_ms=o.metadata.get("price_age_ms"),
                   ws_gap_detected=int(o.metadata.get("ws_gap_detected", False)),
                   bar_label_policy=o.metadata["bar_label_policy"])
        if config.test_version in {"kiwoom_radar_v2", "kiwoom_radar_v2_1"}:
            status = o.metadata.get("rvol_status", "unknown")
            elapsed = int((cutoff-instant(o.session.open_utc)).total_seconds())
            values = {h.session_date: h.volume for h in o.historical_volumes if h.elapsed_seconds == elapsed}
            dates = o.history_dates[:config.rvol_lookback_days]
            if status == "not_eligible":
                row["rvol"] = None
            elif row["rvol"] is not None:
                status = "available"
            elif len(dates) == config.rvol_lookback_days and all(d in values for d in dates) and sum(values[d] for d in dates) == 0:
                status = "zero_denominator"
            elif row["volume"] is None and elapsed > 0:
                status = "incomplete_history"
            elif status not in {"warming", "incomplete_history"}:
                status = "incomplete_history"
            row.update(rvol_status=status, symbol_type_evidence=json.dumps(o.metadata.get("symbol_type_evidence", {})))
        return row
    at, start = instant(o.asof_utc), instant(o.session.open_utc)
    cursor = start
    contiguous = True
    for b in o.bars:
        contiguous &= instant(b.start_utc) == cursor
        cursor = instant(b.end_utc)
    complete = bool(o.bars) and contiguous and cursor == at
    volume = sum(b.volume for b in o.bars) if complete else None
    notional = sum(b.notional for b in o.bars) if complete and all(
        b.notional is not None for b in o.bars
    ) else None
    vwap = notional / volume if notional is not None and volume else None
    hod = max(b.high for b in o.bars) if complete else None
    elapsed = (at - start).total_seconds()
    dates = o.history_dates[:config.rvol_lookback_days]
    history = {h.session_date: h.volume for h in o.historical_volumes if h.elapsed_seconds == elapsed}
    enough = len(dates) == config.rvol_lookback_days and all(d in history for d in dates)
    baseline = sum(history[d] for d in dates) / len(dates) if enough else None

    def opening(minutes):
        end = start + timedelta(minutes=minutes)
        bars = [b for b in o.bars if instant(b.end_utc) <= end]
        cursor = start
        for b in bars:
            if instant(b.start_utc) != cursor:
                return None, None
            cursor = instant(b.end_utc)
        if at < end or cursor != end or not bars:
            return None, None
        return max(b.high for b in bars), min(b.low for b in bars)

    def trailing(minutes):
        target = at - timedelta(minutes=minutes)
        base = next((b.close for b in o.bars if instant(b.end_utc) == target), None)
        return pct(o.price, base)

    one, five = opening(1), opening(5)
    return dict(timestamp_utc=utc(at), timestamp_et=at.astimezone(ET).isoformat(),
                session_date=o.session.date, ticker=o.ticker, price=o.price,
                day_change_pct=pct(o.price, o.previous_close), volume=volume,
                rvol=volume / baseline if volume is not None and baseline else None,
                return_1m=trailing(1), return_5m=trailing(5), vwap=vwap,
                price_vs_vwap_pct=pct(o.price, vwap),
                above_vwap=None if vwap is None else int(o.price > vwap),
                opening_1m_high=one[0], opening_1m_low=one[1],
                opening_5m_high=five[0], opening_5m_low=five[1], hod=hod,
                hod_distance_pct=pct(o.price, hod), current_leader_rank=None,
                emerging_rank=None, test_version=config.test_version,
                session_type=config.session, coverage_complete=int(complete), in_universe=int(o.in_universe))


def rank(rows: list[dict]) -> list[dict]:
    for field, target in (("day_change_pct", "current_leader_rank"), ("return_5m", "emerging_rank")):
        for row in rows:
            row[target] = None
        available = [r for r in rows if r[field] is not None and r["in_universe"]]
        for index, row in enumerate(sorted(available, key=lambda r: (-r[field], r["ticker"])), 1):
            row[target] = index
    return rows
