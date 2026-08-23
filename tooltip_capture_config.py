"""마우스 기준 실시간 Tooltip 캡처 설정."""

# 실제 화면 픽셀 기준: (mouse_x + offset, mouse_y + offset)
ROI_LEFT_OFFSET = 20
ROI_TOP_OFFSET = -475
ROI_RIGHT_OFFSET = 350
ROI_BOTTOM_OFFSET = 475

# 원본 ROI의 왼쪽 80%만 Tooltip 설명란으로 사용한다.
TOOLTIP_KEEP_WIDTH_RATIO = 0.80

# True이면 원본 ROI와 OCR 전처리 이미지를 ocr_results에 보존한다.
SAVE_LIVE_CAPTURE = True

# 기존 OCR 입력과 동일한 전처리 설정
LIVE_OCR_SCALE = 3
LIVE_CLAHE_CLIP_LIMIT = 2.0
LIVE_CLAHE_TILE_GRID_SIZE = (8, 8)
