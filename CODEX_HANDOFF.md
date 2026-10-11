# CODEX HANDOFF

갱신: 2026-10-12 KST. 현재 상태만 유지하며 영구 이력은 [CHANGELOG](CHANGELOG.md)에 둔다. 새 대화는 [START_HERE](START_HERE.md), 최상위 방향 원본은 [PROJECT_DIRECTION.docx](PROJECT_DIRECTION.docx), 도구용 mirror는 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md), 수집 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md)을 따른다.

## 현재 버전과 연구 단계

- **Market Radar v2.6.0 / Opening Hour Research v1 / opening hour schema 1 / Paper Trading v1 / paper schema 1 / Rotation v2 / rotation extension schema 1 / Validator 1 / Collector v2.1 / test_version=kiwoom_radar_v2_1 / research schema 4 / Master schema 1 / Windows Quick Launch v1**.
- **Phase A 마무리 ~ Phase B 시작**. 실데이터 수집은 시작됐고 2026-10-06 ET 표준 150-slot Capacity 세션의 검증 결과가 있다. 여러 거래일의 표준 full-session/1주 축적 완료나 전략 확정을 뜻하지 않는다.
- 최종 목표는 시장 후보 → 검증 조건 → 기존 Stock Helper 가격구조 분석 → 데이터 기반 단기매매 의사결정 보조다. Current Leaders/Emerging은 후보 탐색의 두 축이며 향후 검증된 Candidate Rank와 구분한다. 자동주문은 현재 기본 목표가 아니다.
- 기존 Current/Emerging 공식, RVOL/NULL, 보호 추적, result sampling, provider 및 OCR/매입가/반등가/벽/Toss/halt는 유지한다. launcher로 바탕화면 Stock Helper 또는 launch_stock_helper.cmd를 사용한다. 상세 실행법은 [README](README.md).

## 수집과 저장

- **Regular Session**: 매 거래일 preopen arm → XNYS official open 시작 → official close 종료(조기폐장 포함). 장중 시작은 허용하되 late/비준수다.
- **Capacity Test**: preopen arm 필수 → official open 시작 → open+65분 자동 종료. Manual은 진단용이며 표준 비교 세션으로 소급 지정하지 않는다. 정시 시작·종료 허용 오차는 각 1초다.
- ARMED/PREPARING/PREOPEN_WAIT → REGULAR_LIVE → FINALIZING → STOPPED. 개장 전 인증·cache·과거 backfill 준비와 정규장 후보 순위·snapshot·FIRST_DETECTED·결과 타이머를 분리한다.
- Dashboard shared historical cache는 **data/market_radar_cache/kiwoom_v2.db**이며 현재 파일이 존재한다. 세션별 DB/export/log와 분리해 재사용하고 legacy cache를 자동 병합하지 않는다. cache 공유가 RVOL coverage를 보장하지는 않는다.
- Master는 **data/market_radar/master.db**, 각 session은 실행별 폴더다. 한 번 arm은 한 거래일이며 다음 거래일에는 다시 arm한다.

## Automatic Session Validator

- Dashboard 종료 시 FINALIZING → final export/quality/최종 protocol → Master import → Validator → validation_summary.json/Master 검증 상태 → STOPPED. 검증기 실패는 성공한 수집 원본을 변경하지 않는다. DB가 있으면 등록 실패 후에도 검증 JSON 산출을 시도한다.
- Protocol/XNYS/timing/preopen, SQLite·참조·metadata·quality·CSV, capacity, 데이터 품질, horizon·sample 일관성을 검사한다. PASS/WARN/FAIL은 수집 품질 판정이며 투자 신호가 아니다. 정책·severity는 TEST_PROTOCOL에 둔다.
- 원본 DB/WAL의 임시 복사본을 읽고 검증 JSON을 atomic 갱신한다. Master schema 1의 quality.summary_json.validation을 사용한다.
- History Validation 열·Validation Summary·Revalidate Session, Live 간단 상태를 제공한다. Review ZIP은 validation JSON과 manifest 상태를 포함하고 identity/freshness를 검사한다.
- 자동 검증은 Dashboard 종료 경로다. 기존 CLI 수집은 Manual이며 별도 검증 명령은 JSON만 생성한다. 사용법은 TEST_PROTOCOL을 따른다.

## Latest 150-slot baseline

원본 data/20261006_184830_801a37e9, ET 2026-10-06, run_id cec24441029dc588e58bf6c97d51028b. 아래는 기존 validation_summary.json의 결과이며 이번 문서 검수에서 Validator를 재실행하지 않았다.

- **Overall WARN**, Protocol/Premarket Leak/DB·export Integrity/Capacity **PASS**, errors 0, clean shutdown. 시작/종료 지연 **57.473ms/56.438ms**.
- **8,584 snapshots / 91 candidates / 231 unique snapshot tickers**. peak Active/Protected/Discovery **106/89/80**, saturation **70.67%**, 미배정 event/peak/last **0/0/0**.
- Last Active/Protected/Discovery **105/78/74**, saturation **70%**. 한 거래일의 슬롯 여유 근거이며 150의 영구 최적값 증명은 아니다.
- WARN: RVOL **0%**, stale **723건/59종목/7.7683%**, deterministic REST **53건**, suppressed **36**, incomplete backfill **3,813 attempts**, missing results **37**.
- +60m pending **78**, EOD/D+1/D+2/D+3 각각 pending **91**은 종료 이후 목표로 정상이다. 상세 horizon·품질 수치와 이전 원본 해시 검증은 CHANGELOG에 보존한다. 검증 JSON은 원본 session의 export/2026-10-06/validation_summary.json에 있다.

## 문서 원본과 검증 범위

- 사용자가 교체한 **2026-10-07 기준 PROJECT_DIRECTION.docx가 source of truth**다. MD는 누락된 의미를 보완했고 START_HERE의 읽는 순서를 따른다. DOCX를 수정·재생성하지 않는다.
- 현재 DOCX SHA256: a92ad593d07d54475268c449374d0bbf1581cc55c9a681e74df3c77564e8175b. 이번 Opening Hour 작업 전후 동일하며 DOCX/MD 방향은 변경하지 않았다.
- RESEARCH_BASIS의 7개 연구 축은 DOCX와 일치한다. 정확한 citation은 **출처 검증 필요**이며 논문명·저자·URL을 만들지 않았다.
- 현재 offline 검증: Opening Hour **64**, Paper v1 **59**, Rotation **30**, NH **48**, Radar/Kiwoom/Dashboard/scheduling/metadata/Validator/audit **247**, 총 **448 통과**. 모두 합성 입력·mock·임시 저장소다. 과거 rotation 384개와 metadata 실패·반복 검증·data 보존 이력은 CHANGELOG에 보존한다.
- 동일 fixture의 baseline/rotation switches/post-exit 결과는 research 분석 전후 동일하다. 신규 테스트에서 60분·집계·결측·시각 정밀도·MFE/MAE·원본 DB/WAL/SHM 보존·DB roundtrip·export 재현성·UI OFF/Capacity·실제 background 종료 순서를 확인했다. CLI 구조 smoke도 임시 합성 DB만 사용했다. 실제 Kiwoom/NH 네트워크·live shadow·보관 실데이터 신규 분석·RC smoke는 실행하지 않았다. actual order call **0**, 수익성 검증이 아니다.
- `paper/`, `paper_rotation_v2/`, `execution/`, `providers/`, 기존 v1/v2 fixture/test, runtime/scheduling 및 방향 DOCX/MD 등 38개 파일의 SHA256은 작업 시작 시와 동일하다. `.env`/인증값/원본 data를 이 작업의 입력·출력으로 사용하지 않았다. 순위·연구 schema 4·Validator 변경 없음. `git diff --check`, 문서 상대 링크·현재 버전 일치·신규/갱신 파일 인증값 패턴 검사 PASS. 실제 인증값을 읽어 대조한 검사는 아니다. 임시 설정/DB로 offscreen UI의 Regular ENABLED·Capacity OFF도 렌더 검수했다.

## Opening Hour Research v1 현재 상태

- `market_radar/opening_hour/`에 read-only source, engine, storage, service를 추가했다. `tools/opening_hour_research/analyze_session.py --session-dir <dir>`로 종료된 과거 세션을 명시적으로 분석할 수 있다. 기존 candidate/discovery/shared cache/완료 1m을 재사용하며 새 REST·WS·backfill·주문 호출은 **0**이다.
- Dashboard 상단 옵션은 기본 OFF, Regular Session 전용, Capacity/Manual 강제 OFF다. 자동 분석은 collector/Paper 종료와 기존 export/Master/Validator 이후에만 실행된다. 자체 실패는 INCOMPLETE로 격리한다. live strategy input/hook을 추가하지 않고 Paper 거래 reference ID만 같은 day/run/ticker로 읽는다.
- XNYS official open~open+60분, 09:30~10:29 시작 1m 60개 → 3m 20개/5m 12개. 첫 60분 unique candidate/discovery는 TOP10/구독/Paper 여부와 무관하게 포함한다. 실제 cache 시가를 읽고 늦은 발견 전 봉은 pre_detection으로 구분한다. unknown/missing/불완전 집계는 NULL이며 거래량 0으로 메우지 않는다.
- anchor/돌파/HOD·LOD·HH·HL·LH·LL·volume·구간 합계·+3/5/10/15/30/45/60 snapshot과 두 기준 excursion/outcome을 보존한다. event/extrema는 1분 구간, intrabar 선후는 알 수 없다. 분 중간 detection의 정확 MFE/MAE는 NULL이며 완전 포함 봉의 부분 관측만 별도 필드다. pinned usa06011의 공식 minute notional 부재로 NULL을 유지한다.
- 별도 `data/opening_hour_research/opening_hour.db` / schema 1 / feature `opening_hour_v1`. 기본 export는 같은 부모의 `export/<ET-date>/<research-id>/`에 8 CSV+metadata/quality/summary JSON이다. 동일 입력의 export가 재현되며 다른 evidence는 새 ID로 보존한다. actual created_at은 DB의 최초 생성 시각으로 유지한다. 실제 data에는 개발용 DB/export를 생성하지 않았다.
- COMPLETE는 60분 OHLCV coverage 상태다. 기술통계·표본 수만 제공하며 buy signal/prediction model/profitable pattern/score/probability를 만들지 않는다. 원본 Capacity/late/non-compliant 표기는 유지한다. 수식·정밀도·NULL 조건은 [기술 README](market_radar/README.md#opening-hour-research-v1), 운영/CLI는 [README](README.md#opening-hour-research-v1)에 둔다.

## Paper Trading v1 현재 상태

- v2.4.1은 잠금 확보 전에 다른 실행의 OPEN을 복원/중단시키던 가능성, 늦은 shutdown 완료 시각으로 수동 중지를 SESSION_END로 오분류하던 가능성, 동일 수신시각의 서로 다른 가격을 생략하던 문제를 수정했다. provider 연결·collector lock 확보 뒤 Paper를 시작하고 해제 전에 종료한다. 중지 요청시각은 최초 값으로 고정하고 이후 tick/새 signal은 차단한다. 같은 시각의 다른 가격은 도착 순서대로 처리한다.

- `market_radar/paper/`에 독립 policy/engine/storage/runtime/UI/replay를 구현했다. Dashboard Paper Trading 탭의 Enable은 기본 OFF이고 수집 시작 전에만 설정한다. Capacity Test에서는 UI/controller/worker가 강제 차단하여 Paper DB/thread/WS hook을 만들지 않는다. Regular Session 및 Manual 정규장 진단만 지원한다.
- `paper_v1_baseline`은 **experimental shadow test**, **strategy profitability = not established**다. 양쪽 TOP10 → Emerging → Current, 그 안에서 Emerging rank → Current rank → ticker 순이다. 기존 슬롯 배정·rank 공식은 변경하지 않는다.
- WATCHING 이후 실제 새 완료 1m 봉, 양수 1m/5m return, watch 가격 초과·freshness·정상 WS가 필요하다. NULL이면 진입하지 않으며 RVOL/OR/HOD는 불변 evidence로만 저장한다. Signal은 실제 평가 시각, OPEN은 그보다 늦은 첫 유효 FE 수신가격이다. 30초 snapshot을 tick proxy로 쓰지 않는다.
- 동시 OPEN 1개·ET 일일 4회·동일 ticker 재진입 금지. +15%/-10% crossing 관측가격, 정상 official close의 SESSION_END, gap의 DATA_INTEGRITY_INVALID, 중단/restart의 INTERRUPTED를 구분한다. 재시작은 DB의 count/ticker 차단을 복원하며 미완료 watch/signal도 중단 기록 후 새 관측부터 시작한다.
- 별도 `data/paper_trading/paper_trades.db` / paper schema 1. 현재 작업에서는 임시 DB로만 테스트했고 실제 data에 Paper DB를 생성하지 않았다. entry evidence/summary·gap 범위·MFE/MAE를 저장하고 종료 뒤 exact-time horizon과 CSV/JSON summary를 생성한다. 가격은 로컬 수신시각 기준이고 비용·spread·slippage·실제 체결 가능성은 모델링하지 않는다.
- **Actual orders = disabled / not implemented**. NH import/호출 없음. 기존 2026-10-07 clock_jump 세션은 수익성 표본으로 사용하지 않았고 실제 DB replay도 수행하지 않았다. 실행법은 README를 따른다.

## Rotation v2 / post-exit review 현재 상태

- 사용자 요청에 따라 독립 실험 `paper_rotation_v2`를 추가했다. v1 baseline은 변경·삭제하지 않는다. Dashboard Paper 탭에서 Baseline v1과 Rotation v2를 구분하며 v2 기본 OFF, 선택하면 v1도 실행한다. Capacity 모두 강제 OFF다.
- 같은 직렬 시세·평가시각을 두 독립 engine/DB에 전달한다. v2 normal confirmation은 v1을 재사용하고, 새 후보의 양쪽 TOP10·양쪽 rank/완료 momentum 우위·fresh/WS 정상·당일 미거래 및 현재 약화 증거를 모두 요구한다. NULL/누락 rank는 근거 부족으로 전환하지 않는다. 이름은 material evidence dominance이고 상승확률은 없다.
- switch signal보다 늦은 기존 ticker 첫 유효 FE로 exit, exit보다 늦은 새 ticker 첫 유효 FE로 entry다. 같은 관측 재사용 없음. 각 전략의 일일 4회·재진입 금지·OPEN 1개를 별도로 유지한다. gap/invalid/중단/restart는 pending switch를 취소하고 미확인 체결가격은 NULL이다.
- v1 DB/export/schema 보존. 별도 `data/paper_trading/paper_rotation_v2/review.db`에 v2 거래·전환 근거·v1 복사본·post-exit 집계와 실제 tick을 저장한다. 원래 entry/TP/SL, extrema/return, TP/SL hit+시각, TP 추가 상승/반락, SL 회복 및 evidence tag를 제공한다. official close까지 기존 보호 입력을 연장한다. 조기중지/공백은 INCOMPLETE이고 SESSION_END의 이후 창은 NO_POST_EXIT_WINDOW다. 추적 결과는 판단 입력이 아니다.
- 동일 시작 trade는 exact run/ticker/signal/entry 시각·가격으로 연결한다. v1 종료수익률과 v2 chain 복리, old ticker의 switch 이후 TP, 새 ticker MFE/MAE를 별도 CSV로 보존한다. 동일시각 old 가격이 있을 때만 aligned 차이를 계산하고 없으면 NULL이다. 가격/비용/수익률 가정과 모든 필드는 기술 README에 둔다.

## 독립 NH read-only adapter v1.1

- Kiwoom은 실시간 시장 시세·market discovery/research, NH는 read-only execution-price/account 조회로 역할을 분리한다. `execution/nhplug_client.py`·`nhplug_accounts.py`와 명시적 `tools/nhplug_probe/probe.py`·`accounts.py`를 사용하며 Dashboard/Collector/Paper에 자동 연결하지 않는다. NH 구현은 이번 작업에서 그대로 유지했다.
- 공식 `nhplug[tls]==0.4.1`. 실제 OAuth·계좌목록·AAPL REST 현재가 PASS. 운영 유형 01·02 기준 **3개** 모두 미국/USD 잔고 조회 **PASS**, 응답 순서의 보유 종목 수 **0·0·1개**다. 빈 잔고를 사용 불가 계좌로 처리하지 않는다.
- 공식 잔고 `/gbstock/inquiry/v1/balance`와 매수가능수량 `/gbstock/inquiry/v1/buyableAmount`를 사용한다. `pcs_dit="2"`, 원화증거금·지정가 기준, NH AAPL REST 가격 **336.265 USD**로 3개 계좌 모두 수량 조회 **PASS**. `byn_pbl_qty`/`orr_pbl_qty`/`max_pbl_qty` 모두 **0**이다. 이 값은 조회 당시 해당 조건의 결과이며 실제 주문 가능 여부를 주문으로 검증한 것은 아니다.
- 사용 가능 계좌가 3개이므로 기본 계좌를 선택하지 않았다. 끝 4자리만 마스킹 출력하고 `live-N`은 이번 응답 순서의 임시 구분자다. `.env`·`NHPLUG_DEFAULT_ACCOUNT`를 자동 추가·변경하지 않는다. 사용자 선택이 필요하다.
- **RC/WSS10012는 보류**. 기존 GIC/RC 구현을 그대로 보존했으며 이번 작업에서 연결·재구독하지 않았다. 이전 재시험의 이용권한/유료시세 약정 원인은 미확정이고 상세 이력은 CHANGELOG에 둔다. 지연 rc fallback도 없다.
- 위 NH 수치는 이전 계좌 smoke 결과이며 이번 Paper 작업에서 재조회하지 않았다. 토큰은 메모리만 사용하고 원본 응답은 저장하지 않는다. NH 실제 주문은 미구현이며 주문 경로는 차단한다. 실행법·입력 조건은 README와 기술 README를 따른다.
- NH probe를 별도 프로세스로 다시 실행하면 파일 캐시가 꺼져 있어 새 토큰 발급/보안 알림이 발생할 수 있다. 같은 프로세스의 유효 토큰은 재사용한다. 이번 재검토는 코드·오프라인 테스트만 수행했으며 발급/주문 호출 0건이다. 안전한 실행 간 토큰 재사용은 아직 구현하지 않았다.

## 남은 제약

- controller metadata 교체의 일시적 Windows 접근 거부는 25·50·100ms의 제한된 재시도로 복구한다. 영구 거부·임시 파일 작성 실패·기타 I/O 실패는 감추지 않고 파일명/작업 단계/OS 번호를 표시한다. 외부 점유 프로세스는 미확정이며 장기 실운영의 무오류를 보장하지 않는다. scheduling/Capacity 정책과 허용 오차는 변경하지 않았다.

- RVOL 0% 세션은 RVOL 조건 연구 근거가 되지 않는다. stale/결측과 Validator 경고를 분석별로 고려한다. shared cache coverage 축적·다른 시장 상태·장기 CPU/UI/연결 안정성·새 실제 자동 검증 종료 흐름은 추가 관측이 필요하다.
- feed 범위/분봉 라벨/무체결 불확실성/unknown 종목 유형/NULL VWAP/EOD provisional 제약을 유지한다. 자동 D+n 수집·주간 재arm·Windows 깨우기·같은 입력의 다중 슬롯 replay는 미구현이다.

## Next Work

다음은 월요일 새 Regular Session에서 Opening Hour Research와 Paper/Rotation v2를 각각 명시적으로 Enable하고 preopen arm하여 official close까지 수집하는 것이다. 종료 후 validation·Opening Hour quality/coverage·날짜별 export, 동일 시작 trade 연결·전환 시각 분리·post-exit 추적 품질을 확인한다. 여러 거래일 descriptive dataset을 축적하며 수익성 검증과 구분한다. 파일 오류가 재발하면 파일명·작업 단계·OS 번호로 외부 점유/지속적 권한 거부를 추가 조사한다. Capacity Test는 두 전략과 Opening Hour 자동 분석 모두 OFF다. NH 운영 계좌 선택은 별개이며 기본 계좌를 임의 설정하지 않는다. RC/WSS10012는 보류하고 실제 주문은 구현하지 않는다. 기존 clock_jump 문제는 수정하지 않았으며 연구 단계는 아래 순서를 유지한다.

1. 표준 정규장 수집과 Automatic Session Validator의 실제 운영 안정성을 확인한다. 고정 운영 후보 **150 slots**, 동일 정책/shared cache로 Regular Session을 매 거래일 preopen arm해 official close까지 수집한다. CONFIG/default를 자동 변경하지 않는다.
2. 종료 후 validation과 coverage/stale/missing/포화·미배정을 확인한다. FAIL은 표준 비교에서 제외하고 WARN은 분석별 제약을 반영한다. 필요하면 추가 Capacity 실험을 검토한다.
3. 여러 거래일을 축적한 뒤 Current/Emerging·source·momentum·RVOL·HOD·OR별 +30s~D+3 결과를 가능한 범위에서 descriptive하게 분석한다. 1주는 첫 표본이며 필요하면 2주/4주 이상 검증한다.
4. 이후 유망 조건을 다른 기간·시장 상태에서 재검증하고 기존 벽/매입가/반등가와 독립 비교 후 결합 효과를 검증한다. 충분히 검증된 근거만 Candidate Rank/최종 GUI에 반영한다. 연구 citation은 원문 확인 후 보강한다.

## Git 상태와 작업 보존

이번 추가 범위는 `opening_hour/`, `tools/opening_hour_research/`, 신규 test, controller/UI의 종료 후 연결, 앱 버전 및 문서다. 기존 미커밋 Paper/Rotation/NH/metadata/runtime 변경은 모두 보존했다. `paper/`, `paper_rotation_v2/`, `execution/`, `providers/`의 파일은 한 바이트도 변경하지 않았다.

작업 시작 때 있던 추적 변경 10개와 미추적 Paper/Rotation/NH/test 파일을 보존했다. 이번에는 opening_hour 디렉터리·CLI 디렉터리·test 3개 status 항목을 추가했다. NH client/probe/test/requirements, .env.example, Paper/Rotation runtime 및 market_radar/runtime.py는 이번 작업에서 변경하지 않았다. 현재 상태는 `git status --short --branch`로 확인한다.

기존 scheduling/Kiwoom provider/slot/ranking/RVOL/Validator/cache/schema/launcher 코드는 변경하지 않았다. .env·원본 evidence를 편집하거나 Git에 추가하지 않았다. 이번 작업에서 git add/commit/push 및 사용자 변경 reset/restore/checkout/clean은 하지 않는다.
