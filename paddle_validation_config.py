"""PaddleOCR 가격 검증 테스트 설정."""

OCR_CONFIDENCE_THRESHOLD = 0.85
PRICE_MAX_MULTIPLIER = 10
NUMERIC_CROP_PADDING = 12
NUMERIC_CROP_SCALE = 5

# Paddle inference가 UI와 CPU를 과도하게 경쟁하지 않도록 제한한다.
# 장비별 비교 테스트 시 2, 4, 6 등으로 이 값만 변경한다.
PADDLE_CPU_THREADS = 4

# Prefer the first NVIDIA GPU.  paddle_ocr_validation falls back to CPU only
# when the installed Paddle backend cannot expose a CUDA device.
PADDLE_DEVICE = "gpu:0"

# 좌표는 3배 확대된 Tooltip crop 이미지 내부 기준이다. 가격 후보의 숫자 bbox
# 중심이 이 범위 밖이면 라벨과 가까워도 절대 선택하지 않는다.
DAILY_VALUE_X_MIN = 170
DAILY_VALUE_X_MAX = 720
DAILY_VALUE_Y_MIN = 1400
DAILY_VALUE_Y_MAX = 2450
# 오른쪽 20%가 제거된 live Tooltip 설명란의 실제 가격 열 범위.
MINUTE_VALUE_X_MIN = 140
MINUTE_VALUE_X_MAX = 760
MINUTE_VALUE_Y_MIN = 400
MINUTE_VALUE_Y_MAX = 2300

# 라벨과 값이 서로 다른 OCR token일 때 같은 행으로 인정할 Y 중심 차이(px).
ROW_CENTER_Y_TOLERANCE = 28

# 숫자 bbox 자체에만 적용할 작은 재판독 여백. X/Y를 따로 두어 인접 행과
# 설명란 바깥 문자가 crop에 들어오는 것을 줄인다.
NUMERIC_CROP_PADDING_X = 5
NUMERIC_CROP_PADDING_Y = 4

# 종목 정보는 Tooltip crop 상단에서만 찾는다.
STOCK_HEADER_Y_MAX = 450
STOCK_HEADER_LOOKBACK = 500

# 항목명은 찾았지만 숫자 박스가 없을 때 오른쪽에서 재판독할 기본 폭
MISSING_NUMERIC_REGION_WIDTH = 300

# 같은 시각적 행으로 묶을 bounding box 중심 Y 좌표 허용 비율
LINE_GROUP_Y_TOLERANCE = 0.55
LINE_GROUP_MAX_HORIZONTAL_GAP = 180
