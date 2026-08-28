from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paddle_ocr_validation import (
    OCRToken,
    PriceResult,
    VisualLine,
    canonical_section,
    collect_daily_items,
    collect_minute_items,
    expand_endgame_wall_multipliers,
    merge_taecho_and_absolute_half,
)


def visual_line(text: str, y: int) -> VisualLine:
    return VisualLine([OCRToken(text, 0.99, (200, y, 700, y + 24))])


def price_result(key: str, value: str) -> PriceResult:
    decimal = Decimal(value)
    return PriceResult(
        key=key,
        item_text=key,
        item_bbox=(200, 1500, 400, 1524),
        price_bbox=(420, 1500, 600, 1524),
        raw_text=value,
        value=decimal,
        confidence=0.99,
        status="valid",
        raw_value=decimal,
    )


def main() -> int:
    lines = [
        visual_line("[가격 이동평균]", 1400),
        visual_line("20 :3.8282 (-52.57%)", 1450),
        visual_line("33 :3.8410 (-52.41%)", 1500),
        visual_line("60 :4.6713 (-42.12%)", 1550),
        visual_line("112 :6.0841 (-24.62%)", 1600),
        visual_line("[day60]", 1700),
        visual_line("60바닥 :4.6428", 1750),
        visual_line("[day112]", 1850),
        visual_line("벽112:15.8494", 1900),
        visual_line("바닥112:11.5854", 1950),
    ]
    items = collect_daily_items(lines, Decimal("8.0000"), image_width=800)
    values = {item.key: item.value for item in items if item.status == "valid"}

    assert values["moving_average_20_wall"] == Decimal("3.8282")
    assert values["moving_average_33_wall"] == Decimal("3.8410")
    assert values["moving_average_60_wall"] == Decimal("4.6713")
    assert values["moving_average_112_wall"] == Decimal("6.0841")
    assert values["day60_floor"] == Decimal("4.6428")
    assert values["day112_wall"] == Decimal("15.8494")
    assert values["day112_floor"] == Decimal("11.5854")
    assert Decimal("-52.57") not in values.values()
    assert Decimal("-52.41") not in values.values()
    assert Decimal("-42.12") not in values.values()
    assert Decimal("-24.62") not in values.values()

    # 실제 GIPR OCR 형태: 가격 열이 x=180 부근에서 시작하고 112 라벨/값이
    # 잡음 token 때문에 서로 다른 VisualLine으로 갈린 경우를 재현한다.
    gipr_lines = [
        VisualLine([OCRToken("[가격 이동평균]", 0.99, (8, 1514, 284, 1565))]),
        VisualLine(
            [
                OCRToken("20", 0.99, (21, 1561, 72, 1599)),
                OCRToken(":0.6010 (4.21%)", 0.98, (178, 1558, 480, 1602)),
            ]
        ),
        VisualLine([OCRToken("112", 0.99, (23, 1725, 89, 1769))]),
        VisualLine(
            [
                OCRToken(":2.0666", 0.96, (187, 1727, 346, 1769)),
                OCRToken("‘", 0.36, (333, 1738, 351, 1757)),
                OCRToken("(258.35%)", 0.99, (342, 1728, 515, 1770)),
            ]
        ),
        VisualLine([OCRToken("[day20]", 0.99, (8, 1850, 136, 1900))]),
        VisualLine([OCRToken("20 바닥 :2.7275", 0.95, (19, 1938, 339, 1980))]),
    ]
    gipr_items = collect_daily_items(
        gipr_lines, Decimal("0.5767"), image_width=792
    )
    gipr_values = {
        item.key: item.value for item in gipr_items if item.status == "valid"
    }
    assert gipr_values["moving_average_20_wall"] == Decimal("0.6010")
    assert gipr_values["moving_average_112_wall"] == Decimal("2.0666")
    assert "moving_average_2_wall" not in gipr_values
    assert gipr_values["day20_floor"] == Decimal("2.7275")

    # 실제 WHLR 실패 형태: 여는 대괄호가 빠진 섹션명은 복구하되 기간 숫자는
    # 허용된 값 및 다음 벽/바닥 라벨과 정확히 일치할 때만 사용한다.
    fuzzy_section_lines = [
        visual_line("가격 이동평균]", 1400),
        visual_line("33 :3.1784 (16.43%)", 1450),
        visual_line("30 :3.2000", 1500),
        visual_line("20", 1550),
        visual_line("20벽 :3.4000", 1600),
        visual_line("33", 1650),
        visual_line("30벽 :3.5000", 1700),
    ]
    fuzzy_items = collect_daily_items(
        fuzzy_section_lines, Decimal("2.7900"), image_width=800
    )
    fuzzy_values = {
        item.key: item.value for item in fuzzy_items if item.status == "valid"
    }
    assert fuzzy_values["moving_average_33_wall"] == Decimal("3.1784")
    assert fuzzy_values["day20_wall"] == Decimal("3.4000")
    assert "moving_average_30_wall" not in fuzzy_values
    assert "day33_wall" not in fuzzy_values
    assert canonical_section("가격 이동평]") == "가격 이동평균"
    assert canonical_section("dy20]") == "day20"
    assert canonical_section("[day30]") == "__other__"

    minute_items = collect_minute_items(
        [
            visual_line("[절대값]", 1800),
            visual_line("니 위에서 관문 터치하면 매도:17.1222", 1850),
        ],
        Decimal("23.9000"),
        image_width=800,
    )
    minute_values = {
        item.key: item.value for item in minute_items if item.status == "valid"
    }
    assert minute_values["buy_price"] == Decimal("17.1222")

    endgame = price_result("corpse_wall_4", "0.8334")
    endgame.item_text = "끝판왕"
    expanded = expand_endgame_wall_multipliers([endgame], Decimal("1.0000"))
    assert [item.raw_value for item in expanded] == [
        Decimal("0.8334"),
        Decimal("1.6668"),
        Decimal("2.5002"),
        Decimal("3.3336"),
        Decimal("4.1670"),
    ]
    assert all(item.status == "valid" for item in expanded)

    merged = merge_taecho_and_absolute_half(
        [price_result("taecho", "6.6526"), price_result("absolute_half", "6.6526")]
    )
    merged_values = {item.key: item.value for item in merged}
    assert merged_values == {"taecho": Decimal("6.6526")}

    print("[PARSER REGRESSION TEST] passed")
    print("moving_average: 3.8282, 3.8410, 4.6713, 6.0841")
    print("day60_floor: 4.6428")
    print("day112_wall: 15.8494")
    print("day112_floor: 11.5854")
    print("buy_price_inline: 17.1222")
    print("taecho: 6.6526")
    print("absolute_half: null")
    print("GIPR split-row parsing: 0.6010, 2.0666, 2.7275")
    print("fuzzy section recovery: WHLR 3.1784")
    print("exact period matching: verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
