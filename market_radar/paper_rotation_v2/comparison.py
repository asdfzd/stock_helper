"""Descriptive comparisons computed only on stored observations, after decisions."""
from . import STRATEGY_VERSION


def comparisons(trades, posts, switches, marks=()):
    baseline = [t for t in trades if t["strategy_version"] == "paper_v1_baseline"]
    rotation = {t["trade_id"]: t for t in trades if t["strategy_version"] == STRATEGY_VERSION}
    post = {t["trade_id"]: t for t in posts if t["strategy_version"] == STRATEGY_VERSION}
    results = []
    # Each root has one comparison; a rotation chain compounds only its linked legs.
    for root in rotation.values():
        if root.get("rotation_root_trade_id") != root["trade_id"]:
            continue
        match = next((t for t in baseline if t["radar_run_id"] == root["radar_run_id"]
            and t["ticker"] == root["ticker"] and t["entry_signal_at"] == root["entry_signal_at"]
            and t["entry_price_at"] == root["entry_price_at"] and t["entry_price"] == root["entry_price"]), None)
        legs = sorted((t for t in rotation.values() if t.get("rotation_root_trade_id") == root["trade_id"]),
                      key=lambda t: (t["entry_price_at"], t["trade_id"]))
        linked_switches = [s for s in switches if s["rotation_root_trade_id"] == root["trade_id"]]
        complete = all(t.get("return_pct") is not None and t["data_quality_status"] == "valid" for t in legs)
        complete = complete and all(s["stage"] == "COMPLETE" for s in linked_switches)
        capital = 1.0
        for leg in legs:
            if leg.get("return_pct") is not None:
                capital *= 1 + leg["return_pct"]/100
        v1 = match.get("return_pct") if match else None
        v2 = (capital-1)*100 if complete else None
        results.append(dict(rotation_root_trade_id=root["trade_id"], baseline_trade_id=match["trade_id"] if match else None,
            match_status="EXACT_SAME_ENTRY" if match else "NO_EXACT_BASELINE_ENTRY", baseline_v1_return=v1,
            rotation_v2_return=v2, rotation_minus_baseline_pct=v2-v1 if v1 is not None and v2 is not None else None,
            comparison_basis="each strategy's own exits; unequal end times possible; no costs",
            rotation_leg_trade_ids=[t["trade_id"] for t in legs], switch_ids=[s["switch_id"] for s in linked_switches]))
    for switch in switches:
        target = rotation.get(switch["target_trade_id"])
        old = post.get(switch["source_trade_id"], {})
        aligned = next((m for m in marks if m["review_id"] == old.get("review_id")
                        and target and m["timestamp_utc"] == target.get("exit_price_at")), None)
        hold_return = ((aligned["price"]/switch["rotation_exit_price"]-1)*100
                       if aligned and switch.get("rotation_exit_price") else None)
        target_return = target.get("return_pct") if target else None
        results.append(dict(switch_id=switch["switch_id"], switch_stage=switch["stage"],
            source_trade_id=switch["source_trade_id"], target_trade_id=switch["target_trade_id"],
            old_ticker_original_tp_after_switch=old.get("original_tp_hit_after_exit"),
            old_ticker_original_tp_after_switch_at=old.get("original_tp_hit_after_exit_at"),
            old_ticker_last_observed_price=old.get("last_observed_price"),
            old_ticker_last_observed_at=old.get("last_observed_at"), old_ticker_tracking_status=old.get("tracking_status"),
            old_ticker_post_exit_max_return=old.get("post_exit_max_return_vs_entry_pct"),
            old_ticker_post_exit_min_return=old.get("post_exit_min_return_vs_entry_pct"),
            new_ticker_mfe_pct=target.get("mfe_pct") if target else None,
            new_ticker_mae_pct=target.get("mae_pct") if target else None,
            new_ticker_return_pct=target_return,
            aligned_observation_at=aligned["timestamp_utc"] if aligned else None,
            hold_old_from_switch_return_pct=hold_return,
            switch_advantage_at_new_exit_pct=(target_return-hold_return
                if target_return is not None and hold_return is not None else None),
            aligned_comparison_status="EXACT_OBSERVED_TIME" if aligned else "NO_EXACT_OLD_PRICE_AT_NEW_EXIT",
            aligned_comparison_basis="old switch exit to new exit; new entry to new exit; cash gap earns zero; no costs"))
    return results
