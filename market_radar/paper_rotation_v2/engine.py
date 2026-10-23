"""Rotation state machine; post-exit review is not an input dependency."""
import uuid

from ..models import instant
from ..paper.engine import PaperEngine, encode
from ..paper.policy import finite, reason, top
from . import STRATEGY_VERSION, SWITCH_REASON
from .policy import RotationPolicy


class RotationEngine(PaperEngine):
    def __init__(self, *args, **kwargs):
        self.rotation_watch = None
        self.pending_switch = None
        super().__init__(*args, **kwargs)
        self.policy = RotationPolicy()

    def recover(self, at):
        super().recover(at)
        with self.store.db:
            for switch in self.store.documents("rotation_switches"):
                if switch["stage"] in {"EXIT_PENDING", "ENTRY_PENDING"}:
                    switch.update(stage="CANCELLED", cancelled_at=at, cancellation_reason="restart_gap")
                    self.store.put("rotation_switches", switch["switch_id"], switch)

    def _state(self, at):
        super()._state(at)
        self.store.db.execute("UPDATE paper_sessions SET strategy_version=? WHERE radar_run_id=? AND session_date_et=?",
            (STRATEGY_VERSION, self.run_id, self.session.date if self.session else "pending"))

    def _save(self, at, event=None, evidence=None):
        if self.active:
            self.active["strategy_version"] = STRATEGY_VERSION
            self.active.setdefault("rotation_root_trade_id", self.active["trade_id"])
        super()._save(at, event, evidence)

    def interests(self, *, entries=None):
        result = super().interests(entries=entries)
        if self.rotation_watch:
            result.add(self.rotation_watch["ticker"])
        if self.pending_switch:
            result.add(self.pending_switch["target_ticker"])
        return result

    def _cancel_switch(self, at, reason):
        if self.pending_switch:
            self.pending_switch.update(stage="CANCELLED", cancelled_at=at, cancellation_reason=reason)
            self.store.put("rotation_switches", self.pending_switch["switch_id"], self.pending_switch)
        self.pending_switch = self.rotation_watch = None

    def _terminate(self, at, exit_reason, detail, *, valid):
        super()._terminate(at, exit_reason, detail, valid=valid)
        if exit_reason != SWITCH_REASON:
            self._cancel_switch(at, exit_reason)

    def on_frame(self, session, rows, available_at, *, ws_connected=True, ws_gap=False):
        if not self.active or self.active["state"] != "OPEN":
            return super().on_frame(session, rows, available_at, ws_connected=ws_connected, ws_gap=ws_gap)
        if (not self.enabled or self.finished or session.date != self.session.date
                or not instant(session.open_utc) <= instant(available_at) < instant(session.close_utc)
                or not ws_connected or ws_gap):
            return super().on_frame(session, rows, available_at, ws_connected=ws_connected, ws_gap=ws_gap)
        if self.pending_switch or len(self._entries()) >= 4:
            return
        session.validate()
        self.session = session
        rows = [r for r in rows if r.get("timestamp_utc") and instant(r["timestamp_utc"]) <= instant(available_at)
                and r.get("completed_bar_at") and instant(r["completed_bar_at"]) <= instant(r["timestamp_utc"])]
        current = next((r for r in rows if r["ticker"] == self.active["ticker"]), None)
        eligible = [r for r in rows if top(r.get("current_leader_rank")) and top(r.get("emerging_rank"))]
        candidate = self.policy.select(eligible, {t["ticker"] for t in self._entries()}, available_at, self.max_age)
        with self.store.db:
            if candidate is None:
                self.rotation_watch = None
                self._save(available_at)
                return
            if not self.rotation_watch or self.rotation_watch["ticker"] != candidate["ticker"]:
                self.rotation_watch = dict(ticker=candidate["ticker"], watch_started_at=available_at,
                    watch_reference_price=candidate["price"], watch_completed_bar_at=candidate.get("completed_bar_at"))
                self._save(available_at, "ROTATION_WATCHING", self.rotation_watch)
                return
            evidence = self.policy.dominance(candidate, current, self.rotation_watch, available_at, self.max_age)
            if evidence:
                evidence["entry_confirmation"]["policy"] = STRATEGY_VERSION
                identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"rotation:{self.run_id}:{available_at}:{candidate['ticker']}").hex
                self.pending_switch = dict(switch_id=identifier, radar_run_id=self.run_id,
                    session_date_et=session.date, source_trade_id=self.active["trade_id"],
                    rotation_root_trade_id=self.active["rotation_root_trade_id"], target_trade_id=None,
                    source_ticker=self.active["ticker"], target_ticker=candidate["ticker"],
                    switch_signal_at=available_at, stage="EXIT_PENDING", switch_reason="material_evidence_dominance",
                    evidence=evidence, watch=dict(self.rotation_watch))
                self.active.update(switch_id=identifier, switch_signal_at=available_at,
                    switch_reason="material_evidence_dominance", switch_evidence_json=encode(evidence), switch_signaled=True)
                self.store.put("rotation_switches", identifier, self.pending_switch)
                self._save(available_at, "SWITCH_SIGNAL", evidence)
            else:
                self._save(available_at)

    def on_tick(self, ticker, price, price_at, *, valid=True):
        switch = self.pending_switch
        if not switch or self.finished or not self.enabled or not self.session:
            return super().on_tick(ticker, price, price_at, valid=valid)
        at = instant(price_at)
        if not instant(self.session.open_utc) <= at < instant(self.session.close_utc):
            return
        if switch["stage"] == "EXIT_PENDING":
            if ticker != switch["source_ticker"]:
                return  # Never cache a target tick for later execution.
            if at <= instant(switch["switch_signal_at"]):
                return
            active = self.active
            last = instant(active["last_price_at"])
            if not valid or not finite(price) or price <= 0 or at < last or (at-last).total_seconds() > self.max_age:
                self.gap(price_at, "invalid_switch_exit_price")
                return
            with self.store.db:
                active.update(current_price=price, last_price_at=price_at, rotation_exit_price=price,
                    switched=True, rotation_exit_at=price_at,
                    max_price=max(price, active["max_price"]), min_price=min(price, active["min_price"]))
                if price == active["max_price"]:
                    active["mfe_at"] = price_at
                if price == active["min_price"]:
                    active["mae_at"] = price_at
                active.update(mfe_pct=(active["max_price"]/active["entry_price"]-1)*100,
                    mae_pct=(active["min_price"]/active["entry_price"]-1)*100,
                    current_return_pct=(price/active["entry_price"]-1)*100)
                self._terminate(price_at, SWITCH_REASON, "material_evidence_dominance", valid=True)
                switch.update(stage="ENTRY_PENDING", rotation_exit_price=price, rotation_exit_at=price_at)
                watch, row = switch["watch"], switch["evidence"]["candidate"]
                evidence = switch["evidence"]["entry_confirmation"]
                target_id = uuid.uuid5(uuid.NAMESPACE_URL, "target:" + switch["switch_id"]).hex
                switch["target_trade_id"] = target_id
                self.active = dict(trade_id=target_id, radar_run_id=self.run_id, session_date_et=self.session.date,
                    strategy_version=STRATEGY_VERSION, ticker=switch["target_ticker"],
                    state="ENTRY_SIGNAL", **{k: v for k, v in watch.items() if k != "ticker"},
                    entry_signal_at=price_at, switch_signal_at=switch["switch_signal_at"],
                    rotation_root_trade_id=switch["rotation_root_trade_id"], parent_trade_id=switch["source_trade_id"],
                    switch_id=switch["switch_id"], switch_reason="material_evidence_dominance",
                    switch_evidence_json=encode(switch["evidence"]), switched=True,
                    entry_reason=reason(evidence), entry_evidence_json=encode(evidence),
                    candidate_class=self.policy.candidate_class(row), current_rank=row["current_leader_rank"],
                    emerging_rank=row["emerging_rank"], candidate_sources=encode(evidence["candidate_sources"]),
                    candidate_detected_at=row.get("candidate_detected_at"), last_price_at=row["price_received_utc"],
                    current_price=row["price"], created_at=price_at, updated_at=price_at, data_quality_status="valid")
                self.status = "ENTRY_SIGNAL"
                self.store.put("rotation_switches", switch["switch_id"], switch)
                self._save(price_at, "ROTATION_ENTRY_PENDING", switch["evidence"])
            return
        if ticker != switch["target_ticker"] or at <= instant(switch["rotation_exit_at"]):
            return
        super().on_tick(ticker, price, price_at, valid=valid)
        if self.active and self.active["state"] == "OPEN":
            with self.store.db:
                switch.update(stage="COMPLETE", rotation_entry_at=price_at, rotation_entry_price=price)
                self.active["rotation_entry_price"] = price
                self.store.put("rotation_switches", switch["switch_id"], switch)
                self.pending_switch = self.rotation_watch = None
                self._save(price_at)

    def view(self, *, entries=None):
        result = super().view(entries=entries)
        result.update(strategy=STRATEGY_VERSION, rotation_watch=self.rotation_watch,
                      pending_switch=self.pending_switch)
        return result
