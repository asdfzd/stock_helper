# CODEX HANDOFF

갱신일: **2026-09-29 KST**. 프로젝트: Windows `stock_helper`.

**현재 상태: Kiwoom Market Radar Provider v1 구현 및 짧은 실서버 검증 완료. 제약을 명시한 연구 데이터 수집을 시작할 수 있다.** 완료된 1분 기준 RVOL/return, VWAP NULL, 과거 일봉 결과 provisional 정책이다. 1주 연속 운영·전략 유효성·전 거래소 통합 feed·공식 종가 확정은 검증하지 않았다.

기존 캡처/OCR/자동입력/Registry/매입가·반등가·벽/Toss/halt 코드는 이번 provider 작업에서 변경하지 않았다. 주문·계좌·잔고·환전 API가 없다. Commit/push/staging, `.bak` 생성은 하지 않았다. 기존 미커밋 변경을 보존했다.

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

## 3. Market Radar 전체 구조

```text
market_radar/
  config.py                    # CONFIG: Kiwoom 실행 설정
  models.py                    # Session/Bar/Observation/MarketBatch/DailyClose
  provider.py                  # connect/fetch/close Protocol, factory, replay
  calculations.py              # 일반 계산 및 명시적 completed_1m 분기
  engine.py                    # ranking/event/result, batch 트랜잭션
  storage.py                   # legacy schema v1 / 별도 Kiwoom schema v2, CSV
  runtime.py                   # snapshot worker, 로그, 오류·종료
  ui.py / __main__.py           # 두 TOP10 표, UI/headless/replay
  requirements-kiwoom.txt       # optional live 의존성
  providers/
    kiwoom.py                  # 후보·구독·scheduler·정규화 조합
    kiwoom_rest.py             # OAuth/read-only REST/중앙 limiter/retry
    kiwoom_ws.py               # LOGIN/REG/REMOVE/PING/FE/optional FT/reconnect
    kiwoom_cache.py            # raw/분봉/일봉/catalog/cache completeness
    kiwoom_calendar.py         # exchange_calendars:XNYS
    kiwoom_lock.py             # 로컬 단일 collector lock
    kiwoom_contract.json       # Probe 고정 revision의 9 TR 부분 계약
tests/
  market_radar_test.py         # 기존 Radar 31개
  market_radar_audit_test.py   # 기존 검수 19개, 새 기본 provider 기대값만 갱신
  kiwoom_provider_test.py      # 신규 offline 33개
tools/kiwoom_provider/
  run_validation.py           # 전체 테스트 + OCR 초기화, API 연결 없음
  smoke.py                    # 별도 DB, 실서버 최대 2종목, 시간 제한
tools/kiwoom_probe/            # 앞 단계 조사 근거, 이번 작업에서 수정 안 함
```

`create_live_provider(config)` → KiwoomProvider → RadarWorker → MarketBatch 검증 → 계산/재정렬 → DB/event/결과 → 크기 1 메시지 큐 → Qt 표. 제품은 probe 코드·output/deps를 import하지 않는다.

## 4. 실제 데이터 공급원과 근거

키움 real 미국주식 REST `https://api.kiwoom.com`, WS `wss://api.kiwoom.com:10000/api/us/websocket`.
고정 공식 revision은 `953e5dbff123f437ab4d11a78a95191a685eb51f`이며 Probe가 실제 검증한 계약을 사용한다.

- [키움 공식 API 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/_data/kiwoom_api_spec.json)
- [공식 제한](https://openapi.kiwoom.com/intro?dummyVal=0)
- [달력 라이브러리](https://github.com/gerrymanoim/exchange_calendars), [NYSE 휴장·조기폐장](https://www.nyse.com/trade/hours-calendars)

FE/FT 공식 명세는 무료 나스닥 토탈뷰 제공을 설명한다. 이를 미국 전체 거래소 SIP 통합 체결·거래량·VWAP으로 보장하지 않는다. 유료 시세를 신청해도 REST에서는 무료 시세로 제공한다는 공식 설명이 있다.

## 5. REST 목록과 인증

| TR | 용도 | endpoint |
|---|---|---|
| usa20100 | 현재가·전일종가·OHLC·누적량 | /api/us/mrkcond |
| usa10099 | ND/NY/NA 종목 목록 | /api/us/stkinfo |
| usa20910 | 당일 등락률 상위 | /api/us/rkinfo |
| usa20930 | 5분 가격급등 후보 | /api/us/stkinfo |
| usa20530 | 거래량 상위 | /api/us/rkinfo |
| usa20540 | 거래대금 상위 | /api/us/rkinfo |
| usa06011 | 1분 OHLCV / pagination | /api/us/chart |
| usa06012 | 일봉·조건부 후속 종가 | /api/us/chart |
| usa20590 | 일별주가 비교 raw | /api/us/mrkcond |

OAuth는 `/oauth2/token`, client_credentials다. 루트 `.env`의 KIWOOM_MODE=real, APP_KEY, APP_SECRET을 connect 시 로드한다. 키/secret/token은 로그나 DB에 저장하지 않는다. HTTP 헤더·OAuth 응답 전체·WS LOGIN frame은 raw 수집 대상에서 제외한다. 반환 필드는 계약 allowlist로 제한하고 실제 secret 문자열도 제거한다.

가격 필드 방향 부호는 abs로 정규화하고 등락률/변동 금액 부호는 유지한다. 잘못된/nonfinite 값은 쓰지 않는다. minute volume 부호 의미를 임의 추정하지 않으며 잘못된 OHLCV는 계산에서 제외한다.

## 6. 후보 universe와 타입 정책

등락률/5분급등/거래량/거래대금 네 source의 상위 페이지 합집합, ticker dedup이다. candidate_sources, source별 원래 rank와 received timestamp를 보존한다. 여러 페이지는 원자적 snapshot이 아니다. source 요청이 부분 실패하면 전체 universe를 교체하지 않는다. 기존 universe가 180초 또는 refresh×3보다 오래되면 batch 갱신을 보류한다.

종목리스트의 ND=NASDAQ/NY=NYSE/NA=AMEX와 순위 TR의 숫자 거래소 필터를 구분했다. 전체 12,000개 이상 개별 polling하지 않는다. 거래소가 상충하는 ticker는 raw 후보에 남기고 구독에서는 제외한다.

기본 stock_type_policy=common_and_unknown. 공식 mkgb/upgb/isEtf enum이 아직 불명확하여 v1은 type_unknown으로 보존한다. 이름/suffix로 보통주·ETF·워런트를 추정하지 않는다. 따라서 워런트·ETF가 포함될 수 있다. `all` 설정도 가능하지만 현재 unknown 분류에서는 동일하게 보존된다. 정확한 분류 확인 이후 필터를 강화해야 한다.

## 7. 200종목 슬롯과 WebSocket

운영 우선순위: 여러 source에 등장 → 등락률 source rank → 5분급등 rank → 거래량 rank → 거래대금 rank → ticker. 투자 점수가 아니다. max_ws_symbols 기본 200, 초과 설정은 거부한다. 후보를 우선 배정하고 남는 슬롯에 pending 추적 종목을 넣는다. 슬롯이 부족한 추적 종목은 단기 결과 missing 가능, 일봉 보완은 별도로 지속한다.

단일 관리 thread가 LOGIN 후 REG, REMOVE를 전송하고 성공 ack 후 active set을 변경한다. REG refresh=1은 공식 명세상 기존 등록 유지다. 종목 제거를 먼저 ack 받고 신규 등록하여 일시적 초과를 막는다. 서버 JSON PING을 echo한다. 계좌 F4/F5 이벤트는 사용하지 않는다.

FE raw: 10/12/13/14/15/16/17/18/20/22/290/51020 및 received_utc, exchange, connection_generation, real mode. 290의 의미는 해석하지 않는다. FT 10호가 raw는 optional/default OFF다. 호가는 순위에 사용하지 않는다.

## 8. 재연결·gap·중복 실행

단절 시 active set과 최신 가격을 비우고 gap을 기록한다. 지수 backoff(최대 60초), 인증 재확인, 재연결, desired set 재등록. 오래된 연결의 가격을 새 수신처럼 재사용하지 않는다. gap은 보수적으로 유지하며 완전한 tick 복원으로 표시하지 않는다. 연결 후 30초 안정화되면 실패 횟수를 초기화한다.

실제 강제 단절 부하시험은 하지 않았으며 mock으로 재등록/gap을 검증했다. websocket 라이브러리 내부 수신 thread가 추가될 수 있다. 로컬 파일 OS lock은 UI/headless 중복 collector를 차단하고 프로세스 종료 시 해제된다. 다른 PC·별도 프로그램은 통제하지 못한다.

## 9. Snapshot·시각·완료 봉 정책

기본 snapshot=30초. Observation/batch timestamp는 공통 **클라이언트 수집 시각 UTC**이며 거래소 생성 시각으로 주장하지 않는다. 최신 FE 가격의 received_utc 및 price_age_ms, raw 체결시각을 따로 저장한다. 거래소 timestamp 해상도가 미확정이므로 provider_latency_ms는 NULL이다. 수신 후 90초 초과 또는 개장 이전 FE는 현재 세션 Observation에서 제외한다.

정규장 달력은 exchange_calendars 4.13.2의 XNYS를 사용하고 America/New_York/DST/조기폐장을 반영한다. 이 출처를 batch metadata에 저장한다. Kiwoom 달력 API를 구현한 것이 아니며 돌발 휴장은 업데이트·운영 확인이 필요하다.

cntr_tm은 **가정 `assumed_et_start_v1`**로 해석한다. 현재 시각에서 2초를 뺀 최근 분 경계까지 종료된 봉만 계산한다. 캐시에 진행 봉으로 수신된 값은 시간이 지나도 완료로 승격하지 않고 다시 조회해야 한다. 24시 이상 시간과 extended 자료는 원문으로 남기고 regular 계산에서 제외한다. 이 정책은 공식 봉 라벨 확정이 아니라 Probe 관측에 근거한 연구 선택이다.

## 10. Current Leaders / Emerging

Current Leaders: `(live price / previous_close - 1)*100` 내림차순. Emerging: 최근 완료 봉 close 기반 return_5m 내림차순. 동률은 ticker 오름차순이다. provider source rank를 그대로 UI rank로 복사하지 않는다. source 급등률은 후보 발견에만 쓴다.

정규화된 유효 구독 후보 안에서 재정렬하고 UI는 TOP10씩 표시한다. 전체 시장 통합 순위가 아니다. NULL은 해당 순위에서 제외하고 in_universe=False 추적 전용 종목도 순위에서 제외한다. VWAP NULL, 음수 상승률 등을 이유로 종목을 제거하지 않는다.

## 11. 1분/5분 Return

Kiwoom은 cutoff의 완료 봉 close / 정확히 1·5분 이전 봉 close - 1로 계산한다. 실시간 price는 snapshot에 별도로 유지한다. 진행 봉과 30초 snapshot fallback은 return에 섞지 않는다. 필요한 끝점이 없으면 NULL이다.

기존 replay/일반 provider는 기존 관측시각 기준 return 및 정확한 snapshot fallback을 유지한다. completed_1m metadata가 있는 경우에만 새 분기를 사용해 legacy 의미를 바꾸지 않는다.

## 12. RVOL

**rvol_basis=completed_1m**. 오늘 정규장 시작~완료 분 cutoff 누적 volume / 최근 완료된 14거래일 각각 동일 경과분 누적 volume 평균. rvol_lookback_days는 config다. cutoff ET와 Observation 시각을 분리한다. 동일 분 안의 30초 snapshot은 같은 분모·분자를 사용하고 임의 반분량 추정은 없다.

history 중 하나라도 미완료, 해당 cutoff의 봉 누락, 0 분모, 과거 조기폐장 이후 시각이면 NULL이다. 무체결 분을 0으로 생성하지 않는다. Kiwoom 분기에는 기존 정확한 30초 DB snapshot fallback을 적용하지 않는다.

실서버 검증: AAPL의 14거래일 cache가 완료된 뒤 cutoff 2026-09-28 15:19 ET에서 **RVOL 0.7414334602**를 수신 원시값으로 계산했다. 15:19:19와 15:19:49 snapshot에서 동일하게 유지됐다. SOAR는 과거 coverage 부족으로 NULL이었다.

## 13. Historical cache와 일별 증분

`data/market_radar_cache/kiwoom_v1.db`. ticker/exchange/session_date/raw_timestamp 키로 OHLCV 원본과 수신 시각 저장. cache_days.complete는 달력의 모든 regular 분을 검증한 결과다. 이미 complete인 날은 재호출하지 않는다. 다음 거래일에는 새로 완료된 하루만 추가한다. 신규 후보는 background 페이지별 수집으로 warm-up한다.

REST/cache worker 한 개가 source refresh, 종목별 quote/일봉/당일 분봉과 과거 페이지를 번갈아 수행한다. 당일 처음에는 개장까지 backfill하고 이후는 이미 연속 확보한 마지막 구간과 겹쳐 갱신한다. 과거 불완전일은 15분 후 재시도하며 다른 날짜를 진행한다. 완료일의 이후 정정·분할 수정은 자동 재다운로드하지 않는 제한이 있다.

원본 보존 테이블: minute_bars, cache_days, daily_bars, raw_records, symbol_catalog, cache_metadata. raw REST/FE/optional FT를 저장하므로 연구 DB와 cache DB 둘 다 있어야 알고리즘을 재계산할 수 있다. queue 상한 초과는 raw_dropped와 로그/gap으로 남긴다.

## 14. VWAP / OR / HOD

Kiwoom exact_vwap_enabled=false. VWAP/거리/above_vwap은 NULL이다. FE14는 raw로만 보존하고 단위/세션 초기화/정밀도를 추정하지 않는다. OHLC typical-price 근사나 누적 notional의 임의 봉 배분이 없다. legacy/replay의 실제 bar.notional 기반 계산과 테스트는 그대로다.

OR1/OR5는 09:30 ET 시작의 연속된 1/5분 high/low. 구간 완료 전 또는 해당 봉 누락 시 NULL. HOD는 개장~bar cutoff 전체의 연속된 봉 high이며 quote의 당일 high를 regular HOD로 대체하지 않는다. coverage_complete는 이 시간 가정 아래의 **봉 연속성**이고 통합시장/tick 완전성은 아니다. HOD 거리=(live price/HOD-1)*100이다.

## 15. Event

FIRST_DETECTED, ENTER_LEADER_TOP10/5/3, ENTER_EMERGING_TOP10/5/3, HOD_BREAK, VWAP_LOSS/RECLAIM을 유지한다. 최초 두 TOP10 진입에서 candidate를 생성하고 같은 snapshot/event 및 유지 중 진입은 중복 기록하지 않는다. 순위 이탈 후 재진입은 새 event다. 상태는 DB에 남아 재시작에도 유지되고 거래일 변경 시 새 포착이다.

VWAP NULL↔값은 전이로 간주하지 않는다. Kiwoom의 VWAP는 계속 NULL이므로 VWAP event를 생성하지 않는다. 일부 종목의 stale/구독 제외로 순위에서 빠질 수 있으므로 event 분석 시 raw universe/active 상태를 함께 확인한다.

## 16. 결과 추적·MFE/MAE

최초 포착 가격·시각 기준 +30초/+1/3/5/10/30/60분은 목표 이후 첫 snapshot, 기본 허용 지연 30초 이내다. 세션 밖은 outside_session, 허용 구간에 없으면 missing, 미래는 pending/NULL이다.

EOD/D+1~D+3는 calendar 거래일에 맞춰 과거 usa06012 일봉을 후속 조회한다. **다음 ET 날짜부터 과거 일봉을 provisional / kiwoom:usa06012로 기록**한다. 현재 장중 일봉·15:59/16:00 분봉으로 확정 종가를 만들지 않는다. official close 계약이 확인되기 전 complete로 승격하지 않는다. provisional 결과는 다시 조회해 수정 가능하다. 이번 smoke 시점에 미래였던 D+n을 실제 검증했다고 주장하지 않는다.

MFE/MAE는 최초 가격·관측 snapshot·결과 가격을 사용하고 excursion_basis=sampled_prices다. tick-level 극값이 아니다. 포착 가격 포함으로 MFE≥0/MAE≤0이다. 정지 중 단기 결과를 소급 가격 추정으로 채우지 않는다.

## 17. 연구 DB와 schema

실제 기본 DB는 `data/stock_helper_market_kiwoom_v1.db`, **schema_version=2**, test_version=kiwoom_radar_v1. 기존 stock_helper_market.db를 조용히 ALTER하지 않는다. identity/schema 불일치 시 거부한다. replay는 `data/replay/stock_helper_market.db`와 legacy schema v1이다.

기존 9개 테이블 유지:

| 테이블 | 주요 데이터 / v2 추가 |
|---|---|
| radar_metadata | identity, schema_version |
| radar_batches | 시각/provider/mode/universe/raw_json/received_utc; observations 및 전체 source 후보 metadata |
| radar_snapshots | 기존 가격·변화율·volume/RVOL/return/VWAP/OR/HOD/rank/coverage + previous_close, candidate_sources, source_ranks, symbol_type, rvol_basis, rvol_cutoff_et, bar_cutoff_et, price_received_utc, provider_latency_ms, price_age_ms, ws_gap_detected, provider, exchange, bar_label_policy |
| radar_candidates | 최초 포착/가격/session_close/version + candidate_sources/provider/exchange |
| radar_events | timestamp/ticker/event/price/snapshot FK/version |
| radar_state | ticker별 마지막 snapshot/거래일 |
| market_sessions | session_date/open_utc/close_utc |
| market_closes | ticker/date/time/price + source/status |
| radar_results | horizon/목표·관측시각/price/return/status/오차/MFE/MAE/basis + result_source/close_status |

정확한 DDL은 storage.py의 SCHEMA 및 SCHEMA_V2다. batch별 snapshot/event/result는 원자적 transaction, WAL/foreign_keys/5초 busy timeout을 사용한다. cache는 별도 연결·별도 identity/schema v1이며 RLock으로 thread 접근을 직렬화한다. 두 DB 사이를 한 transaction으로 묶지는 않는다.

## 18. CSV / 파일 / 로그

```text
data/
  stock_helper_market_kiwoom_v1.db (+ 실행 중 wal/shm)
  market_radar_cache/kiwoom_v1.db (+ wal/shm)
  kiwoom_collector.lock
  logs/YYYY-MM-DD.log
  export/YYYY-MM-DD/{candidates,snapshots,events,results}.csv
  export/YYYY-MM-DD/metadata.json
  replay/stock_helper_market.db
  kiwoom_provider_validation/    # 전체 테스트·보호 파일 검사 증거
  kiwoom_provider_smoke/20260928T191747Z/  # 이번 실데이터 smoke, 생산 DB와 분리
```

CSV는 SQLite에서 300초 및 정상 종료 때 갱신, 신규 컬럼 포함, UTF-8 BOM, NULL 빈 칸이다. 후속 결과는 최초 포착일 results.csv를 갱신한다. 개별 파일 임시쓰기 후 replace이며 네 파일 전체 묶음 atomic은 아니다. SQLite가 원본, DB 클라우드 동기화/자동 업로드는 없고 export만 추후 복사한다.

로그 날짜는 ET, 내용 시각은 UTC. 기존 시작/종료/DB/export/계산 오류 외에 kiwoom_auth_success, rest_error, rate_limit, ws_connected/disconnected/reconnected, subscription_add/remove, backfill_start/complete/error, data_gap, stale_data를 기록한다. 키/토큰/응답 exception 본문을 출력하지 않는다.

## 19. Thread와 호출 제한

Radar snapshot worker + REST/cache scheduler 1개 + WS 관리 thread 1개. 라이브러리 내부 수신 thread 추가 가능. Qt는 250ms timer와 크기 1 큐로 최신 화면만 받는다. 네트워크/인증/backfill/DB/export는 Qt 밖이다. stop 요청은 Event만 설정하며 종료 작업/timeout은 background에서 기다린다.

공식 조회 일반 5회/초·한국시간 09~10시 3회/초보다 낮은 **3/2회**로 설정했다. 종목리스트는 **4회/60초**로 공식 5회/분보다 낮다. 모든 REST/OAuth가 중앙 lock/limiter를 공유한다. 공식 추가 50/20 유량을 일반 제한 우회에 사용하지 않는다.

HTTP connect/read timeout 5/10초, 최대 2회 재시도, 2·4초 backoff. 429와 일반 오류를 로그에 구분하고 실패 응답을 값으로 사용하지 않는다. 토큰 실패/WS 재연결에서 OAuth를 다시 확인한다. source/cache 규모에 따라 warm-up 및 bar 갱신이 느릴 수 있으며 stale/NULL을 감추지 않는다.

## 20. 설정과 실행 방법

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip install -r market_radar/requirements-kiwoom.txt
& .\.venv-paddle\Scripts\python.exe -m market_radar
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect
# 첫 주에는 작은 후보 구독 집합으로 시작 가능
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 20 --data-dir data/kiwoom_week_01
& .\.venv-paddle\Scripts\python.exe -m market_radar --replay tests/fixtures/market_radar_replay.jsonl --fast
```

`.env`의 KIWOOM_MODE=real / APP_KEY / APP_SECRET 사용. 기존 TOSS_CLIENT_ID/SECRET은 기존 기능 전용이며 Radar에서 쓰지 않는다. 현재 환경에는 Kiwoom optional 의존성을 설치했다. numpy/pandas/Paddle/OCR 기존 버전은 바꾸지 않고 websockets/exchange_calendars와 필요한 신규 보조 라이브러리만 추가했다.

CONFIG 기본: provider=kiwoom, test_version=kiwoom_radar_v1, snapshot=30, lookback=14, max_ws=200, refresh=60, pages=1, backfill_concurrency=1, extended=false, orderbook=false, exact_vwap=false. RadarConfig() 자체 기본은 legacy 테스트 호환용이며 CONFIG와 구별해야 한다. 환경변수 값은 출력하지 않는다. EXE 빌드/배포는 이번에 검증하지 않았다.

## 21. 테스트 결과와 실제 smoke

최종 전체 자동 테스트는 `tools/kiwoom_provider/run_validation.py`로 별도 subprocess에서 실행한다. 결과 파일은 `data/kiwoom_provider_validation/tests_summary.json`, 각 suite 로그와 OCR 초기화 로그다. 기존 assert 스크립트는 1개=검증 단위 1, unittest는 method 수로 계산하며 재실행은 중복 집계하지 않는다.

| 범위 | 최종 검증 단위 |
|---|---:|
| 기존 OCR/Registry/UI/Toss/halt/성능/캡처 등 assert 스크립트 12개 | 12 |
| market_radar_test.py | 31 |
| market_radar_audit_test.py | 19 |
| kiwoom_provider_test.py | 33 |
| 기존 probe_test.py | 4 |
| 실제 GPU OCR 초기화 | 1 |
| 합계 | **100 통과 / 0 실패** |

기존 미연결 기본값을 검사하던 audit 2개는 새 CONFIG/factory 기대값으로 갱신했다. 미연결 CLI 오류 검사는 명시적 --provider unconfigured로 유지한다. 실제 UI/main offscreen 시작·종료·Radar 열기, 느린 fetch 중 responsiveness 테스트를 보존했다. OCR 초기화는 실제 캐시 모델/GPU를 사용했고 실제 영웅문 캡처 정확도 전체를 검증한 뜻은 아니다.

신규 테스트는 가격 부호, merge/dedup/unknown, 200종목, ack/PING/optional FT/reconnect, 완료 봉/partial cache, 1m/5m return/OR/RVOL, 14일 증분 cache, stale FE, rate/retry/allowlist/redaction, v2 DB/CSV/legacy 거부, provisional EOD/future pending, queue overflow, 단일 lock을 검증한다.

실서버 smoke: **2026-09-28 19:17:47~19:20:17 UTC / 15:17~15:20 ET**, 약 150초. 자동 테스트 통과 후 실행했다. probe 파일의 과거 자료를 복사하지 않고 새 provider가 실제 API에서 새 cache를 만들었다.

| 실제 결과 | 값 |
|---|---|
| OAuth / 네 candidate source / 9 TR | 성공 |
| 후보 universe | 78종목 |
| smoke 구독 | AAPL benchmark + 실제 거래량 후보 SOAR, 최대 2 |
| FE / FT / PING | 1,250 / 0 (OFF) / 14 |
| REST 시장데이터 요청 | 227 |
| historical cache | AAPL 14일 complete; SOAR 1일 complete, 나머지는 incomplete/NULL |
| snapshot / 자체 5분 return | 8 / 8 |
| RVOL 값 / VWAP 값 | 2 / 0 |
| CSV | 4종 생성 |
| worker_error / failed_batches | NULL / 0 |
| DB 및 cache integrity_check | 둘 다 ok |
| 정상 REMOVE/연결 종료/export/worker stop | 성공 |

smoke 전용 선택 override는 벤치마크 AAPL과 거래량 후보 하나로 네트워크 범위를 제한한다. 실제 source 목록·시장값은 대체하지 않는다. 생산 provider의 일반 200슬롯 부하를 검증한 결과는 아니다. 실제 EOD/D+3 경과·실제 강제 재연결·1주 연속 수집은 미검증이다.

이번 AAPL은 실제 VALUE source에도 포함돼 in_universe=True였고 SOAR는 CHG+VOL이었다. AAPL을 강제로 후보 source에 추가하거나 제거하지 않았다. 후보 2개, event 14개, 단기 complete 결과 4개를 저장했고 미래 결과는 pending이었다.

## 22. 미구현·알려진 위험

- Kiwoom 공식 timezone/봉 라벨/24시 이상 표기·장구분 enum 미확정. 사용자 승인된 1분 연구 정책과 raw 증거를 기록했으며 공식 계약으로 단정하지 않는다.
- Exact VWAP, FE14 단위/정밀도/세션 초기화, 장중 과거 notional 복원 미구현. VWAP NULL은 의도된 상태다.
- 30초 precision RVOL은 구현하지 않고 명시적 completed_1m으로 대체했다.
- 전 거래소 통합 feed, 종목유형 enum, 확정 공식 EOD는 미검증. 일봉 결과는 provisional이며 미래는 pending이다.
- 저유동성·정지·IPO의 분봉 누락은 0으로 채우지 않아 RVOL/HOD가 장기간 NULL일 수 있다. 실제 SOAR 과거 이력에서 확인했다.
- 완료 과거 cache의 정정/분할 자동 refresh 없음. snapshot 결과의 기업행동 자동 보정도 없다.
- 200종목/첫 날 많은 신규 후보의 backfill은 비용이 크다. 요청 속도를 올려 제한을 넘기지 말고 작은 구독 집합으로 시작한다.
- 캐시/원본 자동 삭제·압축·보관기간 관리가 없다. 디스크 사용량을 점검한다. queue overflow는 기록하지만 삭제된 raw는 복구하지 않는다.
- 다른 PC의 중복 세션, 돌발 휴장, 주간 안정성, EXE 배포는 추가 검증 대상이다.

## 23. 일주일 실험 전 사용자 체크리스트

- [ ] `.env` real 인증값과 인터넷/IP/API 이용 권한 확인. 값 공유/로그 출력 금지.
- [ ] 다른 Kiwoom WS 프로그램을 끄고 collector 한 개만 실행.
- [ ] 새 DB 경로 선택, 기존 실험 identity와 다른 설정은 새 data-dir 사용.
- [ ] 시작은 20개 정도 구독으로 warm-up/속도를 확인한 뒤 범위를 조절.
- [ ] 일부 종목 raw timestamp/OHLCV와 키움 차트를 대조해 assumed_et_start 정책 검토.
- [ ] 휴일/조기폐장·돌발 폐장 일정 확인. 무료 feed/unknown 타입의 분석 범위 명시.
- [ ] backfill 중 NULL이 0으로 표시되지 않는지, 완료 뒤 RVOL과 cutoff를 확인.
- [ ] 매일 snapshot 간격, ws_gap, stale, raw_dropped, 오류, NULL 비율, 저장 용량·CSV 확인.
- [ ] EOD/D+n은 pending/provisional을 구분하고 확정 종가처럼 분석하지 않기.
- [ ] 절전/강제종료 방지, Ctrl+C 또는 중지 버튼으로 export/종료 로그 확인.
- [ ] 마지막 포착일 D+3 이후까지 수집하고 원본 연구 DB+cache를 보관. 클라우드는 export만 복사.

## 24. 이번 파일 변경 범위

신규: providers/ 아래 8개 파일, requirements-kiwoom.txt, tests/kiwoom_provider_test.py, tools/kiwoom_provider/{run_validation,smoke}.py, KIWOOM_PROVIDER_V1_REPORT.md.
수정: market_radar/{config,models,provider,calculations,engine,storage,runtime,ui,__main__}.py 및 README.md, 루트 README.md, CODEX_HANDOFF.md, tests/market_radar_audit_test.py.

기존 root Python 파일 전체, `.env`, KIWOOM_PROBE_REPORT.md, probe 소스는 작업 시작 시 SHA256과 일치함을 확인했다. root live_ui.py의 HEAD 대비 16줄은 앞 작업분이다. 이번 변경의 대부분은 원래 untracked인 market_radar/ 내부여서 기본 git diff --stat에 나타나지 않는다.

최종 source/문서/runtime 산출물 54개 실제 APP_KEY/APP_SECRET 비노출 검사, 보호 파일 해시, git diff --check 및 .env/DB ignore 검사를 통과했다. 근거: data/kiwoom_provider_validation/final_checks.json. 토큰은 메모리에서만 사용하며 sanitizer·필드 allowlist·mock 테스트로 저장 방지를 검증했다.

## 25. git diff --stat

```text
 .gitignore              |  1 +
 README.md               |  3 +++
 live_ui.py              | 16 ++++++++++++++++
 requirements-paddle.txt |  1 +
 requirements.txt        |  1 +
 5 files changed, 22 insertions(+)
```

## 26. git status --short

```text
 M .gitignore
 M README.md
 M live_ui.py
 M requirements-paddle.txt
 M requirements.txt
?? CODEX_HANDOFF.md
?? KIWOOM_PROBE_REPORT.md
?? KIWOOM_PROVIDER_V1_REPORT.md
?? market_radar/
?? tests/fixtures/
?? tests/kiwoom_provider_test.py
?? tests/market_radar_audit_test.py
?? tests/market_radar_test.py
?? tools/kiwoom_probe/
?? tools/kiwoom_provider/
```

기존 변경을 되돌리지 않았다. runtime DB/캐시/로그/.env는 Git 제외다. **Commit/push/staging하지 않았다.**
