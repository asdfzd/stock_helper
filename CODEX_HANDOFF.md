# CODEX HANDOFF

갱신: 2026-10-03 KST. 현재 개발 상태만 기록한다. 영구 이력은 [CHANGELOG](CHANGELOG.md), 실행법은 [README](README.md), 기술 상세는 [Market Radar README](market_radar/README.md), 실행 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md), 작업 규칙은 [AGENTS](AGENTS.md)를 따른다.

## 현재 버전 / 범위

- **Market Radar v2.2.2 / Collector v2.1 / test_version=kiwoom_radar_v2_1 / research schema 4 / master_schema_version 1**. 기반 HEAD=`faedfa2`. 이번 변경은 미커밋이며 add/commit/push하지 않았다.
- 기존 Dashboard/shared cache 구현을 바탕으로 scheduled collection을 추가했다. OCR/Registry/벽/Toss/halt, Current/Emerging 공식, RVOL/NULL/MIXED_OR_UNCERTAIN, 보호 추적/결과 샘플/REST 억제를 유지한다. 주문/계좌/추천 점수를 추가하지 않았다.
- PROJECT_DIRECTION 및 RESEARCH_BASIS는 저장소에 없으며 새로 만들지 않았다. 이전 상세 검증·20/50/100 실제 증거는 CHANGELOG에 보존되어 있다.

## 실행 / 상태

| Mode | 시작 | 종료 |
|---|---|---|
| Manual | Start 즉시, 기존 CLI 포함 | 사용자 Stop |
| Regular Session | preopen arm → 공식 open, 장중 late도 허용 | 공식 close, 조기폐장 포함 |
| Capacity Test | preopen arm 필수 → 공식 open | 공식 open+65분 |

- Session state: **STOPPED → ARMED → PREPARING → PREOPEN_WAIT → REGULAR_LIVE → FINALIZING → STOPPED**, 실패는 ERROR. 준비 구간에 따라 일부 상태를 건너뛸 수 있다. collector STARTING/RUNNING/STOPPING/STOPPED/ERROR와 별개다.
- `scheduling.py`의 clock/calendar 주입 가능한 상태기계, controller의 50ms 감독, provider/worker 양쪽 regular gate다. Calendar는 기존 `ExchangeCalendar`/설치된 `exchange_calendars` XNYS와 America/New_York를 사용한다. 주말·휴일·DST·조기폐장은 calendar에서 계산한다.
- 기본 `preopen_prepare_minutes=30`. 준비: lock/session DB/shared cache/auth/REST/WS LOGIN/catalog/type/과거 분봉 backfill. 마지막 15초에는 새 준비 요청을 보류한다. 진행 중 REST가 첫 source refresh를 지연할 수 있다.
- 금지: open 전 candidate source refresh/배정/rank/snapshot/FIRST_DETECTED/result clock/regular OR/HOD. open 후 DB/REST 준비와 WS 연결을 확인하고 새 universe부터 시작한다. incomplete cache는 gate를 막지 않는다.
- 실제 gate 개방 `actual_regular_start_utc`, `first_candidate_refresh_utc`, `first_regular_batch_utc`를 별도로 보존한다. actual_stop은 gate 폐쇄/종료 요청이며 lifecycle stopped_at은 collector 종료 완료다.
- Capacity는 open 이상~공식 close 미만 arm을 거부한다. 공식 close 이후/휴일은 다음 거래일을 선택한다. Regular late는 실제 시작/late_start=true/비준수를 기록한다. 사용자 Stop=user_requested. connection 지연/clock jump/resume/누락 open을 소급 생성하지 않는다.
- FINALIZING은 provider close/최종 CSV·quality·metadata/Master import까지다. 등록 실패는 session ERROR/비준수로 표시하고 수동 import를 안내하며 collector 성공과 구분한다. DB 생성 전 취소는 lifecycle만 남는다.
- 한 arm은 한 거래일. Weekly protocol은 매 거래일 preopen에 다시 arm해 open~official close를 수집한다. 자동 주간 반복/Windows 깨우기/부팅 실행은 없다.

## Protocol / 저장

- Capacity v1: XNYS/preopen arm/open~+65분/snapshot 30초/grace 0.25/동일 CONFIG v2.1 정책/shared cache/max WS만 실험변수. 정시 timer 허용 오차 1초, 연결 open 전 관측 필수, scheduler gap >5초 또는 UTC/monotonic drift >2초 등은 비준수다. 자세한 기준은 TEST_PROTOCOL을 따른다.
- `collection_protocol`: mode/name/version/compliant/reason, arm/prepare/connection 시각, ET/UTC 예정 open·close, 예정·실제 start·stop, delay/stop_reason, max WS/cache, armed_before_open/late_start/full_session/regular_duration_seconds.
- 저장 위치: dashboard_run.json, research DB radar_metadata JSON, metadata.json, quality_summary.json, ZIP review_manifest.json. Master는 기존 quality.summary_json을 사용하고 History에서 Mode/compliant/scheduled/actual/delay 등으로 표시한다. 연구 schema 4/Master schema 1 유지, ALTER 없음, legacy protocol은 unknown.
- Session별 연구 DB/log/export/reviews와 공유 cache/Master를 분리한다. canonical cache: **C:/Users/Choi/projects/stock_helper/data/market_radar_cache/kiwoom_v2.db**, Master: data/market_radar/master.db. 캐시 contract 검증/NULL 정책을 유지하며 legacy cache 자동 복사/병합은 없다. raw도 cache에 누적되지만 ZIP에는 포함하지 않는다.
- STOPPED Current Active=0/Connected=No, Last Active/Protected/Discovery/Unavailable/Saturation은 마지막 batch. WAIT connection은 subscription과 별개다. 기존 History Duration은 arm을 포함한 controller 구간, 실제 연구 구간은 protocol.regular_duration_seconds다.
- Review ZIP은 export 6종+당일 로그 allowlist/민감 필드 검사/hash/atomic replace를 유지한다. 수집 중 ZIP은 export 시점 기준이며 종료 후 생성 권장이다. Master import는 source read-only/transaction/idempotent이며 미래 schema는 거부한다.

## 검증 / 제한

- 전체 회귀 **233 통과 / 실패 suite 0**(기존 207 + 신규 schedule 26). fake clock/합성 provider/임시 SQLite/offscreen UI이며 실제 live smoke가 아니다. runner: tools/kiwoom_provider/run_validation.py, 근거: data/kiwoom_provider_validation/tests_summary.json 및 suite 로그.
- 기존 20/50/100은 saturation 증거로 유지하되 **새 standardized protocol의 통제 비교로 소급 지정하지 않는다**. Non-compliant/Manual/late는 정량 capacity 비교에서 제외한다. 다른 날짜 시장/cache 상태도 달라 슬롯 인과 효과나 최적값을 단정할 수 없다.
- 새 live smoke/150-slot 수집은 하지 않았다. 실제 개장 지연/REST·WS/절전/공유 cache coverage/RVOL 축적/1주 운영은 사용자 검증이 필요하다. same-input 다중 슬롯 offline replay는 후속 작업이다.
- 기존 feed 범위/봉 라벨·무체결 분·unknown 유형/EOD provisional 제약은 그대로다. Capacity 종료 시 늦은 후보 +60분/EOD/D+n은 pending일 수 있다. 자동 D+n 수집은 추가하지 않았다.

## 다음 실제 작업

1. **Capacity Test / Max WS 150 / grace 0.25**, XNYS open 전에 Start Collection으로 arm. 가능하면 open−30분 이전 준비. canonical cache tooltip/선택 거래일/countdown 확인.
2. PC와 앱을 유지하고 official open 자동 전환/actual delay → open+65분 자동 FINALIZING/STOPPED까지 기다린다. 수동 Stop이면 비준수다.
3. Protocol/Current 0·No/Last gauge/clean shutdown, peak protected/discovery/saturation/미배정, REST·WS/RVOL 상태를 확인하고 Review ZIP 생성. 150도 포화되면 200 테스트를 검토하되 자동 default 변경은 하지 않는다.
4. 주간 운영은 검증한 고정 max WS로 Regular Session을 매 거래일 preopen arm하고 공식 close까지 수집한다.

~~~powershell
& .\.venv-paddle\Scripts\python.exe -m market_radar
& .\.venv-paddle\Scripts\python.exe live_ui.py
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/run_validation.py
~~~

CLI --collect는 Manual이며 standardized Capacity 실행을 대신하지 않는다.

## Git / 완료 점검

기반 HEAD faedfa2. 이번 변경만 미커밋이며 사용자 변경을 reset/restore하지 않았다. git add/commit/push 없음. data/DB/log/export/인증 파일을 Git에 추가하지 않는다. 기존 evidence와 운영 Master 38개 파일 SHA256 전후 동일. .env 수정 없음. diff --check/변경 범위/버전/문서 상대 링크/비밀정보 패턴 검사를 완료했다.

`git diff --stat`: 16 files changed, 353 insertions(+), 178 deletions(-). 미추적 3개 파일은 이 stat에 미포함.

~~~text
 M CHANGELOG.md
 M CODEX_HANDOFF.md
 M README.md
 M market_radar/README.md
 M market_radar/__init__.py
 M market_radar/config.py
 M market_radar/controller.py
 M market_radar/dashboard_widgets.py
 M market_radar/providers/kiwoom.py
 M market_radar/quality.py
 M market_radar/research/importer.py
 M market_radar/research/master_db.py
 M market_radar/research/review_export.py
 M market_radar/runtime.py
 M market_radar/storage.py
 M market_radar/ui.py
?? TEST_PROTOCOL.md
?? market_radar/scheduling.py
?? tests/market_radar_schedule_test.py
~~~
