# 프로젝트 요구사항 명세

## 1. 프로젝트 목적

- Windows에서 영웅문 Global 차트 Tooltip 정보를 캡처하고 OCR하여 주식 매매 보조 UI에 표시한다.
- 초기 단계에서는 PySide6 UI 프로토타입과 가짜 데이터 기반 동작부터 개발한다.
- 이후 화면 캡처, OCR, AI 분석, SQLite 기록 기능을 단계적으로 추가한다.

## 2. 종목 카드 UI

### 상단 1/3

- 종목 코드, 현재 주가, ON/OFF 버튼을 표시한다.
- OFF는 미보유 또는 관심 종목, ON은 매수하여 보유 중인 종목을 뜻한다.

### 하단 2/3

- 현재가를 기준으로 움직이는 가격 영역을 제공한다.
- 현재가보다 높은 가격 중 가장 가까운 1개와 낮은 가격 중 가장 가까운 1개만 표시한다.
- 가격 종류는 매입가, 반등가, 벽이다.
- 현재가가 변하면 표시 대상과 현재가 대비 퍼센트를 즉시 다시 계산한다.
- 가격선은 실제 가격 차이에 비례해 배치하며 최소 간격을 강제하지 않는다.
- 가격 텍스트는 왼쪽, 현재가 텍스트는 오른쪽에 표시한다.

## 3. 알림

- OFF 상태에서 현재가가 매입가의 -5% 구간에 진입하면 카드 테두리를 점멸한다.
- 소리 알림은 사용하지 않는다.
- ON 상태에서는 매입가 접근 알림을 사용하지 않는다.

## 4. 향후 기능

- 마우스 오른쪽에 나타나는 영웅문 Tooltip 캡처
- 일봉/분봉 Tooltip 크기와 구성 차이 대응
- OCR 결과 파싱 및 AI 기반 구조화·보정
- SQLite 기록과 종목별 과거 분석 기록
- 설정 화면 및 Always on Top

## 5. 일봉 및 분봉 파싱 규칙

### 자동 판별

- OCR 텍스트에 `시체소굴`, `절대값half`, `절대값 half` 중 하나라도 있으면 분봉으로 판별한다.
- 세 키워드가 모두 없으면 일봉으로 판별한다.
- 대괄호 존재 여부는 판별 조건으로 강제하지 않는다.

### 일봉

다음 섹션에서 추출한 가격을 모두 벽 후보로 처리한다.

- `[가격 이동평균]`
- `[day20]`, `[day33]`, `[day60]`, `[day112]`, `[day224]`, `[day335]`

### 분봉

- `[시체소굴]` 가격은 벽으로 처리한다.
- `[절대값]`의 `니 위에서 관문 터치하면 매도`는 매입가로 처리한다.
- `[절대값]`의 `니 바닥으로 흐르면 매도`는 반등가로 처리한다.
- `[절대값]`의 `태초마을`은 태초마을로 처리한다.
- `[절대값half]` 가격은 절대값 half로 처리한다.

절대값 half와 태초마을의 차이가 태초마을 기준 1% 이하면 절대값 half를 별도로 표시하지 않고 태초마을로 통합한다.

`abs(절대값 half - 태초마을) / 태초마을 <= 0.01`

### 공통 가격 검증

- 가격이 0 이하면 제외한다.
- 가격이 현재가의 10배 이상이면 제외한다.
- 숫자를 임의로 추정하거나 보정하지 않는다.

## 6. PaddleOCR bbox 기반 추출 및 검증

### Tooltip 내부 좌표와 가격 열

- OCR token은 전체 화면이 아닌 전처리된 Tooltip crop 내부 좌표로 처리한다.
- 일봉과 분봉은 서로 다른 가격 열 X 범위와 유효 Y 범위를 설정으로 관리한다.
- 숫자 bbox 중심이 해당 차트의 허용 X/Y 범위 밖이면 가격 후보로 사용하지 않는다.
- 설명란 밖 숫자는 라벨과 가까워도 가격으로 연결하지 않는다.
- 좌표 범위는 자동 추측하지 않고 실제 Tooltip 크기에 맞춰 설정 파일에서 조정한다.

### 라벨과 가격의 동일 행 연결

- 가격은 항목 라벨과 같은 행이며 라벨 오른쪽에 있는 숫자만 연결한다.
- 같은 행 여부는 라벨 bbox와 숫자 bbox의 Y 중심 차이로 판단한다.
- 다른 행의 숫자는 거리와 관계없이 연결하지 않는다.
- 가격 이동평균 20/33/60/112/224/335와 day20/day33/day60/day112/day224/day335의 벽·바닥에 이 규칙을 적용한다.
- 분봉의 절대값 half, 매입가 문구, 반등가 문구, 태초마을 및 시체소굴 가격에도 같은 규칙을 적용한다.

### 의심값과 숫자 재판독

- confidence 미달, 숫자 파싱 실패, 0 이하, 현재가의 10배 이상 또는 대응 가격 미검출은 의심값이다.
- 전체 Tooltip OCR은 이미지당 한 번만 수행한다.
- 의심값만 선택 숫자 token bbox에 작은 여백을 더해 별도 crop한다.
- 숫자 crop에는 4~6배 확대, grayscale 및 CLAHE 대비 강화를 적용하고 PaddleOCR로 재판독한다.
- 재판독 결과가 유효하고 confidence 기준을 통과할 때만 값을 교체한다.
- 계속 불확실하면 추정하지 않고 `null`과 `uncertain` 또는 `invalid`로 처리한다.
- 동일 가격은 중복 제거한다.

### 종목코드와 종목명

- Tooltip 상단에서 종목코드와 종목명을 추출한다.
- 종목코드는 괄호 안 영문 티커를 우선하며, 괄호가 누락되면 상단의 영문 대문자 티커 후보를 보조적으로 사용한다.
- 종목명은 한글 이름을 우선하고 내부 데이터에는 전체 이름을 저장한다.
- `display_name`은 UI 표시용으로만 앞 6글자를 사용하며 저장된 `stock_name`은 자르지 않는다.

### 디버그 결과

- 항목별 label text/bbox, 선택 가격 text/bbox, value, confidence, source 및 status를 출력한다.
- Tooltip 이미지에 차트별 허용 가격 영역, 라벨 bbox 및 선택 가격 bbox를 표시한 디버그 이미지를 저장한다.

## 7. 토스증권 Open API 현재가

- 실제 현재가는 Tooltip의 시가·고가·저가·종가를 사용하지 않고 토스증권 Open API에서 조회한다.
- OCR에서 추출한 `stock_code`를 `/api/v1/prices`의 `symbols` 파라미터로 사용한다.
- 응답 `result[]`에서 해당 symbol의 `lastPrice`를 현재가로 사용한다.
- OAuth2 Client Credentials로 발급한 access token을 캐시하고 만료 시 재발급한다.
- Client ID와 Client Secret은 코드에 저장하지 않고 프로젝트 루트 `.env`에서 읽는다.
- API 현재가를 가져오지 못하면 Tooltip 종가로 대체하지 않고 `current_price`를 `unavailable`로 처리한다.
- 현재가의 10배 이상을 제외하는 필터는 API 현재가가 제공됐을 때 그 값을 기준으로 적용한다.
- OCR 모듈은 외부에서 받은 현재가를 주입할 수 있게 하며, OCR 단독 테스트 중에는 API를 자동 호출하지 않는다.
- Tooltip의 시가·고가·저가·종가는 실시간 현재가가 아니며 `current_price` 또는 API 실패 fallback으로 절대 사용하지 않는다.
- OCR에서 얻은 `stock_code`는 종목 데이터에 계속 저장하고 이후 모든 현재가 갱신에서 API `symbol`로 다시 사용한다.
- 등록된 여러 종목의 symbol은 현재가 갱신 때마다 목록을 새로 만들어 하나의 다중 symbol API 요청으로 전달한다.
- 응답 가격은 symbol별 종목 데이터에 독립적으로 매핑하고, 응답에서 누락된 종목은 기존 값을 보존하면서 unavailable로 표시한다.
- 현재 프로그램의 종목 데이터와 UI에는 API 응답의 currency를 저장하거나 표시하지 않는다.

## 8. 여러 종목 데이터와 현재가 갱신 흐름

- 한 종목은 종목코드, 전체 종목명, 6글자 표시명, 현재가, 매입가, 반등가, 태초마을, 절대값 half, 벽 목록, 보유 여부 및 마지막 가격 갱신 시각을 독립적으로 보관한다.
- 종목 저장소는 여러 종목을 종목코드로 관리한다.
- 현재가 갱신 흐름은 `등록 종목 → symbol 목록 → Toss API 다중 조회 → symbol별 lastPrice 매핑 → 종목 current_price 갱신`이다.
- 갱신 함수는 단발성 동기 메서드로 제공하며 자동 polling은 하지 않는다.
- 향후 PySide6 worker thread 또는 QThread에서 갱신 함수를 호출할 수 있도록 UI 객체에 의존하지 않는다.
- API 현재가가 확보된 후 그 값을 OCR 가격의 10배 필터 기준으로 주입한다.
- 정상 파싱되고 confidence 기준을 통과한 1차 OCR 값의 유일한 탈락 사유가 `price_over_max_multiplier`이면 숫자 재판독을 수행하지 않는다.
- 이 경우 OCR 오류가 아닌 비즈니스 규칙 제외로 구분하여 `filtered / primary_ocr` 상태로 처리한다.
- 필터링된 후보 `value`는 null로 만들되 `selected_value_text`와 `raw_value`에는 1차 OCR 값을 보존한다.

## 9. OCR 분석 결과 병합

- `StockRegistry`는 정규화된 `stock_code`를 키로 종목을 관리한다.
- daily와 minute OCR 결과의 `stock_code`가 같으면 하나의 `StockRecord`에 병합한다.
- daily 결과는 `daily_values`와 `daily_price_candidates`, minute 결과는 `minute_values`와 `minute_walls`로 출처를 구분해 저장한다.
- UI용 통합 후보가 필요할 때만 daily 후보, minute 벽, 태초마을, 매입가 및 반등가를 조합한다.
- 같은 chart type을 다시 분석하면 해당 chart type의 저장 영역만 최신 결과로 교체한다.
- 다른 chart type의 데이터와 API 현재가 상태는 유지한다.
- 정상적인 새 종목명은 갱신할 수 있지만 빈 종목명으로 기존 이름을 덮어쓰지 않는다.
- 향후 F8 캡처 결과도 `capture → OCR → analyze → merge_analysis_result → refresh_current_prices → UI` 경계를 재사용한다.
- 저장 이미지 기반 병합 테스트 단계에서는 글로벌 F8 단축키와 실시간 화면 캡처를 사용하지 않는다.

## 10. Windows 글로벌 F8 Tooltip 캡처

- 실제 입력 흐름은 `영웅문Global Tooltip → mouse hover → F8 → 마우스 상대 ROI 캡처 → OCR → daily/minute 판별 → Toss 현재가 → 검증 → StockRegistry 병합`이다.
- Windows native `RegisterHotKey`로 프로그램 포커스와 관계없이 F8 하나만 등록하고 종료 시 반드시 해제한다.
- F8 반복 입력은 `MOD_NOREPEAT`로 억제한다.
- F8 이벤트 순간 마우스 위치와 해당 모니터 bounds를 얻고, 설정 파일의 상대 offset으로 ROI를 계산한다.
- ROI는 마우스가 있는 모니터 경계 안에서 원래 크기를 최대한 유지하도록 이동·clamp하며 음수 좌표 보조 모니터도 지원한다.
- F8 순간 화면 캡처는 즉시 수행하고, 캡처 후 OCR과 API 호출은 단일 FIFO worker queue에서 순차 처리한다.
- worker는 UI widget을 직접 수정하지 않으며 완료 callback을 통해 향후 Qt signal/slot에 연결할 수 있다.
- 실시간 원본 ROI 저장 여부와 OCR 전처리 설정은 별도 설정 파일에서 관리한다.
- 저장 이미지 테스트와 실시간 캡처는 동일한 `capture_image`, `analyze_capture`, `merge_analysis_result` 흐름을 사용한다.
- stock_code 또는 필수 Tooltip 섹션을 찾지 못하면 Registry에 종목을 생성하지 않고 해당 작업만 실패 처리한다.
- API 실패 시 Tooltip 종가 fallback을 사용하지 않으며 기존 정상 현재가는 덮어쓰지 않는다.
- 이 단계에서는 카드 자동 생성과 글로벌 핫키의 UI 통합은 수행하지 않는다.

### Live Tooltip 입력 정제

- live 입력은 `mouse-relative raw ROI → 오른쪽 20% 제거 → tooltip-only ROI → 전처리 → PaddleOCR → parser` 순서로 처리한다.
- 유지할 가로 비율은 `TOOLTIP_KEEP_WIDTH_RATIO` 설정으로 관리하며 기본값은 `0.80`이다.
- 캡처 보존 설정이 활성화되면 전체 raw ROI, tooltip-only ROI 및 tooltip 기준 전처리 이미지를 구분해 저장한다.
- parser는 라벨과 가격이 같은 OCR token에 포함된 경우와 라벨·가격이 별도 token인 경우를 모두 처리한다.
- inline token의 숫자는 OCR 문자열에 실제 존재하는 숫자 substring만 사용하며 추측하거나 교정하지 않는다.
- 시체소굴 가격은 해당 섹션 시작부터 `끝판왕` 행까지로 제한하고, 끝판왕 이후 값은 벽 후보로 사용하지 않는다.
