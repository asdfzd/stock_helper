from __future__ import annotations

import json
from datetime import timedelta

from .calculations import calculate, pct, rank
from .config import RadarConfig
from .models import ET, MarketBatch, instant, utc
from .storage import RadarStore

HORIZONS = {"30s": 30, "1m": 60, "3m": 180, "5m": 300, "10m": 600, "30m": 1800, "60m": 3600}


class RadarEngine:
    def __init__(self, store: RadarStore, config: RadarConfig, provider: str, mode: str):
        self.store, self.config, self.provider, self.mode = store, config, provider, mode

    def process(self, batch: MarketBatch, received_utc: str) -> list[dict]:
        batch.validate()
        rows = [calculate(o, self.config) for o in batch.observations]
        stamp = utc(instant(batch.timestamp_utc))
        db = self.store.db
        if db.execute("SELECT 1 FROM radar_batches WHERE timestamp_utc=?", (stamp,)).fetchone():
            # Return committed values, including snapshot-based return baselines.
            return [dict(r) for r in db.execute("SELECT * FROM radar_snapshots WHERE timestamp_utc=?", (stamp,))]
        latest = db.execute("SELECT MAX(timestamp_utc) FROM radar_batches").fetchone()[0]
        if latest and instant(stamp) <= instant(latest):
            raise ValueError("Out-of-order batch; use a separate DB for historical replay")
        for row, observation in zip(rows, batch.observations):
            if observation.metadata.get("rvol_basis") == "completed_1m":
                continue  # no 30-second snapshot fallback for explicitly minute-based metrics
            for minutes in (1, 5):
                key = f"return_{minutes}m"
                if row[key] is None:
                    target = utc(instant(stamp) - timedelta(minutes=minutes))
                    baseline = db.execute("SELECT price FROM radar_snapshots WHERE ticker=? AND session_date=? AND timestamp_utc=?",
                                          (row["ticker"], row["session_date"], target)).fetchone()
                    if baseline:
                        row[key] = pct(row["price"], baseline[0])
            if row["rvol"] is None and row["volume"] is not None:
                dates = observation.history_dates[:self.config.rvol_lookback_days]
                elapsed = (instant(stamp) - instant(observation.session.open_utc)).total_seconds()
                history = {h.session_date: h.volume for h in observation.historical_volumes if h.elapsed_seconds == elapsed}
                for day in dates:
                    if day not in history:
                        prior_volume = db.execute(
                            "SELECT volume FROM radar_snapshots WHERE ticker=? AND session_date=? "
                            "AND substr(timestamp_et,12,length(timestamp_et)-17)=? AND volume IS NOT NULL LIMIT 1",
                            (row["ticker"], day, row["timestamp_et"][11:-6])).fetchone()
                        if prior_volume:
                            history[day] = prior_volume[0]
                if len(dates) == self.config.rvol_lookback_days and all(day in history for day in dates):
                    baseline = sum(history[day] for day in dates) / len(dates)
                    row["rvol"] = row["volume"] / baseline if baseline > 0 else None
        rank(rows)
        with db:  # snapshots, transitions and outcomes commit/rollback together
            db.execute("INSERT INTO radar_batches VALUES(?,?,?,?,?,?)", (
                stamp, self.provider, self.mode, batch.universe,
                json.dumps(batch.raw(), allow_nan=False), received_utc))
            for session in batch.sessions:
                db.execute("INSERT OR REPLACE INTO market_sessions VALUES(?,?,?)", (
                    session.date, utc(instant(session.open_utc)), utc(instant(session.close_utc))))
            for close in batch.daily_closes:
                session = db.execute("SELECT close_utc FROM market_sessions WHERE session_date=?", (close.session_date,)).fetchone()
                if session is None or instant(session[0]) != instant(close.timestamp_utc):
                    raise ValueError("Official close requires matching exchange calendar close")
                if self.store.schema_version >= 2:
                    db.execute("INSERT OR REPLACE INTO market_closes VALUES(?,?,?,?,?,?)", (
                        close.ticker, close.session_date, utc(instant(close.timestamp_utc)), close.price, close.source, close.status))
                elif close.status == "complete":
                    db.execute("INSERT OR REPLACE INTO market_closes VALUES(?,?,?,?)", (
                        close.ticker, close.session_date, utc(instant(close.timestamp_utc)), close.price))
            present = {r["ticker"] for r in rows}
            if self.store.schema_version >= 3:
                day = instant(stamp).astimezone(ET).date().isoformat()
                reasons = batch.metadata.get("missing_reasons", {})
                for item in batch.metadata.get("candidates", []):
                    prior = db.execute("SELECT candidate_sources FROM radar_discoveries WHERE session_date=? AND ticker=?",
                                       (day, item["ticker"])).fetchone()
                    sources = sorted(set(item.get("sources", [])) | set(json.loads(prior[0]) if prior else []))
                    state = ("excluded_symbol_type" if item.get("excluded_symbol_type") else
                             reasons.get(item["ticker"], "subscribed" if item["ticker"] in batch.metadata.get("active", {}) else "awaiting_subscription"))
                    db.execute("INSERT INTO radar_discoveries VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(session_date,ticker) "
                        "DO UPDATE SET last_seen_utc=excluded.last_seen_utc,symbol_type=excluded.symbol_type,"
                        "symbol_type_evidence=excluded.symbol_type_evidence,excluded_symbol_type=excluded.excluded_symbol_type,"
                        "candidate_sources=excluded.candidate_sources,candidate_tracking_state=excluded.candidate_tracking_state",
                        (day,item["ticker"],stamp,stamp,item.get("type", "unknown"),json.dumps(item.get("symbol_type_evidence", {})),
                         int(bool(item.get("excluded_symbol_type"))),json.dumps(sources),state))
                db.execute("UPDATE radar_candidates SET candidate_tracking_state='released' WHERE tracking_protected_until<?", (stamp,))
                for c in db.execute("SELECT id,ticker FROM radar_candidates WHERE tracking_protected_until>=?", (stamp,)).fetchall():
                    db.execute("UPDATE radar_candidates SET candidate_tracking_state=? WHERE id=?",
                        (reasons.get(c["ticker"], "protected"),c["id"]))
            # Absence from a full universe means a ranking exit, not a repeated entry.
            for state in db.execute("SELECT ticker,snapshot_json FROM radar_state").fetchall():
                if state[0] not in present:
                    previous = json.loads(state[1])
                    previous["current_leader_rank"] = previous["emerging_rank"] = None
                    db.execute("UPDATE radar_state SET snapshot_json=? WHERE ticker=?", (json.dumps(previous), state[0]))
            for row, observation in zip(rows, batch.observations):
                snapshot_id = self.store.insert("radar_snapshots", row)
                prior = db.execute("SELECT snapshot_json FROM radar_state WHERE ticker=? AND session_date=?",
                                   (row["ticker"], row["session_date"])).fetchone()
                previous = json.loads(prior[0]) if prior else None
                events = []
                detected = any(row[k] is not None and row[k] <= 10 for k in ("current_leader_rank", "emerging_rank"))
                candidate = db.execute("SELECT id FROM radar_candidates WHERE session_date=? AND ticker=?",
                                       (row["session_date"], row["ticker"])).fetchone()
                if detected and candidate is None:
                    candidate_id = self.store.insert("radar_candidates", dict(
                        session_date=row["session_date"], ticker=row["ticker"], first_detected_utc=stamp,
                        first_detected_et=row["timestamp_et"], first_price=row["price"],
                        session_close_utc=utc(instant(observation.session.close_utc)), test_version=self.config.test_version))
                    self._new_results(candidate_id, stamp, observation.session.close_utc)
                    if self.store.schema_version >= 2:
                        db.execute("UPDATE radar_candidates SET candidate_sources=?,provider=?,exchange=? WHERE id=?", (
                            row.get("candidate_sources"), self.provider, row.get("exchange"), candidate_id))
                    if self.store.schema_version >= 3:
                        end = min(instant(stamp)+timedelta(seconds=max(max(HORIZONS.values()), self.config.result_tracking_hold_minutes*60)),
                                  instant(observation.session.close_utc)) + timedelta(seconds=self.config.result_tolerance_seconds)
                        db.execute("UPDATE radar_candidates SET tracking_protected_until=?,candidate_tracking_state='protected',"
                            "symbol_type=?,symbol_type_evidence=? WHERE id=?", (utc(end), row.get("symbol_type", "unknown"),
                            row.get("symbol_type_evidence", "{}"), candidate_id))
                    events.append("FIRST_DETECTED")
                for key, label in (("current_leader_rank", "LEADER"), ("emerging_rank", "EMERGING")):
                    for threshold in (10, 5, 3):
                        old = previous[key] if previous else None
                        if row[key] is not None and row[key] <= threshold and (old is None or old > threshold):
                            events.append(f"ENTER_{label}_TOP{threshold}")
                if previous:
                    if row["hod"] is not None and previous["hod"] is not None and row["hod"] > previous["hod"]:
                        events.append("HOD_BREAK")
                    old, new = previous["above_vwap"], row["above_vwap"]
                    if old is not None and new is not None and old != new:
                        events.append("VWAP_RECLAIM" if new else "VWAP_LOSS")
                for kind in events:
                    self.store.insert("radar_events", dict(
                        timestamp_utc=stamp, timestamp_et=row["timestamp_et"], session_date=row["session_date"],
                        ticker=row["ticker"], event_type=kind, price=row["price"], snapshot_id=snapshot_id,
                        test_version=self.config.test_version))
                db.execute("INSERT OR REPLACE INTO radar_state VALUES(?,?,?)",
                           (row["ticker"], row["session_date"], json.dumps(row)))
            self._resolve_results(stamp)
        return rows

    def _new_results(self, candidate_id, detected, close):
        for name, seconds in HORIZONS.items():
            target = instant(detected) + timedelta(seconds=seconds)
            status = "pending" if target <= instant(close) else "outside_session"
            self.store.insert("radar_results", dict(candidate_id=candidate_id, horizon=name, target_utc=utc(target), status=status))
            if self.store.schema_version >= 3 and status == "outside_session":
                self.store.db.execute("UPDATE radar_results SET missing_reason='outside_session' WHERE candidate_id=? AND horizon=?", (candidate_id,name))
        for name in ("EOD", "D+1", "D+2", "D+3"):
            self.store.insert("radar_results", dict(candidate_id=candidate_id, horizon=name,
                                                   target_utc=utc(instant(close)) if name == "EOD" else None))

    def _resolve_results(self, now):
        db = self.store.db
        pending = db.execute(
            "SELECT r.*,c.ticker,c.session_date,c.first_detected_utc,c.first_price "
            "FROM radar_results r JOIN radar_candidates c ON c.id=r.candidate_id WHERE r.status IN ('pending','provisional')"
        ).fetchall()
        for result in pending:
            target = result["target_utc"]
            if result["horizon"].startswith("D+") and target is None:
                # Never infer trading days by adding weekdays; supplied calendar only.
                known = db.execute("SELECT 1 FROM market_sessions WHERE session_date=?", (result["session_date"],)).fetchone()
                following = db.execute("SELECT close_utc FROM market_sessions WHERE session_date>? ORDER BY session_date LIMIT 3",
                                       (result["session_date"],)).fetchall() if known else []
                index = int(result["horizon"][-1]) - 1
                if len(following) > index:
                    target = following[index][0]
                    db.execute("UPDATE radar_results SET target_utc=? WHERE id=?", (target, result["id"]))
            if target is None or instant(now) < instant(target):
                continue
            if result["horizon"] in HORIZONS:
                limit = utc(instant(target) + timedelta(seconds=self.config.result_tolerance_seconds))
                observed = db.execute(
                    "SELECT timestamp_utc,price FROM radar_snapshots WHERE ticker=? AND session_date=? "
                    "AND timestamp_utc>=? AND timestamp_utc<=? ORDER BY timestamp_utc LIMIT 1",
                    (result["ticker"], result["session_date"], target, limit)).fetchone()
                selection = 'post_target'
                # Wait out the post-target window before using any early sample.
                # Otherwise a later valid post-target sample would lose priority.
                if observed is None and instant(now) > instant(limit) and self.store.schema_version >= 4:
                    earliest = utc(instant(target) - timedelta(seconds=self.config.result_pre_target_grace_seconds))
                    observed = db.execute(
                        "SELECT timestamp_utc,price FROM radar_snapshots WHERE ticker=? AND session_date=? "
                        "AND timestamp_utc>=? AND timestamp_utc<? ORDER BY timestamp_utc DESC LIMIT 1",
                        (result['ticker'], result['session_date'], earliest, target)).fetchone()
                    selection = 'pre_target_grace'
                if observed is None and instant(now) > instant(limit):
                    db.execute("UPDATE radar_results SET status='missing' WHERE id=?", (result["id"],))
                    if self.store.schema_version >= 3:
                        evidence = db.execute("SELECT raw_json FROM radar_batches WHERE timestamp_utc>=? AND timestamp_utc<=? "
                            "ORDER BY timestamp_utc DESC LIMIT 1", (target,limit)).fetchone()
                        reason = "no_snapshot_within_tolerance"
                        if evidence:
                            reason = json.loads(evidence[0]).get("metadata", {}).get("missing_reasons", {}).get(result["ticker"], reason)
                        db.execute("UPDATE radar_results SET missing_reason=? WHERE id=?", (reason,result["id"]))
            else:
                observed = db.execute("SELECT timestamp_utc,price FROM market_closes WHERE ticker=? AND timestamp_utc=?",
                                      (result["ticker"], target)).fetchone()
            if observed is None:
                continue
            values = db.execute(
                "SELECT MAX(price),MIN(price) FROM radar_snapshots WHERE ticker=? AND timestamp_utc>=? AND timestamp_utc<=?",
                (result["ticker"], result["first_detected_utc"], observed[0])).fetchone()
            high = max(result["first_price"], observed[1], values[0] or result["first_price"])
            low = min(result["first_price"], observed[1], values[1] or result["first_price"])
            db.execute("UPDATE radar_results SET observed_utc=?,price=?,return_pct=?,status='complete',"
                       "timing_error_seconds=?,mfe_pct=?,mae_pct=? WHERE id=?", (
                           observed[0], observed[1], pct(observed[1], result["first_price"]),
                           (instant(observed[0]) - instant(target)).total_seconds(),
                           pct(high, result["first_price"]), pct(low, result["first_price"]), result["id"]))
            if self.store.schema_version >= 4 and result['horizon'] in HORIZONS:
                db.execute("UPDATE radar_results SET sample_timestamp=?,sample_offset_seconds=?,sample_selection=? WHERE id=?",
                    (observed[0], (instant(observed[0])-instant(target)).total_seconds(), selection, result['id']))
            if self.store.schema_version >= 2:
                source, status = "sampled_prices", "complete"
                if result["horizon"] not in HORIZONS:
                    provenance = db.execute("SELECT source,status FROM market_closes WHERE ticker=? AND timestamp_utc=?",
                                            (result["ticker"], target)).fetchone()
                    source, status = provenance
                db.execute("UPDATE radar_results SET result_source=?,close_status=?,status=? WHERE id=?",
                           (source, status if result["horizon"] not in HORIZONS else None, status, result["id"]))
