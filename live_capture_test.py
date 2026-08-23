from __future__ import annotations

import argparse
import threading
from pathlib import Path

from live_capture import (
    CaptureProcessor,
    GlobalF8Hotkey,
    enable_dpi_awareness,
    get_mouse_position,
)
from paddle_ocr_validation import INPUTS, create_reader
from stock_models import StockRegistry


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


def wait_for_ctrl_c(hotkey: GlobalF8Hotkey, show_registered: bool = False) -> None:
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
        print("\nStopping ...")
    finally:
        hotkey.stop()


def main() -> int:
    args = parse_args()
    enable_dpi_awareness()
    if args.hotkey_only:
        print("Stock Helper Hotkey Test")

        def print_hotkey_event(key: str) -> None:
            print(f"[HOTKEY] key={key} mouse={get_mouse_position()}", flush=True)

        wait_for_ctrl_c(GlobalF8Hotkey(print_hotkey_event), show_registered=True)
        print("Stopped.")
        return 0

    print("Stock Helper Live Capture")
    print("PaddleOCR will initialize in the OCR worker thread.")
    registry = StockRegistry()
    processor = CaptureProcessor(create_reader, registry)
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
        processor.enqueue_live_capture()

    hotkey = GlobalF8Hotkey(capture_for_hotkey)
    print("0 / - / = / `: capture tooltip")
    print("Ctrl+C: quit")
    wait_for_ctrl_c(hotkey)
    processor.stop(drain=True)
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
