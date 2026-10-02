# Capacity Test Protocol v1

적용: **Market Radar v2.2.2 / Collector v2.1 / kiwoom_radar_v2_1 / research schema 4 / Master schema 1**. Dashboard에서 선택하며 기존 CLI `--collect`는 Manual이다.

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

1. 다음 실제 검증은 Dashboard **Capacity Test / Max WS 150 / grace 0.25**다. open 전에 Start Collection을 누른다. 가능하면 open−30분 이전에 arm한다. 새 session 폴더와 XNYS 날짜/Open/Close/countdown/cache tooltip을 확인한다. 주말·휴일은 다음 거래일을 기다린다. PC 절전과 앱 종료 없이 유지한다.
2. ARMED → PREPARING → PREOPEN_WAIT. lock/DB/cache/auth/WS LOGIN/catalog/type/과거 분봉 backfill을 준비한다. **개장 전 후보 source 조회·후보 WS 배정·rank·snapshot·FIRST_DETECTED·result timer·regular OR/HOD 생성은 금지**다.
3. 공식 open 도달 및 DB/REST 준비·WS 연결 확인 후 REGULAR_LIVE gate를 연다. 새 source refresh → universe → subscription allocation → 유효 가격/rank → FIRST_DETECTED 순서다. gate 개방은 `actual_regular_start_utc`, 첫 source refresh와 첫 batch는 `first_candidate_refresh_utc`, `first_regular_batch_utc`로 따로 기록한다. 동일 시각이라고 가정하지 않는다.
4. 캐시가 incomplete여도 개장을 미루지 않는다. RVOL warming/incomplete_history/NULL을 유지한다. preopen은 catalog 목록을 순회하며 과거 분봉을 요청하고 마지막 15초에는 새 준비 요청을 쉬지만 이미 진행 중인 REST가 첫 discovery를 지연할 수 있다. actual/first refresh 시각에서 확인한다.
5. open+65분 FINALIZING → provider 종료 → CSV/quality/metadata → Master import → STOPPED를 확인한다. Current Active WS=0/Connected=No, Last gauge는 마지막 값이어야 한다. Session History에서 Review ZIP을 생성한다.

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
- 검증한 max WS를 고정해 운영한다. 다음 150-slot Capacity 검증 전에 150을 최적값으로 확정하지 않는다.
- `full_session=true`는 정시 준비/시작/종료 및 준수 조건을 충족한 Regular Session에만 기록한다. 장중 시작은 허용하되 `late_start=true`, `protocol_compliant=false`, 실제 시작 시각을 기록한다.
- 매일 clean shutdown/protocol/saturation/미배정/REST·WS/RVOL/return availability/결과 pending·missing을 확인하고 ZIP을 보관한다. Capacity +65분으로 늦은 후보의 +60분/EOD/D+n이 모두 완성되는 것은 아니다. full-session도 미래 D+n을 즉시 완성하지 않는다.
- 한 번 arm은 **한 거래일**이다. 다음 거래일은 다시 arm한다. 주간 자동 반복·Windows 깨우기·부팅 자동 실행·D+n 자동 스케줄은 포함하지 않는다.

참고: [README](README.md), [기술 상세](market_radar/README.md), [HANDOFF](CODEX_HANDOFF.md), [CHANGELOG](CHANGELOG.md).
