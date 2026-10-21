"""Diagnostics without exception messages, source lines, locals or credentials."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import traceback


def safe_path(value):
    from .research.review_export import SENSITIVE
    text = str(value).replace(str(Path.home()), '<home>')
    # Paths and frame names are untrusted too. Never retain credential-like text.
    if SENSITIVE.search(text) or re.search(r'eyJ[\w-]+\.[\w-]+\.|[\w-]{40,}', text):
        return '[REDACTED]'
    if '://' in text or '=' in text or '?' in text:
        return '[REDACTED]'
    return re.sub(r'(?<!\d)\d(?:[- ]?\d){7,}(?!\d)', '[REDACTED]', text)


def path_present(path):
    try:
        return Path(path).exists()
    except OSError:
        return None  # Unknown is never accepted as proof of an absent output.


def write_diagnostics(path, value):
    """Atomic sidecar; clean only the uniquely named temporary created here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='opening_diagnostic_', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def exception_evidence(exc, phase):
    """Keep stack locations and OS codes; arbitrary messages cannot be sanitized reliably."""
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(dict(type=safe_path(type(current).__name__),
            errno=getattr(current, 'errno', None), winerror=getattr(current, 'winerror', None),
            filename=safe_path(current.filename) if getattr(current, 'filename', None) else None,
            filename2=safe_path(current.filename2) if getattr(current, 'filename2', None) else None,
            traceback=[dict(file=safe_path(f.filename), line=f.lineno, function=safe_path(f.name))
                       for f in traceback.extract_tb(current.__traceback__)], message_omitted=True))
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return dict(phase=phase, occurred_at_utc=datetime.now(timezone.utc).isoformat(), **chain[0], chain=chain[1:])


@contextmanager
def diagnostic_stage(diagnostics, phase, **paths):
    if diagnostics is not None:
        diagnostics['phase'] = phase
        diagnostics.setdefault('paths', {}).update({k: safe_path(v) for k, v in paths.items()})
    try:
        yield
    except Exception as exc:
        if diagnostics is not None and 'exception' not in diagnostics:
            diagnostics['exception'] = exception_evidence(exc, phase)
        raise
