"""Inspect finalized SQLite evidence on disposable DB/WAL copies only."""
from contextlib import closing, contextmanager
from pathlib import Path
import shutil
import sqlite3
import tempfile


@contextmanager
def copied_database(path):
    path = Path(path).resolve(strict=True)
    sources = [path, Path(str(path) + "-wal")]
    def stamps():
        return [(p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None for p in sources]
    before = stamps()
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
