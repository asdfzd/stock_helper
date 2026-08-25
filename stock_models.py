from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from typing import Any, Iterable

from toss_api import CurrentPrice, TossApiClient, TossApiError


@dataclass
class StockRecord:
    stock_code: str
    stock_name: str
    current_price: Decimal | None = None
    buy_price: Decimal | None = None
    rebound_price: Decimal | None = None
    taecho: Decimal | None = None
    absolute_half: Decimal | None = None
    walls: list[Decimal] = field(default_factory=list)
    daily_values: dict[str, Decimal] = field(default_factory=dict)
    minute_values: dict[str, Decimal] = field(default_factory=dict)
    daily_price_candidates: list[Decimal] = field(default_factory=list)
    minute_walls: list[Decimal] = field(default_factory=list)
    daily_loaded: bool = False
    minute_loaded: bool = False
    holding: bool = False
    last_price_update: str | None = None
    price_status: str = "unavailable"
    price_error: str | None = None

    @property
    def display_name(self) -> str:
        return self.stock_name[:6]

    @property
    def price_candidates(self) -> list[Decimal]:
        return [candidate.value for candidate in build_price_candidates(self)]


@dataclass(frozen=True)
class PriceCandidate:
    """UI에서 사용할 출처 라벨이 보존된 유효 가격 후보."""

    key: str
    label: str
    value: Decimal


def candidate_label(key: str) -> str:
    if key.startswith("moving_average_"):
        period = key.removeprefix("moving_average_").removesuffix("_wall")
        return f"이평{period}"
    if key.startswith("day"):
        if key.endswith("_floor"):
            return f"{key.removesuffix('_floor')} 바닥"
        if key.endswith("_wall"):
            return f"{key.removesuffix('_wall')} 벽"
        return key
    if key.startswith("corpse_wall_"):
        return "시체소굴"
    return {
        "buy_price": "매입가",
        "rebound_price": "반등가",
        "taecho": "태초마을",
    }.get(key, key)


def build_price_candidates(stock: StockRecord) -> list[PriceCandidate]:
    """daily와 minute 최신 snapshot에서 UI 후보를 중복 없이 만든다.

    Registry에 저장된 valid 값만 입력되며, absolute_half는 태초마을 통합
    규칙에 따라 독립 UI 후보로 사용하지 않는다.
    """
    ordered_values = [
        *stock.daily_values.items(),
        *(
            (key, value)
            for key, value in stock.minute_values.items()
            if key.startswith("corpse_wall_")
        ),
        ("taecho", stock.taecho),
        ("buy_price", stock.buy_price),
        ("rebound_price", stock.rebound_price),
    ]
    seen: set[Decimal] = set()
    candidates: list[PriceCandidate] = []
    for key, value in ordered_values:
        if value is None or value in seen:
            continue
        seen.add(value)
        candidates.append(PriceCandidate(key, candidate_label(key), value))
    return candidates


@dataclass(frozen=True)
class RefreshResult:
    requested_symbols: tuple[str, ...]
    updated_symbols: tuple[str, ...]
    unavailable_symbols: tuple[str, ...]
    error: str | None = None


class StockRegistry:
    """UI와 네트워크 계층 사이에서 여러 종목의 독립 상태를 관리한다."""

    def __init__(self, api_client: TossApiClient | None = None) -> None:
        self._stocks: dict[str, StockRecord] = {}
        self._api_client = api_client or TossApiClient()
        self._lock = RLock()
        self._refresh_lock = RLock()

    @property
    def api_client(self) -> TossApiClient:
        return self._api_client

    def register(self, stock: StockRecord) -> StockRecord:
        symbol = stock.stock_code.strip().upper()
        if not symbol:
            raise ValueError("stock_code가 비어 있습니다.")
        stock.stock_code = symbol
        with self._lock:
            self._stocks[symbol] = stock
        return stock

    def get(self, symbol: str) -> StockRecord | None:
        with self._lock:
            return self._stocks.get(symbol.strip().upper())

    @staticmethod
    def _snapshot(stock: StockRecord) -> StockRecord:
        return replace(
            stock,
            walls=list(stock.walls),
            daily_values=dict(stock.daily_values),
            minute_values=dict(stock.minute_values),
            daily_price_candidates=list(stock.daily_price_candidates),
            minute_walls=list(stock.minute_walls),
        )

    def get_snapshot(self, symbol: str) -> StockRecord | None:
        """UI가 worker 갱신 중인 mutable record를 직접 읽지 않게 복사한다."""
        with self._lock:
            stock = self._stocks.get(symbol.strip().upper())
            return self._snapshot(stock) if stock is not None else None

    def all(self) -> tuple[StockRecord, ...]:
        with self._lock:
            return tuple(self._stocks.values())

    def all_snapshots(self) -> tuple[StockRecord, ...]:
        """ticker 최초 등록 순서를 유지한 UI용 snapshot 목록."""
        with self._lock:
            return tuple(self._snapshot(stock) for stock in self._stocks.values())

    def symbols(self) -> list[str]:
        with self._lock:
            return list(self._stocks)

    def remove(self, symbol: str) -> StockRecord | None:
        """종목을 Registry와 이후 현재가 polling 대상에서 제거한다."""
        normalized = symbol.strip().upper()
        with self._lock:
            return self._stocks.pop(normalized, None)

    def set_holding(self, symbol: str, holding: bool) -> None:
        with self._lock:
            stock = self._stocks.get(symbol.strip().upper())
            if stock is None:
                raise KeyError(f"등록되지 않은 종목입니다: {symbol}")
            stock.holding = holding

    def mark_price_stale(self, symbol: str, error: str) -> None:
        with self._lock:
            stock = self._stocks.get(symbol.strip().upper())
            if stock is not None:
                stock.price_status = (
                    "stale" if stock.current_price is not None else "unavailable"
                )
                stock.price_error = error

    def refresh_current_prices(self) -> RefreshResult:
        """등록된 symbol 전체를 매 호출마다 한 번의 API 요청으로 갱신한다.

        동기식 경계이므로 향후 QThread/worker에서 이 메서드만 호출하면 된다.
        네트워크 중에는 registry lock을 잡지 않는다.
        """
        with self._refresh_lock:
            symbols = self.symbols()
            if not symbols:
                return RefreshResult((), (), ())
            try:
                quotes = self._api_client.get_current_prices(symbols)
            except TossApiError as exc:
                with self._lock:
                    for symbol in symbols:
                        stock = self._stocks.get(symbol)
                        if stock is not None:
                            stock.price_status = (
                                "stale" if stock.current_price is not None else "unavailable"
                            )
                            stock.price_error = str(exc)
                return RefreshResult(
                    tuple(symbols), (), tuple(symbols), error=str(exc)
                )

            updated: list[str] = []
            unavailable: list[str] = []
            with self._lock:
                for symbol in symbols:
                    stock = self._stocks.get(symbol)
                    if stock is None:
                        continue
                    quote = quotes.get(symbol)
                    if quote is None:
                        # 기존 last good 가격은 보존하고 freshness 상태만 구분한다.
                        stock.price_status = (
                            "stale" if stock.current_price is not None else "unavailable"
                        )
                        stock.price_error = "API 응답에 해당 symbol 결과가 없습니다."
                        unavailable.append(symbol)
                        continue
                    self._apply_quote(stock, quote)
                    updated.append(symbol)
            return RefreshResult(
                tuple(symbols), tuple(updated), tuple(unavailable)
            )

    @staticmethod
    def _apply_quote(stock: StockRecord, quote: CurrentPrice) -> None:
        stock.current_price = quote.last_price
        stock.last_price_update = quote.timestamp or datetime.now(timezone.utc).isoformat()
        stock.price_status = "valid"
        stock.price_error = None

    def update_ocr_values(
        self,
        symbol: str,
        *,
        stock_name: str | None = None,
        buy_price: Decimal | None = None,
        rebound_price: Decimal | None = None,
        taecho: Decimal | None = None,
        absolute_half: Decimal | None = None,
        walls: Iterable[Decimal] | None = None,
    ) -> StockRecord:
        normalized = symbol.strip().upper()
        with self._lock:
            stock = self._stocks.get(normalized)
            if stock is None:
                stock = StockRecord(normalized, stock_name or normalized)
                self._stocks[normalized] = stock
            elif stock_name:
                stock.stock_name = stock_name
            if buy_price is not None:
                stock.buy_price = buy_price
            if rebound_price is not None:
                stock.rebound_price = rebound_price
            if taecho is not None:
                stock.taecho = taecho
            if absolute_half is not None:
                stock.absolute_half = absolute_half
            if walls is not None:
                stock.walls = list(dict.fromkeys(walls))
            return stock

    def merge_analysis_result(self, result: Any) -> StockRecord:
        """stock_code 기준으로 해당 chart_type의 snapshot 전체를 교체한다.

        `OcrAnalysis`의 구체 타입을 import하지 않아 저장소가 PaddleOCR에
        의존하지 않는다. 향후 F8 캡처도 analyze 단계 결과를 그대로 전달한다.
        """
        stock_info = result.stock
        symbol = (stock_info.stock_code or "").strip().upper()
        if not symbol:
            raise ValueError("분석 결과에 stock_code가 없습니다.")
        chart_type = result.chart_type
        if chart_type not in {"daily", "minute"}:
            raise ValueError(f"지원하지 않는 chart_type입니다: {chart_type!r}")

        valid_items = {
            item.key: item.value
            for item in result.items
            if item.status == "valid" and item.value is not None
        }
        with self._lock:
            stock = self._stocks.get(symbol)
            record_created = stock is None
            if stock is None:
                stock = StockRecord(symbol, stock_info.stock_name or symbol)
                self._stocks[symbol] = stock
            elif stock_info.stock_name and stock_info.stock_name.strip():
                stock.stock_name = stock_info.stock_name.strip()

            if result.current.status == "valid" and result.current.value is not None:
                stock.current_price = result.current.value
                stock.price_status = "valid"
                stock.price_error = None

            chart_was_loaded = (
                stock.daily_loaded if chart_type == "daily" else stock.minute_loaded
            )
            previous_minute_walls_count = len(stock.minute_walls)
            if chart_type == "daily":
                self._replace_daily_snapshot(stock, valid_items)
            else:
                self._replace_minute_snapshot(stock, valid_items)

            # 기존 소비자를 위한 합산 벽 목록. 출처별 원본은 위 필드에 유지된다.
            stock.walls = list(
                dict.fromkeys(
                    [*stock.daily_price_candidates, *stock.minute_walls]
                )
            )
            action = (
                "created"
                if record_created
                else (
                    f"{chart_type}_replaced"
                    if chart_was_loaded
                    else f"{chart_type}_added"
                )
            )
            print("[REGISTRY]", flush=True)
            print(f"ticker: {symbol}", flush=True)
            print(f"chart_type: {chart_type}", flush=True)
            print(f"action: {action}", flush=True)
            if chart_type == "minute":
                print("[REGISTRY REPLACE]", flush=True)
                print(f"ticker: {symbol}", flush=True)
                print(
                    f"previous_minute_walls_count: {previous_minute_walls_count}",
                    flush=True,
                )
                print(
                    f"new_minute_walls_count: {len(stock.minute_walls)}",
                    flush=True,
                )
            return stock

    @staticmethod
    def _replace_daily_snapshot(
        stock: StockRecord, valid_items: dict[str, Decimal]
    ) -> None:
        stock.daily_values = dict(valid_items)
        stock.daily_price_candidates = list(dict.fromkeys(valid_items.values()))
        stock.daily_loaded = True

    @staticmethod
    def _replace_minute_snapshot(
        stock: StockRecord, valid_items: dict[str, Decimal]
    ) -> None:
        # 새 캡처에 없는 값은 None/빈 목록이 되어 이전 snapshot을 보충하지 않는다.
        stock.minute_values = dict(valid_items)
        stock.minute_walls = list(
            dict.fromkeys(
                value
                for key, value in valid_items.items()
                if key.startswith("corpse_wall_")
            )
        )
        stock.buy_price = valid_items.get("buy_price")
        stock.rebound_price = valid_items.get("rebound_price")
        stock.taecho = valid_items.get("taecho")
        stock.absolute_half = valid_items.get("absolute_half")
        stock.minute_loaded = True
