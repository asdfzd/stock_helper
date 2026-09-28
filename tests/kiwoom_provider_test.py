"""Offline Kiwoom contract/policy tests. Never load real credentials or contact APIs."""
import csv
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from market_radar.config import CONFIG
from market_radar.models import Bar, DailyClose, HistoricalVolume, MarketBatch, Observation, Session, instant, utc
from market_radar.calculations import calculate, rank
from market_radar.engine import RadarEngine
from market_radar.storage import RadarStore
from market_radar.provider import ProviderError, RateLimitError
from market_radar.providers.kiwoom import KiwoomProvider, allocate_slots, merge_candidates
from market_radar.providers.kiwoom_cache import KiwoomCache, POLICY, completed_cutoff, contiguous, normalize_bars, number
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.providers.kiwoom_rest import APIS, KiwoomRest, RateLimiter
from market_radar.providers.kiwoom_ws import KiwoomWebSocket

SESSION = Session("2026-09-28", "2026-09-28T13:30:00+00:00", "2026-09-28T20:00:00+00:00")
LOGGER = logging.getLogger("kiwoom-test")


def raw_bars(day="2026-09-28", minutes=10):
    start = datetime.fromisoformat(day+"T09:30:00")
    return [dict(cntr_tm=(start+timedelta(minutes=i)).strftime("%Y%m%d%H%M%S"), bus_dt=day.replace("-", ""),
                 open_pric=str(100+i), high_pric=str(102+i), low_pric=str(99+i), cur_prc=str(101+i), trde_qty="100")
            for i in range(minutes)]


def observation(minute=10, seconds=30, history_count=14):
    cutoff = instant(SESSION.open_utc)+timedelta(minutes=minute)
    at = cutoff+timedelta(seconds=seconds)
    dates = tuple(s.date for s in ExchangeCalendar().history(SESSION.date, 14))
    metadata = dict(rvol_basis="completed_1m", bar_cutoff_utc=utc(cutoff), bar_cutoff_et=cutoff.isoformat(),
        rvol_cutoff_et=cutoff.isoformat(), exchange="ND", candidate_sources=["day_change", "5m_spike"],
        source_ranks={"day_change": 1}, bar_label_policy=POLICY, ws_gap_detected=True)
    return Observation("TEST", utc(at), 200, 100, SESSION,
        normalize_bars(raw_bars(minutes=minute), SESSION, cutoff),
        tuple(HistoricalVolume(day, minute*60, minute*200) for day in dates[:history_count]), dates, True, metadata)


class ParsingAndMetricsTests(unittest.TestCase):
    def test_signed_prices_and_percent(self):
        self.assertEqual(number("-339.7600", price=True), 339.76)
        self.assertEqual(number("-0.38"), -.38)
        self.assertIsNone(number(""))
        with self.assertRaises(ValueError): number("nan")

    def test_completed_bar_and_extended_exclusion(self):
        rows = raw_bars(minutes=11)+[dict(raw_bars()[0], cntr_tm="20260928274400")]
        cutoff = completed_cutoff(instant("2026-09-28T13:40:30+00:00"), SESSION)
        bars = normalize_bars(rows, SESSION, cutoff)
        self.assertEqual(len(bars), 10)
        self.assertTrue(contiguous(bars, SESSION, cutoff))

    def test_cached_partial_never_becomes_complete_by_waiting(self):
        row = dict(raw_bars()[0], _received_utc="2026-09-28T13:30:20+00:00")
        self.assertEqual(normalize_bars([row], SESSION, instant("2026-09-28T14:00:00+00:00")), ())

    def test_cutoff_fixed_between_snapshots_and_grace(self):
        a = completed_cutoff(instant("2026-09-28T13:40:05+00:00"), SESSION)
        b = completed_cutoff(instant("2026-09-28T13:40:35+00:00"), SESSION)
        self.assertEqual(a, b)
        self.assertEqual(completed_cutoff(instant("2026-09-28T13:40:01+00:00"), SESSION).minute, 39)

    def test_returns_use_final_close_not_live_price(self):
        row = calculate(observation(), CONFIG)
        self.assertAlmostEqual(row["return_1m"], (110/109-1)*100)
        self.assertAlmostEqual(row["return_5m"], (110/105-1)*100)
        self.assertEqual(row["price"], 200)
        self.assertEqual(row["day_change_pct"], 100)

    def test_opening_range_one_and_five(self):
        row = calculate(observation(), CONFIG)
        self.assertEqual((row["opening_1m_high"], row["opening_1m_low"]), (102, 99))
        self.assertEqual((row["opening_5m_high"], row["opening_5m_low"]), (106, 99))
        self.assertIsNone(calculate(observation(minute=4), CONFIG)["opening_5m_high"])

    def test_completed_minute_rvol_and_half_minute_same(self):
        a = calculate(observation(seconds=3), CONFIG)
        b = calculate(observation(seconds=33), CONFIG)
        self.assertEqual(a["rvol"], .5)
        self.assertEqual(a["rvol"], b["rvol"])
        self.assertEqual(a["rvol_basis"], "completed_1m")

    def test_incomplete_history_null(self):
        self.assertIsNone(calculate(observation(history_count=13), CONFIG)["rvol"])

    def test_missing_session_bar_hod_and_rvol_null(self):
        o = observation()
        row = calculate(replace(o, bars=o.bars[1:]), CONFIG)
        self.assertIsNone(row["hod"])
        self.assertIsNone(row["rvol"])
        self.assertEqual(row["coverage_complete"], 0)

    def test_vwap_null_even_if_notional_supplied(self):
        o = observation()
        row = calculate(replace(o, bars=tuple(replace(b, notional=10000) for b in o.bars)), CONFIG)
        self.assertIsNone(row["vwap"])
        self.assertIsNone(row["above_vwap"])
        self.assertEqual(rank([row])[0]["emerging_rank"], 1)

    def test_calendar_holiday_early_close_dst(self):
        cal = ExchangeCalendar()
        self.assertIsNone(cal.session("2026-11-26"))
        self.assertEqual(cal.session("2026-11-27").close_utc, "2026-11-27T18:00:00+00:00")
        self.assertEqual(cal.session("2026-09-28").open_utc, "2026-09-28T13:30:00+00:00")


class UniverseTests(unittest.TestCase):
    def test_merge_dedup_sources_ranks_unknown_type(self):
        pages = [("day_change", [{"stk_cd": "AAA", "stex_tp": "ND"}], "one", 0),
                 ("5m_spike", [{"stk_cd": "AAA", "stex_tp": "ND"}], "two", 0)]
        result = merge_candidates(pages, {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result["AAA"]["sources"], ["day_change", "5m_spike"])
        self.assertEqual(result["AAA"]["source_received"]["5m_spike"], "two")
        self.assertEqual(result["AAA"]["type"], "type_unknown")

    def test_operational_slots_cap_and_priority(self):
        rows = [{"stk_cd": f"T{i:03}", "stex_tp": "ND"} for i in range(240)]
        merged = merge_candidates([("volume", rows, "now", 0), ("day_change", [rows[-1]], "now", 0)], {})
        slots = allocate_slots(merged, 200)
        self.assertEqual(len(slots), 200)
        self.assertEqual(slots[0]["ticker"], "T239")

    def test_exchange_conflict_not_subscribed(self):
        merged = merge_candidates([("volume", [{"stk_cd": "X", "stex_tp": "ND"}, {"stk_cd": "X", "stex_tp": "NY"}], "now", 0)], {})
        self.assertEqual(allocate_slots(merged, 200), [])

    def test_refresh_failure_preserves_universe(self):
        p = KiwoomProvider(CONFIG)
        p.candidates = {"OLD": {}}
        p.rest = Mock()
        p.rest.example.return_value = {}
        p.rest.request.side_effect = ProviderError("offline")
        with self.assertRaises(ProviderError): p._refresh_universe()
        self.assertEqual(p.candidates, {"OLD": {}})


class FakeClock:
    def __init__(self): self.now = 0; self.waits = []
    def wait(self, seconds): self.waits.append(seconds); self.now += seconds; return False
    def is_set(self): return False


class RestTests(unittest.TestCase):
    def test_rate_limits_normal_peak_and_list(self):
        clock = FakeClock()
        gate = RateLimiter(CONFIG, clock, clock=lambda: clock.now, wall=lambda: instant("2026-09-28T18:00:00+00:00"))
        gate.acquire("x");gate.acquire("x")
        self.assertGreaterEqual(clock.now, 1/3)
        gate.wall = lambda: instant("2026-09-29T00:00:00+00:00")
        gate.acquire("x");gate.acquire("x")
        self.assertAlmostEqual(clock.waits[-1], .5)
        for _ in range(5): gate.acquire("usa10099")
        self.assertGreaterEqual(clock.waits[-1], 58)

    def test_forbidden_api_never_calls_network(self):
        http = Mock()
        rest = KiwoomRest(CONFIG, threading.Event(), session=http)
        for tr in ("kt10000", "ust10000", "usa21670", "F4", "F5"):
            with self.assertRaises(ProviderError): rest.request(tr, {})
        http.post.assert_not_called()
        self.assertTrue(all('/ordr' not in v['path'] and '/acnt' not in v['path'] for v in APIS.values()))

    def test_rest_retry_and_secret_redaction(self):
        http, wait = Mock(), FakeClock()
        rest = KiwoomRest(CONFIG, wait, session=http)
        rest._token, rest._credentials = "TEST_TOKEN", ("TEST_KEY", "TEST_SECRET")
        rest.limiter = Mock()
        rejected = Mock(status_code=429)
        accepted = Mock(status_code=200, headers={})
        accepted.json.return_value = {"return_code": 0, "cur_prc": "-20", "token": "TEST_TOKEN"}
        http.post.side_effect = [rejected, accepted]
        result, _, _ = rest.quote("TEST", "ND")
        self.assertEqual(result, {"cur_prc": "-20"})
        self.assertIn(2, wait.waits)
        safe = json.dumps(rest.sanitize({"anything": "TEST_KEY TEST_SECRET TEST_TOKEN", "token": "x"}))
        for value in ("TEST_KEY", "TEST_SECRET", "TEST_TOKEN"): self.assertNotIn(value, safe)

    def test_error_budget_bounded(self):
        http = Mock()
        http.post.return_value = Mock(status_code=429)
        rest = KiwoomRest(CONFIG, FakeClock(), session=http)
        rest._token = "TEST_TOKEN"
        rest.limiter = Mock()
        with self.assertRaises(RateLimitError): rest.quote("TEST", "ND")
        self.assertEqual(http.post.call_count, 3)


class SocketTests(unittest.TestCase):
    def make_ws(self, stop=None, connector=None):
        return KiwoomWebSocket(Mock(token=Mock(return_value="TEST_ONLY")), CONFIG, stop or threading.Event(), Mock(), LOGGER, connector)

    def test_limit_and_reg_remove_ack(self):
        ws = self.make_ws()
        with self.assertRaises(ValueError): ws.set_symbols({str(i): "ND" for i in range(201)})
        socket = Mock()
        socket.recv.side_effect = [json.dumps({"trnm": "REG", "return_code": 0}),
                                  json.dumps({"trnm": "REMOVE", "return_code": 0}),
                                  json.dumps({"trnm": "REG", "return_code": 0})]
        ws.set_symbols({"A": "ND"});ws.sync_symbols(socket)
        self.assertEqual(ws.active, {"A": "ND"})
        ws.set_symbols({"B": "NY"});ws.sync_symbols(socket)
        self.assertEqual(ws.active, {"B": "NY"})
        packets = [json.loads(c.args[0]) for c in socket.send.call_args_list]
        self.assertEqual([p["trnm"] for p in packets], ["REG", "REMOVE", "REG"])
        self.assertEqual(packets[0]["refresh"], "1")

    def test_failed_ack_not_active(self):
        ws = self.make_ws();ws.set_symbols({"A": "ND"})
        socket = Mock();socket.recv.return_value = '{"trnm":"REG","return_code":1}'
        with self.assertRaises(RuntimeError): ws.sync_symbols(socket)
        self.assertFalse(ws.active)

    def test_ping_and_raw_fe_ft_off(self):
        ws, send = self.make_ws(), Mock()
        ws.handle({"trnm": "PING"}, send)
        self.assertEqual(json.loads(send.call_args.args[0]), {"trnm": "PING"})
        ws.handle({"trnm":"REAL","data":[{"type":"FE","item":"A","values":{"10":"-10","290":"unknown","token":"SECRET"}},
                                            {"type":"FT","item":"A","values":{"41":"10"}}]}, send)
        self.assertEqual(ws.latest["A"]["values"], {"10":"-10","290":"unknown"})
        self.assertEqual(ws.counts["FT"], 0)

    def test_optional_ft_capture(self):
        ws=self.make_ws();ws.config=replace(CONFIG,enable_orderbook_capture=True)
        ws.handle({'trnm':'REAL','data':[{'type':'FT','item':'A','values':{'41':'10','61':'50','token':'SECRET'}}]},Mock())
        self.assertEqual(ws.counts['FT'],1)
        self.assertEqual(ws.raw_sink.call_args.args[3]['values'],{'41':'10','61':'50'})
        self.assertEqual(ws.packet('REG',{'A':'ND'})['data'][0]['type'],['FE','FT'])

    def test_reconnect_reregisters_and_marks_gap(self):
        stop, packets = threading.Event(), []
        class Connection:
            def __init__(self, index): self.index=index;self.last=None
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def send(self, data):
                self.last=json.loads(data);packets.append((self.index,self.last['trnm']))
                if self.index==2 and self.last['trnm']=='REG':stop.set()
            def recv(self,timeout):
                if self.last:
                    name=self.last['trnm'];self.last=None
                    return json.dumps({'trnm':name,'return_code':0})
                raise ConnectionError('synthetic disconnect')
        count=[0]
        def connector(*args,**kwargs): count[0]+=1;return Connection(count[0])
        ws=self.make_ws(stop,connector);ws.set_symbols({'A':'ND'})
        ws._run()
        self.assertEqual(count[0],2)
        self.assertIn((2,'REG'),packets)
        self.assertTrue(ws.gap)
        self.assertFalse(ws.active)
        self.assertTrue(ws.rest.token.call_args.kwargs['force'])


class PersistenceTests(unittest.TestCase):
    def test_adapter_uses_actual_cache_and_rejects_stale_fe(self):
        cal=ExchangeCalendar();at=instant('2026-09-28T13:40:30+00:00')
        with tempfile.TemporaryDirectory() as tmp:
            cache=KiwoomCache(Path(tmp)/'cache.db')
            for session in cal.history(SESSION.date,14):
                cache.put_minutes('TEST','ND',raw_bars(session.date,390),utc(at))
                cache.mark_complete('TEST','ND',session,utc(at))
            cache.put_minutes('TEST','ND',raw_bars(minutes=10),utc(at))
            p=KiwoomProvider(CONFIG,calendar=cal);p.cache=cache;p.ws=Mock()
            p.universe_at=at;p.selected={'TEST':'ND'}
            p.candidates={'TEST':{'sources':['volume'],'source_ranks':{'volume':1},'source_received':{'volume':utc(at)},'type':'type_unknown'}}
            p.quotes={'TEST':({'base_close_pric':'100'},utc(at))}
            tick={'TEST':{'received_utc':utc(at),'values':{'10':'-200'}}}
            p.ws.snapshot.return_value=(tick,{'TEST':'ND'},False,True)
            with patch('market_radar.providers.kiwoom.datetime') as clock:
                clock.now.return_value=at
                batch=p.fetch();batch.validate()
                self.assertEqual(calculate(batch.observations[0],CONFIG)['rvol'],1)
                tick['TEST']['received_utc']=utc(at-timedelta(seconds=100))
                self.assertEqual(p.fetch().observations,())
            cache.close()

    def test_raw_queue_overflow_explicit_gap(self):
        p=KiwoomProvider(replace(CONFIG,raw_queue_size=1))
        p._raw('FE','A','now',{'values':{'10':'1'}})
        p._raw('FE','A','now',{'values':{'10':'2'}})
        self.assertEqual(p.raw_dropped,1)
        self.assertEqual(p.raw_queue.qsize(),1)

    def test_single_collector_lock_released(self):
        from market_radar.providers.kiwoom_lock import CollectorLock
        with tempfile.TemporaryDirectory() as tmp, patch('market_radar.providers.kiwoom_lock.APP_ROOT',Path(tmp)):
            first,second=CollectorLock(),CollectorLock()
            first.acquire()
            try:
                with self.assertRaises(ProviderError):second.acquire()
            finally:first.close()
            second.acquire();second.close()

    def test_fourteen_day_cache_and_incremental_reuse(self):
        cal=ExchangeCalendar()
        with tempfile.TemporaryDirectory() as tmp:
            cache=KiwoomCache(Path(tmp)/'cache.db')
            for session in cal.history(SESSION.date,14):
                cache.put_minutes('TEST','ND',raw_bars(session.date,390),'2026-09-28T13:40:00+00:00')
                self.assertTrue(cache.mark_complete('TEST','ND',session,'2026-09-28T13:40:00+00:00'))
            provider=KiwoomProvider(CONFIG,calendar=cal);provider.cache=cache;provider.rest=Mock()
            for session in cal.history(SESSION.date,14):
                provider._chart_step('TEST','ND',session,True,instant('2026-09-28T14:00:00+00:00'))
            provider.rest.minutes_page.assert_not_called()
            missing=[s.date for s in cal.history('2026-09-29',14) if not cache.is_complete('TEST','ND',s.date)]
            self.assertEqual(missing,['2026-09-28'])
            cache.close()

    def test_incomplete_cache_not_marked_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache=KiwoomCache(Path(tmp)/'cache.db')
            cache.put_minutes('TEST','ND',raw_bars(minutes=389),'2026-09-29T00:00:00+00:00')
            self.assertFalse(cache.mark_complete('TEST','ND',SESSION,'2026-09-29T00:00:00+00:00'))
            cache.close()

    def test_schema_and_csv_new_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(Path(tmp)/'new.db',{'provider':'kiwoom'})
            o=observation();engine=RadarEngine(store,CONFIG,'kiwoom','live')
            rows=engine.process(MarketBatch(o.asof_utc,(o,),(SESSION,)),o.asof_utc)
            self.assertEqual(rows[0]['rvol'],.5)
            self.assertIsNone(rows[0]['vwap'])
            folder=store.export_day(SESSION.date,Path(tmp)/'export')
            with (folder/'snapshots.csv').open(encoding='utf-8-sig',newline='') as stream:
                row=next(csv.DictReader(stream))
            self.assertEqual(row['rvol_basis'],'completed_1m')
            self.assertEqual(row['previous_close'],'100.0')
            self.assertEqual(row['exchange'],'ND')
            self.assertEqual(row['vwap'],'')
            self.assertEqual(store.schema_version,2)
            store.close()

    def test_old_db_not_migrated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'old.db'
            store=RadarStore(path,{'provider':'legacy'});store.close()
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):RadarStore(path,{'provider':'kiwoom'})
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_daily_close_provisional_future_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(Path(tmp)/'new.db',{'provider':'kiwoom'})
            engine=RadarEngine(store,CONFIG,'kiwoom','live');o=observation()
            cal=ExchangeCalendar();following=cal.sessions('2026-09-28','2026-10-02')
            engine.process(MarketBatch(o.asof_utc,(o,),following),o.asof_utc)
            later='2026-09-29T14:00:00+00:00'
            engine.process(MarketBatch(later,(),following,(DailyClose('TEST',SESSION.date,SESSION.close_utc,210,'kiwoom:usa06012','provisional'),)),later)
            rows={r['horizon']:dict(r) for r in store.db.execute('SELECT * FROM radar_results')}
            self.assertEqual(rows['EOD']['status'],'provisional')
            self.assertEqual(rows['EOD']['result_source'],'kiwoom:usa06012')
            self.assertEqual(rows['D+1']['status'],'pending')
            self.assertIsNone(rows['D+1']['price'])
            store.close()

    def test_no_snapshot_fallback_into_minute_basis(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=RadarStore(Path(tmp)/'new.db',{'provider':'kiwoom'});engine=RadarEngine(store,CONFIG,'kiwoom','live')
            a=observation(minute=8);engine.process(MarketBatch(a.asof_utc,(a,),(SESSION,)),a.asof_utc)
            b=replace(observation(minute=9),bars=(),historical_volumes=())
            rows=engine.process(MarketBatch(b.asof_utc,(b,),(SESSION,)),b.asof_utc)
            self.assertIsNone(rows[0]['return_1m'])
            self.assertIsNone(rows[0]['rvol'])
            store.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
