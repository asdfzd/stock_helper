from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CaptureHistoryItem:
    capture_id: str
    raw_image_path: Path
    captured_at: str | None
    parsed_ticker: str | None = None
    raw_ticker_text: str = ""
    chart_type: str | None = None
    registry_symbol: str | None = None
    error: str | None = None

    @property
    def ticker_display(self) -> str:
        return self.parsed_ticker or "오류"

    @property
    def ticker_warning(self) -> bool:
        return self.parsed_ticker is None or len(self.parsed_ticker) < 4
