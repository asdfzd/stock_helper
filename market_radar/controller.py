"""Qt-independent lifecycle supervisor around the existing collector worker."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid
from dataclasses import replace

from .provider import create_live_provider
from .runtime import RadarWorker
from .config import DEFAULT_SHARED_CACHE_PATH
from .research.importer import import_session
from .research.master_db import DEFAULT_MASTER_PATH
from .scheduling import SessionSchedule, SystemClock, validate_protocol_config, LATE_CAPACITY


class CollectorController:
    def __init__(self, master_path=DEFAULT_MASTER_PATH, provider_factory=create_live_provider, *, clock=None, calendar=None):
        self.master_path = Path(master_path)
        self.provider_factory = provider_factory
        self.worker = None
        self.thread = None
        self.state = "STOPPED"
        self.events = queue.Queue()
        self._stop = threading.Event()
        self.config = None
        self.lifecycle = {}
        self.started_monotonic = None
        self.connection = {"connected": False, "active_ws": 0}
        self.clock, self.calendar = clock or SystemClock(), calendar
        self.schedule = None

    @property
    def session_phase(self):
        return self.schedule.phase if self.schedule else self.state

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def running(self):
        return self.busy

    def start(self, config, provider=None, *, fresh_directory=True):
        if self.busy:
            return False
        run_id = uuid.uuid4().hex
        if provider is None and config.provider == "kiwoom" and config.historical_cache_path is None:
            # The canonical cache stays outside every generated session directory.
            config = replace(config, historical_cache_path=DEFAULT_SHARED_CACHE_PATH)
        if fresh_directory:
            config = replace(config, data_dir=config.data_dir / (datetime.now().strftime("%Y%m%d_%H%M%S_") + run_id[:8]))
        validate_protocol_config(config)
        if config.collection_mode != "Manual":
            if provider is not None and provider.mode != "live":
                raise ValueError("Scheduled modes require a live provider")
            if config.db_path.exists():
                raise ValueError("Scheduled modes require a new session directory")
        self.config = config
        self.schedule = None
        self.worker = None
        self._stop.clear()
        self.connection = {"connected": False, "active_ws": 0}
        self.state = "STARTING"
        self.started_monotonic = time.monotonic()
        self.lifecycle = dict(run_id=run_id, started_at=datetime.now(timezone.utc).isoformat(), clean_shutdown=False)
        self.thread = threading.Thread(target=self._run, args=(provider,), name="radar-controller", daemon=False)
        self.thread.start()
        return True

    def stop(self):
        if self.busy and self.state in {"STARTING", "RUNNING"} and not self._stop.is_set():
            self._stop.set()
            if self.schedule:
                self.schedule.stop("user_requested")
            self.state = "STOPPING"
            if self.worker:
                self.worker.stop(reason="user_requested")

    def _save_lifecycle(self):
        if self.schedule:
            self.lifecycle["collection_protocol"] = self.schedule.snapshot()
        root = self.worker.config.data_dir if self.worker else self.config.data_dir
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / "dashboard_run.json.tmp"
        temporary.write_text(json.dumps(self.lifecycle, indent=2), encoding="utf-8")
        os.replace(temporary, root / "dashboard_run.json")

    def _run(self, provider):
        try:
            self.schedule = SessionSchedule(self.config, self.calendar, self.clock)
            self._save_lifecycle()
            while self.schedule.phase == "ARMED" and not self._stop.is_set():
                self.schedule.tick()
                self._stop.wait(.05)
            if self._stop.is_set() or self.schedule.phase == "FINALIZING":
                self.schedule.stop("user_requested" if self._stop.is_set() else "missed_window")
                self.schedule.finish(True)
                self.state = "STOPPED"
                self.lifecycle.update(stopped_at=self.clock.now().isoformat(), clean_shutdown=True)
                self._save_lifecycle()
                return
            instance = provider or self.provider_factory(self.config)
            if self.config.collection_mode != "Manual":
                instance.collection_schedule = self.schedule
            self.worker = RadarWorker(instance, self.config, schedule=self.schedule)
            self._save_lifecycle()
            # Worker owns its normal thread and uses the unchanged graceful close/export path.
            self.worker.start()
            if self._stop.is_set():
                self.worker.stop(reason="user_requested")
            saved_protocol_state = None
            while self.worker.running:
                self._sample_connection()
                ready = self.worker.phase == "RUNNING" and (
                    self.config.collection_mode == "Manual" or self.connection.get("connected") is True)
                if self.schedule.tick(ready) == "stop":
                    self._stop.set()
                    self.worker.stop(reason=self.schedule.snapshot()["stop_reason"])
                if not self._stop.is_set() and self.worker.phase == "RUNNING":
                    self.state = "RUNNING"
                elif self.worker.stop_event.is_set():
                    self.state = "STOPPING"
                p = self.schedule.snapshot()
                protocol_state = (self.schedule.phase, p["actual_start"], p["protocol_reason"], p["stop_reason"])
                if protocol_state != saved_protocol_state:
                    self._save_lifecycle()
                    saved_protocol_state = protocol_state
                self.worker.thread.join(.05)
            self._sample_connection()
            self.state = "ERROR" if self.worker.error else "STOPPING"
            self.lifecycle.update(stopped_at=datetime.now(timezone.utc).isoformat(),
                clean_shutdown=not bool(self.worker.error), shutdown_elapsed_ms=self.worker.shutdown_elapsed_ms)
            if not self.worker.error:
                self.connection = {"connected": False, "active_ws": 0}
            self._save_lifecycle()
            self.events.put(("collector_finished", self.worker.error or "정상 종료 · final export 완료"))
        except Exception as exc:
            self.state = "ERROR"
            if self.worker and self.worker.running:
                self.worker.stop(reason="controller_error")
                self.worker.thread.join()
            if self.schedule:
                self.schedule.stop("startup_error")
                self.schedule.finish(False)
                try:
                    self._save_lifecycle()
                except OSError:
                    pass
            # Provider exceptions may contain external text; do not expose arbitrary credentials.
            self.events.put(("collector_error", LATE_CAPACITY if str(exc) == LATE_CAPACITY else
                             f"수집 시작/종료 실패 ({type(exc).__name__}); 로컬 로그 확인"))
            return
        self.schedule.finish(not bool(self.worker.error))
        # Remain FINALIZING until exports, protocol persistence and Master import finish.
        final_phase = self.schedule.phase
        self.schedule.phase = "FINALIZING"
        try:
            self._save_lifecycle()
            self._persist_final_protocol()
            if not self.worker.error and self.worker.config.db_path.exists():
                items = import_session(self.worker.config.db_path, self.master_path, lifecycle=self.lifecycle)
                self.events.put(("imported", items))
        except Exception as exc:
            self.schedule.finish(False)
            final_phase = "ERROR"
            try:
                self._save_lifecycle()
                self._persist_final_protocol()
            except Exception:
                pass
            self.events.put(("import_failed", f"수집 완료, 최종 metadata/Master 등록 실패 ({type(exc).__name__}). Import Existing Session으로 재시도하세요."))
        self.schedule.phase = final_phase
        self.state = "ERROR" if self.worker.error else "STOPPED"
        # Final protocol was persisted before import. No extra disk write is needed for a phase-only change.

    def _persist_final_protocol(self):
        """Worker has closed SQLite; update only this newly-created run's public metadata."""
        import sqlite3
        from contextlib import closing
        path = self.worker.config.db_path
        if not path.exists():
            return
        protocol = self.schedule.snapshot()
        with closing(sqlite3.connect(path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='radar_metadata'").fetchone():
                return
            db.execute("INSERT OR REPLACE INTO radar_metadata VALUES('collection_protocol',?)", (json.dumps(protocol),))
            db.commit()
        for name in ("metadata.json", "quality_summary.json"):
            for path in (self.worker.config.data_dir / "export").glob("*/" + name):
                document = json.loads(path.read_text(encoding="utf-8"))
                document["collection_protocol"] = protocol
                temporary = path.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, path)

    def _sample_connection(self):
        getter = getattr(self.worker.provider, "connection_status", None)
        # Telemetry cannot interrupt the collector or its final export/import.
        try:
            self.connection = getter() if callable(getter) else {"connected": None, "active_ws": None}
        except Exception:
            self.connection = {"connected": None, "active_ws": None}
