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
from .research.validator import revalidate_session
from .scheduling import SessionSchedule, SystemClock, validate_protocol_config, LATE_CAPACITY


class _MetadataWriteError(OSError):
    """Only controlled metadata names and OS codes may reach the UI."""
    def __init__(self, operation, name, cause):
        super().__init__(f"metadata {operation}: {name}; errno={cause.errno}; "
                         f"winerror={getattr(cause, 'winerror', None)}")


def _write_metadata(path, document):
    """Atomic publication; bounded retries for transient Windows file contention."""
    operation = "mkdir"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        operation = "write"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        operation = "replace"
        # Never truncate/rewrite the published file, even if it stays locked.
        delays = (.025, .05, .1)
        for attempt in range(len(delays) + 1):
            try:
                os.replace(temporary, path)
                return
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == len(delays):
                    raise
                time.sleep(delays[attempt])
    except OSError as exc:
        raise _MetadataWriteError(operation, path.name, exc) from exc


def _error_detail(exc):
    return str(exc) if isinstance(exc, _MetadataWriteError) else type(exc).__name__


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
        self.paper = None
        self.paper_enabled = False
        self.paper_path = None
        self.paper_telemetry_path = None
        self.paper_rotation_enabled = False
        self.paper_review_path = None
        self.opening_hour_enabled = False
        self.opening_hour_path = None
        self.opening_hour_result = {"status": "OFF"}
        self.operational_diagnostics = None

    @property
    def session_phase(self):
        return self.schedule.phase if self.schedule else self.state

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    @property
    def running(self):
        return self.busy

    def start(self, config, provider=None, *, fresh_directory=True, paper_enabled=False, paper_rotation_enabled=False,
              opening_hour_enabled=False):
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
        self.paper_enabled = bool((paper_enabled or paper_rotation_enabled) and config.collection_mode != "Capacity Test")
        self.paper_rotation_enabled = bool(paper_rotation_enabled and self.paper_enabled)
        self.opening_hour_enabled = bool(opening_hour_enabled and config.collection_mode == "Regular Session")
        self.opening_hour_result = {"status": "ENABLED" if self.opening_hour_enabled else "OFF"}
        self.paper = None
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
            if self.operational_diagnostics is not None:
                from .run_diagnostics import call_path, worker_state
                self.operational_diagnostics.record('controller_stop', self.session_phase,
                    caller=call_path(), workers=worker_state(self))
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
        _write_metadata(root / "dashboard_run.json", self.lifecycle)

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
            if self.paper_enabled and instance.name == "kiwoom" and instance.mode == "live":
                from .paper_rotation_v2.runtime import PairedRuntime, DEFAULT_PAPER_PATH
                self.paper = PairedRuntime(self.config, self.lifecycle["run_id"],
                    self.paper_path or DEFAULT_PAPER_PATH, clock=self.clock.now,
                    review_path=self.paper_review_path, rotation_enabled=self.paper_rotation_enabled,
                    telemetry_path=self.paper_telemetry_path)
                self.paper.attach(instance)
            self.worker = RadarWorker(instance, self.config, schedule=self.schedule, paper=self.paper)
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
            if self.paper is not None:
                self.paper.finish()
            self.state = "ERROR" if self.worker.error else "STOPPING"
            self.lifecycle.update(stopped_at=datetime.now(timezone.utc).isoformat(),
                clean_shutdown=not bool(self.worker.error), shutdown_elapsed_ms=self.worker.shutdown_elapsed_ms)
            if not self.worker.error:
                self.connection = {"connected": False, "active_ws": 0}
            self._save_lifecycle()
            self.events.put(("collector_finished", self.worker.error or "정상 종료 · final export 완료"))
        except Exception as exc:
            if self.operational_diagnostics is not None:
                from .failure_evidence import exception_evidence
                self.operational_diagnostics.record('controller_exception', self.session_phase,
                    error=True, exception=exception_evidence(exc, 'controller'))
            self.state = "ERROR"
            if self.worker and self.worker.running:
                self.worker.stop(reason="controller_error")
                self.worker.thread.join()
            if self.paper is not None:
                self.paper.finish()
            if self.schedule:
                self.schedule.stop("startup_error")
                self.schedule.finish(False)
                try:
                    self._save_lifecycle()
                except OSError:
                    pass
            # Provider exceptions may contain external text; do not expose arbitrary credentials.
            self.events.put(("collector_error", LATE_CAPACITY if str(exc) == LATE_CAPACITY else
                             f"수집 시작/종료 실패 ({_error_detail(exc)}); 로컬 로그 확인"))
            return
        self.schedule.finish(not bool(self.worker.error))
        # Remain FINALIZING until exports, protocol persistence and Master import finish.
        final_phase = self.schedule.phase
        self.schedule.phase = "FINALIZING"
        items = []
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
            self.events.put(("import_failed", f"수집 완료, 최종 metadata/Master 등록 실패 ({_error_detail(exc)}). Import Existing Session으로 재시도하세요."))
        # Validation is downstream of successful export/import. Its failure must never
        # rewrite collector metadata, protocol, source DB, or successful exports.
        validation_items = items or ([dict(day=self.schedule.snapshot()["session_date_et"], run_id=None)]
                                    if self.worker.config.db_path.exists() else [])
        for item in validation_items:
            try:
                root = self.worker.config.data_dir
                summary = revalidate_session(dict(source_db_path=str(self.worker.config.db_path),
                    session_date_et=item["day"], run_id=item["run_id"], export_path=str(root / "export" / item["day"])),
                    self.master_path if items else None)
                self.events.put(("validated", summary))
            except Exception as exc:
                self.events.put(("validation_failed", f"Session validation unavailable ({type(exc).__name__}); Revalidate Session으로 재시도하세요."))
        # Provider and Paper have finished before this optional read-only research job.
        # No research computation or hook is added to the live market event path.
        self._analyze_opening_hour()
        self.schedule.phase = final_phase
        self.state = "ERROR" if self.worker.error else "STOPPED"
        # Final protocol was persisted before import. No extra disk write is needed for a phase-only change.

    def _analyze_opening_hour(self):
        if not self.opening_hour_enabled:
            return
        if not self.worker or self.worker.running:
            raise RuntimeError("Opening research requires a stopped collector")
        from .failure_evidence import exception_evidence, safe_path, path_present, write_diagnostics
        from .opening_hour.storage import DEFAULT_DB
        output = self.opening_hour_path or DEFAULT_DB
        diagnostics = dict(diagnostic_schema=1, paths={'output_db': safe_path(output)})
        try:
            from .opening_hour.service import analyze_session
            paper_paths = (self.paper.path, self.paper.review_path) if self.paper is not None else ()
            self.opening_hour_result = analyze_session(self.worker.config.data_dir,
                day=self.schedule.snapshot()["session_date_et"], cache_path=self.config.cache_path,
                paper_paths=paper_paths, output_db=output, calendar=self.calendar, diagnostics=diagnostics)
        except Exception as exc:
            # Analysis failure is separate from collection/validation and never changes their evidence.
            diagnostics.update(status='INCOMPLETE', error=type(exc).__name__,
                               output_database_present=path_present(output))
            if 'exception' not in diagnostics:
                diagnostics['exception'] = exception_evidence(exc, 'opening_analysis')
            self.opening_hour_result = {"status": "INCOMPLETE", "error": type(exc).__name__}
        diagnostics.update(run_id=self.lifecycle.get('run_id'),
                           session_date=self.schedule.snapshot()['session_date_et'])
        self.opening_hour_result['diagnostics'] = diagnostics
        # New evidence beside this run's lifecycle, even if the output directory failed.
        try:
            write_diagnostics(self.worker.config.data_dir/'opening_hour_diagnostics.json', diagnostics)
        except Exception as exc:
            # Keep both errors in memory; missing durable evidence blocks safe preservation.
            diagnostics['persistence_exception'] = exception_evidence(exc, 'write_opening_diagnostics')

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
                _write_metadata(path, document)

    def _sample_connection(self):
        getter = getattr(self.worker.provider, "connection_status", None)
        # Telemetry cannot interrupt the collector or its final export/import.
        try:
            self.connection = getter() if callable(getter) else {"connected": None, "active_ws": None}
        except Exception:
            self.connection = {"connected": None, "active_ws": None}
