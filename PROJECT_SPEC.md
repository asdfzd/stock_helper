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

### Tooltip 존재 여부 검증

- PaddleOCR에서 섹션의 왼쪽 또는 오른쪽 대괄호가 누락될 수 있으므로, Tooltip 검증은 `[]` 완전 일치에 의존하지 않는다.
- 검증 시 대소문자와 공백을 정규화한다. 예를 들어 `day 33`과 `day33`은 같은 evidence로 취급하지만 OCR 문자를 임의로 추정하거나 보정하지 않는다.
- 분봉은 `시체소굴`, `절대값half`, `절대값 half` 중 하나가 있으면 충분한 evidence로 판정한다.
- 일봉은 다음 중 하나를 만족해야 한다: `가격 이동평균`과 하나 이상의 `dayXX`, `매집봉`과 하나 이상의 `dayXX`, 또는 서로 다른 `dayXX` 섹션 두 개 이상.
- 위 section evidence 없이 일반 HTS UI 문구만 있는 화면은 `tooltip_content_not_found`로 거부한다.
- Tooltip 유효성은 종목 identity/ticker resolution과 분리한다. 유효한 Tooltip의 ticker를 자동 확정하지 못해도 분석 결과를 unresolved pending으로 보존한다.

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
- `끝판왕` OCR 가격을 1배 벽으로 보고 2배, 3배, 4배, 5배 가격을 추가로 계산하여 끝판왕 계열 벽을 총 5개 생성한다.
- 계산된 끝판왕 배수 벽에도 0 이하 제외 및 현재가 10배 이상 제외 규칙을 동일하게 적용하며, 필터링된 배수는 UI 벽 후보로 사용하지 않는다.
- `[절대값]`의 `니 위에서 관문 터치하면 매도`는 매입가로 처리한다.
- `[절대값]`의 `니 바닥으로 흐르면 매도`는 반등가로 처리한다.
- `[절대값]`의 `태초마을`은 태초마을로 처리한다.
- `[절대값half]` 가격은 절대값 half로 처리한다.

절대값 half와 태초마을의 차이가 태초마을 기준 1% 이하면 절대값 half를 별도로 표시하지 않고 태초마을로 통합한다.

`abs(절대값 half - 태초마을) / 태초마을 <= 0.01`

- 통합 시 `taecho`를 유지하고 `absolute_half`만 제거한다.
- 예를 들어 두 값이 모두 `6.6526`이면 최종 `taecho = 6.6526`, `absolute_half = null`이다.

### 일봉 inline 숫자

- 가격 이동평균 행이 `20 :3.8282 (-52.57%)`처럼 인식되면 `:` 뒤 첫 번째 명확한 숫자인 `3.8282`를 가격으로 사용한다.
- 괄호 안 현재가 대비 퍼센트는 가격 후보로 사용하지 않는다.
- `60바닥 :4.6428`, `벽112:15.8494`, `바닥112:11.5854`처럼 라벨과 숫자가 하나의 OCR token이면 같은 token의 `:` 뒤 숫자를 해당 벽/바닥 값으로 사용한다.
- OCR 문자열에 없는 숫자를 추측하거나 자리수를 보정하지 않는다.

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

- ticker calibration 기능은 유지하며 fixed ROI가 비활성화된 실행에서는 첫 백틱 입력으로 마우스 주변의 ticker 위치를 찾는다.
- `USE_FIXED_TICKER_ROI = True`이면 시작 calibration을 생략하고 `TICKER_FIXED_ROI`의 screen absolute 좌표로 즉시 `CAPTURE_READY` 상태가 된다. 현재 테스트 기본값은 `(85, 114, 121, 135)`이다.
- `USE_FIXED_TICKER_ROI = False`이면 기존 `TICKER_CALIBRATION` 상태와 안내 문구를 사용해 위치를 다시 지정한다.
- calibration은 `TICKER_SEARCH_WIDTH = 160`, `TICKER_SEARCH_HEIGHT = 60`의 임시 search ROI를 OCR하고, 마우스와 겹치거나 가까운 1~4자 영문 대문자 token을 선택한다.
- 검출 bbox를 screen absolute 좌표로 변환한 뒤 좌/우 4px, 상/하 3px margin을 더한 고정 ticker ROI를 현재 실행 세션에만 저장한다.
- calibration 성공 후 매 백틱 입력마다 고정 ticker ROI와 마우스 상대 Tooltip ROI를 같은 callback에서 즉시 캡처하여 하나의 `CaptureTask`에 함께 저장한다.
- 고정되는 것은 화면 좌표뿐이며 ticker 문자열은 매 capture마다 전용 ROI를 다시 OCR한다. ticker 형식은 trim/uppercase 후 `^[A-Z]{1,4}$`만 허용하고 문자를 추측하거나 보정하지 않는다.
- Tooltip의 회사명, 한글 종목명 및 괄호 ticker hint는 표시 정보로 보존할 수 있지만 Registry ticker key 결정에는 사용하지 않는다.
- 회사명 기반 Toss `/stocks/all` catalog resolver는 live ticker 결정 경로에서 호출하지 않는다. live ticker는 전용 ROI OCR을 1순위, 사용자 수동 입력을 2순위로 사용한다.
- 전용 ticker ROI OCR이 실패하거나 confidence 기준에 미달하면 `ticker unresolved`로 보존한다.
- unresolved capture는 OCR/parser 결과를 capture ID별 pending snapshot으로 유지하며 Registry에 잘못된 임시 ticker를 만들지 않는다.
- `티커명 직접 입력` UI와 수동 ticker 검증·연결 기능은 사용하지 않는다.
- ticker가 unresolved인 캡처는 디버그 로그와 pending 분석 정보만 보존하고 UI 종목 카드나 Registry record를 생성하지 않는다.
- 종목명은 한글 이름을 우선하고 내부 데이터에는 전체 이름을 저장한다.
- `display_name`은 UI 표시용으로만 앞 6글자를 사용하며 저장된 `stock_name`은 자르지 않는다.

### 디버그 결과

- 항목별 label text/bbox, 선택 가격 text/bbox, value, confidence, source 및 status를 출력한다.
- Tooltip 이미지에 차트별 허용 가격 영역, 라벨 bbox 및 선택 가격 bbox를 표시한 디버그 이미지를 저장한다.

## 7. 토스증권 Open API 현재가

- 실제 현재가는 Tooltip의 시가·고가·저가·종가를 사용하지 않고 토스증권 Open API에서 조회한다.
- 전용 고정 ticker ROI에서 추출한 `stock_code`를 `/api/v1/prices`의 `symbols` 파라미터로 사용한다.
- 응답 `result[]`에서 해당 symbol의 `lastPrice`를 현재가로 사용한다.
- OAuth2 Client Credentials로 발급한 access token을 캐시하고 만료 시 재발급한다.
- Client ID와 Client Secret은 코드에 저장하지 않고 프로젝트 루트 `.env`에서 읽는다.
- API 현재가를 가져오지 못하면 Tooltip 종가로 대체하지 않고 `current_price`를 `unavailable`로 처리한다.
- 현재가의 10배 이상을 제외하는 필터는 API 현재가가 제공됐을 때 그 값을 기준으로 적용한다.
- OCR 모듈은 외부에서 받은 현재가를 주입할 수 있게 하며, OCR 단독 테스트 중에는 API를 자동 호출하지 않는다.
- Tooltip의 시가·고가·저가·종가는 실시간 현재가가 아니며 `current_price` 또는 API 실패 fallback으로 절대 사용하지 않는다.
- 전용 ticker ROI OCR 또는 검증된 수동 입력에서 얻은 `stock_code`는 종목 데이터에 저장하고 이후 모든 현재가 갱신에서 API `symbol`로 다시 사용한다.
- 등록된 여러 종목의 symbol은 현재가 갱신 때마다 목록을 새로 만들어 하나의 다중 symbol API 요청으로 전달한다.
- 응답 가격은 symbol별 종목 데이터에 독립적으로 매핑하고, 응답에서 누락된 종목은 기존 값을 보존하면서 unavailable로 표시한다.
- 현재 프로그램의 종목 데이터와 UI에는 API 응답의 currency를 저장하거나 표시하지 않는다.
- 현재가는 OCR worker와 독립된 price worker가 기본 2초마다 `/api/v1/prices`의 최대 200 symbol batch 조회로 갱신한다.
- Live UI에서는 `ENABLE_REALTIME_PRICE_POLLING = True`로 반복 polling을 활성화한다. 필요하면 설정을 `False`로 바꿔 Capture 직후 10배 필터용 단발 현재가 조회만 유지할 수 있다.
- 단일 price worker는 이전 요청 완료 후 다음 interval을 기다려 polling 요청 중복을 방지한다.
- API 일시 실패 시 last good `current_price`와 timestamp를 보존하고 `price_status = stale` 및 error를 별도로 기록한다. 성공 이력이 없을 때만 unavailable이다.

## 8. 여러 종목 데이터와 현재가 갱신 흐름

- 한 종목은 종목코드, 전체 종목명, 6글자 표시명, 현재가, 매입가, 반등가, 태초마을, 절대값 half, 벽 목록, 보유 여부 및 마지막 가격 갱신 시각을 독립적으로 보관한다.
- 종목 저장소는 여러 종목을 종목코드로 관리한다.
- 현재가 갱신 흐름은 `등록 종목 → symbol 목록 → Toss API 다중 조회 → symbol별 lastPrice 매핑 → 종목 current_price 갱신`이다.
- Registry 갱신 함수는 단발성 동기 메서드이며 별도 background price worker가 이를 주기적으로 호출한다.
- 향후 PySide6 worker thread 또는 QThread에서 갱신 함수를 호출할 수 있도록 UI 객체에 의존하지 않는다.
- API 현재가가 확보된 후 그 값을 OCR 가격의 10배 필터 기준으로 주입한다.
- 정상 파싱되고 confidence 기준을 통과한 1차 OCR 값의 유일한 탈락 사유가 `price_over_max_multiplier`이면 숫자 재판독을 수행하지 않는다.
- 이 경우 OCR 오류가 아닌 비즈니스 규칙 제외로 구분하여 `filtered / primary_ocr` 상태로 처리한다.
- 필터링된 후보 `value`는 null로 만들되 `selected_value_text`와 `raw_value`에는 1차 OCR 값을 보존한다.

## 9. OCR 분석 결과 병합

- `StockRegistry`는 정규화된 ticker(`stock_code`)마다 하나의 `StockRecord`를 관리하고, 그 안에 daily snapshot과 minute snapshot을 독립적으로 보관한다.
- ticker마다 UI 카드도 하나만 생성하며 같은 ticker의 후속 daily/minute 캡처는 기존 카드를 갱신한다.
- daily와 minute OCR 결과의 `stock_code`가 같으면 하나의 `StockRecord`에 병합한다.
- daily 결과는 `daily_values`와 `daily_price_candidates`, minute 결과는 `minute_values`와 `minute_walls`로 출처를 구분해 저장한다.
- UI용 통합 후보가 필요할 때만 daily 후보, minute 벽, 태초마을, 매입가 및 반등가를 조합한다.
- 새 daily 분석은 `daily_values`, `daily_price_candidates`, `daily_loaded`로 구성된 기존 daily snapshot 전체를 교체하고 minute snapshot은 유지한다.
- 새 minute 분석은 `minute_values`, `minute_walls`, `buy_price`, `rebound_price`, `taecho`, `absolute_half`, `minute_loaded`로 구성된 기존 minute snapshot 전체를 교체하고 daily snapshot은 유지한다.
- snapshot 갱신은 누적·append·union이 아니다. 특히 `minute_walls`는 새 minute 결과만 남으며, 새 결과에 없는 단일 가격은 이전 값으로 보충하지 않고 `null`이 된다.
- current price와 마지막 API 가격 상태는 indicator snapshot과 별개이므로 daily/minute 재캡처로 초기화하지 않는다.
- 다른 chart type의 데이터와 API 현재가 상태는 유지한다.
- 정상적인 새 종목명은 갱신할 수 있지만 빈 종목명으로 기존 이름을 덮어쓰지 않는다.
- 백틱 캡처 결과도 `capture → OCR → analyze → merge_analysis_result → refresh_current_prices → UI` 경계를 재사용한다.
- UI는 별도 daily/minute 상태를 소유하지 않고 thread-safe Registry snapshot을 읽어 최신 화면을 만든다.
- ticker 최초 Registry 등록 순서가 카드 배치 순서이며, 재캡처는 카드 위치를 바꾸지 않는다.
- 저장 이미지 기반 병합 테스트 단계에서는 글로벌 백틱 단축키와 실시간 화면 캡처를 사용하지 않는다.

## 10. Windows 글로벌 백틱 Tooltip 캡처

- fixed ROI 활성화 시 시작 상태는 즉시 `CAPTURE_READY`다. 비활성화 시에만 `TICKER_CALIBRATION`으로 시작하고 UI에 "티커명 위치에서 백틱 키를 눌러주세요"를 표시한 뒤 성공 시 `CAPTURE_READY`로 전환한다.
- 실제 capture 흐름은 `고정 ticker ROI capture + 영웅문Global Tooltip mouse hover → 백틱 → 마우스 상대 ROI capture → queue → ticker OCR → Tooltip OCR/parser → Toss 현재가 → StockRegistry 병합`이다.
- Windows native `RegisterHotKey`로 백틱(`VK_OEM_3`) 하나만 modifier 없이 등록하고 종료 시 반드시 해제한다. `0`, `-`, `=`은 등록하지 않는다.
- 백틱 반복 입력은 `MOD_NOREPEAT`로 억제한다.
- 백틱 이벤트 순간 ticker ROI와 Tooltip ROI를 포함하는 화면을 한 번만 grab하고, 메모리에서 두 ROI를 분리해 같은 `CaptureTask`에 넣는다.
- hotkey callback은 메모리 이미지 확보 직후 queue에 등록한다. Tooltip 경계 탐지, NumPy 변환 및 PNG 저장은 OCR worker에서 수행하여 파일 저장 때문에 캡처 순간이 늦어지지 않게 한다.
- worker 대기 중 HTS 화면이 바뀌어도 queue에는 백틱 순간의 메모리 이미지가 보존되어 서로 다른 종목 이미지가 섞이지 않는다.
- Tooltip용 마우스 위치와 해당 모니터 bounds를 얻고 설정 파일의 상대 offset으로 ROI를 계산한다.
- ROI는 마우스가 있는 모니터 경계 안에서 원래 크기를 최대한 유지하도록 이동·clamp하며 음수 좌표 보조 모니터도 지원한다.
- 백틱 순간 화면 캡처는 즉시 수행하고, 캡처 후 OCR과 API 호출은 단일 FIFO worker queue에서 순차 처리한다.
- worker는 UI widget을 직접 수정하지 않는다. 완료 callback은 ticker만 Qt signal로 전달하고 GUI thread의 slot이 Registry snapshot을 읽어 카드를 생성하거나 갱신한다.
- 실시간 원본 ROI 저장 여부와 OCR 전처리 설정은 별도 설정 파일에서 관리한다.
- 저장 이미지 테스트와 실시간 캡처는 동일한 `capture_image`, `analyze_capture`, `merge_analysis_result` 흐름을 사용한다.
- 필수 Tooltip 섹션을 찾지 못하면 해당 작업을 실패 처리한다. Tooltip은 정상이나 ticker ROI OCR만 실패하면 분석 snapshot을 pending으로 보존하고 Registry에는 아직 병합하지 않는다.
- API 실패 시 Tooltip 종가 fallback을 사용하지 않으며 기존 정상 현재가는 덮어쓰지 않는다.
- live UI에서는 글로벌 핫키 캡처, FIFO OCR worker, Registry 병합 및 카드 자동 갱신을 하나의 프로세스로 실행한다.

### Live Tooltip 입력 정제

- live 입력은 `mouse-relative RAW ROI → 최대 유지 비율 0.80 → 경량 Tooltip 오른쪽 경계 탐지 → 최종 tooltip-only crop → 전처리 → PaddleOCR → chart 판별 → parser → Toss API → StockRegistry` 순서로 처리한다.
- RAW ROI의 가로 범위는 `ROI_LEFT_OFFSET = 0`부터 `ROI_RIGHT_OFFSET = 330`까지이며, `mouse_x ~ mouse_x + 330`의 330px 폭을 사용한다.
- `TOOLTIP_KEEP_WIDTH_RATIO = 0.80`은 RAW 폭에서 경계 탐지와 OCR에 사용할 최대 범위다. 현재 사용자가 조정한 코드값을 유지한다.
- `TOOLTIP_KEEP_WIDTH_RATIO`를 낮추면 호가창 오탐과 OCR 입력 폭이 줄지만 buy price 등 오른쪽 숫자가 잘릴 위험이 커진다. 높이면 값 절단 위험은 줄지만 HTS 숫자와 OCR 처리량이 늘어난다. 실사용 기본값은 `0.80`이다.
- 최대 범위 안에서 인접 grayscale 열의 평균 밝기 차이로 Tooltip 오른쪽 경계를 찾는다. 신뢰 가능한 경계가 없으면 최대 0.80 crop을 그대로 사용하는 fail-safe를 적용한다.
- `TOOLTIP_EDGE_MIN_WIDTH_RATIO = 0.55`는 동적 crop의 최소 허용 폭이다. 높이면 과도한 crop은 줄지만 짧은 일봉 경계를 놓칠 수 있고, 낮추면 내부 문자를 경계로 오인할 수 있다. 기본값은 `0.55`다.
- `TOOLTIP_EDGE_MIN_COLUMN_DIFF = 50.0`은 경계로 인정할 최소 평균 열 밝기 차이다. 높이면 fallback이 늘고, 낮추면 잘못된 조기 crop이 늘 수 있다. 기본값은 `50.0`이다.
- `TOOLTIP_EDGE_MARGIN_PX = 2`는 검출 경계 오른쪽의 안전 여백이다. 높이면 글자 절단 위험과 입력 폭이 함께 늘고, 낮추면 경계 가까운 숫자가 잘릴 수 있다. 기본값은 `2`다.
- crop 결과가 너무 좁거나 필요한 숫자가 잘리면 최소 폭·margin 또는 keep ratio를 높이고, HTS 영역이 남으면 threshold를 신중히 낮추거나 keep ratio를 낮춘다. 문제 발생 시 위 기본값 `0.80 / 0.55 / 50.0 / 2`로 되돌린다.
- 캡처 보존 설정이 활성화되면 전체 raw ROI, tooltip-only ROI 및 tooltip 기준 전처리 이미지를 구분해 저장한다.
- parser는 라벨과 가격이 같은 OCR token에 포함된 경우와 라벨·가격이 별도 token인 경우를 모두 처리한다.
- inline token의 숫자는 OCR 문자열에 실제 존재하는 숫자 substring만 사용하며 추측하거나 교정하지 않는다.
- 시체소굴 가격은 해당 섹션 시작부터 `끝판왕` 행까지로 제한하고, 끝판왕 이후 값은 벽 후보로 사용하지 않는다.

### Live capture 종료

- Ctrl+C가 들어오면 글로벌 핫키를 해제해 새 캡처를 차단하고 pending queue를 폐기한다.
- 실행 중인 Paddle `predict()`는 안전한 취소 API가 없으므로 thread를 강제 종료하지 않는다. OCR worker는 daemon으로 두고 종료 후 결과를 폐기한다.
- shutdown 이후에는 Toss/API 후속 처리와 분석 결과의 Registry merge를 진행하지 않는다.
- 정상 Ctrl+C는 queue drain을 기다리지 않으며 traceback 없이 `Stopping ...`과 `Stopped.`로 종료한다.

## 11. Live Registry 카드 UI

- `StockRegistry`가 UI의 source of truth이며 ticker당 `StockRecord` 1개와 `StockCard` 1개를 유지한다.
- 카드 영역은 처음 등장한 ticker 순서대로 최대 6개만 표시한다.
- 종목이 1~3개일 때는 두 번째 행을 만들지 않고 한 행의 큰 카드로 세로 공간을 모두 사용한다.
- 4번째 종목이 추가되는 순간부터 `상단 1·2·3 / 하단 4·5·6`의 3열×2행 구조로 전환한다.
- 각 행의 카드 묶음은 중앙 정렬하고 새 카드는 기존 카드의 오른쪽에 추가한다.
- 카드 ON/OFF 위의 `X`를 누르면 `(티커) 추적 종료할까요?` Yes/No 확인창을 표시한다. Yes 선택 시 Registry에서 종목을 제거하여 UI 카드와 현재가 polling 대상에서 함께 제외한다.
- daily만 또는 minute만 로드된 중간 상태에서도 사용 가능한 최신 후보로 카드를 표시한다.
- UI 후보는 최신 `daily_values`, `minute_values`의 시체소굴 벽, `taecho`, `buy_price`, `rebound_price`에서 만든다.
- invalid, uncertain, filtered 및 null 값은 Registry의 valid snapshot에 들어오지 않으므로 UI 후보에서 제외된다.
- 같은 가격이 여러 출처에 있으면 기존 후보 우선순서대로 한 번만 표시하고 최초 출처 라벨을 유지한다.
- 현재가는 Toss API의 valid 또는 last-good stale 값만 사용한다. 성공 이력이 없는 unavailable이면 위/아래 비교를 중단하고 카드에 `unavailable`을 표시한다.
- 현재가보다 높은 후보와 낮은 후보를 각각 가격 차이가 가까운 순서로 최대 2개씩 가격 및 퍼센트와 함께 표시한다.
- ±10% 안의 후보를 우선 자연스럽게 볼 수 있으며 후보가 더 많아도 방향별 2개 제한을 적용한다. 예를 들어 `-15%, -10%, -7%, +5%, +11%, +19%`이면 `-10%, -7%, +5%, +11%`를 표시한다.
- ON/OFF는 `StockRecord.holding`에 저장하며 OCR snapshot과 독립적으로 유지한다.
- OCR worker의 완료 callback은 Qt signal을 emit하고, GUI thread slot이 Registry의 복사 snapshot을 읽어 Widget을 갱신한다.
- price worker도 Widget을 직접 수정하지 않고 Qt signal을 통해 GUI thread가 모든 카드를 다시 계산한다.
- 정상적인 daily/minute 재캡처는 해당 snapshot을 완전 교체한 직후 동일 카드의 후보를 다시 계산한다.
- 캡처 실패 시 완료 signal과 Registry merge가 발생하지 않으므로 기존 record, 카드 및 유효 snapshot을 보존한다.
- daily/minute indicator snapshot은 capture 성공 시에만 갱신하고, current price는 capture와 무관하게 periodic price worker가 갱신한다.
- current price가 바뀔 때마다 가장 가까운 위/아래 후보와 퍼센트를 같은 snapshot 후보에서 다시 계산하며 ON/OFF 상태는 유지한다.
- 실시간 및 로그 상태 카드 바깥의 빈 영역은 흰색 대신 앱 기본 배경색과 동일하게 표시한다.
- 매입가와 반등가는 검정, 태초마을은 RGB `(255, 0, 255)`, 통합되지 않은 절대값 half는 RGB `(0, 128, 0)`으로 선과 글자를 표시한다.
- 이평선, 시체소굴 벽, day 벽·바닥 등 나머지 가격선과 글자는 기존 day20 바닥의 파란색을 공통으로 사용한다.
- 카드의 `daily + minute 완료` 상태는 ticker 바로 오른쪽에 배치한다.
- 삭제 `X`와 ON/OFF 버튼 묶음은 현재가 옆이 아니라 카드 헤더의 오른쪽 위 끝에 고정하며, `X`를 ON/OFF 위에 배치한다.
- 카드 하단 가격 차트 영역은 흰색 배경으로 표시하고, 상단 헤더는 고정된 얇은 높이로 줄여 남는 세로 공간을 차트가 사용한다.
- 표시 중인 위·아래 가격 중 현재가와 가장 멀리 떨어진 가격 차이를 차트 스케일로 사용한다. 최소 스케일은 현재가의 10%로 유지하며, 각 선은 실제 가격 차이 비율에 따라 넓어진 차트의 위·아래 범위에 배치한다.
- 현재가가 매입가, 반등가, 태초마을 또는 통합되지 않은 절대값 half 가격의 ±4% 이내에 연속으로 머무르면 해당 선에 `근처 N분째`를 표시한다. 범위를 벗어나면 시간을 초기화한다.
- 태초마을과 절대값 half가 1% 이내라 태초마을로 통합된 경우에는 태초마을 한 선의 근처 체류 시간만 계산한다.

## 12. Live UI 메뉴와 로그 확인

- Live UI 왼쪽 메뉴는 `실시간`과 `로그 확인` 화면을 전환한다.
- `실시간` 화면은 기존 종목 카드와 캡처 상태 표시를 유지하며 수동 ticker 입력 영역은 표시하지 않는다.
- `로그 확인` 상단에는 ticker가 확정되어 Registry에 등록된 순서대로 최대 6개의 종목 상태 카드만 동적으로 생성한다.
- 로그 상태 카드 묶음은 한 줄 중앙 정렬하고 신규 ticker 카드는 기존 카드의 오른쪽에 추가한다.
- 각 상태 카드는 ticker, `일봉 완료/아직..`, 구분선, `분봉 완료/아직..` 및 현재가 polling 상태를 표시한다.
- 현재가 polling 성공 시 카드 하단 중앙에 `실시간 감지중`, 현재가 및 최근 갱신 시각을 표시한다. 실패 시 last-good 가격을 유지하면서 `실시간 지연`, 성공 이력이 없으면 `현재가 대기`로 표시한다.
- `로그 확인` 중단에는 Git Bash로 출력되는 캡처·OCR·파싱·Registry·현재가 로그를 그대로 복제한다. 콘솔 출력 자체는 제거하지 않는다.
- 정상 처리 로그는 파란색, ticker 미검출·unresolved는 굵은 노란색, 처리 오류는 빨간색으로 구분한다.
- 하단에는 캡처, queue, OCR, Registry, 실시간 가격, 종료 및 오류와 같은 주요 진행사항을 모아서 표시한다.
