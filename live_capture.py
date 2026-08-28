from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import queue
import re
import threading
import time
import traceback
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image, ImageGrab

from capture_history import CaptureHistoryItem
from paddle_ocr_validation import (
    OcrCapture,
    OcrPerformanceMetrics,
    OCRToken,
    StockIdentity,
    StockInfo,
    analyze_capture,
    capture_image,
    primary_ocr,
    validate_tooltip_content,
)
from pending_captures import PendingCapture, PendingCaptureStore
from runtime_paths import APP_ROOT
from stock_models import StockRecord, StockRegistry
from toss_api import TossApiError
from tooltip_capture_config import (
    LIVE_CLAHE_CLIP_LIMIT,
    LIVE_CLAHE_TILE_GRID_SIZE,
    LIVE_OCR_SCALE,
    ROI_BOTTOM_OFFSET,
    ROI_LEFT_OFFSET,
    ROI_RIGHT_OFFSET,
    ROI_TOP_OFFSET,
    SAVE_LIVE_CAPTURE,
    TOOLTIP_EDGE_MARGIN_PX,
    TOOLTIP_EDGE_MIN_COLUMN_DIFF,
    TOOLTIP_EDGE_MIN_WIDTH_RATIO,
    TOOLTIP_KEEP_WIDTH_RATIO,
    TICKER_MOUSE_MAX_DISTANCE,
    TICKER_OCR_CONFIDENCE_THRESHOLD,
    TICKER_OCR_SCALE,
    TICKER_ROI_MARGIN_BOTTOM,
    TICKER_ROI_MARGIN_LEFT,
    TICKER_ROI_MARGIN_RIGHT,
    TICKER_ROI_MARGIN_TOP,
    TICKER_SEARCH_HEIGHT,
    TICKER_SEARCH_WIDTH,
)


PROJECT_ROOT = APP_ROOT
OUTPUT_DIRECTORY = PROJECT_ROOT / "ocr_results"
QUEUE_DIRECTORY = OUTPUT_DIRECTORY / "live_queue"
VK_OEM_3 = 0xC0
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
HOTKEY_ID_BACKTICK = 1
HOTKEYS = {
    HOTKEY_ID_BACKTICK: ("`", VK_OEM_3),
}

TICKER_PATTERN = re.compile(r"^[A-Z]{1,4}$")


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
    captured_at: str | None = None
    ticker_image_path: Path | None = None
    ticker_roi: tuple[int, int, int, int] | None = None
    raw_image: Image.Image | None = None
    ticker_image: Image.Image | None = None
    capture_elapsed_seconds: float = 0.0
    performance: OcrPerformanceMetrics = field(
        default_factory=OcrPerformanceMetrics,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True)
class TickerCalibrationTask:
    capture_id: str
    search_image_path: Path
    search_roi: tuple[int, int, int, int]
    mouse_position: tuple[int, int]
    monitor_bounds: tuple[int, int, int, int]


@dataclass(frozen=True)
class TickerCalibrationResult:
    success: bool
    ticker: str | None
    confidence: float
    ticker_roi: tuple[int, int, int, int] | None
    reason: str
    elapsed_seconds: float


@dataclass(frozen=True)
class TickerOcrResult:
    ticker: str | None
    raw_text: str
    confidence: float
    reason: str | None
    elapsed_seconds: float


@dataclass(frozen=True)
class TooltipCrop:
    image: Image.Image
    max_width: int
    detected_width: int | None
    final_width: int
    source: str
    edge_score: float


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


def calculate_ticker_search_box(
    mouse_position: tuple[int, int],
    monitor_bounds: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    mouse_x, mouse_y = mouse_position
    left_bound, top_bound, right_bound, bottom_bound = monitor_bounds
    width = min(TICKER_SEARCH_WIDTH, right_bound - left_bound)
    height = min(TICKER_SEARCH_HEIGHT, bottom_bound - top_bound)
    left = min(max(mouse_x - width // 2, left_bound), right_bound - width)
    top = min(max(mouse_y - height // 2, top_bound), bottom_bound - height)
    return left, top, left + width, top + height


def normalize_ticker_text(text: str) -> str:
    return text.strip().upper()


def validate_ticker_text(text: str) -> str | None:
    normalized = normalize_ticker_text(text)
    return normalized if TICKER_PATTERN.fullmatch(normalized) else None


def point_to_bbox_distance(point: tuple[float, float], bbox: tuple[int, int, int, int]) -> float:
    x, y = point
    x1, y1, x2, y2 = bbox
    dx = max(x1 - x, 0, x - x2)
    dy = max(y1 - y, 0, y - y2)
    return (dx * dx + dy * dy) ** 0.5


def select_calibration_ticker_token(
    tokens: list[OCRToken],
    mouse_in_search: tuple[int, int],
    scale: int = TICKER_OCR_SCALE,
) -> tuple[OCRToken, str] | None:
    scaled_mouse = (mouse_in_search[0] * scale, mouse_in_search[1] * scale)
    max_distance = TICKER_MOUSE_MAX_DISTANCE * scale
    candidates: list[tuple[float, float, OCRToken, str]] = []
    for token in tokens:
        ticker = validate_ticker_text(token.text)
        if ticker is None or token.confidence < TICKER_OCR_CONFIDENCE_THRESHOLD:
            continue
        distance = point_to_bbox_distance(scaled_mouse, token.bbox)
        if distance <= max_distance:
            candidates.append((distance, -token.confidence, token, ticker))
    if not candidates:
        return None
    _, _, token, ticker = min(candidates, key=lambda item: (item[0], item[1]))
    return token, ticker


def ticker_bbox_to_screen_roi(
    bbox: tuple[int, int, int, int],
    search_roi: tuple[int, int, int, int],
    monitor_bounds: tuple[int, int, int, int],
    scale: int = TICKER_OCR_SCALE,
) -> tuple[int, int, int, int]:
    sx1, sy1, _, _ = search_roi
    bx1, by1, bx2, by2 = bbox
    left = sx1 + int(bx1 / scale) - TICKER_ROI_MARGIN_LEFT
    top = sy1 + int(by1 / scale) - TICKER_ROI_MARGIN_TOP
    right = sx1 + int((bx2 + scale - 1) / scale) + TICKER_ROI_MARGIN_RIGHT
    bottom = sy1 + int((by2 + scale - 1) / scale) + TICKER_ROI_MARGIN_BOTTOM
    ml, mt, mr, mb = monitor_bounds
    return max(left, ml), max(top, mt), min(right, mr), min(bottom, mb)


def capture_mouse_relative_roi() -> tuple[Image.Image, tuple[int, int], tuple[int, int, int, int]]:
    mouse_position = get_mouse_position()
    bounds = monitor_bounds_for_point(*mouse_position)
    capture_box = calculate_capture_box(mouse_position, bounds)
    image = ImageGrab.grab(bbox=capture_box, all_screens=True)
    return image, mouse_position, capture_box


def capture_live_rois(
    ticker_roi: tuple[int, int, int, int],
) -> tuple[
    Image.Image,
    Image.Image,
    tuple[int, int],
    tuple[int, int, int, int],
    tuple[int, int, int, int],
]:
    """한 번의 화면 grab에서 Tooltip과 고정 ticker ROI를 동시에 확보한다."""
    mouse_position = get_mouse_position()
    monitor_bounds = monitor_bounds_for_point(*mouse_position)
    capture_box = calculate_capture_box(mouse_position, monitor_bounds)
    combined_box = (
        min(capture_box[0], ticker_roi[0]),
        min(capture_box[1], ticker_roi[1]),
        max(capture_box[2], ticker_roi[2]),
        max(capture_box[3], ticker_roi[3]),
    )
    combined_image = ImageGrab.grab(bbox=combined_box, all_screens=True)

    def local_box(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return (
            box[0] - combined_box[0],
            box[1] - combined_box[1],
            box[2] - combined_box[0],
            box[3] - combined_box[1],
        )

    raw_image = combined_image.crop(local_box(capture_box)).copy()
    ticker_image = combined_image.crop(local_box(ticker_roi)).copy()
    return raw_image, ticker_image, mouse_position, capture_box, combined_box


def detect_tooltip_crop(image: Image.Image) -> TooltipCrop:
    if not 0 < TOOLTIP_KEEP_WIDTH_RATIO <= 1:
        raise ValueError("TOOLTIP_KEEP_WIDTH_RATIO는 0보다 크고 1 이하여야 합니다.")
    if not 0 < TOOLTIP_EDGE_MIN_WIDTH_RATIO <= TOOLTIP_KEEP_WIDTH_RATIO:
        raise ValueError(
            "TOOLTIP_EDGE_MIN_WIDTH_RATIO는 0보다 크고 keep ratio 이하여야 합니다."
        )
    if TOOLTIP_EDGE_MIN_COLUMN_DIFF < 0 or TOOLTIP_EDGE_MARGIN_PX < 0:
        raise ValueError("Tooltip 경계 threshold와 margin은 0 이상이어야 합니다.")

    max_width = max(1, round(image.width * TOOLTIP_KEEP_WIDTH_RATIO))
    min_width = max(1, round(image.width * TOOLTIP_EDGE_MIN_WIDTH_RATIO))
    grayscale = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    column_difference = np.mean(
        np.abs(np.diff(grayscale.astype(np.int16), axis=1)), axis=0
    )
    search_start = min(min_width, max_width - 1)
    search_stop = max(search_start, max_width - TOOLTIP_EDGE_MARGIN_PX)
    detected_width: int | None = None
    edge_score = 0.0
    if search_stop > search_start:
        scores = column_difference[search_start:search_stop]
        relative_index = int(np.argmax(scores))
        edge_score = float(scores[relative_index])
        if edge_score >= TOOLTIP_EDGE_MIN_COLUMN_DIFF:
            edge_column = search_start + relative_index + 1
            detected_width = min(edge_column + TOOLTIP_EDGE_MARGIN_PX, max_width)

    final_width = detected_width or max_width
    source = (
        "detected_tooltip_edge"
        if detected_width is not None
        else "fallback_keep_ratio"
    )
    return TooltipCrop(
        image.crop((0, 0, final_width, image.height)),
        max_width,
        detected_width,
        final_width,
        source,
        edge_score,
    )


def trim_tooltip_image(image: Image.Image) -> Image.Image:
    """기존 호출자를 위한 최종 Tooltip crop 반환 경계."""
    return detect_tooltip_crop(image).image


def print_crop_diagnostics(raw_width: int, crop: TooltipCrop) -> None:
    detected = crop.detected_width if crop.detected_width is not None else "none"
    print("[LIVE CROP]", flush=True)
    print(f"raw_width: {raw_width}", flush=True)
    print(f"max_keep_ratio: {TOOLTIP_KEEP_WIDTH_RATIO:.2f}", flush=True)
    print(f"max_width: {crop.max_width}", flush=True)
    print(f"detected_width: {detected}", flush=True)
    print(f"final_width: {crop.final_width}", flush=True)
    print(f"edge_score: {crop.edge_score:.2f}", flush=True)
    print(f"crop_source: {crop.source}", flush=True)


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


def ticker_preprocessed_output_path(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.stem}_preprocessed.png")


def preprocess_ticker_image(image_path: Path) -> Path:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"ticker 이미지를 읽을 수 없습니다: {image_path}")
    upscaled = cv2.resize(
        image,
        None,
        fx=TICKER_OCR_SCALE,
        fy=TICKER_OCR_SCALE,
        interpolation=cv2.INTER_CUBIC,
    )
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    output = clahe.apply(upscaled)
    output_path = ticker_preprocessed_output_path(image_path)
    if not cv2.imwrite(str(output_path), output):
        raise OSError(f"ticker 전처리 이미지 저장 실패: {output_path}")
    return output_path


def read_ticker_crop(
    reader: Any,
    image_path: Path,
    performance: OcrPerformanceMetrics | None = None,
) -> TickerOcrResult:
    started = time.perf_counter()
    ticker_pre_started = time.perf_counter()
    try:
        preprocessed_path = preprocess_ticker_image(image_path)
    finally:
        if performance is not None:
            performance.ticker_pre_seconds += time.perf_counter() - ticker_pre_started
    ticker_ocr_started = time.perf_counter()
    try:
        tokens = primary_ocr(reader, preprocessed_path)
    finally:
        if performance is not None:
            performance.ticker_ocr_seconds += time.perf_counter() - ticker_ocr_started
    valid = [
        (validate_ticker_text(token.text), token)
        for token in tokens
        if validate_ticker_text(token.text) is not None
        and token.confidence >= TICKER_OCR_CONFIDENCE_THRESHOLD
    ]
    distinct = {ticker for ticker, _token in valid if ticker is not None}
    if len(distinct) != 1:
        raw_text = " | ".join(token.text for token in tokens)
        reason = "ticker_not_found" if not distinct else "ambiguous_ticker_tokens"
        return TickerOcrResult(
            None,
            raw_text,
            max((token.confidence for _ticker, token in valid), default=0.0),
            reason,
            time.perf_counter() - started,
        )
    ticker = next(iter(distinct))
    matching = [token for candidate, token in valid if candidate == ticker]
    best = max(matching, key=lambda token: token.confidence)
    return TickerOcrResult(
        ticker,
        best.text,
        best.confidence,
        None,
        time.perf_counter() - started,
    )


def capture_has_required_content(capture: OcrCapture) -> bool:
    text = "\n".join(line.text for line in capture.lines)
    return validate_tooltip_content(text).valid


def print_tooltip_validation(capture: OcrCapture) -> bool:
    text = "\n".join(line.text for line in capture.lines)
    result = validate_tooltip_content(text)
    print("[TOOLTIP VALIDATION]", flush=True)
    print(
        "minute_evidence: " + (", ".join(result.minute_evidence) or "0"),
        flush=True,
    )
    print(
        "daily_evidence: " + (", ".join(result.daily_evidence) or "0"),
        flush=True,
    )
    print(f"status: {'valid' if result.valid else 'invalid'}", flush=True)
    if result.valid:
        print(f"chart_type: {result.chart_type}", flush=True)
        capture.chart_type = result.chart_type or capture.chart_type
    else:
        print(f"reason: {result.reason}", flush=True)
    return result.valid


class CaptureProcessor:
    def __init__(
        self,
        reader_factory: Callable[[], Any],
        registry: StockRegistry,
        on_complete: Callable[[StockRecord], None] | None = None,
        on_pending: Callable[[PendingCapture], None] | None = None,
        on_calibration: Callable[[TickerCalibrationResult], None] | None = None,
        on_history: Callable[[CaptureHistoryItem], None] | None = None,
        pending_store: PendingCaptureStore | None = None,
    ) -> None:
        self._reader_factory = reader_factory
        self._reader: Any | None = None
        self._registry = registry
        self._on_complete = on_complete
        self._on_pending = on_pending
        self._on_calibration = on_calibration
        self._on_history = on_history
        self.pending_store = pending_store or PendingCaptureStore()
        self._history_drafts: dict[str, CaptureHistoryItem] = {}
        self._queue: queue.Queue[CaptureTask | TickerCalibrationTask | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="tooltip-ocr-worker", daemon=True
        )
        self._started = False
        self._shutdown = threading.Event()
        self._state_lock = threading.Lock()
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

    def enqueue_ticker_calibration(self) -> TickerCalibrationTask:
        if self._shutdown.is_set():
            raise RuntimeError("capture_processor_shutting_down")
        mouse_position = get_mouse_position()
        monitor_bounds = monitor_bounds_for_point(*mouse_position)
        search_roi = calculate_ticker_search_box(mouse_position, monitor_bounds)
        image = ImageGrab.grab(bbox=search_roi, all_screens=True)
        capture_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        search_path = OUTPUT_DIRECTORY / f"ticker_calibration_search_{capture_id}.png"
        image.save(search_path, format="PNG")
        task = TickerCalibrationTask(
            capture_id,
            search_path,
            search_roi,
            mouse_position,
            monitor_bounds,
        )
        self._queue.put(task)
        print("[TICKER CALIBRATION]", flush=True)
        print(f"mouse: {mouse_position}", flush=True)
        print(f"search_roi: {search_roi}", flush=True)
        print(f"[QUEUE] pending={self._queue.qsize()}", flush=True)
        return task

    def enqueue_live_capture(
        self, ticker_roi: tuple[int, int, int, int]
    ) -> CaptureTask:
        if self._shutdown.is_set():
            raise RuntimeError("capture_processor_shutting_down")
        capture_started = time.perf_counter()
        capture_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        image, ticker_image, mouse_position, capture_box, combined_box = (
            capture_live_rois(ticker_roi)
        )
        captured_at = datetime.now().astimezone().isoformat()
        capture_elapsed = time.perf_counter() - capture_started
        print("[ROI]", flush=True)
        print(f"mouse_x: {mouse_position[0]}", flush=True)
        print(f"left_offset: {ROI_LEFT_OFFSET}", flush=True)
        print(f"right_offset: {ROI_RIGHT_OFFSET}", flush=True)
        print(f"raw_x1: {capture_box[0]}", flush=True)
        print(f"raw_x2: {capture_box[2]}", flush=True)
        print(f"raw_width: {capture_box[2] - capture_box[0]}", flush=True)
        directory = OUTPUT_DIRECTORY if SAVE_LIVE_CAPTURE else QUEUE_DIRECTORY
        directory.mkdir(parents=True, exist_ok=True)
        base_name = f"live_capture_{capture_id}"
        raw_path = directory / f"{base_name}_raw.png"
        tooltip_path = directory / f"{base_name}_tooltip.png"
        ticker_path = directory / f"{base_name}_ticker.png"
        task = CaptureTask(
            capture_id,
            tooltip_path,
            mouse_position,
            capture_box,
            preserve_files=SAVE_LIVE_CAPTURE,
            captured_at=captured_at,
            ticker_image_path=ticker_path,
            ticker_roi=ticker_roi,
            raw_image=image,
            ticker_image=ticker_image,
            capture_elapsed_seconds=capture_elapsed,
        )
        self._queue.put(task)
        print(
            "[CAPTURE]\n"
            f"capture_id: {capture_id}\n"
            f"ticker_roi: {ticker_roi}\n"
            f"tooltip_roi: {capture_box}\n"
            f"combined_roi: {combined_box}", flush=True,
        )
        print(
            f"[CAPTURE TIMING] screen_grab_and_crop={capture_elapsed:.3f}s "
            "queued_in_memory=true",
            flush=True,
        )
        print(f"[QUEUE] pending={self._queue.qsize()}", flush=True)
        return task

    @staticmethod
    def _materialize_live_capture(task: CaptureTask) -> None:
        """hotkey 순간의 메모리 이미지를 worker에서 crop하고 디버그 파일로 저장한다."""
        if task.raw_image is None:
            return
        if task.ticker_image is None or task.ticker_image_path is None:
            raise RuntimeError("in_memory_ticker_image_not_available")

        started = time.perf_counter()
        crop = detect_tooltip_crop(task.raw_image)
        tooltip_image = crop.image
        image_array = np.asarray(task.raw_image)
        channels = image_array.shape[2] if image_array.ndim == 3 else 1
        raw_path = task.image_path.with_name(
            task.image_path.name.replace("_tooltip.png", "_raw.png")
        )
        if task.preserve_files:
            task.raw_image.save(raw_path, format="PNG")
        tooltip_image.save(task.image_path, format="PNG")
        task.ticker_image.save(task.ticker_image_path, format="PNG")

        print_crop_diagnostics(task.raw_image.width, crop)
        print("[LIVE IMAGE]", flush=True)
        print(
            f"type: {type(task.raw_image).__module__}.{type(task.raw_image).__name__}",
            flush=True,
        )
        print(
            f"raw_size: {task.raw_image.width}x{task.raw_image.height}", flush=True
        )
        print(
            f"tooltip_size: {tooltip_image.width}x{tooltip_image.height}",
            flush=True,
        )
        print(f"keep_width_ratio: {TOOLTIP_KEEP_WIDTH_RATIO:.2f}", flush=True)
        print(f"numpy_shape: {image_array.shape}", flush=True)
        print(f"dtype: {image_array.dtype}", flush=True)
        print(f"channels: {channels}", flush=True)
        print(f"contiguous: {image_array.flags.c_contiguous}", flush=True)
        print(
            f"[CAPTURE MATERIALIZE] elapsed={time.perf_counter() - started:.3f}s",
            flush=True,
        )

    @staticmethod
    def _raw_image_path(task: CaptureTask) -> Path:
        return task.image_path.with_name(
            task.image_path.name.replace("_tooltip.png", "_raw.png")
        )

    def _begin_capture_history(self, task: CaptureTask) -> None:
        raw_path = self._raw_image_path(task)
        if not raw_path.is_file():
            return
        self._history_drafts[task.capture_id] = CaptureHistoryItem(
            capture_id=task.capture_id,
            raw_image_path=raw_path,
            captured_at=task.captured_at,
        )

    def _update_capture_history(self, capture_id: str, **changes: Any) -> None:
        item = self._history_drafts.get(capture_id)
        if item is not None:
            self._history_drafts[capture_id] = replace(item, **changes)

    def _finish_capture_history(self, task: CaptureTask, **changes: Any) -> None:
        item = self._history_drafts.pop(task.capture_id, None)
        if item is None:
            raw_path = self._raw_image_path(task)
            if not raw_path.is_file():
                return
            item = CaptureHistoryItem(
                task.capture_id, raw_path, task.captured_at
            )
        item = replace(item, **changes)
        if self._on_history is not None and not self._shutdown.is_set():
            self._on_history(item)

    @staticmethod
    def delete_capture_files(capture_id: str) -> tuple[Path, ...]:
        """캡처 ID가 포함된 보존 파일만 명시된 결과 폴더 안에서 삭제한다."""
        deleted: list[Path] = []
        for root in (OUTPUT_DIRECTORY, QUEUE_DIRECTORY):
            if not root.is_dir():
                continue
            resolved_root = root.resolve()
            for path in root.rglob(f"*{capture_id}*"):
                resolved = path.resolve()
                if not resolved.is_file() or resolved_root not in resolved.parents:
                    continue
                resolved.unlink(missing_ok=True)
                deleted.append(resolved)
        return tuple(deleted)

    def enqueue_preprocessed_image(self, image_path: Path, name: str) -> CaptureTask:
        """저장 이미지로 worker/merge 흐름만 확인하기 위한 테스트 경계."""
        if self._shutdown.is_set():
            raise RuntimeError("capture_processor_shutting_down")
        task = CaptureTask(name, image_path, None, None, True, True)
        self._queue.put(task)
        print(f"[QUEUE] test image={image_path.name} pending={self._queue.qsize()}")
        return task

    def enqueue_saved_capture(self, image_path: Path) -> CaptureTask:
        """저장된 live 원본 PNG를 live와 동일한 파일 전처리 경로로 재실행한다."""
        if self._shutdown.is_set():
            raise RuntimeError("capture_processor_shutting_down")
        if image_path.stem.endswith("_tooltip"):
            tooltip_path = image_path
        else:
            with Image.open(image_path) as raw_image:
                raw_size = raw_image.size
                crop = detect_tooltip_crop(raw_image.convert("RGB"))
                tooltip_image = crop.image
                base_stem = image_path.stem.removesuffix("_raw")
                tooltip_path = image_path.with_name(f"{base_stem}_tooltip.png")
                tooltip_image.save(tooltip_path, format="PNG")
            print_crop_diagnostics(raw_size[0], crop)
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
            with self._state_lock:
                self._shutdown.set()
            self._queue.put(None)
            self._thread.join()
        else:
            with self._state_lock:
                self._shutdown.set()
            discarded = self._discard_pending_tasks()
            self._queue.put(None)
            self._thread.join(timeout=0.2)
            print(f"[SHUTDOWN] pending discarded={discarded}", flush=True)
            if self._thread.is_alive():
                print(
                    "[SHUTDOWN] OCR is still inside Paddle predict; "
                    "result and registry merge will be ignored",
                    flush=True,
                )
        self._started = False

    def _discard_pending_tasks(self) -> int:
        discarded = 0
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                return discarded
            try:
                if task is not None:
                    discarded += 1
                    self._cleanup_task_files(task)
            finally:
                self._queue.task_done()

    @staticmethod
    def _cleanup_task_files(task: CaptureTask | TickerCalibrationTask) -> None:
        if isinstance(task, TickerCalibrationTask):
            ticker_preprocessed_output_path(task.search_image_path).unlink(missing_ok=True)
            return
        if task.preserve_files or task.already_preprocessed:
            return
        task.image_path.unlink(missing_ok=True)
        preprocessed_output_path(task.image_path).unlink(missing_ok=True)
        if task.ticker_image_path is not None:
            task.ticker_image_path.unlink(missing_ok=True)
            ticker_preprocessed_output_path(task.ticker_image_path).unlink(missing_ok=True)

    def _stop_requested(self, task: CaptureTask | TickerCalibrationTask, stage: str) -> bool:
        if not self._shutdown.is_set():
            return False
        print(
            f"[SHUTDOWN] discarded capture_id={task.capture_id} stage={stage}",
            flush=True,
        )
        return True

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
            traceback.print_exc()
        finally:
            self._reader_ready.set()
        if self._startup_error is not None:
            return

        while True:
            task = self._queue.get()
            processing_started = time.perf_counter()
            try:
                if task is None:
                    return
                if isinstance(task, TickerCalibrationTask):
                    self._process_ticker_calibration(task)
                else:
                    self._process(task)
            except Exception as exc:  # 한 캡처 실패가 worker를 종료하면 안 된다.
                print("[CAPTURE FAILED]", flush=True)
                print(
                    f"capture_id: {task.capture_id if task else 'unknown'}",
                    flush=True,
                )
                print(f"reason: {type(exc).__name__}: {exc}", flush=True)
                if isinstance(task, CaptureTask):
                    self._finish_capture_history(
                        task, error=f"{type(exc).__name__}: {exc}"
                    )
                if (
                    isinstance(task, TickerCalibrationTask)
                    and self._on_calibration is not None
                    and not self._shutdown.is_set()
                ):
                    self._on_calibration(
                        TickerCalibrationResult(
                            False,
                            None,
                            0.0,
                            None,
                            f"calibration_error: {type(exc).__name__}: {exc}",
                            0.0,
                        )
                    )
            finally:
                if task is not None:
                    if isinstance(task, CaptureTask):
                        self._print_performance(task, processing_started)
                    self._cleanup_task_files(task)
                self._queue.task_done()

    def _process_ticker_calibration(self, task: TickerCalibrationTask) -> None:
        started = time.perf_counter()
        if self._reader is None:
            raise RuntimeError("PaddleOCR reader가 worker에서 초기화되지 않았습니다.")
        preprocessed_path = preprocess_ticker_image(task.search_image_path)
        tokens = primary_ocr(self._reader, preprocessed_path)
        mouse_in_search = (
            task.mouse_position[0] - task.search_roi[0],
            task.mouse_position[1] - task.search_roi[1],
        )
        selected = select_calibration_ticker_token(tokens, mouse_in_search)
        if selected is None:
            result = TickerCalibrationResult(
                False,
                None,
                0.0,
                None,
                "ticker_not_found",
                time.perf_counter() - started,
            )
            print("detected_text: null", flush=True)
            print("status: failed", flush=True)
            print("reason: ticker_not_found", flush=True)
        else:
            token, ticker = selected
            ticker_roi = ticker_bbox_to_screen_roi(
                token.bbox,
                task.search_roi,
                task.monitor_bounds,
            )
            final_path = OUTPUT_DIRECTORY / f"ticker_calibration_final_{task.capture_id}.png"
            with Image.open(task.search_image_path) as search_image:
                local_box = (
                    ticker_roi[0] - task.search_roi[0],
                    ticker_roi[1] - task.search_roi[1],
                    ticker_roi[2] - task.search_roi[0],
                    ticker_roi[3] - task.search_roi[1],
                )
                search_image.crop(local_box).save(final_path, format="PNG")
            result = TickerCalibrationResult(
                True,
                ticker,
                token.confidence,
                ticker_roi,
                "success",
                time.perf_counter() - started,
            )
            print(f"detected_text: {token.text}", flush=True)
            print(f"detected_bbox: {token.bbox}", flush=True)
            print(f"screen_bbox: {ticker_roi}", flush=True)
            print(
                "margin: "
                f"left={TICKER_ROI_MARGIN_LEFT},right={TICKER_ROI_MARGIN_RIGHT},"
                f"top={TICKER_ROI_MARGIN_TOP},bottom={TICKER_ROI_MARGIN_BOTTOM}",
                flush=True,
            )
            print(f"final_ticker_roi: {ticker_roi}", flush=True)
            print("status: success", flush=True)
        print(f"elapsed: {result.elapsed_seconds:.2f}s", flush=True)
        if self._on_calibration is not None and not self._shutdown.is_set():
            self._on_calibration(result)

    def _process(self, task: CaptureTask) -> None:
        if self._stop_requested(task, "before_processing"):
            return
        total_started = time.perf_counter()
        print(f"[OCR STEP] start capture_id={task.capture_id}", flush=True)
        if self._reader is None:
            raise RuntimeError("PaddleOCR reader가 worker에서 초기화되지 않았습니다.")
        self._materialize_live_capture(task)
        self._begin_capture_history(task)
        if self._stop_requested(task, "after_capture_materialize"):
            return
        if task.ticker_image_path is None:
            ticker_result = TickerOcrResult(
                None, "", 0.0, "ticker_image_not_available", 0.0
            )
        else:
            ticker_result = read_ticker_crop(
                self._reader,
                task.ticker_image_path,
                task.performance,
            )
        print("[TICKER OCR]", flush=True)
        print(f"raw_text: {ticker_result.raw_text or 'null'}", flush=True)
        print(f"ticker: {ticker_result.ticker or 'null'}", flush=True)
        print(f"confidence: {ticker_result.confidence:.3f}", flush=True)
        print(f"elapsed: {ticker_result.elapsed_seconds:.2f}s", flush=True)
        self._update_capture_history(
            task.capture_id,
            parsed_ticker=ticker_result.ticker,
            raw_ticker_text=ticker_result.raw_text,
        )
        if ticker_result.reason:
            print(f"reason: {ticker_result.reason}", flush=True)
        step_started = time.perf_counter()
        print(
            "[OCR STEP] before preprocessing "
            f"already_preprocessed={task.already_preprocessed}",
            flush=True,
        )
        tooltip_pre_started = time.perf_counter()
        try:
            preprocessed_path = (
                task.image_path
                if task.already_preprocessed
                else preprocess_live_capture(task.image_path)
            )
        finally:
            task.performance.tooltip_pre_seconds += (
                time.perf_counter() - tooltip_pre_started
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
        capture = capture_image(
            self._reader,
            f"live_{task.capture_id}",
            preprocessed_path,
            task.performance,
        )
        print(
            f"[OCR STEP] after capture_image() elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        if self._stop_requested(task, "after_primary_ocr"):
            return
        step_started = time.perf_counter()
        print("[OCR STEP] before tooltip/identity validation", flush=True)
        if not print_tooltip_validation(capture):
            raise ValueError("tooltip_content_not_found")
        identity = capture.identity or StockIdentity("", None, None, None, 0.0)
        print("[IDENTITY]", flush=True)
        print(f"capture_id: {task.capture_id}", flush=True)
        print(f"company_name: {identity.english_name or identity.korean_name or 'null'}", flush=True)
        print(f"korean_name: {identity.korean_name or 'null'}", flush=True)
        print(f"ticker_hint: {identity.ticker_hint or 'null'}", flush=True)
        print(f"confidence: {identity.confidence:.3f}", flush=True)

        print("[CHART]", flush=True)
        print(f"capture_id: {task.capture_id}", flush=True)
        print(f"ticker: {ticker_result.ticker or 'unresolved'}", flush=True)
        print(f"chart_type: {capture.chart_type}", flush=True)
        print(
            "[OCR STEP] after tooltip/identity validation "
            f"elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )

        symbol = ticker_result.ticker
        current_price: Decimal | None = None
        price_error: str | None = None
        if symbol is not None:
            existing = self._registry.get_snapshot(symbol)
            try:
                quote = self._registry.api_client.get_current_price(symbol)
                current_price = quote.last_price
            except TossApiError as exc:
                price_error = str(exc)
                if existing is not None and existing.current_price is not None:
                    current_price = existing.current_price

        step_started = time.perf_counter()
        print("[ANALYZE STEP] before analyze_capture", flush=True)
        analysis = analyze_capture(
            self._reader,
            capture,
            current_price,
            task.performance,
        )
        print(
            "[ANALYZE STEP] after analyze_capture "
            f"elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        if self._stop_requested(task, "after_analysis"):
            return
        if symbol is None or current_price is None:
            reason = (
                ticker_result.reason or "ticker_ocr_unavailable"
                if symbol is None
                else f"current_price_unavailable: {price_error or 'no_price'}"
            )
            self._store_pending(task, capture, analysis, reason)
            self._finish_capture_history(
                task,
                chart_type=capture.chart_type,
                error=reason,
            )
            print(
                f"[OCR] pending elapsed={time.perf_counter() - total_started:.2f}s",
                flush=True,
            )
            return

        stock_name = (
            identity.korean_name
            or identity.english_name
            or symbol
        )
        analysis.stock = StockInfo(symbol, stock_name)
        step_started = time.perf_counter()
        print("[REGISTRY STEP] before merge", flush=True)
        with self._state_lock:
            if self._shutdown.is_set():
                print(
                    f"[SHUTDOWN] discarded capture_id={task.capture_id} "
                    "stage=before_registry_merge",
                    flush=True,
                )
                return
            stock = self._registry.merge_analysis_result(
                analysis, capture_id=task.capture_id
            )
            if price_error:
                self._registry.mark_price_stale(symbol, price_error)
        print(
            f"[REGISTRY STEP] after merge elapsed={time.perf_counter() - step_started:.2f}s",
            flush=True,
        )
        print(
            f"[OCR] complete elapsed={time.perf_counter() - total_started:.2f}s",
            flush=True,
        )
        self._print_summary(task, analysis.chart_type, stock, price_error)
        self._finish_capture_history(
            task,
            chart_type=analysis.chart_type,
            registry_symbol=symbol,
            error=price_error,
        )
        if self._on_complete is not None and not self._shutdown.is_set():
            self._on_complete(stock)

    @staticmethod
    def _print_performance(task: CaptureTask, processing_started: float) -> None:
        metrics = task.performance
        total_seconds = task.capture_elapsed_seconds + (
            time.perf_counter() - processing_started
        )
        print(
            "[PERF] "
            f"capture={task.capture_elapsed_seconds:.3f}s "
            f"ticker_pre={metrics.ticker_pre_seconds:.3f}s "
            f"ticker_ocr={metrics.ticker_ocr_seconds:.3f}s "
            f"tooltip_pre={metrics.tooltip_pre_seconds:.3f}s "
            f"primary_ocr={metrics.primary_ocr_seconds:.3f}s "
            f"numeric_retry={metrics.numeric_retry_ocr_seconds:.3f}s "
            f"retry_count={metrics.numeric_retry_count} "
            f"total={total_seconds:.3f}s",
            flush=True,
        )

    def _store_pending(
        self,
        task: CaptureTask,
        capture: OcrCapture,
        analysis: Any,
        reason: str,
    ) -> None:
        pending = PendingCapture(
            capture_id=task.capture_id,
            identity=capture.identity or StockIdentity("", None, None, None, 0.0),
            chart_type=capture.chart_type,
            analysis=analysis,
            resolver_reason=reason,
        )
        self.pending_store.add(pending)
        print("[PENDING CAPTURE]", flush=True)
        print(f"capture_id: {task.capture_id}", flush=True)
        print(f"chart_type: {capture.chart_type}", flush=True)
        print(f"reason: {reason}", flush=True)
        if self._on_pending is not None and not self._shutdown.is_set():
            self._on_pending(pending)

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
            if stock.price_status in {"valid", "stale"}
            and stock.current_price is not None
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


class GlobalCaptureHotkey:
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


# 이전 테스트/호출 코드와의 import 호환성만 유지한다. 실제 등록 키는 백틱 하나다.
GlobalF8Hotkey = GlobalCaptureHotkey
