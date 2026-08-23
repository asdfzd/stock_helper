from __future__ import annotations

from dataclasses import dataclass, field
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
        values = [
            *self.daily_price_candidates,
            *self.minute_walls,
            self.taecho,
            self.buy_price,
            self.rebound_price,
        ]
        return list(dict.fromkeys(value for value in values if value is not None))


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

    def all(self) -> tuple[StockRecord, ...]:
        with self._lock:
            return tuple(self._stocks.values())

    def symbols(self) -> list[str]:
        with self._lock:
            return list(self._stocks)

    def refresh_current_prices(self) -> RefreshResult:
        """등록된 symbol 전체를 매 호출마다 한 번의 API 요청으로 갱신한다.

        동기식 경계이므로 향후 QThread/worker에서 이 메서드만 호출하면 된다.
        네트워크 중에는 registry lock을 잡지 않는다.
        """
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
                        stock.price_status = "unavailable"
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
                    # 기존 가격은 보존하되 이번 refresh 결과가 없음을 표시한다.
                    stock.price_status = "unavailable"
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
        """stock_code 기준으로 한 chart_type의 최신 OCR 분석만 병합한다.

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
            if stock is None:
                stock = StockRecord(symbol, stock_info.stock_name or symbol)
                self._stocks[symbol] = stock
            elif stock_info.stock_name and stock_info.stock_name.strip():
                stock.stock_name = stock_info.stock_name.strip()

            if result.current.status == "valid" and result.current.value is not None:
                stock.current_price = result.current.value
                stock.price_status = "valid"
                stock.price_error = None

            if chart_type == "daily":
                stock.daily_values = dict(valid_items)
                stock.daily_price_candidates = list(
                    dict.fromkeys(valid_items.values())
                )
                stock.daily_loaded = True
            else:
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

            # 기존 소비자를 위한 합산 벽 목록. 출처별 원본은 위 필드에 유지된다.
            stock.walls = list(
                dict.fromkeys(
                    [*stock.daily_price_candidates, *stock.minute_walls]
                )
            )
            return stock
