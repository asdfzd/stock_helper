"""v2 research data quality regression tests; all market inputs are synthetic."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.kiwoom_provider_test import observation, SESSION, raw_bars
from market_radar.config import CONFIG
from market_radar.calculations import calculate, rank
from market_radar.engine import RadarEngine
from market_radar.models import MarketBatch, instant, utc
from market_radar.provider import UniverseStale, UniverseWarmingUp, ProviderError
from market_radar.providers.kiwoom import KiwoomProvider, merge_candidates, allocate_slots
from market_radar.providers.kiwoom_types import classify_symbol
from market_radar.providers.kiwoom_cache import KiwoomCache, normalize_bars, contiguous
from market_radar.quality import QualityCounters, daily_summary
from market_radar.runtime import RadarWorker
from market_radar.storage import RadarStore


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = replace(CONFIG, data_dir=Path(self.tmp.name))
        self.store = RadarStore(self.config.db_path, {"provider":"kiwoom","test_version":CONFIG.test_version})
        self.engine = RadarEngine(self.store,self.config,"kiwoom","live")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def process(self, o=None, metadata=None):
        o = o or observation()
        self.engine.process(MarketBatch(o.asof_utc,(o,),(SESSION,),metadata=metadata or {}),o.asof_utc)
        return o

    def provider(self, limit=1):
        p=KiwoomProvider(replace(self.config,max_ws_symbols=limit))
        p.ws=Mock()
        p.candidates=merge_candidates([("day_change",[{"stk_cd": "NEW", "stex_tp":"ND"}],"now",0)],{})
        return p

    def test_initial_warmup_exception(self):
        with self.assertRaises(UniverseWarmingUp): self.provider().fetch()

    def test_stale_after_success_exception(self):
        p=self.provider();p.universe_at=datetime.now(timezone.utc)-timedelta(hours=1)
        with self.assertRaises(UniverseStale): p.fetch()

    def test_runtime_warmup_info_stale_warning_real_error(self):
        p=Mock(name="provider");p.name="kiwoom";p.mode="live"
        p.fetch.side_effect=[UniverseWarmingUp(),UniverseStale(),ProviderError("test"),StopIteration()]
        worker=RadarWorker(p,replace(self.config,data_dir=Path(self.tmp.name)/"worker",snapshot_seconds=.001))
        worker._run()
        text="\n".join(f.read_text(encoding="utf-8") for f in worker.config.data_dir.glob("logs/*.log"))
        self.assertIn("INFO kiwoom_universe_warming_up",text)
        self.assertIn("WARNING kiwoom_universe_stale",text)
        self.assertEqual(text.count("ERROR api_error"),1)
        self.assertEqual(text.count("Traceback"),1)

    def test_uncertain_missing_minute_not_filled(self):
        rows=raw_bars();del rows[3]
        bars=normalize_bars(rows,SESSION,instant(observation().metadata["bar_cutoff_utc"]))
        self.assertEqual(len(bars),9)
        self.assertFalse(contiguous(bars,SESSION,instant(observation().metadata["bar_cutoff_utc"])))
        row=calculate(replace(observation(),bars=bars),self.config)
        self.assertIsNone(row["rvol"]);self.assertIsNone(row["hod"])

    def test_real_zero_volume_bar_preserved_without_price_synthesis(self):
        rows=raw_bars(); rows[2]["trde_qty"]="0"
        bars=normalize_bars(rows,SESSION,instant(observation().metadata["bar_cutoff_utc"]))
        self.assertEqual(bars[2].volume,0)
        self.assertEqual(bars[2].close,103)

    def test_backfill_disabled_and_zero_default(self):
        p=self.provider();self.assertTrue(p.backfill_eligible("NEW"))
        p.config=replace(p.config,rvol_backfill_enabled=False)
        self.assertFalse(p.backfill_eligible("NEW"))

    def test_liquidity_threshold_does_not_remove_discovery(self):
        p=self.provider();p.config=replace(p.config,rvol_backfill_min_day_volume=100,rvol_backfill_min_price=2)
        p.quotes["NEW"]=({"acc_trde_qty":"99","cur_prc":"3"},utc(datetime.now(timezone.utc)))
        self.assertFalse(p.backfill_eligible("NEW"))
        p._select_symbols(datetime.now(timezone.utc));self.assertIn("NEW",p.selected)
        p.quotes["NEW"]=({"acc_trde_qty":"100","cur_prc":"3"},utc(datetime.now(timezone.utc)))
        self.assertTrue(p.backfill_eligible("NEW"))

    def test_rvol_available(self): self.assertEqual(calculate(observation(),self.config)["rvol_status"],"available")

    def test_rvol_not_eligible(self):
        o=observation();o=replace(o,metadata=dict(o.metadata,rvol_status="not_eligible"))
        row=calculate(o,self.config)
        self.assertIsNone(row["rvol"]);self.assertEqual(row["rvol_status"],"not_eligible")

    def test_rvol_warming_and_incomplete(self):
        o=observation(history_count=1)
        for state in ("warming","incomplete_history"):
            self.assertEqual(calculate(replace(o,metadata=dict(o.metadata,rvol_status=state)),self.config)["rvol_status"],state)

    def test_zero_denominator(self):
        o=observation();o=replace(o,historical_volumes=tuple(replace(h,volume=0) for h in o.historical_volumes))
        row=calculate(o,self.config)
        self.assertIsNone(row["rvol"]);self.assertEqual(row["rvol_status"],"zero_denominator")

    def test_protection_persisted_sixty_minutes_plus_tolerance(self):
        o=self.process()
        t=self.store.protected_tracking(o.asof_utc)[o.ticker]
        self.assertEqual((instant(t["tracking_protected_until"])-instant(o.asof_utc)).total_seconds(),3630)

    def test_protected_survives_churn_and_releases(self):
        o=self.process();p=self.provider();p.protected=self.store.protected_tracking(o.asof_utc)
        p.selected={"TEST":"ND"};p._select_symbols(instant(o.asof_utc)+timedelta(minutes=59))
        self.assertEqual(p.selected,{"TEST":"ND"});self.assertIn("NEW",p.slot_unavailable)
        p._select_symbols(instant(o.asof_utc)+timedelta(minutes=61))
        self.assertEqual(p.selected,{"NEW":"ND"})

    def test_sixty_minute_result_completes_before_release(self):
        first=self.process(observation(minute=10,seconds=30))
        later=self.process(observation(minute=70,seconds=30))
        row=self.store.db.execute("SELECT status,missing_reason FROM radar_results WHERE horizon='60m'").fetchone()
        self.assertEqual(tuple(row),("complete",None))
        self.assertIn("TEST",self.store.protected_tracking(later.asof_utc))
        self.assertEqual(self.store.protected_tracking(utc(instant(later.asof_utc)+timedelta(seconds=31))),{})

    def test_protection_restored_from_database(self):
        o=self.process()
        self.store.close()
        self.store=RadarStore(self.config.db_path,{"provider":"kiwoom","test_version":CONFIG.test_version})
        self.assertIn("TEST",self.store.protected_tracking(o.asof_utc))

    def test_followup_does_not_change_candidate_ranking(self):
        a=calculate(replace(observation(),ticker="A",in_universe=False,price=1000),self.config)
        b=calculate(replace(observation(),ticker="B"),self.config)
        rank([a,b])
        self.assertIsNone(a["current_leader_rank"]);self.assertIsNone(a["emerging_rank"])
        self.assertEqual((b["current_leader_rank"],b["emerging_rank"]),(1,1))

    def test_twenty_protected_slots(self):
        p=self.provider(20);now=datetime.now(timezone.utc)
        p.selected={f"P{i}":"ND" for i in range(20)}
        p.protected={t:{"exchange":"ND","tracking_protected_until":utc(now+timedelta(hours=1))} for t in p.selected}
        before=dict(p.selected);p._select_symbols(now)
        self.assertEqual(p.selected,before);self.assertEqual(p.slot_unavailable,{"NEW"})

    def test_unsubscribed_discovery_saved_without_fake_snapshot(self):
        item={"ticker":"NEW","type":"unknown","sources":["day_change"]}
        self.process(metadata={"candidates":[item],"missing_reasons":{"NEW":"tracking_slot_unavailable"}})
        self.assertEqual(self.store.db.execute("SELECT candidate_tracking_state FROM radar_discoveries").fetchone()[0],"tracking_slot_unavailable")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM radar_snapshots WHERE ticker='NEW'").fetchone()[0],0)

    def test_missing_reason_from_target_window(self):
        o=self.process();at=utc(instant(o.asof_utc)+timedelta(seconds=30))
        self.engine.process(MarketBatch(at,(),metadata={"missing_reasons":{"TEST":"stale_price"}}),at)
        at=utc(instant(o.asof_utc)+timedelta(seconds=61))
        self.engine.process(MarketBatch(at,()),at)
        row=self.store.db.execute("SELECT status,missing_reason FROM radar_results WHERE horizon='30s'").fetchone()
        self.assertEqual(tuple(row),("missing","stale_price"))

    def test_outside_session_reason(self):
        self.process(observation(minute=389,seconds=30))
        self.assertEqual(self.store.db.execute("SELECT missing_reason FROM radar_results WHERE horizon='60m'").fetchone()[0],"outside_session")

    def test_etf(self): self.assertEqual(classify_symbol({"isEtf":"Y"})[0],"ETF")
    def test_warrant_official_names(self):
        self.assertEqual(classify_symbol({"isEtf":"N","stk_nm":"아베 콜 워런트","stk_enm":"ARBE C/WTS 07/10/2026"})[0],"warrant")
    def test_explicit_common(self):
        self.assertEqual(classify_symbol({"isEtf":"N","stk_enm":"EXAMPLE COMMON STOCK"})[0],"common")
    def test_unit_official_names(self):
        self.assertEqual(classify_symbol({"isEtf":"N","stk_nm":"퓨처 비전 유닛","stk_enm":"EXAMPLE CORP UNITS("})[0],"other")
    def test_warrant_common_underlying_not_common(self):
        self.assertEqual(classify_symbol({"isEtf":"N","stk_enm":"WARRANTS TO BUY COMMON STOCK"})[0],"unknown")
    def test_non_etf_does_not_prove_common(self):
        self.assertEqual(classify_symbol({"isEtf":"N","stk_cd":"ANYW","upgb":"Technology"})[0],"unknown")
    def test_type_filter_and_all_override(self):
        pages=[("volume",[{"stk_cd":"ETF","stex_tp":"NY"}],"now",0)]
        catalog={"ETF":{"raw_json":json.dumps({"isEtf":"Y"})}}
        self.assertEqual(allocate_slots(merge_candidates(pages,catalog),20),[])
        self.assertEqual(len(allocate_slots(merge_candidates(pages,catalog,"all"),20)),1)

    def test_summary_numbers_and_export(self):
        o=self.process(metadata={"candidates":[{"ticker":"TEST","type":"unknown","sources":["day_change","volume"]}]})
        counter=QualityCounters();counter.add("subscription_add_count",20,SESSION.date);counter.persist(self.store);counter.persist(self.store)
        summary=daily_summary(self.store,SESSION.date)
        self.assertEqual(summary["snapshot_count"],1)
        self.assertEqual(summary["candidate_count"],1)
        self.assertEqual(summary["rvol"]["available_pct"],100)
        self.assertEqual(summary["market_data"]["subscription_add_count"],20)
        self.assertEqual(summary["candidate_sources"]["multi_source"],1)
        self.assertEqual(summary["results"]["+60m"]["pending"],1)
        folder=self.store.export_day(SESSION.date,Path(self.tmp.name)/"export")
        self.assertEqual(json.loads((folder/"quality_summary.json").read_text())["snapshot_count"],1)

    def test_counters_only_day_exports_null_percent(self):
        counters=QualityCounters();counters.add("api_error_count",1,"2026-09-29");counters.persist(self.store)
        folder=self.store.export_all(Path(self.tmp.name)/"export")[0]
        summary=json.loads((folder/"quality_summary.json").read_text())
        self.assertIsNone(summary["rvol"]["available_pct"])
        self.assertEqual(summary["runtime"]["api_error_count"],1)

    def test_quality_gauges_use_requested_et_day(self):
        self.process(metadata={"tracking_counts":{"protected_count":7}})
        at="2026-09-29T13:45:00+00:00"
        self.engine.process(MarketBatch(at,(),metadata={"tracking_counts":{"protected_count":2}}),at)
        self.assertEqual(daily_summary(self.store,SESSION.date)["tracking"]["protected_count"],7)
        self.assertEqual(daily_summary(self.store,"2026-09-29")["tracking"]["protected_count"],2)

    def test_rank_formula_unchanged(self):
        rows=[calculate(replace(observation(),ticker="A"),self.config),calculate(replace(observation(),ticker="B",price=300),self.config)]
        rank(rows)
        self.assertEqual([r["current_leader_rank"] for r in rows],[2,1])
        self.assertEqual([r["emerging_rank"] for r in rows],[1,2])

    def test_schema_two_rejected_without_migration(self):
        p=Path(self.tmp.name)/"old.db";old=RadarStore(p,{"provider":"kiwoom"});old.close()
        before=p.read_bytes()
        with self.assertRaises(ValueError): RadarStore(p,{"provider":"kiwoom","test_version":CONFIG.test_version})
        self.assertEqual(before,p.read_bytes())

    def test_cache_without_contract_rejected_unchanged(self):
        import sqlite3
        p=Path(self.tmp.name)/"unversioned.db"
        with sqlite3.connect(p) as db:
            db.execute("CREATE TABLE minute_bars(raw_json TEXT)")
        db.close()
        before=p.read_bytes()
        with self.assertRaises(ValueError): KiwoomCache(p)
        self.assertEqual(before,p.read_bytes())


if __name__ == "__main__": unittest.main()
