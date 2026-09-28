"""Read-only contracts pinned to the successful Kiwoom probe revision."""
from collections import deque
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values

from runtime_paths import APP_ROOT
from ..provider import ProviderError, RateLimitError

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
            now = self.clock()
            peak = self.wall().astimezone(ZoneInfo("Asia/Seoul")).hour == 9
            rate = self.config.peak_rest_requests_per_second if peak else self.config.rest_requests_per_second
            while self.list_times and now - self.list_times[0] >= 60:
                self.list_times.popleft()
            target = max(now, self.next_at)
            if api_id == "usa10099" and len(self.list_times) >= 4:  # headroom below 5/min
                target = max(target, self.list_times[0] + 60.1)
            if self.stop.wait(max(0, target-now)):
                raise ProviderError("Kiwoom stopped")
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

    def token(self, force=False):
        with self.lock:
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
                raise ProviderError("Kiwoom OAuth failed (credentials/response redacted)") from None
            self.logger.info("kiwoom_auth_success")
            return self._token

    def request(self, api_id, body, cursor=None):
        if api_id not in APIS or set(body) - set(APIS[api_id]["request_fields"]):
            raise ProviderError("Rejected TR or request field outside read-only contract")
        with self.lock:
            for attempt in range(self.config.rest_retries + 1):
                token = self.token()
                self.limiter.acquire(api_id)
                headers = {"Content-Type": "application/json;charset=UTF-8", "api-id": api_id,
                           "authorization": "Bearer " + token}
                if cursor:
                    headers.update({"cont-yn": "Y", "next-key": cursor})
                rate_error = False
                try:
                    self.request_count += 1
                    r = self.http.post(BASE + APIS[api_id]["path"], headers=headers, json=body,
                                       timeout=(5, 10), allow_redirects=False)
                    if r.status_code == 401 and attempt < self.config.rest_retries:
                        self.token(force=True)
                        continue
                    p = r.json() if r.status_code != 429 else {}
                    rate_error = r.status_code == 429
                    if r.status_code != 200 or p.get("return_code") != 0:
                        raise ValueError("API rejected")
                    fields = set(APIS[api_id]["response_fields"])
                    safe = {k: v for k, v in p.items() if k in fields and not isinstance(v, (dict, list))}
                    for name in ("result_list", "list"):
                        if isinstance(p.get(name), list):
                            safe[name] = [{k: v for k, v in row.items() if k in fields and isinstance(v, (str, int, float, type(None)))}
                                          for row in p[name] if isinstance(row, dict)]
                    safe = self.sanitize(safe)
                    received = datetime.now(timezone.utc).isoformat()
                    self.raw_sink("REST", body.get("stk_cd"), received,
                                  {"api_id": api_id, "request": body, "continuation": bool(cursor), "response": safe})
                    next_key = r.headers.get("next-key") if r.headers.get("cont-yn") == "Y" else None
                    return safe, next_key, received
                except Exception:
                    # Never include exception messages, request/response objects or tracebacks.
                    self.logger.warning("%s api_id=%s attempt=%s",
                        "kiwoom_rate_limit" if rate_error else "kiwoom_rest_error", api_id, attempt+1)
                    if attempt == self.config.rest_retries:
                        if rate_error:
                            raise RateLimitError("Kiwoom rate limit", 30) from None
                        raise ProviderError("Kiwoom market-data request failed: " + api_id) from None
                    if self.stop.wait(min(30, 2 ** (attempt + 1))):
                        raise ProviderError("Kiwoom stopped") from None

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
