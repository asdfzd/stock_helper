# CODEX HANDOFF

갱신: 2026-11-01 KST. 현재 상태만 유지하며 영구 이력은 [CHANGELOG](CHANGELOG.md)에 둔다. 새 대화는 [START_HERE](START_HERE.md), 최상위 방향 원본은 [PROJECT_DIRECTION.docx](PROJECT_DIRECTION.docx), 도구용 mirror는 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md), 수집 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md)을 따른다.

## 현재 버전과 연구 단계

- **Market Radar v2.6.10 소스 / 가족용 경량 EXE**. 새 postrun audit schema1. 기존 Collector2.1/research4/Paper1·schema2/Rotation2·extension1/Opening1.1·schema1/archive2/integration4/Master1/Validator1/family config1/watchdog1 유지. 프로젝트 방향·DOCX/MD·랭킹·전략·주문 OFF 불변이며 Phase A 마무리~Phase B 근거 축적 단계다.

## 현재 변경과 검증

- 가족이 **Market Radar 자동 수집 v2.6.10**을 한 번 실행하면 기존 당일65분/WS150/Paper·Rotation·Opening 자동 ARM과 실행별 사후 감사가 함께 작동한다. 추가 도구 실행은 필요 없다. ARMED는 인증 성공이 아니다. 초기 설정 오류도 배포본 audit_startup에 로컬 요약을 시도한다.
- 로컬 출력: data/market_radar_family/<ET-date>/attempt_<id>/audit/postrun_audit.json 및 .txt. 기존 JSON·제한된 live 메모리만 사용하고 원본 DB/ZIP은 열지 않는다. 연구FAILED와 보존VERIFIED·종료 요청을 구분하며 generic 최초 이벤트와 구체 primary_reason의 시각을 따로 보존한다. 코드 상세·크기/시간 제한은 기술 README를 따른다.
- MarketRadarAudit_*는 open+100분의 독립 관측 작업이며 기존 90분 비상 작업과 별개다. 다음 가족 실행에서 미완료 기록을 보충한다. PC OFF/완전 미실행의 외부 통보는 구현되지 않았다. 공개 저장소 업로드·자동 AI·ChatGPT automation 모두 OFF. 목적지/권한/비공개 검증·비용 한도와 별도 격리 worker가 있어야 후속 연결한다.
- 전체 unittest **853 PASS / FAIL0 / ERROR0 / SKIP0**, legacy **12/12 exit0**, 최종 실제 EXE **10/10 격리 case PASS**. R3 package25코드/PS/리소스/Windows PNG, literal 무인수 시작 오류→자동 감사, 실제 Task Scheduler의 독립 PID dry-run·삭제·부재 PASS. 실패/보완 이력은 CHANGELOG, 근거는 build/audit_v2610_20261101. 실제 API/Windows 전원·예약·취소/주문/외부 전송/AI 호출0.
- 최종 `dist/MarketRadarFamily_v2_6_10_FINAL_20261101_R3/MarketRadarFamily.exe`, SHA256 `e8e3881857c155e0dfc1795ab800eba6019112fc6f858155725bbde3507b13a4`, 989files. 새 아이콘은 인수 없음/같은 배포 폴더 working directory. 기존 v2.6.9·v2.6.8/Stock Helper·첫 v2.6.10 빌드는 보존하며 R3만 새 배포 대상으로 쓴다. 정상 종료ON·비상 종료OFF. 원본23672개 SHA256/크기/mtime 동일.

## 기존 첫 1시간 운영 정책

기존 DashboardOneHour의 bounded_forward_v1 정책, actual open monotonic+3,900초와 UTC stop, 연구 FAILED/보존 VERIFIED 분리, writer 종료/재export/DB integrity/ZIP CRC·SHA 검증, UTC/monotonic 개장+70분 및 OS 직전 재검사를 유지한다. 연구 품질이나 clock 이상을 숨기지 않는다. 일반 GUI의 실제 종료 기본 OFF·30초 독립 확인 버튼은 그대로다. 가족용만 위 명시적 경로/설정과 독립 감시를 사용한다. 이전 v2.6.4~7 구현·오류·검증 이력은 CHANGELOG에 보존한다.

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

- 사용자가 교체한 **2026-10-12 기준 PROJECT_DIRECTION.docx가 source of truth**다. MD는 같은 의미의 mirror로 보존하며 START_HERE의 읽는 순서를 따른다. 두 파일을 수정·재생성하지 않는다.
- 현재 DOCX SHA256: eb06f5add0fde0f5fecc424e00977932831d2f6d3279f10e9d67fe2c947641ef. MD SHA256: 50029336dea7d83563c4912d74a34509c8da35f7f3cf551e2d0ef496b0df840c. 이번 작업은 전략과 분리된 운영 자동화이며 두 파일을 보존했다.
- RESEARCH_BASIS의 7개 연구 축은 DOCX와 일치한다. 정확한 citation은 **출처 검증 필요**이며 논문명·저자·URL을 만들지 않았다.
- 이번 가족용 자동 감사·패키지·회귀·보존 결과는 build/audit_v2610_20261101의 JSON/log에 둔다. 이전 버전의 상세 검증·배포 해시·근거 위치는 CHANGELOG의 영구 이력을 참조한다.
- 실제 장애의 근본 병목이나 live 안정성은 미검증이다. v2.6.10 가족용 EXE·사후 감사 격리 smoke와 실제 감사 Task Scheduler dry-run은 완료했다. 첫 실제 수집에는 PC 자동 종료 없이 테스트를 유지한다.

## Opening Hour Research v1 현재 상태

- `market_radar/opening_hour/`에 read-only source, engine, storage, service를 추가했다. `tools/opening_hour_research/analyze_session.py --session-dir <dir>`로 종료된 과거 세션을 명시적으로 분석할 수 있다. 기존 candidate/discovery/shared cache/완료 1m을 재사용하며 새 REST·WS·backfill·주문 호출은 **0**이다.
- Dashboard 상단 옵션은 기본 OFF, Regular Session 전용, Capacity/Manual 강제 OFF다. 자동 분석은 collector/Paper 종료와 기존 export/Master/Validator 이후에만 실행된다. 자체 실패는 INCOMPLETE로 격리한다. live strategy input/hook을 추가하지 않고 Paper 거래 reference ID만 같은 day/run/ticker로 읽는다.
- XNYS official open~open+60분, 09:30~10:29 시작 1m 60개 → 3m 20개/5m 12개. 첫 60분 unique candidate/discovery는 TOP10/구독/Paper 여부와 무관하게 포함한다. 실제 cache 시가를 읽고 늦은 발견 전 봉은 pre_detection으로 구분한다. unknown/missing/불완전 집계는 NULL이며 거래량 0으로 메우지 않는다.
- anchor/돌파/HOD·LOD·HH·HL·LH·LL·volume·구간 합계·+3/5/10/15/30/45/60 snapshot과 두 기준 excursion/outcome을 보존한다. event/extrema는 1분 구간, intrabar 선후는 알 수 없다. 분 중간 detection의 정확 MFE/MAE는 NULL이며 완전 포함 봉의 부분 관측만 별도 필드다. pinned usa06011의 공식 minute notional 부재로 NULL을 유지한다.
- 별도 `data/opening_hour_research/opening_hour.db` / schema 1 / feature `opening_hour_v1_1`. 기본 export는 같은 부모의 `export/<ET-date>/<research-id>/`에 8 CSV+metadata/quality/summary JSON이다. 물리 source 경로를 fingerprint·metadata에서 제외한다. 같은 논리 evidence를 다른 root로 복사해도 ID/export가 동일하며 실제 내용 변경은 새 ID로 보존한다. 기존 v1 dataset과 최초 created_at은 재작성하지 않는다. 실제 data에는 개발용 DB/export를 생성하지 않았다.
- 모든 후보를 보존하고 기존 stock_type_policy에 따라 analysis_eligible·제외 사유를 기록한다. all/eligible 개수·coverage·제외 사유별 수를 분리하며 median/outcome 표본은 eligible 기준이다. 미구독·결측 자체로 후보를 제외하지 않는다. COMPLETE/PARTIAL/EMPTY는 각각 완전 coverage/일부 관측/관측 없음이며 처리 오류의 INCOMPLETE와 구별한다. 기술통계·표본 수만 제공하며 buy signal/prediction model/profitable pattern/score/probability를 만들지 않는다. 원본 Capacity/late/non-compliant 표기는 유지한다. 수식·정밀도·NULL 조건은 [기술 README](market_radar/README.md#opening-hour-research-v1), 운영/CLI는 [README](README.md#opening-hour-research-v1)에 둔다.

## Paper Trading v1 현재 상태

- v2.4.1은 잠금 확보 전에 다른 실행의 OPEN을 복원/중단시키던 가능성, 늦은 shutdown 완료 시각으로 수동 중지를 SESSION_END로 오분류하던 가능성, 동일 수신시각의 서로 다른 가격을 생략하던 문제를 수정했다. provider 연결·collector lock 확보 뒤 Paper를 시작하고 해제 전에 종료한다. 중지 요청시각은 최초 값으로 고정하고 이후 tick/새 signal은 차단한다. 같은 시각의 다른 가격은 도착 순서대로 처리한다.

- `market_radar/paper/`에 독립 policy/engine/storage/runtime/UI/replay를 구현했다. Dashboard Paper Trading 탭의 Enable은 기본 OFF이고 수집 시작 전에만 설정한다. Capacity Test에서는 UI/controller/worker가 강제 차단하여 Paper DB/thread/WS hook을 만들지 않는다. Regular Session 및 Manual 정규장 진단만 지원한다.
- `paper_v1_baseline`은 **experimental shadow test**, **strategy profitability = not established**다. 양쪽 TOP10 → Emerging → Current, 그 안에서 Emerging rank → Current rank → ticker 순이다. 기존 슬롯 배정·rank 공식은 변경하지 않는다.
- WATCHING 이후 실제 새 완료 1m 봉, 양수 1m/5m return, watch 가격 초과·freshness·정상 WS가 필요하다. NULL이면 진입하지 않으며 RVOL/OR/HOD는 불변 evidence로만 저장한다. Signal은 실제 평가 시각, OPEN은 그보다 늦은 첫 유효 FE 수신가격이다. 30초 snapshot을 tick proxy로 쓰지 않는다.
- 동시 OPEN 1개·ET 일일 4회·동일 ticker 재진입 금지. +15%/-10% crossing 관측가격, 정상 official close의 SESSION_END, gap의 DATA_INTEGRITY_INVALID, 중단/restart의 INTERRUPTED를 구분한다. 재시작은 DB의 count/ticker 차단을 복원하며 미완료 watch/signal도 중단 기록 후 새 관측부터 시작한다.
- 별도 `data/paper_trading/paper_trades.db` / paper schema 2. 동일 시각 복수 가격·exact duplicate도 monotonic observation_id로 보존한다. live 중복 처리/entry/exit 결정 규칙은 유지한다. 사후 horizon은 같은 run/date/ticker에서 target 이후 tolerance(기본 Radar와 같은 30초) 안의 첫 timestamp/arrival 표본이며 표본시각·offset·관측 ID·선택 정책을 DB/CSV에 남긴다. 보간·pre-target 사용 없음, 누락은 NULL/명시적 사유다. 실제 data에 개발용 Paper DB를 생성하지 않았다. 가격은 로컬 수신시각이며 비용·spread·slippage·실제 체결 가능성은 모델링하지 않는다.
- **Actual orders = disabled / not implemented**. NH import/호출 없음. 기존 2026-10-07 clock_jump 세션은 수익성 표본으로 사용하지 않았고 실제 DB replay도 수행하지 않았다. 실행법은 README를 따른다.

## Rotation v2 / post-exit review 현재 상태

- 사용자 요청에 따라 독립 실험 `paper_rotation_v2`를 추가했다. v1 baseline은 변경·삭제하지 않는다. Dashboard Paper 탭에서 Baseline v1과 Rotation v2를 구분하며 v2 기본 OFF, 선택하면 v1도 실행한다. Capacity 모두 강제 OFF다.
- 같은 직렬 시세·평가시각을 두 독립 engine/DB에 전달한다. v2 normal confirmation은 v1을 재사용하고, 새 후보의 양쪽 TOP10·양쪽 rank/완료 momentum 우위·fresh/WS 정상·당일 미거래 및 현재 약화 증거를 모두 요구한다. NULL/누락 rank는 근거 부족으로 전환하지 않는다. 이름은 material evidence dominance이고 상승확률은 없다.
- switch signal보다 늦은 기존 ticker 첫 유효 FE로 exit, exit보다 늦은 새 ticker 첫 유효 FE로 entry다. 같은 관측 재사용 없음. 각 전략의 일일 4회·재진입 금지·OPEN 1개를 별도로 유지한다. gap/invalid/중단/restart는 pending switch를 취소하고 미확인 체결가격은 NULL이다.
- 별도 `data/paper_trading/paper_rotation_v2/review.db`에 v2 거래·전환 근거·v1 복사본·post-exit 집계와 실제 tick을 저장한다. 두 DB의 공통 Paper evidence는 schema 2이며 rotation extension은 1이다. marks/horizons CSV를 sidecar export에도 추가한다. 원래 entry/TP/SL, extrema/return, TP/SL hit+시각, TP 추가 상승/반락, SL 회복 및 evidence tag를 제공한다. official close까지 기존 보호 입력을 연장한다. 조기중지/공백은 INCOMPLETE이고 SESSION_END의 이후 창은 NO_POST_EXIT_WINDOW다. 추적 결과는 판단 입력이 아니다.
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

- 가족용 인증 파일 부재 등 Collector 생성 전 실패에서 첫 오류는 유지되지만 기존 공통 finalizer가 `finalization_AttributeError`를 부가 기록한다. 격리 실행에서 research DB0, shutdown_eligible=false/BLOCKED와 지속 오류 화면을 확인했다. 원인 코드 전달을 보존하며 공통 진단 정리는 후속 검토 대상이다.

- 현재 실제 첫 1시간 실행의 Paper/Opening DB가 존재하며 이번 분석은 복사본만 사용했다. legacy Paper schema 1은 원본 DB/WAL 복사 검사 후 거부하며 자동 migration하지 않는다. 사용 전 별도 복사본의 offline migration이 필요하고 과거 동일시각 유실 틱은 복원할 수 없다. 첫 live에서는 schema 2 marks/horizon과 Opening all/eligible coverage·표본 수·metadata를 확인해야 한다.

- controller metadata 교체의 일시적 Windows 접근 거부는 25·50·100ms의 제한된 재시도로 복구한다. 영구 거부·임시 파일 작성 실패·기타 I/O 실패는 감추지 않고 파일명/작업 단계/OS 번호를 표시한다. 외부 점유 프로세스는 미확정이며 장기 실운영의 무오류를 보장하지 않는다. scheduling/Capacity 정책과 허용 오차는 변경하지 않았다.

- RVOL 0% 세션은 RVOL 조건 연구 근거가 되지 않는다. stale/결측과 Validator 경고를 분석별로 고려한다. shared cache coverage 축적·다른 시장 상태·장기 CPU/UI/연결 안정성·새 실제 자동 검증 종료 흐름은 추가 관측이 필요하다.
- feed 범위/분봉 라벨/무체결 불확실성/unknown 종목 유형/NULL VWAP/EOD provisional 제약을 유지한다. 자동 D+n 수집·주간 재arm·Windows 깨우기·같은 입력의 다중 슬롯 replay는 미구현이다.

## Next Work

이번 감독 시험 대상은 **Market Radar 자동 수집 v2.6.10**가 가리키는 FINAL_20261101_R3 배포본이다. 중간 빌드나 이전 아이콘과 구분한다.

첫 감독 실행 후 audit/postrun_audit.txt의 연구·보존·전원 판정과 최초 원인·근거를 확인한다. 비공개 목적지가 결정되기 전 외부 전송/AI는 OFF이며 비용·한도·최소권한 검증 전 실제 실행기를 연결하지 않는다.

먼저 사용자가 기존 Stock Helper/Collector를 정상 종료하고 모든 앱의 작업을 저장한다. 새 가족용 EXE/설정 경로와 버전을 확인하고 첫 실장은 `--dry-run`으로 감독한다. ARMED는 예약이며 Kiwoom 실제 연결은 별도 확인한다. 65분 planned stop, Paper/Rotation/Opening, 진단 ZIP·보존 verdict, 두 취소 경로를 검토한다. 정상 자동 종료와 실제90분 비상 종료는 각각 독립적인 사용자 실기 시험이며 기존30초 성공을 전체 검증으로 대체하지 않는다. 기본 비상 강제 종료는 계속 OFF다.

실장 Paper 지연·sticky WS gap/Fresh·Opening cache 부족은 미해결 제약이다. 최초 실패 전 SQL read/write/commit, queue 처리율과 전체 처리 p95/p99/max·host CPU/디스크를 같은 시간축으로 비교하되 FIFO·2초·전략·품질 기준을 완화하지 않는다. 과거 FAILED 데이터를 승격하지 않는다.

장기 연구는 동일 정책의 여러 거래일 full-session/Paper/Rotation/Opening evidence 축적 → Validator/coverage/누락·표본 검토 → 첫1주 descriptive 분석 → 다른 기간 재검증 → 기존 가격구조와 독립·결합 비교 순서다. 가족용65분은 full-session의 대체 검증이 아니다. NH 계좌 선택/RC 권한은 별도이며 실제 주문은 추가하지 않는다. 출처 미검증 연구는 RESEARCH_BASIS 표시를 유지한다.

## Git 상태와 작업 보존

시작 main clean, 로컬/실제 원격 main d075a9e834cef8a38870387477512642e06ae02c 일치. 사용자의 이번 요청에 따라 검증한 소스·테스트·기술 문서만 명시적 staging/일반 commit/push로 반영한다. 최종 SHA·원격 일치와 보존 해시는 Git 제외 단일 CODEX_LAST_TASK_REPORT.md 및 build/audit_v2610_20261101에 기록한다. EXE manifest의 source_base_head는 빌드 전 기준, source_sha256은 검증한 소스를 식별한다. 현재 커밋은 git log -1, 변경 상태는 git status --short로 확인한다. 실제 .env/data/DB/cache/WAL/SHM/ZIP/log/build/dist/EXE/거래일 감사 출력·개발 보고서는 Git 제외. force/reset/clean/stash/rebase/merge 사용 없음.
