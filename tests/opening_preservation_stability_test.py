"""Synthetic evidence only: Opening failures, durable diagnostics and archive gates."""
import errno
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from market_radar.controller import CollectorController
from market_radar.failure_evidence import exception_evidence
from market_radar.opening_hour.service import analyze_session
from market_radar.one_hour_archive import (digest, validate_archive, local_inventory,
    OPENING_DB, OPENING_DIAGNOSTICS, OPENING_DIAGNOSTICS_SOURCE, MISSING_OPENING_POLICY)
from tools.one_hour_runner.artifacts import inspect_text
from tools.one_hour_runner.system import RunBlocked
from tests.opening_hour_research_test import make_source, file_state
from tests import one_hour_gui_test as gui


class OpeningFailureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.database, self.cache = make_source(self.root)
        self.output = self.root/'output/opening_hour.db'
        self.diagnostics = {}

    def analyze(self):
        return analyze_session(self.source, output_db=self.output, diagnostics=self.diagnostics)

    def test_space_preflight_records_db_and_wal_and_does_not_copy(self):
        # Synthetic WAL bytes need not be SQLite-readable: the gate precedes copy/open.
        Path(str(self.database)+'-wal').write_bytes(b'synthetic WAL')
        before = file_state(self.root)
        with patch('market_radar.evidence_io.shutil.disk_usage', return_value=SimpleNamespace(free=0)), \
             patch('market_radar.evidence_io.shutil.copyfile') as copy:
            with self.assertRaises(OSError) as caught:
                self.analyze()
        copy.assert_not_called()
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        d = self.diagnostics
        self.assertEqual(d['status'], 'INCOMPLETE')
        self.assertEqual(d['failure_code'], 'temporary_space_insufficient')
        check = d['space_checks'][0]
        self.assertEqual(check['source_bytes'], self.database.stat().st_size+13)
        self.assertEqual(check['required_bytes'], check['source_bytes']+check['reserve_bytes'])
        self.assertEqual(check['available_bytes'], 0)
        self.assertEqual(before, file_state(self.root))
        self.assertFalse(self.output.exists())

    def test_cache_oserror_has_phase_codes_stack_and_cleans_only_own_copy(self):
        temporary = self.root/'temporary'; temporary.mkdir()
        keep = temporary/'unrelated.txt'; keep.write_text('keep')
        before = file_state(self.root)
        real_copy = shutil.copyfile
        def broken(source, destination):
            if Path(source) == self.cache:
                Path(destination).write_bytes(b'partial copy')
                exc = OSError(errno.ENOSPC, 'Bearer SYNTHETIC_CREDENTIAL account=1234567890', str(source))
                exc.winerror = 112
                raise exc
            return real_copy(source, destination)
        with patch('tempfile.tempdir', str(temporary)), patch('market_radar.evidence_io.shutil.copyfile', side_effect=broken):
            with self.assertRaises(OSError):
                self.analyze()
        d = self.diagnostics
        self.assertEqual(d['exception']['phase'], 'cache_copy')
        self.assertEqual((d['exception']['errno'], d['exception']['winerror']), (errno.ENOSPC,112))
        self.assertTrue(d['exception']['traceback'])
        self.assertFalse(d['output_database_present'])
        self.assertEqual(before, file_state(self.root))
        self.assertEqual(list(temporary.iterdir()), [keep])
        encoded = json.dumps(d).encode()
        self.assertNotIn(b'SYNTHETIC_CREDENTIAL', encoded)
        self.assertNotIn(b'1234567890', encoded)
        inspect_text(io.BytesIO(encoded))

    def test_space_race_after_successful_preflight_is_still_recorded(self):
        with patch('market_radar.evidence_io.shutil.disk_usage', return_value=SimpleNamespace(free=10**12)), \
             patch('market_radar.evidence_io.shutil.copyfile', side_effect=PermissionError(errno.EACCES, 'synthetic denied')):
            with self.assertRaises(PermissionError):
                self.analyze()
        self.assertGreater(self.diagnostics['space_checks'][0]['available_bytes'], self.diagnostics['space_checks'][0]['required_bytes'])
        self.assertEqual(self.diagnostics['exception']['errno'], errno.EACCES)
        self.assertEqual(self.diagnostics['exception']['phase'], 'radar_copy')

    def test_output_creation_failure_is_distinct_from_copy_failure(self):
        before = file_state(self.root)
        with patch('market_radar.opening_hour.service.OpeningHourStore', side_effect=OSError(errno.ENOSPC, 'synthetic')):
            with self.assertRaises(OSError):
                self.analyze()
        self.assertEqual(self.diagnostics['exception']['phase'], 'create_output')
        self.assertEqual(before, file_state(self.root))

    def test_normal_analysis_retains_results_and_originals(self):
        before = file_state(self.root)
        first = self.analyze()
        second = self.analyze()
        self.assertEqual(first, second)
        self.assertEqual(self.diagnostics['status'], 'PARTIAL')
        self.assertEqual(self.diagnostics['phase'], 'complete')
        self.assertTrue(self.diagnostics['space_checks'])
        after = file_state(self.root)
        self.assertEqual(before, {p:after[p] for p in before})

    def test_explicit_retry_does_not_reuse_previous_error(self):
        with patch('market_radar.evidence_io.shutil.copyfile', side_effect=OSError(errno.ENOSPC, 'synthetic')):
            with self.assertRaises(OSError): self.analyze()
        self.assertIn('exception', self.diagnostics)
        self.analyze()
        self.assertNotIn('exception', self.diagnostics)
        self.assertNotIn('error', self.diagnostics)
        self.assertEqual(self.diagnostics['status'], 'PARTIAL')

    def test_atomic_diagnostic_failure_keeps_existing_file_and_cleans_only_own_temp(self):
        from market_radar.failure_evidence import write_diagnostics
        output = self.root/'diagnostics.json'; output.write_bytes(b'existing')
        unrelated = self.root/'opening_diagnostic_unrelated.tmp'; unrelated.write_bytes(b'keep')
        before = file_state(self.root)
        with patch('market_radar.failure_evidence.os.replace', side_effect=OSError(errno.ENOSPC, 'synthetic')):
            with self.assertRaises(OSError): write_diagnostics(output, {'status':'INCOMPLETE'})
        self.assertEqual(before, file_state(self.root))

    def test_chained_messages_source_lines_and_locals_are_not_serialized(self):
        try:
            try:
                raise ValueError('api_key=SYNTHETIC_CREDENTIAL')
            except ValueError as cause:
                raise OSError(errno.EACCES, 'account 1234567890', 'api_key=SYNTHETIC_CREDENTIAL') from cause
        except OSError as exc:
            record = exception_evidence(exc, 'copy')
        encoded = json.dumps(record).encode()
        self.assertNotIn(b'SYNTHETIC_CREDENTIAL', encoded)
        self.assertNotIn(b'1234567890', encoded)
        self.assertEqual(record['chain'][0]['type'], 'ValueError')
        inspect_text(io.BytesIO(encoded))


class MissingOpeningArchiveTests(unittest.TestCase):
    setUp = gui.IntegratedEvidenceTests.setUp

    def prepare(self):
        # The real Controller exception handler runs after the synthetic stores close.
        # Opening itself fails before any output DB is created, without deleting one.
        def through_controller(*args, **kwargs):
            self.c.opening_hour_enabled = True
            self.c.worker.config = self.c.config
            real_copy = shutil.copyfile
            def broken(source, destination):
                if Path(source) == self.c.config.cache_path:
                    raise OSError(errno.ENOSPC, 'synthetic copy failure', str(source))
                return real_copy(source, destination)
            with patch('market_radar.opening_hour.service.analyze_session', analyze_session), \
                 patch('market_radar.evidence_io.shutil.copyfile', side_effect=broken):
                CollectorController._analyze_opening_hour(self.c)
            return self.c.opening_hour_result
        with patch('market_radar.opening_hour.service.analyze_session', side_effect=through_controller):
            gui.IntegratedEvidenceTests.prepare(self)

    def finish(self):
        self.prepare(); self.r.no_shutdown = True; self.r.tick()
        return self.r

    def validate(self, *, safe=True):
        return validate_archive(self.r.archive, self.r.summary['run_id'], self.r.summary['session_date'], self.r.folder, require_safe=safe)

    def test_missing_db_preserved_diagnostic_with_explicit_failure_and_original_hashes(self):
        self.prepare()
        before = file_state(self.r.folder)
        cache_before = hashlib.sha256(self.c.config.cache_path.read_bytes()).hexdigest()
        self.r.no_shutdown = True; self.r.tick()
        self.assertEqual(self.r.state, 'COMPLETE_NO_SHUTDOWN', self.r.reason)
        self.assertEqual(self.r.summary['status'], 'FAILED')
        self.assertEqual(self.r.summary['preservation_status'], 'VERIFIED')
        self.assertEqual(self.r.summary['opening_hour_status'], 'INCOMPLETE')
        self.assertTrue(self.r.summary['shutdown_disabled'])
        self.assertFalse((self.r.folder/OPENING_DB).exists())
        manifest = self.validate()
        self.assertEqual(manifest['evidence_policy'], MISSING_OPENING_POLICY)
        self.assertEqual(set(manifest['missing_originals']), {OPENING_DB})
        self.assertIn(OPENING_DIAGNOSTICS, manifest['essential'])
        after = file_state(self.r.folder)
        # Mutable supervisor status is separate from frozen research/error sources.
        for p in before:
            if p.replace('\\', '/') not in {'one_hour_run_summary.json', 'diagnostics/runtime.json'}:
                self.assertEqual(before[p], after[p], p)
        self.assertEqual(cache_before, hashlib.sha256(self.c.config.cache_path.read_bytes()).hexdigest())
        self.assertEqual(self.shutdown.mock_calls, [])

    def test_strict_inventory_still_requires_opening_db(self):
        self.prepare()
        with self.assertRaisesRegex(RunBlocked, 'local_original_missing'):
            local_inventory(self.r.folder)

    def test_missing_failure_record_blocks_and_records_first_and_retry(self):
        self.prepare(); (self.r.folder/OPENING_DIAGNOSTICS_SOURCE).unlink()
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.assertFalse(self.r.summary['shutdown_eligible'])
        self.assertEqual([x['phase'] for x in self.r.summary['archive_exceptions']], ['primary_finalization','diagnostic_retry'])
        self.shutdown.request.assert_not_called()

    def test_wrong_failure_identity_blocks(self):
        self.prepare()
        p = self.r.folder/OPENING_DIAGNOSTICS_SOURCE
        value = json.loads(p.read_text()); value['run_id'] = 'b'*32
        p.write_text(json.dumps(value))
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.shutdown.request.assert_not_called()

    def test_missing_db_cannot_be_relabelled_as_normal_research(self):
        self.finish()
        with zipfile.ZipFile(self.r.archive) as z: entries = {n:z.read(n) for n in z.namelist()}
        m = json.loads(entries['manifest.json']); m['archive_kind'] = 'research'
        entries['manifest.json'] = json.dumps(m).encode()
        with zipfile.ZipFile(self.r.archive, 'w') as z:
            for name,data in entries.items(): z.writestr(name,data)
        with self.assertRaises(RunBlocked): self.validate()
        self.shutdown.request.assert_not_called()

    def test_missing_core_export_blocks_operational_completion(self):
        self.prepare()
        (self.c.config.data_dir/'export'/self.r.plan['session_date']/'snapshots.csv').unlink()
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.assertFalse(self.r.summary['shutdown_eligible'])
        self.assertIsNotNone(self.r.archive)
        self.validate(safe=False)
        with self.assertRaises(RunBlocked): self.validate()
        self.shutdown.request.assert_not_called()

    def test_corrupt_core_database_blocks_operational_completion(self):
        self.prepare(); self.c.paper.path.write_bytes(b'corrupt synthetic database')
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.assertFalse(self.r.summary['shutdown_eligible'])
        self.shutdown.request.assert_not_called()

    def test_failed_radar_integrity_verdict_blocks_operational_completion(self):
        self.prepare()
        p = self.c.config.data_dir/'export'/self.r.plan['session_date']/'validation_summary.json'
        value = json.loads(p.read_text()); value['integrity']['status'] = 'FAIL'; p.write_text(json.dumps(value))
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.assertIn('radar_integrity_not_verified', self.r.summary['operational_failures'])
        self.shutdown.request.assert_not_called()

    def test_export_db_disagreement_blocks_operational_completion(self):
        self.prepare()
        p = self.c.paper.path.parent/'export'/self.r.plan['session_date']/'summary.json'
        value = json.loads(p.read_text()); value['synthetic_corruption'] = True; p.write_text(json.dumps(value))
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.assertIn('research_export_mismatch', self.r.summary['operational_failures'])
        self.shutdown.request.assert_not_called()

    def test_failure_record_modified_after_archive_blocks_final_gate(self):
        self.prepare(); self.r.tick()
        self.assertEqual(self.r.state, 'WAITING_SHUTDOWN', self.r.reason)
        p = self.r.folder/OPENING_DIAGNOSTICS_SOURCE; value = json.loads(p.read_text())
        value['exception']['errno'] = errno.EACCES; p.write_text(json.dumps(value))
        self.clock.advance(300); self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.shutdown.request.assert_not_called()

    def test_distinct_publisher_exceptions_are_saved_without_messages(self):
        self.prepare()
        with patch('market_radar.one_hour_archive.publish_archive', side_effect=[
            OSError(errno.ENOSPC, 'api_key=SYNTHETIC_A'), PermissionError(errno.EACCES, 'account=SYNTHETIC_B')]):
            self.r.tick()
        records = self.r.summary['archive_exceptions']
        self.assertEqual([x['errno'] for x in records], [errno.ENOSPC, errno.EACCES])
        self.assertEqual([x['phase'] for x in records], ['primary_finalization','diagnostic_retry'])
        saved = (self.r.folder/'one_hour_run_summary.json').read_bytes()
        self.assertNotIn(b'SYNTHETIC_A', saved); self.assertNotIn(b'SYNTHETIC_B', saved)
        self.shutdown.request.assert_not_called()

    def test_failed_diagnostic_write_is_not_success(self):
        with patch('market_radar.failure_evidence.write_diagnostics', side_effect=OSError(errno.ENOSPC, 'synthetic')):
            self.prepare()
        self.assertIn('persistence_exception', self.c.opening_hour_result['diagnostics'])
        self.r.tick()
        self.assertEqual(self.r.state, 'ERROR')
        self.shutdown.request.assert_not_called()

    def test_archive_identity_hash_crc_and_required_evidence(self):
        self.finish()
        self.assertEqual(self.r.state, 'COMPLETE_NO_SHUTDOWN', self.r.reason)
        with zipfile.ZipFile(self.r.archive) as z: original = {n:z.read(n) for n in z.namelist()}
        def write(entries):
            with zipfile.ZipFile(self.r.archive, 'w', zipfile.ZIP_STORED) as z:
                for name, data in entries.items(): z.writestr(name, data)
        for field, wrong in [('run_id','b'*32),('session_date','2026-10-13')]:
            with self.subTest(field=field):
                entries = dict(original); m = json.loads(entries['manifest.json']); m[field] = wrong
                entries['manifest.json'] = json.dumps(m).encode(); write(entries)
                with self.assertRaisesRegex(RunBlocked, 'archive_identity'): self.validate()
        entries = dict(original); entries['diagnostics.json'] += b' '; write(entries)
        with self.assertRaisesRegex(RunBlocked, 'archive_hash_mismatch'): self.validate()
        entries = dict(original); key = OPENING_DIAGNOSTICS if OPENING_DIAGNOSTICS in entries else 'radar/snapshots.csv'
        del entries[key]; m = json.loads(entries['manifest.json']); del m['files'][key]; del m['provenance'][key]
        entries['manifest.json'] = json.dumps(m).encode(); write(entries)
        with self.assertRaises(RunBlocked): self.validate()
        write(original)
        with zipfile.ZipFile(self.r.archive) as z: entry = z.getinfo('diagnostics.json')
        raw = bytearray(self.r.archive.read_bytes()); offset = entry.header_offset
        length, extra = struct.unpack_from('<HH', raw, offset+26)
        raw[offset+30+length+extra] ^= 1
        self.r.archive.write_bytes(raw)
        with self.assertRaises((RunBlocked, zipfile.BadZipFile)): self.validate()
        write(original); self.validate()
        self.assertEqual(self.shutdown.mock_calls, [])


class NormalArchiveRegressionTests(unittest.TestCase):
    setUp = gui.IntegratedEvidenceTests.setUp
    prepare = gui.IntegratedEvidenceTests.prepare
    validate = MissingOpeningArchiveTests.validate
    test_archive_identity_hash_crc_and_required_evidence = MissingOpeningArchiveTests.test_archive_identity_hash_crc_and_required_evidence

    def finish(self):
        self.prepare(); self.r.no_shutdown = True; self.r.tick()
        self.assertEqual(self.c.opening_hour_result['status'], 'COMPLETE')
        self.assertEqual(self.r.summary['status'], 'VERIFIED')
        self.assertEqual(self.validate()['evidence_policy'], 'complete')
        return self.r


if __name__ == '__main__':
    unittest.main()
