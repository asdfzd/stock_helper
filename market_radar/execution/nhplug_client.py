"""Namuh read-only probe adapter; no collector, database or order integration.

Contract: https://www.nhplug.com/openapi-docs/gbstock/openapi.json
SDK: https://github.com/PLUG-OpenAPI/nhplug-sdk (nhplug 0.4.1).
REST trdprc; RC trdprc_1z17; GIC from the official m_gtsstock master.
Tokens are memory-only. Public results never contain raw responses/accounts.
"""
from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib
import json
import logging
import math
import os
from pathlib import Path
import re
import ssl
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://api.nhplug.com:8443"
INSTRUMENTS_BASE = "https://www.nhplug.com/instruments"
QUOTE_ENDPOINT = "/gbstock/quote/v1/current"
ACCOUNTS_ENDPOINT = "/n2/acctinfo"
BALANCE_ENDPOINT = "/gbstock/inquiry/v1/balance"
BUYABLE_ENDPOINT = "/gbstock/inquiry/v1/buyableAmount"
ALLOWED_READ_ONLY_ENDPOINTS = frozenset({QUOTE_ENDPOINT, ACCOUNTS_ENDPOINT,
                                      BALANCE_ENDPOINT, BUYABLE_ENDPOINT})
AUTH_ENDPOINT = "/oauth2/token"
EXECUTION_CHANNEL = "RC"  # Case-sensitive; rc is delayed, rC is another asset.
WS_ACK_OK = "00000"  # Official WS ACK code only; not a REST success-code rule.
_SDK_LOCK = threading.RLock()


class NhplugClientError(RuntimeError):
    """Only locally generated reasons, never SDK exception text or response bodies."""


class _Discard:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


@contextmanager
def _quiet_transport_logs():
    # urllib3 DEBUG can include the OAuth query. Suppress this thread only, so
    # another provider's logs remain intact if this adapter is reused later.
    thread = threading.get_ident()
    rule = lambda record: record.thread != thread
    names = {"urllib3.connectionpool", "urllib3.util.retry", "websocket"}
    names.update(name for name in logging.Logger.manager.loggerDict
                 if name.startswith(("urllib3.", "requests.", "nhplug.")))
    loggers = [logging.getLogger(name) for name in names]
    for logger in loggers:
        logger.addFilter(rule)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(rule)


def _safe_error(exc):
    category = getattr(exc, "category", None)
    if category not in {"config", "auth", "rate_limit", "network", "http"}:
        category = {"WebSocketTimeoutException": "websocket_timeout",
                    "WebSocketConnectionClosedException": "websocket_disconnected",
                    "SSLCertVerificationError": "tls_verification_failed"}.get(
                        type(exc).__name__, "sdk_or_transport")
    status = getattr(exc, "status", None)
    suffix = f" HTTP {status}" if type(status) is int and 100 <= status <= 599 else ""
    return NhplugClientError(category + suffix)


def guard_endpoint(path):
    if path not in ALLOWED_READ_ONLY_ENDPOINTS:
        raise NhplugClientError("endpoint_not_allowed")


def _ticker(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", value):
        raise NhplugClientError("invalid_ticker")
    return value


def _price(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _received_at():
    return datetime.now(timezone.utc).isoformat()


class _OfficialSdk:
    """Scoped bridge to the optional SDK. Full identifiers never leave this layer."""

    def __init__(self, env_file):
        from dotenv import dotenv_values
        # Parse locally without interpolating unrelated secrets or editing .env.
        with redirect_stderr(_Discard()):
            file_values = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
        defaults = {"NHPLUG_BASE_URL": BASE_URL, "NHPLUG_AUTH_URL": BASE_URL,
                    "NHPLUG_INSTRUMENTS_BASE": INSTRUMENTS_BASE,
                    "NHPLUG_WS_URL": "wss://api.nhplug.com:7080/websocket"}
        self._config = {key: os.environ.get(key) or file_values.get(key) or default
                        for key, default in defaults.items()}
        for key in ("NHPLUG_APP_KEY", "NHPLUG_APP_SECRET"):
            self._config[key] = os.environ.get(key) or file_values.get(key) or ""
        # Reject a different destination before credentials can be sent.
        self._destinations_ok = all(self._config[key].rstrip("/") == value
                                    for key, value in defaults.items())
        self._config.update(NHPLUG_TOKEN_CACHE="0", NHPLUG_RATE_LIMIT="4")
        self.endpoint_counts = {}

    def configured(self):
        return all(bool(self._config[key].strip()) for key in
                   ("NHPLUG_APP_KEY", "NHPLUG_APP_SECRET"))

    @contextmanager
    def session(self):
        # NH-only settings are restored on exit. No global logging reconfiguration.
        with _SDK_LOCK, redirect_stdout(_Discard()), redirect_stderr(_Discard()), _quiet_transport_logs():
            old = {key: os.environ.get(key) for key in self._config}
            bridges = []
            try:
                os.environ.update(self._config)
                sdk = importlib.import_module("nhplug")
                if not self._destinations_ok:
                    raise NhplugClientError("namuh_live_destination_required")
                # SDK REST internals are pinned to 0.4.1; guard even its OAuth calls.
                for name in ("nhplug.auth", "nhplug.client"):
                    module = importlib.import_module(name)
                    bridges.append((module, module.requests))
                    module.requests = self
                self.sdk = sdk
                yield self
            finally:
                for module, previous in bridges:
                    module.requests = previous
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    # requests-compatible narrow bridge, so SDK exceptions retain their categories.
    @property
    def RequestException(self):
        import requests
        return requests.RequestException

    def post(self, url, **kwargs):
        import requests
        allowed = {BASE_URL + path for path in ALLOWED_READ_ONLY_ENDPOINTS | {AUTH_ENDPOINT}}
        if url not in allowed:
            raise NhplugClientError("transport_endpoint_not_allowed")
        path = url[len(BASE_URL):]
        self.endpoint_counts[path] = self.endpoint_counts.get(path, 0) + 1
        # Never follow a redirect carrying credentials to another host or endpoint.
        kwargs["allow_redirects"] = False
        response = requests.post(url, **kwargs)
        if 300 <= response.status_code < 400:
            raise NhplugClientError("redirect_not_allowed")
        return response

    def authenticate(self):
        return bool(self.sdk.get_token())  # Token remains inside SDK memory.

    def call(self, path, body):
        guard_endpoint(path)
        if path == BUYABLE_ENDPOINT and body.get("pcs_dit") != "2":
            raise NhplugClientError("buyable_quantity_inquiry_only")
        return self.sdk.call(path, body, timeout=10)

    def inquiry_page(self, path, body, *, cts=None, cts_flag=None):
        if path != BALANCE_ENDPOINT:
            raise NhplugClientError("balance_inquiry_only")
        return self.sdk.call(path, body, timeout=10, want_meta=True, cts=cts, cts_flag=cts_flag)

    def resolve_gic(self, ticker):
        from nhplug.instruments import master
        # Only public instrument files, in a disposable directory outside data/.
        with tempfile.TemporaryDirectory(prefix="nhplug_instruments_") as directory:
            key = "NHPLUG_INSTRUMENTS_CACHE_DIR"
            previous = os.environ.get(key)
            os.environ[key] = directory
            try:
                path = master.download("m_gtsstock", dest=Path(directory) / "m_gtsstock.mst", timeout=15)
                rows = master.parse_file("m_gtsstock", path)
            finally:
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous
        # sGIC[15] is a fixed-width master field, not necessarily a 15-character
        # JSON value. The official SDK parser removes right-space padding.
        # Use that value unchanged; never invent a country prefix or pad the key.
        matches = {r["sGIC"].rstrip(" ") for r in rows
                   if r.get("sSymbol") == ticker and r.get("sNationCode") == "USA"}
        if len(matches) != 1:
            raise NhplugClientError("gic_missing_or_ambiguous")
        gic = matches.pop()
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,15}", gic):
            raise NhplugClientError("invalid_gic")
        return gic

    def connect(self, timeout):
        import websocket
        import truststore
        from nhplug.realtime import ws_url
        # Official SDK URL derivation, official websocket-client dependency.
        url = ws_url(tr_cd=EXECUTION_CHANNEL)
        if url != "wss://api.nhplug.com:7080/websocket":
            raise NhplugClientError("websocket_destination_not_allowed")
        websocket.enableTrace(False)
        return websocket.create_connection(url, timeout=timeout,
            sslopt={"context": truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)})

    def subscription(self, gic, kind):
        if kind not in {"1", "2"}:
            raise NhplugClientError("subscription_not_allowed")
        return json.dumps({"header": {"token": self.sdk.get_token(), "tr_type": kind},
                           "body": {"tr_cd": EXECUTION_CHANNEL, "tr_key": gic}})


class NhplugClient:
    def __init__(self, *, env_file=ROOT / ".env", backend=None):
        self._backend = backend if backend is not None else _OfficialSdk(env_file)

    def configuration_status(self):
        return {"credentials_configured": self._backend.configured()}

    def _invoke(self, operation):
        if not self._backend.configured():
            raise NhplugClientError("credentials_missing")
        try:
            with self._backend.session():
                return operation()
        except NhplugClientError:
            raise
        except Exception as exc:
            raise _safe_error(exc) from None

    def authenticate(self):
        if not self._invoke(self._backend.authenticate):
            raise NhplugClientError("authentication_failed")
        return True

    def _request(self, path, body):
        guard_endpoint(path)  # Check before SDK loading/authentication/network.
        response = self._invoke(lambda: self._backend.call(path, body))
        if not isinstance(response, dict):
            raise NhplugClientError("malformed_response")
        # No universal REST success code exists in the official SDK. Require the
        # documented result block; never promote HTTP 200 alone into success.
        if any(response.get(k) for k in ("error", "error_code", "error_description")):
            raise NhplugClientError("api_error")
        message = response.get("message")
        messages = [response.get("rsp_msg")]
        if isinstance(message, dict):
            messages.append(message.get("usr_msg"))
        # Read business messages without returning them (they can contain accounts).
        if any(isinstance(m, str) and re.search(
                r"오류|실패|불가|유효하지|권한.*없|약정.*필요|미등록|denied|invalid|error|failed",
                m, re.IGNORECASE) for m in messages):
            raise NhplugClientError("api_business_error")
        return response

    def overseas_current_price(self, ticker):
        ticker = _ticker(ticker)
        data = self._request(QUOTE_ENDPOINT, {"iem_cd": ticker})
        output = data.get("Output_0")
        reason = None
        price = None
        if not isinstance(output, dict):
            reason = "missing_or_malformed_Output_0"
        else:
            # Official iem_cd is a 15-character instrument identifier. Namuh live
            # returns the GIC here; resolve through the official master, never
            # invent a prefix or assume the requested ticker proves identity.
            identity = output.get("iem_cd")
            matches = identity == ticker
            if not matches and isinstance(identity, str) and identity:
                matches = identity == self._invoke(lambda: self._backend.resolve_gic(ticker))
            if not matches:
                reason = "ticker_mismatch_or_missing"
            else:
                price = _price(output.get("trdprc"))
                if price is None:
                    reason = "trdprc_missing_or_invalid"
        return {"provider": "nhplug", "ticker": ticker, "price": price,
                "received_at": _received_at(), "reason": reason}

    @staticmethod
    def mask_account(account):
        if not isinstance(account, str):
            return "********"
        digits = re.sub(r"[\s-]", "", account)
        if not digits.isascii() or not digits.isdigit() or len(digits) <= 4:
            return "********"
        return "********" + digits[-4:]

    def list_accounts(self):
        data = self._request(ACCOUNTS_ENDPOINT, {})
        rows = data.get("Output_0")
        if not isinstance(rows, list):
            raise NhplugClientError("accounts_missing_or_malformed")
        accounts = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("acct_no"), str):
                raise NhplugClientError("account_row_malformed")
            masked = self.mask_account(row["acct_no"])
            if masked == "********":
                raise NhplugClientError("account_number_malformed")
            code = row.get("acct_type")
            # Unknown account types are not proven usable; never default to live.
            code = code.strip() if isinstance(code, str) else ""
            identity = (re.sub(r"[\s-]", "", row["acct_no"]), code)
            if identity in seen:
                continue
            seen.add(identity)
            accounts.append({"account": masked,
                "account_type": code if code in {"01", "02", "03"} else "unknown",
                "usable_live": code in {"01", "02"}})
        return accounts

    def usable_accounts(self, accounts=None):
        rows = self.list_accounts() if accounts is None else accounts
        return [dict(row) for row in rows if row.get("usable_live") is True]

    def realtime_execution(self, ticker="AAPL", timeout=15):
        ticker = _ticker(ticker)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise NhplugClientError("invalid_realtime_timeout")
        return self._invoke(lambda: self._realtime(ticker, timeout))

    def _realtime(self, ticker, timeout):
        import websocket
        result = dict(status="FAIL", connected=False, subscribed=False,
                      unsubscribe_sent=False, unsubscribed=False, closed=False,
                      tick=None, reason=None, ack_code=None, ack_shape=None,
                      subscription_audit=None)
        gic = self._backend.resolve_gic(ticker)
        ws = None
        sent = False

        def ack(message, kind):
            header, body = message.get("header", {}), message.get("body", {})
            return (isinstance(header, dict) and isinstance(body, dict)
                    and header.get("tr_type") == kind and header.get("tr_cd") == EXECUTION_CHANNEL
                    and gic in (body.get("tr_key") if isinstance(body.get("tr_key"), list)
                                else [body.get("tr_key")])
                    and header.get("rsp_cd") == WS_ACK_OK)

        def receive(deadline):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise websocket.WebSocketTimeoutException()
            ws.settimeout(remaining)
            raw = ws.recv()
            if not raw:
                raise NhplugClientError("websocket_closed_early")
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise NhplugClientError("malformed_websocket_message")
            return message

        try:
            deadline = time.monotonic() + timeout
            ws = self._backend.connect(timeout)
            result["connected"] = True
            payload = self._backend.subscription(gic, "1")
            body = json.loads(payload).get("body", {})
            if body.get("tr_cd") != EXECUTION_CHANNEL or body.get("tr_key") != gic:
                raise NhplugClientError("subscription_does_not_match_master")
            ws.send(payload)
            sent = True
            # Inspect the serialized frame actually passed to send, not the
            # ticker input. Never return the frame or its authentication header.
            result["subscription_audit"] = {
                "tr_cd": EXECUTION_CHANNEL, "key_format": "gicz15_from_official_master",
                "key_length": len(body["tr_key"]), "master_field_width": 15,
                "key_is_ticker": body["tr_key"] == ticker}
            while time.monotonic() < deadline:
                try:
                    message = receive(deadline)
                except websocket.WebSocketTimeoutException:
                    break
                header, body = message.get("header", {}), message.get("body", {})
                if not isinstance(header, dict) or not isinstance(body, dict):
                    raise NhplugClientError("malformed_websocket_message")
                if "tr_type" in header or "rsp_cd" in header:
                    code = header.get("rsp_cd")
                    result["ack_code"] = code if isinstance(code, str) and re.fullmatch(
                        r"(?:WSS[0-9]{5}|[0-9]{5})", code) else "unknown"
                    result["ack_shape"] = {
                        "channel_matches": header.get("tr_cd") == EXECUTION_CHANNEL,
                        "type_matches": header.get("tr_type") == "1",
                        "key_matches": body.get("tr_key") in (gic, [gic])}
                    if code != WS_ACK_OK:
                        raise NhplugClientError("subscribe_ack_rejected")
                    if not ack(message, "1"):
                        raise NhplugClientError("subscribe_ack_mismatched")
                    result["subscribed"] = True
                    if result["tick"]:
                        break
                    continue
                if (header.get("tr_cd") != EXECUTION_CHANNEL or header.get("tr_key") != gic
                        or body.get("gicz15") != gic):
                    continue
                price = _price(body.get("trdprc_1z17"))
                if price is None:
                    raise NhplugClientError("execution_price_missing_or_invalid")
                # These are provider date/time strings, not an invented timezone.
                date, stamp = body.get("trade_datez8"), body.get("trade_timez6")
                result["tick"] = dict(provider="nhplug", ticker=ticker, price=price,
                    received_at=_received_at(),
                    trade_date=date if isinstance(date, str) and re.fullmatch(r"[0-9]{8}", date) else None,
                    trade_time=stamp if isinstance(stamp, str) and re.fullmatch(r"[0-9]{6}", stamp) else None)
                if result["subscribed"]:
                    break
            if result["subscribed"]:
                result["status"] = "PASS" if result["tick"] else "CONNECTED_NO_TICK"
            else:
                result["reason"] = "subscribe_ack_timeout"
        except Exception as exc:
            result["reason"] = str(exc) if isinstance(exc, NhplugClientError) else str(_safe_error(exc))
        finally:
            if ws is not None:
                try:
                    if sent:
                        ws.send(self._backend.subscription(gic, "2"))
                        result["unsubscribe_sent"] = True
                        deadline = time.monotonic() + min(timeout, 2)
                        while time.monotonic() < deadline:
                            if ack(receive(deadline), "2"):
                                result["unsubscribed"] = True
                                break
                except Exception:
                    pass
                try:
                    ws.close(timeout=2)
                    result["closed"] = True
                except Exception:
                    pass
            if result["status"] != "FAIL" and not (result["unsubscribed"] and result["closed"]):
                result.update(status="FAIL", reason="cleanup_not_confirmed")
        return result
