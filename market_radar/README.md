# Market Radar v2.2.2 — Dashboard / Kiwoom research v2.1

현재 **Market Radar v2.2.2 / Collector v2.1 / test_version=kiwoom_radar_v2_1 / research schema=4 / master schema=1**의 기술 문서다. 프로그램 소개·설치는 [루트 README](../README.md), 현재 작업 상태는 [CODEX_HANDOFF](../CODEX_HANDOFF.md), 버전별 검증 이력은 [CHANGELOG](../CHANGELOG.md)를 따른다. 주문/계좌/자동매매 기능은 없다.

## Architecture / Provider

`create_live_provider` → KiwoomProvider → RadarWorker → MarketBatch 검증·계산·재정렬 → 연구 DB/event/result/export → 크기 1 메시지 큐 → Qt 표로 이어진다. 기존 캡처/OCR/Registry와 독립된 RadarPanel을 lazy import로 열며 네트워크·계산·저장은 Qt 밖에서 수행한다.

| 모듈 | 역할 |
|---|---|
| config / provider / models | 설정, live/replay 계약, 검증된 batch |
| providers/kiwoom / kiwoom_rest / kiwoom_failures | 후보 합집합, serialized REST/cache scheduler, 제한·실패 분류·억제 |
| providers/kiwoom_ws | 단일 WS 관리 thread, LOGIN/REG/REMOVE ack, FE/optional FT, PING·재연결 |
| providers/kiwoom_cache / kiwoom_calendar / kiwoom_types / kiwoom_lock | raw/OHLCV·XNYS 달력·유형 근거·단일 collector lock |
| calculations / engine / storage / quality | 지표·순위·event/result·연구 DB·CSV·일별 품질 |
| runtime / console / ui / __main__ | worker·종료·표시·CLI |

실서버 REST와 WS를 사용하며 [고정 read-only 계약](providers/kiwoom_contract.json)의 9개 TR만 허용한다: 현재가 usa20100, 종목목록 usa10099, 후보 usa20910/usa20930/usa20530/usa20540, 분봉 usa06011, 일봉 usa06012, 일별주가 usa20590. 제품은 probe output을 import하지 않는다. 거래소 ND/NY/NA와 XNYS/ET 달력을 사용하며 snapshot 시각은 거래소 생성 시각이 아닌 클라이언트 수집 UTC다. WS 단절 시 이전 가격을 폐기하고 gap을 남긴다.

## Dashboard / Master Research DB

기존 `live_ui.py`의 lazy `show_radar` 연결을 확장했다. `ui.py`의 RadarPanel은 Live Radar / Session History / Candidate History 탭, `dashboard_widgets.py`는 NULL·상태를 보존하는 표를 담당한다. `controller.py`는 STOPPED/STARTING/RUNNING/STOPPING/ERROR를 관리하며 기존 RadarWorker를 시작·중지한다. provider 생성·연결, REST/WS/backfill, export는 모두 worker에서 실행된다. runtime의 기존 수집 경로를 유지한다. v2.2.2는 별도 SessionSchedule과 provider/worker gate를 추가하며 순위·연구/cache schema는 그대로다.

UI timer는 메시지와 완료된 Future만 처리한다. Master 조회/import/CSV ZIP/hash와 live health SQL은 별도 단일 research executor에서 실행한다. 수집 지표와 Last gauge는 5초 간격 DB snapshot으로 읽으며 오류 시 마지막 값임을 표시한다. Current Connected/Active WS는 controller thread가 provider의 현재 WS 연결/ack를 읽는다. STOPPED는 No/0이고 Last 값은 변경하지 않는다. RUNNING은 Current Protected/Discovery/Slot unavailable(최근 DB 기준), Current Saturation(현재 ack 기준)을 표시한다. STOPPING은 종료 중 실제 연결 상태를 유지하며 인위적으로 지연하지 않는다. 정상 종료 label은 import 메시지 이후에도 남는다. Stop은 동일 Event/기존 graceful close를 요청하고 중복 클릭을 무시한다. 수집 중 창 닫기도 정상 종료를 기다린다. 앱 종료 시에도 non-daemon controller가 final export와 자동 import를 마친다. 강제 kill은 없다.

Data root 아래 실행 시각+UUID 폴더를 만들고 `dashboard_run.json`에 실제 시작·종료·clean_shutdown·shutdown 시간을 보존한다. max WS 기본 200과 grace 0.25초 등은 CONFIG를 따른다. 200은 최적 capacity 권고가 아니다. QSettings에는 마지막 max WS/data root/창 위치/열 폭만 저장한다. 기존 단일 Kiwoom lock을 그대로 공유하므로 UI/CLI 충돌은 ERROR 및 명확한 메시지로 표시한다.

| 저장 계층 | 역할 |
|---|---|
| Session research DB + raw/export/log | 원본 증거, 연구 schema 4 유지; 과거 schema 1–4 읽기 지원 |
| `data/market_radar_cache/kiwoom_v2.db` | Dashboard persistent historical cache, 기존 OHLCV schema/contract 유지 |
| `data/market_radar/master.db` | 재구축 가능한 세션/후보/결과/품질 index, **master_schema_version=1** (`PRAGMA user_version`) |

`research/master_db.py`는 sessions/candidates/results/quality와 날짜·ticker·session·horizon index를 생성한다. 알려지지 않은 버전은 거부하며 기존 research DB를 ALTER하지 않는다. Master에는 raw snapshot 전체를 복사하지 않는다. 원본 DB/export/log 경로와 정규화된 source identity를 보존한다.

`research/importer.py`는 `mode=ro`+`query_only`+단일 read transaction으로 committed WAL까지 읽는다. 정규화된 DB identity+최초 batch 시각+ET 거래일의 SHA256 기반 run_id를 사용한다. 동일 세션은 transaction 안에서 갱신하며 ID를 유지하므로 D+n 재import가 중복되지 않는다. 최초 batch가 없는 세션은 dashboard run_id 또는 원본 경로를 보조 identity로 사용한다. 기존 DB에 여러 실행이 섞였으면 거래일 단위로 묶고 실행을 임의 분리하지 않는다. 새 UI는 실행별 폴더로 구분한다. 전체 원본 재import로 Master를 재구축할 수 있다.

초기 후보 지표는 FIRST_DETECTED와 정확히 일치하는 snapshot을 사용한다. result status/missing_reason/sample 선택·offset/MFE/MAE 의미를 보존한다. first rank는 각 TOP10의 최초 진입 순위다. 과거 자료에 없는 운영 counter/clean shutdown은 NULL이며 duration은 관측 snapshot 구간임을 표시한다. UI lifecycle이 있으면 duration은 전체 collector 실행 구간이다. 여러 ET 날짜에 걸친 실행의 날짜별 행에도 동일 실행 구간이 표시된다. v3 이하의 세분 REST 오류 수는 unknown이며 과거 API 오류를 REST 오류로 바꾸지 않는다. 최대 saturation은 해당 날짜 batch gauge의 최대값이다.

History는 200행씩 조회한다. Mode/Protocol compliant/Scheduled·Actual start·stop/Delay/Reason 열을 추가했으며 첫 9개 열에 Date/Max WS/Candidates/Snapshots/Max Saturation/Slot unavailable events/REST errors/RVOL/Clean Shutdown을 배치한다. slot_unavailable_count는 누적 이벤트 수이며 마지막 gauge는 summary_json.tracking에 별도로 있다. 기존 master schema 1로 충분해 변경하지 않았다. ticker history는 최근 1,000개 horizon 행을 표시한다. 통계는 관측 표본·품질·결과 상태에 한정하며 추천 점수는 없다. 후보 더블클릭은 `radar_ticker_selected(str)`로 기존 분석 카드 또는 clipboard 준비 흐름에 연결된다. 자동 캡처·Registry 가짜 종목 생성은 하지 않는다.

## Regular-session scheduling (v2.2.2)

Dashboard Collection Mode는 **Manual / Regular Session / Capacity Test**다. Manual과 CLI의 즉시 실행/사용자 종료 흐름은 유지한다. Scheduled protocol은 CONFIG Collector v2.1 정책을 고정하고 max WS, data/cache 경로, preopen lead time만 운영 설정으로 허용한다. scheduled replay와 기존 연구 DB 재사용은 거부한다.

`scheduling.SessionSchedule`은 **STOPPED / ARMED / PREPARING / PREOPEN_WAIT / REGULAR_LIVE / FINALIZING / ERROR**를 관리한다. collector의 STARTING/RUNNING/STOPPING/STOPPED/ERROR와 별개다. ARMED 동안 collector state STARTING은 예약 작업이 진행 중이라는 뜻이며 연구 수집 시작을 뜻하지 않는다. Calendar 조회와 상태 전이는 controller thread, provider IO와 SQLite는 각 worker에서 수행한다. Qt는 mode/phase/collector/XNYS open·close ET/countdown/scheduled·actual UTC/delay/protocol/max WS/cache를 표시한다.

기존 `ExchangeCalendar`의 `exchange_calendars:<version>:XNYS` 일정을 사용해 주말·휴일·DST·조기폐장을 처리한다. open−`preopen_prepare_minutes`(기본 30분)에 준비를 시작하고 lock/cache/auth/REST/WS LOGIN/catalog/type/과거 분봉을 준비한다. cached catalog 순회 backfill은 누락 분을 0으로 만들지 않는다. 마지막 15초에는 새 준비 요청을 보류하며 incomplete cache가 regular gate를 막지 않는다. 이미 진행 중인 REST는 반환까지 기다리므로 first refresh 시각도 보존한다.

Provider는 개장 전 candidate source refresh/배정을 차단하고 Worker는 fetch/engine 양쪽에서 gate와 batch 시각을 확인한다. 따라서 rank/snapshot/FIRST_DETECTED/result timer/regular OR/HOD는 개장 전 생성되지 않는다. open 후 DB/REST 준비 및 WS 연결이 확인되면 gate를 열고 새 source universe부터 시작한다. 실제 gate 개방 `actual_regular_start_utc`, 첫 source refresh `first_candidate_refresh_utc`, 첫 batch `first_regular_batch_utc`를 구분한다. 준비 중 auth connection과 Active WS ack 수는 별개이며 Current/Last 의미를 유지한다.

Capacity는 open+65분, Regular는 공식 close에 50ms controller 점검으로 graceful stop을 요청하며 30초 snapshot 대기를 깨운다. FINALIZING은 provider close/최종 export/quality/metadata/Master import를 포함한다. import 실패는 Session ERROR/비준수이며 collector 수집 성공 상태와 구분하고 수동 import 재시도를 안내한다. 준비 전 또는 DB 생성 전 취소는 lifecycle만 보존한다. 한 번 arm은 한 거래일이며 다음 거래일 자동 반복/절전 깨우기는 없다.

Capacity의 장중 late arm은 명시적으로 거부하고 공식 close 이후에 다음 거래일을 선택한다. Regular late arm은 허용하되 late_start/비준수/실제 시각을 저장한다. 시작·종료 tolerance 1초, 관측 공백 5초, UTC/monotonic drift 2초 기준은 [TEST_PROTOCOL](../TEST_PROTOCOL.md)에 고정했다. 연결이 open 이후 처음 확인되면 1초 이내에도 비준수다. 가짜 open 시각·누락 snapshot 소급 생성은 없다.

Protocol은 lifecycle, `radar_metadata.collection_protocol` JSON, export metadata/quality, ZIP manifest에 기록한다. Master schema 1의 `quality.summary_json`으로 보존하고 History 조회 시 필드를 펼치므로 research schema 4/Master schema 1 migration은 필요하지 않다. legacy 값은 unknown이며 20/50/100 과거 자료를 compliant로 채우지 않는다. `actual_stop`은 gate 폐쇄/stop 요청, lifecycle stopped_at은 collector 종료 완료, regular_duration_seconds는 실제 연구 구간이다. 기존 History duration은 arm을 포함한 controller 구간이다. full_session은 준수 Regular 실행에만 true다.

## Review ZIP

`research/review_export.py`는 선택 세션(선택이 없으면 현재 세션)의 export allowlist `quality_summary.json`, `candidates.csv`, `snapshots.csv`, `events.csv`, `results.csv`, `metadata.json`과 해당 ET 날짜 로그만 묶는다. 없는 파일은 manifest에 기록하며 파일 전체가 없으면 생성하지 않는다. DB/cache/raw/임의 하위 폴더는 포함하지 않는다. `market_radar_review_<ET-date>_<run-id>.zip`을 해당 세션 `reviews/`에 atomic replace로 저장한다.

manifest에는 생성시각·날짜·run_id·test/schema version·max WS·파일명/크기/SHA256을 담는다. 각 파일은 streaming으로 검사하며 인증/토큰/계좌 등 민감정보 키 패턴이 발견되거나 원본 밖 링크이면 ZIP 전체를 거부한다. 민감값을 오류 메시지에 재출력하지 않는다. 기존 안전 ZIP은 실패 시 유지된다. 임의의 무표식 비밀 문자열까지 탐지하는 보장은 없으므로 collector의 기존 공개 필드 allowlist도 유지한다.

수집 중 ZIP은 가장 최근 export 기준이며 파일 묶음 전체의 원자성을 보장하지 않는다. 검수 전달에는 정상 종료 후 생성이 권장된다. 과거 하나의 폴더가 여러 실행을 포함하면 날짜 로그도 여러 실행을 포함할 수 있다. UI auto-import는 final export 뒤에만 실행되며 import 실패가 수집 성공을 실패로 바꾸지 않는다. Import Existing Session으로 재시도한다.

## 실행

~~~powershell
& .\.venv-paddle\Scripts\python.exe -m pip install -r market_radar/requirements-kiwoom.txt
& .\.venv-paddle\Scripts\python.exe -m market_radar
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 20 --data-dir data/kiwoom_week_v2_1
# 합성 fixture replay, live와 별도 DB
& .\.venv-paddle\Scripts\python.exe -m market_radar --replay tests/fixtures/market_radar_replay.jsonl --fast
~~~

명령은 프로젝트 루트에서 실행한다. Kiwoom real 모드의 로컬 인증 설정을 connect 시 읽으며 값을 출력/저장하지 않는다. 창을 여는 것만으로 수집하지 않으며 시작 버튼 또는 --collect가 필요하다. 로컬 collector lock으로 동시 실행을 막는다. 다른 PC/프로그램의 Kiwoom WS 세션은 별도로 확인한다. Ctrl+C/중지 버튼은 정상 종료 및 export를 요청한다.

## 데이터 정책

- 네 source(당일 등락률 CHG, 5분 급등 SPIKE, 거래량 VOL, 거래대금 VALUE)를 합쳐 중복 제거한다. 일부 source 실패 시 기존 universe를 유지한다.
- Current Leaders는 day_change_pct, Emerging은 완료 1분봉의 return_5m 내림차순이며 동률 ticker 순이다. 공식은 v1과 같다. 유효 실시간 가격을 받은 구독 후보 부분집합의 순위다.
- RVOL은 개장부터 완료 분 cutoff까지 누적 volume / 과거 14거래일의 동일 경과분 평균이다. 30초 분할·보간·snapshot fallback을 하지 않는다.
- 무체결 분 판정은 **MIXED_OR_UNCERTAIN**이다. timestamp 누락을 volume=0으로 바꾸지 않는다. OHLC/High/Low/Close/HOD/OR을 만들지 않는다. 실제 API가 준 유효 0-volume 봉은 사용할 수 있다.
- 분봉 시간은 연구 가정 assumed_et_start_v1이다. 원문 cntr_tm을 보존하며 ET 분 시작으로 해석하고 2초 유예 후 완료 봉만 쓴다. 캐시에 진행 중으로 들어온 봉은 재조회 없이 완료 승격하지 않는다. 24시 이상 및 정규장 밖 자료는 raw만 보존한다.
- return은 cutoff의 완료 close와 정확히 1/5분 전 close를 비교한다. OR1/OR5/HOD와 volume은 해당 구간 봉의 연속성이 있어야 한다.
- VWAP는 NULL이다. 일봉 보완 EOD/D+n은 provisional, 미래는 pending이다. 무료 나스닥 토탈뷰를 전 거래소 통합 SIP로 간주하지 않는다.

## RVOL eligibility와 상태

| RadarConfig 설정 | 기본값 | 의미 |
|---|---:|---|
| rvol_backfill_enabled | true | 과거 RVOL 이력 조회 활성화 |
| rvol_backfill_min_day_volume | 0 | usa20100 acc_trde_qty 최소값 |
| rvol_backfill_min_price | 0 | usa20100 cur_prc 최소값 |
| result_pre_target_grace_seconds | 0.25 | post-target 샘플이 없을 때 허용하는 목표 직전 초 |
| rest_negative_ttl_seconds | 21600 | 확정 실패의 재검증 간격(6시간); ET 날짜 변경 시 초기화 |
| result_tracking_hold_minutes | 60 | 최소 결과 추적 시간; 최대 intraday horizon보다 짧아도 보호 유지 |

CLI: --result-pre-target-grace-seconds, --rvol-backfill-min-day-volume, --rvol-backfill-min-price, --disable-rvol-backfill. 양수 기준을 켰을 때 당일 quote가 없으면 eligibility 미달이다. 조건에 미달해도 Radar 후보·당일 분봉·결과 추적을 제거하지 않는다. 캐시가 있어도 not_eligible은 RVOL NULL이다.

rvol_status는 available, warming, incomplete_history, not_eligible, zero_denominator, unknown이다. 과거/현재 구간 누락은 incomplete, 아직 이력을 채우는 중이면 warming이다. v1 표본은 하루 거래량이 높아도 과거 분봉이 불완전해 특정 양수 임계값의 근거가 부족했다. 기본 0을 유지한다.

## 결과 추적과 슬롯

FIRST_DETECTED는 두 표 TOP10 진입 시 유효 가격으로 기록한다. tracking_protected_until은 첫 포착 + max(60분, 설정 hold), 정규장 종료로 제한한 뒤 결과 tolerance 30초를 더한다. 보호 상태는 DB에 저장되어 재시작 때 복원된다.

슬롯 순서는 기존 보호 중 구독 → 추가 보호 대상 → 현재 discovery → 기타 pending 추적이다. 보호 중에는 source/ranking 이탈만으로 REMOVE하지 않는다. 슬롯 수를 넘어 보호 대상이 생겨도 기존 보호 구독을 유지하고 미등록 종목의 사유를 기록한다. 결과 데이터 완전성을 위한 네트워크 배정이다.

20은 smoke/test 규모이며 전체 후보를 추적하는 주간 운영 권장값이 아니다. 기존 50-slot에 이어 100-slot 보관 세션도 최대 saturation 100%로 확인했다. 다음 사용자 검증은 150-slot capacity test다. 기존 max_ws_symbols 설정과 CLI를 유지하며 200은 공식 상한이다. 운영값은 capacity smoke 결과로 결정한다.

슬롯이 차도 전체 discovery는 radar_discoveries 및 raw UNIVERSE에 남긴다. 신규 종목은 tracking_slot_unavailable, awaiting_subscription 등으로 구분한다. 유효 가격 없는 종목의 FIRST_DETECTED/결과 기준가를 만들지 않는다.

단기 결과는 target~target+30초의 가장 빠른 실제 snapshot을 우선한다. 해당 창이 끝날 때까지 post-target 샘플이 없으면 target-0.25초 이상~target 미만 중 가장 가까운 snapshot을 사용한다. 유예는 result_pre_target_grace_seconds로 설정하며 0이면 비활성이다. 30초 tolerance는 유지한다. sample_timestamp/sample_offset_seconds/sample_selection(post_target 또는 pre_target_grace)을 DB/CSV에 보존하며 보간하지 않는다.

단기 결과의 complete/missing/pending/outside_session 의미는 유지한다. missing_reason은 목표~tolerance 구간에 보존된 batch metadata에서 확인한 stale_price, not_subscribed, ws_gap, tracking_slot_unavailable 또는 근거가 없을 때 no_snapshot_within_tolerance다. 장 밖 목표는 outside_session이다. 원인을 추정하여 채우지 않는다.

## 공식 종목 유형 근거와 필터

공식 usa10099에서 mkgb는 거래소명, upgb는 업종명, isEtf는 ETF 여부다. 별도 보통주/워런트 enum으로 해석하지 않는다.

- isEtf=Y → ETF. 실제 HYG/TQQQ/SOXL 확인.
- 공식 한국명 워런트 + 영문명 C/WTS 또는 WARRANT → warrant. 실제 ARBEW/WALDW/BZFDW/SHFSW의 일관된 명시적 상품명 근거다. 공식 enum이라고 주장하지 않는다.
- 공식 한국명 유닛 + 영문명 UNIT(S) → other. v1 FVNNU 표본 확인.
- 양 언어 공식명에 명시적 ETN 표기 → ETN. 실제 ETN 표본은 이번에 검증하지 않았다.
- 비ETF이며 공식 영문명에 명시적 COMMON STOCK/ORDINARY SHARES, 상충하는 warrant/unit/preferred/right 표기가 없으면 common. 이 규칙은 합성 테스트로만 검증됐다.
- 나머지는 unknown. AAPL/NVDA의 isEtf=N도 common 확정 근거가 아니므로 unknown으로 보존한다. ticker suffix는 사용하지 않는다.

기본 stock_type_policy=common_and_unknown은 common/unknown을 포함하고 확정된 나머지는 제외한다. all로 변경 가능하다. 제외된 discovery는 excluded_symbol_type, 원본 public fields, source와 판정 규칙을 남긴다.

## 파일·schema·운영

~~~text
data/
  stock_helper_market_kiwoom_v2_1.db    # research schema 4, test_version kiwoom_radar_v2_1
  market_radar_cache/kiwoom_v2.db       # unchanged OHLCV cache contract/schema 1
  logs/YYYY-MM-DD.log                  # ET date, UTC log timestamps
  export/YYYY-MM-DD/{candidates,snapshots,events,results}.csv
  export/YYYY-MM-DD/metadata.json
  export/YYYY-MM-DD/quality_summary.json
~~~

위 구조는 기존 CLI 기본 동작이다. CLI는 `--data-dir` 아래 research/export/log/cache를 생성한다. 명시적인 `--cache-path <DB 파일>`을 주면 cache만 분리한다. 옵션을 생략한 기존 CLI 스크립트의 의미는 바꾸지 않았다. `RadarConfig.historical_cache_path=None`은 legacy 동작, `config.cache_path`는 최종 경로다.

Dashboard/controller는 시작 전에 historical_cache_path를 `APP_ROOT/data/market_radar_cache/kiwoom_v2.db`로 지정한 뒤 session data_dir만 실행별 폴더로 바꾼다. 명시된 cache 경로는 존중한다. APP_ROOT는 개발 시 저장소 루트, frozen 앱은 실행파일 디렉터리다. Data root 변경은 canonical cache/Master 위치를 바꾸지 않는다. Header의 Historical Cache tooltip에서 전체 경로를 확인할 수 있고 replay는 cache 미사용으로 표시한다.

~~~text
data/
  market_radar_cache/kiwoom_v2.db       # Dashboard shared persistent cache
  market_radar/master.db               # shared summary/index
  YYYYMMDD_HHMMSS_uuid/
    stock_helper_market_kiwoom_v2_1.db  # session research
    dashboard_run.json
    export/YYYY-MM-DD/
    logs/YYYY-MM-DD.log
    reviews/
~~~

이번 감사 시 canonical cache는 없었다. 다음 실제 provider connect 때 생성하고, 이후 compatible cache를 재사용한다. 기존 session cache 여러 개를 자동 복사/merge하지 않는다. 필요하면 별도 명시적 import/migration 작업으로 검토한다. cache의 provider=kiwoom/mode=real/schema=1/bar_policy=assumed_et_start_v1 metadata 검증을 DDL/journal 변경 전에 그대로 수행하며 incompatible cache를 ALTER하지 않는다. 기존 cache에는 minute/daily/catalog뿐 아니라 raw_records도 있으므로 shared cache 사용 시 raw도 그 DB에 누적된다. raw 분리/자동 삭제는 이번 변경에 포함되지 않는다.

공유는 매 실행 cold cache 구조를 없애는 변경이며 RVOL available을 보장하지 않는다. 부족한 history와 무체결 불확실 분은 기존처럼 NULL/MIXED_OR_UNCERTAIN이다. 실제 누적 효과는 다음 150-slot 수집에서 검증한다.

~~~powershell
# 다음 사용자 capacity test: CLI에서도 공유 cache를 명시하는 경우
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 150 --data-dir data/kiwoom_capacity_150_next --cache-path data/market_radar_cache/kiwoom_v2.db
~~~

정상 초기 universe 준비는 INFO kiwoom_universe_warming_up, 첫 성공 후 갱신 시간이 초과하면 WARNING kiwoom_universe_stale다. 실제 REST/인증/JSON/DB/worker 장애는 ERROR다.

Quality summary는 300초 export와 정상 종료 때 갱신한다. snapshot/candidate/RVOL/return/result는 SQLite에서, API/DB/worker/export·historical backfill·WS/subscription·raw drop은 구조화된 ET 날짜별 counter에서 계산한다. counter는 동일 연구 DB에 누적되어 재시작에도 유지된다. 백필 count는 unique cache 일수가 아닌 완료/불완전/오류 시도 횟수, subscription count는 ack된 종목 수다. 날짜별 discovery source는 중복 없는 종목별 source 합집합, type은 마지막 확인값이다. 아직 snapshot이 없으면 비율은 NULL이다. tracking은 마지막 수집 batch의 gauge다. 정상 종료 REMOVE는 counter에 반영되며 gauge는 마지막 batch 시점 값이다.

SQLite와 raw cache가 원본이고 summary는 보조 자료다. CSV/JSON은 파일별 임시파일 교체 방식이며 전체 파일 묶음이 원자적이지는 않다. 갑작스러운 전원 차단 전 아직 DB로 flush하지 않은 counter는 소실될 수 있다.

REST 일반 3회/초·한국시간 09~10시 2회/초, 종목리스트 4회/60초, backfill worker 1개, WS 최대 200개다. optional 의존성은 requirements-kiwoom.txt를 사용한다. 네트워크/cache/연구 DB/export는 Qt 밖에서 수행한다.

## REST 실패와 종료

HTTP 429/5xx, timeout/connection error 및 확인된 rate-limit/auth-expiry 코드만 제한된 재시도를 한다. 공식 종목 조회 불가(1901/1902/1903), 지원 불가, 잘못된 요청 및 성공한 빈 initial chart는 동일 ticker/TR/query/cursor/ET 날짜별 negative cache로 억제한다. TTL(6시간) 또는 날짜 변경 후 재조회하며 영구 종목 blacklist는 없다. 미확인 API/parse 오류는 unclassified로 남기고 60초 cooldown을 둔다. 실제 실패만 API counter에 반영하고 억제 호출은 별도 suppressed_count로 센다. 정상 빈 chart는 no_data이며 api_error에 더하지 않는다.

백필 complete/incomplete/skipped/deterministic_unavailable/transient_error/unclassified_error를 구분하고 기존 error 필드를 유지한다. incomplete와 종료 취소는 API/worker 오류가 아니다. quality_summary v2에는 REST 오류 및 ticker/TR/HTTP/code 상위 10개, shutdown 요청·취소·소요시간, 결과 sample 선택 통계, slot_saturation_pct가 추가됐다. raw REST_ERROR에는 공개 필드와 비밀값이 제거된 메시지만 저장한다.

Ctrl+C 수신 즉시 새 scheduling/REST retry/WS REG를 중단한다. 현재 bounded request가 반환되면 미완료 백필 jobs 취소, WS REMOVE/disconnect, raw cache flush/close, 연구 DB checkpoint, CSV/quality export를 수행한다. 추가 Ctrl+C는 같은 종료 상태를 사용한다. shutdown_requested부터 shutdown_complete/program_stop까지 로그로 확인한다. 정상 환경에서 15~20초 이내를 목표로 하며 강제 thread/process 종료는 쓰지 않는다.

## Known Limitations

- 20/50/100슬롯의 saturation을 확인했으며 다음 사용자 검증은 150-slot capacity test다. 장기 부하와 전체 후보 추적 여부는 별도 검증해야 한다.
- 봉 라벨/무체결 분/FE14 notional/FID290 enum은 미확정이다. 무료 feed를 전 거래소 SIP로 보장하지 않으며 결측을 합성하지 않는다.
- 공식 확정 EOD·기업행동 보정·완료 cache의 정정/분할 자동 refresh는 미구현이다. raw 보관기간·압축·자동 삭제도 없다.
- type unknown에는 확인하지 못한 비보통주가 남을 수 있다. negative TTL 안의 데이터 회복은 즉시 감지하지 못할 수 있다.
- 실제 강제 단절/재연결 부하, 돌발 휴장, 1주 연속 수집 및 Radar EXE 배포 검증은 별도 과제다. 기존 테스트·smoke의 구체적인 범위는 CHANGELOG에 기록한다.

## 검증 도구

~~~powershell
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/run_validation.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/audit.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/probe_types.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/smoke_v2_1.py --seconds 1200 --max-ws-symbols 20 --data-dir data/kiwoom_smoke_v2_1_new
# Windows: 별도 숨김 콘솔에서 10분 수집 후 실제 CTRL_C_EVENT 전달
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/windows_ctrlc_smoke.py --seconds 600 --data-dir data/kiwoom_smoke_v2_1_ctrlc_new
~~~

audit는 v1 evidence를 read-only SQLite로 열고 다른 폴더에 결과를 쓴다. probe는 인증 및 공식 시장 종목목록만 읽는다. smoke_v2_1은 정규장 여부를 확인하며 새 data-dir을 요구하고 provider의 구독 선택을 덮어쓰지 않는다. 일반 smoke의 timer 종료는 Ctrl+C 검증이 아니다. 직접 Ctrl+C를 누르거나 windows_ctrlc_smoke.py로 OS console event를 전달한다.
