from __future__ import annotations

import warnings
from pathlib import Path

import easyocr


PROJECT_ROOT = Path(__file__).resolve().parent
INPUTS = {
    "daily": PROJECT_ROOT / "ocr_crops" / "1_daily_crop_preprocessed.png",
    "minute": PROJECT_ROOT / "ocr_crops" / "2_minute_crop_preprocessed.png",
}
RESULT_DIRECTORY = PROJECT_ROOT / "ocr_results"


def main() -> int:
    RESULT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        reader = easyocr.Reader(
            ["ko", "en"],
            gpu=False,
            model_storage_directory=str(PROJECT_ROOT / ".easyocr-models"),
            verbose=False,
        )

        for name, image_path in INPUTS.items():
            if not image_path.is_file():
                raise FileNotFoundError(f"전처리 이미지를 찾을 수 없습니다: {image_path}")
            lines = reader.readtext(str(image_path), detail=0, paragraph=False)
            raw_text = "\n".join(str(line) for line in lines)
            output_path = RESULT_DIRECTORY / f"{name}_easyocr.txt"
            output_path.write_text(raw_text + "\n", encoding="utf-8")

            print(f"\n=== {name} / EasyOCR ===")
            print(raw_text)
            print(f"saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
