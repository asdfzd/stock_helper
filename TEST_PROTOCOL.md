# Capacity Test Protocol v1

적용: **Market Radar v2.6.8 / Opening Hour Research v1.1 / opening hour schema 1 / Paper v1 / paper schema 2 / Rotation v2 / rotation extension schema 1 / Validator 1 / Collector v2.1 / kiwoom_radar_v2_1 / research schema 4 / Master schema 1**. Dashboard에서 선택하며 기존 CLI `--collect`는 Manual이다. Capacity 조건은 변경하지 않았으며 Paper v1·Rotation v2·Opening Hour 자동 분석은 모두 강제 OFF다.

| 항목 | 고정 기준 |
|---|---|
| Market / 시간 | XNYS / America/New_York (ET), 설치된 `exchange_calendars` 거래 일정 |
| Arm | 선택 거래일 공식 open **이전** |
| 준비 | `preopen_prepare_minutes=30`, open−30분부터 |
| 시작 / 종료 | 공식 open / 공식 open + **65분**, graceful stop |
| Snapshot / pre-target grace | **30초 / 0.25초** |
| Collector 정책 | CONFIG v2.1 기본 정책 전체 고정; ranking/RVOL/NULL/보호 추적/REST 억제 유지 |
| Shared historical cache | `data/market_radar_cache/kiwoom_v2.db`, 실행별 session 밖 |
| 의도한 실험변수 | **max_ws_symbols만 변경** |

1. Dashboard **Capacity Test / Max WS 지정 / grace 0.25**로 arm한다. 첫 150-slot 표준 세션은 2026-10-06 ET에 수집되어 offline 재검증했으며 상세 근거는 CHANGELOG에 둔다. 추가 실험도 open 전에 Start Collection을 누른다. 가능하면 open−30분 이전에 arm한다. 새 session 폴더와 XNYS 날짜/Open/Close/countdown/cache tooltip을 확인한다. 주말·휴일은 다음 거래일을 기다린다. PC 절전과 앱 종료 없이 유지한다.
2. ARMED → PREPARING → PREOPEN_WAIT. lock/DB/cache/auth/WS LOGIN/catalog/type/과거 분봉 backfill을 준비한다. **개장 전 후보 source 조회·후보 WS 배정·rank·snapshot·FIRST_DETECTED·result timer·regular OR/HOD 생성은 금지**다.
3. 공식 open 도달 및 DB/REST 준비·WS 연결 확인 후 REGULAR_LIVE gate를 연다. 새 source refresh → universe → subscription allocation → 유효 가격/rank → FIRST_DETECTED 순서다. gate 개방은 `actual_regular_start_utc`, 첫 source refresh와 첫 batch는 `first_candidate_refresh_utc`, `first_regular_batch_utc`로 따로 기록한다. 동일 시각이라고 가정하지 않는다.
4. 캐시가 incomplete여도 개장을 미루지 않는다. RVOL warming/incomplete_history/NULL을 유지한다. preopen은 catalog 목록을 순회하며 과거 분봉을 요청하고 마지막 15초에는 새 준비 요청을 쉬지만 이미 진행 중인 REST가 첫 discovery를 지연할 수 있다. actual/first refresh 시각에서 확인한다.
5. open+65분 FINALIZING → provider 종료 → CSV/quality/metadata → Master import → Session Validator → validation_summary.json / Master 검증 상태 → STOPPED를 확인한다. Current Active WS=0/Connected=No, Last gauge는 마지막 값이어야 한다. Session History에서 Review ZIP을 생성한다.

09:30~10:35는 일반 거래일의 ET 예시다. UTC/KST offset을 고정하지 않는다. 당일 open 이상~공식 close 미만의 Capacity arm은 **거부**한다(+65분 이후도 장중 거부). 공식 close 이후는 다음 거래일로 arm한다. Manual/장중 Regular는 진단 수집으로 사용할 수 있다.

## 준수 판정과 기록

- 시작·종료 timer 운영 허용 오차는 각각 **1,000ms**(`START_STOP_TOLERANCE_MS`). 실제 delay를 그대로 기록하며 과거 시각으로 소급 저장하지 않는다. open 전 arm과 준비된 연결 관측이 필요하다. open 이후 처음 연결을 확인하면 1초 이내라도 `connection_not_ready_before_open`으로 비준수다. 시작이 1초 초과 지연되면 `delayed_start`, 장중 Regular arm은 `late_arm`도 남긴다.
- scheduler 관측 간격 >5초는 `scheduler_gap_or_resume`, UTC/monotonic 차이 >2초 또는 역행은 `clock_jump`다. open 이후 관측 공백/절전 복귀는 비준수이며 snapshot 소급 생성은 없다. PC 시계의 절대 정확성을 외부에서 인증하는 기능은 아니다.
- 수동 Stop은 `user_requested`, 자동 종료는 `scheduled_capacity_end` 또는 `scheduled_close`. `actual_stop`은 **연구 gate 폐쇄/정상 종료 요청 시각**이다. 전체 collector 종료 완료는 lifecycle `stopped_at`, export까지 소요는 `shutdown_elapsed_ms`다.
- 실행 중 `protocol_compliant=false / in_progress`, 종료 시에만 준수 여부를 확정한다. 시작 실패·수집/export/등록 오류·조기/지연 종료도 비준수다. 준수는 시간·설정 기준이며 feed 품질이나 RVOL 완성의 보증이 아니다. REST/WS 오류/미배정/NULL은 별도 평가한다.
- 저장: `dashboard_run.json`, research DB `radar_metadata.collection_protocol`, `metadata.json`, `quality_summary.json`, Review ZIP `review_manifest.json`. Master는 schema 1의 `quality.summary_json`에 보존하고 History에 주요 필드를 표시한다. 기존 schema ALTER는 없다. 준비 전에 취소하면 lifecycle만 남고 DB/export/Master 행은 없다.
- 필드: mode/name/version/compliant/reason, arm/prepare/connection 시각, ET/UTC 예정 open/close, 예정·실제 start/stop, delay, stop reason, max WS/cache 경로, `armed_before_open`, `late_start`, `full_session`, `regular_duration_seconds`. History의 기존 Duration은 arm을 포함한 collector lifecycle이며 실제 연구 구간은 `regular_duration_seconds`다.

**Non-compliant run은 정량 capacity 비교에서 제외**한다. Manual/late run은 diagnostic evidence로만 사용한다. 기존 20/50/100-slot 세션은 saturation 증거이며 본 프로토콜의 통제 비교 표본으로 소급 지정하지 않는다. legacy protocol 값은 unknown이다. 다른 거래일 시장 상황과 누적 cache coverage/REST 부하도 달라지므로 준수 세션만으로 슬롯 수의 인과 효과/최적값을 단정하지 않는다. 같은 입력의 다중 슬롯 offline replay는 후속 작업이다.

# Weekly Research Collection Protocol

- 매 거래일 **Regular Session**을 preopen arm하고 공식 open 시작 → **공식 close 자동 종료**한다. 조기폐장일은 calendar close를 따른다. 동일 Collector 정책과 shared cache를 유지한다.
- 검증한 max WS를 고정해 운영한다. 첫 150-slot 세션은 capacity PASS이나 한 거래일로 최적값을 확정하지 않는다. 운영 중 saturation과 미배정을 계속 확인한다.
- `full_session=true`는 정시 준비/시작/종료 및 준수 조건을 충족한 Regular Session에만 기록한다. 장중 시작은 허용하되 `late_start=true`, `protocol_compliant=false`, 실제 시작 시각을 기록한다.
- 매일 clean shutdown/protocol/saturation/미배정/REST·WS/RVOL/return availability/결과 pending·missing을 확인하고 ZIP을 보관한다. Capacity +65분으로 늦은 후보의 +60분/EOD/D+n이 모두 완성되는 것은 아니다. full-session도 미래 D+n을 즉시 완성하지 않는다.
- 한 번 arm은 **한 거래일**이다. 다음 거래일은 다시 arm한다. 주간 자동 반복·Windows 깨우기·부팅 자동 실행·D+n 자동 스케줄은 포함하지 않는다.

참고: [README](README.md), [기술 상세](market_radar/README.md), [HANDOFF](CODEX_HANDOFF.md), [CHANGELOG](CHANGELOG.md).

## 30초 Windows 종료 시험의 오프라인 경계 (v2.6.7)

이 버튼은 수집/전략 검증과 분리된 운영 시험이다. 자동화에서는 실제 `shutdown.exe` 요청과 취소를 모두 차단하고 `tests.shutdown_test`의 주입 execute/guard를 사용한다. 생성·일반 GUI 시작·확인창 취소의 OS 호출 0회, 확인 후 정확한 `/s /f /t 30`, 성공/거절/timeout·소유권, monotonic 30→0, `/a` 성공/실패/예약 없음, 중복 클릭·다른 예약·창 닫기·앱 종료, STOPPED/예약/writer/ZIP/검증 gate와 기존 Controller 무변경을 검사한다. 기존 65분·clock·전원·Paper/Rotation/Opening/ZIP 전체 회귀도 유지한다.

패키지 smoke는 새 EXE의 `--offline-gui-smoke <새 경로> --ocr`로 runtime·QSettings·모델·출력을 격리하고 네트워크/자식 프로세스를 차단한다. GUI와 OCR 초기화 PASS는 실제 API 수집·HTS 캡처·Windows 전원 종료 PASS가 아니다. 실제 30초 종료 버튼을 누르는 시험은 사용자가 저장 작업과 수집 종료를 확인한 뒤 직접 수행하며, Codex 자동화에서 실제 Windows 종료/예약/취소 명령을 실행하지 않는다.

## Automatic Session Validator 1

정책 구현은 `market_radar/research/validator.py`의 `ValidatorPolicy`에 모은다. 시작·종료 오차는 scheduler의 `START_STOP_TOLERANCE_MS`, Capacity 길이는 `CAPACITY_MINUTES`, 결과 horizon은 engine의 `HORIZONS`를 재사용한다. 결과 tolerance/grace는 원본 identity를 읽으며 sampling 자체를 바꾸지 않는다.

- **FAIL**: scheduled protocol 비준수/필수 timing·XNYS 일정 불일치/개장 전 research snapshot·event·candidate/regular 수집 미시작, DB 손상·필수 표 누락·참조 불일치·metadata/quality/CSV 불일치, 누락 horizon·결과 sample 불일치, worker/DB/export 오류. Validator 1은 batch 유실을 동반하는 worker/DB counter도 보수적으로 FAIL로 처리한다.
- **WARN**: 슬롯 미배정 또는 capacity 근거 부족, WS gap, REST 오류·억제, incomplete history, 결과 missing 또는 이미 관측 창을 넘긴 pending, Manual/unknown protocol·불충분한 legacy 근거. 필수 export 누락은 legacy에서도 FAIL이다.
- **Coverage 경보 기본값**: RVOL available <50%, stale 관측 비율 >5%, missing result ≥1. 수익성 기준이 아닌 검토용 초기 품질 정책이며 JSON에 실제 사용한 policy를 기록한다. stale 비율은 `stale batch observations / (유효 snapshots + stale batch observations)`이고 counter의 이벤트 수와 분리해 unique ticker도 기록한다.
- **PASS**: 검사한 범위에서 FAIL/WARN 조건 없음. 전략 수익성이나 매수 적합성 판정이 아니다. 종료 이후 목표인 +60m/EOD/D+n pending은 정상이며 그 자체로 WARN/FAIL이 아니다.

검증은 종료된 원본 DB/WAL의 임시 복사본과 export를 읽는다. 원본 DB·SHM·CSV·quality·metadata·lifecycle·log는 수정하지 않는다. 출력은 `<session>/export/<ET-date>/validation_summary.json`이며 atomic replace한다. Master는 schema 1의 `quality.summary_json.validation`만 갱신한다. 검증기 실행/저장 실패는 collector 성공을 변경하지 않고 unavailable로 표시한다. 수집/export/Master 실패로 등록할 수 없어도 DB가 있으면 해당 거래일 검증 JSON을 시도한다.

재검증은 Session History의 **Revalidate Session**으로 실행한다. 원본이 달라지면 기존 validation을 재사용하지 않는다. logical DB 내용과 export/lifecycle SHA256을 확인하므로 단순 SQLite checkpoint는 검증을 무효화하지 않는다. 재import는 동일 evidence의 validation만 보존한다. Review ZIP은 summary의 identity와 freshness를 검사하고 오래된 검증이면 재검증을 요구한다.

CLI/보관 세션의 명시적 검증(JSON만 생성, Master 갱신 없음):

~~~powershell
& .\.venv-paddle\Scripts\python.exe -m market_radar.research.validator data/<session>/stock_helper_market_kiwoom_v2_1.db --date YYYY-MM-DD
~~~

Dashboard 종료 후 자동 검증과 CLI 검증을 구분한다. 기존 CLI 수집은 Manual이며 표준 scheduled 비교 세션으로 소급 지정하지 않는다.

## Opening Hour Research v1 검증과 운영

Regular Session에서 명시적으로 옵션을 켜면 collector/Paper 종료 후 기존 자료를 읽어 첫 60분 descriptive dataset을 생성한다. 기본 OFF, Capacity/Manual 자동 OFF이며 기존 수집 호출·WS·backfill·Paper 판단 경로는 유지한다. 분석 자체의 COMPLETE/PARTIAL/EMPTY는 각각 전체 OHLCV 완전/일부 관측/실제 관측 없음이다. all/eligible coverage와 상태를 따로 보고, 처리 오류의 INCOMPLETE·Session Validator·수익성 PASS와 구분한다.

새 거래일 preopen arm 전에 Regular Session, 기존 max WS/shared cache, Opening Hour 옵션을 확인한다. Paper v1/Rotation v2 동시 shadow는 Paper 탭에서 따로 활성화한다. 공식 close까지 앱·PC를 유지하고 종료 후 validation과 `data/opening_hour_research/export/<ET-date>/<research-id>/quality.json`을 함께 확인한다. 날짜별 연구 폴더를 ZIP 보관해 거래일 간 비교하되 결측·late/Capacity·분봉 시각의 한계를 반영한다. 신규 호출/실제 주문은 0건이다.

개발 검증은 합성 자료·임시 DB/폴더만 사용한다. 정확한 60분 경계/DST·조기폐장, 3m/5m 완전 집계, 0 volume와 결측, 구조·anchor·event 구간 시각, MFE/MAE 및 unaligned detection NULL, 원본 DB/WAL/SHM 보존, DB/CSV 재현성, UI OFF/Capacity 강제 OFF, 종료 순서, REST/WS/NH 호출 차단과 기존 replay 동일성을 검사한다. 실제 세션/cache를 이용한 신규 분석과 live smoke는 별도 운영 검증이다.

~~~powershell
& .\.venv-paddle\Scripts\python.exe -m unittest tests.research_evidence_quality_test -q
& .\.venv-paddle\Scripts\python.exe -m unittest tests.opening_hour_research_test -q
& .\.venv-paddle\Scripts\python.exe -m unittest tests.paper_trading_test tests.paper_rotation_v2_test tests.nhplug_accounts_test tests.nhplug_client_test -q
& .\.venv-paddle\Scripts\python.exe -m unittest tests.market_radar_test tests.market_radar_patch_test tests.market_radar_audit_test tests.market_radar_dashboard_test tests.market_radar_metadata_test tests.market_radar_schedule_test tests.market_radar_validator_test tests.kiwoom_provider_test tests.kiwoom_quality_test tests.kiwoom_stability_test -q
~~~

v2.6.1 보존 회귀는 동일 시각 A→B의 TP/SL crossing과 marks 도착 순서, exact duplicate 전량 저장, observation_id 재시작 연속성, horizon의 첫 post-target/기본 30초 tolerance/경계/NULL·동일시각 tie/run 격리, schema 1 DB/WAL 무변경 거부를 검사한다. Paper strategy와 Rotation dominance는 checkpoint `66589272e013644a94d2e206c4cddbcfa1951383` replay 전체 결과와 비교한다. Opening feature `opening_hour_v1_1`은 동일 session/cache의 root 이동 후 같은 ID·export, metadata 절대경로 미노출, 내용 변경 시 새 ID·기존 dataset 보존, 모든 후보 유지·stock_type_policy·eligible median·all/eligible 분모·결측을 검사한다.

첫 Regular live 전에는 기존 Paper/Opening DB 유무와 버전을 확인한다. legacy Paper schema 1은 자동 변환하지 않으며 원본 보존 후 별도 복사본의 offline migration이 필요하다. 이번 작업 시작 시 실제 대상 DB는 없었고 data 전체는 해시·크기·mtime로 보존 여부를 확인한다. 테스트 출력은 임시 디렉터리만 사용하며 실제 data에 출력을 만드는 통합 validation runner·live smoke는 실행하지 않는다. 종료 후 두 전략의 marks/horizons와 Opening all/eligible coverage·표본 수·metadata를 함께 검수한다.

## 가족용 원클릭 / 독립 90분 감시 검증 (v2.6.8)

- `tests.family_watchdog_test`: 당일 preopen·휴장/늦은 실행·DST/조기폐장, 명시적 경로/키 존재/용량·원본 무변경, 같은 Collector·65분 보존·70분 gate, 등록 실패/중복·취소·정상/비상 경합·다른 작업 미변경, 날짜/기한/부팅·최종 OS 직전 재검사, GUI/후처리 hang·process missing 합성 검사. 실제 OAuth/API/NH/주문/Windows 종료는 차단하고 주입 adapter를 사용합니다.
- `--offline-family-smoke <새 경로>`: 실제 EXE에서 scratch APP_ROOT/QSettings/합성 .env/header/cache, 실제 GUI/Controller의 ARMED와 설정, Mock TaskScheduler·일반 실행 무예약·취소를 확인합니다. 모든 socket·subprocess 및 scratch 밖 쓰기를 차단합니다. 첫 API 준비 전의 가상 preopen이며 실제 인증 성공/실장 수집과 구분합니다.
- 실제 Task Scheduler 시험은 `MarketRadarFamily_TEST_<date>_<id>`와 종료 없는 action만 사용합니다. 현재 로그인 사용자·일반 권한으로 등록/조회/실행 및 별도 PID를 확인하고 해당 task만 삭제합니다. 짧게 설정한 합성 기한은 실제 90분 대기·실장 성공을 뜻하지 않습니다. 반복/boot/logon trigger 없음과 missed-start OFF를 조회하며 재부팅 자체는 자동화에서 수행하지 않습니다.
- 사용자가 감독하는 첫 실장은 기존 수집 앱을 정상 종료한 뒤 전용 설정/버전/경로를 확인하고 `--dry-run`으로 두 종료를 끕니다. 실제 연결·65분/후처리·ZIP/취소를 검토한 뒤 정상 종료 및 비상 강제 종료를 각각 명시적으로 허용합니다. 비상 종료는 DB/ZIP·저장 전 작업 손상 가능성이 있으므로 정상 VERIFIED와 혼동하지 않습니다.

검증 수준은 구현 / 오프라인 Mock / 실제 Task Scheduler dry-run / 실제 미국장 자동 예약·수집 / 실제 강제 종료로 구분합니다. 이전 30초 강제 종료 성공은 사용자 보고이며 watchdog 검증으로 승격하지 않습니다. Windows 시간대/두 시계의 일관성은 절대 UTC 정확성의 외부 인증이 아닙니다.
