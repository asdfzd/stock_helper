from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PADDLE_CACHE = PROJECT_ROOT / ".paddle-cache"
PADDLE_HOME = PADDLE_CACHE / "home"
PADDLE_HOME.mkdir(parents=True, exist_ok=True)

# Paddle/PaddleX가 사용자 프로필 대신 프로젝트 내부에 캐시와 모델을 저장하게 한다.
os.environ["USERPROFILE"] = str(PADDLE_HOME)
os.environ["PADDLE_PDX_CACHE_HOME"] = str(PADDLE_CACHE)
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

from paddleocr import PaddleOCR  # noqa: E402 - 캐시 환경변수 설정 후 import


INPUTS = {
    "daily": PROJECT_ROOT / "ocr_crops" / "1_daily_crop_preprocessed.png",
    "minute": PROJECT_ROOT / "ocr_crops" / "2_minute_crop_preprocessed.png",
}
RESULT_DIRECTORY = PROJECT_ROOT / "ocr_results"


def main() -> int:
    RESULT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        reader = PaddleOCR(
            lang="korean",
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="korean_PP-OCRv5_mobile_rec",
            ocr_version="PP-OCRv5",
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

        for name, image_path in INPUTS.items():
            if not image_path.is_file():
                raise FileNotFoundError(f"전처리 이미지를 찾을 수 없습니다: {image_path}")

            lines: list[str] = []
            for result in reader.predict(str(image_path)):
                lines.extend(str(text) for text in result["rec_texts"])
            raw_text = "\n".join(lines)
            output_path = RESULT_DIRECTORY / f"{name}_paddleocr.txt"
            output_path.write_text(raw_text + "\n", encoding="utf-8")

            print(f"\n=== {name} / PaddleOCR ===")
            print(raw_text)
            print(f"saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
