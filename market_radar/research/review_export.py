"""Allowlisted review artifacts, scanned before publication; no DB/raw/auth files."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

EXPORT_FILES = ("quality_summary.json", "candidates.csv", "snapshots.csv", "events.csv", "results.csv", "metadata.json", "validation_summary.json")
# Fail closed: even a suspicious field name prevents sharing that artifact.
SENSITIVE = re.compile(r"(?i)(?:app[_ -]?(?:key|secret)|client[_ -]?secret|\bsecret\b|secret[_ -]?key|access[_ -]?token|refresh[_ -]?token|\btoken\b|authorization|bearer\s+|account(?:[_ -]?(?:id|no|number))?|acct[_ -]?(?:no|id)|계좌|비밀번호|password|api[_ -]?key|private[_ -]?key)")


def safe_name(value):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", str(value))[:100]


def create_review_zip(session, output_dir=None):
    root = Path(session["data_dir"]).resolve()
    exports = Path(session["export_path"]).resolve()
    exports.relative_to(root)
    files = [(exports / name, name) for name in EXPORT_FILES]
    log = Path(session["log_path"]).resolve() if session.get("log_path") else None
    if log:
        log.relative_to(root)
        if log.suffix != ".log":
            raise ValueError("Invalid session log path")
        files.append((log, "session.log"))
    output_dir = Path(output_dir) if output_dir else root / "reviews"
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"market_radar_review_{safe_name(session['session_date_et'])}_{safe_name(session['run_id'])}.zip"
    destination = output_dir / filename
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(),
        "session_date": session["session_date_et"], "run_id": session["run_id"],
        "test_version": session.get("test_version"), "schema_version": session.get("research_schema_version"),
        "max_ws_symbols": session.get("max_ws_symbols"), "clean_shutdown": session.get("clean_shutdown"),
        "validation_overall": None, "validator_version": None,
        "included_files": [], "missing_files": [], "consistency": "export files as read; active collection may update files"}
    handle, temporary = tempfile.mkstemp(prefix="review_", suffix=".tmp", dir=output_dir)
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, name in files:
                if not path.is_file():
                    manifest["missing_files"].append(name)
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Review file leaves session directory")
                digest, size = hashlib.sha256(), 0
                protocol_lines = []
                with path.open("rb") as stream, archive.open(name, "w") as target:
                    # Stream lines to avoid loading large snapshot CSV/logs in memory.
                    for line in stream:
                        if SENSITIVE.search(line.decode("utf-8-sig", errors="strict")):
                            raise ValueError(f"Sensitive content detected in {name}; ZIP was not published")
                        digest.update(line)
                        size += len(line)
                        target.write(line)
                        if name in ("metadata.json", "validation_summary.json"):
                            protocol_lines.append(line)
                if name == "metadata.json":
                    manifest["collection_protocol"] = json.loads(b"".join(protocol_lines).decode("utf-8-sig")).get("collection_protocol")
                if name == "validation_summary.json":
                    validation = json.loads(b"".join(protocol_lines).decode("utf-8-sig"))
                    if validation.get("run_id") != session["run_id"] or validation.get("session_date") != session["session_date_et"]:
                        raise ValueError("Validation identity mismatch; revalidate session")
                    from .validator import evidence_fingerprint
                    source = session.get("source_db_path")
                    if not source or validation.get("evidence_sha256") != evidence_fingerprint(source, session["session_date_et"]):
                        raise ValueError("Validation is stale; revalidate session")
                    manifest.update(validation_overall=validation["overall"], validator_version=validation["validator_version"])
                manifest["included_files"].append({"name": name, "size_bytes": size, "sha256": digest.hexdigest()})
            encoded = json.dumps(manifest, ensure_ascii=False, indent=2)
            if SENSITIVE.search(encoded):
                raise ValueError("Sensitive review metadata; ZIP was not published")
            archive.writestr("review_manifest.json", encoded)
        if not manifest["included_files"]:
            raise ValueError("No session exports/log available yet")
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
