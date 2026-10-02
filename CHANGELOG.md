# Changelog

버전별 개발·검증 이력을 보존한다. 현재 실행법은 [README](README.md), 기술 계약은 [Market Radar README](market_radar/README.md), 현재 개발 상태는 [CODEX_HANDOFF](CODEX_HANDOFF.md)를 따른다.

날짜는 확인된 Git commit 날짜와 실제 검증 시각을 구분한다. 별도 릴리스 날짜·commit이 확인되지 않은 단계는 임의로 채우지 않는다. 프로그램 버전과 research schema 버전은 별개다. 아래 Known limitations는 각 버전 당시의 상태이며 이후 해결 여부는 다음 버전을 참조한다. 로컬 data 경로는 증거 위치이며 Git에 포함하지 않는다. 공용 tests_summary.json은 후속 실행으로 갱신되므로 과거 테스트 수는 당시 commit의 문서 기록을 근거로 한다.

## Market Radar v2.2.1 — Persistent cache / Current vs Last Health

개발·read-only 감사: 2026-10-03 KST, 미커밋. 앱 **v2.2.1**, Collector v2.1 / test_version=`kiwoom_radar_v2_1` / research schema=4 / master schema=1 / OHLCV schema=1 유지.

### Changed

- 기존 provider가 `config.data_dir/market_radar_cache/kiwoom_v2.db`를 사용하므로 Dashboard 실행별 새 폴더마다 cache도 새로 생기는 것을 코드와 100-slot 실제 파일에서 확인했다.
- `RadarConfig.historical_cache_path`를 분리했다. Dashboard/controller는 기본으로 `APP_ROOT/data/market_radar_cache/kiwoom_v2.db`를 공유하고 session research DB/export/log와 Master는 기존 역할을 유지한다. Data root가 바뀌어도 canonical cache는 이동하지 않는다.
- canonical cache는 이번 감사 시 없었다. 다음 실제 수집에서 생성하며 이번 작업에서 운영 cache를 생성하거나 legacy cache를 복사/병합하지 않았다. 기존 provider/mode/schema/bar_policy 검증을 DDL 전에 그대로 수행한다. incompatible cache는 ALTER하지 않는다. legacy 이관이 필요하면 별도 명시적 import/migration으로 검토한다.
- CLI `--data-dir`의 기존 cache 위치는 유지한다. `--cache-path <DB 파일>`을 명시한 경우만 그 cache를 사용한다. 기존 CONFIG max WS=200 및 사용자 저장값은 변경하지 않았으며 150/200을 자동 권장/default로 설정하지 않는다. 다음 테스트는 사용자가 150을 명시한다.
- `Current Connected`와 `Current Active WS`는 controller가 provider의 실제 WS ack/connection 상태를 읽어 표시한다. STOPPED는 No/0, 마지막 batch gauge는 `Last Active WS/Protected/Discovery/Slot unavailable/Slot saturation %`로 보존한다. RUNNING의 protected/discovery/slot unavailable은 5초 DB 갱신 기준이며 Current Saturation은 현재 ack 수 기준이다. STOPPING은 종료 중 실제 연결을 표시하고 인위적 지연을 추가하지 않는다.
- `Stopped cleanly · shutdown … ms`를 별도 label로 유지해 auto-import 메시지에도 사라지지 않는다. 이 시간은 final CSV/첫 quality export까지다. Historical Cache 경로는 짧은 label+전체 경로 tooltip으로 표시한다.
- Session History 첫 열 묶음에 Max WS/Candidates/Snapshots/Max Saturation/Slot unavailable events/REST/RVOL/Clean Shutdown을 배치했다. 기존 quality.slot_unavailable_count는 **이벤트 수**, summary_json.tracking은 **마지막 gauge**다. 기존 Master에 필요한 값이 이미 있어 schema bump/운영 DB 갱신은 하지 않았다.
- 순위·RVOL NULL·MIXED_OR_UNCERTAIN·보호 추적·샘플링·REST 억제·정상 종료·Review ZIP allowlist/검사/manifest/hash/atomic replace는 유지했다. shared cache 전체는 ZIP에 넣지 않는다. 기존 cache 안의 raw_records도 공유 DB에 남으며 raw 삭제/분리는 이번 범위 밖이다.

### Actual 100-slot capacity evidence

원본 `data/20261002_223750_e6b4cd6b`, **2026-10-02 ET** 운영. 이번 작업은 보관 evidence를 읽은 감사이며 새 live 수집이 아니다. research DB의 SQL/batch metadata/counter, CSV 4종, quality_summary.json, dashboard_run.json, session log, cache REST_ERROR raw, 운영 Master를 대조했다. summary 재계산과 export가 동일했고 임시 Master 재import의 주요 12개 필드 및 결과 1,067행이 운영 Master와 일치했다.

| 항목 | 원본 재계산 |
|---|---:|
| 실행 UTC | 13:37:50.300549 ~ 14:39:13.611360 |
| duration (controller lifecycle) | 3,683.310811초 (61분 23.311초) |
| max_ws_symbols | 100 |
| candidates / snapshots / unique snapshot tickers | 97 / 8,644 / 163 |
| 최대 Active WS / Protected / Discovery | 100 / 97 / 80 |
| 마지막 Active WS / Protected / Discovery | 100 / 82 / 77 |
| 최대 / 마지막 slot unavailable gauge | 15 / 15 |
| slot unavailable 이벤트 | 86 |
| 최대 / 마지막 saturation | 100% / 100% |
| REST 분류 total / deterministic / transient / unclassified | 33 / 33 / 0 / 0 |
| REST unsupported / no_data / suppressed | 21 / 12 / 26 |
| API error / transport / parse | 21 / 0 / 0 |
| stale / WS gaps / raw dropped | 913 / 0 / 0 |
| RVOL available / warming / incomplete_history snapshots | 0 (0%) / 1,509 / 7,135 |
| DB / worker / export errors | 0 / 0 / 0 |
| clean_shutdown / 표시 shutdown elapsed | true / 422ms |
| 로그 shutdown_complete / program_stop까지 | 약 484ms / 약 484ms |
| 종료 시 취소 historical jobs | 159 |

REST raw 33건도 deterministic unsupported 21 + no_data 12로 일치했다. 21건은 HTTP 200/top-level 7/embedded 1903, 12건은 HTTP 200/return_code 0의 no_data이며 API 오류 21건과 구분한다. TR별 usa06011=29, usa06012=2, usa20100=2. ticker별 NSTR=14, ACCV=12, SAIQ=7. cache_days는 complete 148/incomplete 723이며 unique cache day와 backfill 시도 횟수는 다르다. RVOL 0%를 cache 분리만으로 해결했다고 주장하지 않는다.

| Horizon | complete | missing | pending | outside_session |
|---|---:|---:|---:|---:|
| +30s | 94 | 3 | 0 | 0 |
| +1m | 92 | 5 | 0 | 0 |
| +3m | 89 | 8 | 0 | 0 |
| +5m | 90 | 6 | 1 | 0 |
| +10m | 87 | 8 | 2 | 0 |
| +30m | 74 | 5 | 18 | 0 |
| +60m | 16 | 1 | 80 | 0 |

missing 총 36: stale_price=34, not_subscribed=1, no_snapshot_within_tolerance=1. EOD/D+1/D+2/D+3는 각각 pending 97. complete 542건 중 post_target=532/pre_target_grace=10이며 원본 결과 의미는 바꾸지 않았다. 종료 요청 14:39:13.122361 UTC, export 완료 13.543, shutdown_complete 13.605, program_stop 13.606. summary의 422ms를 전체 종료시간으로 오인하지 않는다.

**Capacity 결론:** 20 부족, 50 saturation에 이어 100도 실제 100%에 도달했다. 100을 전체 후보 추적용으로 충분하다고 확정할 수 없다. 다음 사용자 실장 테스트는 **max_ws_symbols=150**이며 Active/protected/discovery peak, saturation, slot unavailable, REST/WS, CPU/UI 응답, shared cache 재사용과 RVOL 상태를 확인한다. 150이 충분하면 1주 운영 후보로 검토하고 포화되면 200 테스트를 검토한다. 200은 공식 hard limit이며 최적값 판정이 아니다.

### Validation / limitations

- 최종 전체 회귀 **207 통과 / 실패 suite 0** (기존 194 + 신규 13). `tools/kiwoom_provider/run_validation.py`로 기존 OCR 초기화·Registry·벽·Toss·halt·CLI·Current/Emerging·RVOL NULL·보호 추적·result sampling·REST suppression·graceful shutdown·Master·Review ZIP 회귀를 유지했다. 근거는 `data/kiwoom_provider_validation/tests_summary.json` 및 suite 로그다. 기존 assert 스크립트 단위와 메서드 수의 합이며 재실행은 중복 집계하지 않았다.
- 신규 cache/current-last 회귀 12개 및 기존 Dashboard suite의 원본/Master gauge 보존 테스트 1개를 추가했다. 실제 네트워크 대신 fake REST/WS와 실제 임시 SQLite를 사용해 두 Dashboard 실행의 cache 재사용·research DB 분리·persistent Master·계약 거부/무변경·CLI 호환·NULL coverage·UI 상태를 확인했다.
- 실제 session/cache/export/log/Review ZIP 및 운영 Master는 read-only로 감사했다. 임시 Master/ZIP만 테스트에 썼으며 기존 source를 수정하거나 legacy cache를 합치지 않았다.
- 기존 week_v2/50-slot/100-slot evidence 및 운영 Master 등 38개 파일 SHA256 전후 동일. `git diff --check`, 상대 링크 23개, 앱/collector/schema 버전 일치와 비밀정보 패턴 검사를 통과했다. `.env` 수정 및 git add/commit/push 없음.
- 새 live smoke 또는 150-slot 수집은 실행하지 않았다. shared cache의 실장중 누적 효과·CPU/UI 응답 및 1주 안정성은 다음 사용자 검증 대상이다. 기존 cache/raw 보관·크기 관리도 남아 있다.

## Market Radar v2.2 — Dashboard / Master Research DB

개발: 2026-10-02 KST, 미커밋. 앱 v2.2 / Collector 연구 v2.1 / test_version=`kiwoom_radar_v2_1` / research schema=4 유지 / **master_schema_version=1**.

### Added

- 기존 Stock Helper에서 여는 Market Radar Dashboard: 별도 Current Leaders/Emerging, Start/Stop, max WS/data root/grace 설정, System Health, Session History, Candidate History, 폴더 열기.
- Qt 밖 lifecycle controller와 research executor. 수집 시작마다 새 폴더, 종료/export 후 Master 자동 등록, 실패 시 수동 import 재시도. 정상 종료 중 중복 Stop을 무시한다.
- SQLite Master sessions/candidates/results/quality, 초기 schema migration·version guard·날짜/ticker/session/horizon index. 원본 경로/identity와 NULL/result sample 의미를 보존한다.
- 연구 DB schema 1–4의 읽기 전용 import, DB identity+최초 batch+ET 날짜 기반 중복 방지와 원자적 갱신. D+n 결과 재import 가능. 원본 증거를 수정하거나 raw snapshot 전체를 복제하지 않는다.
- Review ZIP allowlist, 선택 날짜 log, manifest/크기/SHA256/누락 파일 목록, 민감정보 패턴 검사와 atomic publication. DB/cache/raw/인증 파일 제외, 검사 실패 시 기존 ZIP 유지.
- `radar_ticker_selected(str)` 연결: 기존 분석 카드 선택 또는 clipboard ticker 준비. 자동 캡처 없이 기존 OCR 흐름 사용. QSettings에 일반 UI 설정만 저장.

### Changed

- runtime의 수집/종료 phase 표시만 추가했다. CLI, provider REST/WS/backfill, Current Leaders/Emerging 순위, protected tracking, schema 4와 cache는 변경하지 않았다.
- GUI도 기존 단일 collector lock과 graceful export 경로를 사용한다. 비동기 초기화에서 기존 worker=None 계약과 종료 동작을 보존했다.
- 기존 문서 역할과 사용자 미커밋 문서를 유지하며 README/기술 문서/HANDOFF를 현재 상태로 갱신했다. PROJECT_DIRECTION 및 RESEARCH_BASIS 파일은 저장소에 없어 수정하지 않았다.

### Validated

- 신규 offline 테스트 26개 통과: Master 생성/version/index·동시 초기화, import·중복·복사 identity·여러 날짜·NULL·D+n 갱신·rollback·원본 보존, ZIP allowlist/민감정보/manifest/hash/선택 파일 누락/실패 시 기존 ZIP 보존, history, controller 중복 stop/초기화 중 stop/lock 충돌/자동 import/실패 격리, UI thread responsiveness, CLI replay.
- 최종 전체 회귀 **194 통과 / 실패 suite 0** (기존 168 + 신규 26). `tools/kiwoom_provider/run_validation.py` 실행으로 기존 UI audit 19개, CLI/replay, Current/Emerging·protected tracking, OCR 초기화·Registry·벽·Toss·halt까지 통과했다. 메서드 수와 기존 assert 스크립트 단위를 합친 프로젝트 집계이며 재실행은 중복 집계하지 않았다. 근거: `data/kiwoom_provider_validation/tests_summary.json` 및 suite 로그. 최초 전체 회귀에서 시작 전 worker/close 계약 문제가 발견돼 수정 후 전체 재실행했다.
- `git diff --check`, 상대 링크 23개, 현재 앱/collector/schema 버전 구분, 변경 파일 범위 및 비밀정보 패턴 검사 통과. .env·provider·engine·storage·cache 변경 없음. GUI import와 offscreen 이벤트/표·선택·종료 검증을 수행했다.
- 실제 보관 세션 4개를 **임시 Master DB**에 read-only import하고 같은 입력 재import·ZIP 생성을 검증했다. 운영 master에 테스트 세션을 넣지 않았다. 원본 DB/cache/raw/export/log 등 **50개 파일 SHA256 전후 동일**, Master integrity_check=ok. 새 live 수집은 실행하지 않았다.

| 보관 원본 | ET 날짜 | Research schema | Max WS | Candidates | Snapshots | Missing Results | Max saturation |
|---|---|---:|---:|---:|---:|---:|---:|
| kiwoom_week_01 | 2026-09-28 | 2 | 20 | 20 | 676 | 15 | unknown |
| kiwoom_week_v2 | 2026-09-30 | 3 | 20 | 33 | 4,595 | 5 | 100% |
| kiwoom_smoke_v2_1 | 2026-09-30 | 4 | 20 | 19 | 378 | 0 | 100% |
| kiwoom_capacity_50_20261001 | 2026-10-01 | 4 | 50 | 50 | 5,244 | 10 | 100% |

이 수치는 원본 보관 데이터를 다시 읽은 결과이며 새 capacity/live smoke가 아니다. 과거 clean_shutdown은 명시적 lifecycle 부재로 NULL이다. 50-slot saturation 확인에 따라 다음 검증은 100-slot capacity test이며 최적값으로 단정하지 않는다.

### Known limitations

- GUI live Start→시장 수집→Stop 실장중 smoke와 EXE 배포는 아직 검증하지 않았다. mock/replay/offscreen 검증과 구분한다.
- legacy DB는 실행별 식별자가 없어 같은 DB의 ET 날짜로 요약한다. 실행시간은 관측 snapshot 범위이며 clean_shutdown/없는 counter는 unknown이다. 최초 batch가 없는 legacy 세션은 경로 기반 보조 identity를 쓴다.
- live Health는 5초 주기 DB 기준이며 export 묶음의 원자성은 없다. 종료 후 ZIP 전달을 권장한다. 과거 날짜 로그는 같은 날 여러 실행을 포함할 수 있다.
- 세션 목록은 200개/page, 종목 결과는 최근 1,000 horizon 행. 원본은 별도 보존한다. 원본 여러 DB를 한 폴더에 넣으면 DB별 import transaction이며 중간 오류 시 성공한 DB는 재import로 중복 없이 복구한다.
- 민감정보 검사는 알려진 필드 패턴과 경로 allowlist 기반이며 무표식 임의 문자열까지 식별하지는 못한다. live 수집 원본의 기존 공개 필드 정책은 유지한다.

## Market Radar v2.1

Git: 2026-10-01, `330492a` — Stabilize Kiwoom market radar v2.1. test_version=`kiwoom_radar_v2_1`, research schema=4. 실제 smoke 운영일은 2026-09-30 ET다.

### Added

- REST deterministic/transient/unclassified 실패 분류와 공개 오류 metadata, raw REST_ERROR 기록.
- ET 날짜·TR·ticker/exchange/date/query·cursor 단위 negative cache. 확정 실패 TTL 6시간, 날짜 변경/TTL 이후 재검증, 성공 시 회복. 영구 symbol blacklist 없음.
- `result_pre_target_grace_seconds=0.25`와 result의 sample_timestamp/sample_offset_seconds/sample_selection 감사 정보.
- quality summary v2의 REST 분류·상위 10개 차원별 집계, shutdown·result_sampling·slot_saturation_pct.

### Changed

- 새 연구 DB `stock_helper_market_kiwoom_v2_1.db` 사용. 이전 연구 DB는 ALTER하지 않고 schema/identity 불일치를 거부한다.
- cache OHLCV schema 1/assumed_et_start_v1 계약은 유지한다. 실제 smoke에는 별도 새 cache를 사용했다.
- API/transport/parse 오류와 historical backfill incomplete·deterministic_unavailable·취소를 분리했다. 정상 빈 initial chart는 no_data이며 API 오류가 아니다.
- FIRST_DETECTED부터 +60m까지 보호 추적, ETF/warrant 등 확정 유형 필터, 기존 Current Leaders/Emerging 공식과 RVOL NULL 정책을 유지했다.

### Fixed

- 실패 종류와 무관한 즉시 3회 retry 및 동일 요청 재순회로 인한 retry storm. 확인된 transient만 제한 재시도하며 미확인 실패는 60초 cooldown으로 둔다.
- stop 이후 scheduling/page/retry/WS REG를 막고 pending historical jobs를 취소한다. KeyboardInterrupt 수신·종료 phase를 기록하며 반복/중첩 Ctrl+C와 provider close 실패 표시를 보강했다.
- 목표 직전 수 ms의 snapshot과 목표+30초를 넘긴 다음 snapshot 사이에서 생기던 결과 경계 문제. 유효 post-target을 우선하고 해당 창 종료 후에만 pre-target grace를 사용한다. 기존 30초 tolerance와 실제 가격 기준을 유지하며 보간하지 않는다.

### Validated

기존 133개 + stability 35개 = **168 tests passed / 0 failed**. 기존 OCR 초기화·Registry·UI·Toss·halt 및 +60m 보호 회귀를 유지했다. 버전·schema·경로 기대값만 갱신했으며 실패 테스트를 삭제하지 않았다. 모의 active request + 대기 historical 300건 + 반복 stop은 78ms, 300건 취소, final export 및 DB integrity 통과였다.

#### FFR 조사와 REST 오류 증거

v2 REST 시도 오류 1,020건은 usa20100 474건 + usa06011 546건이다. retry=2로 각각 158/182개의 실패 cycle이 3번씩 호출됐다. 백필 오류의 ticker 분포는 FFR 174, GTNA 3, RZAI 3, GOW 2다. quote 오류는 ticker가 로그에 없어서 474건 모두를 FFR로 확정하지 않는다. worker_error 158건은 quote 실패가 scheduler의 광범위 exception handler까지 전파된 기록과 일치한다. 실패 분류 없이 즉시 3회 retry하고, quote는 다음 순회마다/백필은 60초 뒤 같은 요청을 재시도하는 구조가 오류를 증폭했다.

**v2 증거 한계:** 실패 응답은 raw에 저장되지 않았고 HTTP status/return_code/message/timeout/connection/parse 구분이 없었다. 과거 1,020건의 해당 차원은 unknown이며 모두를 1903으로 소급 분류하지 않는다. endpoint는 기존 allowlist상 usa20100 `/api/us/mrkcond`, usa06011 `/api/us/chart`다.

FFR은 첫 universe에서 CHG+VOL, 일간 합집합에 SPIKE로도 발견됐다. 거래소는 ND이며 FE가 실제 수신됐다. usa10099 catalog에 없어 type=unknown으로 유지했다. usa20100/usa06011 성공 raw는 없었다. FE 가격은 있지만 REST previous_close/completed 1m history가 없어 day_change_pct/volume/RVOL이 NULL이다. FE 필드로 기존 계산 정의를 대체하지 않았다.

정규장 중 허용 read-only probe를 7회 실행했다: ND/NY/NA usa10099 각 1회, FFR/NVDA usa20100·usa06011 각 1회. 공개 결과는 `data/kiwoom_v2_1_validation/public_failure_probe.json`이다.

- catalog: HTTP 200 / return_code 0, 각각 5,206 / 5,579 / 1,947행. 세 거래소 모두 FFR entry 없음.
- FFR 현재가·분봉: HTTP 200 / return_code **7**, message 내 **1903: 종목 정보가 없습니다**. 입력 ND/FFR, 분봉 0행.
- 같은 ND의 NVDA: 두 TR 모두 HTTP 200 / return_code 0, 분봉 100행.
- 확인된 현상은 **후보/FE에는 존재하지만 해당 REST catalog/조회에서는 symbol을 찾지 못하는 불일치**다. 공급자 내부 원인, 상장상태, 유형, 거래소 이동을 추측하지 않는다. ticker 하드코딩/제외/영구 blacklist는 없다.

분류 근거는 [Kiwoom 공식 오류 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/core/errors.py)이다. top-level 7만으로 transient를 판정하지 않는다. 공식 1901/1902/1903은 symbol lookup 오류, 1700/1701/1702는 rate limit, 8005/8031/8103은 인증 회복 대상이다. 그 외 확인되지 않은 service error는 unclassified로 남긴다.

#### 종료 지연 조사

기존 pending jobs는 FIFO 전체를 drain하는 구조가 아니라 IO thread가 페이지 단위로 처리하는 dict였다. 기존 stop_event도 있었다. 따라서 '기존 종료가 반드시 queue 전체를 처리했다'고 주장하지 않는다. scheduler 내부 loop/page/구독 선택의 stop 확인이 부족했고 stop 예외가 오류로 집계될 수 있었다. v2에는 Ctrl+C 요청 시각이 없어 전체 지연을 재구성할 수 없다. 마지막 WS disconnect 15:27:27.612 UTC→final export 15:27:40.307은 12.695초, program_stop까지는 12.709초지만 Ctrl+C부터의 시간은 아니다.

#### 실제 +30s 경계 재검산

원본 missing 4건의 가까운 pre-target offset은 LIQT -0.001633s, NVDA -0.003049s, PACB -0.002346s, FRGT -0.002783s다. 다음 snapshot은 각각 +30.001094/+30.020835/+30.006155/+30.003765s였다. 원본 결과는 수정하지 않았다. `counterfactual_sampling.json`의 읽기 전용 재검산에서는 기존 complete 선택이 전부 동일하고 이 4건만 grace로 복구 가능했다. 운영 증거 전체 SHA256도 작업 전후 동일하다.

#### 실제 Windows CTRL_C_EVENT smoke

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

근거: smoke_summary.json, ctrlc_driver.json, export/2026-09-30/quality_summary.json, logs/2026-09-30.log 및 `data/kiwoom_v2_1_validation/smoke_audit.json`. raw/log/export에 실제 인증값 문자열이 없고 기존 v2 evidence SHA256가 전부 동일함을 확인했다.

smoke 시작 당시 166개가 통과했고, 수집 중 최종 검토에서 **중첩 Ctrl+C handler 재진입 방지**와 **provider close 실패 표시**만 추가했다. 두 예외 경로는 이후 mock 테스트를 추가해 stability 35개/전체 168개로 확인했다. 정상 live 수집·REST·sampling 경로는 smoke 이후 변경하지 않았다. 실제 연속 두 번 Ctrl+C나 provider close 실패를 live에서 재현했다고 주장하지 않는다.

### Known limitations

- 실제 smoke의 pre_target_grace 사용은 0건이다. 경계 개선은 자동 테스트·원본 read-only 재검산으로 확인했으며 live grace 사용 검증으로 주장하지 않는다.
- 20 WS slots saturation은 남는다. 전체 후보 추적을 위한 50/100/200 capacity는 별도 검증 대상이다.
- RVOL missing-minute 의미는 MIXED_OR_UNCERTAIN이다. 무체결을 0-volume으로 채우지 않는다. 새 cache의 짧은 smoke에서 RVOL available=0이었고 개선을 주장하지 않는다.
- FFR 공급자 내부 원인 및 과거 v2 오류 전체의 HTTP/code/parse 원인은 복원 불가다. 6시간 negative TTL 중 데이터 회복을 즉시 감지하지 못할 수 있다.
- +60m는 기존 v2 장시간 evidence와 자동 회귀가 근거다. v2.1의 약 10분 smoke로 새 장시간 검증을 했다고 주장하지 않는다. EOD/D+n은 provisional/future pending이며 1주 연속 안정성을 보증하지 않는다.

## Market Radar v2

기록 기준: 종전 HANDOFF 갱신 2026-09-30 KST. 별도 v2 commit/릴리스 날짜는 확인되지 않으며 v2 변경은 v2.1 commit `330492a`에 함께 보존돼 있다. test_version=`kiwoom_radar_v2`, research schema=3.

### Added

- FIRST_DETECTED의 tracking_protected_until/candidate_tracking_state 및 재시작 복원. 기존 보호 구독 → 추가 보호 대상 → discovery → 기타 추적 순서.
- radar_discoveries, quality_counters, rvol_status/symbol_type_evidence 및 result missing_reason.
- Daily Quality Summary와 ET 날짜별 구조화 counter, backfill eligibility 설정.
- 공식 public fields/명시적 상품명에 근거한 보수적 ETF/ETN/warrant/unit-other 분류.

### Changed

- 새 연구 DB `stock_helper_market_kiwoom_v2.db`와 별도 v2 cache 경로. cache OHLCV schema 1은 유지하되 DDL 이전 identity 검증을 강화했다.
- eligibility는 과거 RVOL backfill만 제어하고 discovery/당일 분봉/결과 추적은 유지한다. day-volume·price 기본 threshold는 0이다.
- FIRST_DETECTED가 없는 discovery, 실제 active 구독, 보호 후보, 슬롯 미배정을 구분했다.

### Fixed

- 단순 source/rank 이탈로 보호 후보를 제거하던 구독 churn.
- 정상 초기 warm-up의 오류/traceback 취급을 INFO로 변경. 첫 성공 이후 오래된 universe는 WARNING으로 구분했다.

### Validated

#### 당시 133개 자동 검증과 production smoke

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

#### 이후 장시간 운영 baseline — 2026-09-30 ET

아래 수치는 v2.1 작업에서 v2 운영 자료를 다시 계산한 기록이다.

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

#### 누락 분 규약 검증

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

#### 종목 유형 실제 표본

공식 mkgb는 거래소명, upgb는 업종명, isEtf는 ETF 여부다. 실제 read-only usa10099 재조회 결과는 data/kiwoom_v2_validation/public_type_probe.json에 public fields/분류/수신시각만 저장했다. 인증값/토큰은 없다.

| 실제 표본 | 공식 필드/상품명 | v2 판정 |
|---|---|---|
| HYG, TQQQ, SOXL | isEtf=Y | ETF, 제외 |
| ARBEW, WALDW, BZFDW, SHFSW | isEtf=N, 한국명 워런트 + 영문명 C/WTS | warrant, 제외 |
| FVNNU (v1 catalog) | 한국명 유닛 + 영문명 UNITS | other, 제외 |
| AAPL, NVDA | isEtf=N, 거래소/업종만 제공 | unknown, 포함 |

워런트/유닛은 공식 상품명에 명시된 양 언어 descriptor의 일관성에 근거한다. 공식 전용 type enum이나 ticker suffix 판정이 아니다. N만으로 보통주라 표시하지 않는다. 명시적 COMMON STOCK/ORDINARY SHARES, ETN descriptor 처리도 구현했으나 이 규칙의 실제 해당 표본 검증은 남아 있다. type evidence는 source/rule/public fields다.

stock_type_policy=common_and_unknown은 common/unknown만 포함하고 확정된 ETF/ETN/warrant/other는 제외한다. all도 가능하다. 제외 종목의 raw/catalog/discovery는 보존하며 excluded_symbol_type을 기록한다.

추가 liquidity 관측: FVNNU 당일량 2,173~2,185, SOAR 608,226,198~613,819,995였다. 당일량이 큰 SOAR도 과거 이력은 불완전해 임의 양수 eligibility threshold의 근거로 쓰지 않았다. ticker별 원자료는 당시 baseline JSON에 있다.

### Known limitations

- 20슬롯 고갈 및 낮은 RVOL availability. 다른 거래일·종목·새 cache인 v1/v2 missing 비율을 통제된 개선 비교로 해석하지 않았다.
- v2의 QSI +1m missing은 목표 직전 2.6ms/허용 끝 직후 8.9ms 표본이 관측됐다. 장시간 v2의 +30s missing 4건과 REST 반복 오류는 v2.1에서 다뤘다.
- 20분 smoke 자체로 +60m를 검증하지 않았다. 후속 장시간 v2에서 첫 20개 +60m complete를 별도로 확인했다.
- 무체결 분·공식 timezone/봉 라벨/24시 표기/FID290 enum 미확정. exact VWAP와 공식 확정 EOD, 기업행동 보정/완료 cache 정정 refresh, raw 보관·압축·자동 삭제는 미구현이다.

## Kiwoom Provider v1

Git: 2026-09-29, `9698b12` — Add Kiwoom market radar provider and validation. 당시 HANDOFF도 2026-09-29 KST로 기록돼 있다. test_version=`kiwoom_radar_v1`, research schema=2. 실제 smoke 운영일은 2026-09-28 ET다.

### Added

- Kiwoom real read-only REST 9개 시장 TR allowlist, 단일 WebSocket FE/optional FT, ack 기반 REG/REMOVE, PING, reconnect/gap 및 로컬 collector lock.
- 네 candidate source 합집합, ticker dedup, 별도 REST/cache worker, 완료 1분봉 정규화, XNYS 달력.
- 연구 DB와 historical/raw/cache DB 분리, provider/source/cutoff/price age provenance 및 일봉 EOD/D+n provisional 결과.
- 기존 UI에서 독립 RadarPanel을 여는 연결은 앞 Market Radar 단계의 16줄 변경이었다. provider 작업은 기존 root 기능 코드를 다시 수정하지 않았다.

### Changed

- Kiwoom RVOL과 1m/5m return은 completed_1m 기준. generic/replay 기존 snapshot fallback과 분리했다.
- exact VWAP는 NULL, cntr_tm은 assumed_et_start_v1 연구 가정, 진행 봉은 재조회 없이 완료 승격하지 않는다.
- schema 1 legacy DB를 자동 ALTER하지 않고 별도 schema 2 DB를 사용했다. cache schema는 1이다.

### Validated

당시 commit의 HANDOFF 기준 **100 통과 / 0 실패**: 기존 assert 스크립트 12, Radar 31, audit 19, provider 33, probe 4, 실제 GPU OCR 초기화 1. 전체 영웅문 화면 OCR 정확도나 실계좌 동작을 검증한 뜻은 아니다.

2026-09-28 **19:17:47~19:20:17 UTC / 15:17~15:20 ET**, 약 150초 실서버 진단:

| 항목 | 결과 |
|---|---|
| OAuth / 4개 source / 9개 시장 TR | 성공 |
| universe / 제한 구독 | 78종목 / AAPL benchmark + 실제 거래량 후보 SOAR, 최대 2 |
| FE / FT / PING / REST 요청 | 1,250 / 0(OFF) / 14 / 227 |
| historical cache | AAPL 14일 complete, SOAR 1일 complete·나머지 incomplete/NULL |
| snapshot / 5m return non-null / RVOL non-null / VWAP non-null | 8 / 8 / 2 / 0 |
| candidate / event / 단기 complete result | 2 / 14 / 4 |
| CSV / DB·cache integrity / 정상 REMOVE·export·종료 | 4종 / 둘 다 ok / 성공 |
| worker_error / failed_batches | NULL / 0 |

AAPL은 실제 VALUE source에도 포함됐고 SOAR는 CHG+VOL이었다. 진단의 선택 override는 종목 수를 제한한 것으로 일반 production 슬롯 배정을 검증한 것은 아니다. AAPL cutoff 2026-09-28 15:19 ET의 RVOL은 **0.7414334602**, 15:19:19와 15:19:49 snapshot에서 동일했다. SOAR는 과거 coverage 부족으로 NULL이었다. 미래 EOD/D+n은 미검증이다.

#### 첫 운영 baseline — 2026-09-28 ET

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

### Known limitations

- 당시 공식 type enum 미확정으로 type_unknown을 포함해 ETF/워런트가 들어올 수 있었다. 슬롯 부족으로 추적 후보의 단기 결과가 missing이 될 수 있었다.
- 무료 feed를 전 거래소 SIP로 보장하지 않으며 봉 라벨/무체결 규약/FE14 notional 의미와 확정 공식 종가는 미검증이었다.
- 일반 200종목 부하, 실제 강제 단절/재연결, 1주 연속 수집 및 Radar EXE 배포는 이 150초 진단으로 검증하지 않았다.

## Market Radar v1

별도 구현·릴리스 날짜는 확인되지 않는다. `9698b12`에 포함된 첫 Radar 코드와 당시 HANDOFF에서 Kiwoom Provider v1보다 앞선 단계로 확인된다. generic/replay test_version=`market_radar_v1`, research schema=1.

### Added

- 독립 market_radar 모듈, connect/fetch/close provider 계약, MarketBatch 모델, 명시적 JSONL replay와 별도 replay DB.
- Current Leaders / Emerging TOP10, 지표 계산, FIRST_DETECTED·순위 진입·HOD/VWAP event, SQLite/CSV 및 +30s~+60m/EOD/D+1~D+3 사후 결과.
- Qt 밖 RadarWorker, 크기 1 메시지 큐, 별도 RadarPanel과 기존 UI의 lazy 연결.

### Validated

- 후속 Provider v1의 전체 검증에서 기존 Radar 31개와 audit 19개가 보존돼 통과한 기록이 있다. 별도의 v1 단독 전체 테스트 수나 실제 Kiwoom 연결 검증을 추정하지 않는다.

### Known limitations

- generic/replay 기반 계산과 Kiwoom의 완료 1분봉 정책은 구분해야 한다. replay 합성 fixture는 실시간 시장 수집 증거가 아니다.
- 자동 매수/매도·계좌 기능은 없다.

## 초기 Stock Helper

Git 최초 commit `71822e0`: 2026-08-23. 초기 구현 commit `dac8a33`: 같은 날. 별도 프로그램 semver/릴리스 날짜는 기록에서 확인되지 않는다.

### Added

- `dac8a33`: PySide6 종목 카드, live capture, OCR 파싱/검증, StockRegistry, Toss 현재가 및 진단 도구.
- `0b22ef2` (2026-08-25): live capture UI와 종목 추적 통합, clipboard ticker 관련 캡처·해결 테스트, pending captures/price refresh.
- `0910c54` (2026-08-25): 거래정지 모니터와 일간 벽 누적.
- `189b412` (2026-08-28): 관리자 권한 EXE와 캡처 갤러리.
- `e9cc76c` (2026-09-02): GPU OCR 지원과 추적 개선.

### Changed

- `8cadc11` (2026-08-25): 종목 카드 차트·추적 UI 정리.
- `3cc22c5` (2026-08-25): tests/tools 구조 정리.
- `e293037` (2026-08-28), `b90d354` (2026-08-29): 캡처 UI 최적화, 일간 범위의 접촉한 벽 추적·보존.
- `7033a17` (2026-09-29): 캡처·UI workflow 갱신.

### Fixed

- `766ee03` (2026-08-29): rebuild 중 패키징 앱 보호.
- `3116e7c` (2026-09-11): endgame OCR 및 CPU fallback 수정.

### Validated

위 초기 이력은 commit 제목·변경 파일에서 확인한 범위만 기록했다. 각 commit 당시 전체 테스트 수나 실장중 성공 여부는 임의로 부여하지 않았다.
