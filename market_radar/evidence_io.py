"""Inspect finalized SQLite evidence on disposable DB/WAL copies only."""
from contextlib import closing, contextmanager
from pathlib import Path
import errno
import shutil
import sqlite3
import tempfile

from .failure_evidence import diagnostic_stage, safe_path


@contextmanager
def copied_database(path, *, diagnostics=None, phase='database'):
    # Opt-in for Opening only; other evidence readers retain their existing policy.
    with diagnostic_stage(diagnostics, phase+'_resolve', source=path):
        path = Path(path).resolve(strict=True)
    with diagnostic_stage(diagnostics, phase+'_copy', source=path):
        with _database_copy(path, diagnostics) as db:
            with diagnostic_stage(diagnostics, phase+'_read', source=path):
                yield db


@contextmanager
def _database_copy(path, diagnostics):
    path = Path(path).resolve(strict=True)
    sources = [path, Path(str(path) + "-wal")]
    def stamps():
        return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in sources]
    before = stamps()
    if diagnostics is not None:
        temporary = Path(tempfile.gettempdir()).resolve()
        source_bytes = sum(s[0] for s in before if s is not None)
        # DB + WAL coexist on the temporary volume. Reserve at least 64 MiB,
        # or 10%, for filesystem/SQLite overhead; this is not a quota guarantee.
        reserve = max(64 * 1024 * 1024, (source_bytes + 9) // 10)
        free = shutil.disk_usage(temporary).free
        check = dict(source=safe_path(path), temporary_directory=safe_path(temporary),
                     source_bytes=source_bytes, reserve_bytes=reserve,
                     required_bytes=source_bytes+reserve, available_bytes=free)
        diagnostics.setdefault('space_checks', []).append(check)
        if free < check['required_bytes']:
            diagnostics['failure_code'] = 'temporary_space_insufficient'
            raise OSError(errno.ENOSPC, 'Insufficient space for evidence copy', str(temporary))
    with tempfile.TemporaryDirectory(prefix="evidence_read_") as folder:
        target = Path(folder)/path.name
        shutil.copyfile(path, target)
        if sources[1].exists():
            shutil.copyfile(sources[1], Path(str(target) + "-wal"))
        if before != stamps():
            raise ValueError("Source changed during copy; retry after collection stops")
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            yield db
        if before != stamps():
            raise ValueError("Source changed during analysis; retry after collection stops")
