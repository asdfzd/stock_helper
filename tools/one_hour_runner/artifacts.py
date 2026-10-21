"""Allowlisted, identity-bound exports and an independently verified atomic ZIP."""
from contextlib import closing
from datetime import timedelta
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile

from market_radar.config import CONFIG
from market_radar.evidence_io import copied_database
from market_radar.models import instant
from market_radar.research.review_export import EXPORT_FILES, SENSITIVE, create_review_zip
from market_radar.research.validator import evidence_fingerprint

from .core import atomic_json, threads_finished, paper_health
from .system import RunBlocked
from .quality import assess_collection

PAPER = ("paper_trades.csv", "paper_trade_events.csv", "paper_horizons.csv", "paper_trade_marks.csv", "summary.json")
ROTATION = ("paper_trades.csv", "paper_rotation_switches.csv", "paper_rotation_comparison.csv",
            "paper_post_exit.csv", "paper_post_exit_marks.csv", "paper_trade_marks.csv", "paper_horizons.csv",
            "paper_daily_summary.json")
OPENING = ("candidates.csv", "bars_1m.csv", "bars_3m.csv", "bars_5m.csv", "events.csv", "time_snapshots.csv",
           "outcomes.csv", "volume_segments.csv", "metadata.json", "quality.json", "summary.json")
GROUPS = {"radar": EXPORT_FILES, "paper_v1": PAPER, "rotation_v2": ROTATION, "opening_hour": OPENING}
REQUIRED = {f"{group}/{name}" for group, names in GROUPS.items() for name in names} | {
    "radar/review.zip", "paper_v1/identity.json", "rotation_v2/identity.json", "one_hour_run_summary.json"}
EXTRA_SENSITIVE = re.compile(r"-----BEGIN .*PRIVATE KEY-----|\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.")
EXPECTED_PROTOCOL_ERRORS = {"protocol_non_compliant", "stop_outside_tolerance", "unexpected_stop_reason"}


def require(condition, code):
    if not condition:
        raise RunBlocked(code)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def local_file(path, root):
    path, root = Path(path), Path(root).resolve()
    require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root), "missing_or_foreign_file")
    return path


def source_db(path, root):
    local_file(path, root)
    # Even read-only WAL connections can create/update an original SHM file.
    return copied_database(path)


def check_identity(document, run_id, day, *, review_id=None):
    """Check embedded identity even inside JSON-encoded CSV fields."""
    if isinstance(document, dict):
        for key, value in document.items():
            if key in {"radar_run_id", "collector_run_id"}:
                require(value == run_id, "foreign_run_identity")
            if key in {"session_date_et", "session_date"}:
                require(value == day, "foreign_session_date")
            check_identity(value, run_id, day, review_id=review_id)
    elif isinstance(document, list):
        for item in document:
            check_identity(item, run_id, day, review_id=review_id)
    elif isinstance(document, str) and document.startswith(("{", "[")):
        try:
            value = json.loads(document)
        except ValueError:
            return
        check_identity(value, run_id, day, review_id=review_id)


def inspect_text(stream):
    digest, size = hashlib.sha256(), 0
    for line in stream:
        value = line.decode("utf-8-sig", errors="strict")
        require(not SENSITIVE.search(value) and not EXTRA_SENSITIVE.search(value), "sensitive_artifact_content")
        digest.update(line)
        size += len(line)
    return dict(sha256=digest.hexdigest(), size_bytes=size)


def inspect_file(path):
    with Path(path).open("rb") as stream:
        return inspect_text(stream)


def _paper_database(path, root, run_id, day):
    with source_db(path, root) as db:
        require(db.execute("PRAGMA quick_check").fetchone()[0] == "ok", "paper_db_integrity")
        require(db.execute("PRAGMA user_version").fetchone()[0] == 2, "paper_schema")
        sessions = list(db.execute("SELECT radar_run_id,session_date_et,status FROM paper_sessions"))
        require(len(sessions) == 1 and tuple(sessions[0])[:2] == (run_id, day), "paper_session_identity")
        for table in ("paper_trades", "paper_trade_marks"):
            for row in db.execute(f"SELECT DISTINCT radar_run_id,session_date_et FROM {table}"):
                require(tuple(row) == (run_id, day), "paper_foreign_rows")
        require(not db.execute("SELECT 1 FROM paper_trades WHERE state='OPEN'").fetchone(), "paper_open_after_stop")
        for table in ("paper_trade_events", "paper_horizons"):
            require(not db.execute(f"SELECT 1 FROM {table} x LEFT JOIN paper_trades t ON x.trade_id=t.trade_id "
                                   "WHERE t.trade_id IS NULL").fetchone(), "paper_orphan_evidence")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("review_trades", "paper_post_exit", "rotation_switches", "rotation_details"):
            if table in tables:
                for row in db.execute(f"SELECT detail_json FROM {table}"):
                    check_identity(json.loads(row[0]), run_id, day)


def _check_csv(path, run_id, day):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        require(bool(reader.fieldnames) and None not in reader.fieldnames, "invalid_csv_header")
        for row in reader:
            require(None not in row, "invalid_csv_row")
            check_identity(row, run_id, day)


def _paper_csv_matches(path, db_path):
    """Raw table exports must match all committed rows, including header-only results."""
    table = path.stem
    with copied_database(db_path) as db:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            cursor = db.execute("SELECT * FROM "+table+(" ORDER BY observation_id" if table == "paper_trade_marks" else ""))
            require(next(reader) == [d[0] for d in cursor.description], "paper_export_columns")
            # Hash multisets of rows so CSV ordering never changes the evidence test.
            def row_hash(row):
                return hashlib.sha256(json.dumps(["" if v is None else str(v) for v in row], ensure_ascii=False).encode()).hexdigest()
            from collections import Counter
            require(Counter(map(row_hash, reader)) == Counter(map(row_hash, cursor)), "paper_export_rows")


def verify_research_exports(c, root, day, files, *, inspect=inspect_file, include_opening=True):
    """Re-export committed copies using the original public exporters, then compare.

    This also checks fields without run IDs (horizons/comparisons/summary), empty
    CSVs, and Opening counts/values. No copied row is accepted just on its name.
    """
    from market_radar.paper.storage import PaperStore
    from market_radar.paper_rotation_v2.storage import RotationStore
    from market_radar.opening_hour.storage import OpeningHourStore
    with tempfile.TemporaryDirectory(prefix="verify_exports_", dir=root) as temporary:
        temporary = Path(temporary)
        def copy_database(source, name):
            target = temporary/(name+".db")
            with source_db(source, root) as src, closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
            return target
        baseline = PaperStore(copy_database(c.paper.path, "paper"), result_tolerance_seconds=c.config.result_tolerance_seconds)
        try:
            baseline_summary = baseline.summary(day)
            baseline.export(day, temporary/"paper_v1")
        finally:
            baseline.close()
        rotation = RotationStore(copy_database(c.paper.review_path, "rotation"), result_tolerance_seconds=c.config.result_tolerance_seconds)
        try:
            rotation.export_review(day, temporary/"rotation_v2", baseline_summary)
        finally:
            rotation.close()
        groups = ['paper_v1', 'rotation_v2']
        if include_opening:
            opening = OpeningHourStore(copy_database(c.opening_hour_result["db_path"], "opening"))
            try:
                expected_opening = opening.export(c.opening_hour_result["research_id"], temporary/"opening_hour")
            finally:
                opening.close()
            groups.append('opening_hour')
        for group in groups:
            folder = expected_opening if group == "opening_hour" else temporary/group
            for name in GROUPS[group]:
                require(inspect(folder/name) == inspect(files[group+"/"+name]), "research_export_mismatch")


def collect_evidence(runner, *, inspect=inspect_file, include_review=True):
    c, root, plan = runner.controller, runner.folder, runner.plan
    require(threads_finished(c), "worker_still_active")
    require(c.lifecycle.get("clean_shutdown") is True and c.state == "STOPPED" and not c.worker.error,
            "collector_not_clean")
    require(not paper_health(c.paper)["failed"], "paper_processing_error")
    require(c.opening_hour_result.get("status") in {"COMPLETE", "PARTIAL", "EMPTY"}, "opening_processing_error")
    run_id, day = c.lifecycle["run_id"], plan["session_date"]
    require(runner.summary["run_id"] == run_id, "controller_identity_changed")
    require(c.config.data_dir.resolve() == (root/"radar").resolve(), "foreign_radar_root")
    require(Path(c.paper.path).resolve() == (root/"paper_v1/paper.db").resolve(), "foreign_paper_root")
    require(Path(c.paper.review_path).resolve() == (root/"rotation_v2/review.db").resolve(), "foreign_rotation_root")
    lifecycle = read_json(local_file(root/"radar/dashboard_run.json", root))
    require(lifecycle["run_id"] == run_id and lifecycle["clean_shutdown"] is True, "lifecycle_identity")
    protocol = lifecycle["collection_protocol"]
    require(protocol == c.schedule.snapshot(), "protocol_not_finalized")
    folders = dict(radar=root/"radar/export"/day, paper_v1=root/"paper_v1/export"/day,
                   rotation_v2=root/"rotation_v2/export"/day,
                   opening_hour=Path(c.opening_hour_result["export_path"]))
    require(folders["opening_hour"].resolve().is_relative_to((root/"opening_hour/export"/day).resolve()),
            "foreign_opening_root")
    require(Path(c.opening_hour_result["db_path"]).resolve() == (root/"opening_hour/opening_hour.db").resolve(),
            "foreign_opening_database")
    files = {f"{group}/{name}": local_file(folder/name, root)
             for group, folder in folders.items() for name in GROUPS[group]}
    for name, path in files.items():
        inspect(path)
        if path.suffix == ".csv":
            _check_csv(path, run_id, day)
        else:
            check_identity(read_json(path), run_id, day)
    meta = read_json(files["radar/metadata.json"])
    quality = read_json(files["radar/quality_summary.json"])
    validation = read_json(files["radar/validation_summary.json"])
    opening = read_json(files["opening_hour/metadata.json"])
    opening_quality = read_json(files["opening_hour/quality.json"])
    require(meta["collection_protocol"] == protocol == quality["collection_protocol"], "protocol_export_mismatch")
    require(meta["test_version"] == CONFIG_TEST_VERSION and meta["schema_version"] == 4 and meta["max_ws_symbols"] == 150,
            "radar_policy_mismatch")
    require(opening["radar_run_id"] == run_id and opening["research_id"] == c.opening_hour_result["research_id"],
            "opening_identity_mismatch")
    require(opening_quality == c.opening_hour_result["quality"], "opening_quality_mismatch")
    require(instant(opening["regular_open_utc"]) == instant(plan["open"]) and
            instant(opening["opening_hour_end_utc"]) == instant(plan["opening_hour_end"]), "opening_window_mismatch")
    require(validation["session_date"] == day and validation["evidence_sha256"] == evidence_fingerprint(c.config.db_path, day),
            "stale_validation")
    with source_db(c.config.db_path, root) as db:
        identity = json.loads(db.execute("SELECT value FROM radar_metadata WHERE key='identity'").fetchone()[0])
        first = db.execute("SELECT MIN(timestamp_utc) FROM radar_batches").fetchone()[0]
        origin = json.dumps(identity, sort_keys=True)+"|"+(first or run_id)
        review_id = hashlib.sha256((origin+"|"+day).encode()).hexdigest()[:32]
        require(review_id == validation["run_id"], "review_identity_mismatch")
        observed = db.execute("SELECT MIN(timestamp_utc),MAX(timestamp_utc),COUNT(*) FROM radar_batches").fetchone()
        snapshots = db.execute("SELECT MIN(timestamp_utc),MAX(timestamp_utc),COUNT(*) FROM radar_snapshots").fetchone()
        collection_quality = assess_collection(db, files["opening_hour/candidates.csv"],
                                              files["opening_hour/bars_1m.csv"], instant(plan["open"]))
    for group, db_path in (("paper_v1", c.paper.path), ("rotation_v2", c.paper.review_path)):
        _paper_database(db_path, root, run_id, day)
        for name in (("paper_trades.csv", "paper_trade_events.csv", "paper_horizons.csv", "paper_trade_marks.csv")
                     if group == "paper_v1" else ("paper_trade_marks.csv", "paper_horizons.csv")):
            _paper_csv_matches(files[group+"/"+name], db_path)
        # Existing summary formats intentionally remain byte-for-byte unchanged.
        identity_path = folders[group]/"identity.json"
        atomic_json(identity_path, dict(collector_run_id=run_id, session_date=day, schema=2,
                                       isolation="new database for this run only"))
        files[group+"/identity.json"] = identity_path
    verify_research_exports(c, root, day, files, inspect=inspect)
    failures = list(runner.failures)
    failures.extend(reason for reason in collection_quality["failures"] if reason not in failures)
    def check(ok, reason):
        if not ok and reason not in failures:
            failures.append(reason)
    opened, stopped = instant(plan["open"]), instant(plan["stop"])
    check(protocol.get("collection_mode") == "Regular Session" and protocol.get("max_ws_symbols") == 150, "wrong_mode_or_slots")
    check(protocol.get("armed_before_open") is True and protocol.get("ready_before_open") is True, "not_ready_before_open")
    check(bool(protocol.get("connection_ready_at")) and instant(protocol["connection_ready_at"]) < opened, "connection_not_ready")
    start = instant(protocol["actual_start"]) if protocol.get("actual_start") else None
    stop = instant(protocol["actual_stop"]) if protocol.get("actual_stop") else None
    check(start is not None and 0 <= (start-opened).total_seconds() <= 1, "start_outside_tolerance")
    check(stop is not None and 0 <= (stop-stopped).total_seconds() <= 1, "stop_outside_one_hour_tolerance")
    check(runner.requested_stop_at is not None and stop is not None and
          abs((instant(runner.requested_stop_at)-stop).total_seconds()) <= 1, "stop_not_requested_by_runner")
    check(protocol.get("stop_reason") == "user_requested", "unexpected_stop")
    check(set(protocol.get("protocol_reason", "").split(";")) <= {"user_requested", "stop_time_mismatch"}, "other_protocol_failure")
    check(protocol.get("full_session") is False and protocol.get("protocol_compliant") is False, "early_stop_evidence_missing")
    check(quality["snapshot_count"] > 0 and snapshots[2] > 0 and observed[2] > 0, "no_research_observations")
    check(observed[0] is not None and opened <= instant(observed[0]) <= opened+LAST_BATCH_SLACK,
          "first_batch_late_or_missing")
    check(observed[1] is not None and stopped-LAST_BATCH_SLACK <= instant(observed[1]) <= stopped+TIMING_SLACK,
          "last_batch_late_or_missing")
    for error in validation.get("errors", []):
        check(error.get("section") == "protocol" and error.get("code") in EXPECTED_PROTOCOL_ERRORS,
              "validator_"+str(error.get("section"))+"_"+str(error.get("code")))
    for name in ("db_error_count", "worker_error_count", "export_error_count", "transport_error_count", "parse_error_count"):
        check(quality.get("runtime", {}).get(name) == 0, name)
    for name in ("ws_gap_count", "raw_dropped_count"):
        check(quality.get("market_data", {}).get(name) == 0, name)
    check(opening_quality["status"] in {"COMPLETE", "PARTIAL"} and opening_quality.get("actual_1m_rows", 0) > 0,
          "opening_hour_empty")
    summary = dict(runner.summary, status="FAILED" if failures else "VERIFIED", failures=failures,
        shutdown_eligible=not failures, shutdown_disabled=runner.no_shutdown, clean_shutdown=True,
        all_workers_stopped=True, run_id=run_id, session_date=day, review_run_id=review_id,
        early_stop_reason="planned_open_plus_65_minutes", controller_stop_reason=protocol.get("stop_reason"),
        actual_start=protocol.get("actual_start"), actual_stop=protocol.get("actual_stop"),
        stop_requested_at=runner.requested_stop_at, first_batch=observed[0], last_batch=observed[1],
        batch_count=observed[2], first_snapshot=snapshots[0], last_snapshot=snapshots[1], snapshot_count=snapshots[2],
        original_validation_overall=validation["overall"], original_validation_errors=validation["errors"],
        original_protocol_compliant=protocol.get("protocol_compliant"), full_session=False,
        first_60m_completeness=opening_quality,
        collection_quality=collection_quality,
        interpretation="planned shortened operational run; original Regular Session FAIL retained; no profitability claim",
        identity_mapping="run_id is Controller/Paper/Opening UUID; review_run_id is original Master/Validator content ID")
    session = dict(data_dir=str(root/"radar"), export_path=str(folders["radar"]), source_db_path=str(c.config.db_path),
                   session_date_et=day, run_id=review_id, clean_shutdown=True,
                   test_version=meta["test_version"], research_schema_version=4, max_ws_symbols=150)
    # Log deliberately omitted. The nested archive duplicates only the seven Radar exports.
    if include_review:
        review = create_review_zip(session, root/"reviews")
        files["radar/review.zip"] = local_file(review, root)
    return files, summary


CONFIG_TEST_VERSION = CONFIG.test_version
# First/last batch allow one 30s cadence plus the scheduler's 1s tolerance.
TIMING_SLACK = timedelta(seconds=1)
LAST_BATCH_SLACK = timedelta(seconds=CONFIG.snapshot_seconds+1)


def _nested_review(archive, manifest, outer):
    with zipfile.ZipFile(io.BytesIO(archive.read("radar/review.zip"))) as review:
        require(len(review.namelist()) == len(set(review.namelist())), "duplicate_review_entry")
        require(set(review.namelist()) == set(EXPORT_FILES)|{"review_manifest.json"}, "review_allowlist")
        require(review.testzip() is None, "review_crc")
        document = json.loads(review.read("review_manifest.json"))
        require(document["run_id"] == manifest["review_run_id"] and document["session_date"] == manifest["session_date"],
                "review_identity")
        require(document["missing_files"] == [], "review_missing_files")
        entries = {e["name"]: e for e in document["included_files"]}
        require(set(entries) == set(EXPORT_FILES), "review_manifest_files")
        for name in EXPORT_FILES:
            with review.open(name) as stream:
                actual = inspect_text(stream)
            require(actual == {k: entries[name][k] for k in actual} == outer["radar/"+name], "review_hash_mismatch")
        with review.open("review_manifest.json") as stream:
            inspect_text(stream)


def validate_zip(path, run_id, day):
    require(Path(path).is_file(), "zip_missing")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and set(names) == REQUIRED|{"manifest.json"}, "zip_allowlist")
        require(archive.testzip() is None, "zip_crc")
        manifest = json.loads(archive.read("manifest.json"))
        require(manifest["run_id"] == run_id and manifest["session_date"] == day, "zip_identity")
        require(set(manifest["files"]) == REQUIRED, "manifest_file_set")
        actual = {}
        for name in sorted(REQUIRED):
            with archive.open(name) as stream:
                if name.endswith(".zip"):
                    digest, size = hashlib.sha256(), 0
                    for block in iter(lambda: stream.read(1024*1024), b""):
                        digest.update(block); size += len(block)
                    value = dict(sha256=digest.hexdigest(), size_bytes=size)
                else:
                    value = inspect_text(stream)
            require(value == manifest["files"][name], "manifest_hash_mismatch")
            actual[name] = value
        with archive.open("manifest.json") as stream:
            inspect_text(stream)
        summary = json.loads(archive.read("one_hour_run_summary.json"))
        require(summary["run_id"] == run_id and summary["session_date"] == day and
                summary["review_run_id"] == manifest["review_run_id"], "summary_identity")
        # Validate semantic identity again from the actual archive, not just its source files.
        for name in REQUIRED:
            if name.endswith(".json"):
                check_identity(json.loads(archive.read(name)), run_id, day)
            elif name.endswith(".csv"):
                with archive.open(name) as binary, io.TextIOWrapper(binary, encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    require(bool(reader.fieldnames), "zip_csv_header")
                    for row in reader:
                        check_identity(row, run_id, day)
        require(json.loads(archive.read("radar/validation_summary.json"))["run_id"] == manifest["review_run_id"],
                "zip_validation_identity")
        require(json.loads(archive.read("opening_hour/metadata.json"))["radar_run_id"] == run_id, "zip_opening_identity")
        _nested_review(archive, manifest, actual)
    return manifest


def publish_run(runner):
    files, summary = collect_evidence(runner)
    if summary["shutdown_eligible"] is not True:
        from .diagnostics import publish_diagnostic
        runner.summary = summary
        runner.failures = list(summary["failures"])
        return publish_diagnostic(runner, summary["failures"][0])
    summary_path = runner.folder/"one_hour_run_summary.json"
    atomic_json(summary_path, summary)
    files["one_hour_run_summary.json"] = summary_path
    run_id, day = summary["run_id"], summary["session_date"]
    destination = runner.folder/f"one_hour_research_{day}_{run_id}.zip"
    require(not destination.exists(), "archive_already_exists")
    handle, temporary = tempfile.mkstemp(prefix="one_hour_", suffix=".tmp", dir=runner.folder)
    os.close(handle)
    try:
        manifest = dict(manifest_schema=1, run_id=run_id, review_run_id=summary["review_run_id"], session_date=day,
            files={}, duplicate_policy="radar/review.zip repeats the seven radar exports byte-for-byte; both hashes checked")
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(files.items()):
                archive.write(path, name)
            # Hash the bytes actually written to the archive, then revalidate on disk.
        with zipfile.ZipFile(temporary) as archive:
            for name in files:
                with archive.open(name) as stream:
                    digest, size = hashlib.sha256(), 0
                    for block in iter(lambda: stream.read(1024*1024), b""):
                        digest.update(block); size += len(block)
                    manifest["files"][name] = dict(sha256=digest.hexdigest(), size_bytes=size)
        with zipfile.ZipFile(temporary, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        validate_zip(temporary, run_id, day)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        validate_zip(destination, run_id, day)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination, summary
