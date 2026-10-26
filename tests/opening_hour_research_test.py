"""Synthetic completed minutes, temporary evidence and offline post-session research."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from market_radar.config import CONFIG
from market_radar.models import ET, instant, utc
from market_radar.opening_hour.engine import analyze, analyze_candidate, normalize_minutes, shape
from market_radar.opening_hour.service import analyze_session, enabled_for
from market_radar.opening_hour.source import copied_database, digest, read_session
from market_radar.opening_hour.storage import OpeningHourStore
from market_radar.providers.kiwoom_calendar import ExchangeCalendar
from market_radar.providers.kiwoom_cache import KiwoomCache, POLICY
from market_radar.storage import RadarStore

CALENDAR = ExchangeCalendar()
SESSION = CALENDAR.session("2026-10-12")
START = instant(SESSION.open_utc)


def stamp(minute, seconds=0):
    return utc(START+timedelta(minutes=minute, seconds=seconds))


def candidate(detected=0, seconds=0):
    return dict(ticker="A", exchange="NAS", session_date_et=SESSION.date,
        candidate_first_detected_at=stamp(detected, seconds), candidate_first_detected_price=100,
        candidate_sources=["price_change"], source_ranks={"price_change": 20}, previous_close=80,
        source_history=[], paper_v1_trade_ids=[], rotation_v2_trade_ids=[],
        bars=[dict(bar_start_utc=stamp(i), open=100, high=101, low=99, close=100, volume=i+1) for i in range(60)])


def evidence(candidates=None):
    result = dict(session_date_et=SESSION.date, radar_run_id="fixture-run", test_version=CONFIG.test_version,
        regular_open_utc=SESSION.open_utc, regular_close_utc=SESSION.close_utc, opening_hour_end_utc=stamp(60),
        source_cache_path="synthetic", bar_policy=POLICY, calendar_source=CALENDAR.source,
        candidates=candidates if candidates is not None else [candidate()])
    result["source_fingerprint"] = digest(result)
    return result


def file_state(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


def make_source(root):
    """Actual schema 4/cache schema 1 with a non-TOP10 discovery and late source evidence."""
    source = root/"session"
    config = replace(CONFIG, data_dir=source)
    cache_path = root/"cache"/"minute.db"
    store = RadarStore(config.db_path, dict(provider="kiwoom", mode="live", test_version=CONFIG.test_version))
    db = store.db
    protocol = dict(collection_mode="Capacity Test", session_date_et=SESSION.date,
                    shared_cache_path=str(cache_path), protocol_compliant=False)
    with db:
        db.execute("INSERT INTO market_sessions VALUES(?,?,?)", (SESSION.date, SESSION.open_utc, SESSION.close_utc))
        db.execute("INSERT INTO radar_metadata VALUES('collection_protocol',?)", (json.dumps(protocol),))
        bars = [dict(start_utc=stamp(i), end_utc=stamp(i+1), high=101, low=99, close=100, volume=i+1) for i in range(60)]
        for minute, items in ((10, [dict(ticker="A", exchange="NAS", sources=["price_change"], source_ranks={"price_change":20})]),
                              (45, [dict(ticker="A", exchange="NAS", sources=["transaction_value"], source_ranks={"transaction_value":2})]),
                              (60, [dict(ticker="OUT", exchange="NAS", sources=["price_change"])])):
            raw = dict(metadata=dict(candidates=items, bar_policy=POLICY), observations=[])
            if minute == 60:
                raw["observations"] = [dict(ticker="A", metadata=dict(rvol_basis="completed_1m", bar_cutoff_utc=stamp(60), exchange="NAS"), bars=bars)]
            db.execute("INSERT INTO radar_batches VALUES(?,?,?,?,?,?)", (stamp(minute), "kiwoom", "live", "candidates", json.dumps(raw), stamp(minute)))
        db.execute("INSERT INTO radar_discoveries VALUES(?,?,?,?,?,?,?,?,?)", (
            SESSION.date,"B",stamp(35),stamp(59),None,None,0,json.dumps(["later_union_source"]),"tracking_slot_unavailable"))
        snapshot = dict(timestamp_utc=stamp(10), timestamp_et=instant(stamp(10)).astimezone(ET).isoformat(),
            session_date=SESSION.date, ticker="A", price=100, previous_close=80, day_change_pct=25,
            return_1m=1, return_5m=3, rvol=2, rvol_status="complete", current_leader_rank=20, emerging_rank=21,
            test_version=CONFIG.test_version, session_type="regular", coverage_complete=1, in_universe=1,
            exchange="NAS", candidate_sources=json.dumps(["price_change"]), source_ranks=json.dumps({"price_change":20}))
        store.insert("radar_snapshots", snapshot)
    store.close()
    (source/"dashboard_run.json").write_text(json.dumps(dict(run_id="fixture-run", stopped_at=SESSION.close_utc,
        clean_shutdown=True, collection_protocol=protocol)), encoding="utf-8")
    cache = KiwoomCache(cache_path)
    raw = [dict(cntr_tm=(START+timedelta(minutes=i)).astimezone(ET).strftime("%Y%m%d%H%M%S"),
        open_pric="100", high_pric="101", low_pric="99", cur_prc="100", trde_qty=str(i+1)) for i in range(61)]
    cache.put_minutes("A", "NAS", raw, stamp(61))
    cache.close()
    return source, config.db_path, cache_path


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.c = candidate()

    def result(self):
        return analyze_candidate(self.c, START)

    def test_exact_60_minutes(self):
        bars = self.result()["bars"][:60]
        self.assertEqual(len(bars), 60)
        self.assertTrue(bars[0]["bar_start_et"].startswith(SESSION.date+"T09:30:00"))
        self.assertTrue(bars[-1]["bar_start_et"].startswith(SESSION.date+"T10:29:00"))
        self.assertEqual(bars[-1]["bar_end_utc"], stamp(60))

    def test_1030_bar_excluded(self):
        self.c["bars"].append(dict(bar_start_utc=stamp(60), open=200, high=999, low=1, close=500, volume=9999))
        self.assertEqual(self.result()["outcome"]["opening_hour_high"], 101)

    def test_three_minute_boundaries_and_ohlcv(self):
        rows = [r for r in self.result()["bars"] if r["timeframe"] == "3m"]
        self.assertEqual(len(rows), 20)
        self.assertEqual([(r["bar_start_utc"], r["bar_end_utc"]) for r in rows], [(stamp(i), stamp(i+3)) for i in range(0,60,3)])
        self.assertEqual(rows[0]["volume"], 6)
        self.assertEqual(rows[-1]["volume"], 177)

    def test_five_minute_boundaries_and_ohlcv(self):
        self.c["bars"][0]["open"] = 99
        self.c["bars"][4]["close"] = 101
        rows = [r for r in self.result()["bars"] if r["timeframe"] == "5m"]
        self.assertEqual(len(rows), 12)
        self.assertEqual([(r["bar_start_utc"], r["bar_end_utc"]) for r in rows], [(stamp(i), stamp(i+5)) for i in range(0,60,5)])
        self.assertEqual([rows[0][k] for k in ("open","high","low","close","volume")], [99,101,99,101,15])

    def test_missing_minute_is_not_zero_volume(self):
        self.c["bars"].pop(1)
        rows = self.result()["bars"]
        self.assertTrue(rows[1]["missing_bar"])
        self.assertIsNone(rows[1]["volume"])
        self.assertIsNone(rows[59]["cumulative_volume_from_open"])
        self.assertIsNone(rows[2]["higher_high_vs_prev"])

    def test_real_zero_volume_preserved(self):
        self.c["bars"][1]["volume"] = 0
        row = self.result()["bars"][1]
        self.assertEqual(row["volume"], 0)
        self.assertTrue(row["aggregate_complete"])
        self.assertFalse(row["missing_bar"])

    def test_incomplete_three_minutes_all_ohlcv_null(self):
        self.c["bars"].pop(1)
        row = self.result()["bars"][60]
        self.assertFalse(row["aggregate_complete"])
        self.assertTrue(all(row[k] is None for k in ("open","high","low","close","volume")))

    def test_incomplete_five_minutes_all_ohlcv_null(self):
        self.c["bars"].pop(3)
        row = self.result()["bars"][80]
        self.assertFalse(row["aggregate_complete"])
        self.assertTrue(all(row[k] is None for k in ("open","high","low","close","volume")))

    def test_close_location_and_wicks(self):
        row = shape(dict(open=102, high=110, low=90, close=105))
        self.assertEqual(row["close_location_value"], .75)
        self.assertEqual(row["body_pct_of_range"], 15)
        self.assertEqual((row["upper_wick_abs"], row["lower_wick_abs"]), (5,12))
        self.assertEqual(row["upper_wick_pct_of_range"], 25)

    def test_zero_range_ratios_null(self):
        row = shape(dict(open=100,high=100,low=100,close=100))
        for field in ("close_location_value", "body_pct_of_range", "upper_wick_pct_of_range", "lower_wick_pct_of_range"):
            self.assertIsNone(row[field])
        self.assertEqual(row["direction"], "FLAT")

    def test_higher_lower_structures_and_streaks(self):
        self.c["bars"][1].update(open=101,high=103,low=100,close=102)
        self.c["bars"][2].update(open=102,high=104,low=101,close=103)
        self.c["bars"][3].update(open=100,high=102,low=98,close=99)
        rows = self.result()["bars"]
        self.assertTrue(rows[1]["higher_high_vs_prev"] and rows[1]["higher_low_vs_prev"])
        self.assertTrue(rows[3]["lower_high_vs_prev"] and rows[3]["lower_low_vs_prev"])
        out = self.result()["outcome"]
        self.assertEqual(out["higher_high_count"], 2)
        self.assertEqual(out["lower_high_count"], 2)
        self.assertEqual(out["max_consecutive_up_bars"], 2)
        self.assertEqual(out["max_consecutive_down_bars"], 1)

    def breaks(self):
        self.c["bars"][3].update(high=103,close=102)
        self.c["bars"][4].update(low=97,close=98)
        self.c["bars"][5].update(high=104,close=103)
        self.c["bars"][6].update(low=96,close=97)
        return self.result()

    def test_hod_updates(self):
        result = self.breaks()
        self.assertEqual(result["outcome"]["hod_update_count"], 2)
        self.assertEqual(result["outcome"]["final_opening_hour_hod_time"], stamp(5))
        self.assertEqual(result["outcome"]["time_to_opening_hour_high_seconds"], 300)
        self.assertEqual(len([e for e in result["events"] if e["event_type"] == "HOD_UPDATE"]), 2)

    def test_lod_updates(self):
        result = self.breaks()
        self.assertEqual(result["outcome"]["lod_update_count"], 2)
        self.assertEqual(result["outcome"]["time_of_low"], stamp(6))

    def test_first_three_high_break(self):
        self.assertEqual(self.breaks()["outcome"]["first_3m_high_first_break_at"], stamp(3))

    def test_first_three_low_break(self):
        self.assertEqual(self.breaks()["outcome"]["first_3m_low_first_break_at"], stamp(4))

    def test_first_five_high_break(self):
        self.assertEqual(self.breaks()["outcome"]["first_5m_high_first_break_at"], stamp(5))

    def test_first_five_low_break(self):
        self.assertEqual(self.breaks()["outcome"]["first_5m_low_first_break_at"], stamp(6))

    def test_break_interval_and_observed_return_inside(self):
        events = self.breaks()["events"]
        e = next(e for e in events if e.get("anchor_minutes") == 3 and e["break_direction"] == "UP")
        self.assertEqual(e["timestamp_precision"], "1m_interval")
        self.assertEqual(e["interval_end_utc"], stamp(4))
        self.assertEqual(e["returned_inside_at"], stamp(8))
        self.assertTrue(e["first_time_certain"])

    def test_missing_path_never_claims_exact_first_break(self):
        self.breaks()
        self.c["bars"].pop(5)
        self.c["bars"][7].update(high=105)
        e = next(e for e in self.result()["events"] if e.get("anchor_minutes") == 5 and e["break_direction"] == "UP")
        self.assertFalse(e["first_time_certain"])
        self.assertIsNone(self.result()["outcome"]["opening_hour_high"])
        self.assertIsNone(self.result()["outcome"]["hod_update_count"])

    def test_snapshots_all_checkpoints(self):
        snaps = [s for s in self.result()["snapshots"] if s["kind"] == "market_open"]
        self.assertEqual([s["horizon_minutes"] for s in snaps], [3,5,10,15,30,45,60])
        self.assertEqual([s["target_utc"] for s in snaps], [stamp(i) for i in (3,5,10,15,30,45,60)])

    def test_open_relative_mfe(self):
        self.assertAlmostEqual(self.breaks()["outcome"]["opening_hour_mfe_from_open"], 4)
        self.assertAlmostEqual(self.result()["snapshots"][0]["mfe_from_open_so_far"], 1)

    def test_open_relative_mae(self):
        self.assertAlmostEqual(self.breaks()["outcome"]["opening_hour_mae_from_open"], -4)
        self.assertAlmostEqual(self.result()["snapshots"][0]["mae_from_open_so_far"], -1)

    def test_detection_relative_excursions(self):
        self.breaks()
        self.c.update(candidate_first_detected_at=stamp(5), candidate_first_detected_price=103)
        snap = next(s for s in self.result()["snapshots"] if s["kind"] == "candidate_detection" and s["horizon_minutes"] == 3)
        self.assertEqual(snap["status"], "complete")
        self.assertAlmostEqual(snap["mfe_pct"], (104/103-1)*100)
        self.assertAlmostEqual(snap["mae_pct"], (96/103-1)*100)

    def test_detection_beyond_hour(self):
        self.c["candidate_first_detected_at"] = stamp(59)
        snaps = [s for s in self.result()["snapshots"] if s["kind"] == "candidate_detection"]
        self.assertEqual(snaps[0]["status"], "complete")
        self.assertTrue(all(s["status"] == "outside_opening_hour" and s["mfe_pct"] is None for s in snaps[1:]))

    def test_unaligned_detection_no_pre_detection_extreme_leak(self):
        self.c["candidate_first_detected_at"] = stamp(5,30)
        self.c["bars"][5].update(high=900, low=1)
        snap = next(s for s in self.result()["snapshots"] if s["kind"] == "candidate_detection" and s["horizon_minutes"] == 3)
        self.assertEqual(snap["status"], "unaligned_detection_window")
        self.assertIsNone(snap["mfe_pct"])
        self.assertAlmostEqual(snap["observed_bar_mfe_pct"], 1)
        self.assertEqual(snap["observed_complete_bar_count"], 2)

    def test_late_detection_marking_and_sources_asof(self):
        self.c["candidate_first_detected_at"] = stamp(35)
        self.c["source_history"] = [dict(at=stamp(45), sources=["transaction_value"])]
        result = self.result()
        self.assertTrue(all(r["pre_detection"] for r in result["bars"][:35]))
        self.assertFalse(result["bars"][35]["pre_detection"])
        self.assertFalse(result["snapshots"][4]["candidate_detected_yet"])
        self.assertIsNone(result["snapshots"][4]["candidate_sources"])
        self.assertEqual(result["snapshots"][5]["candidate_sources"], ["price_change","transaction_value"])

    def test_missing_detection_price_is_null(self):
        self.c["candidate_first_detected_price"] = None
        out = self.result()["outcome"]
        self.assertIsNone(out["opening_hour_mfe_from_detection"])
        self.assertEqual(out["detection_excursion_status"], "missing_detection_price")

    def test_notional_is_never_invented(self):
        self.assertTrue(all(r["minute_notional"] is None for r in self.result()["bars"]))

    def test_pullback_rebound_exclude_unknown_intrabar_order(self):
        out = self.breaks()["outcome"]
        self.assertAlmostEqual(out["max_pullback_from_hod_pct"], (96/104-1)*100)
        self.assertAlmostEqual(out["max_rebound_from_lod_pct"], (104/97-1)*100)
        self.assertIn("intrabar order unknown", out["excursion_path_basis"])

    def test_volume_max_cumulative_segments(self):
        result = self.result()
        self.assertEqual(result["outcome"]["maximum_volume_bar"], 60)
        self.assertEqual(result["outcome"]["maximum_volume_bar_timestamp"], stamp(59))
        self.assertEqual(result["bars"][59]["cumulative_volume_from_open"], 1830)
        self.assertEqual(result["segments"][0]["volume_sum"], 6)
        self.assertEqual(result["segments"][-1]["volume_sum"], sum(range(46,61)))
        self.assertEqual(result["outcome"]["structure_5m"]["maximum_volume_bar"], sum(range(56,61)))

    def test_anchor_gap_and_bar_returns(self):
        out = self.result()["outcome"]
        self.assertEqual(out["gap_from_previous_close_pct"], 25)
        for label in ("first_1m","first_3m","first_5m","opening_10m","opening_15m","opening_hour"):
            self.assertEqual(out[label+"_high"], 101)
            self.assertEqual(out[label+"_low"], 99)
        self.assertEqual(self.result()["bars"][1]["previous_bar_return_pct"], 0)

    def test_same_input_same_output(self):
        self.assertEqual(analyze(evidence()), analyze(evidence()))

    def test_quality_missing_rows(self):
        self.c["bars"].pop(1)
        q = analyze(evidence([self.c]))["quality"]
        self.assertEqual((q["expected_1m_rows"],q["actual_1m_rows"],q["missing_minute_count"]), (60,59,1))
        self.assertEqual(q["candidates_incomplete"], 1)
        self.assertEqual(q["complete_3m_rate"], 19/20)
        self.assertEqual(q["complete_5m_rate"], 11/12)
        self.assertEqual(q["full_1m_coverage_rate"], 0)
        self.assertEqual(q["observed_1m_row_rate"], 59/60)

    def test_no_candidates_no_fake_zero_median(self):
        result = analyze(evidence([]))
        self.assertEqual(result["quality"]["status"], "EMPTY")
        self.assertIsNone(result["summary"]["median_60m_mfe"])
        self.assertIsNone(result["quality"]["full_1m_coverage_rate"])

    def test_calendar_dst_and_early_close(self):
        for day, utc_hour in (("2026-03-06",14),("2026-03-09",13),("2026-11-27",14)):
            session = CALENDAR.session(day)
            start = instant(session.open_utc)
            self.assertEqual(start.hour, utc_hour)
            c = candidate()
            c.update(session_date_et=day,candidate_first_detected_at=session.open_utc,bars=[])
            rows = normalize_minutes(c,start)
            self.assertEqual(rows[-1]["bar_end_utc"], utc(start+timedelta(minutes=60)))
            self.assertLessEqual(instant(rows[-1]["bar_end_utc"]), instant(session.close_utc))


class SourceStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.source_db, self.cache = make_source(self.root)
        self.output = self.root/"research"/"opening_hour.db"

    def run_analysis(self):
        return analyze_session(self.source, output_db=self.output)

    def test_discovery_universe_not_top_ten_and_exact_detection(self):
        result = read_session(self.source)
        self.assertEqual([c["ticker"] for c in result["candidates"]], ["A","B"])
        a,b = result["candidates"]
        self.assertEqual(a["current_rank_at_detection"], 20)
        self.assertEqual(a["source_ranks"], {"price_change":20})
        self.assertEqual(a["candidate_sources"], ["price_change"])
        self.assertIsNone(b["candidate_sources"])
        self.assertIsNone(b["candidate_first_detected_price"])
        self.assertEqual(b["candidate_sources_session_evidence"], ["later_union_source"])
        self.assertEqual(len(a["bars"]), 60)
        self.assertEqual(a["bars"][0]["open"], 100)

    def test_earlier_discovery_wins_without_future_detection_metadata(self):
        with closing(sqlite3.connect(self.source_db)) as db, db:
            db.execute("INSERT INTO radar_discoveries VALUES(?,?,?,?,?,?,?,?,?)", (SESSION.date,"A",stamp(5),stamp(59),None,None,0,'["future"]',"discovery"))
        a = read_session(self.source)["candidates"][0]
        self.assertEqual(a["candidate_first_detected_at"], stamp(5))
        self.assertIsNone(a["candidate_sources"])
        self.assertIsNone(a["current_rank_at_detection"])
        self.assertIsNone(a["candidate_first_detected_price"])

    def test_source_cache_export_bytes_and_mtime_unchanged(self):
        before = file_state(self.root)
        self.run_analysis()
        after = file_state(self.root)
        self.assertEqual(before, {p:after[p] for p in before})
        self.assertEqual(set(after)-set(before), {p for p in after if p.startswith("research")})

    def test_wal_copy_does_not_mutate_source_shm(self):
        db = sqlite3.connect(self.source_db)
        self.addCleanup(db.close)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO radar_metadata VALUES('wal_evidence','true')")
        db.commit()
        before = file_state(self.source)
        with copied_database(self.source_db) as copied:
            self.assertEqual(copied.execute("SELECT value FROM radar_metadata WHERE key='wal_evidence'").fetchone()[0], "true")
            with self.assertRaises(sqlite3.OperationalError):
                copied.execute("DELETE FROM radar_metadata")
        self.assertEqual(before, file_state(self.source))

    def test_missing_cache_never_fabricates_open(self):
        result = read_session(self.source, cache_path=self.root/"absent.db")
        a = analyze(result)["candidates"][0]
        self.assertIsNone(a["bars"][0]["open"])
        self.assertEqual(a["bars"][0]["high"], 101)
        self.assertIsNone(a["bars"][60]["close"])
        self.assertIsNone(a["outcome"]["regular_open_price"])
        self.assertFalse(a["outcome"]["full_60m"])

    def test_cached_partial_not_promoted_after_wait(self):
        with closing(sqlite3.connect(self.cache)) as db, db:
            db.execute("UPDATE minute_bars SET received_utc=? WHERE raw_timestamp=?", (stamp(0,30), START.astimezone(ET).strftime("%Y%m%d%H%M%S")))
        a = read_session(self.source)["candidates"][0]
        # The separate completed batch can supply HLCV, but not a fabricated open.
        self.assertIsNone(a["bars"][0]["open"])
        self.assertEqual(a["bars"][0]["source"], "radar_batch_completed_1m")

    def test_running_session_rejected(self):
        (self.source/"dashboard_run.json").write_text('{"run_id":"running"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stopped"):
            self.run_analysis()
        self.assertFalse(self.output.exists())

    def test_unknown_cache_policy_rejected(self):
        with closing(sqlite3.connect(self.cache)) as db, db:
            db.execute("UPDATE cache_metadata SET value='{}' WHERE key='identity'")
        with self.assertRaisesRegex(ValueError, "identity"):
            self.run_analysis()

    def test_source_calendar_mismatch_rejected(self):
        with closing(sqlite3.connect(self.source_db)) as db, db:
            db.execute("UPDATE market_sessions SET open_utc=?", (stamp(1),))
        with self.assertRaisesRegex(ValueError, "calendar"):
            self.run_analysis()

    def test_output_cannot_modify_input_db(self):
        before = file_state(self.root)
        for path in (self.source_db,self.cache):
            with self.assertRaises(ValueError):
                analyze_session(self.source, output_db=path)
        self.assertEqual(before, file_state(self.root))

    def test_database_roundtrip_and_idempotency(self):
        result = analyze(read_session(self.source))
        store = OpeningHourStore(self.output)
        try:
            rid = store.save(result)
            self.assertEqual(result, store.load(rid))
            creation = store.db.execute("SELECT created_at FROM opening_hour_sessions").fetchone()[0]
            store.save(result)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM opening_hour_bars").fetchone()[0], 184)
            self.assertEqual(store.db.execute("SELECT created_at FROM opening_hour_sessions").fetchone()[0], creation)
            self.assertEqual(store.db.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            store.close()

    def test_export_reproducibility(self):
        first = self.run_analysis()
        directory = Path(first["export_path"])
        before = {p.name:p.read_bytes() for p in directory.iterdir()}
        second = self.run_analysis()
        self.assertEqual(first, second)
        self.assertEqual(before, {p.name:p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(set(before), {"candidates.csv","bars_1m.csv","bars_3m.csv","bars_5m.csv","events.csv",
            "time_snapshots.csv","outcomes.csv","volume_segments.csv","metadata.json","quality.json","summary.json"})

    def test_reanalysis_changed_evidence_preserves_old_dataset(self):
        first = self.run_analysis()
        with closing(sqlite3.connect(self.cache)) as db, db:
            row = db.execute("SELECT raw_timestamp,raw_json FROM minute_bars ORDER BY raw_timestamp LIMIT 1").fetchone()
            raw = json.loads(row[1]); raw["trde_qty"] = "999"
            db.execute("UPDATE minute_bars SET raw_json=? WHERE raw_timestamp=?", (json.dumps(raw),row[0]))
        second = self.run_analysis()
        self.assertNotEqual(first["research_id"], second["research_id"])
        self.assertTrue(Path(first["export_path"]).exists())
        with closing(sqlite3.connect(self.output)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM opening_hour_sessions").fetchone()[0],2)

    def test_quality_and_capacity_remain_descriptive(self):
        result = self.run_analysis()
        q = result["quality"]
        self.assertEqual((q["candidate_count"],q["candidates_with_full_60m"],q["candidates_incomplete"]), (2,1,1))
        self.assertEqual((q["expected_1m_rows"],q["actual_1m_rows"],q["missing_minute_count"]), (120,60,60))
        self.assertEqual(q["candidate_detection_price_available_rate"], .5)
        self.assertEqual(q["opening_hour_outcome_available_rate"], .5)
        meta = json.loads((Path(result["export_path"])/"metadata.json").read_text())
        self.assertEqual(meta["collection_mode"], "Capacity Test")
        self.assertFalse(meta["protocol_compliant"])
        self.assertIn("descriptive", result["summary"]["interpretation"])

    def test_paper_reference_ids_same_run_only(self):
        path = self.root/"paper.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE paper_trades(trade_id,ticker,strategy_version,session_date_et,radar_run_id,entry_price)")
            db.executemany("INSERT INTO paper_trades VALUES(?,?,?,?,?,?)", [("v1","A","paper_v1_baseline",SESSION.date,"fixture-run",100),
                ("v2","A","paper_rotation_v2",SESSION.date,"fixture-run",100), ("other","A","paper_v1_baseline",SESSION.date,"other-run",100)])
        a = read_session(self.source, paper_paths=[path])["candidates"][0]
        self.assertEqual(a["paper_v1_trade_ids"], ["v1"])
        self.assertEqual(a["rotation_v2_trade_ids"], ["v2"])

    def test_no_new_rest_calls(self):
        with patch("market_radar.providers.kiwoom_rest.KiwoomRest.request", side_effect=AssertionError("forbidden")) as rest:
            self.run_analysis()
        rest.assert_not_called()

    def test_no_new_ws_subscriptions(self):
        with patch("market_radar.providers.kiwoom_ws.KiwoomWebSocket.set_symbols", side_effect=AssertionError("forbidden")) as ws:
            self.run_analysis()
        ws.assert_not_called()

    def test_actual_nh_calls_zero(self):
        with patch("market_radar.execution.nhplug_client._OfficialSdk.call", side_effect=AssertionError("forbidden")) as nh, \
             patch("requests.Session.request", side_effect=AssertionError("network forbidden")) as network, \
             patch("socket.socket.connect", side_effect=AssertionError("network forbidden")) as connect:
            result = self.run_analysis()
        nh.assert_not_called(); network.assert_not_called(); connect.assert_not_called()
        self.assertEqual(result["summary"]["actual_nh_order_calls"], 0)

    def test_cli_structural_smoke_temporary_evidence(self):
        p = subprocess.run([sys.executable,str(ROOT/"tools/opening_hour_research/analyze_session.py"),
            "--session-dir",str(self.source),"--output-db",str(self.output)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)["quality"]["candidate_count"],2)


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_default_off(self):
        from market_radar.controller import CollectorController
        self.assertFalse(enabled_for("Regular Session"))
        controller = CollectorController(self.root/"master.db")
        self.assertEqual(controller.opening_hour_result["status"],"OFF")
        with patch("market_radar.opening_hour.service.analyze_session") as analyzer:
            controller._analyze_opening_hour()
        analyzer.assert_not_called()

    def start_mode(self,mode):
        from market_radar.controller import CollectorController
        controller = CollectorController(self.root/"master.db")
        with patch.object(controller,"_run"):
            controller.start(replace(CONFIG,collection_mode=mode,data_dir=self.root/"session"), opening_hour_enabled=True)
            controller.thread.join(5)
        return controller

    def test_capacity_forced_off(self):
        self.assertFalse(enabled_for("Capacity Test",True))
        self.assertFalse(self.start_mode("Capacity Test").opening_hour_enabled)

    def test_regular_enable_and_manual_off(self):
        self.assertTrue(enabled_for("Regular Session",True))
        self.assertTrue(self.start_mode("Regular Session").opening_hour_enabled)
        self.assertFalse(self.start_mode("Manual").opening_hour_enabled)

    def test_postprocessor_rejects_running_collector(self):
        from market_radar.controller import CollectorController
        controller = CollectorController(self.root/"master.db")
        controller.opening_hour_enabled=True
        controller.worker=SimpleNamespace(running=True)
        with self.assertRaisesRegex(RuntimeError,"stopped"):
            controller._analyze_opening_hour()

    def test_postprocessor_error_separate_from_collector(self):
        from market_radar.controller import CollectorController
        controller = CollectorController(self.root/"master.db")
        controller.opening_hour_enabled=True
        controller.config=replace(CONFIG,data_dir=self.root/"session")
        controller.worker=SimpleNamespace(running=False,config=controller.config)
        controller.schedule=SimpleNamespace(snapshot=lambda:dict(session_date_et=SESSION.date))
        with patch("market_radar.opening_hour.service.analyze_session",side_effect=ValueError("not for UI")):
            controller._analyze_opening_hour()
        result = controller.opening_hour_result
        self.assertEqual((result['status'], result['error']), ('INCOMPLETE', 'ValueError'))
        self.assertEqual(result['diagnostics']['exception']['phase'], 'opening_analysis')
        saved = json.loads((controller.config.data_dir/'opening_hour_diagnostics.json').read_text())
        self.assertEqual(saved, result['diagnostics'])
        self.assertNotIn('not for UI', json.dumps(saved))
        self.assertEqual(controller.state,"STOPPED")

    def test_controller_runs_analysis_only_after_provider_and_paper_finish(self):
        from market_radar.controller import CollectorController
        from tests.market_radar_schedule_test import Clock, SyntheticLive
        clock = Clock()
        provider = SyntheticLive(clock)
        provider.ws_factory = Mock(side_effect=AssertionError("No WebSocket in synthetic test"))
        controller = CollectorController(self.root/"master.db",clock=clock,calendar=CALENDAR)
        config = replace(CONFIG,data_dir=self.root/"sessions",historical_cache_path=self.root/"cache.db",collection_mode="Regular Session")
        controller.paper_path = self.root/"paper.db"
        controller.paper_review_path = self.root/"rotation.db"
        controller.opening_hour_path = self.root/"opening.db"
        def inspect(*args, **kwargs):
            self.assertTrue(provider.closed)
            self.assertFalse(controller.worker.running)
            self.assertFalse(controller.paper.thread.is_alive())
            self.assertEqual(controller.session_phase,"FINALIZING")
            self.assertEqual(kwargs["paper_paths"],(controller.paper_path,controller.paper_review_path))
            self.assertEqual(kwargs["output_db"],controller.opening_hour_path)
            return dict(status="INCOMPLETE",quality=dict(candidate_count=0,candidates_with_full_60m=0))
        with patch("market_radar.opening_hour.service.analyze_session",side_effect=inspect) as analyzer:
            try:
                controller.start(config,provider,paper_enabled=True,paper_rotation_enabled=True,opening_hour_enabled=True)
                deadline = time.monotonic()+8
                while controller.session_phase != "PREOPEN_WAIT" and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(controller.session_phase,"PREOPEN_WAIT")
                analyzer.assert_not_called()
                controller.stop()
                controller.thread.join(8)
                analyzer.assert_called_once()
                self.assertNotIn("error",controller.opening_hour_result)
                self.assertEqual(controller.state,"STOPPED")
            finally:
                controller.stop()
                if controller.thread:
                    controller.thread.join(8)

    def test_paper_v1_same_replay_with_research_between_runs(self):
        from market_radar.paper.replay import replay
        fixture=ROOT/"tests/fixtures/paper_baseline_v1.jsonl"
        before=replay(fixture)
        analyze(evidence())
        self.assertEqual(before,replay(fixture))

    def test_rotation_same_replay_with_research_between_runs(self):
        from market_radar.paper_rotation_v2.replay import replay
        fixture=ROOT/"tests/fixtures/paper_rotation_v2.jsonl"
        before=replay(fixture)
        analyze(evidence())
        after=replay(fixture)
        self.assertEqual(before["switches"],after["switches"])
        self.assertEqual(before["rotation_trades"],after["rotation_trades"])
        self.assertEqual(before,after)

    def test_post_exit_same_replay_with_research_between_runs(self):
        from market_radar.paper_rotation_v2.replay import replay
        fixture=ROOT/"tests/fixtures/paper_rotation_v2.jsonl"
        before=replay(fixture)["post_exit"]
        analyze(evidence())
        self.assertTrue(before)
        self.assertEqual(before,replay(fixture)["post_exit"])

    def test_ui_default_off_regular_toggle_capacity_reset(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from market_radar.ui import RadarPanel
        app=QApplication.instance() or QApplication([])
        panel=RadarPanel(config=replace(CONFIG,data_dir=self.root/"session"),master_path=self.root/"master.db",
            settings=QSettings(str(self.root/"settings.ini"),QSettings.Format.IniFormat))
        try:
            self.assertFalse(panel.opening_hour_enable.isChecked())
            panel.collection_mode.setCurrentText("Regular Session")
            self.assertTrue(panel.opening_hour_enable.isEnabled())
            panel.opening_hour_enable.setChecked(True)
            self.assertIn("ENABLED",panel.opening_hour_status.text())
            panel.collection_mode.setCurrentText("Capacity Test")
            self.assertFalse(panel.opening_hour_enable.isChecked())
            self.assertFalse(panel.opening_hour_enable.isEnabled())
            self.assertIn("OFF",panel.opening_hour_status.text())
        finally:
            panel.close()
            # close() deliberately leaves research IO asynchronous. Finish this
            # test's initial history query before TemporaryDirectory removes DB.
            panel.executor.shutdown(wait=True)
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
