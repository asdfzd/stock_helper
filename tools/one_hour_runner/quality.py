"""Read-only first-hour shutdown gate; not a strategy or full-session validator.

The persisted batch envelope proves acknowledged WS state, not exchange delivery
between samples. Historical OHLCV coverage is measured independently of that WS
evidence. Only complete minutes starting at/after discovery are charged to a
candidate; missing pre-discovery history remains visible but is not a failure.
"""
import csv
import json
import math

from market_radar.models import instant, utc


POLICY = dict(version=2, slot_seconds=30, minimum_slot_coverage=.95,
              maximum_evidence_gap_seconds=91, maximum_price_age_seconds=90,
              minimum_total_post_detection_ohlcv_coverage=.95,
              minimum_candidate_post_detection_ohlcv_coverage=.90,
              maximum_consecutive_missing_minutes=2)


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _true(value):
    return value is True or value == "True" or value == "1"


def _timeline(points, start=0):
    step = POLICY["slot_seconds"]
    first = math.ceil(start/step)
    expected = set(range(first, 3600//step))
    points = sorted({p for p in points if first*step <= p < 3600})
    occupied = {int(p//step) for p in points} & expected
    edges = [first*step, *points, 3600]
    gap = max((b-a for a, b in zip(edges, edges[1:])), default=0)
    return dict(expected_slots=len(expected), observed_slots=len(occupied),
                coverage=len(occupied)/len(expected) if expected else None,
                missing_slot_offsets_seconds=[i*step for i in sorted(expected-occupied)],
                maximum_gap_seconds=gap,
                first_offset_seconds=points[0] if points else None,
                last_offset_seconds=points[-1] if points else None)


def assess_collection(db, candidates_path, bars_path, opened):
    candidates = _rows(candidates_path)
    bars = {}
    for row in _rows(bars_path):
        bars.setdefault(row["ticker"], {})[instant(row["bar_start_utc"])] = row
    eligible = [c for c in candidates if _true(c.get("analysis_eligible"))]
    first_demand = min(((instant(c["candidate_first_detected_at"])-opened).total_seconds()
                        for c in eligible), default=3600)
    points = {k: [] for k in ("batch", "ws_connected", "ws_subscribed", "fresh_observation")}
    telemetry = dict(disconnected_batches=0, unknown_connection_batches=0,
                     unknown_subscription_batches=0, subscription_count_mismatch_batches=0,
                     raw_observations=0, fresh_subscribed_observations=0,
                     observations_without_fresh_receipt=0)
    symbols, slot_evidence = {}, {}
    for stamp, raw_json in db.execute("SELECT timestamp_utc,raw_json FROM radar_batches ORDER BY timestamp_utc"):
        at = instant(stamp)
        seconds = (at-opened).total_seconds()
        if not 0 <= seconds < 3600:
            continue
        raw = json.loads(raw_json)
        meta = raw.get("metadata", {})
        points["batch"].append(seconds)
        connected = meta.get("ws_connected") is True
        if connected:
            points["ws_connected"].append(seconds)
        elif meta.get("ws_connected") is False:
            telemetry["disconnected_batches"] += 1
        else:
            telemetry["unknown_connection_batches"] += 1
        active = meta.get("active")
        if not isinstance(active, dict):
            telemetry["unknown_subscription_batches"] += 1
            active = {}
        count = meta.get("tracking_counts", {}).get("active_subscription_count")
        if count is not None and count != len(active):
            telemetry["subscription_count_mismatch_batches"] += 1
        slot = int(seconds//POLICY["slot_seconds"])
        item = dict(known=isinstance(meta.get("active"), dict) and isinstance(meta.get("ws_connected"), bool),
                    connected=connected, active=set(active),
                    missing=meta.get("missing_reasons", {}), fresh=set())
        slot_evidence.setdefault(slot, []).append(item)
        if connected and active:
            points["ws_subscribed"].append(seconds)
            for ticker in active:
                s = symbols.setdefault(ticker, dict(subscribed=[], observed=[]))
                s["subscribed"].append(seconds)
        fresh = False
        for observation in raw.get("observations", []):
            telemetry["raw_observations"] += 1
            om = observation.get("metadata", {})
            try:
                age = (at-instant(om["price_received_utc"])).total_seconds()
                price = observation["price"]
                valid = (math.isfinite(price) and price > 0 and
                         0 <= age <= POLICY["maximum_price_age_seconds"] and
                         om.get("ws_gap_detected") is False)
            except (KeyError, TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                telemetry["observations_without_fresh_receipt"] += 1
            ticker = observation["ticker"]
            if valid and connected and ticker in active and meta.get("ws_gap_detected") is False:
                item["fresh"].add(ticker)
                telemetry["fresh_subscribed_observations"] += 1
                symbols.setdefault(ticker, dict(subscribed=[], observed=[]))["observed"].append(seconds)
                fresh = True
        if fresh:
            points["fresh_observation"].append(seconds)
    timelines = {k: _timeline(v, max(0, first_demand) if k in {"ws_subscribed", "fresh_observation"} else 0)
                 for k, v in points.items()}
    failures = []
    for name, value in timelines.items():
        if value["coverage"] is None or value["coverage"] < POLICY["minimum_slot_coverage"]:
            failures.append("first_hour_"+name+"_coverage")
        if value["maximum_gap_seconds"] > POLICY["maximum_evidence_gap_seconds"]:
            failures.append("first_hour_"+name+"_gap")
    if telemetry["subscription_count_mismatch_batches"]:
        failures.append("first_hour_subscription_evidence_mismatch")

    per_candidate = []
    total_expected = total_complete = subscribed_expected = subscribed_complete = 0
    totals = dict(fully_subscribed_minutes=0, partially_subscribed_minutes=0,
                  not_subscribed_minutes=0, explicit_slot_unavailable_minutes=0,
                  unknown_subscription_minutes=0, disconnected_minutes=0,
                  cache_missing_subscribed_minutes=0, cache_missing_unsubscribed_minutes=0)
    for candidate in candidates:
        ticker = candidate["ticker"]
        detected = instant(candidate["candidate_first_detected_at"])
        first_minute = max(0, math.ceil((detected-opened).total_seconds()/60))
        expected_minutes = list(range(first_minute, 60))
        missing, observed, complete, consecutive, max_missing = [], 0, 0, 0, 0
        subscribed_minutes, subscribed_missing = [], []
        categories = {k: 0 for k in totals}
        source = bars.get(ticker, {})
        by_minute = {int((at-opened).total_seconds()/60): b for at, b in source.items()}
        for minute in expected_minutes:
            bar = by_minute.get(minute, {})
            # Two acknowledged 30-second samples bound the evidence for a minute.
            # This is NOT proof of continuous subscription between those samples.
            samples = [sample for slot in (minute*2, minute*2+1) for sample in slot_evidence.get(slot, [])]
            known = all(slot in slot_evidence for slot in (minute*2, minute*2+1)) and all(s["known"] for s in samples)
            if not known:
                categories["unknown_subscription_minutes"] += 1
                subscription = "unknown"
            elif not all(s["connected"] for s in samples):
                categories["disconnected_minutes"] += 1
                subscription = "disconnected"
            elif all(ticker in s["active"] for s in samples):
                categories["fully_subscribed_minutes"] += 1
                subscribed_minutes.append(minute)
                subscription = "full"
            elif any(ticker in s["active"] for s in samples):
                categories["partially_subscribed_minutes"] += 1
                subscription = "partial"
            else:
                categories["not_subscribed_minutes"] += 1
                subscription = "none"
                # Never infer capacity loss merely from candidate count > slots.
                if any(s["missing"].get(ticker) in {"tracking_slot_unavailable", "slot_unavailable"} for s in samples):
                    categories["explicit_slot_unavailable_minutes"] += 1
            observed += bool(bar) and not _true(bar.get("missing_bar", True))
            if _true(bar.get("aggregate_complete")):
                complete += 1
                consecutive = 0
            else:
                missing.append(minute)
                consecutive += 1
                max_missing = max(max_missing, consecutive)
            if not _true(bar.get("aggregate_complete")):
                if subscription == "full":
                    subscribed_missing.append(minute)
                    categories["cache_missing_subscribed_minutes"] += 1
                elif subscription in {"none", "partial"}:
                    categories["cache_missing_unsubscribed_minutes"] += 1
        rate = complete/len(expected_minutes) if expected_minutes else None
        is_eligible = _true(candidate.get("analysis_eligible"))
        gate = is_eligible and bool(expected_minutes)
        candidate_failures = []
        if gate:
            total_expected += len(expected_minutes)
            total_complete += complete
            for key, count in categories.items():
                totals[key] += count
        subscribed_gate = gate and bool(subscribed_minutes)
        subscribed_rate = (len(subscribed_minutes)-len(subscribed_missing))/len(subscribed_minutes) if subscribed_minutes else None
        # Consecutive missing cache minutes are charged only within confirmed
        # subscription windows. Unsubscribed periods break this run of gaps.
        consecutive = max_subscribed_missing = 0
        for minute in expected_minutes:
            consecutive = consecutive+1 if minute in subscribed_missing else 0
            max_subscribed_missing = max(max_subscribed_missing, consecutive)
        if subscribed_gate:
            subscribed_expected += len(subscribed_minutes)
            subscribed_complete += len(subscribed_minutes)-len(subscribed_missing)
            if subscribed_rate < POLICY["minimum_candidate_post_detection_ohlcv_coverage"]:
                candidate_failures.append("post_detection_ohlcv_coverage")
            if max_subscribed_missing > POLICY["maximum_consecutive_missing_minutes"]:
                candidate_failures.append("post_detection_ohlcv_gap")
        if candidate_failures and "first_hour_candidate_coverage" not in failures:
            failures.append("first_hour_candidate_coverage")
        evidence = symbols.get(ticker, dict(subscribed=[], observed=[]))
        # Check individual acknowledged subscription samples, so one healthy
        # symbol cannot mask another symbol's missing/future/stale receipt data.
        # Zero trades do not require ticks in every minute; fresh carried prices
        # remain valid up to the existing 90-second age threshold.
        subscribed_slots = sorted({int(p//30) for p in evidence["subscribed"] if p >= max(0, (detected-opened).total_seconds())})
        observed_slots = {int(p//30) for p in evidence["observed"]}
        missing_slots = [s for s in subscribed_slots if s not in observed_slots]
        run = maximum_missing_slots = 0
        previous = None
        for slot in subscribed_slots:
            run = (run+1 if previous == slot-1 else 1) if slot in missing_slots else 0
            maximum_missing_slots = max(maximum_missing_slots, run)
            previous = slot
        observed_rate = (len(subscribed_slots)-len(missing_slots))/len(subscribed_slots) if subscribed_slots else None
        windows = []
        for slot in subscribed_slots:
            if not windows or slot != windows[-1][-1]+1:
                windows.append([])
            windows[-1].append(slot)
        observation_gap = 0
        for window in windows:
            points_in_window = sorted({p for p in evidence["observed"] if int(p//30) in window
                                      and p >= max(0, (detected-opened).total_seconds())})
            edges = [window[0]*30, *points_in_window, (window[-1]+1)*30]
            observation_gap = max(observation_gap, max(b-a for a, b in zip(edges, edges[1:])))
        if gate and subscribed_slots and (observed_rate < POLICY["minimum_slot_coverage"] or observation_gap > POLICY["maximum_evidence_gap_seconds"]):
            candidate_failures.append("subscribed_fresh_observation_insufficient")
            if "first_hour_subscribed_observation_coverage" not in failures:
                failures.append("first_hour_subscribed_observation_coverage")
        per_candidate.append(dict(ticker=ticker, candidate_first_detected_at=utc(detected),
            analysis_eligible=is_eligible, gate_population=gate,
            exemption_reason=None if gate else "excluded_symbol_type" if not is_eligible else "no_full_post_detection_minute",
            expected_post_detection_minutes=len(expected_minutes), observed_post_detection_minutes=observed,
            complete_post_detection_minutes=complete, complete_post_detection_coverage=rate,
            missing_post_detection_minute_offsets=missing, maximum_consecutive_missing_minutes=max_missing,
            pre_detection_minutes_exempt=min(first_minute, 60),
            full_hour_observed_minutes=sum(not _true(b.get("missing_bar", True)) for b in source.values()),
            full_hour_complete_minutes=sum(_true(b.get("aggregate_complete")) for b in source.values()),
            subscription_scope=categories, subscribed_gate_population=subscribed_gate,
            subscribed_minutes=subscribed_minutes, missing_subscribed_minute_offsets=subscribed_missing,
            subscribed_ohlcv_coverage=subscribed_rate,
            maximum_consecutive_subscribed_missing_minutes=max_subscribed_missing,
            subscribed_observation_coverage=observed_rate, missing_subscribed_observation_slots=missing_slots,
            maximum_subscribed_observation_gap_seconds=observation_gap,
            subscription_evidence=_timeline(evidence["subscribed"], max(0, (detected-opened).total_seconds())),
            observation_evidence=_timeline(evidence["observed"], max(0, (detected-opened).total_seconds())),
            failures=candidate_failures))
    rate = total_complete/total_expected if total_expected else None
    subscribed_rate = subscribed_complete/subscribed_expected if subscribed_expected else None
    if subscribed_rate is None or subscribed_rate < POLICY["minimum_total_post_detection_ohlcv_coverage"]:
        failures.append("first_hour_post_detection_ohlcv_coverage")
    return dict(policy=dict(POLICY), status="FAIL" if failures else "PASS", failures=failures,
        evidence_scope="persisted 30-second samples; no claim of continuous WS delivery or profitability",
        ws_evidence=telemetry, timelines=timelines,
        opening_coverage=dict(candidate_count=len(candidates), eligible_candidate_count=len(eligible),
            expected_post_detection_minutes=total_expected, complete_post_detection_minutes=total_complete,
            complete_post_detection_coverage=rate,
            scope_status="PARTIAL" if rate is None or rate < 1 else "COMPLETE",
            scope_only_not_shutdown_gate=True,
            subscribed_expected_minutes=subscribed_expected, subscribed_complete_minutes=subscribed_complete,
            subscribed_ohlcv_coverage=subscribed_rate, subscription_scope=totals,
            interpretation="Cache OHLCV availability is independent of live WS delivery; missing cache rows do not prove lost ticks",
            candidates=per_candidate))
