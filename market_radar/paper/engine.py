"""Experimental baseline state machine. Inputs are known-at-time facts only.

MFE = (max observed price since entry / entry - 1) * 100 (>=0).
MAE = (min observed price since entry / entry - 1) * 100 (<=0).
Invalid paths retain observed excursions but have NULL exit price/return and are
excluded from valid-return statistics. Received UTC timestamps retain microseconds.
"""
import json
import uuid

from ..models import instant, utc
from . import STRATEGY_VERSION
from .policy import ExperimentalBaselinePolicyV1, finite, reason


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


class PaperEngine:
    def __init__(self, store, run_id, *, enabled=False, collection_mode="Manual", max_age=90, started_at=None):
        self.store, self.run_id, self.max_age = store, run_id, max_age
        self.enabled = enabled and collection_mode in {"Manual", "Regular Session"}
        self.policy = ExperimentalBaselinePolicyV1()
        self.session = None
        self.active = None
        self.latest_row = None
        self.finished = False
        self.status = "WAITING" if self.enabled else "OFF"
        if self.enabled and started_at:
            self.recover(started_at)

    def recover(self, at):
        # Read persisted watch/open state before invalidating the unobserved path.
        # Counts/ticker bans come from OPEN-confirmed rows, including interrupted ones.
        with self.store.db:
            recovered = set()
            for trade in self.store.trades():
                if trade["state"] in {"OPEN", "WATCHING", "ENTRY_SIGNAL"}:
                    self.active = trade
                    self._terminate(at, "INTERRUPTED", "restart_gap", valid=False)
                    recovered.add((trade["radar_run_id"], trade["session_date_et"]))
            self.active = None
            for run, day in recovered:
                self.store.db.execute("UPDATE paper_sessions SET status='INTERRUPTED',state_json=?,summary_json=?,updated_at=? "
                                      "WHERE radar_run_id=? AND session_date_et=?",
                                      (encode(dict(status="INTERRUPTED", reason="restart_gap")),
                                       encode(self.store.summary(day)), at, run, day))

    def _entries(self):
        return self.store.entered(self.session.date) if self.session else []

    def interests(self):
        result = {r["ticker"] for r in self._entries()}
        if self.active:
            result.add(self.active["ticker"])
        return result

    def _state(self, at):
        data = self.view()
        self.store.db.execute("INSERT INTO paper_sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(radar_run_id,session_date_et) DO UPDATE SET "
            "session_date_et=excluded.session_date_et,status=excluded.status,state_json=excluded.state_json,updated_at=excluded.updated_at",
            (self.run_id, self.session.date if self.session else "pending", STRATEGY_VERSION, self.status, encode(data), None, at))

    def _save(self, at, event=None, evidence=None):
        if self.active:
            self.active["updated_at"] = at
            self.store.save(self.active)
            if event:
                self.store.event(self.active, at, event, evidence or {})
        self._state(at)

    def _idle(self):
        self.status = "DAILY_LIMIT_REACHED" if len(self._entries()) >= 4 else "FLAT"

    def on_frame(self, session, rows, available_at, *, ws_connected=True, ws_gap=False):
        if not self.enabled:
            return
        session.validate()
        at = instant(available_at)
        if self.session and self.session.date != session.date:
            self.finish(available_at, "date_changed")
            self.active = self.latest_row = None
            self.finished = False
        self.session = session
        if self.finished:
            return
        if at >= instant(session.close_utc):
            self.heartbeat(available_at)
            return
        if at < instant(session.open_utc):
            self.status = "WAITING"
            return
        # A frame may contain only already-completed, nonfuture observations.
        rows = [r for r in rows if r.get("timestamp_utc") and instant(r["timestamp_utc"]) <= at
                and (not r.get("completed_bar_at") or instant(r["completed_bar_at"]) <= instant(r["timestamp_utc"]))]
        with self.store.db:
            if not ws_connected or ws_gap:
                self._invalidate(available_at, "ws_gap")
                self._state(available_at)
                return
            if self.active and self.active["state"] == "OPEN":
                # Price management is tick-based; frame/rank churn cannot close it.
                self._save(available_at)
                return
            if self.active and self.active["state"] == "ENTRY_SIGNAL":
                return  # Immutable signal; only a later live tick can execute it.
            entries = self._entries()
            if len(entries) >= 4:
                self.status = "DAILY_LIMIT_REACHED"
                self._state(available_at)
                return
            row = self.policy.select(rows, {r["ticker"] for r in entries}, available_at, self.max_age)
            if row is None:
                self._invalidate(available_at, "candidate_not_eligible")
                self._idle()
                self._state(available_at)
                return
            self.latest_row = dict(row)
            if not self.active or self.active["ticker"] != row["ticker"]:
                if self.active:
                    self._terminate(available_at, "INTERRUPTED", "watch_replaced", valid=False)
                identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"{self.run_id}:{row['ticker']}:{available_at}").hex
                self.active = dict(trade_id=identifier, radar_run_id=self.run_id, session_date_et=session.date,
                    strategy_version=STRATEGY_VERSION, ticker=row["ticker"], state="WATCHING",
                    watch_started_at=available_at, watch_reference_price=row["price"],
                    watch_completed_bar_at=row.get("completed_bar_at"), candidate_detected_at=row.get("candidate_detected_at"),
                    candidate_class=self.policy.candidate_class(row), current_rank=row.get("current_leader_rank"),
                    emerging_rank=row.get("emerging_rank"), candidate_sources=encode(self.policy.evidence(row, {"watch_reference_price":row["price"]})["candidate_sources"]),
                    last_price_at=row["price_received_utc"], current_price=row["price"],
                    created_at=available_at, data_quality_status="valid", updated_at=available_at)
                self.status = "WATCHING"
                self._save(available_at, "WATCHING", self.policy.evidence(row, self.active))
                return
            if self.policy.confirmed(row, self.active):
                evidence = self.policy.evidence(row, self.active)
                self.active.update(state="ENTRY_SIGNAL", entry_signal_at=available_at,
                    entry_reason=reason(evidence), entry_evidence_json=encode(evidence),
                    candidate_class=evidence["candidate_class"], current_rank=evidence["current_leader_rank"],
                    emerging_rank=evidence["emerging_rank"], candidate_sources=encode(evidence["candidate_sources"]))
                self.status = "ENTRY_SIGNAL"
                self._save(available_at, "ENTRY_SIGNAL", evidence)
            else:
                self._save(available_at)

    def _invalidate(self, at, detail):
        if self.active:
            self._terminate(at, "DATA_INTEGRITY_INVALID", detail, valid=False)
        self._idle()

    def gap(self, at, detail="ws_disconnect"):
        if self.enabled and not self.finished:
            with self.store.db:
                self._invalidate(at, detail)
                self._state(at)

    def on_tick(self, ticker, price, price_at, *, valid=True):
        if not self.enabled or not self.session or self.finished:
            return
        at = instant(price_at)
        if not instant(self.session.open_utc) <= at < instant(self.session.close_utc):
            return
        with self.store.db:
            active = self.active
            if not valid or not finite(price) or price <= 0:
                if active and ticker == active["ticker"]:
                    self._invalidate(price_at, "invalid_live_price_or_ws_state")
                    self._state(price_at)
                return
            if ticker in self.interests():
                self.store.db.execute("INSERT OR IGNORE INTO paper_trade_marks VALUES(?,?,?,?)",
                                      (ticker, price_at, price, self.session.date))
            if not active or ticker != active["ticker"]:
                return
            last = active.get("last_price_at")
            if last and at < instant(last):
                self._invalidate(price_at, "out_of_order_price")
                self._state(price_at)
                return
            if last and (at-instant(last)).total_seconds() > self.max_age:
                self._invalidate(price_at, "stale_gap")
                self._state(price_at)
                return
            # One WS message may contain several FE prices with the same receipt
            # timestamp. Preserve their arrival order; only exact duplicates skip.
            # ENTRY_SIGNAL still requires a strictly later timestamp below.
            if last and at == instant(last) and price == active.get("current_price"):
                return
            active.update(last_price_at=price_at, current_price=price)
            if active["state"] == "ENTRY_SIGNAL" and at > instant(active["entry_signal_at"]):
                if len(self._entries()) >= 4 or ticker in {r["ticker"] for r in self._entries()}:
                    self._invalidate(price_at, "daily_entry_guard")
                    return
                active.update(state="OPEN", entry_price=price, entry_price_at=price_at,
                    entry_price_source="kiwoom_live", target_price=price*1.15, stop_price=price*.90,
                    max_price=price, min_price=price, mfe_pct=0.0, mae_pct=0.0, mfe_at=price_at, mae_at=price_at,
                    current_return_pct=0.0)
                self.status = "OPEN"
                self._save(price_at, "OPEN", dict(price=price, price_timestamp=price_at, source="kiwoom_live"))
                return
            if active["state"] == "OPEN":
                if price > active["max_price"]:
                    active.update(max_price=price, mfe_at=price_at)
                if price < active["min_price"]:
                    active.update(min_price=price, mae_at=price_at)
                active.update(mfe_pct=(active["max_price"]/active["entry_price"]-1)*100,
                              mae_pct=(active["min_price"]/active["entry_price"]-1)*100,
                              current_return_pct=(price/active["entry_price"]-1)*100)
                if price >= active["target_price"] or price <= active["stop_price"]:
                    trigger = "TAKE_PROFIT_15" if price >= active["target_price"] else "STOP_LOSS_10"
                    self._terminate(price_at, trigger, "first_observed_threshold_crossing", valid=True)
                    self._idle()
                    self._state(price_at)
                    return
            self._save(price_at)

    def _terminate(self, at, exit_reason, detail, *, valid):
        trade = self.active
        if not trade:
            return
        opened = trade.get("entry_price") is not None
        evidence = dict(reason=detail, gap_start=trade.get("last_price_at") if not valid else None,
                        gap_end=at if not valid else None, last_observed_price=trade.get("current_price"),
                        last_observed_at=trade.get("last_price_at"))
        trade.update(state="CLOSED" if valid and opened else "INVALIDATED", exit_signal_at=at,
            exit_reason=exit_reason, exit_reason_detail=detail, exit_evidence_json=encode(evidence),
            data_quality_status="valid" if valid else "invalid", updated_at=at,
            exit_price=trade.get("current_price") if valid and opened else None,
            exit_price_at=trade.get("last_price_at") if valid and opened else None,
            return_pct=trade.get("current_return_pct") if valid and opened else None)
        self.store.save(trade)
        self.store.event(trade, at, exit_reason, evidence)
        self.active = None

    def heartbeat(self, at):
        if not self.enabled or not self.session or self.finished:
            return
        now, close = instant(at), instant(self.session.close_utc)
        with self.store.db:
            if self.active and self.active.get("last_price_at"):
                gap = (min(now, close)-instant(self.active["last_price_at"])).total_seconds()
                if gap > self.max_age or gap < 0:
                    self._invalidate(at, "stale_gap" if gap > 0 else "clock_reversal")
            if now >= close:
                self.finish(at, "official_close")
            else:
                self._state(at)

    def finish(self, at, detail="collector_stop"):
        if not self.enabled or self.finished:
            return
        with self.store.db:
            close_reached = self.session and instant(at) >= instant(self.session.close_utc)
            if self.active:
                last = self.active.get("last_price_at")
                continuous = last and (instant(self.session.close_utc)-instant(last)).total_seconds() <= self.max_age
                valid = bool(close_reached and continuous and self.active["state"] == "OPEN")
                self._terminate(at, "SESSION_END" if valid else "INTERRUPTED", detail, valid=valid)
            self.finished = True
            self.status = "CLOSED"
            self._state(at)
            if self.session:
                self.store.horizons(self.session.date)
                self.store.db.execute("UPDATE paper_sessions SET summary_json=? WHERE radar_run_id=? AND session_date_et=?",
                                      (encode(self.store.summary(self.session.date)), self.run_id, self.session.date))

    def view(self):
        return dict(status=self.status, strategy=STRATEGY_VERSION, trades=len(self._entries()),
                    active=dict(self.active) if self.active else None, evidence=dict(self.latest_row) if self.latest_row else None,
                    recent=self.store.entered(self.session.date)[-10:] if self.session else [],
                    summary=self.store.summary(self.session.date) if self.session else {}, actual_order_calls=0)
