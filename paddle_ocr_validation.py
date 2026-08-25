from __future__ import annotations

import os
import json
import re
import threading
import time
import warnings
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import cv2

from paddle_validation_config import (
    DAILY_VALUE_X_MAX,
    DAILY_VALUE_X_MIN,
    DAILY_VALUE_Y_MAX,
    DAILY_VALUE_Y_MIN,
    LINE_GROUP_MAX_HORIZONTAL_GAP,
    LINE_GROUP_Y_TOLERANCE,
    MISSING_NUMERIC_REGION_WIDTH,
    NUMERIC_CROP_PADDING_X,
    NUMERIC_CROP_PADDING_Y,
    NUMERIC_CROP_SCALE,
    OCR_CONFIDENCE_THRESHOLD,
    PRICE_MAX_MULTIPLIER,
    MINUTE_VALUE_X_MAX,
    MINUTE_VALUE_X_MIN,
    MINUTE_VALUE_Y_MAX,
    MINUTE_VALUE_Y_MIN,
    ROW_CENTER_Y_TOLERANCE,
    STOCK_HEADER_Y_MAX,
)


PROJECT_ROOT = Path(__file__).resolve().parent
PADDLE_CACHE = PROJECT_ROOT / ".paddle-cache"
PADDLE_HOME = PADDLE_CACHE / "home"
PADDLE_HOME.mkdir(parents=True, exist_ok=True)
os.environ["USERPROFILE"] = str(PADDLE_HOME)
os.environ["PADDLE_PDX_CACHE_HOME"] = str(PADDLE_CACHE)
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

from paddleocr import PaddleOCR  # noqa: E402 - 캐시 환경변수 설정 후 import


INPUTS = {
    "daily": PROJECT_ROOT / "ocr_crops" / "1_daily_crop_preprocessed.png",
    "minute": PROJECT_ROOT / "ocr_crops" / "2_minute_crop_preprocessed.png",
}
RETRY_DIRECTORY = PROJECT_ROOT / "ocr_crops" / "numeric_retry"

MINUTE_KEYWORDS = ("시체소굴", "절대값half", "절대값 half")
DAILY_SECTIONS = {
    "가격 이동평균",
    "day20",
    "day33",
    "day60",
    "day112",
    "day224",
    "day335",
}
SECTION_PATTERN = re.compile(r"\[\s*([^\]]+?)\s*\]")
NUMBER_PATTERN = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w.])"
)


BBox = tuple[int, int, int, int]


@dataclass(frozen=True)
class OCRToken:
    text: str
    confidence: float
    bbox: BBox


@dataclass
class VisualLine:
    tokens: list[OCRToken] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(token.text for token in sorted(self.tokens, key=lambda token: token.bbox[0]))

    @property
    def bbox(self) -> BBox:
        return union_boxes([token.bbox for token in self.tokens])


@dataclass
class PriceResult:
    key: str
    item_text: str
    item_bbox: BBox
    price_bbox: BBox
    raw_text: str
    value: Decimal | None
    confidence: float
    status: str
    source: str = "primary_ocr"
    reasons: list[str] = field(default_factory=list)
    raw_value: Decimal | None = None

    @property
    def label_text(self) -> str:
        return self.item_text

    @property
    def label_bbox(self) -> BBox:
        return self.item_bbox


@dataclass(frozen=True)
class StockInfo:
    stock_code: str | None
    stock_name: str | None

    @property
    def display_name(self) -> str | None:
        return self.stock_name[:6] if self.stock_name else None


@dataclass(frozen=True)
class StockIdentity:
    raw_identity_text: str
    english_name: str | None
    korean_name: str | None
    ticker_hint: str | None
    confidence: float


@dataclass
class OcrCapture:
    name: str
    image: Any
    tokens: list[OCRToken]
    lines: list[VisualLine]
    chart_type: str
    stock: StockInfo
    identity: StockIdentity | None = None


@dataclass
class OcrAnalysis:
    chart_type: str
    stock: StockInfo
    current: PriceResult
    items: list[PriceResult]


@dataclass(frozen=True)
class TooltipValidation:
    valid: bool
    chart_type: str | None
    minute_evidence: tuple[str, ...]
    daily_evidence: tuple[str, ...]
    reason: str | None = None


def create_reader() -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return PaddleOCR(
            lang="korean",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
            ocr_version="PP-OCRv5",
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )


def primary_ocr(reader: Any, image_path: Path) -> list[OCRToken]:
    """전체 Tooltip 전처리 이미지는 여기서 정확히 한 번만 OCR한다."""
    tokens: list[OCRToken] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        started = time.perf_counter()
        print(
            f"[PADDLE] predict start thread={threading.current_thread().name}",
            flush=True,
        )
        for result in reader.predict(str(image_path)):
            texts = result["rec_texts"]
            scores = result["rec_scores"]
            boxes = result.get("rec_boxes")
            if boxes is None:
                boxes = result["rec_polys"]
            for text, score, box in zip(texts, scores, boxes):
                tokens.append(
                    OCRToken(
                        text=str(text),
                        confidence=float(score),
                        bbox=box_to_xyxy(box),
                    )
                )
        print(
            f"[PADDLE] predict complete elapsed={time.perf_counter() - started:.2f}s "
            f"thread={threading.current_thread().name}",
            flush=True,
        )
    return tokens


def box_to_xyxy(box: Any) -> BBox:
    values = box.tolist() if hasattr(box, "tolist") else box
    if len(values) == 4 and not isinstance(values[0], (list, tuple)):
        left, top, right, bottom = values
    else:
        xs = [point[0] for point in values]
        ys = [point[1] for point in values]
        left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    return int(left), int(top), int(right), int(bottom)


def union_boxes(boxes: list[BBox]) -> BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def group_visual_lines(tokens: list[OCRToken]) -> list[VisualLine]:
    lines: list[VisualLine] = []
    for token in sorted(tokens, key=lambda item: ((item.bbox[1] + item.bbox[3]) / 2, item.bbox[0])):
        token_center = (token.bbox[1] + token.bbox[3]) / 2
        token_height = max(token.bbox[3] - token.bbox[1], 1)
        best_line: VisualLine | None = None
        best_distance = float("inf")
        for line in lines:
            line_box = line.bbox
            line_center = (line_box[1] + line_box[3]) / 2
            line_height = max(line_box[3] - line_box[1], 1)
            distance = abs(token_center - line_center)
            tolerance = max(token_height, line_height) * LINE_GROUP_Y_TOLERANCE
            horizontal_gap = max(
                token.bbox[0] - line_box[2], line_box[0] - token.bbox[2], 0
            )
            if (
                distance <= tolerance
                and distance <= ROW_CENTER_Y_TOLERANCE
                and horizontal_gap <= LINE_GROUP_MAX_HORIZONTAL_GAP
                and distance < best_distance
            ):
                best_line = line
                best_distance = distance
        if best_line is None:
            lines.append(VisualLine([token]))
        else:
            best_line.tokens.append(token)
    return sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))


def canonical_section(text: str) -> str | None:
    matches = list(SECTION_PATTERN.finditer(text))
    if not matches:
        return None
    for match in matches:
        section = re.sub(r"\s+", " ", match.group(1).strip())
        lowered = section.lower()
        if lowered in {"절대값half", "절대값 half"}:
            return "절대값half"
        if lowered.startswith("day"):
            normalized = lowered.replace(" ", "")
            if normalized in DAILY_SECTIONS:
                return normalized
        if section in {"가격 이동평균", "시체소굴", "절대값"}:
            return section
    return "__other__"


def normalize_tooltip_text(text: str) -> str:
    """검증용으로 대소문자와 공백만 정규화한다; OCR 문자를 추정 보정하지 않는다."""
    return re.sub(r"\s+", "", text).lower()


def collect_minute_evidence(text: str) -> tuple[str, ...]:
    normalized = normalize_tooltip_text(text)
    evidence: list[str] = []
    if "시체소굴" in normalized:
        evidence.append("시체소굴")
    if "절대값half" in normalized:
        evidence.append("절대값half")
    return tuple(evidence)


def collect_daily_evidence(text: str) -> tuple[str, ...]:
    normalized = normalize_tooltip_text(text)
    evidence: list[str] = []
    for keyword in ("가격이동평균", "매집봉"):
        if keyword in normalized:
            evidence.append("가격 이동평균" if keyword == "가격이동평균" else keyword)
    for day in ("20", "33", "60", "112", "224", "335"):
        if re.search(rf"day{day}(?!\d)", normalized):
            evidence.append(f"day{day}")
    return tuple(evidence)


def validate_tooltip_content(text: str) -> TooltipValidation:
    minute = collect_minute_evidence(text)
    daily = collect_daily_evidence(text)
    day_count = sum(item.startswith("day") for item in daily)
    daily_valid = (
        ("가격 이동평균" in daily and day_count >= 1)
        or ("매집봉" in daily and day_count >= 1)
        or day_count >= 2
    )
    if minute:
        return TooltipValidation(True, "minute", minute, daily)
    if daily_valid:
        return TooltipValidation(True, "daily", minute, daily)
    return TooltipValidation(
        False,
        None,
        minute,
        daily,
        "insufficient_section_evidence",
    )


def classify_chart_type(text: str) -> str:
    validation = validate_tooltip_content(text)
    if validation.chart_type is not None:
        return validation.chart_type
    # 저장 이미지용 저수준 OCR 함수의 기존 기본값은 유지한다. 라이브 검증은 별도로 거부한다.
    return "daily"


def parse_decimal(raw: str) -> Decimal | None:
    matches = list(NUMBER_PATTERN.finditer(raw))
    if not matches:
        return None
    try:
        return Decimal(matches[-1].group(0).replace(",", ""))
    except InvalidOperation:
        return None


def box_for_text_span(token: OCRToken, start: int, end: int) -> BBox:
    """Paddle가 라벨과 값을 한 token으로 준 경우 문자 비율로 하위 bbox를 만든다."""
    left, top, right, bottom = token.bbox
    length = max(len(token.text), 1)
    width = right - left
    return (
        left + int(width * start / length),
        top,
        left + int(width * end / length),
        bottom,
    )


def bbox_center_y(box: BBox) -> float:
    return (box[1] + box[3]) / 2


def value_x_allowed(box: BBox, value_x_range: tuple[int, int]) -> bool:
    center_x = (box[0] + box[2]) / 2
    return value_x_range[0] <= center_x <= value_x_range[1]


def price_token_for_row(
    line: VisualLine,
    value_x_range: tuple[int, int],
    value_y_range: tuple[int, int] | None = None,
) -> tuple[OCRToken | None, str, BBox | None, str, BBox, str]:
    """같은 행에서 라벨 오른쪽이며 가격 열 안에 있는 숫자만 선택한다."""
    ordered = sorted(line.tokens, key=lambda token: token.bbox[0])
    candidates: list[tuple[OCRToken, re.Match[str], BBox, bool]] = []
    for token in ordered:
        searchable = token.text.split("(", 1)[0]
        matches = list(NUMBER_PATTERN.finditer(searchable))
        if ":" in searchable:
            colon = searchable.rfind(":")
            # inline 행은 ':' 뒤 첫 숫자만 가격이다. 뒤에 다른 숫자가 있어도
            # percentage나 부가 값으로 보고 가격 후보로 승격하지 않는다.
            matches = [match for match in matches if match.start() > colon][:1]
        elif len(matches) > 1:
            matches = [matches[-1]]
        for match in matches:
            numeric_box = box_for_text_span(token, match.start(), match.end())
            center_y = bbox_center_y(numeric_box)
            if value_x_allowed(numeric_box, value_x_range) and (
                value_y_range is None
                or value_y_range[0] <= center_y <= value_y_range[1]
            ):
                prefix = token.text[: match.start()]
                has_inline_label = bool(re.search(r"[^\W\d_]", prefix, re.UNICODE))
                candidates.append((token, match, numeric_box, has_inline_label))

    # 라벨과 값이 한 token이면 그 값을 우선한다. 그런 값이 없을 때만 같은 행의
    # 오른쪽 별도 token을 사용한다. 가격 열 밖 숫자는 후보 자체가 되지 않는다.
    for token, match, numeric_box, has_inline_label in sorted(
        candidates, key=lambda item: (not item[3], item[2][0])
    ):
        left_tokens = [
            other
            for other in ordered
            if other.bbox[0] < numeric_box[0]
            and abs(bbox_center_y(other.bbox) - bbox_center_y(numeric_box))
            <= ROW_CENTER_Y_TOLERANCE
        ]
        if len(left_tokens) > 1:
            label_box = union_boxes(
                [
                    box_for_text_span(other, 0, match.start())
                    if other is token
                    else other.bbox
                    for other in left_tokens
                ]
            )
            label_text = " ".join(
                (
                    other.text[: match.start()].strip()
                    if other is token
                    else other.text
                )
                for other in left_tokens
            ).strip()
        elif token in left_tokens and match.start() > 0:
            label_box = box_for_text_span(token, 0, match.start())
            label_text = token.text[: match.start()].strip()
        elif left_tokens:
            label_box = union_boxes([other.bbox for other in left_tokens])
            label_text = " ".join(other.text for other in left_tokens).strip()
        else:
            # ':값'처럼 섹션 자체가 라벨인 행도 허용하되 동일 token 내부에서
            # 숫자 앞 문자가 실제로 존재해야 한다.
            if match.start() == 0:
                continue
            label_box = box_for_text_span(token, 0, match.start())
            label_text = token.text[: match.start()].strip()
        if numeric_box[0] < label_box[0]:
            continue
        if abs(bbox_center_y(label_box) - bbox_center_y(numeric_box)) > ROW_CENTER_Y_TOLERANCE:
            continue
        association = "inline_label_token" if has_inline_label else "separate_value_token"
        return token, match.group(0), numeric_box, label_text, label_box, association
    return None, "", None, line.text, line.bbox, "missing"


def missing_numeric_box(item_bbox: BBox, image_width: int) -> BBox:
    left = min(item_bbox[2] + 2, image_width - 1)
    right = min(max(left + MISSING_NUMERIC_REGION_WIDTH, item_bbox[2]), image_width)
    if right <= left:
        left = max(0, item_bbox[0] - MISSING_NUMERIC_REGION_WIDTH)
        right = image_width
    return left, item_bbox[1], right, item_bbox[3]


def make_price_result(
    key: str,
    line: VisualLine,
    current_price: Decimal | None,
    image_width: int,
    value_x_range: tuple[int, int],
    value_y_range: tuple[int, int] | None = None,
) -> PriceResult:
    (
        price_token,
        numeric_text,
        matched_box,
        label_text,
        label_box,
        association,
    ) = price_token_for_row(line, value_x_range, value_y_range)
    if price_token is None:
        confidence = min((token.confidence for token in line.tokens), default=0.0)
        price_bbox = missing_numeric_box(line.bbox, image_width)
        value = None
        raw_text = ""
    else:
        confidence = price_token.confidence
        price_bbox = matched_box or price_token.bbox
        value = parse_decimal(numeric_text)
        raw_text = numeric_text

    result = PriceResult(
        key=key,
        item_text=label_text,
        item_bbox=label_box,
        price_bbox=price_bbox,
        raw_text=raw_text,
        value=value,
        confidence=confidence,
        status="valid",
        source=(
            "primary_ocr_inline"
            if association == "inline_label_token"
            else "primary_ocr"
        ),
        raw_value=value,
    )
    result.reasons = validation_reasons(result, current_price)
    if result.reasons:
        result.status = "invalid" if has_range_error(result.reasons) else "uncertain"
    return result


def validation_reasons(result: PriceResult, current_price: Decimal | None) -> list[str]:
    reasons: list[str] = []
    if result.confidence < OCR_CONFIDENCE_THRESHOLD:
        reasons.append("low_confidence")
    if result.value is None:
        reasons.append("numeric_parse_failed_or_missing")
        return reasons
    if result.value <= 0:
        reasons.append("price_not_positive")
    if result.key == "current_price":
        return reasons
    if current_price is None:
        reasons.append("current_price_unavailable")
    elif result.value >= current_price * PRICE_MAX_MULTIPLIER:
        reasons.append("price_over_max_multiplier")
    return reasons


def has_range_error(reasons: list[str]) -> bool:
    return any(reason in {"price_not_positive", "price_over_max_multiplier"} for reason in reasons)


def daily_key(section: str, line: VisualLine, index: int) -> str:
    text = line.text
    if section == "가격 이동평균":
        matches = NUMBER_PATTERN.findall(text.split(":", 1)[0])
        suffix = matches[0] if matches else str(index)
        return f"moving_average_{suffix}_wall"
    if "바닥" in text:
        return f"{section}_floor"
    if "벽" in text:
        return f"{section}_wall"
    return f"{section}_wall_{index}"


def merge_split_daily_value_rows(lines: list[VisualLine]) -> list[VisualLine]:
    """잡음 token 때문에 period 라벨과 ':가격'이 둘로 갈린 같은 행만 재결합한다."""
    merged: list[VisualLine] = []
    index = 0
    periods = {"20", "33", "60", "112", "224", "335"}
    while index < len(lines):
        line = lines[index]
        label = re.sub(r"\s+", "", line.text)
        if index + 1 < len(lines) and label in periods:
            value_line = lines[index + 1]
            value_text = value_line.text.lstrip()
            if (
                value_text.startswith(":")
                and NUMBER_PATTERN.search(value_text)
                and abs(bbox_center_y(line.bbox) - bbox_center_y(value_line.bbox))
                <= ROW_CENTER_Y_TOLERANCE
            ):
                merged.append(VisualLine([*line.tokens, *value_line.tokens]))
                index += 2
                continue
        merged.append(line)
        index += 1
    return merged


def collect_daily_items(
    lines: list[VisualLine], current_price: Decimal | None, image_width: int
) -> list[PriceResult]:
    lines = merge_split_daily_value_rows(lines)
    items: list[PriceResult] = []
    section: str | None = None
    section_index = 0
    for line in lines:
        detected_section = canonical_section(line.text)
        if detected_section is not None:
            section = detected_section
            section_index = 0
            remainder = line.text.split("]", 1)[1] if "]" in line.text else ""
            if section in DAILY_SECTIONS and ":" in remainder and NUMBER_PATTERN.search(remainder):
                section_index += 1
                items.append(
                    make_price_result(
                        daily_key(section, line, section_index),
                        line,
                        current_price,
                        image_width,
                        (DAILY_VALUE_X_MIN, DAILY_VALUE_X_MAX),
                        (DAILY_VALUE_Y_MIN, DAILY_VALUE_Y_MAX),
                    )
                )
            continue
        if section not in DAILY_SECTIONS:
            continue
        number_count = len(NUMBER_PATTERN.findall(line.text))
        moving_average_pair = (
            section == "가격 이동평균"
            and number_count >= 2
            and any(
                re.search(rf"(?<!\d){period}(?!\d)", line.text)
                for period in (20, 33, 60, 112, 224, 335)
            )
        )
        if (
            ":" not in line.text
            and "벽" not in line.text
            and "바닥" not in line.text
            and not moving_average_pair
        ):
            continue
        section_index += 1
        items.append(
            make_price_result(
                daily_key(section, line, section_index),
                line,
                current_price,
                image_width,
                (DAILY_VALUE_X_MIN, DAILY_VALUE_X_MAX),
                (DAILY_VALUE_Y_MIN, DAILY_VALUE_Y_MAX),
            )
        )
    return items


def collect_minute_items(
    lines: list[VisualLine], current_price: Decimal | None, image_width: int
) -> list[PriceResult]:
    items: list[PriceResult] = []
    section: str | None = None
    corpse_index = 0
    absolute_sell_index = 0
    phrase_keys = {
        "니 위에서 관문 터치하면 매도": "buy_price",
        "니 바닥으로 흐르면 매도": "rebound_price",
        "태초마을": "taecho",
    }
    for line in lines:
        normalized_text = re.sub(r"\s+", "", line.text)
        if (
            section == "절대값half"
            and "절대값half" in normalized_text
            and ":" in line.text
        ):
            items.append(
                make_price_result(
                    "absolute_half", line, current_price, image_width,
                    (MINUTE_VALUE_X_MIN, MINUTE_VALUE_X_MAX)
                    , (MINUTE_VALUE_Y_MIN, MINUTE_VALUE_Y_MAX)
                )
            )
            section = None
            continue
        detected_section = canonical_section(line.text)
        if detected_section is not None:
            section = detected_section
            if section == "절대값":
                absolute_sell_index = 0
            remainder = line.text.split("]", 1)[1] if "]" in line.text else ""
            if (
                section == "절대값half"
                and ":" in remainder
                and NUMBER_PATTERN.search(remainder)
            ):
                items.append(
                    make_price_result(
                        "absolute_half", line, current_price, image_width,
                        (MINUTE_VALUE_X_MIN, MINUTE_VALUE_X_MAX)
                        , (MINUTE_VALUE_Y_MIN, MINUTE_VALUE_Y_MAX)
                    )
                )
                section = None
            continue
        key: str | None = None
        close_corpse_after_line = False
        if section == "시체소굴":
            if NUMBER_PATTERN.search(line.text):
                corpse_index += 1
                key = f"corpse_wall_{corpse_index}"
                if "끝판왕" in normalized_text:
                    close_corpse_after_line = True
        elif section == "절대값half":
            if "절대값half" in normalized_text:
                key = "absolute_half"
                section = None
        elif section == "절대값":
            key = next(
                (
                    value
                    for phrase, value in phrase_keys.items()
                    if re.sub(r"\s+", "", phrase) in normalized_text
                ),
                None,
            )
            if key == "buy_price":
                absolute_sell_index = max(absolute_sell_index, 1)
            elif key == "rebound_price":
                absolute_sell_index = max(absolute_sell_index, 2)
            elif (
                key is None
                and "매도" in normalized_text
                and NUMBER_PATTERN.search(line.text)
            ):
                absolute_sell_index += 1
                if absolute_sell_index == 1:
                    key = "buy_price"
                elif absolute_sell_index == 2:
                    key = "rebound_price"
        if key is not None:
            items.append(
                make_price_result(
                    key, line, current_price, image_width,
                    (MINUTE_VALUE_X_MIN, MINUTE_VALUE_X_MAX)
                    , (MINUTE_VALUE_Y_MIN, MINUTE_VALUE_Y_MAX)
                )
            )
        if close_corpse_after_line:
            section = None
    return items


def merge_taecho_and_absolute_half(items: list[PriceResult]) -> list[PriceResult]:
    taecho = next(
        (item for item in items if item.key == "taecho" and item.status != "invalid"),
        None,
    )
    absolute_half = next(
        (
            item
            for item in items
            if item.key == "absolute_half" and item.status != "invalid"
        ),
        None,
    )
    if (
        taecho is not None
        and absolute_half is not None
        and taecho.value is not None
        and absolute_half.value is not None
        and taecho.value != 0
        and abs(absolute_half.value - taecho.value) / taecho.value <= Decimal("0.01")
    ):
        print("[MERGED] absolute_half -> taecho (difference <= 1%)")
        # 태초마을이 대표값이며 absolute_half만 별도 후보에서 제거한다.
        return [item for item in items if item.key != "absolute_half"]
    return items


def expand_endgame_wall_multipliers(
    items: list[PriceResult], current_price: Decimal | None
) -> list[PriceResult]:
    """Expand a valid 끝판왕 OCR value into 1x through 5x wall candidates."""
    endgame = next(
        (
            item
            for item in items
            if "\ub05d\ud310\uc655" in re.sub(r"\s+", "", item.item_text)
            and item.status == "valid"
            and item.value is not None
        ),
        None,
    )
    if endgame is None:
        return items

    next_index = max(
        (
            int(match.group(1))
            for item in items
            if (match := re.fullmatch(r"corpse_wall_(\d+)", item.key))
        ),
        default=0,
    )
    expanded = list(items)
    for multiplier in range(2, 6):
        derived_value = endgame.value * multiplier
        derived = replace(
            endgame,
            key=f"corpse_wall_{next_index + multiplier - 1}",
            item_text=f"\ub05d\ud310\uc655 {multiplier}\ubc30",
            raw_text=str(derived_value),
            value=derived_value,
            source="derived_endgame_multiplier",
            reasons=[],
            raw_value=derived_value,
        )
        derived.reasons = validation_reasons(derived, current_price)
        if derived.reasons:
            derived.value = None
            derived.status = (
                "filtered" if has_range_error(derived.reasons) else "uncertain"
            )
        expanded.append(derived)
    return expanded


def deduplicate_prices(items: list[PriceResult]) -> list[PriceResult]:
    seen: set[Decimal] = set()
    unique: list[PriceResult] = []
    for item in items:
        if item.value is not None and item.status != "invalid":
            if item.value in seen:
                print(f"[DUPLICATE REMOVED] {item.key}: {format_decimal(item.value)}")
                continue
            seen.add(item.value)
        unique.append(item)
    return unique


def extract_stock_identity_from_ocr(tokens: list[OCRToken]) -> StockIdentity:
    """Tooltip OCR에서 회사명/종목명 행만 찾고 ticker는 괄호 hint로만 보존한다."""
    excluded_terms = {
        "가격 이동평균",
        "시체소굴",
        "절대값",
        "시가",
        "고가",
        "저가",
        "종가",
        "거래량",
        "매도",
        "매집봉",
    }
    candidates: list[tuple[int, float, VisualLine, str | None, str | None, str | None]] = []
    grouped_lines = group_visual_lines(tokens)
    # 괄호 ticker가 한 token 안에 있으면 좌우 HTS 메뉴 token이 섞인 시각 행보다
    # 해당 token 자체를 먼저 평가한다. ticker는 여전히 resolver 검증 전 hint일 뿐이다.
    identity_lines = [
        VisualLine([token])
        for token in tokens
        if re.search(r"\([A-Z][A-Z0-9.\-]{0,7}\)", token.text)
    ] + grouped_lines
    for line in identity_lines:
        text = re.sub(r"\s+", " ", line.text).strip()
        if not text or any(term in text for term in excluded_terms):
            continue
        if canonical_section(text) is not None or re.search(r"\bday\d+\b", text, re.I):
            continue
        ticker_match = re.search(r"\(([A-Z][A-Z0-9.\-]{0,7})\)", text)
        ticker_hint = ticker_match.group(1) if ticker_match else None

        before_korean = re.split(r"[가-힣]", text, maxsplit=1)[0]
        before_korean = before_korean.split("(", 1)[0].strip(" []:-")
        english_name = (
            before_korean
            if re.search(r"[A-Z]", before_korean)
            and len(re.findall(r"[A-Z0-9]+", before_korean)) >= 2
            else None
        )
        before_ticker = text.split("(", 1)[0]
        korean_matches = re.findall(
            r"[가-힣][가-힣0-9]*(?:\s+[가-힣0-9]+)*",
            before_ticker,
        )
        korean_name = korean_matches[-1].strip() if korean_matches else None
        if english_name is None and korean_name is None:
            continue

        confidence = sum(token.confidence for token in line.tokens) / len(line.tokens)
        score = 0
        score += 4 if english_name and korean_name else 0
        score += 10 if ticker_hint else 0
        score += 2 if english_name and re.search(
            r"\b(?:INC|CORP|CORPORATION|LTD|LIMITED|GROUP|PLC)\.?$",
            english_name,
        ) else 0
        score += 1 if line.bbox[1] <= STOCK_HEADER_Y_MAX else 0
        candidates.append(
            (score, confidence, line, english_name, korean_name, ticker_hint)
        )

    if not candidates:
        return StockIdentity("", None, None, None, 0.0)
    _, confidence, line, english_name, korean_name, ticker_hint = max(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1], -candidate[2].bbox[1]),
    )
    return StockIdentity(
        raw_identity_text=line.text,
        english_name=english_name,
        korean_name=korean_name,
        ticker_hint=ticker_hint,
        confidence=confidence,
    )


def extract_stock_info(tokens: list[OCRToken]) -> StockInfo:
    identity = extract_stock_identity_from_ocr(tokens)
    return StockInfo(
        stock_code=identity.ticker_hint,
        stock_name=identity.korean_name or identity.english_name,
    )


def external_current_price_result(current_price: Decimal | None) -> PriceResult:
    available = current_price is not None and current_price > 0
    return PriceResult(
        key="current_price",
        item_text="Toss Open API",
        item_bbox=(0, 0, 0, 0),
        price_bbox=(0, 0, 0, 0),
        raw_text=format_decimal(current_price),
        value=current_price if available else None,
        confidence=1.0 if available else 0.0,
        status="valid" if available else "unavailable",
        source="toss_api",
        reasons=[] if available else ["toss_api_current_price_unavailable"],
        raw_value=current_price if available else None,
    )


def save_bbox_debug_image(
    image_name: str,
    image: Any,
    chart_type: str,
    items: list[PriceResult],
) -> Path:
    canvas = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if len(image.shape) == 2 else image.copy()
    x_min, x_max, y_min, y_max = (
        (DAILY_VALUE_X_MIN, DAILY_VALUE_X_MAX, DAILY_VALUE_Y_MIN, DAILY_VALUE_Y_MAX)
        if chart_type == "daily"
        else (MINUTE_VALUE_X_MIN, MINUTE_VALUE_X_MAX, MINUTE_VALUE_Y_MIN, MINUTE_VALUE_Y_MAX)
    )
    cv2.rectangle(canvas, (x_min, y_min), (x_max, y_max), (255, 160, 0), 2)
    for item in items:
        lx1, ly1, lx2, ly2 = item.label_bbox
        vx1, vy1, vx2, vy2 = item.price_bbox
        cv2.rectangle(canvas, (lx1, ly1), (lx2, ly2), (0, 200, 0), 2)
        cv2.rectangle(canvas, (vx1, vy1), (vx2, vy2), (0, 0, 255), 2)
        cv2.putText(
            canvas, item.key, (max(lx1, 0), max(ly1 - 4, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 200, 0), 1, cv2.LINE_AA,
        )
    output_directory = PROJECT_ROOT / "ocr_results"
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{image_name}_bbox_debug.png"
    if not cv2.imwrite(str(output_path), canvas):
        raise OSError(f"bbox 디버그 이미지 저장 실패: {output_path}")
    return output_path


def create_numeric_retry_crop(image: Any, item: PriceResult, image_name: str) -> Path:
    left, top, right, bottom = item.price_bbox
    height, width = image.shape[:2]
    left = max(0, left - NUMERIC_CROP_PADDING_X)
    top = max(0, top - NUMERIC_CROP_PADDING_Y)
    right = min(width, right + NUMERIC_CROP_PADDING_X)
    bottom = min(height, bottom + NUMERIC_CROP_PADDING_Y)
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        raise ValueError(f"숫자 재판독 crop이 비어 있습니다: {item.key}")
    if len(crop.shape) == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    crop = cv2.resize(
        crop,
        None,
        fx=NUMERIC_CROP_SCALE,
        fy=NUMERIC_CROP_SCALE,
        interpolation=cv2.INTER_CUBIC,
    )
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    crop = clahe.apply(crop)
    RETRY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^0-9A-Za-z_-]+", "_", item.key)
    output_path = RETRY_DIRECTORY / f"{image_name}_{safe_key}.png"
    if not cv2.imwrite(str(output_path), crop):
        raise OSError(f"숫자 재판독 crop 저장 실패: {output_path}")
    return output_path


def numeric_retry(
    reader: Any,
    crop_path: Path,
    current_price: Decimal | None,
    result_key: str,
) -> tuple[Decimal | None, float, str, list[str], str]:
    candidates: list[tuple[Decimal, float, str]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        for result in reader.predict(str(crop_path)):
            for text, score in zip(result["rec_texts"], result["rec_scores"]):
                value = parse_decimal(str(text))
                if value is not None:
                    candidates.append((value, float(score), str(text)))

    unique_values = {candidate[0] for candidate in candidates}
    if len(unique_values) != 1:
        reason = "numeric_retry_no_value" if not candidates else "numeric_retry_multiple_values"
        return None, max((item[1] for item in candidates), default=0.0), "uncertain", [reason], ""

    value = next(iter(unique_values))
    best = max((item for item in candidates if item[0] == value), key=lambda item: item[1])
    retry_result = PriceResult(
        key=result_key,
        item_text="",
        item_bbox=(0, 0, 0, 0),
        price_bbox=(0, 0, 0, 0),
        raw_text=best[2],
        value=value,
        confidence=best[1],
        status="valid",
        raw_value=value,
    )
    reasons = validation_reasons(retry_result, current_price)
    status = "valid" if not reasons else ("invalid" if has_range_error(reasons) else "uncertain")
    return value if status == "valid" else None, best[1], status, reasons, best[2]


def retry_suspicious_items(
    reader: Any,
    image: Any,
    image_name: str,
    items: list[PriceResult],
    current_price: Decimal | None,
) -> None:
    for item in items:
        if item.status == "valid":
            print(f"[PRIMARY VALID] {item.key}: {format_decimal(item.value)} ({item.confidence:.3f})")
            continue
        if item.reasons == ["current_price_unavailable"]:
            print(
                f"[CURRENT PRICE UNAVAILABLE] {item.key}: "
                "10x filter deferred; Tooltip OHLC is not used"
            )
            continue
        business_filter_reasons = {"price_over_max_multiplier", "price_not_positive"}
        if (
            len(item.reasons) == 1
            and item.reasons[0] in business_filter_reasons
            and item.value is not None
            and item.confidence >= OCR_CONFIDENCE_THRESHOLD
        ):
            item.raw_value = item.value
            item.value = None
            item.status = "filtered"
            print(
                f"[FILTERED] {item.key}: {format_decimal(item.raw_value)} "
                f"({item.reasons[0]})"
            )
            continue
        print(f"[NUMERIC RETRY] {item.key}: {', '.join(item.reasons)}")
        crop_path = create_numeric_retry_crop(image, item, image_name)
        value, confidence, status, reasons, raw_text = numeric_retry(
            reader, crop_path, current_price, item.key
        )
        item.value = value
        item.confidence = confidence
        item.status = status
        item.source = "numeric_retry"
        item.reasons = reasons
        item.raw_text = raw_text
        item.raw_value = parse_decimal(raw_text)
        print(
            f"[RETRY {status.upper()}] {item.key}: "
            f"{format_decimal(value)} ({confidence:.3f}) crop={crop_path.name}"
        )


def format_decimal(value: Decimal | None) -> str:
    return "null" if value is None else format(value, "f")


def print_structured_results(chart_type: str, items: list[PriceResult]) -> None:
    print(f"\nchart_type: {chart_type}")
    for item in items:
        print(f"\n{item.key}:")
        print(f"  label_text: {item.label_text}")
        print(f"  label_bbox: {item.label_bbox}")
        print(f"  selected_value_text: {item.raw_text or 'null'}")
        print(f"  selected_value_bbox: {item.price_bbox}")
        print(f"  raw_value: {format_decimal(item.raw_value)}")
        print(f"  value: {format_decimal(item.value)}")
        print(f"  confidence: {item.confidence:.4f}")
        print(f"  status: {item.status}")
        print(f"  source: {item.source}")
        if item.reasons:
            print(f"  reasons: {', '.join(item.reasons)}")


def save_primary_tokens(image_name: str, tokens: list[OCRToken]) -> Path:
    output_directory = PROJECT_ROOT / "ocr_results"
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"{image_name}_paddle_structured.json"
    payload = [
        {
            "text": token.text,
            "confidence": token.confidence,
            "bbox": list(token.bbox),
        }
        for token in tokens
    ]
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path


def capture_image(reader: Any, name: str, image_path: Path) -> OcrCapture:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"전처리 이미지를 읽을 수 없습니다: {image_path}")

    print(f"\n{'=' * 16} {name} {'=' * 16}")
    tokens = primary_ocr(reader, image_path)
    print(f"primary_ocr_count: 1")
    print(f"recognized_tokens: {len(tokens)}")
    print(f"structured_ocr: {save_primary_tokens(name, tokens)}")
    lines = group_visual_lines(tokens)
    all_text = "\n".join(line.text for line in lines)
    chart_type = classify_chart_type(all_text)
    identity = extract_stock_identity_from_ocr(tokens)
    stock = StockInfo(
        stock_code=identity.ticker_hint,
        stock_name=identity.korean_name or identity.english_name,
    )
    return OcrCapture(name, image, tokens, lines, chart_type, stock, identity)


def analyze_capture(
    reader: Any,
    capture: OcrCapture,
    current_price: Decimal | None,
) -> OcrAnalysis:
    name = capture.name
    image = capture.image
    chart_type = capture.chart_type
    stock = capture.stock
    current = external_current_price_result(current_price)

    if chart_type == "daily":
        items = collect_daily_items(capture.lines, current_price, image.shape[1])
    else:
        items = collect_minute_items(capture.lines, current_price, image.shape[1])
    retry_suspicious_items(reader, image, name, items, current_price)
    if chart_type == "minute":
        items = expand_endgame_wall_multipliers(items, current_price)
        items = merge_taecho_and_absolute_half(items)
    items = deduplicate_prices(items)
    debug_items = [current, *items]
    debug_path = save_bbox_debug_image(name, image, chart_type, debug_items)
    print(f"\nstock_code: {stock.stock_code or 'null'}")
    print(f"stock_name: {stock.stock_name or 'null'}")
    print(f"display_name: {stock.display_name or 'null'}")
    print(f"bbox_debug_image: {debug_path}")
    print_structured_results(chart_type, [current, *items])
    return OcrAnalysis(chart_type, stock, current, items)


def process_image(
    reader: Any,
    name: str,
    image_path: Path,
    current_price: Decimal | None = None,
) -> OcrAnalysis:
    capture = capture_image(reader, name, image_path)
    return analyze_capture(reader, capture, current_price)


def main() -> int:
    reader = create_reader()
    for name, image_path in INPUTS.items():
        # OCR 단독 검증에서는 API를 호출하지 않는다. 향후 호출자가 OCR로 얻은
        # stock_code로 Toss API를 조회한 뒤 current_price 인자로 주입한다.
        process_image(reader, name, image_path, current_price=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
