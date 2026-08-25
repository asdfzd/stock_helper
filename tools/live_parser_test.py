from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np

from paddle_ocr_validation import (
    MINUTE_KEYWORDS,
    OCRToken,
    OcrCapture,
    analyze_capture,
    extract_stock_info,
    group_visual_lines,
)


DEFAULT_JSON = (
    PROJECT_ROOT
    / "ocr_results"
    / "live_20260823_101045_332956_paddle_structured.json"
)
TEST_CURRENT_PRICE = Decimal("13.6525")


class ParserOnlyReader:
    def predict(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("parser-only 테스트에서 PaddleOCR가 호출되었습니다.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="저장된 OCR JSON parser-only 테스트")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    return parser.parse_args()


def load_capture(json_path: Path) -> OcrCapture:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    tokens = [
        OCRToken(
            text=str(item["text"]),
            confidence=float(item["confidence"]),
            bbox=tuple(int(value) for value in item["bbox"]),
        )
        for item in payload
    ]
    lines = group_visual_lines(tokens)
    all_text = "\n".join(line.text for line in lines)
    chart_type = (
        "minute" if any(keyword in all_text for keyword in MINUTE_KEYWORDS) else "daily"
    )
    width = max((token.bbox[2] for token in tokens), default=1)
    height = max((token.bbox[3] for token in tokens), default=1)
    image = np.zeros((height, width), dtype=np.uint8)
    return OcrCapture(
        json_path.stem,
        image,
        tokens,
        lines,
        chart_type,
        extract_stock_info(tokens),
    )


def main() -> int:
    args = parse_args()
    json_path = args.json.resolve()
    if not json_path.is_file():
        raise FileNotFoundError(f"structured OCR JSON을 찾을 수 없습니다: {json_path}")

    capture = load_capture(json_path)
    analysis = analyze_capture(
        ParserOnlyReader(), capture, current_price=TEST_CURRENT_PRICE
    )
    values = {
        item.key: item.value
        for item in analysis.items
        if item.status == "valid" and item.value is not None
    }
    walls = [
        value for key, value in values.items() if key.startswith("corpse_wall_")
    ]

    assert capture.stock.stock_code == "SDOT"
    assert capture.stock.stock_name == "사닷 그룹"
    assert values.get("buy_price") == Decimal("17.1222")
    assert values.get("rebound_price") == Decimal("15.4038")
    assert values.get("taecho") == Decimal("14.0149")
    assert "absolute_half" not in values
    assert walls == [
        Decimal("9.1337"),
        Decimal("9.8173"),
        Decimal("11.0028"),
        Decimal("12.1883"),
    ]
    assert Decimal("58.0000") not in values.values()

    print("\n[PARSER TEST] passed")
    print(f"stock_code: {capture.stock.stock_code}")
    print(f"stock_name: {capture.stock.stock_name}")
    print(f"buy_price: {values['buy_price']}")
    print(f"rebound_price: {values['rebound_price']}")
    print(f"taecho: {values['taecho']}")
    print("absolute_half: null (merged into taecho)")
    print("minute_walls:")
    for wall in walls:
        print(f"- {wall}")
    print("58.0000: excluded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
