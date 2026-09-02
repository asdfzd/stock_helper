from __future__ import annotations

import sys
import tempfile
import time
from contextlib import redirect_stdout
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import paddle_ocr_validation
from live_capture import CaptureProcessor, CaptureTask
from paddle_ocr_validation import (
    OcrPerformanceMetrics,
    PriceResult,
    create_reader,
    retry_suspicious_items,
)
from paddle_validation_config import PADDLE_CPU_THREADS


class NumericReader:
    def predict(self, _image_path: str):
        yield {
            "rec_texts": ["5.0"],
            "rec_scores": [0.99],
        }


def main() -> int:
    sentinel_reader = object()
    with (
        patch.object(
            paddle_ocr_validation,
            "_select_paddle_device",
            return_value="gpu:0",
        ),
        patch.object(
            paddle_ocr_validation,
            "PaddleOCR",
            return_value=sentinel_reader,
        ) as paddle_constructor,
    ):
        assert create_reader() is sentinel_reader
    kwargs = paddle_constructor.call_args.kwargs
    assert kwargs["cpu_threads"] == PADDLE_CPU_THREADS
    assert kwargs["device"] == "gpu:0"
    assert PADDLE_CPU_THREADS >= 1
    assert kwargs["enable_mkldnn"] is False
    assert kwargs["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert kwargs["text_recognition_model_name"] == "korean_PP-OCRv5_mobile_rec"

    image = np.full((30, 40), 180, dtype=np.uint8)
    item = PriceResult(
        key="test_price",
        item_text="test",
        item_bbox=(0, 0, 10, 10),
        price_bbox=(5, 5, 20, 20),
        raw_text="",
        value=None,
        confidence=0.0,
        status="uncertain",
        reasons=["numeric_parse_failed_or_missing"],
    )
    metrics = OcrPerformanceMetrics()
    with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as temporary_directory:
        retry_directory = Path(temporary_directory) / "numeric_retry"
        with patch.object(
            paddle_ocr_validation,
            "RETRY_DIRECTORY",
            retry_directory,
        ):
            retry_suspicious_items(
                NumericReader(),
                image,
                "performance_test",
                [item],
                Decimal("10"),
                metrics,
            )
        assert list(retry_directory.glob("*.png"))

    assert metrics.numeric_retry_count == 1
    assert metrics.numeric_retry_ocr_seconds >= 0.0
    assert item.source == "numeric_retry"
    assert item.value == Decimal("5.0")

    perf_task = CaptureTask(
        "perf_test",
        Path("unused.png"),
        None,
        None,
        capture_elapsed_seconds=0.031,
        performance=metrics,
    )
    perf_output = StringIO()
    with redirect_stdout(perf_output):
        CaptureProcessor._print_performance(perf_task, time.perf_counter())
    perf_line = perf_output.getvalue().strip()
    for field in (
        "capture=",
        "ticker_pre=",
        "ticker_ocr=",
        "tooltip_pre=",
        "primary_ocr=",
        "numeric_retry=",
        "retry_count=",
        "total=",
    ):
        assert field in perf_line
    assert perf_line.startswith("[PERF] ")

    print("[PERFORMANCE OPTIMIZATION TEST] passed")
    print(f"cpu_threads: {PADDLE_CPU_THREADS}")
    print("reader_accuracy_options: unchanged")
    print("numeric_retry_metrics: verified")
    print("perf_summary_fields: verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
