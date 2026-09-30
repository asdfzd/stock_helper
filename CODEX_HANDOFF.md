# CODEX HANDOFF

갱신: 2026-10-01 KST / 운영일 2026-09-30 ET. 프로젝트: Windows stock_helper.
현재 버전은 **test_version=kiwoom_radar_v2_1 / schema_version=4**다. 기존 사용자의 uncommitted v2 변경 위에 안정화 작업을 추가했다. 캡처/OCR/Registry/매입가·반등가·벽/Toss/halt/UI 구현, 전략/순위 공식은 변경하지 않았다. 주문·계좌 기능, .env 편집, commit/push/staging, 임시 MD/REPORT 생성은 없다.

이 문서가 프로젝트 상태 전달의 기준이다. 이전 KIWOOM_PROBE_REPORT.md와 KIWOOM_PROVIDER_V1_REPORT.md는 작업 시작 때 이미 없었으며 삭제한 파일은 없다. 새 일회성 REPORT 문서는 만들지 않았다.

## v2.1 실제 v2 baseline 재계산

`data/kiwoom_week_v2` 전체를 읽기 전용으로 분석했다. SQLite는 mode=ro; 시작 파일 SHA256은 `data/kiwoom_v2_1_validation/evidence_hashes.json`, SQL/CSV/log 재계산 결과는 같은 폴더 `baseline.json`이다. 원본 export/DB/cache를 재생성하거나 ALTER하지 않았다.

| 항목 | 직접 확인한 수치 |
|---|---:|
| snapshot / unique ticker / FIRST_DETECTED | 4,595 / 40 / 33 |
| CSV candidate / snapshot / event / result | 33 / 4,595 / 1,365 / 363 |
| RVOL available | 266 / 4,595 = 5.7889% |
| +30s complete / missing | 29 / 4 |
| +1m / +3m complete | 각각 33/33 |
| +5m complete / missing | 32 / 1 |
| +10m complete / pending | 32 / 1 |
| +30m complete / pending | 30 / 3 |
| +60m complete / missing / pending | 20 / 0 / 13 |
| 첫 포착 순 첫 20개 +60m | 20/20 complete |
| ws_gap / DB error / export error | 0 / 0 / 0 |
| tracking_slot_unavailable 이벤트 / 마지막 gauge | 674 / 39 |
| 마지막 active / protected / discovery | 20 / 13 / 74 |
| historical backfill complete / incomplete / error | 91 / 1,421 / 182 |
| runtime API error / worker error | 1,020 / 158 |
| FFR snapshot / FE raw | 158 / 7,821 |
| FFR day_change_pct / volume / RVOL non-null | 모두 0 |

기존 summary와 SQL/CSV/로그 수치가 일치했다. **1,421건 incomplete가 API 1,020건에 포함된 것은 아니다.** v2도 incomplete를 별도 WARNING/counter로 집계했다. v2.1은 이를 유지하고 오류 분류를 확장했다.

## v2.1 REST root cause와 FFR

v2 REST 시도 오류 1,020건은 usa20100 474건 + usa06011 546건이다. retry=2로 각각 158/182개의 실패 cycle이 3번씩 호출됐다. 백필 오류의 ticker 분포는 FFR 174, GTNA 3, RZAI 3, GOW 2다. quote 오류는 ticker가 로그에 없어서 474건 모두를 FFR로 확정하지 않는다. worker_error 158건은 quote 실패가 scheduler의 광범위 exception handler까지 전파된 기록과 일치한다. 실패 분류 없이 즉시 3회 retry하고, quote는 다음 순회마다/백필은 60초 뒤 같은 요청을 재시도하는 구조가 오류를 증폭했다.

**v2 증거 한계:** 실패 응답은 raw에 저장되지 않았고 HTTP status/return_code/message/timeout/connection/parse 구분이 없었다. 과거 1,020건의 해당 차원은 unknown이며 모두를 1903으로 소급 분류하지 않는다. endpoint는 기존 allowlist상 usa20100 `/api/us/mrkcond`, usa06011 `/api/us/chart`다.

FFR은 첫 universe에서 CHG+VOL, 일간 합집합에 SPIKE로도 발견됐다. 거래소는 ND이며 FE가 실제 수신됐다. usa10099 catalog에 없어 type=unknown으로 유지했다. usa20100/usa06011 성공 raw는 없었다. FE 가격은 있지만 REST previous_close/completed 1m history가 없어 day_change_pct/volume/RVOL이 NULL이다. FE 필드로 기존 계산 정의를 대체하지 않았다.

정규장 중 허용 read-only probe를 7회 실행했다: ND/NY/NA usa10099 각 1회, FFR/NVDA usa20100·usa06011 각 1회. 공개 결과는 `data/kiwoom_v2_1_validation/public_failure_probe.json`이다.

- catalog: HTTP 200 / return_code 0, 각각 5,206 / 5,579 / 1,947행. 세 거래소 모두 FFR entry 없음.
- FFR 현재가·분봉: HTTP 200 / return_code **7**, message 내 **1903: 종목 정보가 없습니다**. 입력 ND/FFR, 분봉 0행.
- 같은 ND의 NVDA: 두 TR 모두 HTTP 200 / return_code 0, 분봉 100행.
- 확인된 현상은 **후보/FE에는 존재하지만 해당 REST catalog/조회에서는 symbol을 찾지 못하는 불일치**다. 공급자 내부 원인, 상장상태, 유형, 거래소 이동을 추측하지 않는다. ticker 하드코딩/제외/영구 blacklist는 없다.

분류 근거는 [Kiwoom 공식 오류 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/core/errors.py)이다. top-level 7만으로 transient를 판정하지 않는다. 공식 1901/1902/1903은 symbol lookup 오류, 1700/1701/1702는 rate limit, 8005/8031/8103은 인증 회복 대상이다. 그 외 확인되지 않은 service error는 unclassified로 남긴다.

## v2.1 재시도·품질 정책

`kiwoom_failures.py`는 공개 오류 정보를 가진 RestFailure와 StopRequested를 구분한다. HTTP 429/5xx, timeout/connection error, 확인된 rate-limit/auth-expiry만 최대 2회 재시도한다. 인증 회복도 동일 budget이다. unknown/parse는 즉시 재시도 없이 60초 cooldown이다.

negative cache 키는 **ET 날짜 + TR + 전체 allowlist body(ticker/exchange/date 포함) + cursor**다. deterministic unsupported/invalid request/빈 initial chart는 6시간 TTL(`rest_negative_ttl_seconds=21600`) 내 같은 요청을 막는다. ET 날짜 변경 또는 TTL 만료 후 재검증하고 성공 시 실패 상태를 해제한다. first/last failed time, attempt count, retry_after, reason을 메모리에 보존한다. 프로세스 재시작은 새 세션이다. transient exhausted/unknown은 60초 cooldown이다. 억제 호출은 추가 API 오류로 세지 않는다.

백필 complete/incomplete/skipped/deterministic_unavailable/transient_error/unclassified_error를 분리하고 legacy error 필드를 유지한다. historical 시도 기준이며 skipped는 eligibility로 생략한 scheduler 기회 횟수다. 정상 empty initial chart는 deterministic no_data지만 runtime API 오류가 아니다. HTTP/API/parse/transport 실패는 api_error_count에 남기고 transport/parse counter는 그 부분집합이다. REST가 이미 집계한 실패를 scheduler가 worker_error로 다시 더하지 않는다.

quality_summary_version=2, 기존 필드 유지. rest_errors에 total/transient/deterministic/unclassified/timeout/rate-limit/no-data/unsupported 등을 제공한다. TR/ticker/endpoint/HTTP/top-level code/embedded code는 각각 상위 10개만 출력한다. raw REST_ERROR에는 공개 상태와 sanitized message를 남기며 인증값/Authorization/token은 저장하지 않는다. shutdown 요청·취소·마지막 시간 및 result_sampling을 추가했다. tracking은 마지막 batch의 slot_saturation_pct/active/protected/unavailable이다.

## v2.1 Ctrl+C와 무결성

기존 pending jobs는 FIFO 전체를 drain하는 구조가 아니라 IO thread가 페이지 단위로 처리하는 dict였다. 기존 stop_event도 있었다. 따라서 '기존 종료가 반드시 queue 전체를 처리했다'고 주장하지 않는다. scheduler 내부 loop/page/구독 선택의 stop 확인이 부족했고 stop 예외가 오류로 집계될 수 있었다. v2에는 Ctrl+C 요청 시각이 없어 전체 지연을 재구성할 수 없다. 마지막 WS disconnect 15:27:27.612 UTC→final export 15:27:40.307은 12.695초, program_stop까지는 12.709초지만 Ctrl+C부터의 시간은 아니다.

- CLI/실제 smoke에서 Ctrl+C를 KeyboardInterrupt로 기록하고 Event를 즉시 설정한다. 추가 Ctrl+C는 같은 종료 상태를 사용한다. Windows launcher에서 상속될 수 있는 Ctrl+C ignore flag도 해제한다.
- universe refresh/각 page/백필 시작/REST retry/limiter wait/새 WS REG 앞에서 stop을 검사한다. 진행 중 bounded request 이후 pending historical jobs를 취소한다. thread kill/os._exit/process terminate는 없다.
- shutdown_requested, shutdown_stop_scheduling, shutdown_backfill_cancelled, shutdown_ws_closing, shutdown_export_start, shutdown_export_complete, shutdown_complete 및 program_stop을 기록한다. 단계별 timestamp/elapsed_ms를 확인할 수 있다.
- 실제 순서: scheduling stop → REST IO join/백필 jobs 취소 → WS REMOVE/disconnect → raw cache drain/close 및 REST/collector lock close → 연구 DB commit/checkpoint → CSV/quality final export → DB close → program_stop. WS thread는 같은 Event를 받아 먼저 종료할 수 있다. provider close를 export보다 앞에 두는 것은 종료 REMOVE/raw/error counter까지 summary에 포함하기 위함이다.
- 완료 후 반복 close는 REMOVE/cache close를 반복하지 않는다. 취소는 API 오류가 아니다. CSV/JSON 파일별 atomic replace는 유지한다.

모의 active request + pending historical 300건 + 반복 stop은 **78ms**, 300건 취소, DB integrity ok, final CSV/summary 및 phase 순서 통과였다. HTTP timeout은 connect 5초/read 10초 그대로이며 정상 환경 목표는 15~20초다. 악조건 WS/파일시스템까지 20초를 절대 보장한다는 뜻은 아니다.

## v2.1 결과 샘플과 기존 정책

원본 missing 4건의 가까운 pre-target offset은 LIQT -0.001633s, NVDA -0.003049s, PACB -0.002346s, FRGT -0.002783s다. 다음 snapshot은 각각 +30.001094/+30.020835/+30.006155/+30.003765s였다. 원본 결과는 수정하지 않았다. `counterfactual_sampling.json`의 읽기 전용 재검산에서는 기존 complete 선택이 전부 동일하고 이 4건만 grace로 복구 가능했다. 운영 증거 전체 SHA256도 작업 전후 동일하다.

selector는 target~target+30초의 가장 빠른 실제 snapshot을 우선한다. 창이 끝난 뒤 post-target이 없을 때만 target-`result_pre_target_grace_seconds`(기본 0.25초) 이상~target 미만 중 가장 가까운 snapshot을 쓴다. 그 외 missing이다. grace=0으로 비활성 가능하고 30초 tolerance는 유지한다. A~F 경계, 정확한 0.25초 경계, 비활성, 나머지 intraday horizon을 테스트했다. DB/CSV의 sample_timestamp/sample_offset_seconds/sample_selection은 기존 observed_utc/timing_error_seconds와 일치한다. entry/price/return/MFE/MAE 의미와 EOD/D+n은 유지하며 보간하지 않는다.

보호 배정 알고리즘은 변경하지 않았고 stop guard만 추가했다. FIRST_DETECTED부터 최대 intraday+tolerance 보호, 기존 보호 우선, 60분 결과 후 release, DB 복원, EOD/D+n 분리 회귀를 통과했다. 기존 v2 첫 20개 +60m 완료 evidence를 보존하며 짧은 smoke로 새 60분 수집 성능을 증명하지 않는다.

Current Leaders=day_change_pct descending, Emerging=completed 1m return_5m descending, 동률 ticker 그대로다. RVOL available/warming/incomplete_history/not_eligible/zero_denominator/unknown 및 MIXED_OR_UNCERTAIN 정책도 그대로다. 가짜 zero-volume minute은 만들지 않았다. ETF/ETN/warrant/확정 unit-other 제외, common+unknown 포함을 유지한다. calculations 변경은 기존 v2 정책을 v2.1에도 적용하는 버전 조건 확장뿐이다.

20슬롯은 smoke/test 규모다. 기존 CLI/config max_ws_symbols와 공식 상한 200을 유지했다. 20→200 임의 변경은 없다. 50/100/200 중 주간 권장 capacity 결정은 별도 smoke가 필요하다.

## v2.1 버전·검증·다음 실행

기본 연구 DB는 `data/stock_helper_market_kiwoom_v2_1.db`, schema 4다. 기존 schema3 DB는 DDL 이전 identity/version 검사로 거부하며 byte 불변 테스트를 통과했다. cache OHLCV contract/schema 1/assumed_et_start_v1은 그대로여서 별도 cache schema는 필요 없다. basename `market_radar_cache/kiwoom_v2.db`를 유지하되 실제 smoke는 새 폴더에 새 cache를 만들었다. 기존 운영 cache는 변경하지 않았다.

자동 검증: **168 통과 / 실패 0**. 기존 133개 유지 + 신규 stability 35개. 테스트 삭제 없음. version/schema/path 기대값만 새 버전에 맞췄다. artifact는 `data/kiwoom_provider_validation/tests_summary.json` 및 suite 로그다. 기존 실제 OCR 초기화·Registry·UI·Toss·halt 회귀가 모두 통과했다. 해당 제품 구현 파일은 수정하지 않았다. `git diff --check`도 통과했다.

실제 v2.1 smoke: **완료**. 새 `data/kiwoom_smoke_v2_1`, 20슬롯, production provider. 약 10분(collector 596.875초; interpreter/초기화 포함 driver 600초) 동안 수집했다. windows_ctrlc_smoke.py가 숨김 콘솔에 실제 Windows CTRL_C_EVENT를 전달했고 정상 KeyboardInterrupt handler가 수신했다. ordinary timer의 worker.stop/강제 종료로 대체하지 않았다. driver의 child exit code=0, forced_termination=false다.

| 실제 smoke 항목 | 수치 |
|---|---:|
| 실행 UTC | 2026-09-30 16:53:57.439569 ~ 17:03:56.713607 |
| snapshot / unique ticker / candidate | 378 / 21 / 19 |
| +30s / +1m / +3m / +5m complete | 각각 19/19, missing 0 |
| +10m / +30m / +60m | 각각 pending 19 (짧은 smoke) |
| result_sampling post-target / pre-grace | 76 / 0 |
| REST errors / deterministic / transient / unclassified | 17 / 17 / 0 / 0 |
| FFR usa20100 / usa06012 / usa06011 | 1 / 1 / 15 |
| 동일 요청 재시도 / 억제 호출 | 0 / 134 |
| historical backfill complete / incomplete / deterministic_unavailable | 56 / 179 / 14 |
| DB / worker / export / transport / parse 오류 | 모두 0 |
| WS gap / raw dropped / stale | 0 / 0 / 1 |
| ack add / remove 종목 수 | 21 / 21 |
| slot saturation / active / protected / unavailable 마지막 gauge | 100% / 20 / 19 / 37 |
| slot unavailable 진입 이벤트 | 77 |
| RVOL available / warming / incomplete_history | 0 / 92 / 286 |
| 제외 유형 snapshot | 0 |
| FFR FE raw | 110 |
| 연구 DB / cache integrity_check | 둘 다 ok |
| 종료 취소 historical jobs / stop 이후 새 백필 | 8 / 0 |

17건 모두 HTTP 200 / top-level 7 / embedded 1903 / attempt=1이며 FFR이다. 분봉 15건은 당일+이전 14거래일 각각 1회다. 각 날짜의 backfill_start도 1회씩이므로 같은 query를 반복한 15회가 아니다. API 오류 자체는 숨기지 않고 남겼다. 실제 오류 class 분류와 retry 억제가 확인됐다. 신규 캐시·짧은 수집에서 RVOL available=0이므로 RVOL 개선을 주장하지 않는다. 실제 grace 사용은 0건이어서 경계 개선 근거는 자동 테스트와 원본 read-only 재검산이며 live pre-grace 사용 검증이라고 주장하지 않는다.

| 실제 종료 단계 | UTC |
|---|---|
| 마지막 새 historical backfill 시작 | 17:03:54.692 |
| shutdown_requested, reason=KeyboardInterrupt | 17:03:56.377905 |
| WS REMOVE 20개 | 17:03:56.432 |
| WS disconnect | 17:03:56.439 |
| shutdown_export_start | 17:03:56.523 |
| final CSV export | 17:03:56.662 |
| shutdown_complete / program_stop | 17:03:56.707 / 17:03:56.708 |

Ctrl+C 수신→program_stop은 로그 기준 **0.331초**다. driver 신호 전송 helper 반환→process exit 관측은 0.640초다. summary의 last_shutdown_elapsed_ms=281은 CSV 및 첫 quality export까지의 측정이며, 이후 timing gauge를 반영한 작은 summary 재출력/DB close는 phase 로그의 328ms 및 program_stop에 포함된다. 이 측정 범위를 전체 shutdown 시간과 혼동하지 않는다.

근거: smoke_summary.json, ctrlc_driver.json, export/2026-09-30/quality_summary.json, logs/2026-09-30.log 및 `data/kiwoom_v2_1_validation/smoke_audit.json`. raw/log/export에 실제 APP_KEY/APP_SECRET 문자열이 없고 기존 v2 evidence SHA256가 전부 동일함을 확인했다.

smoke 시작 당시 166개가 통과했고, 수집 중 최종 검토에서 **중첩 Ctrl+C handler 재진입 방지**와 **provider close 실패 표시**만 추가했다. 두 예외 경로는 이후 mock 테스트를 추가해 stability 35개/전체 168개로 확인했다. 정상 live 수집·REST·sampling 경로는 smoke 이후 변경하지 않았다. 실제 연속 두 번 Ctrl+C나 provider close 실패를 live에서 재현했다고 주장하지 않는다.

다음 실행(새 폴더, 20슬롯 제한 수집):

~~~powershell
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 20 --result-pre-target-grace-seconds 0.25 --data-dir data/kiwoom_week_v2_1
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/run_validation.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/windows_ctrlc_smoke.py --seconds 600 --max-ws-symbols 20 --data-dir data/kiwoom_smoke_v2_1_next
~~~

남은 제한: v2 실패 응답 코드 복원 불가; FFR 공급자 내부 원인 미확정; unknown 오류는 60초 cooldown 후 재검증; 6시간 negative TTL 안에 데이터가 회복돼도 즉시 감지하지 않음; slot saturation/RVOL NULL은 남음; 짧은 live smoke가 장기 네트워크 장애/주간 capacity를 보증하지 않음. **새 v2.1 폴더에서 20슬롯 한계를 이해한 일주일 제한 수집은 시작 가능**하다. 전체 후보 추적 목적의 주간 운영 권장은 50/100/200 capacity 검증까지 보류한다. 신규 임시 MD는 없다. commit/push/git add는 하지 않았다.

## 이전 v2 기록

아래의 '현재', schema 3, 133개 및 실행 경로는 **v2 당시의 역사적 기록**이다. 현재 v2.1 상태와 실행법은 위를 따른다. 기존 evidence/인수인계 내용을 보존했다.

## 1. 기존 프로그램 구조와 실행 흐름

`live_ui.py:main()` → `LiveStockWindow` → Qt event loop → `start_capture_services()`.
저장된 클릭 위치/글로벌 단축키 → `live_capture.py`의 캡처와 clipboard ticker 확보 → `CaptureProcessor.enqueue_live_capture()` → OCR 파싱/검증 → `StockRegistry` 갱신 → Qt signal → 종목 카드 표시. clipboard ticker가 있으면 우선한다.

| 파일/영역 | 기존 역할 |
|---|---|
| live_ui.py | 실제 메인 UI, 실시간/추적중/로그/캡처 사진 4페이지 |
| main.py | 종목 카드와 기존 가격/분석 표시 |
| live_capture.py | 단축키·캡처·자동입력·OCR 큐 |
| paddle_ocr_validation.py | PaddleOCR 초기화, Tooltip OCR/검증 |
| stock_models.py | StockRegistry, 매입가·반등가·벽, 기존 추적·병합 상태 |
| toss_api.py / price_refresh.py | 기존 현재가/일봉 고저가 조회 및 polling |
| halt_monitor.py | 독립 거래정지 조회 worker |
| capture_points/history/pending_captures.py | 캡처 위치·이력·미해결 상태 |
| runtime_paths.py | APP_ROOT와 패키징 경로 |

기존 태초마을 재돌파·벽 접근 후 이탈·근접 지속시간 기능을 유지한다. OCR과 Toss/halt는 기존처럼 별도 worker다. 이번에 실제 영웅문 창 클릭/캡처 자동화를 조작하지 않았다.

## 2. Radar 연결 지점

직전 Market Radar 1차 작업에서 live_ui.py에 16줄이 추가되어 있다: 메뉴 버튼, lazy import로 독립 RadarPanel 열기, 앱 shutdown에서 panel.stop/close. **이번 provider 작업은 이 파일을 다시 수정하지 않았다.** 기존 4페이지 index와 OCR 시작 흐름은 유지한다.

창을 여는 것만으로 네트워크 수집하지 않는다. 실제 시작 버튼 또는 headless `--collect`가 필요하다. REPLAY는 별도 버튼/CLI이며 합성 fixture를 실시간으로 위장하지 않는다.


## 3. Radar 현재 구조

CONFIG는 provider=kiwoom / test_version=kiwoom_radar_v2다. RadarConfig() 자체는 legacy fixture용 unconfigured / market_radar_v1 기본값을 유지한다.

| 영역 | 역할 |
|---|---|
| config / provider / models | 설정, read-only provider/replay 계약, 검증된 batch 모델 |
| providers/kiwoom.py | 네 후보 source 합집합, 보호 구독, REST scheduler, completed_1m Observation |
| providers/kiwoom_types.py | 공식 public fields/명시적 상품명 근거의 보수적 분류 |
| providers/kiwoom_rest.py | OAuth, 9 TR allowlist, 중앙 limiter, timeout/retry, 비밀값 제거 |
| providers/kiwoom_ws.py | LOGIN/REG/REMOVE ack, FE/optional FT, PING, 재연결 |
| providers/kiwoom_cache.py | 별도 OHLCV/cache/raw/catalog DB와 contract 검증 |
| providers/kiwoom_calendar.py / kiwoom_lock.py | XNYS 달력 / 로컬 단일 collector lock |
| calculations / engine / storage | 순위 공식, candidate/event/result, research schema 3, CSV |
| quality.py | thread-safe ET 일별 구조화 counter 및 DB 기반 quality_summary |
| runtime / ui / __main__ | Qt 밖 수집·계산·저장, UI/headless/replay |
| tools/kiwoom_provider | 전체 회귀 검증, read-only audit/probe, production smoke_v2 |

REST https://api.kiwoom.com, WS wss://api.kiwoom.com:10000/api/us/websocket.
계약은 [키움 공식 명세 고정 revision](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/_data/kiwoom_api_spec.json)에서 추출했고 이번 작업에서도 공개 원문을 확인했다.
허용 TR은 usa20100(현재가), usa10099(목록), usa20910/usa20930/usa20530/usa20540(후보), usa06011(분봉), usa06012(일봉), usa20590(일별주가)다. 새 endpoint/TR를 추정·추가하지 않았다. 제품은 probe output을 import하지 않는다.

## 4. 실제 v1 첫 운영 baseline

입력 data/kiwoom_week_01은 읽기 전용 evidence다. SQLite mode=ro 및 파일 SHA256으로 감사했다. 재현 도구 tools/kiwoom_provider/audit.py, 결과 data/kiwoom_v2_validation/baseline.json. 날짜는 ET 2026-09-28이다.

| 항목 | 수정 전 실제 수치 |
|---|---:|
| snapshot / unique ticker / FIRST_DETECTED candidate | 676 / 22 / 20 |
| RVOL non-null | 56 / 676 = 8.2840% |
| return_1m non-null | 629 / 676 = 93.0473% |
| return_5m non-null | 618 / 676 = 91.4201% |
| cache day complete / incomplete | 90 / 249 |
| 로그 backfill complete / incomplete 시도 | 524 / 440 |
| stale price 경고 | 29 |
| ws disconnect / raw dropped | 0 / 0 |
| subscription add/remove 이벤트 | 21 / 21 |
| subscription add/remove 종목 수 | 41 / 41 |
| REST 오류 / api_error | 0 / 1 (초기 warm-up traceback) |
| 실제 DB / worker / export 오류 기록 | 0 / 0 / 0 |
| raw FE / REST | 76,765 / 2,643 |

complete 로그 524에는 현재세션 완료도 포함된다. cache day 90과 다른 단위다. v1의 오류/구독 횟수는 당시 structured counter가 없어서 로그를 보조 근거로 썼고, v2는 DB counter를 쓴다.

| horizon | complete | missing | pending | outside_session |
|---|---:|---:|---:|---:|
| +30s | 17 | 3 | 0 | 0 |
| +1m | 17 | 3 | 0 | 0 |
| +3m | 16 | 4 | 0 | 0 |
| +5m | 17 | 3 | 0 | 0 |
| +10m | 17 | 2 | 0 | 1 |
| +30m | 0 | 0 | 0 | 20 |
| +60m | 0 | 0 | 0 | 20 |
| EOD, D+1, D+2, D+3 각각 | 0 | 0 | 20 | 0 |

단기 평가 가능 99건 중 missing 15건(15.15%). 종가 이후 목표 41건은 missing과 구분한다. 첫 운영에서 구독 churn으로 추적 가격이 없어질 수 있는 구조, 저유동 과거 이력 불완전, ETF/워런트 포함, 정상 warm-up ERROR, 일별 품질 집계 부재를 확인했다.

## 5. 무체결 분 검증 결론

**MIXED_OR_UNCERTAIN**. 누락을 거래량 0으로 확정할 증거가 부족하다.

공식 usa06011은 OHLCV/체결시간/영업일자와 pagination을 설명하지만 무체결 분 생략/0 봉 규약을 명시하지 않는다. v1 raw에서 정규장 시간만 비교했다.

| 표본 / 2026-09-25 | 정규장 봉 / 예상 390 | 정규장 volume 합 | 일봉 acc_trde_qty |
|---|---:|---:|---:|
| NVDA | 390 | 45,609,301 | 89,947,712 |
| SOAR | 250 | 471,229 | 1,028,603 |
| ARBEW | 13 | 33,429 | 95,554 |
| WALDW | 13 | 50,736 | 142,194 |
| SHFSW | 3 | 2,958 | 12,839 |

표본에서 반환된 regular zero-volume 봉은 0개다. 저유동 표본은 앞뒤 유효 봉 사이 timestamp가 생략돼 있지만, 개별 누락 분에 체결이 없었다는 독립 증거가 없다. 고유동 NVDA도 분봉합과 일봉량이 다르므로 일봉량 일치로 증명할 수 없다. feed/세션 범위 차이가 미확정이다. extended-hours를 제외하고도 regular 구간 누락이 존재한다. BZFDW는 날짜별 regular 봉 0개여서 raw 페이지/coverage 문제도 배제할 수 없다. 이런 빈 집합의 관측 합 0은 정상 일간 거래량 0 판정이 아니다.

따라서 volume timeline의 0 보완도 구현하지 않았다. 실제 제공된 유효 0-volume 봉만 사용하며 OHLC/high/low/close/HOD/OR 생성은 없다. 미확정 누락은 incomplete/NULL이다. 무체결 확정을 활성화하는 사용자 토글도 두지 않았다.

## 6. RVOL·return·순위 정책

Current Leaders는 day_change_pct 내림차순, Emerging은 완료 1분봉 return_5m 내림차순, 동률 ticker다. 전략 순위 공식을 바꾸지 않았다. follow-up-only, 제외 유형, 지표 NULL은 해당 순위에 들어가지 않는다. VWAP NULL은 순위 제외 조건이 아니다.

RVOL=현재 regular 개장~완료 분 cutoff 누적량 / 직전 14거래일 동일 경과분 누적량 평균. cutoff 2초 유예, historical complete 검증, 조기폐장 cutoff 경계, 진행 봉 재조회 정책을 유지한다. 값 보간이나 과거 DB snapshot fallback은 없다. rvol_status는 available/warming/incomplete_history/not_eligible/zero_denominator/unknown이다.

새 eligibility 설정:
- rvol_backfill_enabled=true
- rvol_backfill_min_day_volume=0
- rvol_backfill_min_price=0

조건은 과거 RVOL backfill만 제어한다. 미달 후보도 발견/순위/당일 분봉/결과 추적은 계속하며 RVOL은 NULL/not_eligible이다. 양수 조건은 당일 usa20100 acc_trde_qty/cur_prc로 검증하며 없는 값을 0으로 만들지 않는다. 해당 설정 시 quote는 주기적으로 갱신한다.

실제 candidate day-volume 범위는 FVNNU 2,173~2,185부터 SOAR 608,226,198~613,819,995까지 매우 넓다. 큰 당일량 SOAR도 과거 regular 분봉이 불완전하므로 하루량으로 coverage를 예측하는 근거가 부족하다. 임의 양수 임계값을 제시/기본 적용하지 않고 초기값 0을 권장한다. 원본 ticker별 min/max/표본 수는 baseline JSON에 있다.

return은 cutoff close와 정확히 1/5분 전 close를 비교한다. OR1/OR5/HOD는 해당 범위 연속 봉이 필요하다. VWAP는 NULL이며 FE14 단위/세션 초기화 미확정, typical-price 근사 없음. cntr_tm의 ET 분 시작 해석은 공식 확정이 아닌 assumed_et_start_v1 연구 가정이다. 24시 이상/장외 봉은 raw에만 둔다. 무료 나스닥 토탈뷰를 전 거래소 통합 체결량으로 주장하지 않는다.

## 7. 보호 추적과 슬롯 고갈

FIRST_DETECTED된 종목은 DB에 tracking_protected_until을 저장한다. 첫 포착 + max(최대 intraday horizon 60분, result_tracking_hold_minutes=60), 당일 close로 제한하고 tolerance 30초까지 보호한다. 이는 실제 마지막 목표의 snapshot 수집 기회를 보호한다. 재시작은 DB에서 복원한다.

배정 순서: 기존 보호 구독 → 추가 보호 대상 → 현재 universe → 기타 추적. 보호 종목은 단순 순위/source 이탈만으로 제거하지 않는다. 보호 기간 후 release 가능하며 EOD/D+n 일봉 추적은 별개로 계속한다.

모든 슬롯이 차면 신규 발견을 radar_discoveries/raw UNIVERSE에 기록하고 tracking_slot_unavailable 상태와 구조화 event/counter를 남긴다. 유효 live 가격 없는 발견은 가격/기준가/FIRST_DETECTED를 만들지 않는다. 따라서 quality candidate_count는 실제 FIRST_DETECTED 수, discovery_count는 raw universe 수다. 보호 여부/신규 수/실제 ack active 수/미배정 수를 batch metadata 및 summary tracking에 보존한다.

단기 result는 기존 status 의미를 유지하고 missing_reason을 추가했다. 목표~tolerance 내 batch의 실제 사유(stale_price/not_subscribed/ws_gap/tracking_slot_unavailable)를 사용한다. 증거가 없으면 no_snapshot_within_tolerance다. outside_session도 명시한다. provider 전체 실패로 batch 자체가 없을 때 원인을 추정해 provider_error로 단정하지 않는다.

## 8. 종목 유형 검증과 필터

공식 mkgb는 거래소명, upgb는 업종명, isEtf는 ETF 여부다. 실제 read-only usa10099 재조회 결과는 data/kiwoom_v2_validation/public_type_probe.json에 public fields/분류/수신시각만 저장했다. 인증값/토큰은 없다.

| 실제 표본 | 공식 필드/상품명 | v2 판정 |
|---|---|---|
| HYG, TQQQ, SOXL | isEtf=Y | ETF, 제외 |
| ARBEW, WALDW, BZFDW, SHFSW | isEtf=N, 한국명 워런트 + 영문명 C/WTS | warrant, 제외 |
| FVNNU (v1 catalog) | 한국명 유닛 + 영문명 UNITS | other, 제외 |
| AAPL, NVDA | isEtf=N, 거래소/업종만 제공 | unknown, 포함 |

워런트/유닛은 공식 상품명에 명시된 양 언어 descriptor의 일관성에 근거한다. 공식 전용 type enum이나 ticker suffix 판정이 아니다. N만으로 보통주라 표시하지 않는다. 명시적 COMMON STOCK/ORDINARY SHARES, ETN descriptor 처리도 구현했으나 이 규칙의 실제 해당 표본 검증은 남아 있다. type evidence는 source/rule/public fields다.

stock_type_policy=common_and_unknown은 common/unknown만 포함하고 확정된 ETF/ETN/warrant/other는 제외한다. all도 가능하다. 제외 종목의 raw/catalog/discovery는 보존하며 excluded_symbol_type을 기록한다.

## 9. 로그·Daily Quality Summary

초기 정상 준비: INFO kiwoom_universe_warming_up, traceback 없음.
첫 정상 universe 후 갱신 허용시간 max(180초, refresh*3) 초과: WARNING kiwoom_universe_stale.
실제 HTTP/인증/JSON/DB/worker 오류: ERROR. 불완전 history는 WARNING kiwoom_backfill_incomplete로 API 오류와 구분한다.

ET 날짜별 export/YYYY-MM-DD/quality_summary.json:
- session_date/test_version/schema_version, snapshot_count/unique_tickers/candidate_count
- rvol 상태별 수와 available_count/pct, return_1m/5m 비율
- horizon별 complete/missing/pending/outside_session 및 missing_reasons
- market_data: ws_gap/stale/raw_dropped/subscription add/remove/tracking_slot_unavailable
- backfill: historical complete/incomplete/error 시도 수
- runtime: API/DB/worker/export 오류
- candidate_sources: CHG/SPIKE/VOL/VALUE/multi_source
- symbol_types: common/ETF/ETN/warrant/unknown/other(발생 시)/excluded
- tracking: 마지막 batch의 protected/discovery/active/unavailable gauge

snapshot/result는 SQLite query, 운영 수치는 명시적 logging extra로 전달되는 thread-safe counter를 ET 날짜별 DB에 누적한다. 로그 문자열 parsing에 의존하지 않는다. source 수는 종목별 일간 source 합집합, type은 마지막 관측, subscription은 ack된 종목 수다. backfill은 고유 cache day 수와 다르다. pct는 snapshot 0개면 NULL이다.

300초마다 및 정상 종료 시 갱신한다. provider 정상 close/REMOVE가 끝난 후 최종 counter/CSV/JSON을 export한다. gauge는 마지막 batch 시점 값이므로 종료 후에도 당시 active 수를 표시한다. snapshot 없는 날도 counter/discovery가 있으면 export한다. DB/raw를 summary로 대체하지 않는다. 강제 종료 시 아직 flush하지 않은 counter는 유실 가능하다.

## 10. Schema·경로·실행

기본 연구 DB: data/stock_helper_market_kiwoom_v2.db (schema 3).
기본 cache: data/market_radar_cache/kiwoom_v2.db (기존 OHLCV contract/schema 1 유지).
새 컬럼: snapshot rvol_status/symbol_type_evidence, candidate tracking_protected_until/candidate_tracking_state/symbol_type/evidence, result missing_reason. 새 radar_discoveries와 quality_counters 테이블.
schema v2 live DB를 ALTER하지 않는다. identity/config/schema가 다르면 거부한다. cache identity는 provider/mode/schema/bar_policy를 DDL 이전 검증한다. 비어 있지 않은데 contract metadata가 없는 cache도 변경 없이 거부한다. 기존 v1 cache 자동 복사/이관은 없다.

~~~powershell
& .\.venv-paddle\Scripts\python.exe -m market_radar
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 20 --data-dir data/kiwoom_week_v2
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/run_validation.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/audit.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/probe_types.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/smoke_v2.py --seconds 1200 --max-ws-symbols 20 --data-dir data/kiwoom_smoke_v2
~~~

CLI eligibility: --rvol-backfill-min-day-volume / --rvol-backfill-min-price / --disable-rvol-backfill.
REST 일반 3회/초, 한국시간 09~10시 2회/초, 종목목록 4회/60초. timeout 5/10초, 최대 재시도 2회. WS ack 후 active 변경, 단절 시 가격 폐기·gap 기록·재등록. REST/cache thread 1, WS thread 1, RadarWorker 계산/연구 DB/export, Qt는 표시만 한다. stop은 Event로 요청하고 timeout 내 종료를 기다린다. optional requirements-kiwoom.txt를 사용하며 기존 Paddle/numpy/pandas 버전은 변경하지 않았다.

## 11. 자동 검증 및 실제 smoke

최종 전체 자동 검증: **133 통과 / 0 실패**. 기존 assert 스크립트 12, Radar 31, audit 19, provider 33, 신규 quality 33, probe 4, 실제 GPU OCR 초기화 1의 합계다. 재실행은 중복 집계하지 않았다. 근거: data/kiwoom_provider_validation/tests_summary.json 및 suite별 로그.

신규 검증은 warm-up INFO/stale WARNING/실제 오류 ERROR, 불확실 분 누락·실제 zero-volume 봉 보존, eligibility·RVOL 상태, 보호 구독·20슬롯 고갈·재시작 복원·60분 결과 완료 후 release, 미등록 discovery, missing_reason, ETF/warrant/unit/common/unknown, 기존 순위 공식, 일별 summary 수치·ET 날짜·CSV, schema v2/미확인 cache 거부를 포함한다. 기존 OCR/Registry/UI/Toss/halt 회귀도 통과했다. 실제 화면의 모든 OCR 인식 정확도를 검증한 뜻은 아니다.
실제 smoke_v2: **2026-09-29 19:11:10~19:31:12 UTC / 15:11~15:31 ET**, 약 1200초. production provider, 20슬롯, 새 data/kiwoom_smoke_v2 사용. benchmark/후보 선택 override 없이 실행했고 정상 종료했다. 실행 중 추가된 cache metadata 없는 DB 거부 guard는 새 cache 정상 경로를 바꾸지 않으며 offline 회귀로 별도 검증했다.

| v2 실제 결과 | 수치 |
|---|---:|
| snapshot / unique ticker / candidate | 751 / 22 / 20 |
| RVOL available / warming / incomplete_history | 40 / 112 / 599 |
| RVOL non-null | 5.3262% |
| return_1m / return_5m non-null | 91.4780% / 92.2770% |
| +30s complete/missing | 20 / 0 |
| +1m complete/missing | 19 / 1 |
| +3m complete/missing | 20 / 0 |
| +5m complete/missing | 19 / 1 |
| +10m complete/missing | 20 / 0 |
| +30m pending / +60m outside_session | 20 / 20 |
| EOD/D+1/D+2/D+3 pending | 각각 20 |
| historical backfill complete/incomplete/error 시도 | 60 / 352 / 0 |
| cache unique day complete/incomplete | 60 / 225 |
| ws_gap / stale / raw_dropped | 1 / 27 / 0 |
| ack subscription add/remove 종목 수 (종료 포함) | 22 / 22 |
| slot_unavailable 진입 이벤트 / 마지막 미배정 수 | 109 / 43 |
| 마지막 protected / discovery / active | 20 / 79 / 20 |
| 일간 발견 ETF/warrant 제외 | 14 / 9 (총 23) |
| 제외 유형 snapshot | 0 |
| 보호 이후 선택 슬롯 이탈 | 0 |
| universe 밖 보호 종목이 계속 선택된 관측 | 60 |
| API / DB / worker / export 오류 | 0 / 0 / 0 / 0 |
| raw FE / REST / UNIVERSE | 27,977 / 2,436 / 20 |

ws_gap 1회는 최초 구독 이전 연결 초기화 중 기록됐다. 단기 missing 2건은 MF +5m stale_price와 QSI +1m no_snapshot_within_tolerance다. 현재 시세를 채워 결과를 수정하지 않았다. 마지막 tracking gauge active=20은 마지막 batch 값이며 종료 REMOVE 20개를 포함한 최종 remove counter는 22다.

v1 단기 missing은 15/99=15.15%, v2는 2/100=2%. RVOL available은 v1 8.28%, v2 5.33%다. 서로 다른 거래일·종목·신규 캐시이므로 통제된 성능 비교나 RVOL 개선으로 주장하지 않는다. 보호 추적과 필터의 실제 불변조건을 별도로 확인했다.

정상 REMOVE/WS close/worker stop 및 최종 export 성공. CSV snapshots/candidates/events/results 행 수 751/20/201/220이 DB와 일치한다. quality summary의 count/비율도 DB와 일치하며 두 DB integrity_check=ok다. 증거: data/kiwoom_smoke_v2/smoke_summary.json, export/2026-09-29/quality_summary.json, data/kiwoom_v2_validation/smoke_checks.json.
실제 public type probe는 9개 요청 표본 모두 확보했고 API 오류 없음. 주문/계좌 API는 사용하지 않았다.

기존 v1 provider의 2026-09-28 150초 AAPL/SOAR 진단 smoke와 이번 첫 운영 baseline/새 v2 smoke는 서로 다른 evidence다. 과거 DB를 v2 결과로 변형하지 않았다.

## 12. 남은 검증과 일주일 운영 조건

- 무체결 분 규약, 공식 timezone/봉 라벨/24시 표기, FID290 enum 미확정. 차트/체결 증거로 확인하기 전 NULL 정책 유지.
- isEtf=N 전체의 common 확정은 불가. unknown을 포함하는 연구 범위를 명시한다. 공식 name descriptor가 없는 warrant/ETN/기타 상품은 unknown에 남을 수 있다.
- snapshot 30초 cadence의 작은 시각 흔들림으로 목표~30초 허용 구간 양쪽에 표본이 걸쳐 missing이 될 수 있다. 이번 실제 QSI +1m에서 목표 직전 2.6ms 및 허용 끝 직후 8.9ms 표본이 확인됐다. 기존 tolerance/결과 의미를 임의 확대하지 않았다.
- 실제 60분 전 구독 보호/후 release는 시간 전진 offline 테스트와 구분한다. 20분 smoke로 실제 +60분 완료를 검증했다고 주장하지 않는다.
- EOD/D+n은 provisional이며 future pending. 공식 확정 종가·기업행동 자동 보정·과거 complete cache 정정/분할 refresh는 미구현.
- 200종목 부하/실제 재연결/돌발 휴장/1주 연속 수집/EXE 배포는 미검증. raw 보관기간/압축/자동 삭제 없음.
- 일주일 수집은 새 data-dir, 단일 WS 세션, 작은 구독 수로 시작하고 daily summary/디스크를 확인한다. 장중 actual feed/로그/CSV 검증이 통과한 범위에서 데이터 품질 검증용으로 진행한다. 전략 수익성 검증 완료라는 뜻이 아니다.
- 다음 장중 검증은 위 --collect 명령으로 ET 15:00 이전(가급적 개장부터)에 시작해 최소 65분 이상 유지한다. 첫 candidate의 +60m complete와 보호 해제를 확인한 뒤 Ctrl+C로 종료한다. smoke_v2의 20~30분 제한만으로 이 검증을 대체할 수 없다. 새 주간 폴더 data/kiwoom_week_v2를 사용하고 smoke/v1 evidence를 덮어쓰지 않는다.
- 누락 RVOL을 0으로 표시하지 않고 available subset과 missing 원인을 분리해 분석한다. 마지막 포착일 D+3 이후까지 일봉 후속 수집, 원본 연구 DB와 cache 동시 보관, 클라우드는 export만 복사한다.

## 13. 최종 Git/보존 확인

작업 전 git status는 clean이었다. 기존 root Python 기능 파일은 변경하지 않았고 runtime data/.env는 Git 제외다. v1 evidence 전체 파일 SHA256 불변, 연구/cache integrity와 CSV/summary 대조, git diff --check를 통과했다. commit/push/staging하지 않았다. 최종 소스·문서·산출물 비밀값 검사 근거는 data/kiwoom_v2_validation/final_checks.json이다.

<!-- FINAL_GIT_EVIDENCE -->

git diff --stat (tracked files only; 6 new untracked files are listed in status):

```text
 CODEX_HANDOFF.md                       | 479 +++++++++++++++------------------
 README.md                              |   1 +
 market_radar/README.md                 | 128 ++++-----
 market_radar/__main__.py               |   8 +-
 market_radar/calculations.py           |  16 ++
 market_radar/config.py                 |  12 +-
 market_radar/engine.py                 |  42 ++-
 market_radar/provider.py               |   8 +
 market_radar/providers/kiwoom.py       | 122 ++++++---
 market_radar/providers/kiwoom_cache.py |   8 +
 market_radar/providers/kiwoom_rest.py  |   7 +-
 market_radar/providers/kiwoom_ws.py    |   8 +-
 market_radar/runtime.py                |  51 +++-
 market_radar/storage.py                |  38 ++-
 tests/kiwoom_provider_test.py          |  12 +-
 tests/market_radar_audit_test.py       |   6 +-
 tools/kiwoom_provider/smoke.py         |   2 +-
 17 files changed, 549 insertions(+), 399 deletions(-)
```

git status --short:

```text
 M CODEX_HANDOFF.md
 M README.md
 M market_radar/README.md
 M market_radar/__main__.py
 M market_radar/calculations.py
 M market_radar/config.py
 M market_radar/engine.py
 M market_radar/provider.py
 M market_radar/providers/kiwoom.py
 M market_radar/providers/kiwoom_cache.py
 M market_radar/providers/kiwoom_rest.py
 M market_radar/providers/kiwoom_ws.py
 M market_radar/runtime.py
 M market_radar/storage.py
 M tests/kiwoom_provider_test.py
 M tests/market_radar_audit_test.py
 M tools/kiwoom_provider/smoke.py
?? market_radar/providers/kiwoom_types.py
?? market_radar/quality.py
?? tests/kiwoom_quality_test.py
?? tools/kiwoom_provider/audit.py
?? tools/kiwoom_provider/probe_types.py
?? tools/kiwoom_provider/smoke_v2.py
```

<!-- V2_1_WORKTREE -->
## v2.1 final worktree

Includes inherited uncommitted changes. Untracked files appear in status only; no staging, commits or pushes.

git diff --stat:

```text
 CODEX_HANDOFF.md                       | 652 ++++++++++++++++++++-------------
 README.md                              |   1 +
 market_radar/README.md                 | 146 ++++----
 market_radar/__main__.py               |  16 +-
 market_radar/calculations.py           |  16 +
 market_radar/config.py                 |  17 +-
 market_radar/engine.py                 |  55 ++-
 market_radar/provider.py               |   8 +
 market_radar/providers/kiwoom.py       | 197 +++++++---
 market_radar/providers/kiwoom_cache.py |   8 +
 market_radar/providers/kiwoom_rest.py  | 121 ++++--
 market_radar/providers/kiwoom_ws.py    |  13 +-
 market_radar/runtime.py                |  96 ++++-
 market_radar/storage.py                |  42 ++-
 tests/kiwoom_provider_test.py          |  12 +-
 tests/market_radar_audit_test.py       |   6 +-
 tools/kiwoom_provider/smoke.py         |   2 +-
 17 files changed, 976 insertions(+), 432 deletions(-)
```

git status --short:

```text
 M CODEX_HANDOFF.md
 M README.md
 M market_radar/README.md
 M market_radar/__main__.py
 M market_radar/calculations.py
 M market_radar/config.py
 M market_radar/engine.py
 M market_radar/provider.py
 M market_radar/providers/kiwoom.py
 M market_radar/providers/kiwoom_cache.py
 M market_radar/providers/kiwoom_rest.py
 M market_radar/providers/kiwoom_ws.py
 M market_radar/runtime.py
 M market_radar/storage.py
 M tests/kiwoom_provider_test.py
 M tests/market_radar_audit_test.py
 M tools/kiwoom_provider/smoke.py
?? market_radar/console.py
?? market_radar/providers/kiwoom_failures.py
?? market_radar/providers/kiwoom_types.py
?? market_radar/quality.py
?? tests/kiwoom_quality_test.py
?? tests/kiwoom_stability_test.py
?? tools/kiwoom_provider/audit.py
?? tools/kiwoom_provider/probe_failures.py
?? tools/kiwoom_provider/probe_types.py
?? tools/kiwoom_provider/smoke_v2.py
?? tools/kiwoom_provider/smoke_v2_1.py
?? tools/kiwoom_provider/windows_ctrlc_smoke.py
```
