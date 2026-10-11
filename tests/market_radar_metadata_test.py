"""Offline controller metadata publication: contention, integrity and safe errors."""
from contextlib import closing
from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from market_radar.config import CONFIG
from market_radar.controller import CollectorController, _MetadataWriteError, _write_metadata
from tests.market_radar_schedule_test import Clock, SyntheticLive


def denied(code=5):
    error = PermissionError(errno.EACCES, "private external error text")
    if code is not None:
        error.winerror = code
    return error


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "dashboard_run.json"
        self.path.write_text('{"old": true}', encoding="utf-8")
        self.original = self.path.read_bytes()

    def test_transient_windows_denial_keeps_old_json_until_atomic_success(self):
        actual_replace = os.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                self.path.write_bytes(self.original)
                attempts = []
                def publish(source, target):
                    attempts.append(1)
                    self.assertEqual(self.path.read_bytes(), self.original)
                    if len(attempts) < 3:
                        raise denied(code)
                    return actual_replace(source, target)
                with patch("market_radar.controller.os.replace", side_effect=publish), \
                     patch("market_radar.controller.time.sleep") as sleep:
                    _write_metadata(self.path, {"new": True})
                self.assertEqual(len(attempts), 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(json.loads(self.path.read_text()), {"new": True})
                self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_permanent_denial_is_bounded_preserves_original_and_redacts_error(self):
        with patch("market_radar.controller.os.replace", side_effect=denied()) as publish, \
             patch("market_radar.controller.time.sleep") as sleep:
            with self.assertRaises(_MetadataWriteError) as caught:
                _write_metadata(self.path, {"new": True})
        self.assertEqual(publish.call_count, 4)
        self.assertLessEqual(sum(call.args[0] for call in sleep.call_args_list), .2)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertIn("replace: dashboard_run.json", str(caught.exception))
        self.assertIn("winerror=5", str(caught.exception))
        self.assertNotIn("private external", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_other_os_failures_are_not_retried(self):
        for error in (denied(None), denied(1314), OSError(errno.ENOSPC, "disk full")):
            with self.subTest(error=type(error).__name__, code=getattr(error, "winerror", None)):
                with patch("market_radar.controller.os.replace", side_effect=error) as publish, \
                     patch("market_radar.controller.time.sleep") as sleep:
                    with self.assertRaises(_MetadataWriteError):
                        _write_metadata(self.path, {"new": True})
                publish.assert_called_once()
                sleep.assert_not_called()
                self.assertEqual(self.path.read_bytes(), self.original)

    def test_temporary_write_failure_is_not_mistaken_for_replace_contention(self):
        with patch.object(Path, "write_text", side_effect=denied()), \
             patch("market_radar.controller.os.replace") as publish:
            with self.assertRaisesRegex(_MetadataWriteError, "metadata write:"):
                _write_metadata(self.path, {"new": True})
        publish.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_directory_failure_reports_stage_without_external_message(self):
        with patch.object(Path, "mkdir", side_effect=denied()):
            with self.assertRaisesRegex(_MetadataWriteError, "metadata mkdir:") as caught:
                _write_metadata(self.path, {})
        self.assertNotIn("private external", str(caught.exception))

    def test_final_protocol_exports_use_same_atomic_recovery(self):
        controller = CollectorController(self.root / "master.db")
        config = replace(CONFIG, data_dir=self.root)
        controller.worker = SimpleNamespace(config=config)
        controller.schedule = SimpleNamespace(snapshot=lambda: {"phase": "STOPPED"})
        with closing(sqlite3.connect(config.db_path)) as db:
            db.execute("CREATE TABLE radar_metadata (key TEXT PRIMARY KEY, value TEXT)")
            db.commit()
        folder = self.root / "export" / "2026-09-28"
        folder.mkdir(parents=True)
        for name in ("metadata.json", "quality_summary.json"):
            (folder / name).write_text('{"existing": 7}', encoding="utf-8")
        actual_replace, attempts = os.replace, {}
        def publish(source, target):
            attempts[target.name] = attempts.get(target.name, 0) + 1
            if attempts[target.name] == 1:
                raise denied()
            return actual_replace(source, target)
        with patch("market_radar.controller.os.replace", side_effect=publish), \
             patch("market_radar.controller.time.sleep"):
            controller._persist_final_protocol()
        self.assertEqual(attempts, {"metadata.json": 2, "quality_summary.json": 2})
        for path in folder.glob("*.json"):
            self.assertEqual(json.loads(path.read_text()),
                             {"existing": 7, "collection_protocol": {"phase": "STOPPED"}})


class ControllerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = Clock()
        self.controller = CollectorController(self.root / "master.db", clock=self.clock)
        self.provider = SyntheticLive(self.clock)
        self.config = replace(CONFIG, data_dir=self.root / "runs",
                              historical_cache_path=self.root / "cache.db", collection_mode="Capacity Test")

    def tearDown(self):
        self.controller.stop()
        if self.controller.thread:
            self.controller.thread.join(10)
        self.tmp.cleanup()

    def test_transient_denial_does_not_abort_collection(self):
        actual_replace, failures = os.replace, []
        def publish(source, target):
            if Path(target).name == "dashboard_run.json" and len(failures) < 2:
                failures.append(1)
                raise denied()
            return actual_replace(source, target)
        with patch("market_radar.controller.os.replace", side_effect=publish):
            self.controller.start(self.config, self.provider)
            deadline = time.monotonic() + 8
            while self.controller.session_phase != "PREOPEN_WAIT" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.controller.session_phase, "PREOPEN_WAIT")
            self.controller.stop()
            self.controller.thread.join(8)
        self.assertEqual(len(failures), 2)
        self.assertEqual(self.controller.state, "STOPPED")
        self.assertIsNone(self.controller.worker.error)
        self.assertTrue(self.controller.lifecycle["clean_shutdown"])
        self.assertFalse(any(kind == "collector_error" for kind, _ in self.controller.events.queue))

    def test_persistent_denial_stops_with_safe_actionable_diagnostic(self):
        with patch("market_radar.controller.os.replace", side_effect=denied()):
            self.controller.start(self.config, self.provider)
            self.controller.thread.join(8)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.state, "ERROR")
        self.assertIsNone(self.controller.worker)
        messages = [message for kind, message in self.controller.events.queue if kind == "collector_error"]
        self.assertEqual(len(messages), 1)
        self.assertIn("metadata replace: dashboard_run.json", messages[0])
        self.assertIn("winerror=5", messages[0])
        self.assertNotIn("private external", messages[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
