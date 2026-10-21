"""Failed-run sharing archive, deliberately incompatible with a success ZIP.

Only allowlisted exports are read. Missing, foreign, malformed or sensitive files
are recorded as omissions, never repaired in place. A diagnostic archive proves
its own bytes/identity, not successful collection or valid strategy results.
"""
from datetime import date
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from market_radar.evidence_io import copied_database
from .core import threads_finished, paper_health
from .system import RunBlocked


def _code(exc):
    return str(exc) if isinstance(exc, RunBlocked) else type(exc).__name__


def validate_diagnostic_zip(path, run_id, day):
    from .artifacts import GROUPS, check_identity, inspect_text, require
    allowed = {f"{group}/{name}" for group, names in GROUPS.items() for name in names}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "diagnostic_duplicate_entry")
        require(set(names) <= allowed | {"diagnostics.json", "manifest.json", "one_hour_run_summary.json"}, "diagnostic_allowlist")
        require({"diagnostics.json", "manifest.json", "one_hour_run_summary.json"} <= set(names), "diagnostic_required")
        require(archive.testzip() is None, "diagnostic_crc")
        manifest = json.loads(archive.read("manifest.json"))
        require(manifest["archive_kind"] == "diagnostic" and manifest["status"] == "FAILED"
                and manifest["shutdown_eligible"] is False, "diagnostic_status")
        require(manifest["run_id"] == run_id and manifest["session_date"] == day, "diagnostic_identity")
        require(set(manifest["files"]) == set(names)-{"manifest.json"}, "diagnostic_manifest_files")
        for name in names:
            data = archive.read(name)
            actual = inspect_text(io.BytesIO(data))
            if name != "manifest.json":
                require(actual == manifest["files"][name], "diagnostic_hash_mismatch")
            if name.endswith(".json"):
                check_identity(json.loads(data.decode("utf-8-sig")), run_id, day)
            else:
                import csv
                reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
                require(bool(reader.fieldnames) and None not in reader.fieldnames, "invalid_csv_header")
                for row in reader:
                    require(None not in row, "invalid_csv_row")
                    check_identity(row, run_id, day)
        diagnostic = json.loads(archive.read("diagnostics.json"))
        require(diagnostic["status"] == "FAILED" and diagnostic["shutdown_eligible"] is False
                and diagnostic["run_id"] == run_id and diagnostic["session_date"] == day
                and bool(diagnostic["failures"]), "diagnostic_status")
        summary = json.loads(archive.read("one_hour_run_summary.json"))
        require(summary["status"] == "FAILED" and summary["shutdown_eligible"] is False
                and summary["archive_kind"] == "diagnostic" and summary["run_id"] == run_id
                and summary["session_date"] == day, "diagnostic_status")
    return manifest


def publish_diagnostic(runner, failure):
    from .artifacts import GROUPS, check_identity, inspect_text, local_file, require
    root, c = runner.folder.resolve(), runner.controller
    require(threads_finished(c), "worker_still_active")
    run_id, day = runner.summary.get("run_id"), runner.summary.get("session_date")
    require(isinstance(run_id, str) and re.fullmatch(r"[a-f0-9]{32}", run_id), "diagnostic_identity_unavailable")
    require(isinstance(day, str) and date.fromisoformat(day).isoformat() == day, "diagnostic_identity_unavailable")
    failures = list(dict.fromkeys([*runner.failures, failure]))
    require(all(isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_]+", code) for code in failures),
            "diagnostic_unsafe_failure_code")
    paper = c.paper
    evidence = dict(paper_health(paper), events={})
    for group, name in (("paper_v1", "paper.db"), ("rotation_v2", "review.db")):
        try:
            source = local_file(root/group/name, root)
            with copied_database(source) as db:
                events = [dict(timestamp_utc=row[0], event_type=row[1], reason=json.loads(row[2]).get("reason"))
                          for row in db.execute("SELECT timestamp_utc,event_type,evidence_json FROM paper_trade_events "
                                                "WHERE event_type IN ('DATA_INTEGRITY_INVALID','INTERRUPTED') ORDER BY event_id")]
            evidence["events"][group] = events
        except Exception as exc:
            evidence["events"][group] = dict(unavailable=_code(exc))
    folders = {group: root/group/"export"/day for group in GROUPS}
    opening = getattr(c, "opening_hour_result", {})
    opening_path = opening.get("export_path")
    folders["opening_hour"] = Path(opening_path) if opening_path else root/"opening_hour/export"/day/"unavailable"
    included, omitted, payloads = {}, {}, {}
    original_validation = None
    for group, names in GROUPS.items():
        for name in names:
            key, source = f"{group}/{name}", folders[group]/name
            try:
                local_file(source, root)
                require(source.resolve().is_relative_to((root/group/"export"/day).resolve()), "foreign_export_root")
                before = (source.stat().st_size, source.stat().st_mtime_ns)
                data = source.read_bytes()
                actual = inspect_text(io.BytesIO(data))
                if name.endswith(".json"):
                    document = json.loads(data.decode("utf-8-sig"))
                    check_identity(document, run_id, day)
                    if key == "radar/validation_summary.json":
                        original_validation = document
                else:
                    # Validate the captured bytes, not a second read of a changing file.
                    import csv
                    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
                    require(bool(reader.fieldnames) and None not in reader.fieldnames, "invalid_csv_header")
                    for row in reader:
                        require(None not in row, "invalid_csv_row")
                        check_identity(row, run_id, day)
                require(before == (source.stat().st_size, source.stat().st_mtime_ns), "export_changed_during_read")
                included[key], payloads[key] = actual, data
            except Exception as exc:
                omitted[key] = _code(exc)
    diagnostic = dict(diagnostic_schema=1, archive_kind="diagnostic", status="FAILED", shutdown_eligible=False,
        run_id=run_id, session_date=day, failures=failures, runtime=evidence,
        original_validator=original_validation, missing_or_rejected_exports=omitted,
        available_exports=sorted(included),
        data_scope={group: dict(included=sum(k.startswith(group+"/") for k in included),
                              expected=len(names),
                              usage="failure investigation; not a verified strategy session" if group in {"paper_v1", "rotation_v2"}
                              else "observed data only; preserve original quality and missing-value limitations")
                    for group, names in GROUPS.items()},
        verification_scope="Archive CRC/SHA256, export syntax and embedded identities; full success validation did not complete",
        original_files_modified=False)
    quality = runner.summary.get("collection_quality")
    if quality is None and all(key in payloads for key in ("opening_hour/candidates.csv", "opening_hour/bars_1m.csv")):
        try:
            from .quality import assess_collection
            from market_radar.models import instant
            with tempfile.TemporaryDirectory(prefix="diagnostic_quality_", dir=root) as temporary:
                temporary = Path(temporary)
                for name in ("candidates.csv", "bars_1m.csv"):
                    (temporary/name).write_bytes(payloads["opening_hour/"+name])
                with copied_database(local_file(root/"radar"/c.config.db_path.name, root)) as db:
                    quality = assess_collection(db, temporary/"candidates.csv", temporary/"bars_1m.csv",
                                                instant(runner.plan["open"]))
        except Exception as exc:
            diagnostic["collection_quality_unavailable"] = _code(exc)
    if quality is not None:
        diagnostic["collection_quality"] = quality
    payloads["diagnostics.json"] = json.dumps(diagnostic, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    included["diagnostics.json"] = inspect_text(io.BytesIO(payloads["diagnostics.json"]))
    destination = root/f"one_hour_diagnostic_{day}_{run_id}.zip"
    summary = dict(runner.summary, status="FAILED", shutdown_eligible=False, failures=failures,
                   archive_kind="diagnostic", diagnostic_archive=destination.name)
    if quality is not None:
        summary["collection_quality"] = quality
    payloads["one_hour_run_summary.json"] = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    included["one_hour_run_summary.json"] = inspect_text(io.BytesIO(payloads["one_hour_run_summary.json"]))
    manifest = dict(manifest_schema=1, archive_kind="diagnostic", status="FAILED", shutdown_eligible=False,
                    run_id=run_id, session_date=day, files=included)
    require(not destination.exists(), "diagnostic_already_exists")
    handle, temporary = tempfile.mkstemp(prefix="diagnostic_", suffix=".tmp", dir=root)
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(payloads.items()):
                archive.writestr(name, data)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        validate_diagnostic_zip(temporary, run_id, day)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        validate_diagnostic_zip(destination, run_id, day)
    finally:
        Path(temporary).unlink(missing_ok=True)
    from .core import atomic_json
    atomic_json(root/"one_hour_run_summary.json", summary)
    return destination, summary
