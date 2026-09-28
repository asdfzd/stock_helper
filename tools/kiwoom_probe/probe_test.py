"""Offline safety checks; no real credentials or network access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from run_probe import Guard, Probe, READ_PATHS, load_specs


class ProbeSafetyTests(unittest.TestCase):
    def test_secret_redaction_nested_and_embedded(self):
        guard = Guard(["TEST_SECRET_ONLY", "TEST_TOKEN_ONLY"])
        value = {"token": "anything", "nested": [{"error": "echo TEST_SECRET_ONLY TEST_TOKEN_ONLY"}]}
        encoded = json.dumps(guard.clean(value))
        self.assertNotIn("TEST_SECRET_ONLY", encoded)
        self.assertNotIn("TEST_TOKEN_ONLY", encoded)
        self.assertNotIn("anything", encoded)

    def test_order_and_account_trs_rejected_before_network(self):
        probe = Probe(load_specs(), Guard(), {"rest_requests": []})
        with patch.object(probe.session, "post") as post:
            for api_id in ("kt10000", "ust10000", "usa21670", "F4", "F5"):
                with self.assertRaises(ValueError):
                    probe.fetch(api_id, {}, "forbidden_test")
            post.assert_not_called()
        probe.session.close()

    def test_unknown_fields_rejected_before_network(self):
        probe = Probe(load_specs(), Guard(), {"rest_requests": []})
        with patch.object(probe.session, "post") as post:
            with self.assertRaises(ValueError):
                probe.fetch("usa20100", {"account": "TEST_ONLY"}, "invalid_test")
            post.assert_not_called()
        probe.session.close()

    def test_documented_contracts_and_safe_file_output(self):
        specs = load_specs()
        for api_id, path in READ_PATHS.items():
            self.assertEqual(specs[api_id]["meta"]["URL"], path)
            self.assertNotIn("acnt", path)
            self.assertNotIn("ordr", path)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe.json"
            Guard(["TEST_SECRET_ONLY"]).write(path, {"message": "TEST_SECRET_ONLY"})
            self.assertNotIn("TEST_SECRET_ONLY", path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
