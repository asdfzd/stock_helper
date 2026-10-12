"""Offline evidence regressions against checkpoint 6658927; all writes are temporary."""
from contextlib import closing, ExitStack
from copy import deepcopy
import csv
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from market_radar.config import CONFIG
from market_radar.opening_hour.engine import analyze
from market_radar.opening_hour.service import analyze_session
from market_radar.opening_hour.source import digest, read_session
from market_radar.opening_hour.storage import OpeningHourStore
from market_radar.paper.engine import PaperEngine
from market_radar.paper.replay import replay as baseline_replay
from market_radar.paper.storage import PaperStore
from market_radar.paper_rotation_v2.replay import replay as paired_replay
from market_radar.paper_rotation_v2.storage import RotationStore
from tests.opening_hour_research_test import candidate, evidence, make_source, file_state
from tests.paper_trading_test import SESSION, stamp, row

ROOT = Path(__file__).resolve().parents[1]


class PaperEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = PaperStore(self.root/'paper.db')
        self.addCleanup(self.store.close)
        self.engine = PaperEngine(self.store, 'synthetic', enabled=True)
        self.engine.on_frame(SESSION, [row(360)], stamp(360))
        self.engine.on_frame(SESSION, [row(422, price=11)], stamp(422))
        self.engine.on_tick('TEST', 11.1, stamp(422.001))
        self.assertEqual(self.engine.status, 'OPEN')

    def marks(self):
        return [dict(r) for r in self.store.db.execute('SELECT * FROM paper_trade_marks ORDER BY observation_id')]

    def horizon(self):
        self.engine.finish(stamp(600))
        return dict(self.store.db.execute("SELECT * FROM paper_horizons WHERE horizon='30s'").fetchone())

    def test_same_timestamp_all_prices_and_arrival_order(self):
        for price in (11.2, 11.3, 11.25):
            self.engine.on_tick('TEST', price, stamp(423))
        marks = self.marks()[-3:]
        self.assertEqual([r['price'] for r in marks], [11.2, 11.3, 11.25])
        self.assertEqual([r['observation_id'] for r in marks], [2, 3, 4])
        self.assertEqual(len({r['timestamp_utc'] for r in marks}), 1)

    def test_second_price_same_timestamp_tp_crossing_evidence(self):
        for price in (11.2, 13):
            self.engine.on_tick('TEST', price, stamp(423))
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual((trade['exit_reason'], trade['exit_price']), ('TAKE_PROFIT_15', 13))
        self.assertEqual(self.marks()[-1]['price'], trade['exit_price'])
        self.assertEqual(self.marks()[-1]['timestamp_utc'], trade['exit_price_at'])

    def test_second_price_same_timestamp_sl_crossing_evidence(self):
        for price in (11.2, 9):
            self.engine.on_tick('TEST', price, stamp(423))
        trade = self.store.entered(SESSION.date)[0]
        self.assertEqual((trade['exit_reason'], trade['exit_price']), ('STOP_LOSS_10', 9))
        self.assertEqual([r['price'] for r in self.marks()[-2:]], [11.2, 9])

    def test_exact_duplicate_preserved_without_duplicate_decision(self):
        self.engine.on_tick('TEST', 11.2, stamp(423))
        before = deepcopy(self.store.trades())
        events = list(self.store.db.execute('SELECT * FROM paper_trade_events'))
        self.engine.on_tick('TEST', 11.2, stamp(423))
        self.assertEqual(self.store.trades(), before)
        self.assertEqual(list(self.store.db.execute('SELECT * FROM paper_trade_events')), events)
        self.assertEqual([r['price'] for r in self.marks()[-2:]], [11.2, 11.2])

    def test_observation_id_survives_reopen(self):
        self.engine.on_tick('TEST', 11.2, stamp(423))
        with closing(PaperStore(self.store.path)) as reopened, reopened.db:
            reopened.record_mark('synthetic', 'TEST', stamp(424), 11.3, SESSION.date)
            self.assertEqual(reopened.db.execute('SELECT MAX(observation_id) FROM paper_trade_marks').fetchone()[0], 3)

    def test_nonexact_first_post_target_sample(self):
        self.engine.on_tick('TEST', 11.2, stamp(452.12))
        h = self.horizon()
        self.assertEqual(h['target_utc'], stamp(452.001))
        self.assertEqual(h['sample_timestamp_utc'], stamp(452.12))
        self.assertAlmostEqual(h['sample_offset_seconds'], .119)
        self.assertEqual(h['sample_selection'], 'first_post_target')
        self.assertEqual(h['price'], 11.2)
        self.assertEqual(h['tolerance_seconds'], CONFIG.result_tolerance_seconds)
        self.assertIsNone(h['missing_reason'])

    def test_outside_tolerance_is_null_with_reason(self):
        self.engine.on_tick('TEST', 11.2, stamp(482.002))
        h = self.horizon()
        for key in ('price', 'return_pct', 'sample_timestamp_utc', 'sample_offset_seconds', 'sample_observation_id'):
            self.assertIsNone(h[key])
        self.assertEqual(h['missing_reason'], 'no_post_target_observation_within_tolerance')

    def test_earliest_tick_not_best_price(self):
        for seconds, price in ((452.2, 11.2), (453, 12), (454, 14)):
            self.engine.on_tick('TEST', price, stamp(seconds))
        self.assertEqual(self.horizon()['price'], 11.2)

    def test_horizon_same_timestamp_tie_uses_arrival(self):
        for price in (11.2, 13, 9):
            self.engine.on_tick('TEST', price, stamp(452.2))
        h = self.horizon()
        self.assertEqual((h['price'], h['sample_observation_id']), (11.2, 2))

    def test_pre_target_tick_never_fills_sample(self):
        self.engine.on_tick('TEST', 11.2, stamp(452))
        self.assertIsNone(self.horizon()['price'])

    def test_tolerance_boundary_inclusive_and_custom(self):
        self.store.result_tolerance_seconds = 2
        self.engine.on_tick('TEST', 11.2, stamp(454.001))
        self.assertEqual(self.horizon()['sample_offset_seconds'], 2)

    def test_other_run_mark_is_not_sampled(self):
        with self.store.db:
            self.store.record_mark('other-run', 'TEST', stamp(452.001), 99, SESSION.date)
        self.assertIsNone(self.horizon()['price'])

    def test_export_contains_ordered_evidence_and_sample(self):
        for price in (11.2, 11.3):
            self.engine.on_tick('TEST', price, stamp(452.2))
        self.horizon()
        self.store.export(SESSION.date, self.root/'export')
        with (self.root/'export'/'paper_trade_marks.csv').open(newline='') as f:
            marks = list(csv.DictReader(f))
        with (self.root/'export'/'paper_horizons.csv').open(newline='') as f:
            horizons = list(csv.DictReader(f))
        self.assertEqual([r['price'] for r in marks[-2:]], ['11.2', '11.3'])
        self.assertEqual(next(h for h in horizons if h['horizon'] == '30s')['sample_observation_id'], '2')

    def test_schema_one_rejected_without_mutating_db_or_wal(self):
        path = self.root/'legacy.db'
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('CREATE TABLE paper_trades(trade_id); PRAGMA user_version=1;')
            before = file_state(self.root)
            with self.assertRaisesRegex(ValueError, 'offline migration'):
                PaperStore(path)
            self.assertEqual(file_state(self.root), before)

    def test_legacy_rotation_rejected_without_mutation(self):
        path = self.root/'legacy_rotation.db'
        with closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('CREATE TABLE paper_trades(trade_id); CREATE TABLE rotation_metadata(version); '
                             'INSERT INTO rotation_metadata VALUES(1); PRAGMA user_version=1;')
            before = file_state(self.root)
            with self.assertRaisesRegex(ValueError, 'offline migration'):
                RotationStore(path)
            self.assertEqual(file_state(self.root), before)


class OpeningEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def population(self):
        common = dict(candidate(), ticker='COMMON', symbol_type='common')
        excluded = dict(candidate(), ticker='ETF', symbol_type='ETF', excluded_symbol_type=True)
        excluded['bars'] = deepcopy(excluded['bars'])
        excluded['bars'][-1].update(close=200, high=201)
        missing = dict(candidate(), ticker='MISSING', bars=[], symbol_type='unknown',
                       candidate_tracking_state='tracking_slot_unavailable')
        return [common, excluded, missing]

    def test_same_bytes_relocated_session_cache_same_identity_and_export(self):
        source, db_path, cache = make_source(self.root/'first')
        shutil.copytree(self.root/'first', self.root/'second')
        before = file_state(self.root)
        a = analyze(read_session(source, cache_path=cache))
        b = analyze(read_session(self.root/'second'/'session', cache_path=self.root/'second'/'cache'/'minute.db'))
        self.assertEqual(a, b)
        self.assertEqual(file_state(self.root), before)
        with closing(OpeningHourStore(self.root/'out.db')) as store:
            first = store.save(a)
            self.assertEqual(store.save(b), first)
            store.export(first, self.root/'export')
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM opening_hour_sessions').fetchone()[0], 1)

    def test_export_metadata_contains_no_absolute_paths(self):
        source, _, cache = make_source(self.root/'inputs')
        result = analyze_session(source, cache_path=cache, output_db=self.root/'output.db')
        text = (Path(result['export_path'])/'metadata.json').read_text(encoding='utf-8')
        self.assertNotIn(str(self.root), text)
        self.assertNotRegex(text, r'[A-Za-z]:[\\/]|/Users/|/home/|\\\\Users\\\\')
        self.assertNotIn('source_cache_path', json.loads(text))
        self.assertIn('source_fingerprint', json.loads(text))

    def test_all_physical_input_paths_excluded_from_identity(self):
        a, b = evidence(), evidence()
        a.update(source_db_path=r'C:\Users\one\run.db', source_paper_paths=[r'C:\Users\one\paper.db'])
        b.update(source_db_path='/home/two/run.db', source_cache_path='/home/two/cache.db', source_paper_paths=['/home/two/paper.db'])
        self.assertEqual(analyze(a), analyze(b))

    def test_changed_content_changes_identity_without_supplied_fingerprint_update(self):
        a, b = evidence(), evidence()
        b['candidates'][0]['bars'][0]['volume'] = 999
        self.assertNotEqual(analyze(a)['metadata']['research_id'], analyze(b)['metadata']['research_id'])

    def test_changed_policy_changes_identity(self):
        a = evidence(self.population())
        b = dict(a, stock_type_policy='all')
        self.assertNotEqual(analyze(a)['metadata']['research_id'], analyze(b)['metadata']['research_id'])

    def test_excluded_candidate_exported_but_not_in_eligible_median(self):
        result = analyze(evidence(self.population()))
        self.assertEqual(result['summary']['median_open_to_60m_return'], 0)
        self.assertEqual(result['summary']['eligible_outcome_sample_count'], 1)
        with closing(OpeningHourStore(self.root/'out.db')) as store:
            folder = store.export(store.save(result))
            with (folder/'candidates.csv').open(newline='', encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 3)
        etf = next(r for r in rows if r['ticker'] == 'ETF')
        self.assertEqual(etf['analysis_eligible'], 'False')
        self.assertEqual(json.loads(etf['analysis_exclusion_reasons']), ['stock_type_policy:ETF'])

    def test_all_and_eligible_coverage_denominators_reproducible(self):
        result = analyze(evidence(self.population()))
        q = result['quality']
        self.assertEqual([q[k] for k in ('all_candidate_count','eligible_candidate_count','excluded_candidate_count')], [3,2,1])
        self.assertEqual(q['all_coverage']['observed_1m_row_rate'], 2/3)
        self.assertEqual(q['eligible_coverage']['observed_1m_row_rate'], 1/2)
        self.assertEqual(q['exclusion_reason_counts'], {'stock_type_policy:ETF':1})
        self.assertEqual(result, analyze(evidence(self.population())))

    def test_missing_unsubscribed_candidate_preserved_in_eligible_population(self):
        result = analyze(evidence(self.population()))
        missing = next(c for c in result['candidates'] if c['candidate']['ticker'] == 'MISSING')
        self.assertTrue(missing['candidate']['analysis_eligible'])
        self.assertEqual(missing['candidate']['candidate_tracking_state'], 'tracking_slot_unavailable')
        self.assertEqual(sum(b['missing_bar'] for b in missing['bars'][:60]), 60)
        self.assertIsNone(missing['outcome']['return_open_to_10_30'])

    def test_stock_type_all_and_common_unknown_match_existing_policy(self):
        kinds = ['common','unknown','type_unknown','ETF','ETN','warrant','other']
        candidates = [dict(candidate(), ticker=str(i), symbol_type=kind) for i,kind in enumerate(kinds)]
        default = analyze(evidence(candidates))
        self.assertEqual([c['candidate']['analysis_eligible'] for c in default['candidates']], [True]*3+[False]*4)
        all_types = analyze(dict(evidence(candidates), stock_type_policy='all'))
        self.assertEqual(all_types['quality']['eligible_candidate_count'], 7)

    def test_exclusion_flag_without_known_type_keeps_source_reason(self):
        result = analyze(evidence([dict(candidate(), excluded_symbol_type=True)]))
        c = result['candidates'][0]['candidate']
        self.assertFalse(c['analysis_eligible'])
        self.assertEqual(c['symbol_type'], 'type_unknown')
        self.assertEqual(c['analysis_exclusion_reasons'], ['source_excluded_symbol_type'])

    def test_source_raw_universe_count_and_discovery_type_retained(self):
        source, db_path, _ = make_source(self.root/'inputs')
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("UPDATE radar_discoveries SET symbol_type='warrant',excluded_symbol_type=1 WHERE ticker='B'")
        e = read_session(source)
        result = analyze(e)
        self.assertEqual(len(e['candidates']), len(result['candidates']))
        self.assertEqual(result['quality']['all_candidate_count'], 2)
        self.assertEqual(result['quality']['eligible_candidate_count'], 1)
        self.assertEqual(result['candidates'][1]['candidate']['analysis_exclusion_reasons'], ['stock_type_policy:warrant'])

    def test_complete_partial_empty_have_explicit_coverage_meaning(self):
        for population, status in (([candidate()], 'COMPLETE'), (self.population(), 'PARTIAL'),
                                   ([dict(candidate(), bars=[])], 'EMPTY'), ([], 'EMPTY')):
            with self.subTest(status=status, count=len(population)):
                self.assertEqual(analyze(evidence(population))['quality']['status'], status)

    def test_legacy_feature_dataset_is_not_overwritten(self):
        old = analyze(evidence())
        old['metadata'].update(feature_version='opening_hour_v1', research_id='legacy-fixture')
        with closing(OpeningHourStore(self.root/'out.db')) as store:
            store.save(old)
            before = store.load('legacy-fixture')
            store.save(analyze(evidence()))
            self.assertEqual(store.load('legacy-fixture'), before)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM opening_hour_sessions').fetchone()[0], 2)


class CheckpointIsolationTests(unittest.TestCase):
    @staticmethod
    def checkpoint_digest(value):
        # The pre-edit snapshot passed through JSON; 1 and 1.0 have identical meaning.
        def normalize(v):
            if isinstance(v, dict):
                return {k: normalize(x) for k, x in v.items()}
            if isinstance(v, list):
                return [normalize(x) for x in v]
            return int(v) if isinstance(v, float) and v.is_integer() else v
        return digest(normalize(value))

    def test_baseline_exact_checkpoint_result(self):
        # SHA256 of entire replay output captured before editing checkpoint 6658927.
        self.assertEqual(self.checkpoint_digest(baseline_replay(ROOT/'tests/fixtures/paper_baseline_v1.jsonl')),
                         'c88a569160c9f31ec1b574aac716060e8ae52b6be4755b41d17eef669f3e35fe')

    def test_rotation_exact_checkpoint_result(self):
        self.assertEqual(self.checkpoint_digest(paired_replay(ROOT/'tests/fixtures/paper_rotation_v2.jsonl')),
                         '4eb6c6098a4d96a8c1b00633b2e13928c2c2f5a2f2b956d4c477f812629ba195')

    def test_research_and_replay_make_zero_network_subscription_order_calls(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            mocks = [stack.enter_context(patch(name, side_effect=AssertionError('forbidden external call'))) for name in (
                'requests.sessions.Session.request', 'socket.socket.connect',
                'market_radar.providers.kiwoom_rest.KiwoomRest.request',
                'market_radar.providers.kiwoom_ws.KiwoomWebSocket.set_symbols',
                'market_radar.execution.nhplug_client._OfficialSdk.call')]
            source, _, cache = make_source(Path(tmp)/'inputs')
            result = analyze_session(source, cache_path=cache, output_db=Path(tmp)/'out.db')
            baseline_replay(ROOT/'tests/fixtures/paper_baseline_v1.jsonl')
            paired_replay(ROOT/'tests/fixtures/paper_rotation_v2.jsonl')
            for mock in mocks:
                mock.assert_not_called()
            self.assertEqual(result['summary']['actual_nh_order_calls'], 0)


if __name__ == '__main__':
    unittest.main()
