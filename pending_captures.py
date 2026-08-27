from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from paddle_ocr_validation import OcrAnalysis, StockIdentity


@dataclass(frozen=True)
class PendingCapture:
    capture_id: str
    identity: StockIdentity
    chart_type: str
    analysis: OcrAnalysis
    resolver_reason: str

    @property
    def display_name(self) -> str:
        return (
            self.identity.korean_name
            or self.identity.english_name
            or self.identity.raw_identity_text
            or "종목명 인식 실패"
        )


class PendingCaptureStore:
    """ticker가 확정되지 않은 parser 결과를 capture_id로 보존한다."""

    def __init__(self) -> None:
        self._captures: dict[str, PendingCapture] = {}
        self._lock = RLock()

    def add(self, pending: PendingCapture) -> None:
        with self._lock:
            self._captures[pending.capture_id] = pending

    def get(self, capture_id: str) -> PendingCapture | None:
        with self._lock:
            return self._captures.get(capture_id)

    def all(self) -> tuple[PendingCapture, ...]:
        with self._lock:
            return tuple(self._captures.values())

    def remove(self, capture_id: str) -> PendingCapture | None:
        with self._lock:
            return self._captures.pop(capture_id, None)
