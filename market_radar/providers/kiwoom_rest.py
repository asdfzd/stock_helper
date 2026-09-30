"""Read-only contracts pinned to the successful Kiwoom probe revision."""
from collections import deque
from datetime import datetime, timezone
import json
import logging
import re
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

from runtime_paths import APP_ROOT
from ..provider import ProviderError, RateLimitError
from ..quality import event
from .kiwoom_failures import StopRequested, RestFailure, RestRateLimitFailure, classify, normalize_code

BASE = "https://api.kiwoom.com"
CONTRACT = json.loads(Path(__file__).with_name("kiwoom_contract.json").read_text(encoding="utf-8"))
APIS = CONTRACT["apis"]


class RateLimiter:
    """All REST/auth callers share one gate, including the WS reconnect thread."""
    def __init__(self, config, stop, clock=time.monotonic, wall=lambda: datetime.now(timezone.utc)):
        self.config, self.stop, self.clock, self.wall = config, stop, clock, wall
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.list_times = deque()

    def acquire(self, api_id):
        with self.lock:
            if self.stop.is_set():
                raise StopRequested("Kiwoom stopped")
            now = self.clock()
            peak = self.wall().astimezone(ZoneInfo("Asia/Seoul")).hour == 9
            rate = self.config.peak_rest_requests_per_second if peak else self.config.rest_requests_per_second
            while self.list_times and now - self.list_times[0] >= 60:
                self.list_times.popleft()
            target = max(now, self.next_at)
            if api_id == "usa10099" and len(self.list_times) >= 4:  # headroom below 5/min
                target = max(target, self.list_times[0] + 60.1)
            if self.stop.wait(max(0, target-now)):
                raise StopRequested("Kiwoom stopped")
            now = self.clock()
            self.next_at = now + 1/rate
            if api_id == "usa10099":
                self.list_times.append(now)


class KiwoomRest:
    def __init__(self, config, stop, logger=None, raw_sink=None, session=None):
        self.config, self.stop = config, stop
        self.logger = logger or logging.getLogger("market_radar.kiwoom")
        self.raw_sink = raw_sink or (lambda *args: None)
        self.http = session or requests.Session()
        self.http.trust_env = False
        self.limiter = RateLimiter(config, stop)
        self.lock = threading.RLock()
        self._token = None
        self._credentials = None
        self.request_count = 0
        self.failures = {}

    def token(self, force=False):
        with self.lock:
            if self.stop.is_set():
                raise StopRequested("Kiwoom stopped")
            if self._token and not force:
                return self._token
            if self._credentials is None:
                env = dotenv_values(APP_ROOT / ".env")
                if env.get("KIWOOM_MODE") != "real" or not env.get("APP_KEY") or not env.get("APP_SECRET"):
                    raise ProviderError("Kiwoom requires real mode and APP_KEY/APP_SECRET in .env")
                self._credentials = (env["APP_KEY"], env["APP_SECRET"])
            self.limiter.acquire("oauth")
            try:
                r = self.http.post(BASE + "/oauth2/token", json={"grant_type": "client_credentials",
                    "appkey": self._credentials[0], "secretkey": self._credentials[1]},
                    timeout=(5, 10), allow_redirects=False)
                p = r.json()
                if r.status_code != 200 or p.get("return_code") != 0 or not p.get("token"):
                    raise ValueError("Auth rejected")
                self._token = p["token"]
            except Exception:
                self.logger.error("kiwoom_auth_error", extra=event("api_error_count"))
                raise ProviderError("Kiwoom OAuth failed (credentials/response redacted)") from None
            self.logger.info("kiwoom_auth_success")
            return self._token

    def request(self, api_id, body, cursor=None):
        if api_id not in APIS or set(body) - set(APIS[api_id]["request_fields"]):
            raise ProviderError("Rejected TR or request field outside read-only contract")
        with self.lock:
            if self.stop.is_set():
                raise StopRequested("Kiwoom stopped")
            day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
            # Cursor is part of the query; never expose it in logs or public errors.
            key = (day, api_id, json.dumps(body, sort_keys=True), cursor)
            now = time.monotonic()
            self.failures = {k: v for k, v in self.failures.items() if k[0] == day and v['retry_after'] > now}
            state = self.failures.get(key)
            if state:
                self.logger.info("kiwoom_rest_suppressed api_id=%s ticker=%s", api_id, body.get('stk_cd'),
                                 extra=event('rest_suppressed_count'))
                raise RestFailure(state['details'], suppressed=True, retry_after=state['retry_after'])
            for attempt in range(self.config.rest_retries + 1):
                if self.stop.is_set():
                    raise StopRequested("Kiwoom stopped")
                token = self.token()
                self.limiter.acquire(api_id)
                if self.stop.is_set():
                    raise StopRequested("Kiwoom stopped")
                headers = {"Content-Type": "application/json;charset=UTF-8", "api-id": api_id,
                           "authorization": "Bearer " + token}
                if cursor:
                    headers.update({"cont-yn": "Y", "next-key": cursor})
                status = code = None
                message, category = '', None
                try:
                    self.request_count += 1
                    r = self.http.post(BASE + APIS[api_id]["path"], headers=headers, json=body,
                                       timeout=(5, 10), allow_redirects=False)
                    status = r.status_code
                    try:
                        p = r.json()
                        if not isinstance(p, dict):
                            raise ValueError('Expected object')
                        code = normalize_code(p.get('return_code'))
                        message = self.sanitize(str(p.get('return_msg', ''))).replace('\n', ' ').replace('\r', ' ')[:300]
                    except ValueError:
                        p, category = {}, 'parse_error'
                    if status == 200 and code == 0 and not category:
                        fields = set(APIS[api_id]["response_fields"])
                        safe = {k: v for k, v in p.items() if k in fields and not isinstance(v, (dict, list))}
                        for name in ("result_list", "list"):
                            if name in p:
                                if not isinstance(p[name], list) or any(not isinstance(row, dict) for row in p[name]):
                                    category = 'parse_error'
                                    break
                                safe[name] = [{k: v for k, v in row.items() if k in fields and isinstance(v, (str, int, float, type(None)))} for row in p[name]]
                        if api_id in {'usa06011', 'usa06012', 'usa10099'} and not any(name in p for name in ('result_list', 'list')):
                            category = 'parse_error'
                        if not category:
                            safe = self.sanitize(safe)
                            received = datetime.now(timezone.utc).isoformat()
                            self.raw_sink("REST", body.get("stk_cd"), received,
                                {"api_id": api_id, "request": body, "continuation": bool(cursor), "response": safe})
                            next_key = r.headers.get("next-key") if r.headers.get("cont-yn") == "Y" else None
                            # Empty initial historical query is explicit no data, not incomplete coverage.
                            if api_id in {'usa06011', 'usa06012'} and not cursor and safe.get('result_list') == [] and not next_key:
                                category = 'no_data'
                            else:
                                self.failures.pop(key, None)
                                return safe, next_key, received
                except requests.Timeout:
                    category = 'timeout'
                except requests.ConnectionError:
                    category = 'connection_error'
                except requests.RequestException:
                    category = 'transport_error'
                failure_class, category, specific = classify(status, code, message, category)
                details = dict(api_id=api_id, ticker=self.sanitize(body.get('stk_cd')), endpoint=APIS[api_id]['path'],
                    http_status=status, return_code=code, specific_code=specific, return_msg=message,
                    failure_class=failure_class, category=category, attempt=attempt+1)
                stamp = datetime.now(timezone.utc).isoformat()
                self.raw_sink('REST_ERROR', body.get('stk_cd'), stamp, details)
                self.logger.error('kiwoom_rest_failure %s', json.dumps(details, ensure_ascii=False),
                                  extra={'rest_error': details})
                ttl = self.config.rest_negative_ttl_seconds if failure_class == 'deterministic' else 60
                retry_at = time.monotonic() + ttl
                self.failures[key] = dict(details=details,
                    first_failed_at=self.failures.get(key, {}).get('first_failed_at', stamp), last_failed_at=stamp,
                    attempt_count=attempt+1, retry_after=retry_at, session_suppressed=failure_class == 'deterministic', reason=category)
                if self.stop.is_set():
                    raise StopRequested('Kiwoom stopped')
                if failure_class != 'transient' or attempt == self.config.rest_retries:
                    raise (RestRateLimitFailure if category == 'rate_limited' else RestFailure)(details, retry_after=retry_at)
                if category == 'auth_expired':
                    self._token = None
                if self.stop.wait(min(30, 2 ** (attempt + 1))):
                    raise StopRequested('Kiwoom stopped')
            raise AssertionError('Unreachable REST retry state')

    def example(self, api_id, **overrides):
        body = dict(APIS[api_id]["example"])
        body.update(overrides)
        return body

    def sanitize(self, value):
        if isinstance(value, dict):
            return {k: "[REDACTED]" if str(k).lower() in {"token", "authorization", "appkey", "secretkey", "app_key", "app_secret"}
                    else self.sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.sanitize(v) for v in value]
        if isinstance(value, str):
            value = re.sub(r'(?i)(bearer\s+)[^\s,;\]"\']+', r'\1[REDACTED]', value)
            value = re.sub(r'(?i)((?:app_?key|app_?secret|secretkey|token|authorization)\s*[=:]\s*)[^\s,;\]"\']+', r'\1[REDACTED]', value)
            for secret in (*self._credentials, self._token) if self._credentials else (self._token,):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value

    def quote(self, ticker, exchange):
        return self.request("usa20100", {"stex_tp": exchange, "stk_cd": ticker})

    def minutes_page(self, ticker, exchange, day, cursor=None):
        return self.request("usa06011", {"stex_tp": exchange, "stk_cd": ticker, "strt_dt": day.replace("-", ""),
            "tic_scope": "1", "upd_stkpc_tp": "0", "exrt_appl_tp": "0"}, cursor)

    def daily(self, ticker, exchange, day):
        return self.request("usa06012", {"stex_tp": exchange, "stk_cd": ticker, "strt_dt": day.replace("-", ""),
            "upd_stkpc_tp": "0", "exrt_appl_tp": "0"})

    def daily_prices(self, ticker, exchange, day):
        return self.request("usa20590", {"stex_tp": exchange, "stk_cd": ticker, "base_dt": day.replace("-", "")})

    def close(self):
        self.http.close()
        self._token = self._credentials = None
