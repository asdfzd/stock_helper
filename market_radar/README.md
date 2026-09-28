# Market Radar — Kiwoom Provider v1

기존 캡처→clipboard ticker→자동입력→OCR→StockRegistry, 매입가·반등가·벽, Toss 현재가 및 halt monitor와 독립된 연구 수집 모듈이다. 주문·계좌·자동매매·투자 추천 기능은 없다.

Kiwoom real 미국주식 REST/WebSocket provider를 연결했다. RVOL/return은 **완료된 1분봉 기준**, VWAP는 **NULL**, 과거 일봉으로 보완한 결과는 **provisional**이다. 미확정 데이터 규약을 확정한 뜻이 아니다. 근거는 `KIWOOM_PROBE_REPORT.md`, 최신 실행 결과는 `KIWOOM_PROVIDER_V1_REPORT.md`, 전체 인수인계는 `CODEX_HANDOFF.md`를 본다.

## 실행

프로젝트 루트 PowerShell:

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip install -r market_radar/requirements-kiwoom.txt
# 독립 Radar UI, 실제 수집 시작 버튼을 누르면 연결
& .\.venv-paddle\Scripts\python.exe -m market_radar
# 기존 프로그램의 Market Radar 버튼으로 같은 창 열기
& .\.venv-paddle\Scripts\python.exe live_ui.py
# Headless, Ctrl+C로 정상 종료 및 export
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect
# 소규모 별도 실험
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 20 --data-dir data/experiment_02
# 명시적 합성 fixture replay, 실시간 아님
& .\.venv-paddle\Scripts\python.exe -m market_radar --replay tests/fixtures/market_radar_replay.jsonl --fast
```

루트 `.env`의 `KIWOOM_MODE=real`, `APP_KEY`, `APP_SECRET`이 필요하다. 값은 코드·문서·로그에 넣지 않는다. `.env`, `data/`는 Git 제외이며 토큰은 메모리에만 있다. optional 의존성은 websockets 15.0.1 / exchange_calendars 4.13.2다. 기존 OCR requirements를 변경하지 않았다.

토큰별 세션 1개이므로 수집기 두 개를 동시에 켜지 않는다. 로컬 OS lock이 이 프로젝트의 중복 collector를 차단한다. 다른 PC·별도 프로그램은 이 lock 범위 밖이다. UI를 여는 것만으로 수집은 시작되지 않는다.

## 설정과 파일

실제 UI/CLI는 `config.py:CONFIG`의 kiwoom / kiwoom_radar_v1을 사용한다. `RadarConfig()` 자체는 기존 fixture 호환용 unconfigured / market_radar_v1이다. snapshot 30초, RVOL 14일, 후보 refresh 60초, source별 1페이지, WS 최대 200, REST 일반 3회/초·한국시간 09~10시 2회/초, backfill worker 1개다. extended=false, exact_vwap_enabled=false, orderbook capture=false다.

| 파일 | 역할 |
|---|---|
| providers/kiwoom_contract.json | Probe revision에서 추출한 9개 read-only TR 계약 |
| providers/kiwoom_rest.py | OAuth, allowlist, 중앙 limiter, timeout/retry, 비밀값 제거 |
| providers/kiwoom_ws.py | LOGIN/REG/REMOVE/PING, FE/optional FT, ack 기반 상태, reconnect |
| providers/kiwoom_cache.py | 일별 OHLCV·REST/FE/FT raw·catalog·일봉 SQLite cache |
| providers/kiwoom_calendar.py | exchange_calendars:XNYS, DST/휴일/조기폐장 |
| providers/kiwoom_lock.py | 로컬 단일 collector lock |
| providers/kiwoom.py | universe·구독·증분 backfill·Observation 변환 |
| models/calculations/engine/storage.py | 지표·event·결과·schema v2 |
| runtime/ui/__main__.py | 수집 worker·두 TOP10 표·UI/headless/replay |

공식 고정 revision은 `953e5dbff123f437ab4d11a78a95191a685eb51f`다. 제품은 tools/probe를 import하지 않는다. REST는 usa20100, usa10099, usa20910, usa20930, usa20530, usa20540, usa06011, usa06012, usa20590만 허용한다.

## Universe와 지표

- 등락률/5분급등/거래량/거래대금의 합집합을 ticker로 중복 제거한다. source rank와 수신 시각을 각각 남기며 페이지를 원자적 snapshot으로 가정하지 않는다. 일부 source 오류 시 기존 universe를 유지하고 stale을 표시한다.
- WS 슬롯은 source 중복 등장 수 → 등락률 → 5분급등 → 거래량 → 거래대금 → ticker 순으로 배정한다. 투자 점수가 아니다. 여유 슬롯은 pending 추적 종목에 사용한다. 부족한 추적 종목의 단기 결과는 missing일 수 있고 일봉 보완은 계속한다.
- 종목유형 enum이 미확정이므로 suffix로 추정하지 않는다. `stock_type_policy=common_and_unknown`이지만 v1에서는 `type_unknown`을 보존하므로 워런트/ETF도 포함될 수 있다.
- Current Leaders는 최신 Observation의 day_change_pct, Emerging은 자체 completed-bar return_5m로 재정렬한다. 동률 ticker, NULL은 해당 순위 제외. VWAP NULL은 제외 조건이 아니다. 범위는 유효 가격이 수신된 **구독 후보 부분집합**이다.
- 봉 시간은 연구 가정 `assumed_et_start_v1`: raw cntr_tm을 ET 분 시작으로 해석하고 원문을 보존한다. 24시 이상과 정규장 밖 봉은 raw만 저장한다. 장구분 FID290은 해석하지 않는다.
- 2초 유예 후 최신 완료 분 경계까지 계산한다. 캐시에 진행 봉으로 수신된 데이터는 시간 경과만으로 완료 승격하지 않고 다시 조회해야 한다.
- return_1m/5m는 cutoff의 완료 close와 정확히 1/5분 전 close를 비교한다. 최신 FE 가격을 분자에 섞거나 30초 snapshot fallback으로 채우지 않는다.
- RVOL은 오늘 개장~완료 분 cutoff 누적 volume / 과거 14 완료 거래일의 동일 경과분 평균이다. `rvol_basis=completed_1m`, bar/RVOL cutoff를 저장한다. 30초 중간 보간은 없다. cache 미완료·누락·과거 조기폐장 이후 cutoff는 NULL이다.
- OR1/OR5는 연속된 개장 1/5분 봉 high/low, HOD는 개장~cutoff 전체 연속 봉 high다. 누락 시 HOD/누적 volume/RVOL NULL, coverage_complete=0. 이 coverage는 봉 연속성이지 전체 tick 보증이 아니다.
- VWAP는 NULL이며 typical-price 근사 및 FE14 단위 추정은 없다. 기존 replay의 실제 notional VWAP 계산은 유지한다.

가격 수신 시각, REST 수신 시각, Observation 시각, bar/RVOL cutoff, 원본 timestamp를 구분한다. provider_latency_ms는 미확정이라 NULL, 수신 이후 경과시간은 price_age_ms다. 무료 나스닥 토탈뷰를 전 거래소 통합 SIP로 간주하지 않는다.

## 캐시·thread·재연결

RadarWorker는 계산/연구 DB/CSV, REST/cache thread 1개는 후보·현재세션·history 페이지를 번갈아 처리하고 WS thread 1개는 실시간 수신을 관리한다. 라이브러리 내부 수신 thread가 추가될 수 있다. Qt에서는 네트워크·heavy SQLite 작업을 하지 않는다.

완료된 과거 거래일은 재다운로드하지 않고 다음 날 완료된 하루를 증분 추가한다. 무체결/결측 분을 0으로 생성하지 않는다. raw queue 초과 시 로그·raw_dropped/ws_gap_detected를 남긴다.

REG refresh=1은 기존 등록 유지다. REMOVE/REG ack 뒤 active set을 변경한다. 단절 시 가격·active set을 비우고 최대 60초 backoff, 인증 재확인, 재연결·재등록한다. gap은 복원했다고 표시하지 않는다. stop은 Event로 요청하며 유한 네트워크 timeout과 최종 export를 기다릴 수 있다. UI에서 join하지 않는다.

REST/OAuth는 공통 limiter를 지나며 종목리스트는 60초당 최대 4회다. HTTP connect/read timeout=5/10초, 재시도 최대 2회, backoff 2·4초다. rate limit/API 오류는 로그에 남기고 데이터를 추정하지 않는다.

## 저장과 결과

```text
data/
  stock_helper_market_kiwoom_v1.db   # 연구 schema v2
  market_radar_cache/kiwoom_v1.db    # 별도 cache schema v1
  kiwoom_collector.lock
  logs/YYYY-MM-DD.log
  export/YYYY-MM-DD/{candidates,snapshots,events,results}.csv
  export/YYYY-MM-DD/metadata.json
  replay/stock_helper_market.db     # 기존 replay schema v1
```

연구 DB는 기존 9개 테이블에 source/rank, previous_close, provider/exchange, metric cutoff/basis, timestamp/age/gap metadata를 추가했다. source/status를 close/result에도 남긴다. `storage.py:SCHEMA_V2`가 정확한 DDL이다. 기존 DB의 identity/schema가 다르면 ALTER하지 않고 거부한다.

cache에는 minute_bars, cache_days, daily_bars, raw_records, symbol_catalog, cache_metadata가 있다. raw OHLCV/원문시각/REST 수신시각, FE 10/12/13/14/15/16/17/18/20/22/290/51020을 보존한다. 인증·헤더·토큰·계좌 raw는 없다. 일주일 뒤 재계산에는 연구 DB와 cache DB가 모두 필요하다.

Event는 FIRST_DETECTED, 두 순위 TOP10/5/3 진입, HOD_BREAK, legacy VWAP 전이다. 상태가 변할 때만 기록한다. Kiwoom VWAP는 NULL이므로 해당 전이는 발생하지 않는다.

포착 후 +30초/+1/3/5/10/30/60분은 실제 snapshot으로 추적하고 누락은 missing, 세션 밖 목표는 outside_session이다. 미래 EOD/D+1/2/3는 pending/NULL. 다음 ET 날짜부터 usa06012 과거 일봉으로 보완한 값은 **provisional / kiwoom:usa06012**이며 공식 확정 종가로 승격하지 않는다. 15:59/16:00 분봉으로 대체하지 않는다. MFE/MAE는 sampled_prices다.

CSV는 300초 및 정상 종료 때 갱신하고 NULL은 빈 칸, UTF-8 BOM이다. 파일별 임시파일 후 교체하며 네 파일 전체가 원자적 묶음은 아니다. SQLite가 원본이다. DB 클라우드 동기화는 구현하지 않았고 export만 추후 복사한다.

## 검증

```powershell
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/run_validation.py
# 별도 DB, 최대 2종목, 실제 read-only smoke
& .\.venv-paddle\Scripts\python.exe tools/kiwoom_provider/smoke.py --seconds 150
```

시작 직후 backfill 중 RVOL NULL은 정상이다. 일주일 운영 전 일부 종목의 raw 봉을 키움 차트와 대조하고 시간 가정·시세 권한·calendar를 확인한다. 매일 stale/gap/cache 오류, NULL 비율, DB 증가, CSV, pending/provisional 상태를 점검한다. 최신 실행 수치와 제한은 루트 provider 보고서 및 HANDOFF를 따른다.
