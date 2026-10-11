# Market Radar v2.6.0 — Opening Hour Research v1 / Paper v1 + Rotation v2

현재 **Market Radar v2.6.0 / Opening Hour Research v1 / opening hour schema=1 / Paper Trading v1 / paper schema=1 / Rotation v2 / rotation extension schema=1 / Validator 1 / Collector v2.1 / test_version=kiwoom_radar_v2_1 / research schema=4 / master schema=1**의 기술 문서다. 프로그램 소개·설치는 [루트 README](../README.md), 현재 작업 상태는 [CODEX_HANDOFF](../CODEX_HANDOFF.md), 버전별 검증 이력은 [CHANGELOG](../CHANGELOG.md)를 따른다. Kiwoom 수집기에는 주문/계좌 기능이 없고, 별도 NH read-only probe만 계좌목록·잔고·매수가능수량을 조회한다. 실제 주문/자동매매 기능은 없다.

NH 어댑터 v1.1은 `execution/nhplug_client.py`와 `execution/nhplug_accounts.py`, 명시적 CLI는 `tools/nhplug_probe/probe.py`와 `tools/nhplug_probe/accounts.py`, optional 의존성은 `requirements-nhplug.txt`다. Kiwoom discovery/research와 분리된 execution-price/account 조회용이며 Collector/순위/schema/Validator에 연결하지 않는다. 공식 nhplug 0.4.1의 OAuth·REST와 종목마스터 파서를 사용한다. REST `Output_0.trdprc`, 기존 실시간 `RC`의 `body.trdprc_1z17`만 가격으로 사용하며 GIC는 공식 마스터의 `sSymbol/sNationCode/sGIC`로 확인한다. SDK HTTP 전송에도 인증·현재가·계좌목록·잔고·매수가능수량 allowlist와 redirect 차단을 적용한다. 원본 계좌는 메모리에서 마스킹하며 토큰 파일 캐시와 SDK 진단 출력은 차단한다. RC/WSS10012는 보류 상태로 이번 계좌 작업에서 변경·재시험하지 않았다. 상세 범위·실행법은 [루트 README](../README.md)를 따른다.

계좌 조회는 [공식 gbstock 명세](https://www.nhplug.com/openapi-docs/gbstock/openapi.json)와 공식 SDK의 [balance 예제](https://github.com/PLUG-OpenAPI/nhplug-sdk/blob/main/snippets/gbstock/balance/balance.py), [buyable_amount 예제](https://github.com/PLUG-OpenAPI/nhplug-sdk/blob/main/snippets/gbstock/buyable_amount/buyable_amount.py)를 따른다. `/gbstock/inquiry/v1/balance`에 `act_no`, `qut_iqr_dit_cd="9"`, `fc_sec_trd_nat_cd="200"`, `cur_cd="USD"`, `xns_dit_cd="1"`을 보내며 연속 조회 완료 후 `Output_1.cns_bse_bnc_qty`가 양수인 보유 종목 수를 집계한다. 명시적인 계좌 거절은 NOT_USABLE, 통신 오류·불명확한 업무 응답은 FAIL, 정상적인 빈 잔고는 PASS/0으로 구분한다. 누락된 출력 블록만으로 잔고 0을 만들지 않는다.

매수가능수량은 `/gbstock/inquiry/v1/buyableAmount`의 `pcs_dit="2"`만 허용한다. 공식 입력 `act_no`, `fc_sec_trd_nat_cd="200"`, `iem_cd="AAPL"`, `wtm_cur_knd_cd="2"`(원화증거금), `oss_orr_knd_cd="1"`, `ahi_nmn_pr_tp_cd="00"`, `fc_orr_uit_pr`(NH REST 현재가)를 사용한다. `Output_0`의 `byn_pbl_qty`/`orr_pbl_qty`/`max_pbl_qty`는 서로 다른 값으로 유지하며 누락을 0으로 대체하지 않는다. 3개 운영 계좌 모두 실제 잔고·수량 조회 PASS, 수량 모두 0, 주문 호출 0건을 확인했다. 여러 사용 가능 계좌 중 기본값을 저장하지 않으며, 마스킹 끝자리가 같을 때 출력하는 `live-N`은 해당 응답 순서에만 유효하다.

최상위 방향 원본은 [PROJECT_DIRECTION.docx](../PROJECT_DIRECTION.docx)이며 [MD mirror](../PROJECT_DIRECTION.md)는 그 의미를 따른다. 장기 흐름은 Market Radar → Session Validator → Research / Master DB → Strategy Analysis → Stock Helper → Live Decision Support다. 현재의 후보 탐색·수집 품질 검증과 향후 충분한 재검증 후의 Candidate Rank를 구분하고, 기존 가격구조와 독립 비교한 뒤 결합 효과를 검증한다. 이 문서의 현재 기능을 최종 전략 검증 완료로 해석하지 않는다.

## Paper Trading v1 — experimental shadow test

v2.4.1 경계 조건 보완: `attach()`는 WS hook만 준비하고 `provider.connect()`가 기존 collector lock을 얻은 뒤 `start()`가 Paper DB 복원을 시작한다. 중지 요청은 UI에서 대기하지 않고 최초 요청시각을 고정한다. worker는 Paper 큐/DB 마무리를 기다린 뒤 provider를 닫아 잠금을 해제한다. 따라서 실패한 중복 실행이 기존 OPEN을 INTERRUPTED로 바꾸지 않으며, 느린 종료가 수동 중지를 SESSION_END로 바꾸지 않는다. 동일 FE 수신시각의 서로 다른 가격은 도착 순서대로 관리하고 동일 시각·동일 가격의 중복만 생략한다. exact-time horizon mark는 그 시각의 첫 유효 가격이다.

`paper/engine.py`의 상태 기계와 `paper/policy.py`의 `ExperimentalBaselinePolicyV1`을 별도로 둔다. strategy는 `paper_v1_baseline`, **actual orders = disabled / not implemented**, **strategy profitability = not established**다. NH 모듈/주문 endpoint를 import하거나 호출하지 않는다.

`controller`의 명시적 Enable에서만 `paper/runtime.py`를 시작한다. Capacity Test는 UI·controller·worker에서 차단하고 consumer/DB/WS hook을 생성하지 않는다. Regular/Manual에서는 기존 Kiwoom WS factory의 raw sink를 감싸 원래 sink를 그대로 호출한 뒤 관심 ticker의 FE 가격·수신시각과 GAP만 bounded queue에 전달한다. 별도 thread가 모든 전달된 tick을 순서대로 처리하므로 UI의 maxsize=1 snapshot 큐에 의존하지 않는다. 기존 provider/slot/cache/scheduling/연구 계산을 수정하지 않는다. Paper 오류는 수집 batch 실패로 승격하지 않는다.

Radar의 실제 ranked rows와 `Observation.bars`/`bar_cutoff_utc`를 사용한다. cutoff에 끝나는 실제 완료 봉이 없으면 `completed_bar_at=NULL`이며 시간 경과를 새 봉으로 대체하지 않는다. 후보 최초 시각은 `radar_candidates.first_detected_utc`를 읽기만 한다. 기존 완료 봉 라벨 `assumed_et_start_v1`의 제약은 그대로다. Candidate priority는 양쪽 TOP10 → Emerging → Current; Emerging rank → Current rank → ticker다. `allocate_slots()`는 사용하지 않는다.

WATCHING 시작 이후 끝난 새 완료 1분봉, 양수 completed return_1m/return_5m, 현재가 > watch_reference_price, TOP10·fresh price·정상 WS를 모두 확인한다. RVOL/OR/HOD/NULL 사유는 evidence일 뿐 gate가 아니다. `entry_signal_at`은 consumer가 조건을 평가한 현재 UTC이며 snapshot/queue 입력 시각으로 소급하지 않는다. 이후 `price_received_utc > entry_signal_at`인 첫 FE 가격으로 OPEN한다. 가격 시각은 로컬 수신시각이며 거래소 체결시각 proxy를 생성하지 않는다.

OPEN마다 관측 return·최대/최저 가격과 시각을 갱신한다. MFE=(최대 관측가격/진입가−1)×100(0 이상), MAE=(최저 관측가격/진입가−1)×100(0 이하), 진입가격도 범위에 포함한다. TP=entry×1.15, SL=entry×0.90을 최초 넘은 유효 tick 가격으로 종료한다. 정상 공식 close는 마지막 유효 관측가격/실제 수신시각과 SESSION_END를 저장한다. 수동 중지·restart는 INTERRUPTED, disconnect/stale/역행/큐 overflow/처리 지연은 DATA_INTEGRITY_INVALID다. gap 시작·끝과 원인을 남기고 불명확한 exit 가격/return은 NULL이다. 기존 freshness 제한(`max_data_delay_seconds`, 기본 90초)을 사용하며 consumer 2초 초과 처리 지연·5초 초과 실행 공백·2초 초과 UTC/monotonic 불일치는 해당 Paper run을 중지한다. 이미 발생한 crossing을 봉/후속 가격으로 채우지 않는다.

`data/paper_trading/paper_trades.db`의 `PRAGMA user_version=1`을 사용하며 `paper_sessions`, `paper_trades`, `paper_trade_events`, `paper_trade_marks`, `paper_horizons`를 둔다. 운영 연구 DB에 ALTER하지 않는다. 거래 단계마다 트랜잭션으로 저장하며 불변 entry evidence·priority·rank·sources·momentum·RVOL/OR/HOD·품질 및 evidence에서만 만든 deterministic reason을 보존한다. OPEN 확정 때만 하루 4회/ticker 차단에 포함하며 재시작에도 DB에서 복원한다. 재시작 OPEN과 미완료 WATCHING/신호는 INTERRUPTED로 남기고 새 관측부터 시작한다. 기본 Enable 상태는 저장하지 않는다.

세션 종료 후 entry 기준 +30s/+1m/+3m/+5m/+10m/+30m을 별도 marks에서 **정확한 timestamp 일치**로만 분석한다. Radar `HORIZONS`의 기간을 재사용하되 FIRST_DETECTED 기준 결과를 entry 기준 결과로 바꾸어 쓰지 않는다. exact 관측이 없으면 NULL/`no_exact_observation`; 보간·목표 전후 대체 없음. 이 방식은 missing이 많을 수 있다. 독립 v1 모듈은 구독 보호를 연장하지 않는다. v2.5.0 Dashboard의 별도 post-exit observer는 아래처럼 공식 close까지 보호를 요청한다.

일일 summary의 entry 수/TP/SL/SESSION_END/invalid를 분리하고 TP/SL win rate 분모에는 TP+SL만 쓴다. return·MFE·MAE 평균/중앙값은 정상 종료 거래만 사용한다. 복리는 해당 정상 거래마다 전액 재투자 가정이며 거래비용/호가/slippage/invalid 기간의 수익은 포함하지 않는다. export는 paper 전용 폴더의 CSV/JSON이다. 합성 replay는 재현성 확인이며 실수익/실제 체결 검증이 아니다. live shadow는 후속으로 별도 실행한다.

## Paper Rotation v2 / post-exit sidecar

`paper/`의 v1 engine/policy/storage/runtime/UI/replay와 기존 fixture/test는 수정하지 않는다. Dashboard는 `paper_rotation_v2/runtime.py`의 단일 직렬 consumer를 연결한다. 같은 frame 평가시각·FE 수신 이벤트를 먼저 v1, 다음 v2로 전달하며 DB·position·WATCHING·일일 카운터·ticker 금지는 독립이다. Rotation은 기본 OFF, 선택하면 v1도 함께 실행한다. Paper 활성화 시 post-exit review는 v1에도 적용한다. Capacity에서는 두 전략과 sidecar를 시작하지 않는다. 잠금 확보·stop fencing·bounded queue·gap 처리는 기존 v1과 같은 규칙이다. sidecar 장애는 v1 판단을 변경하지 않고 별도 ERROR를 표시한다.

v2의 일반 진입/TP/SL은 v1 코드를 재사용한다. OPEN 중 새 후보는 양쪽 TOP10, 양쪽 rank가 현재보다 우수, 같은 완료 cutoff의 1m/5m return이 모두 더 큼, v1 WATCHING 이후 새 완료 봉·양수 momentum·watch 가격 돌파 확인, fresh/WS 정상, 당일 미거래를 모두 만족해야 한다. 현재는 rank >10 또는 1m/5m return ≤0 중 하나가 필요하다. 현재 row/수치/rank가 NULL이면 비교 근거 부족으로 전환하지 않는다. 후보 선택 동률 순서는 v1과 동일하다. 이름은 **material evidence dominance**이며 상승확률·점수는 계산하지 않는다. RVOL/OR/HOD와 순위 공식은 변경하지 않는다.

`switch_signal_at`은 실제 평가시각이다. 그보다 늦은 현재 ticker 첫 유효 FE로 `SUPERIOR_CANDIDATE_SWITCH`/`rotation_exit_price`를 기록한다. 이어 **exit 시각보다 엄격히 늦은** 새 ticker 첫 유효 FE로 `rotation_entry_price`를 기록한다. exit 전 받은 새 ticker 가격과 exit와 같은 수신시각의 가격은 재사용하지 않는다. pending 전환 중 gap/invalid/stale/restart/종료는 취소·무효 기록하며 가상 체결가를 보완하지 않는다. 정상 entry와 rotation entry를 합해 ET 하루 4회, 동시 OPEN 1개, 동일 ticker 재진입 금지다.

Baseline DB `data/paper_trading/paper_trades.db`와 paper schema 1은 유지한다. 별도 `data/paper_trading/paper_rotation_v2/review.db`는 paper schema 1의 거래 테이블을 독립 인스턴스로 재사용하고 **rotation extension schema 1** (`rotation_metadata.version`)의 details/switches/review_trades/post_exit/marks를 추가한다. sidecar에 baseline DB를 지정하면 쓰기 전에 거부한다. baseline 거래의 복사본과 v2 거래는 strategy+trade ID로 분리하고 baseline 원본은 수정하지 않는다.

모든 실제 OPEN 이력 ticker는 기존 provider `set_result_tracking`의 보호 입력을 통해 official close까지 유지한다. 슬롯 상한/배정 알고리즘/순위는 그대로이며 v1·v2가 같은 확장된 시세 스트림을 받는다. 구독·연결·수집 중단으로 가격이 없으면 생성하지 않는다. 종료 후 첫 엄격히 늦은 유효 tick부터 close 미만까지 원본 가격/수신시각/관측 순서를 저장한다. heartbeat가 조기폐장 포함 official close에서 추적을 끝낸다. 조기 중단·restart·invalid·stale·WS gap은 `INCOMPLETE`와 사유, 세션 종료 trade의 이후 창은 `NO_POST_EXIT_WINDOW`다. `OBSERVED_TO_CLOSE`도 관측 범위의 정상 완료이며 모든 시장 체결의 수집을 보증하지 않는다.

Post-exit 필드:

- `original_entry_price`, `original_tp_price`, `original_sl_price`
- `post_exit_max_price`, `post_exit_min_price`, `post_exit_max_return_vs_entry_pct`, `post_exit_min_return_vs_entry_pct`
- `original_tp_hit_after_exit`, `original_tp_hit_after_exit_at`, `original_sl_hit_after_exit`, `original_sl_hit_after_exit_at`
- TP: `max_price_after_tp`, `extra_runup_after_tp_pct`, `lowest_price_after_tp`, `max_drawdown_after_tp_pct`
- SL: `recovered_entry_after_sl`, `recovered_entry_at`, `original_tp_hit_after_sl`, `original_tp_hit_after_sl_at`, `max_price_after_sl`

최대/최저는 exit tick을 제외한 실제 후속 가격이다. 원래 진입 대비 return=(후속가격/entry−1)×100. TP extra runup=max(0, 후속최고/TP exit−1)×100, drawdown은 TP exit를 첫 peak로 포함한 **시간 순서상 peak 대비 이후 가격 하락률의 최솟값(0 이하)**이다. 관측이 없으면 가격/return은 NULL이다. hit=False는 관측된 hit가 없다는 뜻이며 gap 구간의 미도달을 보증하지 않는다. `STOP_THEN_ORIGINAL_TP`는 SL 후 원래 TP 관측, `TP_THEN_CONTINUED_RUN`은 exit 가격 초과 관측, `TP_THEN_REVERSAL`은 위 drawdown ≤−10%인 경우에만 생성한다. 모두 review 전용이며 engine/policy는 이 집계·tag·후속 가격 저장소를 읽지 않는다.

별도 export `<sidecar parent>/export/<ET-date>/`:

- `paper_trades.csv`: 두 전략의 entry/exit reason·불변 evidence·MFE/MAE·switch 연결.
- `paper_post_exit.csv` / `paper_post_exit_marks.csv`: 집계·tag·추적 품질 / 실제 후속 tick 경로.
- `paper_rotation_switches.csv`: signal/exit/entry 시각·가격·stage·취소 사유·dominance 근거.
- `paper_rotation_comparison.csv`: 동일 run/ticker/signal/entry 시각·가격이 모두 같은 시작만 baseline에 연결. `baseline_v1_return`은 해당 v1 종료 수익률, `rotation_v2_return`은 같은 root의 완료된 v2 leg만 전액 재투자 가정으로 연결한 수익률이다. invalid/open/pending leg가 있으면 NULL. 서로 다른 종료시점일 수 있음을 명시한다. 새 leg 종료시각에 원래 ticker의 exact 관측이 있을 때만 switch 이후 원래 종목 유지 대비 차이를 계산하며, 없으면 NULL/사유다. 사후 MFE/MAE와 switch 후 원래 TP 도달도 연결한다.
- `paper_daily_summary.json`: 전략별 독립 일일 통계·schema·actual_order_calls=0. 비용/spread/slippage/체결 가능성은 모델링하지 않는다. 기존 v1 CSV/summary export는 별도로 그대로 생성한다.

합성 paired replay: `python -m market_radar.paper_rotation_v2.replay tests/fixtures/paper_rotation_v2.jsonl`. 실제 shadow 운용·수익성 검증과 구분한다.

## Opening Hour Research v1

`opening_hour/source.py`는 종료된 Radar DB·shared minute cache·선택적 Paper DB의 **임시 DB/WAL 복사본**을 query-only로 읽는다. 원본 SQLite를 열어 SHM을 만들지 않는다. 복사/조회 전후 원본 크기·mtime 변경은 실패 처리하여 활성 수집과 섞인 snapshot을 쓰지 않는다. `engine.py`는 관측값만 계산하고 `storage.py`는 독립 DB/export를 저장한다. `service.py`는 명시적인 사후 실행 진입점이다. provider 생성·REST·WS·주문 호출이나 Paper 판단으로의 출력 경로가 없다.

Dashboard의 옵션은 기본 OFF, Regular Session에서만 선택할 수 있다. controller가 collector/Paper 종료 → 기존 export/Master/Validator를 마친 다음 같은 background thread에서 분석한다. live frame/tick 처리에는 research hook이 없다. 분석 오류는 자체 INCOMPLETE/예외 종류로 표시하며 수집 성공·protocol·validation을 바꾸지 않는다. 조기 중지 시 보유 자료의 결측을 그대로 기록한다. Capacity/Manual의 자동 실행은 차단하고 과거 자료는 별도 CLI에서 명시적으로 분석한다.

### 입력과 시간

- 설치된 XNYS `ExchangeCalendar`의 official open/close를 source `market_sessions`와 대조한다. ET open 포함~open+60분 제외, **09:30~10:29 시작 완료 분봉 60개**, DST·조기폐장은 calendar를 따른다. bar policy는 기존 `assumed_et_start_v1`만 허용하고 혼합/미지원 정책은 거부한다.
- 첫 60분 batch의 candidate universe와 `radar_discoveries.first_seen_utc`, 필요시 `radar_candidates`를 합친 unique ticker다. TOP10/구독/Paper 여부를 필터로 쓰지 않는다. 이후 발견 후보·10:30 시작 봉은 제외한다. 전체 미국 종목으로 확장하거나 새 수집을 요청하지 않는다.
- detection 시각과 정확히 일치하는 실제 snapshot에서 가격·rank·day change·완료 return_1m/5m·RVOL/status·출처/rank를 읽는다. 없는 값은 NULL이다. discovery/candidate 테이블의 나중 source union은 detection 출처로 소급하지 않고 `candidate_sources_session_evidence`로 분리한다. batch에서 확인한 source history만 checkpoint 당시 출처로 사용한다.
- source cache의 `open_pric/high_pric/low_pric/cur_prc/trde_qty`를 보존한다. 실제 수신 시각 전에 끝난 완료 봉만 사용하므로 cached partial을 시간 경과로 완성시키지 않는다. cache가 없으면 Radar의 `completed_1m`/cutoff가 확인된 Bar HLCV만 복구한다. 기존 Bar에 시가가 없어 O는 NULL이며 이전 종가를 시가로 대체하지 않는다.
- 늦게 발견한 ticker도 cache의 개장 이후 경로를 보존하되 `bar_start < candidate_first_detected_at`이면 `pre_detection=true`다. 이 값은 사후 연구 전용이다. 전일종가는 존재하는 snapshot 값과 그 관측 시각을 기록한다.
- local pinned [Kiwoom contract](providers/kiwoom_contract.json)의 `usa06011.response_fields`에는 공식 minute notional이 없다. 모든 timeframe의 `minute_notional=NULL`; 가격×거래량이나 일 거래대금 배분을 만들지 않는다.

### 가격·구조·거래량 계산

1m 테이블에는 기대 분마다 한 행을 두어 실제 분봉과 결측 placeholder를 구분한다. 실제 OHLCV, UTC/ET 시작·UTC 종료, 전일종가/개장/이전 완료 분 종가 대비 return, range/body/wick/CLV, 방향, 누적 거래량, pre_detection 및 quality를 저장한다. missing은 OHLCV NULL·`missing_bar=true`이며 실제 0 volume과 다르다. HLC는 존재하지만 시가/거래량이 없으면 개별 계산 가능 값을 남기고 `missing_open_or_volume`로 표시한다.

- return=(가격/기준가격−1)×100, range_abs=H−L, range_pct=(H−L)/O×100, body_abs=|C−O|. upper wick=H−max(O,C), lower wick=min(O,C)−L, body/wick %는 range 대비 백분율이다. CLV=(C−L)/(H−L), 분모 0이면 비율은 NULL이다. 실제 0인 절대 길이는 0으로 보존한다. direction은 C와 O의 `UP/DOWN/FLAT` 비교다.
- 3m은 open 기준 0/3/…/57분, 5m은 0/5/…/55분으로 정렬한 각각 20/12개다. 구성 1m OHLCV가 모두 유효해야 O=첫O/H=max/L=min/C=마지막C/V=sum이다. 하나라도 누락/불완전하면 집계 OHLCV 모두 NULL, `aggregate_complete=false`다. 별도 API를 쓰지 않는다.
- HH/HL/LH/LL은 **인접한 동일 timeframe**의 H/L 엄격 비교다. range expansion/contraction은 인접 absolute range 증가/감소이며 임계값이 없다. 결측은 비교·UP/DOWN 연속 길이를 끊는다. 전체 count는 관측 가능한 쌍/봉 기준임을 `structure_basis`에 명시하고 1m/3m/5m 구조를 보존한다.
- volume과 high/low/close/range의 절대 변화·%·증가 여부를 저장한다. `volume_change_vs_prev_3m/5m`, `range_change_vs_prev_3m/5m`은 백분율이며 이전 값이 0이면 NULL이다. 누적 거래량은 개장부터 한 분이라도 없으면 이후 NULL이다. 최대 거래량 봉·시각은 관측분 기준이며 `volume_path_complete`로 전체 확인 여부를 구분한다.
- 0~3/0~5/0~10/0~15/15~30/30~45/45~60분은 각각 volume_sum, high/low, (H−L)/구간O×100, 구간 O→마지막 C return을 보존한다. 구성 OHLCV가 불완전하면 구간 합계·가격도 NULL이다.

### Anchor, event, excursion

regular open, first 1/3/5m high/low, opening 10/15m high/low, first-hour high/low/마지막 close, previous close/gap을 저장한다. anchor 구간의 HLC가 완전할 때만 high/low를 확정한다. 해당 range가 완료된 이후 H>anchor high 또는 L<anchor low인 첫 관측 봉을 break event로 기록한다. 가격은 관측 high/low이며 threshold를 별도 보존한다. 돌파와 고저점 시각은 **정확한 tick이 아니라 [분 시작, 다음 분 시작) 구간**이다. `event_at`/`*_first_break_at`은 그 구간 시작이고 `confirmed_at`은 봉 완료 시각이다. 앞선 분이 비었다면 first_time_certain=false, outcome의 first_break_at=NULL, first_observed_break_at만 기록한다. 복귀는 돌파 봉을 포함한 후속 **관측 close가 range 안**에 들어온 최초 완료 시각으로 정의한다. 분 중간의 모든 복귀를 알아냈다는 의미가 아니다.

HOD/LOD는 첫 봉으로 초기화하고 이후 엄격한 갱신만 count한다(첫 봉 자체는 update 0회). 최종 고/저점의 최초 달성 **분 구간**과 개장부터 구간 시작까지의 초를 기록한다. 결측이 있으면 전체 high/low·갱신수·시각은 NULL, `observed_*`는 별도 부분 관측이다. `max_pullback_from_hod_pct`는 **이전 완료 봉들까지의 최대 H** 대비 후속 봉 L의 최소 return(0 이하), rebound는 이전 완료 봉들까지 최소 L 대비 후속 봉 H의 최대 return(0 이상)이다. 같은 봉 H/L의 선후를 추정하지 않으므로 tick 기준 전체 intrabar 최대값과 구분하는 `excursion_path_basis`를 둔다.

개장 +3/5/10/15/30/45/60분 snapshot은 그 시점까지 완료된 봉만 사용한다. close·open/previous-close return·open 기준 MFE/MAE·high/low·volume·HOD/LOD·HH/HL/LH/LL·3m/5m range break·후보 발견 여부·당시 확인된 출처를 보존한다. open 기준 MFE/MAE는 해당 구간 전체 H/L을 실제 09:30 O와 비교하고 경로 결측이면 NULL이다.

detection 기준 +1/3/5/10/15/30분과 10:30 outcome은 **실제 detection price**가 있을 때만 계산한다. MFE=max(0,(maxH/detectionPrice−1)×100), MAE=min(0,(minL/detectionPrice−1)×100). target>10:30은 `outside_opening_hour`, 가격 없으면 `missing_detection_price`, 결측은 `incomplete_minutes`다. detection/target이 분 경계와 일치하지 않으면 포함 분봉의 H/L 선후를 알 수 없으므로 정확한 MFE/MAE는 NULL/`unaligned_detection_window`다. 이때 창 안에 완전히 포함된 실제 봉만의 `observed_bar_mfe/mae_pct`를 별도로 저장한다. target과 일치하는 완료 봉 close가 없으면 endpoint return도 NULL이다. 종가를 찾기 위해 10:30 이후 봉/최근접 가격을 쓰지 않는다.

Outcome은 open/detection→10:30 return, 두 기준 MFE/MAE, first-hour high/low/close/time, 구조/최대 거래량/첫 break 정보를 모은다. EOD를 목표로 사용하지 않는다. 동일 day/run/ticker의 실제 entry가 있는 Paper trade ID 목록만 선택적으로 연결하며 Paper DB를 ALTER하거나 research 값을 live decision에 전달하지 않는다.

### 저장·재현성·품질

기본 `data/opening_hour_research/opening_hour.db`, `PRAGMA user_version=1`, feature_version=`opening_hour_v1`이다. 독립 `opening_hour_sessions/candidates/bars/events/snapshots/outcomes/segments` 테이블에 공통 조회 필드와 전체 detail JSON을 저장한다. 기존 research schema 4, Paper schema 1, rotation extension schema 1, Master schema 1은 변경하지 않는다. 다른 종류/버전의 출력 DB는 쓰기 전에 거부한다.

metadata는 ET date, radar_run_id, test_version, source DB/cache/Paper 경로, source fingerprint, bar/calendar policy, 원본 collection mode/compliance와 종료 시각을 담는다. DB의 `created_at`은 실제 최초 저장 UTC이고 재실행에도 보존한다. 생성 벽시각은 재현 가능한 export에서 제외한다. 동일 evidence/version은 같은 research ID로 idempotent 저장하며, 다른 cache/evidence는 새 ID로 보존한다. 원본 Radar lifecycle에 run ID가 없는 legacy 자료는 원본 identity+첫 batch 시각의 deterministic ID를 사용한다.

export는 `<output-db parent>/export/<ET-date>/<research-id>/` 기본값에 candidates.csv, bars_1m.csv, bars_3m.csv, bars_5m.csv, events.csv, time_snapshots.csv, outcomes.csv, volume_segments.csv, metadata.json, quality.json, summary.json이다. 기존 Radar export/Review ZIP에 섞지 않으며 날짜별 연구 폴더를 별도 ZIP으로 보관한다. CLI는 [루트 실행법](../README.md#opening-hour-research-v1)을 따른다.

quality는 expected/actual/stored 1m rows, missing minute 및 OHLCV 불완전 수, 완전60분 후보/불완전 후보를 구분한다. `full_1m_coverage_rate`=완전60분 후보/전체 후보, `observed_1m_row_rate`=유효HLC 분/기대 분, `complete_ohlcv_1m_rate`=완전OHLCV 분/기대 분이다. 3m/5m rate는 전체 기대 집계봉 중 완전봉 비율이다. detection price와 endpoint outcome availability도 기록하며 단위는 0~1, 표본 0이면 비율 NULL이다. COMPLETE는 후보가 있고 모두 60분 OHLCV가 있을 때만 부여하며 전략/프로토콜 판정이 아니다.

summary는 개수·coverage·open→60분 return/MFE/MAE median과 각 표본 수, 양/음/동일 outcome count, 최초 확인 출처별 표본 수만 제공한다. 한 후보가 여러 출처에 속할 수 있다. BUY/SELL, composite score, 상승확률, 의도 추정 tag, 패턴 추천/최적화는 없다. Capacity/Manual/late evidence의 원래 protocol 제약도 그대로 남는다.

## Architecture / Provider

NH RC 키 감사: [공식 gbstock 명세](https://www.nhplug.com/openapi-docs/gbstock/openapi.json)의 `x-realtime-channels`를 정본으로 사용한다. 공식 [m_gtsstock.h](https://www.nhplug.com/instruments/m_gtsstock.h)와 `.mst`에서 `sSymbol`(offset 98/폭 12), `sNationCode`(95/3)로 AAPL/USA 행을 찾고 `sGIC`(0/15)를 읽는다(레코드 164바이트, CP949). SDK는 우측 공백을 제거하며 AAPL 유효 GIC는 7문자다. wire 키를 임의로 15문자로 채우거나 티커/prefix로 생성하지 않는다. 실제 직렬화한 RC 키가 resolver 결과와 같은지 전송 전에 검사하고 형식/길이만 기록한다. 재시험 ACK WSS10012이며, 서버 거절과 정상 ACK의 키 불일치를 구분한다. rc는 명세상 미국·중국 지연정보이며 이 작업에서는 구독·fallback을 추가하지 않았다.

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

UI timer는 메시지와 완료된 Future만 처리한다. Master 조회/import/CSV ZIP/hash와 live health SQL은 별도 단일 research executor에서 실행한다. 수집 지표와 Last gauge는 5초 간격 DB snapshot으로 읽으며 오류 시 마지막 값임을 표시한다. Current Connected/Active WS는 controller thread가 provider의 현재 WS 연결/ack를 읽는다. STOPPED는 No/0이고 Last 값은 변경하지 않는다. RUNNING은 Current Protected/Discovery/Slot unavailable(최근 DB 기준), Current Saturation(현재 ack 기준)을 표시한다. STOPPING은 종료 중 실제 연결 상태를 유지하며 인위적으로 지연하지 않는다. 정상 종료 label은 import 메시지 이후에도 남는다. Stop은 동일 Event/기존 graceful close를 요청하고 중복 클릭을 무시한다. 수집 중 창 닫기도 정상 종료를 기다린다. 앱 종료 시에도 non-daemon controller가 final export와 자동 import·validation을 마친다. 강제 kill은 없다.

Data root 아래 실행 시각+UUID 폴더를 만들고 `dashboard_run.json`에 실제 시작·종료·clean_shutdown·shutdown 시간을 보존한다. max WS 기본 200과 grace 0.25초 등은 CONFIG를 따른다. 200은 최적 capacity 권고가 아니다. QSettings에는 마지막 max WS/data root/창 위치/열 폭만 저장한다. 기존 단일 Kiwoom lock을 그대로 공유하므로 UI/CLI 충돌은 ERROR 및 명확한 메시지로 표시한다.

v2.4.2: controller의 `dashboard_run.json` 및 최종 protocol JSON 갱신은 임시 파일 작성 후 atomic replace한다. 교체 중 Windows `PermissionError`의 winerror 5/32/33만 25·50·100ms 대기 후 최대 3회 재시도한다(총 대기 175ms, controller thread). 영구 거부·다른 I/O 실패를 성공 처리하지 않으며 기존 공개 파일을 직접 덮어쓰지 않는다. 실패 이벤트에는 고정 metadata 파일명·mkdir/write/replace 단계·OS 오류 번호만 추가하고 원래 예외 본문/전체 경로를 노출하지 않는다. 시작·종료 허용 오차와 표준 수집 정책은 그대로이며 실제 지연은 기존 protocol로 판정한다.

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

Capacity는 open+65분, Regular는 공식 close에 50ms controller 점검으로 graceful stop을 요청하며 30초 snapshot 대기를 깨운다. FINALIZING은 provider close/최종 export/quality/metadata/Master import/validation을 포함한다. import 실패는 Session ERROR/비준수이며 collector 수집 성공 상태와 구분하고 수동 import 재시도를 안내한다. 준비 전 또는 DB 생성 전 취소는 lifecycle만 보존한다. 한 번 arm은 한 거래일이며 다음 거래일 자동 반복/절전 깨우기는 없다.

Capacity의 장중 late arm은 명시적으로 거부하고 공식 close 이후에 다음 거래일을 선택한다. Regular late arm은 허용하되 late_start/비준수/실제 시각을 저장한다. 시작·종료 tolerance 1초, 관측 공백 5초, UTC/monotonic drift 2초 기준은 [TEST_PROTOCOL](../TEST_PROTOCOL.md)에 고정했다. 연결이 open 이후 처음 확인되면 1초 이내에도 비준수다. 가짜 open 시각·누락 snapshot 소급 생성은 없다.

Protocol은 lifecycle, `radar_metadata.collection_protocol` JSON, export metadata/quality, ZIP manifest에 기록한다. Master schema 1의 `quality.summary_json`으로 보존하고 History 조회 시 필드를 펼치므로 research schema 4/Master schema 1 migration은 필요하지 않다. legacy 값은 unknown이며 20/50/100 과거 자료를 compliant로 채우지 않는다. `actual_stop`은 gate 폐쇄/stop 요청, lifecycle stopped_at은 collector 종료 완료, regular_duration_seconds는 실제 연구 구간이다. 기존 History duration은 arm을 포함한 controller 구간이다. full_session은 준수 Regular 실행에만 true다.

## Automatic Session Validator 1

`research/validator.py`는 투자 점수 없이 세션 시스템/데이터 품질을 판정한다. Dashboard controller는 final export·quality·최종 protocol 저장과 Master import 뒤에 실행하며 완료까지 FINALIZING을 유지한다. 검증기의 실행/저장 실패는 collector metadata를 다시 쓰거나 성공한 원본을 변경하지 않는다. Live에는 검증 결과 또는 unavailable, History에는 Validation 열·Validation Summary 탭·Revalidate Session을 제공한다. 긴 상세 내용은 JSON으로 확인한다.

검증 범위는 XNYS/준수/arm/start/stop/clean shutdown, preopen snapshot·event·candidate, SQLite integrity/필수 table/FK/첫 포착 snapshot·event, CSV 4종의 헤더·행 수·내용, metadata/quality 재계산, capacity peak/last, RVOL/return/stale/REST/WS/backfill/runtime, expected horizon과 sample timestamp/offset이다. 아직 종료 시점에 도달하지 않은 pending은 그대로 허용한다. 정량 정책과 severity는 [TEST_PROTOCOL](../TEST_PROTOCOL.md)에 둔다.

원본 SQLite는 DB와 committed WAL을 임시 폴더로 복사해 query_only transaction으로 읽으므로 원본 SHM도 수정하지 않는다. 파일 복사 중 변경을 확인하며 전후 logical DB/export/lifecycle fingerprint를 비교한다. snapshot raw는 전체 시장이 아닌 수집 범위 evidence다. capacity peak는 batch gauge의 최대값이며 batch 사이의 미관측 순간 peak를 보증하지 않는다.

Summary는 `<session>/export/<ET-date>/validation_summary.json`에 atomic replace한다. 필수 키는 validator_version/session_date/run_id/validated_at/overall/protocol/integrity/capacity/data_quality/results/errors/warnings이며 policy와 evidence_sha256도 보존한다. PASS/WARN/FAIL은 수집 품질이며 수익성 결론이 아니다. Manual/legacy는 표준 비교 적합성을 보증하지 않는다.

Master schema 1의 `quality.summary_json.validation`에 전체 결과를 저장하고 조회 때 validation_overall/validator_version/validated_at을 펼친다. research schema 4, Master schema 1 유지. Revalidate는 원본을 수정하지 않으며 동일 run_id를 갱신한다. 재import는 evidence fingerprint가 같은 validation만 유지하고 달라진 결과는 unknown으로 만든다. Review ZIP은 validation JSON도 기존 민감정보/경로 검사·SHA256을 적용하고 manifest에 validation_overall/validator_version을 기록한다. validation identity/freshness가 다르면 ZIP 생성을 거부하므로 먼저 재검증한다.

자동 실행은 Dashboard 종료 경로다. 기존 CLI 수집 정책은 그대로이며 `python -m market_radar.research.validator <source.db> --date YYYY-MM-DD`는 보관 세션 JSON만 생성하는 별도 검증 명령이다. Master 등록 실패 시에도 가능한 원본에 대해서는 JSON 산출을 시도한다. DB 생성 전 취소는 lifecycle만 남는다.

상위 방향은 [PROJECT_DIRECTION](../PROJECT_DIRECTION.md), 새 대화는 [START_HERE](../START_HERE.md), 현재 연구 단계는 [HANDOFF](../CODEX_HANDOFF.md)를 따른다.

## Review ZIP

`research/review_export.py`는 선택 세션(선택이 없으면 현재 세션)의 export allowlist `quality_summary.json`, `candidates.csv`, `snapshots.csv`, `events.csv`, `results.csv`, `metadata.json`, `validation_summary.json`과 해당 ET 날짜 로그만 묶는다. 없는 파일은 manifest에 기록하며 파일 전체가 없으면 생성하지 않는다. DB/cache/raw/임의 하위 폴더는 포함하지 않는다. `market_radar_review_<ET-date>_<run-id>.zip`을 해당 세션 `reviews/`에 atomic replace로 저장한다.

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

20은 smoke/test 규모이며 전체 후보 추적용 주간 운영 권장값이 아니다. 기존 50/100-slot 포화에 이어 2026-10-06 ET 표준 150-slot 세션을 검증했다. 최대 Active 106, protected 89, discovery 80, saturation 70.67%, 미배정 0건이다. 150을 고정 운영 후보로 사용할 근거이나 다른 시장 상태의 추가 확인이 필요하며 영구 최적값 판정은 아니다. 기존 CONFIG max WS와 사용자 저장값은 변경하지 않았다.

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

Dashboard canonical cache는 세션 밖에서 계속 재사용한다. legacy cache 자동 복사/merge는 하지 않으며 필요하면 별도 명시적 import/migration으로 검토한다. cache의 provider=kiwoom/mode=real/schema=1/bar_policy=assumed_et_start_v1 검증을 DDL/journal 변경 전에 유지한다. cache에는 raw_records도 누적되며 raw 분리/자동 삭제는 이번 범위 밖이다.

공유는 매 실행 cold cache 구조를 없애는 변경이며 RVOL available을 보장하지 않는다. 부족한 history와 무체결 불확실 분은 기존처럼 NULL/MIXED_OR_UNCERTAIN이다. 최근 150-slot 보관 세션의 RVOL은 0%였으며 누적 coverage 효과는 추가 거래일에서 확인한다.

~~~powershell
# Manual 진단 예시: 표준 Capacity Test는 Dashboard에서 preopen arm
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

- 20/50/100슬롯의 saturation 이후 표준 150-slot 세션에서 peak 70.67%와 미배정 0건을 확인했다. 장기 부하·다른 시장 상태·전체 후보 추적 여부는 별도 검증해야 한다.
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
