from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests


HALTS_URL = "https://upcuit.com/api/v1/halts"
REQUEST_TIMEOUT_SECONDS = 10
LUDP_INITIAL_PAUSE_SECONDS = 5 * 60


@dataclass(frozen=True)
class HaltInfo:
    symbol: str
    status: str
    reason_codes: tuple[str, ...]
    halted_at: datetime | None
    quote_resumed_at: datetime | None
    resumed_at: datetime | None

    @property
    def is_ludp(self) -> bool:
        return "LUDP" in self.reason_codes

    @property
    def expected_resume_at(self) -> datetime | None:
        if not self.is_ludp or self.halted_at is None:
            return None
        return self.halted_at + timedelta(seconds=LUDP_INITIAL_PAUSE_SECONDS)


@dataclass(frozen=True)
class HaltRefreshEvent:
    halts: dict[str, HaltInfo] | None
    error: str | None = None


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_halts(payload: object) -> dict[str, HaltInfo]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("거래정지 응답의 data가 배열이 아닙니다.")
    halts: dict[str, HaltInfo] = {}
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        status = str(item.get("status", "")).strip().lower()
        if not symbol or status not in {"halted", "quote_resumed", "resumed"}:
            continue
        reasons = item.get("reasons")
        reason_codes = tuple(
            str(reason.get("code", "")).strip().upper()
            for reason in reasons
            if isinstance(reason, dict) and str(reason.get("code", "")).strip()
        ) if isinstance(reasons, list) else ()
        halts[symbol] = HaltInfo(
            symbol=symbol,
            status=status,
            reason_codes=reason_codes,
            halted_at=_parse_datetime(item.get("halted_at")),
            quote_resumed_at=_parse_datetime(item.get("quote_resumed_at")),
            resumed_at=_parse_datetime(item.get("resumed_at")),
        )
    return halts


def halt_display_text(halt: HaltInfo | None, now: datetime | None = None) -> str:
    if halt is None or halt.status == "resumed":
        return ""
    if halt.status == "quote_resumed":
        return "재개중..."
    expected = halt.expected_resume_at
    if expected is None:
        return "거래정지 · 재개시간 미정"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = math.ceil((expected - current).total_seconds())
    if seconds <= 0:
        return "재개중..."
    if seconds < 60:
        return f"{seconds}초후 풀림"
    return f"{math.ceil(seconds / 60)}분 남음"


class HaltApiClient:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()
        self._etag: str | None = None

    def fetch_halts(self) -> dict[str, HaltInfo] | None:
        headers = {"Accept": "application/json"}
        if self._etag:
            headers["If-None-Match"] = self._etag
        response = self._session.get(
            HALTS_URL,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 304:
            return None
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            meta = payload.get("meta")
            if isinstance(meta, dict) and meta.get("stale") is True:
                raise RuntimeError("UpCuit 거래정지 데이터가 stale 상태입니다.")
        self._etag = response.headers.get("ETag") or self._etag
        return parse_halts(payload)


class HaltRefreshWorker:
    def __init__(
        self,
        interval_seconds: float,
        on_complete: Callable[[HaltRefreshEvent], None] | None = None,
        client: HaltApiClient | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("halt refresh interval은 0보다 커야 합니다.")
        self._interval_seconds = interval_seconds
        self._on_complete = on_complete
        self._client = client or HaltApiClient()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="upcuit-halt-refresh-worker",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        self._started = False

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                halts = self._client.fetch_halts()
                event = HaltRefreshEvent(halts=halts)
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                event = HaltRefreshEvent(
                    halts=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
            if self._on_complete is not None:
                self._on_complete(event)
            self._stop_event.wait(self._interval_seconds)
