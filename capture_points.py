from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


CONFIG_DIRECTORY = Path(
    os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
) / "StockHelper"
CONFIG_PATH = CONFIG_DIRECTORY / "capture_points.json"


@dataclass(frozen=True)
class CapturePoints:
    ticker: tuple[int, int]
    after_copy: tuple[int, int]


def load_capture_points() -> CapturePoints | None:
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        ticker = tuple(payload["ticker"])
        after_copy = tuple(payload["after_copy"])
        if (
            len(ticker) != 2
            or len(after_copy) != 2
            or not all(isinstance(value, int) for value in ticker + after_copy)
        ):
            return None
        return CapturePoints(ticker, after_copy)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_capture_points(points: CapturePoints) -> Path:
    CONFIG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_path = CONFIG_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(
            {"ticker": list(points.ticker), "after_copy": list(points.after_copy)},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(CONFIG_PATH)
    return CONFIG_PATH
