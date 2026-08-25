"""마우스 기준 실시간 Tooltip 캡처 설정."""

# 실제 화면 픽셀 기준: (mouse_x + offset, mouse_y + offset)
ROI_LEFT_OFFSET = 0
ROI_TOP_OFFSET = -475
ROI_RIGHT_OFFSET = 330
ROI_BOTTOM_OFFSET = 475

# 원본 ROI에서 Tooltip 경계를 찾을 때 사용할 최대 가로 비율.
TOOLTIP_KEEP_WIDTH_RATIO = 0.8

# 동적 오른쪽 경계 후보는 최소 이 비율보다 넓어야 한다. 값을 높이면 과도한
# crop은 줄지만 짧은 일봉 Tooltip 경계를 놓칠 수 있다.
TOOLTIP_EDGE_MIN_WIDTH_RATIO = 0.55

# 인접 grayscale 열의 평균 밝기 차이가 이 값 이상일 때만 경계로 인정한다.
# 값을 높이면 검출이 보수적이고, 낮추면 내부 문자/차트를 경계로 오인할 수 있다.
TOOLTIP_EDGE_MIN_COLUMN_DIFF = 50.0

# 검출 경계 오른쪽에 남길 안전 여백. 필요한 오른쪽 글자/숫자 절단을 방지한다.
TOOLTIP_EDGE_MARGIN_PX = 2

# True이면 원본 ROI와 OCR 전처리 이미지를 ocr_results에 보존한다.
SAVE_LIVE_CAPTURE = True

# 기존 OCR 입력과 동일한 전처리 설정
LIVE_OCR_SCALE = 3
LIVE_CLAHE_CLIP_LIMIT = 2.0
LIVE_CLAHE_TILE_GRID_SIZE = (8, 8)

# 프로그램 시작 시 마우스 주변에서 ticker token을 찾는 임시 화면 영역.
TICKER_SEARCH_WIDTH = 160
TICKER_SEARCH_HEIGHT = 60

# 검출된 ticker bbox에 더해 고정 screen ROI로 저장할 작은 여백.
TICKER_ROI_MARGIN_LEFT = 4
TICKER_ROI_MARGIN_RIGHT = 4
TICKER_ROI_MARGIN_TOP = 3
TICKER_ROI_MARGIN_BOTTOM = 3

# 작은 ticker 전용 crop은 Tooltip과 별도로 가볍게 확대/대비 강화한다.
TICKER_OCR_SCALE = 3
TICKER_OCR_CONFIDENCE_THRESHOLD = 0.75
TICKER_MOUSE_MAX_DISTANCE = 24

# True이면 calibration을 생략하고 아래 screen absolute ROI를 즉시 사용한다.
# False이면 기존처럼 첫 백틱으로 ticker 위치를 다시 지정한다.
USE_FIXED_TICKER_ROI = True
TICKER_FIXED_ROI = (85, 114, 121, 135)
