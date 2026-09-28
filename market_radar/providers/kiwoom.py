"""Kiwoom research adapter. Slot priority is operational, never an investment score."""
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import logging
import queue
import threading
import time

from ..models import DailyClose, ET, HistoricalVolume, MarketBatch, Observation, instant, utc
from ..provider import ProviderError
from .kiwoom_cache import KiwoomCache, completed_cutoff, contiguous, normalize_bars, number, POLICY
from .kiwoom_calendar import ExchangeCalendar
from .kiwoom_rest import KiwoomRest
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
            # Official mkgb/upgb/isEtf value enums aren't confirmed. Preserve, don't guess by suffix/name.
            if ticker in catalog:
                entry["raw_type_fields"] = {k: v for k, v in json.loads(catalog[ticker]["raw_json"]).items()
                                            if k in {"mkgb", "upgb", "isEtf"}}
    return merged


def allocate_slots(candidates, limit):
    # Deterministic operational ordering; not a buy/ranking score.
    return sorted((c for c in candidates.values() if not c.get("exchange_ambiguous")), key=lambda c: (
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

    def set_logger(self, logger):
        self.logger = logger

    def request_stop(self):
        self.stop_event.set()

    def _raw(self, kind, ticker, received, payload):
        if self.rest:
            payload = self.rest.sanitize(payload)
        try:
            self.raw_queue.put_nowait((kind, ticker, received, payload))
        except queue.Full:
            self.raw_dropped += 1
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
        self.cache = KiwoomCache(self.config.data_dir / "market_radar_cache" / "kiwoom_v1.db")
        self.catalog = self.cache.catalog()
        self.rest = self.rest_factory(self.config, self.stop_event, self.logger, self._raw)
        self.rest.token()
        self.ws = self.ws_factory(self.rest, self.config, self.stop_event, self._raw, self.logger)
        self.ws.start()
        self.io_thread = threading.Thread(target=self._io_loop, name="kiwoom-rest-cache", daemon=False)
        self.io_thread.start()

    def _refresh_universe(self):
        pages = []
        for source, api_id in SOURCES.items():
            body = self.rest.example(api_id)
            if "trde_qty_tp" in body:
                body["trde_qty_tp"] = "0"
            if source == "5m_spike":
                body.update(flu_tp="1", tm_tp="1", tm="5", stk_cnd="0")
            cursor, offset = None, 0
            for _ in range(self.config.candidate_pages):
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
            selected = allocate_slots(candidates, self.config.max_ws_symbols)
            self.selected = {r["ticker"]: r["exchange"] for r in selected}
            # Pending follow-ups can use spare WS slots, but cannot displace prioritized candidates.
            for ticker in self.tracked:
                if len(self.selected) >= self.config.max_ws_symbols:
                    break
                if ticker not in self.selected and ticker in self.catalog:
                    self.selected[ticker] = self.catalog[ticker]["exchange"]
            self.universe_at = datetime.now(timezone.utc)
            desired = dict(self.selected)
        self.ws.set_symbols(desired)

    def _chart_step(self, ticker, exchange, session, historical, now):
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
                    self.logger.warning("kiwoom_backfill_error incomplete ticker=%s day=%s", ticker, session.date)
                else:
                    self.logger.info("kiwoom_backfill_complete ticker=%s day=%s historical=%s", ticker, session.date, historical)
                self.finished[key] = job["bucket"]
                self.jobs.pop(key)
            else:
                job["seen"].add(cursor)
                job["cursor"] = cursor
        except ProviderError:
            self.jobs.pop(key, None)
            self.retry_at[key] = time.monotonic()+60
            self.logger.warning("kiwoom_backfill_error ticker=%s", ticker)

    def _io_loop(self):
        refresh_at = catalog_at = 0.0
        foreground = background = 0
        while not self.stop_event.is_set():
            try:
                now = datetime.now(timezone.utc)
                day = now.astimezone(ET).date().isoformat()
                if time.monotonic() >= catalog_at:
                    for exchange in ("ND", "NY", "NA"):
                        payload, _, received = self.rest.request("usa10099", {"stex_tp": exchange})
                        self.cache.put_catalog(payload.get("list", []), received)
                    self.catalog = self.cache.catalog()
                    catalog_at = time.monotonic()+86400
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
                    if self.quote_days.get(ticker) != day:
                        payload, _, received = self.rest.quote(ticker, exchange)
                        with self.lock:
                            self.quotes[ticker] = (payload, received)
                        self.quote_days[ticker] = day
                    if time.monotonic()-self.daily_updated.get(ticker, 0) > 900:
                        payload, _, received = self.rest.daily(ticker, exchange, day)
                        self.cache.put_daily(ticker, exchange, payload.get("result_list", []), received)
                        # Independent daily-price endpoint retained for comparison, not presumed official close.
                        self.rest.daily_prices(ticker, exchange, day)
                        self.daily_updated[ticker] = time.monotonic()
                    session = self.calendar.session(day)
                    if ticker in dict(selected) and session and instant(session.open_utc) <= now <= instant(session.close_utc):
                        self._chart_step(ticker, exchange, session, False, now)
                    bt, be = selected[background % len(selected)] if selected else (None, None)
                    background += 1
                    if bt:
                        for prior in self.calendar.history(day, self.config.rvol_lookback_days):
                            if self.retry_at.get((bt, be, prior.date, True), 0) > time.monotonic():
                                continue
                            if not self.cache.is_complete(bt, be, prior.date):
                                self._chart_step(bt, be, prior, True, now)
                                break
                self.error = None
                self._flush_raw()
                self.stop_event.wait(.25 if work else 1)
            except Exception as exc:
                self.error = type(exc).__name__  # no payload/credential exception text
                self.logger.warning("kiwoom_rest_error scheduler type=%s", self.error)
                try:
                    self._flush_raw()
                except Exception:
                    self.logger.error("db_error kiwoom_raw_cache")
                self.stop_event.wait(5)

    def fetch(self, tracked_tickers=()):
        now = datetime.now(timezone.utc)
        stamp, day = utc(now), now.astimezone(ET).date().isoformat()
        with self.lock:
            self.tracked = tuple(tracked_tickers)
            candidates, selected, quotes, universe_at = dict(self.candidates), dict(self.selected), dict(self.quotes), self.universe_at
        if universe_at is None or (now-universe_at).total_seconds() > max(180, self.config.candidate_refresh_seconds*3):
            raise ProviderError("Kiwoom universe warming up/stale; retaining prior batch")
        latest, active, ws_gap, connected = self.ws.snapshot()
        session = self.calendar.session(day)
        history = self.calendar.history(day, self.config.rvol_lookback_days)
        sessions = self.calendar.sessions((now.astimezone(ET).date()-timedelta(days=120)).isoformat(),
                                          (now.astimezone(ET).date()+timedelta(days=14)).isoformat())
        observations, closes = [], []
        for ticker, exchange in selected.items():
            quote, quote_received = quotes.get(ticker, ({}, None))
            if session and instant(session.open_utc) <= now <= instant(session.close_utc):
                live = latest.get(ticker)
                if not live or not connected or ticker not in active:
                    continue
                delay = (now-instant(live["received_utc"])).total_seconds()
                if delay > self.config.max_data_delay_seconds or instant(live["received_utc"]) < instant(session.open_utc):
                    self.logger.warning("kiwoom_stale_data ticker=%s age_seconds=%.1f", ticker, delay)
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
                for prior in history:
                    if not self.cache.is_complete(ticker, exchange, prior.date):
                        continue
                    at = instant(prior.open_utc)+timedelta(seconds=elapsed)
                    if at > instant(prior.close_utc):
                        continue
                    prior_bars = normalize_bars(self.cache.rows(ticker, exchange, prior.date), prior, at)
                    if contiguous(prior_bars, prior, at):
                        hv.append(HistoricalVolume(prior.date, elapsed, sum(b.volume for b in prior_bars)))
                candidate = candidates.get(ticker, {"sources": [], "source_ranks": {}, "source_received": {}, "type": "type_unknown"})
                metadata = {"provider": "kiwoom", "provider_mode": "real", "exchange": exchange,
                    "candidate_sources": candidate["sources"], "source_ranks": candidate["source_ranks"],
                    "source_received": candidate["source_received"], "symbol_type": candidate["type"],
                    "rvol_basis": "completed_1m", "bar_cutoff_utc": utc(cutoff), "bar_cutoff_et": cutoff.astimezone(ET).isoformat(),
                    "rvol_cutoff_et": cutoff.astimezone(ET).isoformat(), "bar_label_policy": POLICY,
                    "ws_gap_detected": ws_gap or bool(self.raw_dropped), "price_received_utc": live["received_utc"],
                    "provider_latency_ms": None, "price_age_ms": delay*1000, "rest_received_utc": quote_received,
                    "fe_raw": live["values"], "raw_records_db": "market_radar_cache/kiwoom_v1.db",
                    "raw_dropped": self.raw_dropped, "calendar_source": self.calendar.source}
                observations.append(Observation(ticker, stamp, price, previous, session, bars, tuple(hv),
                    tuple(s.date for s in history), ticker in candidates, metadata))
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
             "bar_policy": POLICY, "calendar_source": self.calendar.source})

    def close(self):
        self.stop_event.set()
        if self.io_thread:
            self.io_thread.join(timeout=30)
            if self.io_thread.is_alive():
                raise RuntimeError("Kiwoom REST shutdown timeout")
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
