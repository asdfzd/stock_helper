"""Kiwoom research adapter. Slot priority is operational, never an investment score."""
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import logging
import queue
import threading
import time

from ..models import DailyClose, ET, HistoricalVolume, MarketBatch, Observation, instant, utc
from ..provider import ProviderError, UniverseWarmingUp, UniverseStale
from ..quality import event
from .kiwoom_types import classify_symbol, excluded_type
from .kiwoom_cache import KiwoomCache, completed_cutoff, contiguous, normalize_bars, number, POLICY
from .kiwoom_calendar import ExchangeCalendar
from .kiwoom_rest import KiwoomRest
from .kiwoom_failures import StopRequested, RestFailure
from .kiwoom_ws import KiwoomWebSocket
from .kiwoom_lock import CollectorLock

SOURCES = {"day_change": "usa20910", "5m_spike": "usa20930", "volume": "usa20530", "value": "usa20540"}


def merge_candidates(pages, catalog, policy="common_and_unknown"):
    merged = {}
    for source, rows, received, offset in pages:
        for index, row in enumerate(rows, offset+1):
            ticker, exchange = row.get("stk_cd", "").strip().upper(), row.get("stex_tp")
            if not ticker or exchange not in {"ND", "NY", "NA"}:
                continue
            entry = merged.setdefault(ticker, {"ticker": ticker, "exchange": exchange, "sources": [],
                "source_ranks": {}, "source_received": {}, "type": "type_unknown", "type_policy": policy})
            if entry["exchange"] != exchange:
                entry["exchange_ambiguous"] = True
            if source not in entry["sources"]:
                entry["sources"].append(source)
            entry["source_ranks"][source] = min(index, entry["source_ranks"].get(source, index))
            entry["source_received"][source] = received
            fields = json.loads(catalog[ticker]["raw_json"]) if ticker in catalog else {}
            entry["type"], entry["symbol_type_evidence"] = classify_symbol(fields)
            entry["raw_type_fields"] = entry["symbol_type_evidence"]["fields"]
            entry["excluded_symbol_type"] = excluded_type(entry["type"], policy)
    return merged


def allocate_slots(candidates, limit):
    # Deterministic operational ordering; not a buy/ranking score.
    return sorted((c for c in candidates.values() if not c.get("exchange_ambiguous") and not c.get("excluded_symbol_type")), key=lambda c: (
        -len(c["sources"]), *(c["source_ranks"].get(s, 10**9) for s in SOURCES), c["ticker"]))[:limit]


class KiwoomProvider:
    name, mode = "kiwoom", "live"

    def __init__(self, config, *, rest_factory=KiwoomRest, ws_factory=KiwoomWebSocket, calendar=None):
        self.config, self.rest_factory, self.ws_factory = config, rest_factory, ws_factory
        self.calendar = calendar
        self.logger = logging.getLogger("market_radar.kiwoom")
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.raw_queue = queue.Queue(maxsize=config.raw_queue_size)
        self.raw_dropped = 0
        self.candidates, self.selected, self.quotes = {}, {}, {}
        self.tracked = ()
        self.universe_at = None
        self.cache = self.rest = self.ws = self.io_thread = None
        self.jobs, self.finished, self.retry_at = {}, {}, {}
        self.quote_days, self.daily_updated = {}, {}
        self.catalog = {}
        self.error = None
        self.collector_lock = None
        self.protected = {}
        self.slot_unavailable = set()
        self._closed = False
        self.shutdown_started = None
        self.cancelled_backfill_tasks = 0

    def set_result_tracking(self, protected):
        with self.lock:
            self.protected = dict(protected)
            if self.ws and self.universe_at:
                self._select_symbols(datetime.now(timezone.utc))

    def _select_symbols(self, now):
        """Called under lock. Existing protected subscriptions survive discovery churn."""
        if self.stop_event.is_set():
            return
        protected = {t: r for t, r in self.protected.items() if instant(r["tracking_protected_until"]) >= now}
        desired = {t: e for t, e in self.selected.items() if t in protected}
        for ticker, row in protected.items():
            if len(desired) < self.config.max_ws_symbols and row.get("exchange"):
                desired.setdefault(ticker, row["exchange"])
        ordered = allocate_slots(self.candidates, len(self.candidates))
        for row in ordered:
            if len(desired) < self.config.max_ws_symbols:
                desired.setdefault(row["ticker"], row["exchange"])
        for ticker in self.tracked:
            if len(desired) < self.config.max_ws_symbols and ticker in self.catalog:
                desired.setdefault(ticker, self.catalog[ticker]["exchange"])
        unavailable = ({r["ticker"] for r in ordered} | set(protected)) - set(desired)
        for ticker in sorted(unavailable - self.slot_unavailable):
            self.logger.warning("tracking_slot_unavailable ticker=%s", ticker,
                                extra=event("tracking_slot_unavailable_count"))
        self.slot_unavailable = unavailable
        self.selected = desired
        self.ws.set_symbols(dict(desired))

    def backfill_eligible(self, ticker):
        if not self.config.rvol_backfill_enabled:
            return False
        quote, received = self.quotes.get(ticker, ({}, None))
        for threshold, field, price in ((self.config.rvol_backfill_min_day_volume, "acc_trde_qty", False),
                                        (self.config.rvol_backfill_min_price, "cur_prc", True)):
            if threshold > 0:
                if not received or instant(received).astimezone(ET).date() != datetime.now(ET).date():
                    return False
                value = number(quote.get(field), price=price)
                if value is None or value < threshold:
                    return False
        return True

    def set_logger(self, logger):
        self.logger = logger

    def request_stop(self):
        if self.shutdown_started is None:
            self.shutdown_started = time.monotonic()
        self.stop_event.set()

    def _raw(self, kind, ticker, received, payload):
        if self.rest:
            payload = self.rest.sanitize(payload)
        try:
            self.raw_queue.put_nowait((kind, ticker, received, payload))
        except queue.Full:
            self.raw_dropped += 1
            self.logger.info("kiwoom_raw_dropped", extra=event("raw_dropped_count"))
            if self.raw_dropped == 1 or self.raw_dropped % 1000 == 0:
                self.logger.error("kiwoom_data_gap raw_queue_overflow dropped=%s", self.raw_dropped)

    def _flush_raw(self):
        rows = []
        for _ in range(5000):
            try:
                rows.append(self.raw_queue.get_nowait())
            except queue.Empty:
                break
        if rows:
            self.cache.raw_many(rows)

    def connect(self):
        self.collector_lock = CollectorLock()
        self.collector_lock.acquire()
        self.calendar = self.calendar or ExchangeCalendar()
        self.cache = KiwoomCache(self.config.cache_path)
        self.catalog = self.cache.catalog()
        self.rest = self.rest_factory(self.config, self.stop_event, self.logger, self._raw)
        self.rest.token()
        self.ws = self.ws_factory(self.rest, self.config, self.stop_event, self._raw, self.logger)
        self.ws.start()
        self.io_thread = threading.Thread(target=self._io_loop, name="kiwoom-rest-cache", daemon=False)
        self.io_thread.start()

    def _check_stop(self):
        if self.stop_event.is_set():
            raise StopRequested("Kiwoom stopped")

    def _refresh_universe(self):
        self._check_stop()
        pages = []
        for source, api_id in SOURCES.items():
            self._check_stop()
            body = self.rest.example(api_id)
            if "trde_qty_tp" in body:
                body["trde_qty_tp"] = "0"
            if source == "5m_spike":
                body.update(flu_tp="1", tm_tp="1", tm="5", stk_cnd="0")
            cursor, offset = None, 0
            for _ in range(self.config.candidate_pages):
                self._check_stop()
                payload, next_cursor, received = self.rest.request(api_id, body, cursor)
                rows = payload.get("result_list", [])
                pages.append((source, rows, received, offset))
                offset += len(rows)
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
        candidates = merge_candidates(pages, self.catalog, self.config.stock_type_policy)
        with self.lock:
            self.candidates = candidates  # all 4 sources succeeded; partial failure never replaces universe
            self.universe_at = datetime.now(timezone.utc)
            self._select_symbols(self.universe_at)
        self._raw("UNIVERSE", None, utc(self.universe_at), {"candidates": list(candidates.values())})

    def _chart_step(self, ticker, exchange, session, historical, now):
        if self.stop_event.is_set():
            return
        key = (ticker, exchange, session.date, historical)
        if self.retry_at.get(key, 0) > time.monotonic():
            return
        if historical and self.cache.is_complete(ticker, exchange, session.date):
            return
        bucket = now.replace(second=0, microsecond=0)
        if not historical and self.finished.get(key) == bucket:
            return
        if key not in self.jobs:
            stop_at = instant(session.open_utc)
            if not historical:
                old = normalize_bars(self.cache.rows(ticker, exchange, session.date), session, now.replace(second=0, microsecond=0))
                if old and contiguous(old, session, instant(old[-1].end_utc)):
                    stop_at = max(stop_at, instant(old[-1].start_utc)-timedelta(minutes=2))
            self.jobs[key] = {"cursor": None, "seen": set(), "pages": 0, "stop_at": stop_at, "bucket": bucket}
            self.logger.info("kiwoom_backfill_start ticker=%s day=%s historical=%s", ticker, session.date, historical)
        job = self.jobs[key]
        try:
            self._check_stop()
            payload, cursor, received = self.rest.minutes_page(ticker, exchange, session.date, job["cursor"])
            rows = payload.get("result_list", [])
            self.cache.put_minutes(ticker, exchange, rows, received)
            job["pages"] += 1
            target = job["stop_at"].astimezone(ET).strftime("%Y%m%d%H%M%S")
            reached = any(str(r.get("cntr_tm", "z")) <= target for r in rows)
            exhausted = not rows or not cursor or cursor in job["seen"] or job["pages"] >= self.config.max_chart_pages
            if reached or exhausted:
                complete = self.cache.mark_complete(ticker, exchange, session, received) if historical else False
                if historical and not complete:
                    self.retry_at[key] = time.monotonic()+900
                    self.logger.warning("kiwoom_backfill_incomplete ticker=%s day=%s", ticker, session.date,
                                        extra=event("backfill_incomplete"))
                else:
                    self.logger.info("kiwoom_backfill_complete ticker=%s day=%s historical=%s", ticker, session.date, historical,
                                     extra=event("backfill_complete", int(historical)))
                self.finished[key] = job["bucket"]
                self.jobs.pop(key)
            else:
                job["seen"].add(cursor)
                job["cursor"] = cursor
        except StopRequested:
            return  # close() cancels and counts the remaining job after IO has stopped
        except RestFailure as exc:
            self.jobs.pop(key, None)
            self.retry_at[key] = exc.retry_after
            if not exc.suppressed:
                category = 'deterministic_unavailable' if exc.failure_class == 'deterministic' else 'transient_error' if exc.failure_class == 'transient' else 'unclassified_error'
                self.logger.warning('kiwoom_backfill_%s ticker=%s day=%s', category, ticker, session.date,
                                    extra=event('backfill_'+category, int(historical)))
        except ProviderError:
            self.jobs.pop(key, None)
            self.retry_at[key] = time.monotonic()+60
            self.logger.error("kiwoom_backfill_error ticker=%s", ticker, extra=event("backfill_error"))

    def _io_loop(self):
        refresh_at = catalog_at = 0.0
        foreground = background = 0
        while not self.stop_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                day = now.astimezone(ET).date().isoformat()
                if time.monotonic() >= catalog_at:
                    for exchange in ("ND", "NY", "NA"):
                        self._check_stop()
                        payload, _, received = self.rest.request("usa10099", {"stex_tp": exchange})
                        self.cache.put_catalog(payload.get("list", []), received)
                    self.catalog = self.cache.catalog()
                    catalog_at = time.monotonic()+86400
                self._check_stop()
                if time.monotonic() >= refresh_at:
                    self._refresh_universe()
                    refresh_at = time.monotonic()+self.config.candidate_refresh_seconds
                with self.lock:
                    selected = list(self.selected.items())
                    tracked = tuple(self.tracked)
                # Follow-up daily data isn't restricted by the WS slot cap.
                daily_only = [(t, self.catalog[t]["exchange"]) for t in tracked if t in self.catalog and t not in dict(selected)]
                work = selected + daily_only
                if work:
                    ticker, exchange = work[foreground % len(work)]
                    foreground += 1
                    if self.quote_days.get(ticker) != day or ((self.config.rvol_backfill_min_day_volume > 0 or
                            self.config.rvol_backfill_min_price > 0) and
                            (now-instant(self.quotes.get(ticker, ({}, utc(now)))[1])).total_seconds() > 60):
                        self._check_stop()
                        try:
                            payload, _, received = self.rest.quote(ticker, exchange)
                            with self.lock:
                                self.quotes[ticker] = (payload, received)
                            self.quote_days[ticker] = day
                        except RestFailure:
                            pass
                    if time.monotonic()-self.daily_updated.get(ticker, 0) > 900:
                        self._check_stop()
                        try:
                            payload, _, received = self.rest.daily(ticker, exchange, day)
                            self.cache.put_daily(ticker, exchange, payload.get("result_list", []), received)
                            self._check_stop()
                            self.rest.daily_prices(ticker, exchange, day)
                            self.daily_updated[ticker] = time.monotonic()
                        except RestFailure:
                            pass
                    session = self.calendar.session(day)
                    if ticker in dict(selected) and session and instant(session.open_utc) <= now <= instant(session.close_utc):
                        self._chart_step(ticker, exchange, session, False, now)
                    bt, be = selected[background % len(selected)] if selected else (None, None)
                    background += 1
                    if bt and not self.backfill_eligible(bt):
                        self.logger.info('kiwoom_backfill_skipped ticker=%s', bt, extra=event('backfill_skipped'))
                    if bt and self.backfill_eligible(bt):
                        for prior in self.calendar.history(day, self.config.rvol_lookback_days):
                            self._check_stop()
                            if self.retry_at.get((bt, be, prior.date, True), 0) > time.monotonic():
                                continue
                            if not self.cache.is_complete(bt, be, prior.date):
                                self._chart_step(bt, be, prior, True, now)
                                break
                self.error = None
                self._flush_raw()
                self.stop_event.wait(.25 if work else 1)
            except StopRequested:
                break
            except RestFailure:
                # Already counted once per actual request by the REST layer.
                self.stop_event.wait(5)
            except Exception as exc:
                self.error = type(exc).__name__  # no payload/credential exception text
                self.logger.error("kiwoom_rest_error scheduler type=%s", self.error,
                                  extra=event("db_error_count" if self.error.endswith("Error") and self.error in {"OperationalError", "DatabaseError", "IntegrityError"} else "worker_error_count"))
                try:
                    self._flush_raw()
                except Exception:
                    self.logger.error("db_error kiwoom_raw_cache", extra=event("db_error_count"))
                self.stop_event.wait(5)

    def fetch(self, tracked_tickers=()):
        now = datetime.now(timezone.utc)
        stamp, day = utc(now), now.astimezone(ET).date().isoformat()
        with self.lock:
            self.tracked = tuple(tracked_tickers)
            candidates, selected, quotes, universe_at = dict(self.candidates), dict(self.selected), dict(self.quotes), self.universe_at
        if universe_at is None:
            raise UniverseWarmingUp("Kiwoom universe warming up")
        if (now-universe_at).total_seconds() > max(180, self.config.candidate_refresh_seconds*3):
            raise UniverseStale("Kiwoom universe stale")
        latest, active, ws_gap, connected = self.ws.snapshot()
        session = self.calendar.session(day)
        history = self.calendar.history(day, self.config.rvol_lookback_days)
        sessions = self.calendar.sessions((now.astimezone(ET).date()-timedelta(days=120)).isoformat(),
                                          (now.astimezone(ET).date()+timedelta(days=14)).isoformat())
        observations, closes = [], []
        reasons = {t: "tracking_slot_unavailable" for t in self.slot_unavailable}
        for ticker in tracked_tickers:
            if ticker not in selected:
                reasons.setdefault(ticker, "not_subscribed")
        for ticker, exchange in selected.items():
            quote, quote_received = quotes.get(ticker, ({}, None))
            if session and instant(session.open_utc) <= now <= instant(session.close_utc):
                live = latest.get(ticker)
                if not live or not connected or ticker not in active:
                    reasons[ticker] = "ws_gap" if not connected else "not_subscribed"
                    continue
                delay = (now-instant(live["received_utc"])).total_seconds()
                if delay > self.config.max_data_delay_seconds or instant(live["received_utc"]) < instant(session.open_utc):
                    self.logger.warning("kiwoom_stale_data ticker=%s age_seconds=%.1f", ticker, delay, extra=event("stale_count"))
                    reasons[ticker] = "stale_price"
                    continue
                price = number(live["values"].get("10"), price=True)
                previous = number(quote.get("base_close_pric"), price=True)
                if not quote_received or instant(quote_received).astimezone(ET).date().isoformat() != day:
                    previous = None
                if not price or price <= 0:
                    continue
                cutoff = completed_cutoff(now, session, self.config.bar_finalization_seconds)
                bars = normalize_bars(self.cache.rows(ticker, exchange, day), session, cutoff)
                elapsed = int((cutoff-instant(session.open_utc)).total_seconds())
                hv = []
                eligible = self.backfill_eligible(ticker)
                for prior in history if eligible else ():
                    if not self.cache.is_complete(ticker, exchange, prior.date):
                        continue
                    at = instant(prior.open_utc)+timedelta(seconds=elapsed)
                    if at > instant(prior.close_utc):
                        continue
                    prior_bars = normalize_bars(self.cache.rows(ticker, exchange, prior.date), prior, at)
                    if contiguous(prior_bars, prior, at):
                        hv.append(HistoricalVolume(prior.date, elapsed, sum(b.volume for b in prior_bars)))
                candidate = candidates.get(ticker, {"sources": [], "source_ranks": {}, "source_received": {}, "type": "unknown"})
                history_incomplete = any(k[0] == ticker and k[3] and v > time.monotonic() for k, v in list(self.retry_at.items()))
                metadata = {"provider": "kiwoom", "provider_mode": "real", "exchange": exchange,
                    "candidate_sources": candidate["sources"], "source_ranks": candidate["source_ranks"],
                    "source_received": candidate["source_received"], "symbol_type": candidate["type"],
                    "symbol_type_evidence": candidate.get("symbol_type_evidence", {}),
                    "rvol_status": "not_eligible" if not eligible else "incomplete_history" if history_incomplete else "warming",
                    "rvol_basis": "completed_1m", "bar_cutoff_utc": utc(cutoff), "bar_cutoff_et": cutoff.astimezone(ET).isoformat(),
                    "rvol_cutoff_et": cutoff.astimezone(ET).isoformat(), "bar_label_policy": POLICY,
                    "ws_gap_detected": ws_gap or bool(self.raw_dropped), "price_received_utc": live["received_utc"],
                    "provider_latency_ms": None, "price_age_ms": delay*1000, "rest_received_utc": quote_received,
                    "fe_raw": live["values"], "raw_records_db": "market_radar_cache/kiwoom_v2.db",
                    "raw_dropped": self.raw_dropped, "calendar_source": self.calendar.source}
                observations.append(Observation(ticker, stamp, price, previous, session, bars, tuple(hv),
                    tuple(s.date for s in history), ticker in candidates and not candidate.get("excluded_symbol_type"), metadata))
        for ticker in set(selected) | set(tracked_tickers):
            exchange = selected.get(ticker) or self.catalog.get(ticker, {}).get("exchange")
            if not exchange:
                continue
            for row in self.cache.daily(ticker, exchange):
                # Only past dates; today is never promoted to a final EOD from a live candle.
                if row["session_date"] >= day or (sessions and row["session_date"] < sessions[0].date):
                    continue
                s = self.calendar.session(row["session_date"])
                value = number(json.loads(row["raw_json"]).get("cur_prc"), price=True)
                if s and value and value > 0:
                    closes.append(DailyClose(ticker, s.date, s.close_utc, value, "kiwoom:usa06012", "provisional"))
        return MarketBatch(stamp, tuple(observations), sessions, tuple(closes), "kiwoom:four_sources:subscribed_subset",
            {"candidates": list(candidates.values()), "selected": selected, "active": active, "universe_received_utc": utc(universe_at),
             "ws_connected": connected, "ws_gap_detected": ws_gap, "raw_dropped": self.raw_dropped,
             "missing_reasons": reasons,
             "tracking_counts": {"protected_count": sum(instant(r["tracking_protected_until"]) >= now for r in self.protected.values()),
                "discovery_count": len(candidates), "active_subscription_count": len(active),
                "slot_saturation_pct": 100*len(active)/self.config.max_ws_symbols,
                "tracking_slot_unavailable_count": len(self.slot_unavailable)},
             "bar_policy": POLICY, "calendar_source": self.calendar.source})

    def connection_status(self):
        """Acknowledged WS state only; no IO or historical gauge mutation."""
        if self._closed or self.ws is None:
            return {"connected": False, "active_ws": 0}
        with self.ws.lock:
            connected = self.ws.connected
            return {"connected": connected, "active_ws": len(self.ws.active) if connected else 0}

    def close(self):
        if self._closed:
            return
        self.request_stop()
        if self.io_thread:
            self.io_thread.join(timeout=30)
            if self.io_thread.is_alive():
                raise RuntimeError("Kiwoom REST shutdown timeout")
        cancelled = sum(bool(k[3]) for k in self.jobs)
        self.cancelled_backfill_tasks += cancelled
        self.jobs.clear()
        self.logger.info('shutdown_backfill_cancelled count=%s elapsed_ms=%.3f', cancelled,
                         (time.monotonic()-self.shutdown_started)*1000, extra=event('cancelled_backfill_tasks', cancelled))
        self.logger.info('shutdown_ws_closing elapsed_ms=%.3f', (time.monotonic()-self.shutdown_started)*1000)
        if self.ws:
            self.ws.close()
        if self.cache:
            while not self.raw_queue.empty():
                self._flush_raw()
            self.cache.raw("STOP", None, utc(datetime.now(timezone.utc)), {"raw_dropped": self.raw_dropped})
            self.cache.close()
        if self.rest:
            self.rest.close()
        if self.collector_lock:
            self.collector_lock.close()
        self._closed = True
