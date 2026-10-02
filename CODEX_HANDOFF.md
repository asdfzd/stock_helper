# CODEX HANDOFF

갱신: 2026-10-03 KST. 현재 상태만 기록한다. 영구 이력은 [CHANGELOG](CHANGELOG.md), 실행법은 [README](README.md), 기술 상세는 [Market Radar README](market_radar/README.md), 작업 규칙은 [AGENTS](AGENTS.md)를 따른다.

## 1. Current Version

- **Market Radar 앱 v2.2.1 / Collector 연구 v2.1 / test_version=kiwoom_radar_v2_1 / research schema=4 / master_schema_version=1**.
- HEAD=`330492a`. 이번 구현은 미커밋이며 git add/commit/push하지 않았다. 이전 v2.2 코드·문서·테스트의 미커밋 상태를 보존하고 이번 patch 범위만 추가했다.
- 요청된 PROJECT_DIRECTION.md/.docx 및 RESEARCH_BASIS.md는 현재 저장소에 없다. 프로젝트 방향을 새로 정의하지 않았다.
- 주문/계좌/자동매매/통합 추천 점수는 없다. Current Leaders/Emerging 공식, protected tracking, OCR/Registry/벽/Toss/halt 로직은 유지한다.

## 2. Current Architecture

기존 `live_ui.py` → lazy `show_radar` → `RadarPanel`을 Dashboard로 확장했다. Live Radar / Session History / Candidate History 세 탭과 System Health를 제공한다. 창을 열기만 해서는 provider를 연결하지 않는다. 종목 더블클릭 또는 Open in Stock Helper는 `radar_ticker_selected(str)` → 기존 분석 카드 선택, 없으면 clipboard ticker 준비로 연결된다. 자동 캡처는 실행하지 않는다.

| 모듈 | 역할 |
|---|---|
| market_radar/ui.py, dashboard_widgets.py | 표·설정·상태·폴더/ZIP 동작·QSettings |
| market_radar/controller.py | STARTING/RUNNING/STOPPING/STOPPED/ERROR, RadarWorker 실행/종료 감독 |
| market_radar/runtime.py | 기존 수집/계산/저장/export; 표시용 phase만 추가 |
| market_radar/research/master_db.py | sessions/candidates/results/quality, 자체 version/index/조회 |
| market_radar/research/importer.py | 원본 read-only import, 중복 upsert, live Health read snapshot |
| market_radar/research/review_export.py | allowlist ZIP, 민감정보 검사, manifest/hash |
| live_ui.py | 기존 메뉴와 느슨한 ticker 선택 연결 |

UI → controller non-daemon thread → 기존 RadarWorker → 기존 provider 구조다. 모든 REST/WS/backfill은 Qt 밖이다. SQLite 조회/import/CSV ZIP/hash는 별도 단일 research executor에서 실행하며 Qt timer가 완료 결과만 반영한다. Last gauge/연구 지표는 5초 주기 DB 기준이고 Current Connected/Active WS는 controller thread가 실제 WS ack/connection을 읽는다. Stop은 기존 graceful 종료 Event를 요청하고 중복 stop은 무시한다. 정상 final export 뒤 Master 자동 등록이며 등록 실패는 수집 실패로 바꾸지 않고 수동 import 재시도를 안내한다. 수집 중 창 닫기는 종료를 기다린다. 기존 CLI와 단일 collector lock은 공유한다.

Start마다 data root 아래 실행시각+UUID 폴더를 만든다. 기존 CONFIG 기본값(max WS 200, grace 0.25)을 따르며 UI에서 지정할 수 있다. dashboard_run.json은 실행 시작/종료/clean_shutdown/shutdown 시간만 저장한다. QSettings는 max WS/data root/geometry/column width만 저장한다.

### Shared cache / Current vs Last

- Dashboard canonical historical cache: **`APP_ROOT/data/market_radar_cache/kiwoom_v2.db`**, 현재 개발 경로는 `C:/Users/Choi/projects/stock_helper/data/market_radar_cache/kiwoom_v2.db`다. Data root/session 폴더가 바뀌어도 이 경로를 공유한다. Master는 `APP_ROOT/data/market_radar/master.db`다.
- provider의 기존 `config.data_dir/market_radar_cache/kiwoom_v2.db` 사용으로 매 Dashboard session마다 새 cache가 생기는 문제를 실제 코드/100-slot 파일에서 확인했다. `historical_cache_path`를 분리하고 controller에서 canonical 경로를 지정했다. 기본 CLI는 기존 data-dir 의미를 유지하며 `--cache-path <DB 파일>` 지정 시만 분리한다.
- canonical cache는 작업 시작 시 없었다. 다음 실제 connect 때 생성하고 compatible cache를 재사용한다. 기존 cache contract(provider/mode/schema/bar policy)는 DDL 이전 검사 그대로며 incompatible cache ALTER/자동 병합/복사는 없다. legacy 이관은 별도 명시적 작업으로 검토한다. 이번 작업은 운영 shared cache를 만들거나 기존 cache를 바꾸지 않았다.
- Historical Cache label은 짧은 표시+전체 경로 tooltip이다. cache에는 기존 raw_records도 포함돼 공유 DB에 누적되며 raw 분리/삭제는 미구현이다. Review ZIP에는 shared cache/raw/DB를 포함하지 않는다.
- **STOPPED: Current Active WS=0, Current Connected=No**. **Last Active WS/Protected/Discovery/Slot unavailable/Slot saturation %**는 마지막 batch 값 그대로다. historical/master 값을 0으로 덮지 않는다.
- RUNNING: Current Active/Connected는 현재 WS 상태, Current Protected/Discovery/Slot unavailable은 최근 DB gauge, Current Saturation은 현재 ack/max WS 기준이다. STOPPING은 실제 연결 상태를 표시하며 인위적 sleep은 없다. `Stopped cleanly · shutdown … ms`는 별도 label로 유지한다. ERROR에서 확인되지 않은 연결은 unknown이다.
- Session History 첫 묶음에 Max WS/Candidates/Snapshots/Max Saturation/Slot unavailable events/REST/RVOL/Clean Shutdown을 배치했다. 이벤트 수와 마지막 gauge는 구분한다. 기존 Master schema 1 필드와 summary_json으로 충분해 schema 변경은 없다.

### Master / source 구분

- Session research DB/export/log = 실행별 원본 증거; raw는 사용하는 cache DB에 보존된다. 기존 research schema 4 및 historical cache schema를 변경하지 않았다.
- Historical cache = minute bars/RVOL 근거. Master로 대체하지 않는다.
- `data/market_radar/master.db` = 재구축 가능한 요약·조회 계층. `PRAGMA user_version=1`, raw snapshot 전체 복제 없음. 기존 schema 1–4 import 지원, 미래 master version은 거부한다.
- DB identity+최초 batch 시각+ET 날짜의 SHA256 기반 run_id로 중복을 막고 재import로 D+n 결과를 갱신한다. 최초 batch 부재 시 dashboard run_id 또는 source 경로를 보조 identity로 쓴다. legacy 같은 DB의 여러 실행은 날짜별로 묶으며 임의로 분리하지 않는다.
- source DB/export/log 경로와 identity를 보존한다. `mode=ro`+`query_only`+read transaction으로 WAL의 committed 자료까지 읽고 원본 DDL/export/수정은 하지 않는다. Master update는 DB별 transaction이며 오류 시 rollback한다.
- 초기 값은 first_detected와 정확히 같은 snapshot; NULL·rvol_status·missing_reason·sample audit·provisional/pending을 보존한다. Legacy duration은 관측 구간, clean_shutdown/없는 운영 값은 unknown이다. UI lifecycle duration은 실행 전체 구간이며 여러 날짜 행에도 동일 구간으로 표시된다.
- History 200행/page, ticker history 최근 1,000 horizon 행. 추천 점수는 없다.

### Review ZIP

선택 세션 또는 현재 세션의 quality_summary.json/candidates.csv/snapshots.csv/events.csv/results.csv/metadata.json과 해당 ET 로그 중 존재하는 파일만 포함한다. DB/cache/raw/인증 파일은 제외한다. review_manifest.json은 날짜/run_id/version/schema/max WS/파일명/크기/SHA256/누락 파일을 기록한다. 출력은 세션 `reviews/market_radar_review_<date>_<run_id>.zip`이며 검사 후 atomic replace다. 인증·계좌 등 필드 패턴이 있으면 전체 생성을 거부하고 민감값을 출력하지 않는다. 임의 무표식 비밀값 식별은 보장하지 않는다. 실행 중 export 파일 묶음은 원자적이지 않으므로 종료 후 검수 ZIP을 권장한다.

## 3. Current Data/Provider Contracts

- production CONFIG는 provider=kiwoom이다. RadarConfig() 자체의 unconfigured/market_radar_v1 기본값은 legacy fixture용이며 live fallback이 아니다.
- 공식 고정 revision `953e5dbff123f437ab4d11a78a95191a685eb51f`의 시장조회 9 TR: usa20100, usa10099, usa20910, usa20930, usa20530, usa20540, usa06011, usa06012, usa20590. 계약 파일은 [kiwoom_contract.json](market_radar/providers/kiwoom_contract.json)이다.
- REST/WS 실서버, ND/NY/NA 거래소, XNYS/ET/DST/조기폐장 달력 사용. 여러 source page는 원자적 시장 snapshot이 아니다. 일부 source 실패 시 기존 universe 유지, 첫 성공 후 max(180초, refresh×3) 초과 시 갱신 보류.
- snapshot 기본 30초, timestamp는 클라이언트 수집 UTC. FE 수신 시각/price age는 별도이며 provider_latency_ms는 NULL이다. 90초 넘게 오래됐거나 개장 전인 FE는 현재 세션에서 제외한다.
- auth는 연결 시 로컬 설정에서 읽고 값·인증 헤더·인증 응답·WS LOGIN은 기록하지 않는다. REST/raw 필드 allowlist와 sanitizer를 사용한다.
- 일반 REST 3회/초, KST 09~10시 2회/초, 목록 4회/60초. HTTP connect/read timeout 5/10초. 한 로컬 collector만 허용하며 다른 PC의 세션까지 통제하지 않는다.

CLI 기본 연구 DB는 `data/stock_helper_market_kiwoom_v2_1.db`(schema 4), cache는 `data/market_radar_cache/kiwoom_v2.db`(OHLCV schema 1/assumed_et_start_v1)다. CLI는 --cache-path를 생략하면 --data-dir 아래 cache를 만드는 호환 동작을 유지한다. Dashboard는 session research DB와 canonical shared cache를 분리한다. 기존 연구 DB는 ALTER하지 않고 DDL 이전 identity/schema 불일치를 거부한다. cache도 기존 contract 검증이 필요하다. 연구 DB와 cache의 transaction은 서로 별개이며 재계산에는 둘 다 필요하다. batch 저장은 transaction/WAL, 파일별 export는 atomic replace이고 파일 묶음 전체는 원자적이지 않다.

실패는 transient/deterministic/unclassified로 구분한다. HTTP 429/5xx·timeout/connection 및 확인된 rate-limit/auth-expiry만 최대 2회 retry. 확정 실패는 ET 날짜+TR+전체 query+cursor별 6시간 negative cache, 날짜/TTL 변경 후 재검증한다. unknown/parse와 transient exhausted는 60초 cooldown이다. 영구 blacklist/특정 ticker 예외는 없다.

Ctrl+C는 KeyboardInterrupt를 기록하고 scheduling/page/retry/새 WS REG를 중단한다. 진행 요청 반환 후 pending historical jobs 취소 → WS REMOVE/disconnect → raw/cache/REST/provider close → 연구 DB checkpoint/CSV/quality export → DB close/program_stop 순이다. WS thread는 같은 Event를 받아 더 먼저 종료할 수 있다. provider close를 export 앞에 두어 종료 counter를 포함한다. 반복·중첩 Ctrl+C는 같은 종료 상태를 사용하며 강제 thread/process 종료는 없다.

## 4. Current Ranking/RVOL Policies

Current Leaders는 day_change_pct 내림차순, Emerging Candidates는 완료 1분봉 기반 return_5m 내림차순, 동률 ticker 오름차순이다. 유효 구독 후보 부분집합의 TOP10이며 시장 전체 통합 순위가 아니다. source rank는 발견용이며 점수로 바꾸지 않는다. follow-up-only/제외 유형/해당 지표 NULL은 해당 순위에서 제외한다. VWAP NULL은 순위 제외 조건이 아니다.

Kiwoom 1m/5m return은 cutoff close와 정확히 1/5분 전 close를 비교한다. RVOL은 오늘 정규장 개장~완료 cutoff 누적량 / 직전 14거래일의 동일 경과분 누적량 평균이다. cutoff는 2초 유예 후 완료 봉, 진행 봉은 재조회 없이는 완료 승격하지 않는다. 조기폐장 이후 cutoff·불완전 과거 coverage·0 분모는 NULL이다. snapshot fallback/보간/가짜 zero-volume 분을 쓰지 않는다.

무체결 분 의미는 **MIXED_OR_UNCERTAIN**. rvol_status는 available/warming/incomplete_history/not_eligible/zero_denominator/unknown이다. backfill eligibility는 과거 이력만 제어하고 후보·당일 분봉·결과 추적은 유지한다. 기본 min day volume/min price=0이다. OR1/OR5/HOD는 필요한 구간의 연속 봉이 있어야 한다. VWAP는 의도적으로 NULL이다. FE14 단위/세션 초기화와 cntr_tm의 ET 분 시작 해석은 공식 확정이 아니며 24시 이상·장외 자료는 raw로만 보존한다.

## 5. Current Tracking Policy

- 두 TOP10 최초 진입의 유효 실제 가격으로 FIRST_DETECTED를 만든다. 첫 포착 + max(최대 intraday horizon 60분, hold 설정)을 당일 close로 제한한 뒤 tolerance 30초까지 보호하며 DB에서 복원한다.
- 기존 보호 구독 → 추가 보호 대상 → 현재 discovery → 기타 추적 순서. 보호 기간 중 source/rank 이탈만으로 REMOVE하지 않는다. 만료 후 release 가능, EOD/D+n은 별도 일봉 follow-up이다.
- 슬롯 미배정 discovery는 raw/radar_discoveries에 남기며 유효 live 가격 없는 candidate/기준가를 생성하지 않는다.
- +30s/+1m/+3m/+5m/+10m/+30m/+60m는 target~target+30초의 가장 빠른 snapshot 우선. 창 종료 뒤 post-target이 없을 때만 target-0.25초 이상~target 미만 중 가장 가까운 실제 snapshot을 사용한다. grace는 설정 가능하며 0이면 비활성이다.
- sample_timestamp/sample_offset_seconds/sample_selection을 result DB/CSV에 보존한다. observed_utc/timing_error_seconds와 일치하며 price/result 의미를 바꾸지 않는다. missing/outside_session/pending과 실제 missing_reason을 구분한다.
- EOD/D+1~D+3는 calendar 거래일의 과거 일봉을 provisional로 기록한다. 미래는 pending이다. MFE/MAE는 최초 가격과 관측 snapshot/result 가격 기반 sampled_prices이며 tick 극값이 아니다.

## 6. Current Symbol Filtering

stock_type_policy=common_and_unknown: common/unknown 포함, 확인된 ETF/ETN/warrant/unit-other 제외. isEtf=Y 및 공식 한국어·영어 상품명 descriptor를 사용하며 ticker suffix로 추정하지 않는다. isEtf=N만으로 common을 확정하지 않는다. type evidence/source/rule/public fields와 제외 discovery를 보존한다. 실제 표본과 검증 범위는 CHANGELOG, 세부 규칙은 Market Radar README에 있다.

## 7. Current Quality Summary

ET 날짜별 `export/YYYY-MM-DD/quality_summary.json`, quality_summary_version=2. 300초 export와 정상 종료 때 갱신한다.

- snapshot/candidate/RVOL/return/result는 SQL, 운영 수치는 명시적 thread-safe counter이며 로그 문자열 parsing에 의존하지 않는다.
- backfill complete/incomplete/skipped/deterministic_unavailable/transient_error/unclassified_error와 legacy error를 구분한다. 기본 historical 시도 수이며 skipped는 eligibility로 생략한 scheduler 기회 수다. incomplete·종료 취소·정상 empty initial chart는 runtime API 오류가 아니다.
- api_error_count에 실제 HTTP/API/parse/transport 실패를 남긴다. transport/parse는 그 부분집합이며 이미 집계한 REST 실패를 worker 오류로 다시 세지 않는다.
- rest_errors: 실제 시도별 분류 및 TR/ticker/endpoint/HTTP/코드별 상위 10개, suppressed_count. raw REST_ERROR에는 sanitized 공개 오류를 남긴다.
- tracking gauge는 마지막 batch의 active/protected/discovery/unavailable/slot_saturation_pct다. subscription counter는 ack 종목 수다. 종료 후 active gauge가 마지막 수집 값을 유지하는 것은 정상이다.
- shutdown 요청·취소·시간과 result_sampling의 post_target/pre_target_grace 횟수를 제공한다. last_shutdown_elapsed_ms는 CSV·첫 quality export까지이며 최종 summary 재출력/DB close까지의 전체 시간은 shutdown_complete/program_stop 로그로 확인한다.
- source는 종목별 일간 합집합, type은 마지막 관측값이다. snapshot이 없으면 비율은 NULL이다. summary는 원본 DB/raw를 대체하지 않으며 강제 종료 전 미flush counter는 유실 가능하다.

## 8. Current Tests / Validation

- 이번 patch의 최종 전체 회귀 **207 통과 / 실패 suite 0** (기존 194 + 신규 13). `tools/kiwoom_provider/run_validation.py`로 OCR 초기화·Registry·벽·Toss·halt·CLI/replay·순위·보호 추적·샘플링·REST suppression·Master·Review ZIP까지 실행했다. 메서드/기존 assert 스크립트 단위의 합이며 중복 실행을 집계하지 않는다. 근거는 `data/kiwoom_provider_validation/tests_summary.json` 및 suite 로그다.
- 추가 13개: 두 Dashboard 실행의 동일 cache/별도 research DB/persistent Master, 기존 compatible cache 내용 재사용, 모든 contract 차원 불일치 거부와 파일 무변경, CLI 기본/명시 cache option, shared incomplete coverage/NULL 유지, 현재/마지막 gauge/연결/오류/종료 message, capacity 열, source/Master 무변경. fake REST/WS+실제 임시 SQLite/offscreen 테스트이며 live 테스트가 아니다.
- 실제 100-slot 원본 DB/CSV/summary/log/cache raw/lifecycle와 운영 Master를 read-only 대조했다. summary/CSV 집계가 일치하며 임시 Master 재import 주요 12개 필드 및 결과 1,067행도 일치했다. 임시 Review ZIP 생성도 통과했다. 운영 Master를 재import하거나 원본을 수정하지 않았다.
- 이번 patch 작업은 새 live smoke/150-slot 테스트를 하지 않았다. 과거 검증/실장 이력은 CHANGELOG에 보존했다.
- 기존 week_v2/50-slot/100-slot evidence와 운영 Master 등 **38개 파일 SHA256 전후 동일**. `.env` 변경 없음. `git diff --check`, 상대 링크 23개, 버전 일치, 비밀정보 패턴 검사 통과. 이번 patch의 계산/순위/연구 schema/cache contract 변경은 없다.

### Latest capacity evidence: 100 slots

원본 `data/20261002_223750_e6b4cd6b`, 운영일 2026-10-02 ET. UTC 13:37:50.300549~14:39:13.611360, lifecycle duration **3,683.310811초**. 후보 **97**, snapshot **8,644**, unique snapshot ticker **163**. Active 최대/마지막 **100/100**, Protected **97/82**, Discovery **80/77**, slot unavailable 최대/마지막 **15/15**, 미배정 이벤트 **86**, saturation 최대/마지막 **100%/100%**.

REST total **33** (deterministic 33, transient/unclassified 0): unsupported 21 + no_data 12, suppression 26. API error는 21로 no_data와 구분한다. stale 913, WS gap/raw dropped 0, RVOL available **0%**, warming 1,509/incomplete_history 7,135. DB/worker/export error 모두 0. clean_shutdown=true, summary/UI shutdown **422ms**는 CSV/첫 quality export까지이며 로그 전체 종료는 약 **484ms**다. horizon별 결과 및 missing reason 상세는 CHANGELOG에 영구 보존했다.

**20 부족 → 50 saturation → 100도 saturation 확인**. 100을 전체 후보 추적에 충분하다고 확정할 수 없다. 다음은 **150-slot capacity test**다. 200은 공식 hard limit이며 자동 default/최적값으로 채택하지 않는다. 기존 CONFIG max WS=200은 CLI 호환을 위해 이번에 변경하지 않았고 UI는 기존 저장값을 유지한다. 다음 테스트는 150을 명시한다.

## 9. Current Known Limitations

- shared cache 적용 후 실제 재사용·coverage 누적·RVOL 상태 변화·CPU/UI 응답·REST/WS 안정성은 아직 실장 검증하지 않았다. RVOL NULL/MIXED_OR_UNCERTAIN 정책은 그대로이며 shared cache만으로 available 개선을 주장하지 않는다.
- 150의 포화/미배정/보호 peak를 확인한 뒤에만 1주 운영 후보로 검토한다. 150도 포화되면 200 테스트를 검토한다. 1주 안정성/EXE 배포/다른 PC 중복 수집/실제 단절 부하는 미검증이다.
- legacy cache 자동 이관/merge 및 raw 보관·압축·삭제는 없다. 기존 cache에는 raw도 있어 장기 disk 사용량을 확인해야 한다. source가 여러 실행을 담으면 날짜별 legacy 로그/구간 제약도 유지한다.
- 무료 feed 범위·무체결 분·timezone/봉 라벨·24시 표기·FE14/FID290 규약, unknown 유형, 확정 EOD/기업행동 보정은 기존 제약 그대로다.

## 10. Next Work / Run

1. **사용자 다음 실장 capacity test: max_ws_symbols=150**. Dashboard에서 Max WS 150 지정, canonical cache tooltip 확인 후 새 session을 시작한다. 이번 Codex 작업은 이를 자동 수행하지 않았다.
2. Active/protected/discovery peak, saturation, slot unavailable, REST/WS stability, CPU/UI 응답, shared cache 재사용과 RVOL 상태 변화를 확인한다. 150이 충분하면 1주 운영 후보로 검토하고 포화되면 200 테스트를 검토한다.
3. Stop 후 Current 0/No와 Last gauge, clean shutdown 시간을 확인하고 History에서 Review ZIP을 만든다. D+3 follow-up 후 재import하며 원본 연구 DB+shared cache를 보관한다.

~~~powershell
& .\.venv-paddle\Scripts\python.exe live_ui.py
& .\.venv-paddle\Scripts\python.exe -m market_radar
# 사용자 다음 실장 테스트: CLI에서도 shared cache를 명시하려면
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 150 --result-pre-target-grace-seconds 0.25 --data-dir data/kiwoom_capacity_150_next --cache-path data/market_radar_cache/kiwoom_v2.db
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/run_validation.py
~~~

## 11. Current Git Status

사용자 미커밋 문서를 기반으로 작업했다. AGENTS.md는 사용자 파일 그대로이며 git add/commit/push하지 않았다. data/원본 evidence/인증값을 Git에 추가하지 않는다. 아래 diff/stat은 작업 전 사용자 변경도 포함한다.

### git diff --stat / git status --short

기존 사용자 미커밋 변경 포함, 미추적 파일은 diff --stat에 미포함.

~~~text
 CODEX_HANDOFF.md                 | 566 +++++++++------------------------------
 README.md                        |  92 ++++++-
 live_ui.py                       |  18 ++
 market_radar/README.md           |  87 +++++-
 market_radar/__init__.py         |   2 +
 market_radar/__main__.py         |   5 +-
 market_radar/config.py           |   9 +
 market_radar/providers/kiwoom.py |  10 +-
 market_radar/runtime.py          |   5 +
 market_radar/ui.py               | 476 ++++++++++++++++++++++++++------
 tests/market_radar_test.py       |   4 +-
 11 files changed, 734 insertions(+), 540 deletions(-)
~~~

~~~text
 M CODEX_HANDOFF.md
 M README.md
 M live_ui.py
 M market_radar/README.md
 M market_radar/__init__.py
 M market_radar/__main__.py
 M market_radar/config.py
 M market_radar/providers/kiwoom.py
 M market_radar/runtime.py
 M market_radar/ui.py
 M tests/market_radar_test.py
?? AGENTS.md
?? CHANGELOG.md
?? market_radar/controller.py
?? market_radar/dashboard_widgets.py
?? market_radar/research/
?? tests/market_radar_dashboard_test.py
?? tests/market_radar_patch_test.py
~~~
