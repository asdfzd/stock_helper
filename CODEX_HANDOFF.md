# CODEX HANDOFF

갱신: 2026-10-07 KST. 현재 상태만 유지하며 영구 이력은 [CHANGELOG](CHANGELOG.md)에 둔다. 새 대화는 [START_HERE](START_HERE.md), 최상위 방향 원본은 [PROJECT_DIRECTION.docx](PROJECT_DIRECTION.docx), 도구용 mirror는 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md), 수집 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md)을 따른다.

## 현재 버전과 연구 단계

- **Market Radar v2.3.0 / Validator 1 / Collector v2.1 / test_version=kiwoom_radar_v2_1 / research schema 4 / Master schema 1 / Windows Quick Launch v1**.
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
- 현재 DOCX SHA256: a92ad593d07d54475268c449374d0bbf1581cc55c9a681e74df3c77564e8175b. 이번 작업은 텍스트 의미 검수이며 DOCX 페이지 편집·렌더 작업이 아니다.
- RESEARCH_BASIS의 7개 연구 축은 DOCX와 일치한다. 정확한 citation은 **출처 검증 필요**이며 논문명·저자·URL을 만들지 않았다.
- 저장된 전체 회귀 결과는 **256 통과 / 실패 suite 0**(data/kiwoom_provider_validation/tests_summary.json). 이전 코드 작업의 검증 기록이며 이번 문서 전용 작업에서 전체 테스트·live smoke·세션 재검증을 실행하지 않았다.
- 이번 검사: DOCX 전후 SHA256, 문서 의미·상대 링크·버전·Next Work, 변경 파일 범위·인증값 패턴·git diff --check, 기존 코드와 .env 보존 및 data 파일 목록/크기/mtime 동일 여부.

## 남은 제약

- RVOL 0% 세션은 RVOL 조건 연구 근거가 되지 않는다. stale/결측과 Validator 경고를 분석별로 고려한다. shared cache coverage 축적·다른 시장 상태·장기 CPU/UI/연결 안정성·새 실제 자동 검증 종료 흐름은 추가 관측이 필요하다.
- feed 범위/분봉 라벨/무체결 불확실성/unknown 종목 유형/NULL VWAP/EOD provisional 제약을 유지한다. 자동 D+n 수집·주간 재arm·Windows 깨우기·같은 입력의 다중 슬롯 replay는 미구현이다.

## Next Work

1. 표준 정규장 수집과 Automatic Session Validator의 실제 운영 안정성을 확인한다. 고정 운영 후보 **150 slots**, 동일 정책/shared cache로 Regular Session을 매 거래일 preopen arm해 official close까지 수집한다. CONFIG/default를 자동 변경하지 않는다.
2. 종료 후 validation과 coverage/stale/missing/포화·미배정을 확인한다. FAIL은 표준 비교에서 제외하고 WARN은 분석별 제약을 반영한다. 필요하면 추가 Capacity 실험을 검토한다.
3. 여러 거래일을 축적한 뒤 Current/Emerging·source·momentum·RVOL·HOD·OR별 +30s~D+3 결과를 가능한 범위에서 descriptive하게 분석한다. 1주는 첫 표본이며 필요하면 2주/4주 이상 검증한다.
4. 이후 유망 조건을 다른 기간·시장 상태에서 재검증하고 기존 벽/매입가/반등가와 독립 비교 후 결합 효과를 검증한다. 충분히 검증된 근거만 Candidate Rank/최종 GUI에 반영한다. 연구 citation은 원문 확인 후 보강한다.

## Git 상태와 작업 보존

사용자의 명시적 GitHub push 요청에 따라 **main → origin/main**에 Validator·UI·테스트·Windows launcher·방향 문서 등 25개 파일을 함께 커밋·푸시하는 범위다. 원격은 https://github.com/asdfzd/stock_helper.git 이며 준비 시 로컬/원격 HEAD는 모두 52e0dec였다. 현재 커밋과 동기화 상태는 `git log -1`, `git status --short --branch`로 확인한다.

게시 준비에서는 HANDOFF의 Git 상태만 갱신하며 기존 Python/launcher/validator 코드와 DOCX는 그대로 보존한다. .env, data/DB/log/export는 Git 대상에서 제외한다. 대상 25개 파일의 인증값 패턴 검사에서 발견 항목은 없고 `git diff --check`를 통과했다. 저장된 회귀 결과 256 통과/실패 suite 0을 확인했으며 게시 준비를 위해 테스트나 live 수집을 재실행하지 않았다. 사용자 변경 reset/restore/checkout은 하지 않는다.
