from __future__ import annotations

from paddle_ocr_validation import validate_tooltip_content


def assert_validation(text: str, valid: bool, chart_type: str | None) -> None:
    result = validate_tooltip_content(text)
    assert result.valid is valid, result
    assert result.chart_type == chart_type, result


def main() -> int:
    daic_daily = """
일자
CID HOLDCO INC CID 홀드코(DAIC)
매집봉
매집봉 강세패턴
가격 이동평균
20 : 0.7426
33 : 1.0061
가격 이동평균
60 : 1.5472
112 : 3.0933
224 : 17.8891
day20
20 벽 : 5.2805
20 바닥 : 0.6856
day 33
33 바닥 : 0.9909
day60
day112
day224
day335
"""
    assert_validation(daic_daily, True, "daily")

    partial_brackets = "가격 이동평균]\nday20]\n[day33\nday60"
    assert_validation(partial_brackets, True, "daily")

    ordinary_hts = """
차트
투자정보
매수
매도
250일최고
250일최저
최신투자의견
현재가
종목명
"""
    assert_validation(ordinary_hts, False, None)

    minute = """
절대값half
절대값
니 위에서 관문 터치하면 매도
니 바닥으로 흐르면 매도
태초마을
시체소굴
"""
    assert_validation(minute, True, "minute")

    assert_validation("day20", False, None)
    assert_validation("매집봉", False, None)

    print("[TOOLTIP VALIDATION TEST] passed")
    print("DAIC daily without brackets: valid/daily")
    print("partial brackets: valid/daily")
    print("ordinary HTS text: invalid")
    print("XPON-like minute: valid/minute")
    print("single daily keyword: invalid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
