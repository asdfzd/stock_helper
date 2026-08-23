from __future__ import annotations

import argparse
import re
import sys
import warnings
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import cv2
from PIL import Image, ImageOps

from ocr_config import CROP_OUTPUT_DIRECTORY, DESCRIPTION_CROP_BOX


MINUTE_KEYWORDS = ("시체소굴", "절대값half", "절대값 half")
DAILY_WALL_SECTIONS = {
    "가격 이동평균",
    "day20",
    "day33",
    "day60",
    "day112",
    "day224",
    "day335",
}
NUMBER_PATTERN = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w.])"
)
SECTION_PATTERN = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*$")
CURRENT_PRICE_PATTERN = re.compile(r"현재\s*가")


@dataclass
class ParsedResult:
    chart_type: str
    current_price: Decimal
    buy_price: Decimal | None = None
    rebound_price: Decimal | None = None
    taecho: Decimal | None = None
    absolute_half: Decimal | None = None
    walls: list[Decimal] = field(default_factory=list)


@dataclass
class CombinedResult:
    chart_types: list[str] = field(default_factory=list)
    current_prices: list[Decimal] = field(default_factory=list)
    buy_prices: list[Decimal] = field(default_factory=list)
    rebound_prices: list[Decimal] = field(default_factory=list)
    taecho_prices: list[Decimal] = field(default_factory=list)
    absolute_half_prices: list[Decimal] = field(default_factory=list)
    walls: list[Decimal] = field(default_factory=list)


class OCRParseError(ValueError):
    pass


def create_ocr_reader() -> Any:
    """여러 이미지에서 재사용할 EasyOCR Reader를 한 번 생성한다."""
    model_directory = Path(__file__).resolve().parent / ".easyocr-models"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        try:
            import easyocr
        except ImportError as exc:
            raise OCRParseError(
                "EasyOCR가 설치되어 있지 않습니다. requirements.txt를 설치해 주세요."
            ) from exc
        return easyocr.Reader(
            ["ko", "en"],
            gpu=False,
            model_storage_directory=str(model_directory),
            verbose=False,
        )


def perform_ocr(reader: Any, image_path: Path) -> str:
    """crop 이미지에서 문자열을 읽기 순서대로 반환한다."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        lines = reader.readtext(str(image_path), detail=0, paragraph=False)
    return "\n".join(str(line) for line in lines)


def crop_description_area(
    image_path: Path,
    crop_box: tuple[int, int, int, int],
    image_number: int,
) -> Path:
    """전체 화면에서 설정된 설명란 영역만 잘라 파일로 저장한다."""
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source)
        left, top, right, bottom = crop_box
        width, height = image.size
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise OCRParseError(
                f"crop 좌표 {crop_box}가 이미지 크기 {width}x{height}를 벗어납니다. "
                "ocr_config.py 또는 --crop 값을 조정해 주세요."
            )

        output_directory = Path(__file__).resolve().parent / CROP_OUTPUT_DIRECTORY
        output_directory.mkdir(parents=True, exist_ok=True)
        output_path = output_directory / f"{image_number}_{image_path.stem}_crop.png"
        image.crop(crop_box).save(output_path, format="PNG")
    return output_path


def preprocess_crop_for_ocr(crop_path: Path) -> tuple[Path, Path]:
    """crop을 3배 확대하고, grayscale/대비 강화본을 생성한다."""
    image = cv2.imread(str(crop_path), cv2.IMREAD_COLOR)
    if image is None:
        raise OCRParseError(f"crop 이미지를 OpenCV로 읽을 수 없습니다: {crop_path}")

    upscaled = cv2.resize(
        image,
        None,
        fx=3,
        fy=3,
        interpolation=cv2.INTER_CUBIC,
    )
    grayscale = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)

    # 소수점과 작은 한글 획을 보존하기 위해 이진화하지 않고 국소 대비만 높인다.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    preprocessed = clahe.apply(grayscale)

    upscaled_path = crop_path.with_name(f"{crop_path.stem}_upscaled.png")
    preprocessed_path = crop_path.with_name(f"{crop_path.stem}_preprocessed.png")
    if not cv2.imwrite(str(upscaled_path), upscaled):
        raise OCRParseError(f"확대 이미지를 저장하지 못했습니다: {upscaled_path}")
    if not cv2.imwrite(str(preprocessed_path), preprocessed):
        raise OCRParseError(f"전처리 이미지를 저장하지 못했습니다: {preprocessed_path}")
    return upscaled_path, preprocessed_path


def detect_chart_type(text: str) -> str:
    return "minute" if any(keyword in text for keyword in MINUTE_KEYWORDS) else "daily"


def extract_numbers(text: str) -> list[Decimal]:
    """인식된 숫자를 보정하지 않고 유효한 10진수 표현만 변환한다."""
    numbers: list[Decimal] = []
    for match in NUMBER_PATTERN.finditer(text):
        raw_number = match.group(0).replace(",", "")
        try:
            numbers.append(Decimal(raw_number))
        except InvalidOperation:
            continue
    return numbers


def find_current_price(lines: list[str]) -> Decimal:
    for index, line in enumerate(lines):
        label_match = CURRENT_PRICE_PATTERN.search(line)
        if label_match is None:
            continue

        numbers = extract_numbers(line[label_match.end() :])
        if numbers:
            return numbers[0]

        for following_line in lines[index + 1 :]:
            if not following_line.strip():
                continue
            if SECTION_PATTERN.match(following_line):
                break
            numbers = extract_numbers(following_line)
            if numbers:
                return numbers[0]
            break

    raise OCRParseError(
        "현재가를 OCR 결과에서 찾지 못했습니다. '현재가' 문구와 가격이 보이는 이미지인지 확인해 주세요."
    )


def canonical_section(line: str) -> str | None:
    match = SECTION_PATTERN.match(line)
    if match is None:
        return None
    section = re.sub(r"\s+", " ", match.group(1).strip())
    lowered = section.lower()
    if lowered in {"절대값half", "절대값 half"}:
        return "절대값half"
    return lowered if lowered.startswith("day") else section


def parse_ocr_text(text: str) -> ParsedResult:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    current_price = find_current_price(lines)
    chart_type = detect_chart_type(text)
    result = ParsedResult(chart_type=chart_type, current_price=current_price)

    if chart_type == "daily":
        parse_daily(lines, result)
    else:
        parse_minute(lines, result)

    apply_price_filter(result)
    merge_taecho_and_half(result)
    result.walls = unique_prices(result.walls)
    return result


def parse_daily(lines: list[str], result: ParsedResult) -> None:
    current_section: str | None = None
    for line in lines:
        section = canonical_section(line)
        if section is not None:
            current_section = section
            continue
        if current_section in DAILY_WALL_SECTIONS and not CURRENT_PRICE_PATTERN.search(line):
            result.walls.extend(extract_numbers(line))


def parse_minute(lines: list[str], result: ParsedResult) -> None:
    current_section: str | None = None
    pending_kind: str | None = None

    for line in lines:
        section = canonical_section(line)
        if section is not None:
            current_section = section
            pending_kind = None
            continue

        if CURRENT_PRICE_PATTERN.search(line):
            continue

        if current_section == "시체소굴":
            result.walls.extend(extract_numbers(line))
        elif current_section == "절대값half":
            numbers = extract_numbers(line)
            if numbers and result.absolute_half is None:
                result.absolute_half = numbers[0]
        elif current_section == "절대값":
            pending_kind = parse_absolute_line(line, result, pending_kind)


def parse_absolute_line(
    line: str,
    result: ParsedResult,
    pending_kind: str | None,
) -> str | None:
    phrase_map = {
        "니 위에서 관문 터치하면 매도": "buy_price",
        "니 바닥으로 흐르면 매도": "rebound_price",
        "태초마을": "taecho",
    }
    detected_kind = next((kind for phrase, kind in phrase_map.items() if phrase in line), None)
    active_kind = detected_kind or pending_kind
    numbers = extract_numbers(line)

    if active_kind is not None and numbers:
        if getattr(result, active_kind) is None:
            setattr(result, active_kind, numbers[0])
        return None
    return active_kind


def apply_price_filter(result: ParsedResult) -> None:
    threshold = result.current_price * Decimal("10")
    for field_name in ("buy_price", "rebound_price", "taecho", "absolute_half"):
        value = getattr(result, field_name)
        if value is not None and value >= threshold:
            setattr(result, field_name, None)
    result.walls = [price for price in result.walls if price < threshold]


def merge_taecho_and_half(result: ParsedResult) -> None:
    if result.taecho is None or result.absolute_half is None or result.taecho == 0:
        return
    difference_ratio = abs(result.absolute_half - result.taecho) / result.taecho
    if difference_ratio <= Decimal("0.01"):
        result.absolute_half = None


def unique_prices(prices: list[Decimal]) -> list[Decimal]:
    return list(dict.fromkeys(prices))


def combine_results(results: list[ParsedResult]) -> CombinedResult:
    combined = CombinedResult()
    for result in results:
        combined.chart_types.append(result.chart_type)
        combined.current_prices.append(result.current_price)
        if result.buy_price is not None:
            combined.buy_prices.append(result.buy_price)
        if result.rebound_price is not None:
            combined.rebound_prices.append(result.rebound_price)
        if result.taecho is not None:
            combined.taecho_prices.append(result.taecho)
        if result.absolute_half is not None:
            combined.absolute_half_prices.append(result.absolute_half)
        combined.walls.extend(result.walls)

    combined.chart_types = list(dict.fromkeys(combined.chart_types))
    combined.current_prices = unique_prices(combined.current_prices)
    combined.buy_prices = unique_prices(combined.buy_prices)
    combined.rebound_prices = unique_prices(combined.rebound_prices)
    combined.taecho_prices = unique_prices(combined.taecho_prices)
    combined.absolute_half_prices = unique_prices(combined.absolute_half_prices)
    combined.walls = unique_prices(combined.walls)

    # 태초마을과 half가 서로 다른 이미지에 있어도 1% 통합 규칙을 적용한다.
    combined.absolute_half_prices = [
        half
        for half in combined.absolute_half_prices
        if not any(
            taecho != 0 and abs(half - taecho) / taecho <= Decimal("0.01")
            for taecho in combined.taecho_prices
        )
    ]
    return combined


def format_price(price: Decimal | None) -> str:
    if price is None:
        return "not_found"
    return format(price, "f")


def print_result(result: ParsedResult) -> None:
    print(f"chart_type: {result.chart_type}")
    print(f"current_price: {format_price(result.current_price)}")
    print(f"buy_price: {format_price(result.buy_price)}")
    print(f"rebound_price: {format_price(result.rebound_price)}")
    print(f"taecho: {format_price(result.taecho)}")
    print(f"absolute_half: {format_price(result.absolute_half)}")
    print("walls:")
    if result.walls:
        for wall in result.walls:
            print(f"- {format_price(wall)}")
    else:
        print("- none")


def print_price_list(name: str, prices: list[Decimal]) -> None:
    print(f"{name}:")
    if prices:
        for price in prices:
            print(f"- {format_price(price)}")
    else:
        print("- none")


def print_combined_result(result: CombinedResult) -> None:
    print("chart_types:")
    for chart_type in result.chart_types:
        print(f"- {chart_type}")
    print_price_list("current_prices", result.current_prices)
    print_price_list("buy_prices", result.buy_prices)
    print_price_list("rebound_prices", result.rebound_prices)
    print_price_list("taecho_prices", result.taecho_prices)
    print_price_list("absolute_half_prices", result.absolute_half_prices)
    print_price_list("walls", result.walls)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="최대 2장의 전체 화면에서 설명란을 crop하여 OCR하고 가격 정보를 파싱합니다."
    )
    parser.add_argument(
        "images",
        type=Path,
        nargs="+",
        help="OCR을 수행할 이미지 파일 경로 (1~2개)",
    )
    parser.add_argument(
        "--crop",
        type=int,
        nargs=4,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        default=DESCRIPTION_CROP_BOX,
        help="설명란 crop 좌표 (기본값: ocr_config.py의 DESCRIPTION_CROP_BOX)",
    )
    parser.add_argument(
        "--ocr-only",
        action="store_true",
        help="파싱하지 않고 이미지별 OCR 원문만 출력",
    )
    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    if len(args.images) > 2:
        parser.error("이미지 경로는 최대 2개까지 입력할 수 있습니다.")

    image_paths = [path.expanduser().resolve() for path in args.images]
    missing_paths = [path for path in image_paths if not path.is_file()]
    if missing_paths:
        for path in missing_paths:
            print(f"오류: 이미지 파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        return 2

    try:
        crop_box = tuple(args.crop)
        reader = create_ocr_reader()
        results: list[ParsedResult] = []

        for image_number, image_path in enumerate(image_paths, start=1):
            print(f"\n=== 이미지 {image_number}: {image_path.name} ===")
            crop_path = crop_description_area(image_path, crop_box, image_number)
            print(f"crop_image: {crop_path}")
            upscaled_path, preprocessed_path = preprocess_crop_for_ocr(crop_path)
            print(f"upscaled_image: {upscaled_path}")
            print(f"preprocessed_image: {preprocessed_path}")

            raw_text = perform_ocr(reader, preprocessed_path)
            print("\n--- OCR 원문 ---")
            print(raw_text)
            if args.ocr_only:
                continue
            print("\n--- 파싱 결과 ---")
            result = parse_ocr_text(raw_text)
            print_result(result)
            results.append(result)

        if not args.ocr_only:
            print("\n=== 통합 파싱 결과 ===")
            print_combined_result(combine_results(results))
    except OCRParseError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"오류: OCR 실행에 실패했습니다: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
