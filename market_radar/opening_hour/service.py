"""Explicit manual analyzer or opt-in post-session job, never a live event handler."""
from pathlib import Path
import tempfile
import shutil

from ..failure_evidence import diagnostic_stage, safe_path, path_present

from .source import read_session
from .engine import analyze
from .storage import OpeningHourStore, DEFAULT_DB


def enabled_for(collection_mode, requested=False):
    return bool(requested and collection_mode == "Regular Session")


def analyze_session(session_dir, *, output_db=DEFAULT_DB, export_root=None, diagnostics=None, **kwargs):
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.clear()  # A caller may reuse the container for a later explicit attempt.
    diagnostics.update(diagnostic_schema=1, status='IN_PROGRESS')
    try:
        with diagnostic_stage(diagnostics, 'resolve_paths'):
            diagnostics['paths'] = dict(session=safe_path(Path(session_dir).resolve()),
                                       output_db=safe_path(Path(output_db).resolve()))
        with diagnostic_stage(diagnostics, 'temporary_environment'):
            temporary = tempfile.gettempdir()
            diagnostics.update(temporary_directory=safe_path(temporary),
                               temporary_available_bytes=shutil.disk_usage(temporary).free)
        with diagnostic_stage(diagnostics, 'read_session'):
            result = _analyze_session(session_dir, output_db=output_db, export_root=export_root,
                                      diagnostics=diagnostics, **kwargs)
        diagnostics['status'] = result['status']
        diagnostics['phase'] = 'complete'
        return result
    except Exception as exc:
        diagnostics.update(status='INCOMPLETE', error=type(exc).__name__,
                           phase=diagnostics.get('exception', {}).get('phase', 'read_session'),
                           output_database_present=path_present(output_db))
        # Preserve the original exception type for callers, including errno/WinError.
        exc.opening_hour_diagnostics = diagnostics
        raise


def _analyze_session(session_dir, *, output_db, export_root, diagnostics, **kwargs):
    root = Path(session_dir).resolve()
    output = Path(output_db).resolve()
    export = Path(export_root).resolve() if export_root else output.parent/"export"
    if output.is_relative_to(root) or export.is_relative_to(root):
        raise ValueError("Opening Hour outputs must be outside the source session")
    inputs = [kwargs.get("cache_path"), *kwargs.get("paper_paths", ())]
    if any(p and output == Path(p).resolve() for p in inputs):
        raise ValueError("Output must not be an input database")
    evidence = read_session(root, diagnostics=diagnostics, **kwargs)
    if output == Path(evidence["source_cache_path"]).resolve():
        raise ValueError("Output must not be the source cache")
    with diagnostic_stage(diagnostics, 'analyze'):
        result = analyze(evidence)
    with diagnostic_stage(diagnostics, 'create_output', output_db=output):
        store = OpeningHourStore(output)
    try:
        with diagnostic_stage(diagnostics, 'save_output', output_db=output):
            rid = store.save(result)
        with diagnostic_stage(diagnostics, 'export_output', export=export):
            folder = store.export(rid, export)
    finally:
        with diagnostic_stage(diagnostics, 'close_output', output_db=output):
            store.close()
    return dict(status=result["quality"]["status"], research_id=rid, export_path=str(folder),
                db_path=str(output), quality=result["quality"], summary=result["summary"])
