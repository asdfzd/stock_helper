# Stock Helper 프로젝트 방향 기준서

Stock Helper의 최종 목표는 실제 데이터를 축적하고 검증하여 사용자의 단기매매 판단에 필요한 근거를 제공하는 데이터 기반 의사결정 보조 프로그램이다. 이 문서는 그 목표와 연구 단계, 기능 추가의 판단 원칙을 유지한다. 현재 구현 버전과 작업 진행 상황은 CODEX_HANDOFF.md에 기록한다.

최상위 방향 원본(source of truth)은 사용자가 교체한 [PROJECT_DIRECTION.docx](PROJECT_DIRECTION.docx)(작성 기준 2026-10-07)다. 이 MD는 그 의미를 반영하는 machine-readable mirror이며, 차이가 있으면 DOCX를 기준으로 MD를 보완한다. MD 동기화를 위해 DOCX를 재생성하거나 덮어쓰지 않는다.

## 1 최종 제품 목표

시장 후보 탐색 → Stock Helper 가격구조 분석 → Result Tracking → 실제 데이터 검증 → 검증된 조건만 실시간 판단 보조에 사용하는 흐름을 유지한다. 시장 스캐너는 이 전체 과정의 첫 단계다.

충분한 실데이터 검증 후에는 실시간 시장 후보에 검증된 조건을 적용하고, 기존 벽·매입가·반등가 분석과 연결하여 사용자가 매매 결정을 내릴 근거를 제공한다. 사용자가 최종 판단하며 자동주문과 계좌 주문 기능은 현재 기본 목표가 아니다.

출발점은 미국 급등·모멘텀 시장의 수많은 종목 중 지금 볼 종목을 좁히는 것이다. Market Radar는 시장 후보 탐색, Stock Helper는 선택한 종목의 가격구조 분석, Result Tracking은 최초 포착 이후의 실제 움직임 검증을 담당한다.

## 2 연구 단계와 근거 수준

- Phase A: 수집 인프라의 정확성 검증. 일정·연결·저장·결과 추적·품질 판정이 의도대로 동작하는지 확인한다.
- Phase B: 표준화된 실데이터 축적. 같은 수집 정책으로 여러 거래일을 기록하고 비교 가능한 세션을 구분한다.
- Phase C: 조건별 descriptive 분석. 표본 수, 평균·중앙값, 결과 분포, MFE/MAE, 결측과 시장 상태를 비교한다.
- Phase D: 유망 조건 도출. source·momentum·RVOL·HOD·Opening Range 조합을 후보화하고 검증할 가설을 정한다.
- Phase E: 다른 기간과 시장 상태에서 재검증. 탐색에 사용하지 않은 기간에서도 조건의 재현성을 확인한다.
- Phase F: 기존 벽·매입가·반등가 분석과 독립 비교. 각각의 정보와 조합이 추가 근거를 제공하는지 평가한다.
- Phase G: 검증된 조합을 실시간 Candidate Rank에 반영. 검증 범위와 데이터 품질을 함께 표시한다.
- Phase H: 최종 GUI 의사결정 보조. 후보 발견부터 가격구조·성과 근거 확인까지 연결한다.

단계 진행은 구현 완료만으로 결정하지 않는다. 실데이터의 양과 품질, 비교 가능성, 별도 기간에서의 검증 근거를 확인한다. 현재 연구 단계와 다음 작업은 CODEX_HANDOFF.md의 Next Work를 따른다.

## 3 표본과 과적합 방지

1주 수집은 첫 분석 표본이며 최종 전략 확정을 뜻하지 않는다. 표본 수와 시장 상태, 결측 분포에 따라 2주·4주 이상 추가 검증할 수 있다. 한 주의 결과에 맞추어 조건과 점수를 반복 조정하지 않는다.

탐색에 사용한 기간과 재검증 기간을 구분한다. 같은 날 반복된 종목·시점의 관측을 모두 독립 표본처럼 해석하지 않는다. 수익성 판단에는 관측 범위와 체결 가능성, 비용 등 별도 검토가 필요하며 수집 정상 여부만으로 이를 확정하지 않는다.

## 4 표준화된 수집과 비교 가능성

입력 데이터의 비교 가능성과 품질을 확보한 뒤 전략 결과를 해석한다. 같은 XNYS regular session 기준, 동일 collection policy, protocol compliance 기록, premarket 분리, shared historical cache, 원본 evidence 보존, 자동 validation을 핵심 원칙으로 삼는다.

Manual 진단, 늦은 시작, 비준수 세션과 표준 세션을 구분한다. Capacity Test는 슬롯 운영 판단을 위한 증거이며 특정 슬롯 수를 영구 최적값으로 확정하지 않는다. 다른 거래일의 시장과 누적 cache 상태 차이를 함께 고려한다.

공유 cache는 이력을 축적하는 수단이다. 핵심 데이터 원칙은 **“틀린 숫자보다 NULL이 낫다”**이다. 부족한 이력을 완성된 것으로 간주하거나 의미가 공식 확인되지 않은 빈 1분봉을 거래량 0으로 채우지 않는다. 확보되지 않은 지표는 NULL과 원래 사유를 유지하며 정확한 VWAP을 만들 수 없으면 근사값을 실제값처럼 사용하지 않는다. 원본 DB·export·log는 각 실행별 evidence로 보존한다.

개장 전에는 인증·shared historical cache·과거 backfill을 준비할 수 있다. 정규장 후보 순위·FIRST_DETECTED·snapshot·결과 타이머는 공식 open 이후에만 시작한다. Capacity Test와 Weekly Research Collection은 TEST_PROTOCOL의 각 표준 기준으로 실행하고 실제 시작·종료 지연, protocol compliance, 오류와 누락 사유를 기록한다.

## 5 Session Validator의 역할

Session Validator는 오늘 수집 세션이 연구 데이터로 사용할 수 있을 만큼 정상적으로 수행되었는지 확인한다. Protocol·개장 전 오염·시작과 종료·DB와 export 무결성·capacity·데이터 품질·결과 추적의 일관성을 검사한다.

- PASS: 검사 범위에서 연구 사용을 막는 중대한 문제가 확인되지 않음.
- WARN: 사용할 수 있으나 해당 지표와 결과의 결측·품질 제약을 고려해야 함.
- FAIL: 표준 비교 및 연구 표본에서 제외해야 할 중대한 문제가 확인됨.

PASS는 전략 수익성, 매수 적합성, 전체 시장 포착 또는 데이터의 완전성을 보증하지 않는다. WARN도 모든 분석에 동일하게 사용 가능하다는 뜻은 아니다. 예를 들어 RVOL 이력이 없으면 RVOL 조건의 성과 근거로 사용할 수 없다. 검증 실패가 이미 수집된 원본을 삭제하거나 수정해서는 안 된다.

Session Validator는 수집 데이터가 정상인가를 판단한다. 향후 Strategy Research는 어떤 조건이 더 좋은 성과를 냈는가를 연구한다. 향후 Live Decision Support는 검증된 조건을 현재 후보에 어떻게 적용할지를 보여준다. 세 역할의 출력과 근거를 구분한다.

## 6 시스템 사이의 연결

개념적인 흐름은 Market Radar → Session Validator → Research DB / Master DB → Strategy Analysis → Stock Helper → Live Decision Support다. 원본 연구 DB는 수집 중 기록되며 Validator는 종료 후 이를 읽는다. Master DB는 원본과 검증 결과를 찾기 위한 재구축 가능한 index다.

Market Radar의 Current Leaders / Emerging Candidates는 현재 관측 순위다. 향후 검증 결과를 적용하는 Trade Candidate Rank와 구분한다. 기존 순위 공식·RVOL 정책·보호 추적·result sampling·provider와 OCR·벽·Toss·halt 기능을 품질 검증 작업의 부수효과로 변경하지 않는다.

- **Current Leaders**: 당일 등락률을 기본 축으로 이미 강한 종목, 즉 지금 시장의 대장을 찾는다.
- **Emerging Candidates**: 아직 당일 상승률 최상위가 아니어도 최근 움직이기 시작한 종목을 찾는다. 기본 축은 완료된 1분봉 기반 최근 5분 수익률이다.

두 축을 분리하며 단순 상승률 TOP 종목만 보여주는 스캐너로 되돌아가지 않는다. Day Change는 현재 강세, 1분·5분 Return은 최근 가속과 단기 모멘텀, RVOL은 동일 장중 시각의 과거 거래량 대비 집중도, Opening Range는 장 초반 가격 발견 범위, HOD는 당일 고점과 현재 위치의 관계를 본다.

기존 OCR·종목코드 자동 인식·매입가·반등가·일봉/분봉 벽·관문·끝판왕·태초마을·알림·추적을 유지한다. 각 기능을 독립적으로 검증하고, 충분한 표본이 쌓인 뒤 **Radar 조건만 사용한 경우 / 기존 가격 레벨만 사용한 경우 / 두 조건을 결합한 경우**를 비교한다. 근거 없이 처음부터 하나의 점수로 합치지 않는다.

## 7 향후 후보 우선순위와 추천의 의미

데이터가 충분히 검증되면 실시간 탭에 진입 후보 순위, 관심 우선순위 또는 Trade Candidate Rank를 표시할 수 있다. 초기 단계부터 임의 BUY 점수를 만들지 않는다. 반영할 조건과 조합은 별도 기간의 검증을 통과해야 한다.

후보 순위에는 왜 포착되었는지(Current/Emerging/candidate source), 현재 충족하는 조건, 과거 유사조건 표본 수, 과거 +5m·+30m·+60m 결과 분포, MFE/MAE와 실패·누락 비율, 데이터 품질·Validator 상태, 기존 매입가·반등가·벽 등 가격구조를 연결한다. 사용자가 근거를 확인할 수 있어야 하며 불확실성을 순위 숫자로 숨기지 않는다.

향후 UI에서 추천 또는 진입 후보라는 표현을 쓰더라도, 이는 과거 검증 데이터와 현재 조건을 바탕으로 사용자가 우선 검토할 종목이라는 뜻이다. 미래 상승을 보장하는 신호라는 뜻으로 사용하지 않는다. 자동주문은 현재 기본 목표가 아니다.

## 8 연구 기반과 가설

Stocks in Play, Relative Volume, Opening Range, Intraday Momentum, HOD, VWAP, Order Flow / OFI를 연구 개념으로 유지한다. 외부 연구는 가설을 만드는 데 사용하고, 프로젝트의 최종 전략 판단은 자체 수집 결과와 재검증 근거로 한다.

연구 개념의 존재와 현재 지표의 구현·정확성·수익성 검증 여부를 구분한다. 제공되지 않는 거래량·호가·체결 정보로 VWAP나 OFI를 추정해 완성된 지표처럼 표시하지 않는다. 정확한 citation과 확인 수준은 RESEARCH_BASIS.md에 둔다. 출처가 확인되지 않으면 논문명·저자·URL을 만들지 않는다.

## 9 문서 역할과 대화 연속성

- PROJECT_DIRECTION.docx: 사람이 보는 최상위 방향 원본. 사용자 요청 없이 수정하지 않는다.
- PROJECT_DIRECTION.md: DOCX 의미를 반영하는 machine-readable mirror. 불일치 시 DOCX 기준으로 동기화한다.
- START_HERE.md: 새 ChatGPT/Codex 세션의 짧은 진입 안내와 읽는 순서.
- RESEARCH_BASIS.md: 연구 개념·가설·정확한 출처 및 검증 수준.
- TEST_PROTOCOL.md: 수집 및 검증 실행 기준.
- README.md 및 market_radar/README.md: 실행·현재 기능 및 기술 상세.
- CHANGELOG.md: 영구 변경·검증 이력.
- CODEX_HANDOFF.md: 현재 상태·제약·Next Work·Git 상태.
- AGENTS.md: 영구 작업 규칙.

기능 추가 전에 PROJECT_DIRECTION과 현재 HANDOFF의 Next Work가 충돌하지 않는지 확인한다. 최종 목표나 검증 원칙을 바꾸는 요청은 구현 편의와 구분해 명시적으로 기록한다. 이 문서에 매번 변하는 버전 번호와 개발 로그를 누적하지 않는다.

새 ChatGPT/Codex 세션은 [START_HERE](START_HERE.md)에서 안내하는 순서로 읽는다: **PROJECT_DIRECTION.md → CODEX_HANDOFF.md → TEST_PROTOCOL.md → RESEARCH_BASIS.md → 관련 README → 필요 시 CHANGELOG**. 방향은 PROJECT_DIRECTION, 과거 이력은 CHANGELOG, 현재 상태와 다음 작업은 HANDOFF로 분리한다.

## 10 최초 포착 이후의 결과 검증

후보는 처음 포착된 시점을 기준으로 **+30s / +1m / +3m / +5m / +10m / +30m / +60m / EOD / D+1 / D+2 / D+3** 결과를 가능한 범위에서 기록한다. MFE/MAE 등 이후 움직임의 폭도 분석하고, complete/missing/pending/outside_session과 missing reason·sample audit을 구분한다. 그날 좋아 보였다는 기억 대신 후보 선정 조건 자체를 실제 결과로 검증한다.

초기 테스트는 연결·슬롯·REST·저장·종료·shared cache·정규장 gate·protocol·Validator의 정확성을 확인한다. 인프라가 안정되면 조건별 후속 성과를 연구한다. 처음에는 Current/Emerging, CHG/SPIKE/VOL/VALUE source, 1분·5분 momentum, RVOL, HOD, Opening Range를 descriptive하게 비교하고 충분한 표본과 다른 기간의 재검증 후에만 필터·가중치·우선순위를 검토한다. 별점·Strong Buy·상승확률·BUY signal을 먼저 만들고 데이터를 그 점수에 맞추지 않는다.

## 11 최종 사용자 경험과 기능 선택

최종 형태는 기존 Stock Helper GUI 안에서 시장 후보·연구 결과·검증된 우선순위·가격구조를 연결하는 데스크톱 프로그램이다. PowerShell 명령은 개발·검증을 위한 실행 수단이며, 최종 사용 흐름은 장 전 실행 → 정규장 자동 시작 → 자동 종료·세션 검증 → 결과 재사용으로 이어진다. 후보 선택 시 기존 가격구조 분석으로 연결하고 충분한 검증 후에만 유사조건 통계와 후보 우선순위를 제공한다.

새 기능은 후보 탐색의 정확성, 개별 종목 분석, 사후 검증, 데이터 품질 중 무엇을 개선하는지 먼저 확인한다. 필요성, 연구·실전 근거, 현재 데이터 소스에서의 정확한 측정 가능성, 검증할 사후 결과, PROJECT_DIRECTION과의 충돌 여부를 검토한다. 개선하는 것이 없으면 우선순위를 낮춘다.
