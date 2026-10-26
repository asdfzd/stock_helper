# Stock Helper

미국 주식 단기매매 보조 및 시장 모니터링을 위한 개인용 Windows 데스크톱 프로그램입니다. 영웅문 Global 화면의 Tooltip을 캡처·OCR하고, 종목·가격 정보를 Toss 현재가와 결합해 종목 카드에 표시합니다.

최종 목표는 시장 후보 탐색과 사후 검증을 기존 매입가·반등가·벽 분석에 연결하는 **데이터 기반 단기매매 의사결정 보조 프로그램**입니다. 충분한 표본과 다른 기간의 검증 후에만 후보 우선순위를 제공하며, 현재 수집 품질 PASS를 매매 신호로 사용하지 않습니다. 방향의 최상위 원본은 [PROJECT_DIRECTION.docx](PROJECT_DIRECTION.docx), 도구용 mirror는 [PROJECT_DIRECTION.md](PROJECT_DIRECTION.md)입니다.

## 주요 기능

- 영웅문 Global 화면 캡처와 PaddleOCR 기반 Tooltip 인식·검증
- clipboard ticker 자동 인식과 종목 연결
- 매입가 표시·추적
- 반등가 표시·추적
- 벽 분석과 일간 범위의 접촉한 벽 추적
- Toss Open API 현재가 조회
- 거래정지 모니터
- Kiwoom Market Radar를 통한 시장 후보 발견·연구 데이터 수집

## 가족용 원클릭 자동 수집 (v2.6.8)

바탕화면 **Market Radar 자동 수집**은 별도 경량 `MarketRadarFamily.exe --family --config <family_config.json>`를 실행합니다. 기존 Stock Helper와 30초 종료 시험 바로가기는 유지합니다. 새 EXE를 인수 없이 실행하면 수동 Market Radar 화면이며 자동 예약하지 않습니다. OCR/GPU는 가족용 패키지에 포함하지 않습니다. 기존 Collector 하나와 DashboardOneHour의 수집·보존·종료 경로를 사용합니다.

이번 검증·바로가기 대상은 `dist/MarketRadarFamily_v2_6_8_FINAL/MarketRadarFamily.exe`입니다. `_internal`, manifest, family_config.json을 포함한 폴더 전체(약181MB)를 유지하세요. 중간 빌드 `MarketRadarFamily_v2_6_8`, `MarketRadarFamily_v2_6_8_RELEASE` 폴더는 최종 배포 대상이 아닙니다.

- 매 거래일 개장 전에 한 번 실행합니다. 첫65분 / WS150 / Paper v1 / Rotation v2 / Opening ON / 실제 주문 OFF로 자동 예약합니다. `ARMED`는 수집 예약이며 인증 성공 표시가 아닙니다. 준비 단계의 Kiwoom 연결 상태를 따로 확인합니다. 휴장·개장 이후·중복 실행·경로/인증 오류는 거부하며 다음 거래일로 자동 반복하지 않습니다.
- 배포 설정 `family_config.json`은 비밀값 없이 `project_root`, `data_root`, `cache`, `normal_shutdown`, `emergency_shutdown`, `expected_windows_timezone`을 명시합니다. 이 PC는 프로젝트 `.env`, `data`, `data/market_radar_cache/kiwoom_v2.db`를 직접 재사용합니다. 실제 자격증명·5GB 이상 캐시는 EXE로 복사하지 않습니다. 환경변수만으로 Kiwoom 인증을 대체하지 않습니다.
- 가족용 배포의 정상 종료는 허용합니다(`normal_shutdown: true`). 65분 종료와 writer 정리·DB/ZIP 보존 검증 및 기존 UTC/monotonic 개장+70분 gate를 통과한 최초 시점에 기존 Windows 60초 종료 요청을 보냅니다. 연구 FAILED와 보존 VERIFIED는 별개이며 기존 eligibility를 유지합니다.
- **독립 90분 비상 종료는 기본 dry-run**입니다(`emergency_shutdown: false`). 첫 실제 수집을 전원 종료 없이 감독하려면 실행 인수에 `--dry-run`을 추가하면 두 종료 경로 모두 꺼집니다. 이는 API 수집까지 가상으로 바꾸는 옵션이 아닙니다. 실제 종료는 모든 앱의 작업을 저장한 뒤에만 사용합니다.
- 실제 비상 종료를 사용하려면 먼저 감독하에 수집·후처리·취소를 확인한 뒤 사용자가 설정의 `emergency_shutdown`을 `true`로 명시적으로 바꾸고 다음 실행에서 적용합니다. 실행 중인 DB writer/ZIP과 저장 전 작업을 손상시킬 수 있습니다. Codex 작업 중 실제 Windows 종료는 실행하지 않았습니다.

독립 Windows 작업은 공식 XNYS 개장+90분에 실행합니다. 2026-10-27~30은 22:30 KST 개장 → 다음 날00:00 기준이며 DST 종료 후에는 달력으로 계산합니다. 작업 이름은 `MarketRadarFamily_<ET YYYYMMDD>_<collector run_id>`이고 일반 GUI에서는 생성하지 않습니다. 1회 TimeTrigger, UTC offset, 종료 후5분 만료, missed-start OFF, 반복/부팅·로그온 trigger 없음, 현재 로그인 사용자·일반 권한입니다. Windows 자체 중단·정전·로그아웃에서는 실행을 보장하지 않습니다.

화면의 **PC 종료만 취소**는 수집을 계속하며 해당 watchdog도 취소합니다. **무인 모드 / 수집 중지**는 수집과 해당 예약을 취소합니다. 취소 실패/상태 불명은 화면에 남습니다. GUI가 응답하지 않으면 같은 EXE로 `--watchdog-cancel "<watchdog.json 전체 경로>"`를 실행할 수 있습니다. 해당 날짜·run_id 작업만 확인·삭제하며 다른 작업을 일괄 삭제하지 않습니다.

새 결과는 `data/market_radar_family/<ET-date>/attempt_<id>/run_<id>/`, watchdog 상태는 같은 attempt의 `watchdogs/<collector run_id>/watchdog.json`에 있습니다. 최초 오류와 연구/보존 결과는 기존 diagnostics와 ZIP에 보존하고 비상 기록은 별도 상태 파일에 남깁니다. 비상 실행이 정상 ZIP/VERIFIED를 만들어 주지는 않습니다. 상태 기록 실패 시 실제 종료 제출을 차단하므로 디스크/OS 자체 장애까지 보장하는 하드웨어 감시는 아닙니다.

Windows 시간대와 짧은 UTC/monotonic 일관성을 검사하며 절대 UTC/NTP 정확성까지 인증하지 않습니다. 부팅 식별자 읽기 실패·등록 실패는 안전 실패합니다. 실제 Task Scheduler dry-run 통과, 실제 미국장 자동 수집·자동 정상/90분 강제 종료는 미검증입니다. 이전 30초 실제 종료 성공은 사용자 보고이며 전체 경로의 검증을 대체하지 않습니다. [운영·테스트 기준](TEST_PROTOCOL.md), [기술 상세](market_radar/README.md#가족용-진입점과-독립-watchdog-v268)를 참고하세요.

## Market Radar

시장 전체에서 수집한 후보 중 사용자가 살펴볼 종목을 찾는 연구·모니터링 기능입니다. **자동 매수/매도 시스템이 아닙니다.** 실제 표는 유효한 실시간 가격을 확보한 구독 후보 부분집합을 재정렬하며, 시장 전체를 빠짐없이 추적하는 통합 순위는 아닙니다.

| 주요 항목 | 제공 내용 |
|---|---|
| Current Leaders / day change | 전일종가 대비 등락률 내림차순 TOP10 |
| Emerging Candidates / completed 1m·5m return | 완료 1분봉으로 계산한 1분·5분 수익률, 5분 수익률 내림차순 TOP10 |
| RVOL | 과거 14거래일 동일 경과분 대비 정규장 누적 거래량 |
| Opening Range / HOD | 개장 1분·5분 범위, 정규장 고점과 현재가 거리 |
| Candidate source | 등락률·5분 급등·거래량·거래대금 후보의 출처 |
| Protected result tracking | 최초 포착 후보를 최대 intraday 결과 구간까지 우선 보호 |
| 사후 검증 | +30s/+1m/+3m/+5m/+10m/+30m/+60m/EOD/D+n 결과·누락 상태 기록 |

필요한 데이터가 없으면 지표는 NULL로 남깁니다. 누락 분을 거래량 0으로 채우지 않으며 VWAP는 현재 NULL입니다. EOD/D+1~D+3는 일봉 기반 provisional 또는 future pending 상태를 구분합니다.

## 수집 테스트 화면 (v2.6.7)

Market Radar를 열면 **수집 테스트** 탭이 먼저 표시됩니다. Live Radar, Session History, Candidate History, Paper Trading은 그대로이며 기존 세부 설정·Replay·Import·검증 도구는 **수집 상세 설정** 탭에 있습니다.

1. Collection Mode에서 **첫 1시간 (65분)** 또는 **일반 Regular Session**을 직접 선택합니다. 새 GUI 설정의 Max WS 기본은 150이며 기존 저장된 값과 명시적 설정은 유지합니다. 첫 1시간 선택은 기존 Regular/150/Paper/Rotation/Opening 설정을 사용합니다.
2. **PC 자동 종료 없이 테스트**는 기본 체크입니다. 실제 주문은 항상 OFF이며 계좌/주문 연결을 추가하지 않습니다. 수집 시작은 기존 Controller 하나만 실행합니다.
3. 현재 단계·연결·WS·최근 snapshot/후보 집계·실제 수집 경과·UTC 예정 시각·남은 시간을 확인합니다. 값이 없으면 미확인입니다. Paper/Rotation 상태와 연구 실패, ZIP 보존 판정은 별도로 표시합니다.
4. **수집 중지**는 예약/수집을 취소합니다. **PC 종료만 취소**는 수집과 후처리를 계속합니다. 결과 폴더/ZIP 폴더를 열거나 **진단 결과 확인**에서 기존 JSON의 오류·clock·stop caller를 읽습니다. 파일 읽기는 버튼을 누를 때만 실행하며 새 Paper DB 조회나 로그 시스템을 만들지 않습니다.

첫 1시간은 개장 전 예약이 필요합니다. `late_launch`는 명시적으로 표시하며 일반 세션으로 자동 전환하지 않습니다. 일반 Regular Session의 조기 수동 종료는 FULL SESSION 성공이 아닙니다. ZIP 검증 표시는 보존 당시 기록이며 진단 버튼의 존재 확인과 재검증을 혼동하지 않습니다. 2026-10-23 v2.6.5 실장은 65분 예정 종료와 진단 ZIP 보존에 성공했지만 Paper 지연과 연구 품질 부족으로 연구는 FAILED입니다. v2.6.6의 Paper 게시 중복 조회 감소를 포함하며 실장 지연 해결과 실제 PC 종료는 아직 검증되지 않았습니다.

**30초 강제 종료 테스트**는 수집과 별도의 실제 Windows 종료 시험입니다. Collector가 STOPPED이고 첫 1시간 예약·writer·조회/ZIP/검증 작업이 없을 때만 사용할 수 있습니다. 다른 Stock Helper/Collector 앱이 있으면 확인 후에도 차단하므로 먼저 사용자가 기존 작업을 정상 종료해야 합니다. 자동으로 수집을 중단하지 않습니다.

1. 모든 앱의 작업을 저장한 뒤 버튼을 누릅니다. 경고창의 기본 선택은 **아니요**이며 취소하면 OS 명령은 없습니다.
2. **예**를 누르면 Windows 시스템 디렉터리의 `shutdown.exe /s /f /t 30`을 요청합니다. OS 수락 후에만 예약 성공·예상 종료 시각·남은 초를 표시합니다. 남은 시간은 monotonic 기준이며 PC 시계 변경으로 늘어나지 않습니다. 0초 표시는 실제 전원 종료를 확인했다는 뜻이 아닙니다.
3. 시간이 남아 있을 때 **종료 예약 취소**로 `/a`를 요청합니다. 성공/실패·OS 코드·원인 종류를 확인하세요. 취소 실패는 예약을 해제한 것으로 처리하지 않습니다.
4. 창을 닫거나 정상 앱 종료 시 자신이 수락받은 예약을 취소합니다. 실패하면 경고합니다. 강제 프로세스 종료·충돌에서는 예약이 남을 수 있으므로 Windows에서 `shutdown.exe /a`로 직접 취소해야 할 수 있습니다. 결과 불명(timeout)이면 다른 예약을 잘못 취소하지 않도록 자동 `/a`를 하지 않으며 Windows 상태를 직접 확인한 뒤 앱을 다시 여세요.

기존 **PC 자동 종료 없이 테스트** 기본 ON은 65분 수집용 설정입니다. 위 30초 버튼은 별도 확인을 요구하는 일회성 명령입니다. 단순 EXE 실행으로 수집이나 종료가 예약되지 않으며 90분 watchdog도 없습니다. `/f`는 미저장 작업을 잃게 할 수 있습니다. [Microsoft shutdown 명령](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/shutdown).

## Current Version

| 구분 | 현재 값 |
|---|---|
| Market Radar 앱 버전 | **v2.6.8 소스 / 가족용 경량 EXE** (기존 Stock Helper 시험 EXE v2.6.7 보존) |
| 가족용 설정 / 독립 watchdog schema | **1 / 1** (운영 설정·감시 상태, 연구 DB schema와 별도) |
| Session Validator | **1** (수집 품질 검증; 전략 판단 아님) |
| Collector 연구 버전 | **v2.1** |
| test_version | `kiwoom_radar_v2_1` |
| Research schema | **4** |
| Master schema | **1** |
| Windows Quick Launch | **v1** (`.venv-paddle` 소스 launcher; 새 EXE 바로가기와 별도) |
| 독립 One Hour Research Runner | **v1.0.1 소스 / 테스트 EXE v1.0.1** (별도 TEST 폴더; 기존 v1.0.0 보존; manifest/summary 1, diagnostic 1, quality policy 2) |
| 독립 NH read-only adapter | **v1.1** (`nhplug 0.4.1`; 잔고·매수가능수량 조회 PASS, RC 보류) |
| Paper Trading / paper schema | **v1 / 2** (전략 규칙 유지, 기본 OFF, 실제 주문 disabled / not implemented) |
| Rotation Shadow / rotation extension schema | **v2 / 1** (`paper_rotation_v2`, 별도 DB, 기본 OFF) |
| Opening Hour Research / schema | **v1.1 / 1** (`opening_hour_v1_1`, 세션 종료 후 관찰 연구, 기본 OFF) |

프로그램 버전과 DB schema 버전은 별도로 관리합니다. 현재 다음 기능을 제공합니다.

- 실제 Kiwoom read-only REST/WebSocket 연결, 주문·계좌 기능 없음
- ETF·워런트 등 확인 가능한 비보통주 필터; 확인되지 않은 유형은 unknown으로 보존
- protected tracking과 Daily Quality Summary
- graceful Ctrl+C shutdown과 최종 export
- REST deterministic failure suppression 및 결과 샘플 감사 정보
- Dashboard Start/Stop, System Health, Session History, 종목별 결과 조회
- Manual / Regular Session / Capacity Test: XNYS 개장 준비·자동 시작·graceful 종료와 protocol 기록
- 읽기 전용 과거 세션 import, 별도 Master Research DB, 검수용 Review ZIP
- 종료 후 자동 PASS/WARN/FAIL, Validation Summary, Revalidate Session

2026-10-06 ET 표준 **150-slot** 보관 세션을 Validator로 재검증했습니다. **WARN**이며 protocol·무결성·capacity는 PASS, 최대 Active 106/150·saturation 70.67%·미배정 0건입니다. RVOL 0%와 stale·결과 결측 등은 분석 제약입니다. 한 세션으로 150을 영구 최적값으로 확정하지 않으며 CONFIG/저장 설정을 자동 변경하지 않습니다. [현재 개발 상태](CODEX_HANDOFF.md), [영구 검증 이력](CHANGELOG.md)을 참고하세요.

## 문서 안내

단축 수집은 기존 Market Radar GUI의 **첫 1시간 수집 + ZIP 검증 + PC 자동 종료** 옵션을 사용합니다. 독립 OneHourResearch.exe는 실행하지 않습니다. 기존 독립 실행기 소스·배포는 보존하며 ZIP·품질 검증 함수만 재사용합니다. 자세한 사용법과 배포 제약은 아래 GUI 통합 안내를 참고하세요.

새 개발 대화는 [START_HERE](START_HERE.md)에서 시작합니다. 최상위 방향은 [PROJECT_DIRECTION](PROJECT_DIRECTION.md), 연구 가설과 출처 확인 수준은 [RESEARCH_BASIS](RESEARCH_BASIS.md)를 따릅니다. 현재는 Phase A 마무리~Phase B 시작으로, 고정 슬롯의 Regular Session을 preopen arm해 공식 close까지 비교 가능한 데이터를 축적합니다. 1주는 첫 분석 표본입니다. Capacity 비교를 추가할 때에는 **Capacity Test / Max WS 지정 / preopen arm → open+65분 자동 종료**를 사용합니다. 기존 20/50/100은 새로운 표준 통제 비교로 소급 지정하지 않습니다. 자세한 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md)을 따릅니다.

- [Market Radar 기술 문서](market_radar/README.md): provider·지표·추적·품질·CLI·제한
- [CHANGELOG.md](CHANGELOG.md): 영구 버전별 개발·검증 이력
- [CODEX_HANDOFF.md](CODEX_HANDOFF.md): 현재 개발 상태와 다음 작업
- [AGENTS.md](AGENTS.md): 프로젝트 작업·문서 갱신 규칙

## Paper Trading / Shadow Trading v1

**Experimental baseline strategy**이며 수익성은 검증되지 않았습니다(`strategy profitability = not established`). Kiwoom의 현재 후보 순위·완료 분봉 momentum과 live 가격만 사용합니다. NH 연결은 사용하지 않고 실제 주문은 **disabled / not implemented**, 주문 호출은 **0건**입니다. 관측가격 기반 가상 실행이며 거래비용·호가 spread·slippage·체결 가능성을 모델링하지 않습니다.

Dashboard **Paper Trading** 탭에서 수집 시작 전에 **Enable Paper Trading**을 선택합니다. 기본 OFF이며 실행 중에는 설정을 고정합니다. **Regular Session**을 개장 전에 Start Collection하면 preopen 대기 → 정규장 후보 WATCHING → 조건 확인 → 후속 live 가격 진입 → TP/SL → 다음 후보 순서로 진행합니다. Manual은 정규장 진단에만 사용하며 **Capacity Test에서는 강제 OFF**로 Paper thread/DB/WS hook을 시작하지 않습니다. 재실행할 때마다 직접 Enable해야 합니다.

- 후보: 양쪽 TOP10 → Emerging TOP10 → Current TOP10, 그룹 안에서 Emerging rank → Current rank → ticker 순입니다. 기존 슬롯 배정은 변경하지 않습니다.
- 진입: WATCHING 이후 끝난 새 완료 1분봉, 양수 1m·5m return, watch 가격 초과, TOP10·fresh price·정상 WS가 모두 필요합니다. NULL은 진입 불가이며 RVOL/OR/HOD는 근거만 저장합니다.
- 진입 가격: **신호 평가 시각보다 늦게 수신한 첫 유효 Kiwoom 가격**입니다. UTC 로컬 수신시각을 소수초까지 보존하며 거래소 체결시각이라고 주장하지 않습니다.
- 한 번에 1종목, ET 거래일당 OPEN 최대 4회, 동일 ticker 당일 재진입 금지. +15%/-10% 첫 관측 crossing의 실제 관측가격으로 종료하며 목표가 체결을 가정하지 않습니다. 공식 close의 정상 보유분은 마지막 유효가격으로 SESSION_END 처리합니다.
- disconnect·stale·큐 손실·시각 이상은 DATA_INTEGRITY_INVALID, 수동 중단·재시작은 INTERRUPTED로 구분합니다. 불명확한 exit 가격/return은 NULL이며 정상 성과 통계에서 제외합니다. 재시작 시 진입 횟수·ticker 차단을 유지하고 미완료 WATCHING/신호는 새 관측으로 다시 시작합니다.

첫 Enable 실행 때 별도 `data/paper_trading/paper_trades.db`(paper schema 2)를 생성합니다. 연구 schema 4/cache/Validator는 유지합니다. 종료 후 `data/paper_trading/export/<ET-date>/`에 trades/events/horizons/marks CSV와 summary JSON을 저장합니다. marks는 동일 시각의 다른 가격과 exact duplicate까지 observation_id 도착 순서로 보존합니다. 사후 horizon은 target 이후 기본 30초 안의 첫 실제 관측을 사용하고 표본 시각·offset·관측 ID를 기록하며 없으면 NULL입니다. 보간·pre-target 대체는 없습니다. 기존 schema 1 DB는 자동 수정하지 않고 거부합니다. 별도 복사본의 offline migration이 필요하며 과거 유실 틱은 복구할 수 없습니다. 자세한 정의는 [기술 README](market_radar/README.md)를 따릅니다.

v2.4.1에서는 수집기 잠금을 확보한 뒤에만 Paper 복원을 시작하고 잠금 해제 전에 저장을 마칩니다. 수동 중지는 최초 중지 요청시각으로 기록하며, 같은 수신시각에 들어온 서로 다른 가격도 도착 순서대로 TP/SL에 반영합니다.

v2.4.2에서는 수집 상태 JSON을 교체할 때 발생하는 일시적인 Windows 접근 거부에 최대 3회 재시도합니다. 계속 실패하면 기존 파일을 보존하고 실패한 파일명·작업 단계·오류 번호를 표시합니다.

네트워크 없이 합성 재현성 검증(임시 DB만 사용):

```powershell
& .\.venv-paddle\Scripts\python.exe -m unittest tests.paper_trading_test
& .\.venv-paddle\Scripts\python.exe -m market_radar.paper.replay tests/fixtures/paper_baseline_v1.jsonl
```

실제 live shadow는 아직 실행하지 않았습니다. 기존 30초 snapshot만으로 모든 실시간 tick을 복원하지 않으며, clock_jump가 있었던 2026-10-07 Capacity 세션을 전략 수익성 표본으로 사용하지 않습니다.

## Rotation Shadow v2 / Post-exit review

기존 **Baseline v1 전략 규칙은 그대로 보존**합니다. Paper Trading 탭의 **Enable Rotation v2**를 수집 시작 전에 선택하면 같은 시세·평가시각에서 v1과 독립 실행합니다. 기본 OFF이며 Capacity Test에서는 둘 다 실행하지 않습니다. 일반 진입·TP/SL은 v1과 같고, v2만 현재 종목 약화와 새 후보의 모든 **material evidence dominance** 조건을 확인한 뒤 가상 교체합니다. 상승확률은 계산하지 않습니다.

전환은 signal 이후 기존 종목의 첫 유효 가격으로 exit → 그보다 늦은 새 종목 첫 유효 가격으로 entry입니다. 두 체결에 같은 관측을 쓰지 않으며, 각 전략의 하루 4회·동일 ticker 재진입 금지·1종목 원칙을 유지합니다. 실제 주문은 0건입니다.

Paper를 활성화하면 종료된 v1/v2 ticker도 공식 close까지 별도 추적합니다. 원래 TP/SL 도달, SL 이후 회복, TP 이후 추가 상승·시간 순서상 최대 반락과 근거 tag를 기록합니다. 이 데이터는 사후 review 전용입니다. 시세 공백·조기 중지는 INCOMPLETE로 남기며 미래 가격으로 판단/체결을 보완하지 않습니다.

v1 거래 규칙과 기존 실데이터는 보존하며 신규 evidence는 paper schema 2입니다. 별도 `data/paper_trading/paper_rotation_v2/review.db`와 `export/<ET-date>/`에 두 전략의 `paper_trades.csv`, `paper_post_exit.csv`, 실제 후속 tick 경로, `paper_rotation_comparison.csv`, switch evidence CSV, `paper_daily_summary.json`을 저장합니다. 동일 시작 trade는 v1 종료 수익률과 v2 연결 trade 수익률로 비교하고, exact 동일시각 가격이 있는 경우만 교체 이후 손익 차이를 계산합니다. 필드·공식·NULL 정의는 [기술 README](market_radar/README.md)를 따릅니다.

```powershell
& .\.venv-paddle\Scripts\python.exe -m unittest tests.paper_trading_test tests.paper_rotation_v2_test
& .\.venv-paddle\Scripts\python.exe -m market_radar.paper_rotation_v2.replay tests/fixtures/paper_rotation_v2.jsonl
```

## Opening Hour Research v1

미국 정규장 개장 후 첫 60분에 Radar candidate/discovery에 등장한 후보의 가격·거래량 경로를 **기존 자료로 사후 분석**합니다. `data collection / descriptive research`이며 buy signal, prediction model, profitable pattern을 생성하지 않습니다. Paper v1·Rotation v2 판단과 연결하지 않고, 같은 run/ticker의 거래 reference ID만 읽습니다.

Dashboard에서 **Regular Session**을 선택하고 상단 **Opening Hour Research · 종료 후 분석**을 켠 뒤, 공식 개장 전에 Start Collection으로 arm합니다. 기본 OFF이며 Capacity Test와 Manual에서는 강제 OFF입니다. Paper/Rotation은 Paper Trading 탭에서 별도로 켤 수 있습니다. 공식 close까지 수집하면 collector/Paper 종료·기존 export/Validator가 끝난 뒤 별도 분석을 수행합니다. `OFF / ENABLED / COMPLETE / PARTIAL / EMPTY`, 전체/eligible 후보 수와 각각의 60분 coverage를 표시합니다. 처리 오류는 별도 INCOMPLETE입니다. 새 REST·WS 구독이나 backfill은 추가하지 않습니다.

XNYS official open부터 60분, **09:30~10:29 시작의 완료 1분봉 60개**를 사용합니다. 10:30 시작 봉은 제외하고 3분 20개·5분 12개로 집계합니다. cache에 없는 시가, 누락 분, 불완전 집계는 NULL로 보존합니다. 분봉 돌파·고저점 시각은 1분 구간이며, 분 중간에 발견한 후보의 정확한 detection-relative MFE/MAE는 NULL입니다. 해석과 공식은 [기술 문서](market_radar/README.md#opening-hour-research-v1)에 있습니다.

별도 DB는 `data/opening_hour_research/opening_hour.db`, export는 `data/opening_hour_research/export/<ET-date>/<research-id>/`입니다. 날짜별 폴더에 candidates·1m/3m/5m bars·events·time_snapshots·outcomes·volume_segments CSV와 metadata·quality·summary JSON이 생성됩니다. v1.1 식별자는 물리 경로를 제외해 같은 자료를 다른 폴더로 복사해도 동일하며 metadata에 절대 사용자 경로를 내보내지 않습니다. 모든 후보를 보존하고 stock_type_policy에 따른 analysis_eligible·제외 사유, all/eligible coverage를 분리합니다. summary median·outcome 표본은 eligible 기준이며 미구독·결측 후보도 분모에 유지합니다. 해당 폴더를 ZIP으로 보관해 다른 거래일과 비교할 수 있습니다.

종료된 과거 세션의 읽기 전용 수동 분석:

~~~powershell
& .\.venv-paddle\Scripts\python.exe tools/opening_hour_research/analyze_session.py --session-dir "data/<session-directory>"
~~~

필요하면 `--date YYYY-MM-DD`, `--cache-path <cache.db>`, `--paper-db <paper.db>`(여러 번), `--output-db <separate.db>`, `--export-root <folder>`를 지정합니다. 원본 session/cache/export/Master/Paper는 변경하지 않습니다. 기존 Capacity 세션도 구조·기술통계 분석은 가능하지만 전략 수익성 근거로 바꾸지 않습니다. 이번 검증은 합성 임시 자료에 한정하며 실제 live 수집은 후속 확인 대상입니다.

## NH Namuh PLUG read-only probe

Kiwoom은 **market discovery / research**, NH는 독립적인 **read-only execution-price/account provider**로 구분합니다. NH 어댑터 v1.1은 Dashboard/Collector에 자동 연결되지 않습니다. 공식 [nhplug SDK](https://github.com/PLUG-OpenAPI/nhplug-sdk) **0.4.1**을 사용하며, 프로젝트 `.env`의 `NHPLUG_APP_KEY`, `NHPLUG_APP_SECRET`을 읽습니다. 실제 값은 출력하지 않습니다.

NH 토큰은 파일 캐시 없이 프로세스 메모리에서만 재사용합니다. 따라서 NH probe를 새 프로세스로 실행할 때마다 새 발급과 보안 알림이 발생할 수 있습니다. Paper Trading과 오프라인 테스트는 NH 토큰을 발급하지 않습니다.

```bash
./.venv-paddle/Scripts/python.exe -m pip install -r market_radar/requirements-nhplug.txt
./.venv-paddle/Scripts/python.exe tools/nhplug_probe/probe.py
./.venv-paddle/Scripts/python.exe tools/nhplug_probe/accounts.py
```

기본 실행은 인증 → AAPL 현재가 → 계좌목록 조회 후 종료합니다(`--accounts`는 기본 포함 동작). `--realtime`에서만 지정한 한 종목의 공식 GIC를 종목마스터에서 확인해 대문자 `RC`로 구독합니다. `PASS`/`CONNECTED_NO_TICK`/`FAIL`을 구분하고 구독·해제 ACK와 연결 종료를 검사합니다. 출력 가격의 `received_at`은 로컬 UTC 수신 시각이며 거래소 체결시각으로 해석하지 않습니다.

2026-10-08 KST 실제 기본 smoke는 **PASS**: OAuth·AAPL REST 현재가·계좌목록 성공, 계좌 6개 중 운영 유형 `01`·`02` 기준 3개. 마스킹한 끝 4자리가 같아도 전체 계좌가 다르면 별개입니다. 이전 WebSocket 검사는 연결 성공, **구독 FAIL (`WSS10012`)**로 체결 수신과 정상 해제 ACK는 미확인, 소켓 close는 확인했습니다. [공식 명세](https://www.nhplug.com/openapi-docs/gbstock/openapi.json)는 `RC`에 유료시세 약정을 요구하지만 해당 오류 코드의 정확한 원인은 확인되지 않았습니다. RC는 현재 보류하며 이번 계좌 작업에서 수정·재호출하지 않았습니다. 실시간 시장 시세는 Kiwoom이 담당합니다.

`accounts.py`는 운영 계좌 각각의 미국/USD 잔고를 조회하고, 잔고 조회가 PASS인 계좌 모두에 AAPL 매수가능수량을 조회합니다. `/gbstock/inquiry/v1/buyableAmount`의 `pcs_dit="2"`와 NH REST 현재가를 사용하며, 원화증거금(`wtm_cur_knd_cd="2"`)·지정가 기준의 조회입니다. 2026-10-08 실제 결과는 운영 3개 모두 잔고 PASS(보유 종목 0·0·1개), 매수가능수량 조회 PASS(모두 0주), 실제 주문 0건입니다. 수량 0은 조회 실패가 아닙니다.

토큰 파일 캐시는 비활성화하고 계좌번호는 마지막 4자리만 표시합니다. 원본 응답·인증값·계좌 전체를 파일에 저장하지 않으며 기존 `data/`를 사용하지 않습니다. 같은 끝자리 계좌는 이번 응답 순서의 `live-1` 등으로 구분하며 이 번호는 영구 식별자가 아닙니다. 사용 가능한 계좌가 여러 개면 사용자가 선택해야 하며, `NHPLUG_DEFAULT_ACCOUNT`나 `.env`를 자동 변경하지 않습니다. 허용 REST는 인증·현재가·계좌목록·잔고·매수가능수량 조회뿐입니다. NH는 Paper에 연결하지 않으며 실제 주문은 미구현이고 buy/sell/modify/cancel/reservation 경로는 차단합니다.

RC 키 재감사에서도 기존 전송 키는 AAPL 티커가 아닌 공식 마스터 GIC였습니다. 공식 `sGIC[15]`는 저장 필드 폭이며 AAPL 행의 유효 값은 7문자(뒤 공백 8개 제거)입니다. 같은 키를 RC/7080으로 재시험해 WSS10012를 재현했습니다. 권한·약정은 원인 후보로만 남깁니다. probe는 전송한 키의 형식·길이와 ACK code만 진단에 추가하며, 소문자 `rc`로 자동 전환하지 않습니다.

## 환경 변수

프로젝트 루트의 `.env` 파일에 Toss Open API 인증 정보를 입력합니다.

```env
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
```

`.env`는 Git에 포함되지 않습니다.

## 설치

Git Bash 기준:

```bash
git clone https://github.com/asdfzd/stock_helper.git
cd stock_helper

py -3.11 -m venv .venv
./.venv/Scripts/python.exe -m pip install --upgrade pip
./.venv/Scripts/python.exe -m pip install -r requirements.txt

py -3.11 -m venv .venv-paddle
./.venv-paddle/Scripts/python.exe -m pip install --upgrade pip
./.venv-paddle/Scripts/python.exe -m pip install -r requirements-paddle.txt
```

PaddlePaddle backend는 환경에 맞게 별도로 설치합니다. Windows NVIDIA GPU
(CUDA 12.6 호환 드라이버)는 다음 공식 cu126 패키지를 사용합니다.

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip uninstall -y paddlepaddle
& .\.venv-paddle\Scripts\python.exe -m pip install paddlepaddle-gpu==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/
```

GPU를 사용할 수 없는 PC의 CPU fallback 설치 방법은 다음과 같습니다.

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip uninstall -y paddlepaddle-gpu
& .\.venv-paddle\Scripts\python.exe -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

실행 시 `[PADDLE DEVICE]` 로그에서 요청 장치, 실제 장치, CUDA 지원 여부와
GPU 개수를 한 번 확인할 수 있습니다. GPU 사용량과 VRAM은 별도 PowerShell에서
`nvidia-smi -l 1`로 확인합니다. GTX 1650 4GB에서는 reader를 한 번 생성해 계속
재사용하는 현재 구조를 유지하며, FP16/TensorRT/INT8은 사용하지 않습니다.

## 실행

### Quick Launch (Windows)

바탕화면 **Stock Helper**를 더블클릭합니다. 기존 `.venv-paddle`의 `pythonw.exe`로 UI를 열며, 없으면 같은 환경의 `python.exe`를 콘솔 없이 사용합니다. 프로젝트 폴더의 [launch_stock_helper.cmd](launch_stock_helper.cmd)로도 실행할 수 있습니다(직접 실행 시 콘솔이 잠깐 보일 수 있음).

바로가기 재생성은 프로젝트 폴더에서 실행합니다:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\create_stock_helper_shortcut.ps1
```

오류 확인은 [launch_stock_helper_debug.cmd](launch_stock_helper_debug.cmd)를 더블클릭합니다. `python.exe`로 실행하며 종료 후에도 콘솔을 유지합니다. 필수 파일 누락이나 비정상 종료는 일반 실행에서도 오류 안내를 표시합니다.

Stock Helper 창을 여러 개 실행하지 않는 것을 권장합니다. 수집은 Dashboard에서 직접 시작하며 기존 Collector 중복 실행 보호를 유지합니다. 나중에 `assets\stock_helper.ico`를 추가하거나 생성 스크립트에 `-IconPath "아이콘 경로"`를 주고 재실행하면 아이콘을 바꿀 수 있습니다. 다른 대상을 가리키는 동명 바로가기는 덮어쓰지 않습니다. 저장소 이동 후 기존 바로가기가 남았다면 이름을 바꾸고 다시 생성하세요. 이동한 `.venv-paddle` 자체의 정상 동작은 별도 확인이 필요합니다.

### CLI 실행

메인 UI:

```bash
./.venv-paddle/Scripts/python.exe ./live_ui.py
```

기존 UI의 Market Radar 버튼으로 독립 창을 열 수 있습니다. Radar 의존성 설치와 소규모 headless 수집 예시(PowerShell):

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip install -r market_radar/requirements-kiwoom.txt
& .\.venv-paddle\Scripts\python.exe -m market_radar --collect --max-ws-symbols 20 --data-dir data/kiwoom_week_v2_1
```

실제 Kiwoom 수집에는 real 모드의 로컬 인증 설정이 필요합니다. 새 실험은 별도 데이터 폴더에서 시작하고 Ctrl+C로 정상 종료합니다. 상세 설정·replay·검증 명령은 [Market Radar 실행 안내](market_radar/README.md)를 따릅니다.

### Dashboard 사용

1. Stock Helper → **Market Radar**를 엽니다. 창을 여는 것만으로 수집하지 않습니다.
2. **Max WS**, **Data root**, **Pre-target grace (s)**를 지정하고 **Start Collection**을 누릅니다. 매번 Data root 아래 새 실행 폴더를 만듭니다.
3. Live Radar의 두 순위 표와 System Health를 확인합니다. 없는 값은 `-`이며 RVOL 사유는 Data Status에 표시됩니다.
4. **Stop Collection** 후 STOPPING/FINALIZING → STOPPED를 기다립니다. final export → Master 등록 → 자동 Validator → validation summary 및 Master 상태 저장을 수행합니다. 검증기 오류는 원본 수집 결과를 변경하지 않습니다.
5. **Session History**의 **Validation** 열과 **Validation Summary** 탭을 확인합니다. **Revalidate Session**은 종료된 원본을 읽고 summary와 Master 검증 상태만 갱신합니다. **Create Review ZIP**에는 검증 JSON과 manifest 상태도 포함됩니다. ZIP은 해당 세션의 `reviews/`에 저장됩니다.
6. **Open Review ZIP Folder**에서 ZIP을 찾아 검수에 전달합니다. 기본 ZIP에는 DB·raw·인증 파일이 포함되지 않습니다. 민감정보 패턴이 발견되면 생성하지 않습니다.

Dashboard historical cache는 실행 폴더와 별도로 `data/market_radar_cache/kiwoom_v2.db`를 계속 재사용합니다. Data root를 바꿔도 cache는 이동하지 않으며 **Historical Cache** tooltip에서 전체 경로를 확인합니다. 호환되지 않는 cache는 거부하고 과거 session cache를 자동 병합하지 않습니다. 공유해도 과거 coverage가 부족하면 RVOL은 계속 `-`입니다. 최근 150-slot 세션도 RVOL available 0%로 검증됐습니다.

STOPPED의 **Current Active WS=0 / Current Connected=No**는 현재 연결 상태입니다. **Last Active WS / Last Protected / Last Slot saturation**은 마지막 batch 값이며 그대로 보존됩니다. 정상 종료의 `Stopped cleanly`와 shutdown 시간도 유지됩니다. Session History의 **Slot unavailable events**는 순간 종목 수가 아닌 누적 이벤트 수입니다.

기존 CLI는 `--data-dir` 아래 cache를 만드는 동작을 유지합니다. CLI에서도 공유하려면 `--cache-path data/market_radar_cache/kiwoom_v2.db`를 명시합니다. 이번 검증은 보관된 실제 세션의 offline 재감사이며 새로운 live collection은 실행하지 않았습니다.

PASS는 검사 범위에서 중대한 문제가 확인되지 않았다는 뜻입니다. WARN은 해당 결측·품질 제약을 고려해야 하며 FAIL은 표준 비교/연구 표본에서 제외합니다. PASS를 매수 신호나 전략 수익성으로 해석하지 않습니다. Manual/legacy는 표준 비교 적합성을 보증하지 않으며 최소 WARN입니다. 파일이 변경되어 기존 검증이 오래되면 다시 Revalidate해야 ZIP에 검증 상태를 담을 수 있습니다.

**Import Existing Session**은 원본 연구 DB가 있는 폴더를 선택합니다. 거래일별로 등록하고 같은 세션 재가져오기는 중복 대신 결과를 갱신합니다. 기존 원본은 읽기 전용이며 Master는 `data/market_radar/master.db`입니다. 과거 자료의 실제 실행시간·정상 종료를 확인할 구조화 정보가 없으면 관측 구간·unknown으로 표시합니다.

종목을 더블클릭하면 Candidate History에 +30s부터 D+3까지의 상태·결과를 표시하고 Stock Helper로 선택 신호를 전달합니다. 기존 분석 카드가 있으면 그 카드로 이동하며, 없으면 ticker를 클립보드에 준비합니다. 자동 화면 캡처는 실행하지 않습니다. v2.2의 100-slot 보관 실장 세션은 검산했으며, v2.2.1 shared cache 실장 검증과 EXE 배포 검증은 별도입니다.

### 첫 1시간 수집 + ZIP 검증 + PC 자동 종료 (GUI 통합 v4)

**기존 Stock Helper → Market Radar 안의 기능**입니다. OneHourResearch.exe를 실행하지 않으며 현재 Dashboard의 CollectorController 한 개만 사용합니다. 첫 1시간 옵션은 기본 OFF이며 `PC 자동 종료 없이 테스트`는 기본 ON입니다. 실제 종료는 사용자가 테스트 체크를 해제해야 활성화됩니다.

1. 개장 전에 Market Radar의 **수집 테스트 → 첫 1시간 (65분)**을 선택합니다. 기존 체크박스는 수집 상세 설정에 있습니다. 가능하면 개장 30분 전에 준비합니다.
2. Regular Session / Max WS=150 / Paper v1·Rotation v2·Opening Hour ON이 자동 적용됩니다. 실제 주문은 OFF(기존대로 미구현)입니다. 다른 모드로 돌아가려면 체크를 해제하면 이전 GUI 설정이 복원됩니다.
3. 첫 검증은 기본 선택된 **PC 자동 종료 없이 테스트**를 유지합니다. 이것은 실제 수집·실제 ZIP 검증을 수행하는 운영 테스트이며 합성 데이터 모드가 아닙니다.
4. **수집 시작**(상세 설정의 Start Collection)을 한 번 누릅니다. 같은 Collector가 진행 중이면 시작을 거부합니다. 다른 Stock Helper/OneHourResearch 프로세스 또는 수집 잠금·프로세스 확인 오류도 차단하며 기존 프로세스를 종료하지 않습니다.
5. 수집 테스트에서 UTC 예정 시각·현재 상태·ZIP을 확인하고 **결과 폴더 열기 / 진단 결과 확인**을 사용합니다. 수집 상세 설정에는 KST 시각과 전체 ZIP 경로도 있습니다.
6. **PC 종료만 취소**는 수집·후처리를 계속하며 Windows 종료만 해제합니다. **Stop Collection**은 수집 예약/수집을 취소하고 PC 종료도 차단합니다. 창 닫기는 수집 취소로 기록하고 정상 정리를 기다립니다. OS 취소 실패 시 경고와 재시도 버튼을 유지합니다.

| 기준 | 미국 ET | KST 서머타임 | KST 표준시 |
|---|---|---|---|
| 공식 개장 | 09:30 | 22:30 | 23:30 |
| 첫 60분 | 09:30~10:30 | 22:30~23:30 | 23:30~다음 날 00:30 |
| 정상 stop 요청 | 10:35 | 23:35 | 다음 날 00:35 |
| 종료 예약 허용 시작 | 10:40 이후 | 23:40 이후 | 다음 날 00:40 이후 |
| 실제 PC 종료의 가장 이른 시각 | 10:41 | 23:41 | 다음 날 00:41 |

날짜와 시간은 기존 XNYS `exchange_calendars` 일정과 America/New_York 시간대를 사용합니다. 휴일·주말은 다음 거래일, 조기폐장은 공식 close를 유지합니다. 장중에 늦게 실행한 첫 1시간 모드는 `late_launch`로 거부합니다. 개장+70분은 제한 시간이 아니라 **가장 이른 종료 예약 시각**입니다. 후처리가 늦으면 정상 완료까지 기다리며 강제로 worker를 죽이지 않습니다. [NYSE 공식 일정](https://www.nyse.com/trade/hours-calendars)을 기준으로 하는 정규장입니다.

기본 결과 경로는 `<Data root>/market_radar_one_hour/run_<UUID>/`입니다. 기본 Data root에서는 `data/market_radar_one_hour/` 아래에 생성됩니다.

- 정상: `one_hour_research_<ET-date>_<run-id>_v2.zip`
- 실패: `one_hour_diagnostic_<ET-date>_<run-id>_v2.zip` 및 `one_hour_run_summary.json`
- 신규 실행별 원본: `radar/`, `paper_v1/`, `rotation_v2/`, `opening_hour/`. 자동 실행에서는 Paper/Rotation/Opening DB를 실행별로 격리합니다. 일반 모드의 저장 경로는 변경하지 않습니다. 기존 shared historical cache와 Master import 정책은 그대로 사용합니다.
- 실패 ZIP은 `FAILED`와 실패 사유·품질·누락·분석 가능 범위를 유지합니다. 연구 결과와 `preservation_status`/`shutdown_eligible`은 별도 판정이며, 검증된 진단 ZIP과 원본을 안전하게 보존했다면 자동 종료가 가능합니다. DB가 만들어지기 전 시작 거부이거나 안전한 ZIP을 만들 수 없으면 요약/원본만 보존하며 ZIP 생성 실패를 성공으로 바꾸지 않습니다.
- ZIP에는 allowlist CSV/JSON, 오류 근거·원본 보존 manifest와 선택적 처리 시간 계측만 포함하고 DB/WAL/cache/log/.env/계좌·인증정보는 포함하지 않습니다. 기존 원본을 삭제하지 않습니다.

자동 종료는 계획된 개장~65분 수집 또는 호출 근거가 보존된 시계 이상 중지, 정상 Collector 종료, 모든 worker/provider/DB·GUI 파일 작업 완료, 원본 보존 해시, 필수 증거가 있는 정상 또는 진단 ZIP의 CRC/SHA256/파일·날짜·run ID 검증, 파일 flush, 사용자 활성화와 개장+70분 경과를 요구합니다. 미시작·늦은 시작·사용자 취소·원인 미상 중단·원본 손실·필수 증거 누락·ZIP 검증 실패는 차단합니다. Paper/Rotation 오류와 coverage 부족은 연구 FAILED로 남기며 그 자체로 종료를 차단하지 않습니다. 기존 Regular Validator의 조기 종료 FAIL도 보존합니다.

v2.6.5는 예약 대기부터 수집·후처리·종료 대기까지 supervisor 스레드의 Windows 절전 방지 요청을 유지하고 정상/예외/취소 정리 때 해제합니다. 전원 설정을 변경하지 않으며 수동 절전·종료, 배터리/전원 상실을 막지 않습니다. 기본 테스트 모드도 이 요청을 사용합니다.

`diagnostics/runtime.json`은 최초 오류, 단계, 중지 호출 위치, wall/monotonic 차이, Paper/Rotation 오류와 worker 상태, 보존·종료 결과를 atomic/fsync로 남깁니다. 보존 시작 시 고정한 `diagnostics/collection.json`만 ZIP과 원본 해시 검증에 넣고 이후 상태는 runtime에 남깁니다. 저장 실패는 메모리에도 유지하며 종료를 차단합니다. 완전한 디스크 고장이나 프로세스 강제 종료 직전 기록까지 보장하지 않습니다.

v2.6.5는 정시 개장한 실행에서 짧은 전진 보정이 관측되면 연구를 FAILED로 유지하면서 진단용 Radar 원본 수집을 계속할 수 있습니다. 기존 2초 clock_jump 검사는 유지하며, 두 관측 간 wall/monotonic 간격이 각각 5초 이내이고 양의 차이가 2초를 넘는 경우만 후보입니다. 기존 수집 오류·원본 유실·연결 gap·취소가 없고 Paper fail-closed 요청과 영속 진단이 가능해야 합니다. 이 분류는 Windows 정상 동기화의 확정 판정이 아닙니다. 역행·큰 전진·5초 초과 관측 공백·monotonic 이상은 계속 중지합니다. 수집 종료는 공식 UTC open+65분과 실제 개장 gate의 monotonic+3,900초를 모두 요구합니다. +3.532428초 보정이면 실제 종료 wall 시각도 기준보다 그만큼 늦을 수 있으며 원본 timestamp를 수정하지 않습니다. 첫 60분 Opening과 공식 close는 그대로이고 해당 실행을 정상 65분 비교 표본으로 승격하지 않습니다. 반복 보정은 같은 경과시간 기한을 유지하며 관측 불확실 구간과 이후 로컬 시각 구간을 diagnostics에 남깁니다. `clock_jump`/`scheduler_gap_or_resume`의 호출 근거와 모든 원본·export·ZIP 보존이 검증된 경우에만 조건부 종료를 허용합니다. UTC open+70분과 최초 monotonic 기한을 모두 지키며 시계 역행은 기한을 늦춥니다. 후처리 후 추가 보정이 감지되면 최소 60초 안정 대기를 추가합니다. supervisor/GUI가 멈추거나 죽으면 새 종료 요청을 보장할 독립 watchdog은 없으므로 PC가 켜진 채 남을 수 있습니다. 이미 OS에 수락된 예약은 프로세스 종료 후에도 남을 수 있습니다.

GUI는 **연구 정상 / 정상 ZIP 저장 / PC 종료 가능**, **연구 오류 / 검증된 진단 ZIP 저장 / PC 종료 가능**, **저장·검증 오류 또는 취소 / PC 종료 차단**을 구분합니다. 종료 없는 테스트에서는 두 연구 결과 모두 Windows 명령을 실행하지 않습니다.

2026-10-16 소스 안정화: Opening 분석은 실제 TEMP에서 DB/WAL 복사량과 여유 공간을 검사하고, 단계·errno/WinError·안전한 stack 위치를 `radar/opening_hour_diagnostics.json`에 남깁니다. Opening DB 생성 전 실패에 한해 이 진단 근거와 나머지 필수 원본/exports가 검증되면 실패 진단 ZIP을 보존합니다. 빈 DB는 만들지 않으며 Opening INCOMPLETE와 연구 FAILED는 유지합니다. 정확한 과거 OSError 원인은 미확정이고 Paper 2초 지연은 미해결입니다. 다음 실제 실행은 소스를 다시 연 뒤 **PC 자동 종료 없이 테스트**로 확인해야 하며, 이번 검증은 합성 오프라인 검사입니다.

quality policy 2의 구독 확인/경계/미구독 분모와 전체 95%·종목별 90%·연속 결측 최대 2분 기준은 연구 판정에 그대로 적용합니다. ZIP 복사본의 의심스러운 설명 값만 정제하고 원본 해시·행/열·변환을 manifest에 기록합니다. 가격·분봉·연구 지표를 통과하도록 변경하지 않습니다.

다음 실제 첫 1시간 실행에는 `paper_v1/paper_performance.json`이 추가됩니다. 큐 대기/최대 적체, Paper·Rotation, SQL·commit의 평균/p95/p99/최대를 분리합니다. 원시 시세/인증값/SQL 본문은 기록하지 않고 최근 64개 및 최초 오류 주변 시각만 보존합니다. 이벤트별 SQL/commit/엔진 단계 집계와 첫 gap의 정확한 오류 코드·시각도 기록합니다. 중첩 단계는 합산하지 않습니다. 기존 2초 오류 제한과 전략·DB 저장 정책은 유지합니다.

조건을 통과하면 시스템 경로의 `shutdown.exe /s /f /t 60`을 한 번 요청합니다. 예약 후에는 GUI **PC 종료만 취소** 또는 `shutdown.exe /a`로 취소할 수 있습니다. 강제 종료는 다른 앱도 닫으므로 이 옵션을 선택한 운영 PC에서는 미저장 작업을 남기지 않아야 합니다. OS 명령 대기는 10초로 제한하고 결과가 불확실하면 취소를 시도합니다. 취소 실패 시 예약이 남아 있을 수 있습니다. [Microsoft 명령 문서](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/shutdown).

**배포 상태:** 소스와 가족용 경량 EXE는 v2.6.8이며 기존 전체 Stock Helper 시험 EXE v2.6.7과 옛 EXE는 보존합니다. 기존 launcher는 `.venv-paddle`에서 `live_ui.py`를 실행하는 소스 바로가기입니다. 이미 떠 있는 GUI에는 변경이 자동 적용되지 않습니다. 새 가족용 운영의 첫 실제 수집은 위 `--dry-run` 절차로 감독하세요. 실장 Paper 지연 개선과 자동 종료 전체 경로는 미검증입니다. 30초 실제 종료 성공은 사용자 보고입니다.

## Windows 시험 EXE 빌드와 실행

빌드 도구를 설치하고 PowerShell에서 빌드합니다.

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip install -r .\requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

이 전체 OCR 빌더의 기본 결과는 현재 소스 버전을 따른 `dist\StockHelper_v2_6_8_TEST\StockHelper.exe`입니다(이번에는 전체 OCR 패키지를 다시 빌드하지 않음). 이미 배포된 v2.6.7 폴더는 보존합니다. 기존 폴더가 있으면 덮어쓰지 않고 거부하며 `-OutputName StockHelper_v2_6_8_TEST_2`처럼 새 이름을 지정할 수 있습니다. `-PreflightOnly`로 의존성·OCR 모델·여유 공간(최소10GiB)을 검사합니다. 가족용은 `build_family_windows.ps1`로 별도 `dist\MarketRadarFamily_v2_6_8\MarketRadarFamily.exe`를 빌드하며 OCR/GPU를 포함하지 않습니다.

`_internal`, `.paddle-cache/official_models`의 두 OCR 모델, `build_manifest.json`을 포함한 **폴더 전체**가 배포 단위입니다. EXE 하나만 옮기면 실행되지 않습니다. 관리자 승격을 강제하지 않는 `asInvoker` 매니페스트를 사용합니다. Windows 종료 권한이 없으면 OS 거절 코드를 표시합니다. 관리자 권한으로 실행한 HTS와의 캡처/전역 단축키 호환성은 같은 권한 수준에서 별도 확인이 필요합니다.

Frozen runtime root는 EXE의 부모 폴더이며 기본 data/cache/Master DB와 `.env` 검색도 이 위치입니다. 이전에 저장된 Dashboard의 Data root 설정은 유지되므로 수집 전 화면에서 실제 경로를 확인하세요. API 수집이 필요하면 사용자가 자신의 `.env`를 EXE 옆에 별도로 구성합니다. Kiwoom 인증은 이 .env 파일을 직접 읽습니다. 빌드와 smoke는 실제 인증파일을 복사하지 않습니다. `.env.example`은 항목 안내이며 실제 자격증명은 넣지 않습니다. 다른 사람에게는 인증파일·운영 data·로그를 제외한 깨끗한 배포 폴더만 전달하세요.

기존 바탕화면 **Market Radar EXE 테스트**는 보존된 전체 v2.6.7 EXE를 가리킵니다. Stock Helper에서 **Market Radar**를 누르면 수집 테스트의 30초 버튼이 있습니다. 이 기존 EXE 실행만으로 수집이나 PC 종료가 시작되지는 않습니다. 새 **Market Radar 자동 수집** 바로가기와 구분하세요.

오프라인 패키지 검사는 `StockHelper.exe --offline-gui-smoke <새 빈 경로> [--ocr]`로 수행합니다. 이 명시적 진단 모드는 runtime/QSettings/출력을 격리하고 capture·가격/시세 서비스를 시작하지 않으며 모든 자식 프로세스·네트워크를 차단합니다. 실제 GUI·버전·버튼·기본값을 확인해 JSON과 화면 PNG를 남깁니다. `--ocr`는 배포 모델을 scratch로 복사해 OCR 초기화도 확인합니다. 실제 HTS 캡처·API 연결·PC 종료 검증과 구분합니다.

콘솔 기반 live capture 진단:

```bash
./.venv-paddle/Scripts/python.exe ./tools/live_capture_test.py
```

저장된 structured OCR JSON 파서 진단:

```bash
./.venv-paddle/Scripts/python.exe ./tools/live_parser_test.py --json ./ocr_results/파일명.json
```

## 프로젝트 구조

- 프로젝트 루트: 실제 UI, 캡처, OCR 파서, 현재가 조회, Registry 모듈
- `market_radar/`: 독립 연구 수집·provider·지표·DB/export·RadarPanel
- `tests/`: 외부 서비스 없이 반복 실행하는 자동·회귀 테스트
- `tools/`: live capture, OCR 비교, 저장 JSON 파싱, Toss API 수동 진단 도구
- `data/`: 로컬 연구 DB·cache·logs·export; Git에 포함하지 않음

대표 회귀 테스트:

```bash
./.venv-paddle/Scripts/python.exe ./tests/ui_registry_integration_test.py
./.venv-paddle/Scripts/python.exe ./tests/parser_regression_test.py
```

OCR 엔진 비교(PowerShell 스크립트를 Git Bash에서 실행):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./tools/run_ocr_comparison.ps1
```
