"""Explicit SYNTHETIC fixture. No credentials, socket, live provider or OS shutdown.

The controller facade accelerates time; the unchanged Radar/Paper/Rotation/
Opening exporters and Validator process synthetic data in a new private folder.
It is packaging/offline evidence, never a live smoke.
"""
from dataclasses import asdict
from datetime import timedelta
import json
from types import SimpleNamespace
import uuid

from market_radar.models import ET, MarketBatch, Observation, instant, utc
from market_radar.scheduling import SessionSchedule


class SyntheticClock:
    def __init__(self):
        self.wall, self.mono = instant("2026-10-12T12:59:00+00:00"), 0.0

    def now(self):
        return self.wall

    def monotonic(self):
        return self.mono

    def advance(self, seconds):
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


class SyntheticController:
    def __init__(self, master_path, *, clock, calendar=None):
        self.master_path, self.clock, self.calendar = master_path, clock, calendar
        self.busy = False
        self.worker = self.paper = None
        self.state = "STOPPED"
        self.opening_hour_result = {"status": "OFF"}

    @property
    def session_phase(self):
        return self.schedule.phase

    def start(self, config, provider=None, **flags):
        assert provider is None
        assert flags == dict(fresh_directory=False, paper_enabled=True, paper_rotation_enabled=True, opening_hour_enabled=True)
        self.config = config
        self.schedule = SessionSchedule(config, self.calendar, self.clock)
        self.lifecycle = dict(run_id=uuid.uuid4().hex, started_at=utc(self.clock.now()), clean_shutdown=False)
        self.busy, self.state = True, "RUNNING"
        self.worker = SimpleNamespace(running=True, error=None, provider=SimpleNamespace(thread=None, io_thread=None, ws=None))
        return True

    def stop(self):
        if not self.busy:
            return
        self.schedule.stop("user_requested")
        try:
            self._generate()
            self.state = "STOPPED"
        except Exception:
            self.state = "ERROR"
            self.worker.error = "synthetic_generation_failed"
            raise
        finally:
            self.busy = False
            self.worker.running = False

    def _generate(self):
        from .core import atomic_json
        from market_radar.engine import RadarEngine
        from market_radar.storage import RadarStore
        from market_radar.providers.kiwoom_calendar import ExchangeCalendar
        from market_radar.providers.kiwoom_cache import KiwoomCache, POLICY
        from market_radar.paper.storage import PaperStore
        from market_radar.paper.engine import PaperEngine
        from market_radar.paper_rotation_v2.storage import RotationStore
        from market_radar.paper_rotation_v2.paired import PairedEngine
        from market_radar.research.importer import import_session
        from market_radar.research.validator import revalidate_session
        from market_radar.opening_hour.service import analyze_session
        calendar = self.calendar or ExchangeCalendar()
        session = calendar.session(self.schedule.snapshot()["session_date_et"])
        opened = instant(session.open_utc)
        run_id = self.lifecycle["run_id"]
        self.schedule.data["first_regular_batch_utc"] = session.open_utc
        self.schedule.finish(True)
        self.lifecycle.update(clean_shutdown=True, stopped_at=utc(self.clock.now()), collection_protocol=self.schedule.snapshot())
        identity = {k: v for k, v in asdict(self.config).items() if k not in {"data_dir", "historical_cache_path"}}
        identity.update(provider="kiwoom", mode="live", schema_version=4, rvol_basis="completed_1m", synthetic_fixture=True)
        store = RadarStore(self.config.db_path, identity)
        paper = PaperStore(self.paper_path)
        review = RotationStore(self.paper_review_path)
        try:
            engine = RadarEngine(store, self.config, "kiwoom", "live")
            baseline = PaperEngine(paper, run_id, enabled=True, collection_mode="Regular Session")
            paired = PairedEngine(baseline, review, rotation_enabled=True)
            stamps = getattr(self, 'observation_stamps', None)
            if stamps is None:
                stamps = [utc(opened+timedelta(seconds=seconds))
                          for seconds in range(0, getattr(self, 'observation_seconds', 3900), 30)]
            failure = getattr(self, 'paper_failure', None)
            invalidated = False
            for at in stamps:
                observation = Observation("DEMO", at, 100, 100, session,
                    metadata=dict(exchange="ND", rvol_basis="completed_1m", bar_cutoff_utc=at, rvol_cutoff_et=at,
                                  bar_cutoff_et=at, bar_label_policy=POLICY,
                                  price_received_utc=at, price_age_ms=0, ws_gap_detected=False,
                                  candidate_sources=["day_change"], source_ranks={"day_change": 1}))
                batch = MarketBatch(at, (observation,), (session,), universe="SYNTHETIC_OFFLINE",
                    metadata=dict(bar_policy=POLICY, ws_connected=True, ws_gap_detected=False,
                                  candidates=[dict(ticker="DEMO", exchange="ND", sources=["day_change"], type="common")],
                                  tracking_counts=dict(active_subscription_count=1),
                                  active={"DEMO": "ND"}))
                engine.process(batch, at)
                if failure and not invalidated and instant(at) >= instant(failure['at']):
                    paired.gap(at, failure['reason'])
                    invalidated = True
                # Zero trades is a legitimate complete export; no invented fills.
                paired.on_frame(session, [], at, ws_connected=True, ws_gap=False)
            paired.finish(utc(self.clock.now()))
            paper.export(session.date, self.paper_path.parent/"export"/session.date)
            review.export_review(session.date, self.paper_review_path.parent/"export"/session.date, paper.summary(session.date))
            store.db.execute("INSERT OR REPLACE INTO radar_metadata VALUES('collection_protocol',?)", (json.dumps(self.schedule.snapshot()),))
            store.db.commit()
            store.export_all(self.config.data_dir/"export")
        finally:
            store.close(); paper.close(); review.close()
        atomic_json(self.config.data_dir/"dashboard_run.json", self.lifecycle)
        cache = KiwoomCache(self.config.cache_path)
        try:
            raw = [dict(cntr_tm=(opened+timedelta(minutes=i)).astimezone(ET).strftime("%Y%m%d%H%M%S"),
                        open_pric="100", high_pric="101", low_pric="99", cur_prc="100", trde_qty="100")
                   for i in range(min(60, getattr(self, 'observation_seconds', 3900)//60))]
            cache.put_minutes("DEMO", "ND", raw, utc(self.clock.now()))
        finally:
            cache.close()
        # All synthetic collection stores are closed. Match the real Controller:
        # its worker is stopped while import/validation/Opening still keep it busy.
        self.worker.running = False
        items = import_session(self.config.db_path, self.master_path, lifecycle=self.lifecycle)
        for item in items:
            revalidate_session(dict(source_db_path=str(self.config.db_path), session_date_et=item["day"], run_id=item["run_id"],
                                    export_path=str(self.config.data_dir/"export"/item["day"])), self.master_path)
        self.paper = SimpleNamespace(path=self.paper_path, review_path=self.paper_review_path,
                                     error=bool(failure), sidecar_error=False, thread=None)
        self.opening_hour_result = analyze_session(self.config.data_dir, day=session.date, cache_path=self.config.cache_path,
            paper_paths=(self.paper_path, self.paper_review_path), output_db=self.opening_hour_path, calendar=calendar)


def drive(runner, on_change=None):
    """Call from a background thread. Every observed second is explicitly simulated."""
    runner.start(cache=runner.root/"synthetic_cache"/"kiwoom_v2.db")
    previous = None
    while not runner.finalized:
        if on_change and runner.state != previous:
            previous = runner.state
            on_change(runner.state)
        runner.controller.schedule.tick(True)
        runner.tick()
        runner.clock.advance(1)
    if on_change:
        on_change(runner.state)
    return runner
