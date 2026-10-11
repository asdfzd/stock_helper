"""Explicit manual analyzer or opt-in post-session job, never a live event handler."""
from pathlib import Path

from .source import read_session
from .engine import analyze
from .storage import OpeningHourStore, DEFAULT_DB


def enabled_for(collection_mode, requested=False):
    return bool(requested and collection_mode == "Regular Session")


def analyze_session(session_dir, *, output_db=DEFAULT_DB, export_root=None, **kwargs):
    root = Path(session_dir).resolve()
    output = Path(output_db).resolve()
    export = Path(export_root).resolve() if export_root else output.parent/"export"
    if output.is_relative_to(root) or export.is_relative_to(root):
        raise ValueError("Opening Hour outputs must be outside the source session")
    inputs = [kwargs.get("cache_path"), *kwargs.get("paper_paths", ())]
    if any(p and output == Path(p).resolve() for p in inputs):
        raise ValueError("Output must not be an input database")
    evidence = read_session(root, **kwargs)
    if output == Path(evidence["source_cache_path"]).resolve():
        raise ValueError("Output must not be the source cache")
    result = analyze(evidence)
    store = OpeningHourStore(output)
    try:
        rid = store.save(result)
        folder = store.export(rid, export)
    finally:
        store.close()
    return dict(status=result["quality"]["status"], research_id=rid, export_path=str(folder),
                db_path=str(output), quality=result["quality"], summary=result["summary"])
