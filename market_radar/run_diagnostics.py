"""Small durable operational records, independent of research DB transactions.

Only structured codes, booleans, clock samples and sanitized stack locations are
accepted by callers. Never persist exception messages, locals or market payloads.
"""
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import threading
import traceback

from .failure_evidence import exception_evidence, safe_path, write_diagnostics


def call_path():
    return [dict(file=safe_path(f.filename), function=safe_path(f.name), line=f.lineno)
            for f in traceback.extract_stack(limit=10)[:-1]]


class RunDiagnostics:
    def __init__(self, folder, clock):
        self.folder, self.clock = Path(folder), clock
        self.lock = threading.RLock()
        self.events = deque(maxlen=128)
        self.first_error = None
        self.write_errors = []
        self.sequence = 0
        self.context = {}

    def snapshot(self):
        with self.lock:
            return dict(diagnostic_schema=1, **self.context, sequence=self.sequence,
                        first_error=self.first_error, events=list(self.events),
                        persistence_errors=list(self.write_errors))

    def record(self, code, phase, *, error=False, **details):
        with self.lock:
            self.sequence += 1
            try:
                wall, mono = self.clock.now().isoformat(), self.clock.monotonic()
            except Exception:
                wall, mono = datetime.now(timezone.utc).isoformat(), None
            event = dict(sequence=self.sequence, code=safe_path(code), phase=safe_path(phase),
                         wall_utc=wall, monotonic_seconds=mono, **details)
            self.events.append(event)
            if error and self.first_error is None:
                self.first_error = event
            return self.persist()

    def persist(self):
        with self.lock:
            try:
                write_diagnostics(self.folder/'runtime.json', self.snapshot())
                return True
            except Exception as exc:
                if len(self.write_errors) < 8:
                    self.write_errors.append(exception_evidence(exc, 'persist_runtime'))
                return False  # Sticky: a later write must not conceal lost evidence.

    def freeze(self):
        """Immutable archive source; later OS results remain in runtime.json."""
        with self.lock:
            if self.write_errors:
                from tools.one_hour_runner.system import RunBlocked
                raise RunBlocked('diagnostics_write_failed')
            write_diagnostics(self.folder/'collection.json', self.snapshot())


def worker_state(controller):
    """No SQL against a live DB. Thread ownership is the write-in-progress fence."""
    if controller is None:
        return dict(controller_busy=False, worker_running=False, threads={},
                    database_writes='unknown')
    worker = getattr(controller, 'worker', None)
    paper = getattr(controller, 'paper', None)
    provider = getattr(worker, 'provider', None)
    objects = {'controller': controller, 'collector': worker, 'paper': paper,
               'provider': provider, 'ws': getattr(provider, 'ws', None)}
    threads = {}
    for label, obj in objects.items():
        for name in ('thread', 'io_thread'):
            thread = getattr(obj, name, None)
            if thread is not None:
                threads[label+'.'+name] = bool(thread.is_alive())
    busy = bool(controller.busy or getattr(worker, 'running', False) or any(threads.values()))
    return dict(controller_busy=bool(controller.busy), worker_running=bool(getattr(worker, 'running', False)),
                threads=threads, database_writes='possible' if busy else 'owners_finished',
                paper_error=bool(getattr(paper, 'error', False)),
                rotation_error=bool(getattr(paper, 'sidecar_error', False)))
