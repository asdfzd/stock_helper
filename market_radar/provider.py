"""Provider contract; intentionally contains no guessed HTTP endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import MarketBatch


class ProviderError(RuntimeError):
    pass


class RateLimitError(ProviderError):
    def __init__(self, message="Provider rate limit", retry_after_seconds=60):
        super().__init__(message)
        self.retry_after_seconds = max(1, float(retry_after_seconds))


class MarketDataProvider(Protocol):
    name: str
    mode: str  # live or replay; must never silently fall back to fake data

    def connect(self) -> None: ...
    def fetch(self, tracked_tickers: tuple[str, ...]) -> MarketBatch:
        """Full ranking universe + tracked tickers, bounded network timeouts.

        Mark follow-up-only symbols in_universe=False so they cannot affect ranks.
        Supply complete session bars (including current partial bar), actual
        traded notional, matching historical volume, official exchange calendar
        and official closes when supported. Unknown fields stay absent/null.
        No look-ahead. Raise StopIteration at replay EOF.
        """
        ...
    def close(self) -> None: ...


class UnconfiguredProvider:
    name = "unconfigured"
    mode = "live"

    def connect(self):
        raise ProviderError("실제 데이터 공급원 미연결: provider 구현 및 데이터 사용 권한이 필요합니다.")

    def fetch(self, tracked_tickers=()):
        raise ProviderError("No market data provider configured")

    def close(self):
        pass


def create_live_provider(config=None) -> MarketDataProvider:
    """Single integration point for a documented, authorized data adapter."""
    from .config import CONFIG
    config = config or CONFIG
    if config.provider == "kiwoom":
        from .providers.kiwoom import KiwoomProvider
        return KiwoomProvider(config)
    return UnconfiguredProvider()


class JsonlReplayProvider:
    name = "jsonl_replay"
    mode = "replay"

    def __init__(self, path: Path):
        self.path = path
        self._stream = None

    def connect(self):
        self._stream = self.path.open(encoding="utf-8")

    def fetch(self, tracked_tickers=()):
        for line in self._stream:
            if line.strip():
                batch = MarketBatch.from_dict(json.loads(line))
                batch.validate()
                return batch
        raise StopIteration

    def close(self):
        if self._stream:
            self._stream.close()
