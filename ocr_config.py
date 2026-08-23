"""OCR 테스트용 화면 영역 설정."""

# 전체 화면 기준 설명란 영역: (left, top, right, bottom)
# 기본값은 1920x1080 화면의 오른쪽 설명란을 가정한 테스트 값이다.
# 실제 화면 배치에 맞게 이 값만 수정하거나 실행 시 --crop 옵션을 사용한다.
DESCRIPTION_CROP_BOX = (850, 0, 1180, 950)

# crop 이미지는 프로젝트 아래 이 폴더에 저장된다.
CROP_OUTPUT_DIRECTORY = "ocr_crops"
