from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image, ImageGrab

from paddle_ocr_validation import (
    DAILY_SECTIONS,
    MINUTE_KEYWORDS,
    OcrCapture,
    analyze_capture,
    canonical_section,
    capture_image,
)
from stock_models import StockRecord, StockRegistry
from tooltip_capture_config import (
    LIVE_CLAHE_CLIP_LIMIT,
    LIVE_CLAHE_TILE_GRID_SIZE,
    LIVE_OCR_SCALE,
    ROI_BOTTOM_OFFSET,
    ROI_LEFT_OFFSET,
    ROI_RIGHT_OFFSET,
    ROI_TOP_OFFSET,
    SAVE_LIVE_CAPTURE,
    TOOLTIP_KEEP_WIDTH_RATIO,
)


PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIRECTORY = PROJECT_ROOT / "ocr_results"
QUEUE_DIRECTORY = OUTPUT_DIRECTORY / "live_queue"
VK_0 = 0x30
VK_OEM_MINUS = 0xBD
VK_OEM_PLUS = 0xBB
VK_OEM_3 = 0xC0
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
HOTKEY_ID_0 = 1
HOTKEY_ID_MINUS = 2
HOTKEY_ID_EQUAL = 3
HOTKEY_ID_BACKTICK = 4
HOTKEYS = {
    HOTKEY_ID_0: ("0", VK_0),
    HOTKEY_ID_MINUS: ("-", VK_OEM_MINUS),
    HOTKEY_ID_EQUAL: ("=", VK_OEM_PLUS),
    HOTKEY_ID_BACKTICK: ("`", VK_OEM_3),
}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


@dataclass(frozen=True)
class CaptureTask:
    capture_id: str
    image_path: Path
    mouse_position: tuple[int, int] | None
    capture_box: tuple[int, int, int, int] | None
    already_preprocessed: bool = False
    preserve_files: bool = False


def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def get_mouse_position() -> tuple[int, int]:
    if os.name != "nt":
        raise RuntimeError("글로벌 핫키 캡처는 Windows에서만 지원합니다.")
    point = POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        raise ctypes.WinError()
    return point.x, point.y


def monitor_bounds_for_point(x: int, y: int) -> tuple[int, int, int, int]:
    point = POINT(x, y)
    monitor = ctypes.windll.user32.MonitorFromPoint(point, 2)  # nearest monitor
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    if not monitor or not ctypes.windll.user32.GetMonitorInfoW(
        monitor, ctypes.byref(info)
    ):
        raise ctypes.WinError()
    bounds = info.rcMonitor
    return bounds.left, bounds.top, bounds.right, bounds.bottom


def calculate_capture_box(
    mouse_position: tuple[int, int],
    monitor_bounds: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    mouse_x, mouse_y = mouse_position
    monitor_left, monitor_top, monitor_right, monitor_bottom = monitor_bounds
    width = min(ROI_RIGHT_OFFSET - ROI_LEFT_OFFSET, monitor_right - monitor_left)
    height = min(ROI_BOTTOM_OFFSET - ROI_TOP_OFFSET, monitor_bottom - monitor_top)
    if width <= 0 or height <= 0:
        raise ValueError("ROI offset 또는 모니터 bounds가 유효하지 않습니다.")
    desired_left = mouse_x + ROI_LEFT_OFFSET
    desired_top = mouse_y + ROI_TOP_OFFSET
    left = min(max(desired_left, monitor_left), monitor_right - width)
    top = min(max(desired_top, monitor_top), monitor_bottom - height)
    return left, top, left + width, top + height


def capture_mouse_relative_roi() -> tuple[Image.Image, tuple[int, int], tuple[int, int, int, int]]:
    mouse_position = get_mouse_position()
    bounds = monitor_bounds_for_point(*mouse_position)
    capture_box = calculate_capture_box(mouse_position, bounds)
    image = ImageGrab.grab(bbox=capture_box, all_screens=True)
    return image, mouse_position, capture_box


def trim_tooltip_image(image: Image.Image) -> Image.Image:
    if not 0 < TOOLTIP_KEEP_WIDTH_RATIO <= 1:
        raise ValueError("TOOLTIP_KEEP_WIDTH_RATIO는 0보다 크고 1 이하여야 합니다.")
    keep_width = max(1, round(image.width * TOOLTIP_KEEP_WIDTH_RATIO))
    return image.crop((0, 0, keep_width, image.height))


def preprocessed_output_path(image_path: Path) -> Path:
    stem = image_path.stem.removesuffix("_tooltip")
    return image_path.with_name(f"{stem}_preprocessed.png")


def preprocess_live_capture(raw_path: Path) -> Path:
    image = cv2.imread(str(raw_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"캡처 이미지를 읽을 수 없습니다: {raw_path}")
    upscaled = cv2.resize(
        image,
        None,
        fx=LIVE_OCR_SCALE,
        fy=LIVE_OCR_SCALE,
        interpolation=cv2.INTER_CUBIC,
    )
    grayscale = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(
        clipLimit=LIVE_CLAHE_CLIP_LIMIT,
        tileGridSize=LIVE_CLAHE_TILE_GRID_SIZE,
    )
    preprocessed = clahe.apply(grayscale)
    output_path = preprocessed_output_path(raw_path)
    if not cv2.imwrite(str(output_path), preprocessed):
        raise OSError(f"전처리 이미지 저장 실패: {output_path}")
    print(
        f"[LIVE IMAGE] preprocessed_size: {preprocessed.shape[1]}x{preprocessed.shape[0]}",
        flush=True,
    )
    return output_path


def capture_has_required_content(capture: OcrCapture) -> bool:
    text = "\n".join(line.text for line in capture.lines)
    if capture.chart_type == "minute":
        return any(keyword in text for keyword in MINUTE_KEYWORDS)
    return any(canonical_section(line.text) in DAILY_SECTIONS for line in capture.lines)


class CaptureProcessor:
    def __init__(
        self,
        reader_factory: Callable[[], Any],
        registry: StockRegistry,
        on_complete: Callable[[StockRecord], None] | None = None,
    ) -> None:
        self._reader_factory = reader_factory
        self._reader: Any | None = None
        self._registry = registry
        self._on_complete = on_complete
        self._queue: queue.Queue[CaptureTask | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="tooltip-ocr-worker", daemon=False
        )
        self._started = False
        self._reader_ready = threading.Event()
        self._startup_error: Exception | None = None

    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._reader_ready.wait()
            if self._startup_error is not None:
                self._thread.join()
                raise self._startup_error
            self._started = True

    def enqueue_live_capture(self) -> CaptureTask:
        image, mouse_position, capture_box = capture_mouse_relative_roi()
        tooltip_image = trim_tooltip_image(image)
        image_array = np.asarray(image)
        channels = image_array.shape[2] if image_array.ndim == 3 else 1
        print("[LIVE IMAGE]", flush=True)
        print(f"type: {type(image).__module__}.{type(image).__name__}", flush=True)
        print(f"raw_size: {image.width}x{image.height}", flush=True)
        print(
            f"tooltip_size: {tooltip_image.width}x{tooltip_image.height}",
            flush=True,
        )
        print(f"keep_width_ratio: {TOOLTIP_KEEP_WIDTH_RATIO:.2f}", flush=True)
        print(f"numpy_shape: {image_array.shape}", flush=True)
        print(f"dtype: {image_array.dtype}", flush=True)
        print(f"channels: {channels}", flush=True)
        print(f"contiguous: {image_array.flags.c_contiguous}", flush=True)
        capture_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        directory = OUTPUT_DIRECTORY if SAVE_LIVE_CAPTURE else QUEUE_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True)
        base_name = f"live_capture_{capture_id}"
        raw_path = directory / f"{base_name}_raw.png"
        tooltip_path = directory / f"{base_name}_tooltip.png"
        if SAVE_LIVE_CAPTURE:
            image.save(raw_path, format="PNG")
        tooltip_image.save(tooltip_path, format="PNG")
        task = CaptureTask(
            capture_id,
            tooltip_path,
            mouse_position,
            capture_box,
            preserve_files=SAVE_LIVE_CAPTURE,
        )
        self._queue.put(task)
        print(
            f"[CAPTURE] captured id={capture_id} mouse={mouse_position} roi={capture_box}",
            flush=True,
        )
        print(f"[QUEUE] pending={self._queue.qsize()}", flush=True)
        return task

    def enqueue_preprocessed_image(self, image_path: Path, name: str) -> CaptureTask:
        """저장 이미지로 worker/merge 흐름만 확인하기 위한 테스트 경계."""
        task = CaptureTask(name, image_path, None, None, True, True)
        self._queue.put(task)
        print(f"[QUEUE] test image={image_path.name} pending={self._queue.qsize()}")
        return task

    def enqueue_saved_capture(self, image_path: Path) -> CaptureTask:
        """저장된 live 원본 PNG를 live와 동일한 파일 전처리 경로로 재실행한다."""
        if image_path.stem.endswith("_tooltip"):
            tooltip_path = image_path
        else:
            with Image.open(image_path) as raw_image:
                raw_size = raw_image.size
                tooltip_image = trim_tooltip_image(raw_image.convert("RGB"))
                base_stem = image_path.stem.removesuffix("_raw")
                tooltip_path = image_path.with_name(f"{base_stem}_tooltip.png")
                tooltip_image.save(tooltip_path, format="PNG")
            print(
                "[LIVE IMAGE] saved raw trim "
                f"raw_size={raw_size[0]}x{raw_size[1]} "
                f"tooltip_size={tooltip_image.width}x{tooltip_image.height} "
                f"keep_width_ratio={TOOLTIP_KEEP_WIDTH_RATIO:.2f}",
                flush=True,
            )
        task = CaptureTask(
            f"saved_{image_path.stem}", tooltip_path, None, None, False, True
        )
        self._queue.put(task)
        print(
            f"[QUEUE] saved capture={tooltip_path.name} pending={self._queue.qsize()}",
            flush=True,
        )
        return task

    def stop(self, drain: bool = True) -> None:
        if not self._started:
            return
        if drain:
            self._queue.join()
        self._queue.put(None)
        self._thread.join()
        self._started = False

    def _run(self) -> None:
        try:
            print(
                f"[PADDLE] reader create start thread={threading.current_thread().name}",
                flush=True,
            )
            started = time.perf_counter()
            self._reader = self._reader_factory()
            print(
                "[PADDLE] reader create complete "
                f"elapsed={time.perf_counter() - started:.2f}s "
                f"thread={threading.current_thread().name}",
                flush=True,
            )
        except Exception as exc:
            self._startup_error = exc
        finally:
            self._reader_ready.set()
        if self._startup_error is not None:
            return

        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                self._process(task)
            except Exception as exc:  # 한 캡처 실패가 worker를 종료하면 안 된다.
                print("[CAPTURE FAILED]", flush=True)
                print(
                    f"capture_id: {task.capture_id if task else 'unknown'}",
                    flush=True,
                )
                print(f"reason: {type(exc).__name__}: {exc}", flush=True)
            finally:
                if (
                    task is not None
                    and not task.preserve_files
                    and not task.already_preprocessed
                ):
                    task.image_path.unlink(missing_ok=True)
                    preprocessed_output_path(task.image_path).unlink(missing_ok=True)
                self._queue.task_done()

    def _process(self, task: CaptureTask) -> None:
        total_started = time.perf_counter()
        print(f"[OCR STEP] start capture_id={task.capture_id}", flush=True)
        step_started = time.perf_counter()
        print(
            "[OCR STEP] before preprocessing "
            f"already_preprocessed={task.already_preprocessed}",
            flush=True,
        )
        preprocessed_path = (
            task.image_path
            if task.already_preprocessed
            else preprocess_live_capture(task.image_path)
        )
        print(
            "[OCR STEP] after preprocessing "
            f"elapsed={time.perf_counter() - step_started:.2f}s "
            f"path={preprocessed_path}",
            flush=True,
        )
        input_image = cv2.imread(str(preprocessed_path), cv2.IMREAD_UNCHANGED)
        if input_image is None:
            raise FileNotFoundError(f"OCR 입력 이미지를 읽을 수 없습니다: {preprocessed_path}")
        channels = input_image.shape[2] if input_image.ndim == 3 else 1
        print(
            "[OCR STEP] image received "
            f"shape={input_image.shape} dtype={input_image.dtype} "
            f"channels={channels} contiguous={input_image.flags.c_contiguous}",
            flush=True,
        )
        step_started = time.perf_counter()
        print("[OCR STEP] before capture_image()", flush=True)
        if self._reader is None:
            raise RuntimeError("PaddleOCR reader가 worker에서 초기화되지 않았습니다.")
        capture = capture_image(
            self._reader, f"live_{task.capture_id}", preprocessed_path
        )
        print(
            f"[OCR STEP] after capture_image() elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        step_started = time.perf_counter()
        print("[OCR STEP] before stock_code validation", flush=True)
        if not capture.stock.stock_code:
            raise ValueError("stock_code_not_found")
        if not capture_has_required_content(capture):
            raise ValueError("tooltip_content_not_found")
        print(
            "[OCR STEP] after stock_code validation "
            f"elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )

        symbol = capture.stock.stock_code
        self._registry.update_ocr_values(
            symbol, stock_name=capture.stock.stock_name or symbol
        )
        step_started = time.perf_counter()
        print("[API STEP] before Toss refresh", flush=True)
        refresh = self._registry.refresh_current_prices()
        print(
            f"[API STEP] after Toss refresh elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        stock = self._registry.get(symbol)
        current_price: Decimal | None = None
        if stock is not None and stock.price_status == "valid":
            current_price = stock.current_price
        step_started = time.perf_counter()
        print("[ANALYZE STEP] before analyze_capture", flush=True)
        analysis = analyze_capture(self._reader, capture, current_price)
        print(
            "[ANALYZE STEP] after analyze_capture "
            f"elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        step_started = time.perf_counter()
        print("[REGISTRY STEP] before merge", flush=True)
        stock = self._registry.merge_analysis_result(analysis)
        print(
            f"[REGISTRY STEP] after merge elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        print(
            f"[OCR] complete elapsed={time.perf_counter() - total_started:.2f}s",
            flush=True,
        )
        print(
            f"[REGISTRY] merged {stock.stock_code} {analysis.chart_type}",
            flush=True,
        )
        self._print_summary(task, analysis.chart_type, stock, refresh.error)
        if self._on_complete is not None:
            self._on_complete(stock)

    @staticmethod
    def _print_summary(
        task: CaptureTask,
        chart_type: str,
        stock: StockRecord,
        refresh_error: str | None,
    ) -> None:
        print("\n================ CAPTURE ================")
        print(f"capture_id: {task.capture_id}")
        print(f"mouse_position: {task.mouse_position or 'test_image'}")
        print(f"stock_code: {stock.stock_code}")
        print(f"stock_name: {stock.stock_name}")
        print(f"chart_type: {chart_type}")
        current_display = (
            stock.current_price
            if stock.price_status == "valid" and stock.current_price is not None
            else "unavailable"
        )
        print(f"current_price: {current_display}")
        print("source: toss_api")
        print("registry:")
        print(f"  daily_loaded: {str(stock.daily_loaded).lower()}")
        print(f"  minute_loaded: {str(stock.minute_loaded).lower()}")
        print(f"  buy_price: {stock.buy_price if stock.buy_price is not None else 'null'}")
        print(f"  rebound_price: {stock.rebound_price if stock.rebound_price is not None else 'null'}")
        print(f"  taecho: {stock.taecho if stock.taecho is not None else 'null'}")
        print(f"  minute_walls_count: {len(stock.minute_walls)}")
        if refresh_error:
            print(f"api_error: {refresh_error}")
        print("status: success\n")


class GlobalF8Hotkey:
    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback
        self._thread = threading.Thread(
            target=self._message_loop, name="global-capture-hotkeys", daemon=False
        )
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._thread_id: int | None = None
        self._registered_ids: list[int] = []

    @property
    def registered_keys(self) -> tuple[str, ...]:
        return tuple(HOTKEYS[hotkey_id][0] for hotkey_id in self._registered_ids)

    def start(self) -> None:
        if os.name != "nt":
            raise RuntimeError("글로벌 캡처 핫키는 Windows에서만 지원합니다.")
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join()
            self._thread_id = None

    def _message_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.RegisterHotKey.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.c_int,
            ctypes.wintypes.UINT,
            ctypes.wintypes.UINT,
        )
        user32.RegisterHotKey.restype = ctypes.wintypes.BOOL
        user32.UnregisterHotKey.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        user32.UnregisterHotKey.restype = ctypes.wintypes.BOOL
        user32.GetMessageW.argtypes = (
            ctypes.POINTER(ctypes.wintypes.MSG),
            ctypes.wintypes.HWND,
            ctypes.wintypes.UINT,
            ctypes.wintypes.UINT,
        )
        user32.GetMessageW.restype = ctypes.wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
        self._thread_id = kernel32.GetCurrentThreadId()
        print(f"[HOTKEY] thread started id={self._thread_id}", flush=True)
        for hotkey_id, (key, virtual_key) in HOTKEYS.items():
            ctypes.set_last_error(0)
            if user32.RegisterHotKey(
                None, hotkey_id, MOD_NOREPEAT, virtual_key
            ):
                self._registered_ids.append(hotkey_id)
                print(f"[HOTKEY] {key} registered", flush=True)
                continue
            error_code = ctypes.get_last_error()
            error_message = ctypes.FormatError(error_code).strip()
            print(f"[HOTKEY ERROR] {key} registration failed", flush=True)
            print(f"winerror: {error_code}", flush=True)
            print(f"message: {error_message}", flush=True)

        if not self._registered_ids:
            self._error = RuntimeError("글로벌 캡처 핫키를 하나도 등록하지 못했습니다.")
            self._ready.set()
            return
        print("[HOTKEY] waiting for WM_HOTKEY", flush=True)
        self._ready.set()
        message = ctypes.wintypes.MSG()
        try:
            while True:
                get_message_result = user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0
                )
                if get_message_result == 0:
                    break
                if get_message_result == -1:
                    error_code = ctypes.get_last_error()
                    print(
                        "[HOTKEY ERROR] GetMessageW failed "
                        f"winerror={error_code} message={ctypes.FormatError(error_code).strip()}"
                    )
                    break
                hotkey_id = int(message.wParam)
                if message.message == WM_HOTKEY and hotkey_id in HOTKEYS:
                    key = HOTKEYS[hotkey_id][0]
                    print(f"[HOTKEY] {key} received", flush=True)
                    try:
                        self._callback(key)
                    except Exception as exc:
                        print(
                            f"[CAPTURE FAILED]\nreason: capture_failed: {exc}",
                            flush=True,
                        )
                else:
                    user32.TranslateMessage(ctypes.byref(message))
                    user32.DispatchMessageW(ctypes.byref(message))
        finally:
            for hotkey_id in self._registered_ids:
                key = HOTKEYS[hotkey_id][0]
                ctypes.set_last_error(0)
                if user32.UnregisterHotKey(None, hotkey_id):
                    print(f"[HOTKEY] {key} unregistered", flush=True)
                    continue
                error_code = ctypes.get_last_error()
                print(f"[HOTKEY ERROR] {key} unregister failed", flush=True)
                print(f"winerror: {error_code}", flush=True)
                print(
                    f"message: {ctypes.FormatError(error_code).strip()}",
                    flush=True,
                )
            self._registered_ids.clear()
