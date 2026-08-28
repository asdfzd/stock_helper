from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from stock_models import RefreshResult, StockRegistry


@dataclass(frozen=True)
class PriceRefreshEvent:
    result: RefreshResult
    elapsed_seconds: float


class PriceRefreshWorker:
    """OCR queue와 독립된 단일 batch current-price polling worker."""

    def __init__(
        self,
        registry: StockRegistry,
        interval_seconds: float,
        on_complete: Callable[[PriceRefreshEvent], None] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("price refresh interval은 0보다 커야 합니다.")
        self._registry = registry
        self._interval_seconds = interval_seconds
        self._on_complete = on_complete
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="toss-price-refresh-worker",
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
        # 단일 worker가 요청 완료 후 interval을 기다리므로 polling 요청은 겹치지 않는다.
        while not self._stop_event.is_set():
            symbols = self._registry.symbols()
            if symbols:
                started = time.perf_counter()
                result = self._registry.refresh_current_prices()
                elapsed = time.perf_counter() - started
                print("[PRICE REFRESH]", flush=True)
                print(f"symbols: {','.join(result.requested_symbols)}", flush=True)
                print(f"updated: {len(result.updated_symbols)}", flush=True)
                print(f"elapsed: {elapsed:.2f}s", flush=True)
                if result.error:
                    print(f"error: {result.error}", flush=True)
                if result.day_range_error:
                    print(f"day_range_error: {result.day_range_error}", flush=True)
                if self._on_complete is not None:
                    self._on_complete(PriceRefreshEvent(result, elapsed))
            self._stop_event.wait(self._interval_seconds)
