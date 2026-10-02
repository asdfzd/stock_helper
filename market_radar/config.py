from dataclasses import dataclass
from pathlib import Path

from runtime_paths import APP_ROOT

DEFAULT_SHARED_CACHE_PATH = APP_ROOT / "data" / "market_radar_cache" / "kiwoom_v2.db"


@dataclass(frozen=True)
class RadarConfig:
    provider: str = "unconfigured"
    test_version: str = "market_radar_v1"
    snapshot_seconds: float = 30.0
    rvol_lookback_days: int = 14
    max_data_delay_seconds: float = 90.0
    result_tolerance_seconds: float = 30.0
    result_pre_target_grace_seconds: float = 0.25
    rest_negative_ttl_seconds: float = 21600.0
    export_seconds: float = 300.0
    session: str = "regular"
    data_dir: Path = APP_ROOT / "data"
    historical_cache_path: Path | None = None
    max_ws_symbols: int = 200
    candidate_refresh_seconds: float = 60.0
    candidate_pages: int = 1
    backfill_concurrency: int = 1
    rest_requests_per_second: float = 3.0
    peak_rest_requests_per_second: float = 2.0
    include_extended_hours: bool = False
    enable_orderbook_capture: bool = False
    exact_vwap_enabled: bool = False
    stock_type_policy: str = "common_and_unknown"
    bar_label_policy: str = "assumed_et_start"
    bar_finalization_seconds: float = 2.0
    max_chart_pages: int = 20
    rest_retries: int = 2
    reconnect_max_seconds: float = 60.0
    raw_queue_size: int = 20000
    rvol_backfill_enabled: bool = True
    rvol_backfill_min_day_volume: float = 0
    rvol_backfill_min_price: float = 0
    result_tracking_hold_minutes: float = 60

    def __post_init__(self):
        if min(self.snapshot_seconds, self.max_data_delay_seconds,
               self.result_tolerance_seconds, self.export_seconds) <= 0:
            raise ValueError("Radar intervals must be positive")
        if self.rvol_lookback_days < 1 or self.session != "regular":
            raise ValueError("Positive RVOL lookback required; v1 supports regular sessions only")
        if not 1 <= self.max_ws_symbols <= 200 or self.backfill_concurrency != 1:
            raise ValueError("Kiwoom supports <=200 slots and one serialized backfill worker")
        if not 0 < self.rest_requests_per_second <= 4 or not 0 < self.peak_rest_requests_per_second < 3:
            raise ValueError("Conservative Kiwoom request limits required")
        if (self.include_extended_hours or self.exact_vwap_enabled or
                self.stock_type_policy not in {"common_and_unknown", "all"} or
                self.bar_label_policy != "assumed_et_start"):
            raise ValueError("Unsupported/unverified Kiwoom data policy")
        if min(self.candidate_refresh_seconds, self.candidate_pages, self.max_chart_pages,
               self.reconnect_max_seconds, self.raw_queue_size) <= 0 or not 0 <= self.rest_retries <= 3:
            raise ValueError("Invalid provider budget")
        if not 0 < self.rest_negative_ttl_seconds < float("inf"):
            raise ValueError("Negative cache TTL must be positive")
        if self.bar_finalization_seconds < 0:
            raise ValueError("Invalid bar finalization delay")
        from math import isfinite
        if any(not isfinite(v) or v < 0 for v in (self.rvol_backfill_min_day_volume,
                self.rvol_backfill_min_price, self.result_tracking_hold_minutes,
                self.result_pre_target_grace_seconds)):
            raise ValueError("Invalid backfill/tracking policy")

    @property
    def cache_path(self) -> Path:
        # None preserves the original CLI --data-dir/cache relationship.
        return (Path(self.historical_cache_path) if self.historical_cache_path is not None
                else self.data_dir / "market_radar_cache" / "kiwoom_v2.db")

    @property
    def db_path(self) -> Path:
        return self.data_dir / (("stock_helper_market_kiwoom_v2_1.db" if self.test_version == "kiwoom_radar_v2_1" else "stock_helper_market_kiwoom_v2.db") if self.provider == "kiwoom" else "stock_helper_market.db")


CONFIG = RadarConfig(provider="kiwoom", test_version="kiwoom_radar_v2_1")
