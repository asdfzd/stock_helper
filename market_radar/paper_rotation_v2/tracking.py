"""One-way post-trade observer. No reference to an engine or execution policy."""
from ..models import instant
from ..paper.policy import finite

POST_FIELDS = """post_exit_max_price post_exit_min_price post_exit_max_return_vs_entry_pct
post_exit_min_return_vs_entry_pct original_tp_hit_after_exit_at original_sl_hit_after_exit_at
max_price_after_tp extra_runup_after_tp_pct lowest_price_after_tp max_drawdown_after_tp_pct
recovered_entry_at original_tp_hit_after_sl_at max_price_after_sl""".split()


class PostExitTracker:
    def __init__(self, store, max_age=90):
        self.store, self.max_age = store, max_age
        self.records = {r["review_id"]: r for r in store.documents("paper_post_exit")}
        self.sequence = store.db.execute("SELECT COALESCE(MAX(observation_id),0) FROM paper_post_exit_marks").fetchone()[0]
        with store.db:
            for record in self.records.values():
                if record["tracking_status"] == "OBSERVING":
                    record["gap_reasons"].append("restart_gap")
                    self._save(record)

    def _save(self, record):
        self.store.put("paper_post_exit", record["review_id"], record)

    def capture(self, trades, session):
        with self.store.db:
            for trade in trades:
                if trade.get("entry_price") is None:
                    continue
                key = trade["strategy_version"] + ":" + trade["trade_id"]
                self.store.put("review_trades", key, dict(trade, review_id=key, switched=bool(trade.get("switched"))))
                if not trade.get("exit_reason") or key in self.records:
                    continue
                record = dict.fromkeys(POST_FIELDS)
                record.update(review_id=key, trade_id=trade["trade_id"], strategy_version=trade["strategy_version"],
                    radar_run_id=trade["radar_run_id"], session_date_et=trade["session_date_et"], ticker=trade["ticker"],
                    original_entry_price=trade["entry_price"], original_tp_price=trade["target_price"],
                    original_sl_price=trade["stop_price"], exit_price=trade.get("exit_price"),
                    exit_reason=trade["exit_reason"], exit_at=trade["exit_signal_at"], official_close_utc=session.close_utc,
                    original_tp_hit_after_exit=False, original_sl_hit_after_exit=False,
                    recovered_entry_after_sl=False, original_tp_hit_after_sl=False,
                    observation_count=0, last_observed_at=None, last_observed_price=None,
                    tracking_status="OBSERVING", gap_reasons=[], tags=[], running_peak=trade.get("exit_price"),
                    tag_policy="continued_run: price > TP exit; reversal: chronological peak-to-trough <= -10%",
                    post_exit_use="post_trade_review_only")
                self.records[key] = record
                self._save(record)

    def on_tick(self, ticker, price, at, *, valid=True):
        self.sequence += 1
        with self.store.db:
            for record in self.records.values():
                if record["ticker"] != ticker or record["tracking_status"] != "OBSERVING":
                    continue
                if not instant(record["exit_at"]) < instant(at) < instant(record["official_close_utc"]):
                    continue
                last = record["last_observed_at"] or record["exit_at"]
                if instant(at) < instant(last):
                    self._gap_record(record, "out_of_order_price")
                    continue
                if not valid or not finite(price) or price <= 0:
                    self._gap_record(record, "invalid_live_price")
                    continue
                if (instant(at)-instant(last)).total_seconds() > self.max_age:
                    self._gap_record(record, "stale_gap")
                self.store.db.execute("INSERT INTO paper_post_exit_marks VALUES(?,?,?,?)", (self.sequence, record["review_id"], at, price))
                record["observation_count"] += 1
                record.update(last_observed_at=at, last_observed_price=price)
                record["post_exit_max_price"] = max(price, record["post_exit_max_price"] or price)
                record["post_exit_min_price"] = min(price, record["post_exit_min_price"] or price)
                entry = record["original_entry_price"]
                record["post_exit_max_return_vs_entry_pct"] = (record["post_exit_max_price"]/entry-1)*100
                record["post_exit_min_return_vs_entry_pct"] = (record["post_exit_min_price"]/entry-1)*100
                for label, hit in (("tp", price >= record["original_tp_price"]), ("sl", price <= record["original_sl_price"])):
                    key = "original_" + label + "_hit_after_exit"
                    if hit and not record[key]:
                        record[key], record[key + "_at"] = True, at
                if record["exit_reason"] == "STOP_LOSS_10":
                    if price >= entry and not record["recovered_entry_after_sl"]:
                        record.update(recovered_entry_after_sl=True, recovered_entry_at=at)
                    record.update(max_price_after_sl=record["post_exit_max_price"],
                        original_tp_hit_after_sl=record["original_tp_hit_after_exit"],
                        original_tp_hit_after_sl_at=record["original_tp_hit_after_exit_at"])
                    if record["original_tp_hit_after_sl"]:
                        record["tags"] = ["STOP_THEN_ORIGINAL_TP"]
                if record["exit_reason"] == "TAKE_PROFIT_15":
                    record["running_peak"] = max(record["running_peak"], price)
                    drawdown = (price/record["running_peak"]-1)*100
                    record.update(max_price_after_tp=record["post_exit_max_price"], lowest_price_after_tp=record["post_exit_min_price"],
                        extra_runup_after_tp_pct=max(0.0, (record["post_exit_max_price"]/record["exit_price"]-1)*100),
                        max_drawdown_after_tp_pct=min(record["max_drawdown_after_tp_pct"] or 0.0, drawdown))
                    record["tags"] = (["TP_THEN_CONTINUED_RUN"] if record["extra_runup_after_tp_pct"] > 0 else [])
                    if record["max_drawdown_after_tp_pct"] <= -10:
                        record["tags"].append("TP_THEN_REVERSAL")
                self._save(record)

    def _gap_record(self, record, reason):
        if reason not in record["gap_reasons"]:
            record["gap_reasons"].append(reason)
        self._save(record)

    def gap(self, at, reason):
        with self.store.db:
            for r in self.records.values():
                if r["tracking_status"] == "OBSERVING" and instant(at) < instant(r["official_close_utc"]):
                    self._gap_record(r, reason)

    def finish(self, at, *, stopped=False):
        with self.store.db:
            for r in self.records.values():
                if r["tracking_status"] != "OBSERVING":
                    continue
                closed = instant(at) >= instant(r["official_close_utc"])
                if not closed and not stopped:
                    continue
                if not closed:
                    self._gap_record(r, "collector_stopped_before_close")
                elif instant(r["exit_at"]) >= instant(r["official_close_utc"]):
                    r["tracking_status"] = "NO_POST_EXIT_WINDOW"
                else:
                    last = r["last_observed_at"] or r["exit_at"]
                    if not r["observation_count"] or (instant(r["official_close_utc"])-instant(last)).total_seconds() > self.max_age:
                        self._gap_record(r, "no_fresh_price_at_close")
                if r["tracking_status"] == "OBSERVING":
                    r["tracking_status"] = "INCOMPLETE" if r["gap_reasons"] else "OBSERVED_TO_CLOSE"
                r["tracking_ended_at"] = r["official_close_utc"] if closed else at
                self._save(r)
