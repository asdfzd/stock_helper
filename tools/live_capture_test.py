from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from live_capture import (
    CaptureProcessor,
    GlobalCaptureHotkey,
    TickerCalibrationResult,
    enable_dpi_awareness,
    get_mouse_position,
)
from paddle_ocr_validation import INPUTS, create_reader
from stock_models import StockRegistry
from tooltip_capture_config import TICKER_FIXED_ROI, USE_FIXED_TICKER_ROI


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stock Helper 글로벌 Tooltip 캡처")
    parser.add_argument(
        "--test-images",
        action="store_true",
        help="글로벌 핫키 대신 기존 전처리 daily/minute 이미지를 queue로 처리",
    )
    parser.add_argument(
        "--hotkey-only",
        action="store_true",
        help="PaddleOCR/API 없이 글로벌 캡처 키 수신과 마우스 위치만 테스트",
    )
    parser.add_argument(
        "--image",
        type=Path,
        help="저장된 live 원본 PNG 하나를 live와 동일한 전처리/OCR 경로로 실행",
    )
    return parser.parse_args()


def wait_for_ctrl_c(
    hotkey: GlobalCaptureHotkey, show_registered: bool = False
) -> bool:
    interrupted = False
    try:
        hotkey.start()
        if show_registered:
            print("\nRegistered:")
            for key in hotkey.registered_keys:
                print(key)
        print("\nReady.")
        stop_event = threading.Event()
        while not stop_event.wait(1.0):
            pass
    except KeyboardInterrupt:
        interrupted = True
        print("\nStopping ...")
    finally:
        try:
            hotkey.stop()
        except KeyboardInterrupt:
            interrupted = True
            print("\nStopping ...")
    return interrupted


def main() -> int:
    args = parse_args()
    enable_dpi_awareness()
    if args.hotkey_only:
        print("Stock Helper Hotkey Test")

        def print_hotkey_event(key: str) -> None:
            print(f"[HOTKEY] key={key} mouse={get_mouse_position()}", flush=True)

        wait_for_ctrl_c(GlobalCaptureHotkey(print_hotkey_event), show_registered=True)
        print("Stopped.")
        return 0

    print("Stock Helper Live Capture")
    print("PaddleOCR will initialize in the OCR worker thread.")
    registry = StockRegistry()
    ticker_roi: list[tuple[int, int, int, int] | None] = [
        TICKER_FIXED_ROI if USE_FIXED_TICKER_ROI else None
    ]
    calibration_in_progress = threading.Event()

    def calibration_finished(result: TickerCalibrationResult) -> None:
        calibration_in_progress.clear()
        if result.success and result.ticker_roi is not None:
            ticker_roi[0] = result.ticker_roi
            print(
                f"티커 위치 설정 완료 · 현재 인식: {result.ticker}\n`: capture stock",
                flush=True,
            )
        else:
            print(
                "티커명을 찾지 못했습니다. 티커명 위치에서 ` 키를 다시 눌러주세요",
                flush=True,
            )

    processor = CaptureProcessor(
        create_reader,
        registry,
        on_calibration=calibration_finished,
    )
    processor.start()

    if args.image is not None:
        image_path = args.image.resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")
        processor.enqueue_saved_capture(image_path)
        processor.stop(drain=True)
        return 0

    if args.test_images:
        for name, image_path in INPUTS.items():
            processor.enqueue_preprocessed_image(Path(image_path), name)
        processor.stop(drain=True)
        return 0

    def capture_for_hotkey(_key: str) -> None:
        if ticker_roi[0] is None:
            if calibration_in_progress.is_set():
                print("티커 위치 판독 중입니다", flush=True)
                return
            calibration_in_progress.set()
            processor.enqueue_ticker_calibration()
            return
        processor.enqueue_live_capture(ticker_roi[0])

    hotkey = GlobalCaptureHotkey(capture_for_hotkey)
    if USE_FIXED_TICKER_ROI:
        print(f"[TICKER ROI] fixed={TICKER_FIXED_ROI}")
        print("`: capture stock")
    else:
        print("티커명 위치에서 ` 키를 눌러주세요")
        print("`: set ticker position")
    print("Ctrl+C: quit")
    interrupted = wait_for_ctrl_c(hotkey)
    processor.stop(drain=not interrupted)
    print("Stopped.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        raise SystemExit(0)
