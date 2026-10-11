"""Offline synthetic NH contracts; never load user .env or call a live API."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import json
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import websocket
from market_radar.execution.nhplug_client import (
    NhplugClient, NhplugClientError, _OfficialSdk, QUOTE_ENDPOINT, ACCOUNTS_ENDPOINT,
    BASE_URL, guard_endpoint,
    _quiet_transport_logs,
)
from tools.nhplug_probe.probe import main

# Deliberately synthetic values, unrelated to a real customer.
ACCOUNT = "12345671234"
GIC = "USATEST"


def ack(kind="1", code="00000", channel="RC"):
    return {"header": {"tr_type": kind, "tr_cd": channel, "rsp_cd": code},
            "body": {"tr_key": [GIC]}}


def tick():
    return {"header": {"tr_cd": "RC", "tr_key": GIC},
            "body": {"gicz15": GIC, "trdprc_1z17": "123.45",
                     "trade_datez8": "20261007", "trade_timez6": "150001"}}


class Socket:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self.closed = False

    def send(self, data):
        self.sent.append(json.loads(data))
        if self.sent[-1]["header"]["tr_type"] == "2":
            self.messages = [ack("2")]

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self):
        if not self.messages:
            raise websocket.WebSocketTimeoutException()
        item = self.messages.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item)

    def close(self, **kwargs):
        self.closed = True


class Backend:
    def __init__(self):
        self.present = True
        self.response = {"Output_0": {"iem_cd": "AAPL", "trdprc": "123.45"}}
        self.calls = []
        self.socket = Socket([ack(), tick()])

    def configured(self):
        return self.present

    @contextmanager
    def session(self):
        yield self

    def authenticate(self):
        return True

    def call(self, path, body):
        self.calls.append((path, body))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def resolve_gic(self, ticker):
        return GIC

    def connect(self, timeout):
        return self.socket

    def subscription(self, gic, kind):
        return json.dumps({"header": {"token": "fake-token", "tr_type": kind},
                           "body": {"tr_cd": "RC", "tr_key": gic}})


class NhplugTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.client = NhplugClient(backend=self.backend)

    def test_missing_credentials_no_network(self):
        self.backend.present = False
        self.assertEqual(self.client.configuration_status(), {"credentials_configured": False})
        with self.assertRaisesRegex(NhplugClientError, "credentials_missing"):
            self.client.authenticate()
        self.assertEqual(self.backend.calls, [])

    def test_secret_and_token_error_redaction(self):
        for secret in ("fake-app-secret", "Bearer fake-access-token", ACCOUNT):
            self.backend.response = RuntimeError(secret)
            with self.assertRaises(NhplugClientError) as caught:
                self.client.overseas_current_price("AAPL")
            self.assertNotIn(secret, str(caught.exception))

    def test_http_error_is_safe(self):
        error = RuntimeError("Authorization: fake-token " + ACCOUNT)
        error.category, error.status = "http", 403
        self.backend.response = error
        with self.assertRaisesRegex(NhplugClientError, "http HTTP 403"):
            self.client.list_accounts()

    def test_mask_account(self):
        for value in (ACCOUNT, "123-4567-1234"):
            self.assertEqual(self.client.mask_account(value), "********1234")
        for value in (None, "1234", "", "secret", "１２３４５６"):
            self.assertEqual(self.client.mask_account(value), "********")

    def test_current_price(self):
        out = self.client.overseas_current_price("AAPL")
        self.assertEqual(out["price"], 123.45)
        self.assertIsNone(out["reason"])
        self.assertEqual(self.backend.calls, [(QUOTE_ENDPOINT, {"iem_cd": "AAPL"})])
        self.assertEqual(set(out), {"provider", "ticker", "price", "received_at", "reason"})

    def test_missing_and_invalid_prices(self):
        for price in (None, "", "bad", "NaN", "inf", 0, -1, True):
            self.backend.response = {"Output_0": {"iem_cd": "AAPL", "trdprc": price}}
            out = self.client.overseas_current_price("AAPL")
            self.assertIsNone(out["price"])
            self.assertEqual(out["reason"], "trdprc_missing_or_invalid")

    def test_wrong_ticker_not_relabelled(self):
        self.backend.response = {"Output_0": {"iem_cd": "MSFT", "trdprc": 123}}
        self.assertIsNone(self.client.overseas_current_price("AAPL")["price"])

    def test_quote_gic_resolved_using_master(self):
        self.backend.response = {"Output_0": {"iem_cd": GIC, "trdprc": 123.45}}
        self.assertEqual(self.client.overseas_current_price("AAPL")["price"], 123.45)

    def test_duplicate_accounts_counted_once(self):
        self.backend.response = {"Output_0": [{"acct_no": ACCOUNT, "acct_type": "01"}] * 3}
        self.assertEqual(len(self.client.usable_accounts()), 1)

    def test_malformed_response(self):
        for value in (None, [], "raw secret", 7):
            self.backend.response = value
            with self.assertRaisesRegex(NhplugClientError, "malformed_response"):
                self.client.overseas_current_price("AAPL")
        self.backend.response = {"rsp_msg": "bad fake-token"}
        self.assertIsNone(self.client.overseas_current_price("AAPL")["price"])

    def test_gateway_api_error(self):
        self.backend.response = {"error_code": "FAKE", "error_description": ACCOUNT}
        with self.assertRaisesRegex(NhplugClientError, "api_error"):
            self.client.list_accounts()

    def test_accounts_sanitized_and_filtered(self):
        self.backend.response = {"cust_no": "private-customer", "Output_0": [
            {"acct_no": ACCOUNT, "acct_type": code, "secret": "private"}
            for code in ("01", "02", "03", "99")]}
        rows = self.client.list_accounts()
        self.assertEqual(len(self.client.usable_accounts(rows)), 2)
        self.assertNotIn(ACCOUNT, repr(rows))
        self.assertNotIn("private", repr(rows))
        self.assertEqual(self.backend.calls[0][0], ACCOUNTS_ENDPOINT)

    def test_empty_accounts_are_distinct_from_failure(self):
        self.backend.response = {"Output_0": []}
        self.assertEqual(self.client.list_accounts(), [])
        self.backend.response = {"rsp_msg": "failed"}
        with self.assertRaisesRegex(NhplugClientError, "api_business_error"):
            self.client.list_accounts()

    def test_http_200_business_failure_is_not_empty_accounts_success(self):
        self.backend.response = {"Output_0": [], "rsp_msg": "failed " + ACCOUNT}
        with self.assertRaisesRegex(NhplugClientError, "api_business_error"):
            self.client.list_accounts()

    def test_sdk_cache_disabled_and_settings_restored(self):
        backend = _OfficialSdk.__new__(_OfficialSdk)
        backend._config = {"NHPLUG_TOKEN_CACHE": "0", "NHPLUG_APP_KEY": "fake-app-key"}
        backend._destinations_ok = True
        original = object()
        modules = {name: SimpleNamespace(requests=original)
                   for name in ("nhplug", "nhplug.auth", "nhplug.client")}
        with patch.dict(os.environ, {"NHPLUG_TOKEN_CACHE": "1"}, clear=True), \
                patch("importlib.import_module", side_effect=lambda name: modules[name]):
            with backend.session():
                self.assertEqual(os.environ["NHPLUG_TOKEN_CACHE"], "0")
                self.assertIs(modules["nhplug.auth"].requests, backend)
            self.assertEqual(os.environ["NHPLUG_TOKEN_CACHE"], "1")
            self.assertNotIn("NHPLUG_APP_KEY", os.environ)
            self.assertIs(modules["nhplug.auth"].requests, original)

    def test_sdk_console_output_discarded(self):
        backend = _OfficialSdk.__new__(_OfficialSdk)
        backend._config = {}
        backend._destinations_ok = True
        output = io.StringIO()
        with patch("importlib.import_module", return_value=SimpleNamespace(requests=object())), \
                redirect_stdout(output), redirect_stderr(output):
            with backend.session():
                print("fake-secret fake-token " + ACCOUNT)
                print("fake-secret", file=sys.stderr)
        self.assertEqual(output.getvalue(), "")

    def test_transport_debug_logs_do_not_leak_auth_query(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("urllib3.connectionpool")
        old = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            with _quiet_transport_logs():
                logger.debug("/oauth2/token?appkey=fake-secret&token=fake-token")
            self.assertEqual(stream.getvalue(), "")
            logger.debug("public-marker")
            self.assertIn("public-marker", stream.getvalue())
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old)

    def test_malformed_account(self):
        for row in (None, {}, {"acct_no": "1234"}):
            self.backend.response = {"Output_0": [row]}
            with self.assertRaises(NhplugClientError):
                self.client.list_accounts()

    def test_order_endpoint_guard_before_authentication(self):
        for path in ("/gbstock/order/v1/buy", "/order", "/modify", "/cancel",
                     "/reservation", QUOTE_ENDPOINT + "?order=buy", "/n2/acctinfo/../order"):
            with self.assertRaisesRegex(NhplugClientError, "endpoint_not_allowed"):
                self.client._request(path, {})
        self.assertEqual(self.backend.calls, [])

    def test_transport_guard(self):
        backend = _OfficialSdk.__new__(_OfficialSdk)
        backend.endpoint_counts = {}
        with patch("requests.post") as post:
            for url in (BASE_URL + "/order", "https://example.invalid/oauth2/token"):
                with self.assertRaisesRegex(NhplugClientError, "transport_endpoint_not_allowed"):
                    backend.post(url)
            post.assert_not_called()
            post.return_value.status_code = 302
            with self.assertRaisesRegex(NhplugClientError, "redirect_not_allowed"):
                backend.post(BASE_URL + QUOTE_ENDPOINT)
            self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_realtime_pass_cleanup_and_channel_case(self):
        result = self.client.realtime_execution()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["tick"]["price"], 123.45)
        self.assertTrue(result["unsubscribed"] and result["closed"])
        self.assertEqual([m["header"]["tr_type"] for m in self.backend.socket.sent], ["1", "2"])
        self.assertEqual(self.backend.socket.sent[0]["body"], {"tr_cd": "RC", "tr_key": GIC})
        self.assertNotIn("fake-token", repr(result))
        self.assertEqual(result["subscription_audit"], {
            "tr_cd": "RC", "key_format": "gicz15_from_official_master",
            "key_length": len(GIC), "master_field_width": 15, "key_is_ticker": False})

    def test_real_resolver_and_serializer_keep_official_master_key(self):
        backend = _OfficialSdk.__new__(_OfficialSdk)
        backend.sdk = SimpleNamespace(get_token=lambda: "synthetic-token")
        rows = [
            {"sSymbol": "AAPL", "sNationCode": "USA", "sGIC": GIC + " " * 8},
            {"sSymbol": "AAPL", "sNationCode": "JPN", "sGIC": "OTHER"},
            {"sSymbol": "TEST", "sNationCode": "USA", "sGIC": "NOTAAPL"}]
        master = SimpleNamespace(download=lambda *args, **kwargs: "synthetic.mst",
                                 parse_file=lambda *args: rows)
        with patch.dict(sys.modules, {"nhplug.instruments": SimpleNamespace(master=master)}):
            key = backend.resolve_gic("AAPL")
        self.assertEqual(key, GIC)
        frame = json.loads(backend.subscription(key, "1"))
        self.assertEqual(frame["body"], {"tr_cd": "RC", "tr_key": GIC})
        self.assertNotEqual(frame["body"]["tr_key"], "AAPL")
        self.assertEqual(len(frame["body"]["tr_key"]), 7)

    def test_master_missing_or_ambiguous_never_falls_back_to_ticker(self):
        backend = _OfficialSdk.__new__(_OfficialSdk)
        for rows in ([], [{"sSymbol": "AAPL", "sNationCode": "USA", "sGIC": key}
                          for key in ("FIRST", "SECOND")]):
            master = SimpleNamespace(download=lambda *args, **kwargs: "synthetic.mst",
                                     parse_file=lambda *args: rows)
            with patch.dict(sys.modules, {"nhplug.instruments": SimpleNamespace(master=master)}):
                with self.assertRaisesRegex(NhplugClientError, "gic_missing_or_ambiguous"):
                    backend.resolve_gic("AAPL")

    def test_serialized_ticker_instead_of_gic_is_blocked_before_send(self):
        original = self.backend.subscription
        with patch.object(self.backend, "subscription", side_effect=lambda key, kind: original("AAPL", kind)):
            result = self.client.realtime_execution()
        self.assertEqual(result["reason"], "subscription_does_not_match_master")
        self.assertEqual(self.backend.socket.sent, [])

    def test_wss10012_is_rejection_not_missing_tick_or_key_mismatch(self):
        error = ack(code="WSS10012")
        error["body"] = {}
        self.backend.socket = Socket([error])
        result = self.client.realtime_execution()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["ack_code"], "WSS10012")
        self.assertEqual(result["reason"], "subscribe_ack_rejected")
        self.assertFalse(result["subscribed"])
        self.assertEqual(self.backend.socket.sent[0]["body"]["tr_key"], GIC)

    def test_connected_no_tick(self):
        self.backend.socket = Socket([ack()])
        result = self.client.realtime_execution()
        self.assertEqual(result["status"], "CONNECTED_NO_TICK")
        self.assertTrue(result["subscribed"])

    def test_realtime_ack_timeout(self):
        self.backend.socket = Socket([])
        result = self.client.realtime_execution()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "subscribe_ack_timeout")

    def test_rejected_or_wrong_channel_ack(self):
        for message in (ack(code="WSS10006"), ack(channel="rc"), ack(channel="rC")):
            self.backend.socket = Socket([message])
            self.assertEqual(self.client.realtime_execution()["status"], "FAIL")

    def test_disconnection_not_no_tick(self):
        self.backend.socket = Socket([ack(), websocket.WebSocketConnectionClosedException("fake-token")])
        result = self.client.realtime_execution()
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn("fake-token", repr(result))

    def test_invalid_tick_price(self):
        message = tick()
        message["body"]["trdprc_1z17"] = "bad"
        self.backend.socket = Socket([ack(), message])
        self.assertEqual(self.client.realtime_execution()["status"], "FAIL")

    def test_cleanup_failure_not_pass(self):
        with patch.object(self.backend.socket, "close", side_effect=RuntimeError("fake-token")):
            result = self.client.realtime_execution()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "cleanup_not_confirmed")

    def test_cli_default_and_no_tick_exit(self):
        def response(path, body):
            return ({"Output_0": [{"acct_no": ACCOUNT, "acct_type": "01"}]}
                    if path == ACCOUNTS_ENDPOINT else self.backend.response)
        for args in ([], ["--accounts", "--realtime"]):
            self.backend.socket = Socket([ack()])
            output = io.StringIO()
            with patch.object(self.backend, "call", side_effect=response), redirect_stdout(output):
                self.assertEqual(main(args, client_factory=lambda: self.client), 0)
            self.assertNotIn(ACCOUNT, output.getvalue())
            self.assertIn("********1234", output.getvalue())
            self.assertIn("CONNECTED_NO_TICK" if args else "RESULT: PASS", output.getvalue())

    def test_invalid_cli_inputs_no_network(self):
        for ticker in ("", "aapl", "AAPL\nsecret", "https://bad", ACCOUNT):
            with self.assertRaises(NhplugClientError):
                self.client.overseas_current_price(ticker)
        for timeout in (0, -1, float("nan"), 61, True):
            with self.assertRaises(NhplugClientError):
                self.client.realtime_execution(timeout=timeout)
        self.assertEqual(self.backend.calls, [])

    def test_cli_never_prints_untrusted_error(self):
        self.backend.response = RuntimeError("fake-secret Bearer fake-token " + ACCOUNT)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(main([], client_factory=lambda: self.client), 1)
        for secret in ("fake-secret", "fake-token", ACCOUNT):
            self.assertNotIn(secret, output.getvalue())


if __name__ == "__main__":
    unittest.main()
