"""Pure descriptive calculations on completed minutes. No provider or Paper imports.

Times of extrema/breaks are minute intervals, never claimed as exact tick times.
Pullback/rebound compare a prior completed bar's running extreme with a later bar.
An incomplete or unaligned detection window never yields an exact excursion.
"""
from collections import Counter
from datetime import timedelta
import math
from statistics import median

from ..models import ET, instant, utc
from . import FEATURE_VERSION, SCHEMA_VERSION, SNAPSHOT_MINUTES, DETECTION_MINUTES
from .source import digest

PRICE_FIELDS = ("open", "high", "low", "close", "volume")
STRUCTURE_FIELDS = ("higher_high", "higher_low", "lower_high", "lower_low")


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pct(value, base):
    return (value/base-1)*100 if finite(value) and finite(base) and base > 0 else None


def fraction(value, base):
    return value/base*100 if finite(value) and finite(base) and base > 0 else None


def price_complete(row):
    h, l, c = (row.get(k) for k in ("high", "low", "close"))
    return all(finite(v) for v in (h, l, c)) and 0 < l <= c <= h


def valid_ohlcv(row):
    return (price_complete(row) and finite(row.get("open")) and row["low"] <= row["open"] <= row["high"]
            and finite(row.get("volume")) and row["volume"] >= 0)


def shape(row):
    o, h, l, c = (row.get(k) for k in ("open", "high", "low", "close"))
    valid = price_complete(row)
    span = h-l if valid else None
    body = abs(c-o) if valid and finite(o) and o > 0 else None
    upper = h-max(o, c) if body is not None else None
    lower = min(o, c)-l if body is not None else None
    return dict(range_abs=span, range_pct=fraction(span, o), body_abs=body,
        body_pct_of_range=fraction(body, span), upper_wick_abs=upper, lower_wick_abs=lower,
        upper_wick_pct_of_range=fraction(upper, span), lower_wick_pct_of_range=fraction(lower, span),
        close_location_value=(c-l)/span if finite(span) and span > 0 else None,
        direction=("UP" if c > o else "DOWN" if c < o else "FLAT") if body is not None else None,
        return_pct=pct(c, o), minute_notional=None)


def relation(row, prior):
    valid = bool(prior and price_complete(row) and price_complete(prior))
    result = {key + "_vs_prev": None for key in STRUCTURE_FIELDS}
    for name, field, greater in (("higher_high", "high", True), ("higher_low", "low", True),
                                 ("lower_high", "high", False), ("lower_low", "low", False)):
        if valid:
            result[name + "_vs_prev"] = row[field] > prior[field] if greater else row[field] < prior[field]
    for field in ("high", "low", "close", "volume", "range_abs"):
        old, new = (prior or {}).get(field), row.get(field)
        result[field + "_change_vs_prev"] = new-old if finite(new) and finite(old) else None
        result[field + "_change_vs_prev_pct"] = pct(new, old)
        result[field + "_increased"] = new > old if finite(new) and finite(old) else None
    result["range_decreased"] = row["range_abs"] < prior["range_abs"] if prior and finite(row["range_abs"]) and finite(prior["range_abs"]) else None
    return result


def normalize_minutes(candidate, start):
    by_time = {b["bar_start_utc"]: b for b in candidate["bars"]}
    rows, cumulative = [], 0.0
    detected = instant(candidate["candidate_first_detected_at"])
    for minute in range(60):
        at = start+timedelta(minutes=minute)
        source = by_time.get(utc(at), {})
        row = dict(source, **{k: source.get(k) for k in PRICE_FIELDS})
        hlc = price_complete(row)
        volume_valid = finite(row["volume"]) and row["volume"] >= 0
        if not hlc:
            row.update(dict.fromkeys(PRICE_FIELDS))
        elif not valid_ohlcv(row):
            if not finite(row["open"]) or not row["low"] <= row["open"] <= row["high"]:
                row["open"] = None
            if not volume_valid:
                row["volume"] = None
        complete = valid_ohlcv(row)
        quality = "complete" if complete else "missing_minute" if not source else "invalid_ohlcv" if not hlc else "missing_open_or_volume"
        row.update(session_date_et=candidate["session_date_et"], ticker=candidate["ticker"], timeframe="1m",
            bar_start_utc=utc(at), bar_end_utc=utc(at+timedelta(minutes=1)), bar_start_et=at.astimezone(ET).isoformat(),
            minute_from_open=minute, aggregate_complete=complete, missing_bar=not hlc,
            data_quality_status=quality, pre_detection=at < detected)
        row.update(shape(row))
        prior = rows[-1] if rows else None
        row.update(relation(row, prior))
        cumulative = cumulative+row["volume"] if cumulative is not None and finite(row["volume"]) else None
        regular_open = row["open"] if minute == 0 else rows[0]["open"]
        row.update(previous_close_return_pct=pct(row["close"], candidate.get("previous_close")),
            market_open_return_pct=pct(row["close"], regular_open),
            previous_bar_return_pct=pct(row["close"], prior["close"] if prior else None),
            cumulative_volume_from_open=cumulative,
            opening_low_distance_pct=pct(row["close"], rows[0]["low"] if rows else row["low"]),
            opening_high_distance_pct=pct(row["close"], rows[0]["high"] if rows else row["high"]))
        rows.append(row)
    _running_flags(rows)
    return rows


def _running_flags(rows):
    high = low = None
    complete = True
    for r in rows:
        complete = complete and price_complete(r)
        r["new_hod"] = r["high"] > high if complete and high is not None else None
        r["new_lod"] = r["low"] < low if complete and low is not None else None
        if price_complete(r):
            high = max(high, r["high"]) if high is not None else r["high"]
            low = min(low, r["low"]) if low is not None else r["low"]


def aggregate(minutes, size):
    rows, cumulative = [], 0.0
    for offset in range(0, 60, size):
        part = minutes[offset:offset+size]
        complete = len(part) == size and all(r["aggregate_complete"] for r in part)
        row = dict.fromkeys(PRICE_FIELDS)
        if complete:
            row.update(open=part[0]["open"], close=part[-1]["close"], high=max(r["high"] for r in part),
                       low=min(r["low"] for r in part), volume=sum(r["volume"] for r in part))
        row.update(session_date_et=part[0]["session_date_et"], ticker=part[0]["ticker"], timeframe=f"{size}m",
            bar_start_utc=part[0]["bar_start_utc"], bar_start_et=part[0]["bar_start_et"], bar_end_utc=part[-1]["bar_end_utc"],
            minute_from_open=offset, aggregate_complete=complete, pre_detection=part[0]["pre_detection"],
            data_quality_status="complete" if complete else "incomplete_constituents")
        row.update(shape(row))
        row.update(relation(row, rows[-1] if rows else None))
        cumulative = cumulative+row["volume"] if cumulative is not None and complete else None
        row.update(cumulative_volume_from_open=cumulative,
            **{f"volume_change_vs_prev_{size}m": row["volume_change_vs_prev_pct"],
               f"range_change_vs_prev_{size}m": row["range_abs_change_vs_prev_pct"]})
        rows.append(row)
    _running_flags(rows)
    return rows


def structure(rows):
    result = {key + "_count": sum(r.get(key + "_vs_prev") is True for r in rows) for key in STRUCTURE_FIELDS}
    result.update(green_bar_count=sum(r.get("direction") == "UP" for r in rows),
        red_bar_count=sum(r.get("direction") == "DOWN" for r in rows), flat_bar_count=sum(r.get("direction") == "FLAT" for r in rows),
        range_expansion_count=sum(r.get("range_abs_increased") is True for r in rows),
        range_contraction_count=sum(r.get("range_decreased") is True for r in rows))
    for direction, key in (("UP", "max_consecutive_up_bars"), ("DOWN", "max_consecutive_down_bars")):
        maximum = streak = 0
        for r in rows:
            streak = streak+1 if r.get("direction") == direction else 0
            maximum = max(maximum, streak)
        result[key] = maximum
    volumes = [r for r in rows if finite(r.get("volume"))]
    maximum = max(volumes, key=lambda r: r["volume"]) if volumes else None
    result.update(maximum_volume_bar=maximum["volume"] if maximum else None,
        maximum_volume_bar_timestamp=maximum["bar_start_utc"] if maximum else None,
        volume_path_complete=len(volumes) == len(rows), maximum_volume_basis="observed bars only",
        structure_basis="observed adjacent bars; missing bars break streaks and comparisons")
    return result


def path_stats(rows):
    valid = [r for r in rows if price_complete(r)]
    full = bool(rows) and len(valid) == len(rows)
    high = low = None
    hi_at = lo_at = None
    hcount = lcount = 0
    pullback = rebound = 0.0
    for r in valid:
        if high is not None:
            pullback = min(pullback, pct(r["low"], high))
            rebound = max(rebound, pct(r["high"], low))
        if high is None or r["high"] > high:
            hcount += high is not None
            high, hi_at = r["high"], r["bar_start_utc"]
        if low is None or r["low"] < low:
            lcount += low is not None
            low, lo_at = r["low"], r["bar_start_utc"]
    return dict(path_complete=full, observed_high_so_far=high, observed_low_so_far=low,
        high_so_far=high if full else None, low_so_far=low if full else None,
        hod_updates=hcount if full else None, lod_updates=lcount if full else None,
        observed_hod_updates=hcount, observed_lod_updates=lcount,
        first_hod_time=valid[0]["bar_start_utc"] if full else None, first_lod_time=valid[0]["bar_start_utc"] if full else None,
        time_of_high=hi_at if full else None, time_of_low=lo_at if full else None,
        max_pullback_from_hod_pct=pullback if full else None, max_rebound_from_lod_pct=rebound if full else None,
        excursion_path_basis="prior completed bar extreme to subsequent bar extreme; intrabar order unknown",
        extrema_time_precision="1m_interval_start; extreme occurred within [start,start+1m)")


def anchors(minutes, previous_close):
    result = dict(regular_open_price=minutes[0]["open"], previous_close=previous_close,
                  gap_from_previous_close_pct=pct(minutes[0]["open"], previous_close), opening_hour_close=minutes[-1]["close"])
    for size, label in ((1, "first_1m"), (3, "first_3m"), (5, "first_5m"), (10, "opening_10m"), (15, "opening_15m"), (60, "opening_hour")):
        part = minutes[:size]
        full = all(price_complete(r) for r in part)
        result[label+"_high"] = max(r["high"] for r in part) if full else None
        result[label+"_low"] = min(r["low"] for r in part) if full else None
    return result


def events_for(minutes, anchor):
    events = []
    for r in minutes:
        for label, field in (("HOD_UPDATE", "new_hod"), ("LOD_UPDATE", "new_lod")):
            if r[field] is True:
                events.append(dict(event_type=label, anchor_minutes=None, event_at=r["bar_start_utc"],
                    interval_start_utc=r["bar_start_utc"], interval_end_utc=r["bar_end_utc"],
                    timestamp_precision="1m_interval", price=r["high"] if field == "new_hod" else r["low"]))
    for size, label in ((1, "first_1m"), (3, "first_3m"), (5, "first_5m"), (10, "opening_10m"), (15, "opening_15m")):
        high, low = anchor[label+"_high"], anchor[label+"_low"]
        if high is None or low is None:
            continue
        for direction, field, level in (("UP", "high", high), ("DOWN", "low", low)):
            hit = next((r for r in minutes[size:] if price_complete(r) and (r[field] > level if direction == "UP" else r[field] < level)), None)
            if hit:
                index = hit["minute_from_open"]
                returned = next((r for r in minutes[index:] if finite(r["close"]) and low <= r["close"] <= high), None)
                events.append(dict(event_type="OPENING_RANGE_BREAK", anchor_minutes=size, break_direction=direction,
                    event_at=hit["bar_start_utc"], interval_start_utc=hit["bar_start_utc"], interval_end_utc=hit["bar_end_utc"],
                    timestamp_precision="1m_interval", confirmed_at=hit["bar_end_utc"], threshold_price=level, price=hit[field],
                    first_observed=True, first_time_certain=all(price_complete(r) for r in minutes[size:index+1]),
                    returned_inside=bool(returned), returned_inside_at=returned["bar_end_utc"] if returned else None,
                    return_observation_basis="first observed bar close in range, including breakout bar close"))
    return sorted(events, key=lambda e: (e["event_at"], e["event_type"], e.get("anchor_minutes") or 0, e.get("break_direction", "")))


def detection_window(minutes, detected, target, price, end):
    result = dict(status="complete", mfe_pct=None, mae_pct=None, close=None, return_pct=None,
                  observed_bar_mfe_pct=None, observed_bar_mae_pct=None)
    if target > end:
        result["status"] = "outside_opening_hour"
        return result
    if not finite(price) or price <= 0:
        result["status"] = "missing_detection_price"
        return result
    part = [r for r in minutes if instant(r["bar_start_utc"]) >= detected and instant(r["bar_end_utc"]) <= target]
    usable = [r for r in part if price_complete(r)]
    if usable:
        result.update(observed_bar_mfe_pct=max(0.0, pct(max(r["high"] for r in usable), price)),
                      observed_bar_mae_pct=min(0.0, pct(min(r["low"] for r in usable), price)))
    result.update(observed_complete_bar_count=len(usable), expected_window_seconds=(target-detected).total_seconds())
    endpoint = next((r for r in minutes if instant(r["bar_end_utc"]) == target), None)
    if endpoint:
        result.update(close=endpoint["close"], return_pct=pct(endpoint["close"], price))
    if detected.second or detected.microsecond or target.second or target.microsecond:
        result["status"] = "unaligned_detection_window"
    elif not part or len(usable) != len(part):
        result["status"] = "incomplete_minutes"
    else:
        result.update(mfe_pct=result["observed_bar_mfe_pct"], mae_pct=result["observed_bar_mae_pct"])
    return result


def analyze_candidate(candidate, start):
    minutes = normalize_minutes(candidate, start)
    end = start+timedelta(minutes=60)
    three, five = aggregate(minutes, 3), aggregate(minutes, 5)
    anchor = anchors(minutes, candidate.get("previous_close"))
    events = events_for(minutes, anchor)
    snapshots = []
    detected = instant(candidate["candidate_first_detected_at"])
    for count in SNAPSHOT_MINUTES:
        part, at = minutes[:count], start+timedelta(minutes=count)
        stats = path_stats(part)
        known = detected <= at
        sources = set(candidate["candidate_sources"]) if known and candidate.get("candidate_sources") is not None else None
        if known:
            for history in candidate.get("source_history", []):
                if instant(history["at"]) <= at and history.get("sources") is not None:
                    if sources is None:
                        sources = set()
                    sources.update(history["sources"])
        snap = dict(kind="market_open", horizon_minutes=count, target_utc=utc(at), price=part[-1]["close"],
            return_from_open_pct=pct(part[-1]["close"], anchor["regular_open_price"]),
            return_from_previous_close_pct=pct(part[-1]["close"], anchor["previous_close"]),
            mfe_from_open_so_far=pct(stats["high_so_far"], anchor["regular_open_price"]),
            mae_from_open_so_far=pct(stats["low_so_far"], anchor["regular_open_price"]),
            cumulative_volume=part[-1]["cumulative_volume_from_open"], candidate_detected_yet=known,
            candidate_sources=sorted(sources) if sources is not None else None,
            **stats, **structure(part))
        for size in (3, 5):
            for side, direction in (("high", "UP"), ("low", "DOWN")):
                key = f"first_{size}m_{side}_broken"
                hits = [e for e in events if e.get("anchor_minutes") == size and e.get("break_direction") == direction and instant(e["interval_end_utc"]) <= at]
                snap[key] = True if hits else False if count >= size and stats["path_complete"] and anchor[f"first_{size}m_{side}"] is not None else None
        snapshots.append(snap)
    for count in DETECTION_MINUTES:
        target = detected+timedelta(minutes=count)
        snapshots.append(dict(kind="candidate_detection", horizon_minutes=count, target_utc=utc(target),
            **detection_window(minutes, detected, target, candidate.get("candidate_first_detected_price"), end)))
    whole = path_stats(minutes)
    detection = detection_window(minutes, detected, end, candidate.get("candidate_first_detected_price"), end)
    outcome = dict(anchor, **whole, **structure(minutes), return_open_to_10_30=pct(minutes[-1]["close"], anchor["regular_open_price"]),
        return_detection_to_10_30=pct(minutes[-1]["close"], candidate.get("candidate_first_detected_price")),
        opening_hour_mfe_from_open=pct(whole["high_so_far"], anchor["regular_open_price"]),
        opening_hour_mae_from_open=pct(whole["low_so_far"], anchor["regular_open_price"]),
        opening_hour_mfe_from_detection=detection["mfe_pct"], opening_hour_mae_from_detection=detection["mae_pct"],
        detection_excursion_status=detection["status"], detection_observed_bar_mfe_pct=detection["observed_bar_mfe_pct"],
        detection_observed_bar_mae_pct=detection["observed_bar_mae_pct"],
        hod_update_count=whole["hod_updates"], lod_update_count=whole["lod_updates"],
        final_opening_hour_hod_time=whole["time_of_high"], final_opening_hour_lod_time=whole["time_of_low"],
        full_60m=all(r["aggregate_complete"] for r in minutes),
        structure_3m=structure(three), structure_5m=structure(five))
    for field, time_field in (("time_to_opening_hour_high_seconds", "time_of_high"), ("time_to_opening_hour_low_seconds", "time_of_low")):
        outcome[field] = (instant(whole[time_field])-start).total_seconds() if whole[time_field] else None
    for size, label in ((1, "first_1m"), (3, "first_3m"), (5, "first_5m"), (10, "opening_10m"), (15, "opening_15m")):
        for side, direction in (("high", "UP"), ("low", "DOWN")):
            hit = next((e for e in events if e.get("anchor_minutes") == size and e.get("break_direction") == direction), None)
            outcome[label+"_"+side+"_first_break_at"] = hit["event_at"] if hit and hit["first_time_certain"] else None
            outcome[label+"_"+side+"_first_observed_break_at"] = hit["event_at"] if hit else None
    segments = []
    for a, b in ((0, 3), (0, 5), (0, 10), (0, 15), (15, 30), (30, 45), (45, 60)):
        part = minutes[a:b]
        complete = all(r["aggregate_complete"] for r in part)
        segments.append(dict(start_minute=a, end_minute=b, aggregate_complete=complete,
            volume_sum=sum(r["volume"] for r in part) if complete else None,
            price_high=max(r["high"] for r in part) if complete else None,
            price_low=min(r["low"] for r in part) if complete else None,
            price_range_pct=fraction(max(r["high"] for r in part)-min(r["low"] for r in part), part[0]["open"]) if complete else None,
            open_to_end_return_pct=pct(part[-1]["close"], part[0]["open"]) if complete else None))
    return dict(candidate={k: v for k, v in candidate.items() if k != "bars"},
                bars=minutes+three+five, events=events, snapshots=snapshots, outcome=outcome, segments=segments)


def analyze(evidence):
    start = instant(evidence["regular_open_utc"])
    analyzed = [analyze_candidate(dict(c, session_date_et=evidence["session_date_et"]), start) for c in evidence["candidates"]]
    all_minutes = [b for c in analyzed for b in c["bars"] if b["timeframe"] == "1m"]
    count = len(analyzed)
    full = sum(c["outcome"]["full_60m"] for c in analyzed)
    quality = dict(candidate_count=count, candidates_with_full_60m=full, candidates_incomplete=count-full,
        expected_1m_rows=count*60, actual_1m_rows=sum(not b["missing_bar"] for b in all_minutes),
        stored_1m_rows=len(all_minutes), missing_minute_count=sum(b["missing_bar"] for b in all_minutes),
        incomplete_ohlcv_minute_count=sum(not b["aggregate_complete"] for b in all_minutes),
        full_1m_coverage_rate=full/count if count else None,
        observed_1m_row_rate=sum(not b["missing_bar"] for b in all_minutes)/(60*count) if count else None,
        complete_ohlcv_1m_rate=sum(b["aggregate_complete"] for b in all_minutes)/(60*count) if count else None,
        candidate_detection_price_available_rate=sum(finite(c["candidate"].get("candidate_first_detected_price")) for c in analyzed)/count if count else None,
        opening_hour_outcome_available_rate=sum(c["outcome"]["return_open_to_10_30"] is not None for c in analyzed)/count if count else None,
        rates_unit="fraction_0_to_1", status="COMPLETE" if count and full == count else "INCOMPLETE",
        warnings=evidence.get("warnings", []))
    for timeframe, expected in (("3m", 20), ("5m", 12)):
        complete = sum(b["aggregate_complete"] for c in analyzed for b in c["bars"] if b["timeframe"] == timeframe)
        quality["complete_"+timeframe+"_rate"] = complete/(expected*count) if count else None
    returns = [c["outcome"]["return_open_to_10_30"] for c in analyzed if c["outcome"]["return_open_to_10_30"] is not None]
    summary = dict(candidate_count=count, coverage=quality, positive_outcome_count=sum(r > 0 for r in returns),
        negative_outcome_count=sum(r < 0 for r in returns), flat_outcome_count=sum(r == 0 for r in returns),
        source_sample_counts=dict(sorted(Counter(s for c in analyzed for s in c["candidate"].get("candidate_sources") or []).items())),
        interpretation="data collection / descriptive research; no signal, prediction, profitability or pattern selection",
        new_rest_calls=0, new_ws_subscriptions=0, actual_nh_order_calls=0)
    for field, label in (("return_open_to_10_30", "median_open_to_60m_return"),
                         ("opening_hour_mfe_from_open", "median_60m_mfe"), ("opening_hour_mae_from_open", "median_60m_mae")):
        values = [c["outcome"][field] for c in analyzed if c["outcome"][field] is not None]
        summary[label] = median(values) if values else None
        summary[label+"_sample_count"] = len(values)
    metadata = {k: v for k, v in evidence.items() if k != "candidates"}
    metadata.update(feature_version=FEATURE_VERSION, opening_hour_schema_version=SCHEMA_VERSION,
        notional_policy="NULL; pinned usa06011 has no minute notional field", timestamp_policy="1m interval, no exact intrabar times",
        research_only=True)
    research_id = digest(dict(feature_version=FEATURE_VERSION, source_fingerprint=evidence["source_fingerprint"]))[:24]
    metadata["research_id"] = research_id
    return dict(metadata=metadata, candidates=analyzed, quality=quality, summary=summary)
