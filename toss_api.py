from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"
PRICES_PATH = "/api/v1/prices"
REQUEST_TIMEOUT_SECONDS = 15
TOKEN_EXPIRY_MARGIN_SECONDS = 30
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.\-]+$")
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass(frozen=True)
class CurrentPrice:
    symbol: str
    last_price: Decimal
    timestamp: str | None


class TossApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.retry_after = retry_after


class TossApiClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        load_dotenv(ENV_PATH, override=False)
        self._client_id = client_id or os.getenv("TOSS_CLIENT_ID", "").strip()
        self._client_secret = client_secret or os.getenv("TOSS_CLIENT_SECRET", "").strip()
        self._session = session or requests.Session()
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def _require_credentials(self) -> None:
        missing = []
        if not self._client_id:
            missing.append("TOSS_CLIENT_ID")
        if not self._client_secret:
            missing.append("TOSS_CLIENT_SECRET")
        if missing:
            raise TossApiError(
                f"API 인증정보가 비어 있습니다: {', '.join(missing)}. "
                f"{ENV_PATH}에 값을 입력하세요."
            )

    def _request_access_token(self) -> str:
        self._require_credentials()
        try:
            response = self._session.post(
                f"{BASE_URL}{TOKEN_PATH}",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise TossApiError(f"토큰 발급 네트워크 오류: {exc}") from exc

        if not response.ok:
            raise self._http_error(response, "토큰 발급 실패")
        payload = self._json_object(response, "토큰 응답")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise TossApiError("토큰 응답에 access_token이 없습니다.")
        expires_in = payload.get("expires_in", 300)
        try:
            lifetime = max(float(expires_in), 1.0)
        except (TypeError, ValueError):
            lifetime = 300.0
        self._access_token = token
        self._token_expires_at = time.monotonic() + max(
            lifetime - TOKEN_EXPIRY_MARGIN_SECONDS, 1.0
        )
        return token

    def get_access_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token
        return self._request_access_token()

    def invalidate_access_token(self) -> None:
        self._access_token = None
        self._token_expires_at = 0.0

    def get_current_price(self, symbol: str) -> CurrentPrice:
        normalized_symbol = self._normalize_symbols([symbol])[0]
        prices = self.get_current_prices([normalized_symbol])
        if normalized_symbol not in prices:
            raise TossApiError(f"현재가 응답에 {normalized_symbol} 결과가 없습니다.")
        return prices[normalized_symbol]

    def get_current_prices(self, symbols: list[str]) -> dict[str, CurrentPrice]:
        """여러 symbol을 한 번의 /prices 요청으로 조회한다."""
        normalized_symbols = self._normalize_symbols(symbols)

        response = self._get_prices(normalized_symbols)
        if response.status_code == 401:
            self.invalidate_access_token()
            response = self._get_prices(normalized_symbols)
        if not response.ok:
            raise self._http_error(
                response, f"현재가 조회 실패 ({','.join(normalized_symbols)})"
            )

        payload = self._json_object(response, "현재가 응답")
        results = payload.get("result")
        if not isinstance(results, list):
            raise TossApiError("현재가 응답의 result가 배열이 아닙니다.")
        requested = set(normalized_symbols)
        prices: dict[str, CurrentPrice] = {}
        for record in results:
            if not isinstance(record, dict):
                continue
            response_symbol = str(record.get("symbol", "")).strip().upper()
            if response_symbol not in requested:
                continue
            try:
                last_price = Decimal(str(record["lastPrice"]))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue
            if last_price <= 0:
                continue
            prices[response_symbol] = CurrentPrice(
                symbol=response_symbol,
                last_price=last_price,
                timestamp=(
                    str(record["timestamp"])
                    if record.get("timestamp") is not None
                    else None
                ),
            )
        return prices

    @staticmethod
    def _normalize_symbols(symbols: list[str]) -> list[str]:
        normalized: list[str] = []
        for symbol in symbols:
            value = symbol.strip().upper()
            if not value or not SYMBOL_PATTERN.fullmatch(value):
                raise TossApiError(f"지원하지 않는 symbol 형식입니다: {symbol!r}")
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            raise TossApiError("현재가를 조회할 symbol이 없습니다.")
        if len(normalized) > 200:
            raise TossApiError("한 번에 조회할 수 있는 symbol은 최대 200개입니다.")
        return normalized

    def _get_prices(self, symbols: list[str]) -> requests.Response:
        try:
            return self._session.get(
                f"{BASE_URL}{PRICES_PATH}",
                params={"symbols": ",".join(symbols)},
                headers={"Authorization": f"Bearer {self.get_access_token()}"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise TossApiError(f"현재가 조회 네트워크 오류: {exc}") from exc

    @staticmethod
    def _json_object(response: requests.Response, description: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise TossApiError(f"{description}이 JSON 형식이 아닙니다.") from exc
        if not isinstance(payload, dict):
            raise TossApiError(f"{description}이 JSON 객체가 아닙니다.")
        return payload

    @staticmethod
    def _http_error(response: requests.Response, context: str) -> TossApiError:
        status_messages = {
            400: "잘못된 요청",
            401: "인증 실패 또는 만료된 토큰",
            403: "접근 권한 없음 또는 허용 IP 확인 필요",
            404: "API 경로 또는 종목을 찾을 수 없음",
            429: "요청 한도 초과",
            500: "토스증권 서버 오류",
        }
        error_code = None
        detail = None
        try:
            payload = response.json()
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                error_code = str(error.get("code")) if error.get("code") else None
                detail = str(error.get("message")) if error.get("message") else None
        except ValueError:
            pass
        retry_after = response.headers.get("Retry-After") if response.status_code == 429 else None
        parts = [
            f"{context}: HTTP {response.status_code}",
            status_messages.get(response.status_code, "HTTP 오류"),
        ]
        if error_code:
            parts.append(f"code={error_code}")
        if detail:
            parts.append(f"message={detail}")
        if response.status_code == 429:
            parts.append(f"Retry-After={retry_after or '헤더 없음'}")
        return TossApiError(
            " | ".join(parts),
            status_code=response.status_code,
            error_code=error_code,
            retry_after=retry_after,
        )


_default_client: TossApiClient | None = None


def get_current_price(symbol: str) -> CurrentPrice:
    global _default_client
    if _default_client is None:
        _default_client = TossApiClient()
    return _default_client.get_current_price(symbol)


def get_current_prices(symbols: list[str]) -> dict[str, CurrentPrice]:
    global _default_client
    if _default_client is None:
        _default_client = TossApiClient()
    return _default_client.get_current_prices(symbols)
