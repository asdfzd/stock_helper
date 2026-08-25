from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

import live_capture
from live_capture import (
    CaptureProcessor,
    OCRToken,
    TickerCalibrationTask,
    read_ticker_crop,
    select_calibration_ticker_token,
    ticker_bbox_to_screen_roi,
    validate_ticker_text,
)
from stock_models import StockRegistry


class FilenameTickerReader:
    def predict(self, image_path: str):
        name = Path(image_path).name
        ticker = next(
            (symbol for symbol in ("XPON", "DAIC", "LUCY") if symbol in name),
            "DAIC",
        )
        yield {
            "rec_texts": [ticker],
            "rec_scores": [0.99],
            "rec_boxes": np.array([[4, 3, 80, 30]], dtype=np.int32),
        }


def main() -> int:
    token = OCRToken("DAIC", 0.99, (60, 30, 180, 90))
    selected = select_calibration_ticker_token([token], (40, 20))
    assert selected is not None and selected[1] == "DAIC"
    final_roi = ticker_bbox_to_screen_roi(
        token.bbox,
        (100, 200, 260, 260),
        (0, 0, 1920, 1080),
    )
    assert final_roi == (116, 207, 164, 233)

    for valid in ("A", "AI", "IBM", "INC", "DAIC", "XPON", "LUCY"):
        assert validate_ticker_text(valid) == valid
    for invalid in ("ABCDE", "1234", "A.B", "A-B", ""):
        assert validate_ticker_text(invalid) is None

    with tempfile.TemporaryDirectory() as temp_directory:
        temp = Path(temp_directory)
        reader = FilenameTickerReader()
        read_values: list[str] = []
        for ticker in ("XPON", "DAIC", "LUCY"):
            path = temp / f"fixed_roi_{ticker}.png"
            assert cv2.imwrite(str(path), np.full((18, 64), 180, dtype=np.uint8))
            result = read_ticker_crop(reader, path)
            assert result.ticker == ticker
            read_values.append(result.ticker)
        assert read_values == ["XPON", "DAIC", "LUCY"]

        calibration_search = temp / "ticker_calibration_search_mock.png"
        Image.new("RGB", (160, 60), (30, 30, 30)).save(calibration_search)
        calibration_results = []
        calibration_processor = CaptureProcessor(
            lambda: reader,
            StockRegistry(),
            on_calibration=calibration_results.append,
        )
        calibration_processor._reader = reader
        calibration_task = TickerCalibrationTask(
            "mock",
            calibration_search,
            (100, 200, 260, 260),
            (140, 220),
            (0, 0, 1920, 1080),
        )
        with patch.object(live_capture, "OUTPUT_DIRECTORY", temp):
            calibration_processor._process_ticker_calibration(calibration_task)
        assert calibration_results and calibration_results[0].success
        assert calibration_results[0].ticker == "DAIC"
        assert calibration_results[0].ticker_roi == (97, 198, 131, 213)
        assert (temp / "ticker_calibration_final_mock.png").is_file()

        ticker_at_hotkey = Image.new("RGB", (20, 10), (220, 10, 10))
        tooltip_at_hotkey = Image.new("RGB", (330, 950), (10, 20, 220))
        ticker_roi = (10, 20, 30, 30)
        combined_at_hotkey = Image.new("RGB", (820, 950), (0, 0, 0))
        combined_at_hotkey.paste(ticker_at_hotkey, (0, 20))
        combined_at_hotkey.paste(tooltip_at_hotkey, (490, 0))
        processor = CaptureProcessor(lambda: reader, StockRegistry())
        with (
            patch.object(live_capture, "OUTPUT_DIRECTORY", temp),
            patch.object(live_capture, "SAVE_LIVE_CAPTURE", True),
            patch.object(live_capture, "get_mouse_position", return_value=(500, 400)),
            patch.object(
                live_capture,
                "monitor_bounds_for_point",
                return_value=(0, 0, 1920, 1080),
            ),
            patch.object(
                live_capture.ImageGrab,
                "grab",
                return_value=combined_at_hotkey.copy(),
            ) as grab_mock,
        ):
            task = processor.enqueue_live_capture(ticker_roi)
            assert grab_mock.call_count == 1
            assert grab_mock.call_args.kwargs == {
                "bbox": (10, 0, 830, 950),
                "all_screens": True,
            }
            assert not task.image_path.exists()
            assert task.ticker_image_path is not None
            assert not task.ticker_image_path.exists()
            processor._materialize_live_capture(task)
        assert task.ticker_roi == ticker_roi
        assert task.ticker_image_path is not None and task.ticker_image_path.is_file()
        assert task.captured_at is not None
        with Image.open(task.ticker_image_path) as saved_ticker:
            assert saved_ticker.getpixel((0, 0)) == (220, 10, 10)
        with Image.open(task.image_path) as saved_tooltip:
            assert saved_tooltip.getpixel((0, 0)) == (10, 20, 220)

    print("[TICKER CAPTURE TEST] passed")
    print("calibration: DAIC selected near mouse")
    print(f"final_roi_with_margin: {final_roi}")
    print("format_validation: 1-4 uppercase only")
    print("fixed_roi_reads: XPON -> DAIC -> LUCY")
    print("capture_time_pairing: one screen grab, queued in memory before file saving")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
