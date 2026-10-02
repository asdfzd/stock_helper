# Project Working Rules

문서 역할: [README](README.md)는 프로그램 소개·실행·현재 기능, [Market Radar README](market_radar/README.md)는 기술 상세, [CODEX_HANDOFF](CODEX_HANDOFF.md)는 현재 개발 상태, [CHANGELOG](CHANGELOG.md)는 영구 버전 이력이다. 이 파일은 작업 규칙만 유지한다.

- 작업 시작 시 CODEX_HANDOFF.md와 관련 README를 읽고 현재 Git 상태를 확인한다.
- 기존 stock_helper 기능을 회귀시키지 않고 사용자의 미커밋 변경을 보존한다.
- .env, secrets, token 및 실제 인증값을 출력하거나 수정하지 않는다.
- data/, DB, logs, 실제 export를 Git에 추가하지 않는다.
- *_REPORT.md 같은 단계별 임시 보고서를 만들지 않는다. 프로젝트 상태 전달은 CODEX_HANDOFF.md를 사용한다.
- 의미 있는 버전 변경은 CHANGELOG.md에 영구 기록하고 README의 Current Version과 관련 기술 문서를 갱신한다.
- 프로그램 버전과 schema version을 별도로 표시한다(예: Market Radar v2.1 / research schema 4).
- 작업 완료 시 CODEX_HANDOFF.md의 현재 상태·검증·다음 작업·Git 상태를 갱신한다.
- HANDOFF에 과거 버전 기록을 계속 누적하지 않는다. 중요한 검증 사실과 기존 history는 CHANGELOG.md에 보존한 뒤 옮긴다.
- 테스트 결과를 과장하지 않는다. 과거 기록·mock·replay·실제 smoke를 구분하고 실행하지 않은 smoke를 실행했다고 쓰지 않는다.
- 주문/계좌 기능과 Current Leaders / Emerging ranking 공식은 사용자의 명시적 요청 없이는 추가·변경하지 않는다.
- 사용자가 명시적으로 요청하지 않으면 git add / commit / push 하지 않는다.
- 코드 변경 작업이면 관련 회귀 테스트를 실행한다. 문서 전용 작업에서는 불필요한 전체 테스트나 live smoke를 실행하지 않는다.
- 문서 작업 후 git diff --check, 변경 파일 범위, 현재 버전 일치, 상대 링크, 비밀정보 미포함을 확인한다.
