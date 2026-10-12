# Stock Helper / Market Radar

## 프로젝트 방향 기준서 · 최신본

작성 기준: 2026-10-12 · 장기 방향 문서

## 문서 성격

이 문서는 버전별 개발 로그가 아니라 프로젝트가 왜 존재하고, 어떤 기준으로 기능을 선택·검증하며, 최종적으로 어떤 제품을 만들 것인지 고정하는 최상위 방향 기준서다. 구현 세부와 현재 버전은 CODEX_HANDOFF, 과거 변경은 CHANGELOG, 연구 출처는 RESEARCH_BASIS, 실행 기준은 TEST_PROTOCOL에서 관리한다.

새로운 ChatGPT 대화나 Codex 작업은 이 문서의 목표와 검증 순서를 먼저 확인한다. 기능을 추가하기 전에 후보 탐색, 데이터 품질, 전략 검증, 개별 종목 분석 중 무엇을 개선하는지와 기존 방향에 충돌하지 않는지를 확인한다.

## 1. 프로젝트가 해결하려는 문제

이 프로젝트의 출발점은 주가 데이터를 많이 보여주는 프로그램을 만드는 것이 아니다. 미국 단기매매, 특히 급등·모멘텀 종목을 볼 때 가장 먼저 생기는 문제는 “수많은 종목 중 지금 확인할 가치가 있는 종목이 무엇인가?”이다.

기존 Stock Helper는 사용자가 이미 보고 있는 종목의 OCR, 종목코드, 매입가·반등가·벽·관문 등 가격 구조를 정리한다. Market Radar는 시장 전체에서 후보를 찾고, Result Tracking과 연구 모듈은 포착 이후의 실제 움직임을 검증한다.

따라서 프로젝트는 후보 탐색, 데이터 품질 검증, 가상 전략 검증, 개별 종목 가격 구조 분석을 분리하고, 충분한 표본이 쌓인 뒤에만 서로 결합한다.

## 2. 최종 제품 목표

최종 목표는 단순한 급등주 스캐너나 자동매매기가 아니다. 미국 급등·모멘텀 시장에서 지금 볼 후보를 좁히고, 포착 이유와 데이터 품질, 과거 유사조건의 실제 결과, 기존 Stock Helper의 가격 구조를 함께 보여주는 데이터 기반 단기매매 의사결정 보조 프로그램을 만든다.

시스템은 후보를 찾고, 데이터를 수집·검증하고, 가상 전략을 통해 진입·청산 규칙을 검토하고, 사용자가 확인할 근거를 제공한다. 실제 계좌 주문과 자동매매는 현재 기본 목표가 아니며, 향후 검증이 충분히 끝난 뒤 별도의 안전 단계로만 검토한다.

## 3. 전체 구조: 탐색 → 품질 검증 → 연구 → 가상 검증 → 의사결정 보조

1. Market Radar: 미국 시장에서 지금 볼 가치가 있는 후보를 찾는다.
2. Session Validator: 오늘 수집된 데이터가 연구에 사용할 수 있는 품질인지 확인한다.
3. Research / Master DB: 포착 당시 조건과 이후 결과를 여러 거래일에 걸쳐 축적한다.
4. Strategy Analysis: 어떤 조건이 실제로 더 좋은 후속 성과와 연결됐는지 분석한다.
5. Paper Trading v1 / Rotation v2: 실제 주문 없이 진입·청산·교체 규칙을 동일 데이터에서 가상 검증한다.
6. Post-exit Tracking: 손절·익절·교체 이후에도 공식 종가까지 경로를 추적해 규칙의 한계를 확인한다.
7. Opening Hour Research: 개장 후 첫 60분의 가격·거래량 경로를 별도 연구 데이터셋으로 보존한다.
8. Stock Helper: 선택된 종목의 매입가·반등가·벽·관문 등 가격 구조를 분석한다.
9. Execution / Account Layer: NH API는 현재 계좌·잔고·주문가능수량을 read-only로 확인하며 향후 실행 계층 후보로 분리한다.
10. Live Decision Support: 충분히 검증된 조건만 실시간 후보 우선순위와 판단 근거에 반영한다.

각 단계는 먼저 독립적으로 검증한다. 후보 탐색 순위, 가상 전략, 기존 가격 레벨을 처음부터 하나의 임의 점수로 합치지 않는다.

## 4. 연구와 외부 자료를 사용하는 방식

초기 방향을 잡을 때 논문, Day Trading 연구, 실전 자료에서 Stocks in Play, Relative Volume, Opening Range, Intraday Momentum, HOD, VWAP, Order Flow / OFI 같은 개념을 검토했다. 이 자료는 특정 전략을 그대로 복사하기 위한 것이 아니라 무엇을 측정하고 어떤 가설을 검증할 것인지 정하기 위한 출발점이다.

- Stocks in Play: 당일 시장의 관심이 집중된 종목을 어떻게 정의할 것인가.
- Relative Volume: 평소 같은 장중 시각보다 거래가 얼마나 비정상적으로 집중되는가.
- Intraday Momentum: 최근 1분·5분의 가속이 이후 단기 움직임과 어떤 관계가 있는가.
- Opening Range / HOD: 장 초반 가격 발견과 당일 고점·저점 구조가 이후 움직임과 연결되는가.
- VWAP: 정확한 데이터 정의와 세션 범위를 확인할 수 있을 때만 사용한다.
- Order Flow / OFI: 재현 가능한 호가·체결 데이터가 확보될 때만 후속 연구로 검토한다.

외부 연구는 정답이 아니라 가설의 출발점이다. 최종 판단은 프로젝트가 직접 수집한 데이터와 별도 기간의 재검증 결과로 한다.

## 5. 후보 탐색의 두 축

### 5.1 Current Leaders

현재 시장에서 이미 강한 종목을 찾는다. 기본 축은 당일 등락률이며 “지금 시장의 대장은 무엇인가?”에 답한다.

### 5.2 Emerging Candidates

아직 당일 상승률 최상위가 아니더라도 최근 몇 분 사이에 움직이기 시작한 종목을 찾는다. 기본 축은 완료된 1분봉 기반 최근 5분 수익률이며 “아직 대장은 아니지만 지금 새로 움직이는 종목은 무엇인가?”에 답한다.

두 축을 분리하는 것은 핵심 방향이다. 단순 상승률 TOP 종목만 보여주는 스캐너로 되돌아가지 않는다.

## 6. 지표와 데이터의 원칙

목표는 지표를 많이 넣는 것이 아니라 후보 탐색과 검증에 필요한 값을 정확하게 사용하는 것이다.

- Day Change: 현재 강세를 보기 위한 기본 축.
- 1분 / 5분 Return: 최근 가속과 단기 모멘텀.
- RVOL: 동일 장중 시각의 과거 거래량 대비 현재 거래 집중도.
- Opening Range: 장 초반 가격 발견 범위.
- HOD / LOD: 당일 고점·저점과 현재 위치의 관계.
- MFE / MAE: 진입 또는 관측 기준 이후 최대 유리·불리 움직임.
- VWAP / OFI: 현재 데이터 계약으로 정확히 재현할 수 있을 때만 구현.

핵심 데이터 원칙은 “틀린 숫자보다 NULL이 낫다”이다. 누락 분을 거래량 0으로 만들지 않고, 보간값이나 근사 거래대금을 실제값처럼 표시하지 않는다. 동일 시각에 여러 가격이 들어오면 도착 순서와 개별 관측을 보존하고, 미래 데이터를 과거 판단에 사용하지 않는다.

## 7. 표준화된 데이터 수집이 전략보다 먼저다

서로 다른 날의 결과를 비교하려면 입력 데이터의 시간 기준과 수집 조건이 일관되어야 한다. 전략 성과를 보기 전에 수집 프로토콜을 먼저 고정한다.

- XNYS 공식 정규장 기준으로 수집하며 premarket과 regular research data를 분리한다.
- 장 전에 인증·shared cache·historical backfill을 준비할 수 있지만 후보 순위·FIRST_DETECTED·snapshot·결과 타이머는 공식 open 이후에만 시작한다.
- Capacity Test와 Regular Session의 목적과 종료 기준을 구분한다.
- protocol compliance, 실제 시작·종료 지연, 오류와 누락 사유를 저장한다.
- shared historical cache를 재사용하되 원본 session DB·export·log는 실행별 evidence로 보존한다.

목적은 좋은 결과를 만드는 것이 아니라 비교 가능한 데이터를 만드는 것이다.

## 8. Session Validator의 역할

Session Validator는 오늘 데이터가 연구에 사용할 수 있을 만큼 정상적으로 수집됐는지 검사한다. 매매 신호 생성기가 아니다.

- Protocol: 정규장 기준, 시작·종료 시각, premarket contamination 여부.
- Integrity: SQLite 무결성, DB와 export row count, metadata/quality 존재 여부.
- Capacity: active/protected/discovery/saturation/slot unavailable.
- Data Quality: RVOL·return 가용률, stale, WS gap, REST/worker/DB/export 오류.
- Result Tracking: expected horizon, complete/missing/pending/outside_session, missing reason, sample audit.

PASS는 전략이 수익성이 있다는 뜻이 아니다. WARN은 분석별 품질 제약을 반영해야 한다는 뜻이고, FAIL은 표준 비교에서 제외해야 할 중대한 문제가 있다는 뜻이다.

## 9. 후보는 반드시 사후 결과로 검증한다

Radar는 지금 좋아 보이는 종목을 표시하는 것으로 끝나지 않는다. 최초 포착 또는 실제 가상 진입 시점을 기준으로 이후 결과를 기록해 후보 선정과 진입 규칙을 검증한다.

- +30초 / +1분 / +3분 / +5분 / +10분
- +30분 / +60분
- EOD / D+1 / D+2 / D+3
- MFE / MAE, 표본 선택 시각, 누락 이유, 데이터 품질

정확한 관측이 없으면 결과를 만들지 않는다. 목표 시각의 결과를 사용할 때는 실제 선택한 관측시각과 목표 대비 offset을 함께 보존한다.

## 10. Paper Trading v1의 역할

Paper Trading은 수익성이 입증된 전략이 아니라 실시간 데이터 흐름과 진입·청산 규칙을 검증하는 experimental baseline이다. 실제 주문은 하지 않는다.

- 동시에 한 종목만 보유하고 전략별 ET 하루 최대 4회 진입한다.
- 같은 ticker는 같은 거래일에 재진입하지 않는다.
- WATCHING 이후 새 완료 1분봉, 양수 1분·5분 momentum, watch 가격 초과, fresh price, 정상 WS를 요구한다.
- 신호보다 늦게 들어온 첫 유효 Kiwoom live 가격을 가상 진입가로 사용한다.
- +15% 익절 / -10% 손절은 baseline이며 처음 threshold를 넘은 실제 관측가격으로 종료한다.
- entry evidence와 사람이 읽을 수 있는 결정론적 이유를 함께 저장한다.

거래비용, spread, slippage, 실제 체결 가능성은 별도 연구 대상이다. 첫 표본을 보고 즉시 규칙을 바꾸지 않는다.

## 11. Rotation v2와 Post-exit Tracking

Rotation v2는 baseline을 대체하지 않는 별도 shadow 전략이다. 현재 종목보다 새 후보가 조금 더 좋아 보인다는 이유로 교체하지 않는다.

- 새 후보의 Current·Emerging 양쪽 TOP10, 양쪽 rank 우위, 1분·5분 momentum 우위, 정상 진입 조건을 함께 확인한다.
- 현재 종목에는 TOP10 이탈 또는 비양수 momentum 같은 약화 근거가 있어야 한다.
- switch signal보다 늦은 기존 종목 첫 유효가격으로 종료하고, 그보다 늦은 새 종목 첫 유효가격으로 진입한다.
- v1과 v2를 같은 데이터에서 독립 실행해 갈아타지 않은 경우와 갈아탄 경우를 비교한다.

손절·익절·교체 뒤에도 공식 close까지 경로를 추적한다. 손절 뒤 진입가·원래 목표가 회복, 익절 뒤 추가 상승·반락, 교체 뒤 기존 종목의 움직임을 기록하되 사후 데이터는 live 판단에 사용하지 않는다.

## 12. Opening Hour Research의 역할

Opening Hour Research는 개장 후 첫 60분의 가격·거래량 경로를 넓게 보존하는 연구 전용 계층이다. 매수 신호나 예측 모델이 아니다.

- 09:30~10:29 ET의 완료 1분봉을 기준으로 3분봉·5분봉을 파생한다.
- OHLCV, body/wick, close location, 누적 거래량, HH/HL/LH/LL, HOD/LOD, opening range 돌파, pullback/rebound를 기록한다.
- +3/5/10/15/30/45/60분 snapshot과 개장·후보 포착 기준 MFE/MAE를 저장한다.
- 늦게 발견된 종목의 이전 봉은 pre_detection으로 표시하고 live 판단에 사용하지 않는다.
- 패턴 이름과 BUY 규칙을 먼저 만들지 않고 여러 거래일의 원자료를 쌓은 뒤 비슷한 경로를 분류한다.

비보통주 등 기존 정책에서 제외된 상품도 원본 evidence에는 남길 수 있지만, 전략 연구 통계에서는 포함 여부와 표본 범위를 명시적으로 분리한다.

## 13. Kiwoom과 NH API의 역할 분리

데이터 소스와 실행 계층을 한 역할로 섞지 않는다.

- Kiwoom: 시장 전체 후보 탐색, 실시간 가격, 분봉·후보·연구 데이터 수집.
- NH Namuh PLUG: 현재 read-only 계좌목록·해외잔고·주문가능수량·현재가 검증.
- Paper Trading: 현재 Kiwoom live 가격으로만 동작하며 NH 주문 경로를 호출하지 않는다.
- 실제 주문은 Paper/Rotation과 별도 모듈·명시적 활성화·소액 검증·강제 안전장치가 준비된 뒤에만 검토한다.

NH 실시간 해외주식 시세 권한 문제는 실제 주문·계좌 기능과 분리해 관리한다. 자격증명·token·전체 계좌번호는 로그·DB·Git에 저장하지 않는다.

## 14. 테스트를 계속하는 이유

초기 반복 테스트는 수익성을 증명하기 위한 것이 아니라 데이터 연결, 저장, 종료, cache, protocol, Validator, shadow state machine이 정확하고 일관되게 동작하는지 확인하기 위한 것이다.

인프라가 안정된 뒤에는 전략 연구로 중심이 이동한다. 그때부터는 어떤 후보 조건, 진입 조건, opening-hour 구조가 이후 +5분/+30분/+60분 성과와 관계가 있는지를 분석한다. 인프라 검증과 전략 검증을 섞지 않는다.

## 15. 개발·연구 단계

- Phase A - 수집 인프라 정확성 검증: 연결, 저장, 종료, cache, protocol, Validator.
- Phase B - 표준화된 실데이터와 live shadow evidence 축적: 동일 정책으로 여러 거래일을 기록.
- Phase C - descriptive 분석: 후보·진입·청산·post-exit·opening-hour의 표본 수, 평균·중앙값, MFE/MAE, 누락률 비교.
- Phase D - 유망 조건 도출: source·momentum·RVOL·HOD·OR·가격/거래량 경로 조합을 후보화.
- Phase E - 다른 기간·시장 상태에서 재검증: 한 주 결과에 맞춘 과적합 방지.
- Phase F - 기존 벽·매입가·반등가 분석과 독립 비교.
- Phase G - 검증된 조합만 Candidate Rank와 실시간 근거에 반영.
- Phase H - 최종 GUI 의사결정 보조: 후보 발견부터 가격구조·검증 근거 확인까지 연결.

## 16. 1주 데이터의 의미

1주 수집은 최종 전략 확정이 아니라 첫 분석 표본이다. 표본 수와 시장 상태를 보고 2주·4주 이상 추가 검증할 수 있다.

Current/Emerging, CHG/SPIKE/VOL/VALUE, momentum, RVOL, HOD, Opening Range, Paper v1, Rotation v2, Opening Hour 구조를 descriptive하게 비교한다. 충분한 표본과 별도 기간의 재검증 전에는 필터·가중치·TP/SL·교체 조건을 변경하지 않는다.

## 17. 기존 Stock Helper와의 관계

기존 OCR, 종목코드 자동 인식, 매입가, 반등가, 일봉·분봉 벽, 관문, 끝판왕, 태초마을, 알림·추적 기능은 Market Radar와 shadow 연구 때문에 버리거나 임의 변경하지 않는다.

장기적으로 Radar가 어떤 종목을 볼지 좁히고, Stock Helper가 그 종목의 가격 구조를 분석한다. 충분한 표본이 쌓이면 Radar 조건, 기존 가격 레벨, 두 조건의 결합 효과를 독립적으로 비교한다.

## 18. 향후 실시간 후보 우선순위

충분한 실데이터와 재검증이 끝난 뒤에는 진입 후보 순위, 관심 우선순위 또는 Trade Candidate Rank를 표시할 수 있다. 단순 상승률이나 임의 BUY 점수로 만들지 않는다.

- 왜 포착됐는지와 현재 충족 조건.
- 과거 유사조건 표본 수와 +5분/+30분/+60분 결과 분포.
- MFE/MAE, 실패·누락·데이터 품질 비율.
- Paper v1과 Rotation v2의 비교 결과와 post-exit 경로.
- Opening Hour 구조와 기존 Stock Helper 가격 레벨.

추천이나 진입 검토라는 표현은 우선 검토할 종목이라는 뜻이며 미래 상승 보장을 의미하지 않는다.

## 19. 먼저 데이터, 나중에 필터·점수

1. 외부 연구와 실전 자료에서 검증할 가설을 선정한다.
2. 현재 데이터 소스에서 정확히 측정 가능한지 확인한다.
3. 표준화된 원시 데이터를 수집하고 후보·가상거래·첫 60분 경로를 기록한다.
4. Validator와 각 연구 quality로 데이터 사용 가능 범위를 확인한다.
5. 실제 이후 결과와 post-exit 경로를 기록한다.
6. 충분한 표본이 쌓인 뒤 조건별 결과를 비교한다.
7. 다른 기간에서 재검증한 뒤 필터·우선순위·결합 조건을 개선한다.

점수를 먼저 만들고 데이터를 그 점수에 맞추지 않는다.

## 20. 현재 범위에서 하지 않는 것

- 실제 자동 매수·매도와 계좌 주문을 기본 운영 목표로 두지 않는다.
- 검증되지 않은 상승확률·추천등급·별점·Strong Buy를 만들지 않는다.
- 세력·주포·매집처럼 데이터로 검증할 수 없는 의도를 판단 근거로 사용하지 않는다.
- 불확실한 지표를 임의 계산하거나 결측을 보간해 실제값처럼 사용하지 않는다.
- Session Validator PASS/WARN/FAIL을 투자 신호로 사용하지 않는다.
- Paper·Rotation·Opening Hour의 첫날 또는 한 주 결과만으로 규칙을 확정하지 않는다.

## 21. 최종 사용자 경험

최종 형태는 Stock Helper GUI 안에서 후보 탐색, 세션 품질, 가상 전략 상태, post-exit 결과, Opening Hour 연구, 기존 가격 구조를 자연스럽게 연결하는 데스크톱 프로그램이다.

- 장 전 실행 → 정규장 자동 시작 → 공식 close 자동 종료·검증.
- Current Leaders / Emerging Candidates와 데이터 품질 확인.
- Paper v1 / Rotation v2의 가상 진입·청산·교체 이유와 실시간 상태 확인.
- 종료 후 Market Radar, Paper/Rotation, Opening Hour export 생성과 재분석.
- 향후 Review ZIP·메일 보고·안전 종료 같은 운영 자동화는 전략 판단과 분리해 추가할 수 있다.
- 후보 선택 후 기존 Stock Helper의 매입가·반등가·벽 분석으로 연결.

## 22. 앞으로 기능을 선택하는 기준

새 기능은 후보 탐색의 정확성, 개별 종목 분석, 사후 검증, 데이터 품질, 운영 안정성 중 무엇을 개선하는지 먼저 확인한다.

- 왜 필요한가?
- 연구나 실전 근거가 있는가?
- 현재 데이터 소스에서 정확히 얻을 수 있는가?
- 어떤 사후 결과로 유효성을 검증할 것인가?
- live hot path와 기존 기능을 회귀시키지 않는가?
- PROJECT_DIRECTION과 충돌하지 않는가?

## 23. 문서 관리와 새 대화 연속성

- PROJECT_DIRECTION.docx: 사람이 보는 최상위 방향 원본. 방향이 바뀌지 않는 한 자주 수정하지 않는다.
- PROJECT_DIRECTION.md: 동일 방향의 machine-readable mirror.
- START_HERE.md: 새 ChatGPT/Codex 세션의 읽는 순서.
- RESEARCH_BASIS.md: 연구·실전 자료와 지표 선택 근거.
- TEST_PROTOCOL.md: 표준 수집·검증·shadow/연구 운영 기준.
- CHANGELOG.md: 과거 버전별 변경과 검증 기록.
- CODEX_HANDOFF.md: 현재 코드 상태, 검증 결과, 제약, 다음 작업.
- README.md / market_radar/README.md: 실행법과 현재 기능·기술 상세.
- AGENTS.md: Codex가 매 작업에서 지켜야 할 영구 규칙.

방향은 PROJECT_DIRECTION, 과거 이력은 CHANGELOG, 현재 상태는 HANDOFF로 분리한다.

## 24. 현재 위치와 다음 큰 단계

현재는 수집 인프라와 150-slot 운영 후보, Validator, Paper v1, Rotation v2, Post-exit Tracking, Opening Hour Research, NH read-only 계층의 오프라인 구현을 마치고 Phase B의 실제 Regular Session evidence 축적으로 넘어가는 단계다.

다만 Paper/Rotation/Opening Hour를 동시에 사용한 첫 live shadow는 아직 검증되지 않았다. 구현 완료와 전략 유효성을 구분한다.

1. 데이터 보존 감사에서 확인된 same-timestamp 관측 순서, horizon sample 정책, 연구 ID의 경로 독립성 등 연구 품질 항목을 정리한다.
2. Regular Session을 개장 전에 arm하고 Paper v1·Rotation v2·Opening Hour Research를 명시적으로 활성화해 공식 close까지 첫 통합 세션을 수집한다.
3. Session Validator, Paper/Rotation export, post-exit 품질, Opening Hour coverage를 함께 검토한다.
4. 동일 설정으로 여러 거래일을 축적하고 최소 첫 1주를 descriptive하게 분석한다.
5. 유망 조건을 다른 기간에서 재검증한 뒤 기존 가격 구조와 독립·결합 비교한다.
6. 검증된 근거만 후보 우선순위와 최종 GUI 의사결정 보조에 반영한다.

## 핵심 원칙

연구에서 가설을 얻고 → 정확하고 비교 가능한 데이터만 수집하고 → 세션과 연구 품질을 검증하고 → 실제 결과와 가상 전략·사후 경로로 조건을 재검증한 뒤 → 검증된 기능만 실시간 의사결정 보조에 반영한다.
