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


class CollectorController:
    def __init__(self, master_path=DEFAULT_MASTER_PATH, provider_factory=create_live_provider):
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
        self.config = config
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
            self.state = "STOPPING"
            if self.worker:
                self.worker.stop(reason="dashboard")

    def _save_lifecycle(self):
        root = self.worker.config.data_dir if self.worker else self.config.data_dir
        root.mkdir(parents=True, exist_ok=True)
        temporary = root / "dashboard_run.json.tmp"
        temporary.write_text(json.dumps(self.lifecycle, indent=2), encoding="utf-8")
        os.replace(temporary, root / "dashboard_run.json")

    def _run(self, provider):
        try:
            self.worker = RadarWorker(provider or self.provider_factory(self.config), self.config)
            self._save_lifecycle()
            # Worker owns its normal thread and uses the unchanged graceful close/export path.
            self.worker.start()
            if self._stop.is_set():
                self.worker.stop(reason="dashboard")
            while self.worker.running:
                self._sample_connection()
                if not self._stop.is_set() and self.worker.phase == "RUNNING":
                    self.state = "RUNNING"
                elif self.worker.stop_event.is_set():
                    self.state = "STOPPING"
                self.worker.thread.join(.05)
            self._sample_connection()
            self.state = "ERROR" if self.worker.error else "STOPPED"
            self.lifecycle.update(stopped_at=datetime.now(timezone.utc).isoformat(),
                clean_shutdown=not bool(self.worker.error), shutdown_elapsed_ms=self.worker.shutdown_elapsed_ms)
            if not self.worker.error:
                self.connection = {"connected": False, "active_ws": 0}
            self._save_lifecycle()
            self.events.put(("collector_finished", self.worker.error or "정상 종료 · final export 완료"))
        except Exception as exc:
            self.state = "ERROR"
            # Provider exceptions may contain external text; do not expose arbitrary credentials.
            self.events.put(("collector_error", f"수집 시작/종료 실패 ({type(exc).__name__}); 로컬 로그 확인"))
            return
        if not self.worker.error:
            try:
                items = import_session(self.worker.config.db_path, self.master_path, lifecycle=self.lifecycle)
                self.events.put(("imported", items))
            except Exception as exc:
                self.events.put(("import_failed", f"수집 완료, Master 등록 실패 ({type(exc).__name__}). Import Existing Session으로 재시도하세요."))

    def _sample_connection(self):
        getter = getattr(self.worker.provider, "connection_status", None)
        # Telemetry cannot interrupt the collector or its final export/import.
        try:
            self.connection = getter() if callable(getter) else {"connected": None, "active_ws": None}
        except Exception:
            self.connection = {"connected": None, "active_ws": None}
