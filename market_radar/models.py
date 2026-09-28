from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def instant(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError("Timezone-aware timestamps required")
    return dt.astimezone(timezone.utc)


def utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Timezone-aware timestamps required")
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class Session:
    date: str
    open_utc: str
    close_utc: str

    def validate(self):
        start, end = instant(self.open_utc), instant(self.close_utc)
        if start >= end or start.astimezone(ET).date().isoformat() != self.date:
            raise ValueError("Invalid exchange session")
        if start.astimezone(ET).strftime("%H:%M:%S") != "09:30:00":
            raise ValueError("Regular session must open at 09:30 ET")


@dataclass(frozen=True)
class Bar:
    start_utc: str
    end_utc: str
    high: float
    low: float
    close: float
    volume: float
    notional: float | None = None  # actual sum(trade price * trade size)


@dataclass(frozen=True)
class HistoricalVolume:
    session_date: str
    elapsed_seconds: int
    volume: float


@dataclass(frozen=True)
class Observation:
    ticker: str
    asof_utc: str
    price: float
    previous_close: float | None
    session: Session
    bars: tuple[Bar, ...] = ()
    historical_volumes: tuple[HistoricalVolume, ...] = ()
    # Exact recent trading sessions, newest first, supplied by exchange calendar.
    history_dates: tuple[str, ...] = ()
    in_universe: bool = True  # False for follow-up-only symbols outside ranking universe.
    metadata: dict = field(default_factory=dict)

    def validate(self):
        self.session.validate()
        at = instant(self.asof_utc)
        if not self.ticker or self.ticker != self.ticker.strip().upper():
            raise ValueError("Normalized ticker required")
        if not instant(self.session.open_utc) <= at <= instant(self.session.close_utc):
            raise ValueError("Observation outside regular session")
        if not isfinite(self.price) or self.price <= 0:
            raise ValueError("Invalid price")
        if self.previous_close is not None and (
            not isfinite(self.previous_close) or self.previous_close <= 0
        ):
            raise ValueError("Invalid previous close")
        previous_end = instant(self.session.open_utc)
        for bar in self.bars:
            start, end = instant(bar.start_utc), instant(bar.end_utc)
            if start < previous_end or not 0 < (end - start).total_seconds() <= 60 or end > at:
                raise ValueError("Overlapping, future or invalid bar")
            if not all(isfinite(x) for x in (bar.high, bar.low, bar.close, bar.volume)):
                raise ValueError("Non-finite bar")
            if not 0 < bar.low <= bar.close <= bar.high or bar.volume < 0:
                raise ValueError("Invalid OHLC/volume")
            if bar.notional is not None and (not isfinite(bar.notional) or bar.notional < 0):
                raise ValueError("Invalid traded notional")
            previous_end = end
        if len(set(self.history_dates)) != len(self.history_dates) or any(
            d >= self.session.date for d in self.history_dates
        ) or list(self.history_dates) != sorted(self.history_dates, reverse=True):
            raise ValueError("History dates must be distinct prior sessions, newest first")
        keys = [(h.session_date, h.elapsed_seconds) for h in self.historical_volumes]
        if len(keys) != len(set(keys)) or any(
            not isfinite(h.volume) or h.volume < 0 or h.elapsed_seconds < 0
            for h in self.historical_volumes
        ):
            raise ValueError("Invalid historical volume")


@dataclass(frozen=True)
class DailyClose:
    ticker: str
    session_date: str
    timestamp_utc: str
    price: float
    source: str = "official"
    status: str = "complete"


@dataclass(frozen=True)
class MarketBatch:
    timestamp_utc: str
    observations: tuple[Observation, ...]
    # Calendar must include every intervening trading session (incl. early closes).
    sessions: tuple[Session, ...] = ()
    daily_closes: tuple[DailyClose, ...] = ()
    universe: str = "unspecified"
    metadata: dict = field(default_factory=dict)

    def validate(self):
        instant(self.timestamp_utc)
        tickers = [o.ticker for o in self.observations]
        if len(tickers) != len(set(tickers)):
            raise ValueError("Duplicate ticker in batch")
        for o in self.observations:
            o.validate()
            if instant(o.asof_utc) != instant(self.timestamp_utc):
                raise ValueError("Rankings require a common market timestamp")
        for s in self.sessions:
            s.validate()
        for c in self.daily_closes:
            if not isfinite(c.price) or c.price <= 0 or instant(c.timestamp_utc) > instant(self.timestamp_utc):
                raise ValueError("Invalid/future official close")

    def raw(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> MarketBatch:
        observations = []
        for row in data.get("observations", []):
            row = dict(row)
            row["session"] = Session(**row["session"])
            row["bars"] = tuple(Bar(**b) for b in row.get("bars", []))
            row["historical_volumes"] = tuple(HistoricalVolume(**h) for h in row.get("historical_volumes", []))
            row["history_dates"] = tuple(row.get("history_dates", []))
            observations.append(Observation(**row))
        return cls(data["timestamp_utc"], tuple(observations),
                   tuple(Session(**s) for s in data.get("sessions", [])),
                   tuple(DailyClose(**c) for c in data.get("daily_closes", [])),
                   data.get("universe", "unspecified"), data.get("metadata", {}))
