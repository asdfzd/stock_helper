# Changelog

## 2026-10-22 — Market Radar v2.6.5 / 수집 테스트 기본 탭과 소스 동기화

- `수집 테스트`를 기본 탭으로 추가하고 기존 네 탭·상세 수집 설정을 유지했다. Collection Mode/Max WS/Paper/Rotation/Opening/no_shutdown은 원래 위젯과 같은 상태다. 새 GUI Regular/150 기본과 저장값 유지, 실제 주문OFF·PC 자동 종료 기본OFF. 같은 Controller와 cached Paper view를 사용하며 GUI timer의 Paper SQL/추가 Collector는 없다.
- 실제 phase/연결/WS/최근 count/elapsed/UTC 일정/countdown, Paper/Rotation 오류, 최초 오류·queue wait, 연구/ZIP 보존/PC 종료 판정을 구분한다. late_launch 자동 우회 없이 안내한다. 결과·ZIP 폴더와 allowlist JSON 진단 읽기는 사용자 버튼으로만 실행한다. 미확인·쓰기 실패·읽기 실패를 성공으로 표시하지 않는다.
- 전체 오프라인 **732/732 PASS, FAIL/ERROR/SKIP0**, 기존 Stock Helper main scripts **12/12 exit0**, GUI 경계 28/28 PASS(전체와 중복). 1366×768/1920×1080용 client 영역 배치·한글 캡처와 추가 SQL0 검사, 과거 실장 진단 ZIP35항목/원본hash41 재검증 PASS. 근거: build/collection_gui_20261022의 JSON/log/PNG. 실장 원본 FAILED/BLOCKED 유지.
- 첫 전체 실행 731개는 718 PASS/ERROR13. 12개는 synthetic worker가 DB/cache 종료 후에도 running=True였던 fixture 오류로 실제 Controller guard가 차단했다. 합성 collection 종료를 후처리 전에 반영하고 busy는 후처리까지 유지했다. 나머지 WinError5 atomic replace 1건은 최종 재실행에서 재현되지 않았으며 점유 주체/원인 미확정이다. 제품 안전 검사/쓰기 정책은 완화하지 않았다. 초기 GUI 1개 검사에서 숫자 표시 기대값 FAIL과 stub teardown ERROR가 겹쳤고 수정 후 통과했다.
- 기존 누적 운영 안정화·계측·Paper view/publish 최적화와 GUI 변경을 명시적 파일 목록으로 소스 관리한다. `.gitignore`와 staged 검사로 보고서/실장 DB/cache/WAL/SHM/log/ZIP/build/venv/EXE를 제외한다. 방향 DOCX는 기존 tracked blob을 유지한다. 실제 Kiwoom/API/주문/Windows 종료·예약·EXE 빌드 없음. 실장 무인 운용·Paper 병목 해소는 별도 검증이다.

## 2026-10-22 — Market Radar v2.6.5 / GUI integration 4 / 시계 전진 보정 후 원본 지속

- 기존 2초 clock_jump/5초 gap 검사를 유지하면서, 정시 개장한 첫65분 GUI 실행의 짧은 전진 보정 후보만 진단용 Radar 원본을 계속 수집한다. 양쪽 관측 간격 ≤5초와 양의 차이 >2초, 정상 writer/provider·원본 유실 없음·Paper fail()·영속 진단을 요구한다. 역행·큰 전진·긴 관측 공백·monotonic 이상·사용자 취소는 안전 중지한다. 정상 NTP임을 인증하지 않는다.
- 개장 gate의 추가 monotonic 관측으로 3,900초 경과와 UTC 예정 stop을 함께 확인한다. 반복 보정으로 기한을 앞당기거나 다시 시작하지 않는다. 실제 wall timestamp와 불확실 구간/후속 clock epoch를 고정 diagnostics 및 ZIP summary에 보존한다. 연구 FAILED·clock_jump/기존 Validator FAIL을 유지하고, 기존 DB/재export/ZIP/원본 검증 뒤에만 조건부 PC 종료를 허용한다.
- 기존 절전·기록·PC 종료만 취소·실제 종료 기본 OFF·open+70분 양쪽 gate를 보존했다. Paper/Rotation consumer·engine·storage·telemetry, provider/rank, DB/schema/journal/commit·2초 gate·WS150은 변경하지 않았다. 최초 Paper queue wait 2009.582ms 병목은 미해결이다. watchdog·EXE 재빌드·실제 API/주문/수집/Windows 종료는 수행하지 않았다.
- 오프라인 검증 근거: build/clock_continuity_20261022. 확대 회귀 **629/629 PASS**, 최종 경계 **148/148 PASS**, 각각 FAIL/ERROR/SKIP 0. 마지막 active worker 조건 및 70분 직전 보정은 최종 경계 실행으로 재검증했다. 원본 582개 SHA/mtime 동일, A/B 정상65분 동일·보정 시300→3900초(합성), Paper/Paired 전체 논리 DB 등16비교 PASS. 초기 테스트 선택의 존재하지 않는 클래스 2회, 테스트 SQL 열 이름 오류 1회, 합성 원본 DB 직접 조회가 WAL inventory를 바꿔 보존 gate가 정상 차단한 1회와 재시도는 단일 작업 보고서에 기록한다. 원본 조회는 copied_database로 고쳤고 integrity 검사는 완화하지 않았다.


## 2026-10-22 — Market Radar v2.6.4 / GUI integration 3 / 무인 운용 안전 경계

- 10/21 조기 종료 조사: Kernel-General 이벤트 1/RecordId812205의 +3.5324284초 wall 보정과 44ms 뒤 stop을 대조했다. clock_jump 감지는 유지했다. 실제 supervisor 호출 trace 부재는 추론 한계로 기록했고, Paper 오류 뒤 Collector batch를 확인했다. 최초 지연의 queue wait/processing은 2009.582/259.231ms다. 실장 ZIP 35개 entry와 원본 41개를 재검증했으며 원래 FAILED/BLOCKED 판정을 승격하지 않았다.
- 영구 run 진단, 최초 오류/중지 호출 경로/시계 차이/worker 상태/보존·OS 결과, fsync+atomic replace, sticky 쓰기 실패 차단을 추가했다. 고정 collection.json만 ZIP 원본으로 삼고 runtime.json은 후속 상태를 보존한다. Paper 계측은 이벤트별 단계 통계와 최초 gap 코드/시각을 추가했다. 새로운 중복 비용은 확정되지 않아 전략/처리 최적화는 하지 않았다.
- supervisor 스레드의 실행 구간에만 Windows 절전 요청을 유지한다. PC 종료만 취소와 수집 취소를 분리하고 실제 종료 기본 비활성화. 알려진 clock interruption은 clean 종료·writer 완료·원본/재export/ZIP 검증 후에만 실패 진단 종료를 허용한다. open+70 UTC/최초 monotonic 기한, 보정 후 안정 대기, 최종 OS 경계 재검사를 유지한다. 미시작/사용자 취소/unknown crash/보존 실패/남은 writer는 차단한다. watchdog은 미구현이다.
- 앱 버전만 2.6.4로 갱신했다. Paper2/research4/Opening1/Rotation extension1/archive2 schema, 2초 기준/FIFO/commit/WS150/실제 주문 정책은 유지한다. 독립 runner success-only 정책과 EXE는 유지한다. 관련 회귀 514 PASS, 최종 경계 95 PASS, Paper 독립성 1 PASS(중복 포함), FAIL/ERROR/SKIP 0. 계측 A/B 4쌍의 이벤트별 상태/논리 DB 16비교 PASS. 원본/기존 변경 425개 SHA/mtime 동일. 근거는 build/unattended_20261021의 JSON/log다. 초기 67개 검사 중 1개는 새 runtime 진단 파일을 원본 불변 검사로 묶은 기대값 오류였고, 연구 원본 검사는 유지한 채 수정 후 재실행했다. 실제 API/주문/Windows 종료/재수집/EXE 빌드는 실행하지 않았다.

## 2026-10-21 — 이전 HANDOFF의 Paper 조사 및 publish 개선 기록 보존

- **2026-10-21 장중 Paper 오류 조사 / PARTIAL**: `data/20261021_014425_586dc5d6`(ET 10/20)의 두 Paper DB에서 **16:57:05.501237 UTC, WORX, paper_processing_delay**를 확인했다. 각각 17개 WATCH 이력·950 marks·진입 0이며 동일 오류다. 두 DB 세션은 종료 때 CLOSED, GUI 최상위만 runtime.error로 ERROR를 표시하므로 Rotation CLOSED를 정상 처리 완료로 해석하면 안 된다. 앞선 NNNN stale_gap은 90초 freshness 초과로 watch만 무효화한 별도 사건이다.
- 일반 수집의 상세 telemetry는 비활성화되어 enqueue/dequeue·queue p95/p99·SQL/commit 지연은 복원 불가다. 원시 FE와 marks 순서 대조 및 장중 합성 시나리오에서는 판단 회귀를 확인하지 못했다. 이번에는 **생산 코드·전략·2초 gate를 수정하지 않았다**. 관련 **488개 unittest PASS, FAIL/ERROR/SKIP 0**, 장중 합성 A/B **12개 비교 PASS**(이전 publish 최적화 전/현재, 실행24회). 근거는 `build/paper_gap_20261021_024109/`와 단일 `CODEX_LAST_TASK_REPORT.md`에 있다. 다음 일반 장중 검증에는 기존 계측을 명시적으로 연결하는 최소 후속 작업이 먼저 필요하며, 이번 작업에서는 계측 기능도 임의 확장하지 않았다.

- **2026-10-21 Paper publish 후속 개선 / PARTIAL**: 이전 같은 호출 내 entries 재사용을 유지하고, 두 consumer에서 엔진 동작 없는 idle/error drain의 DB publish만 생략했다. tick/frame/gap/heartbeat/finish 직후 상태·interests·보호 정보 발행은 유지한다. `entered()`의 기존 NULL 필터를 SQL로 앞당겨 미진입 행과 Rotation 상세 JSON을 읽지 않는다. 전략·2초 gate·FIFO·commit/schema 변경은 없다. 고정 Paired view 1,000회 SELECT는 이전 14,000→6,000에 이어 이번 6,000→4,000이다. 2.25초 WATCHING idle publish 19→2, error idle 19→0. 관측 밀도 수정 후 4회 중 2회 전량 처리/2회 지연 실패이며 수정 전도 1회 전량 처리되어, 실행 변동과 실장 안정성은 미확정이다.
- 관련 **488개 일괄 PASS, FAIL/ERROR/SKIP 0**. 결정론적 전체 DB/상태와 이벤트마다 interests/protection 동일, 두 runtime의 idle·오류·stale heartbeat·finish 비교 및 40 tick/60ms 입력의 DB/공개 상태/commit 횟수 동일성을 확인했다. 실제 수집/API/Windows 종료/EXE 빌드는 수행하지 않았다. 이번 근거는 `build/paper_publish_20261021_004858/`와 `CODEX_LAST_TASK_REPORT.md`, 직전 484개 검사/첫 중복 조회 개선 근거는 변경하지 않은 `build/paper_queue_20261020_235440/`다. 게임 병행은 기본 실장 안정화 이후 별도 과제다.

- 당시 main/HEAD는 6895584e10903be80d480b67250926ac5ab780a4였다. 장중 조사 보존 기준 648개 및 publish 작업 619개 원본/검증 파일의 비교는 각각 build/paper_gap_20261021_024109/preservation_after.json과 build/paper_publish_20261021_004858/preservation_after.json에 있다. 두 작업 모두 실제 API/주문/Windows 종료/EXE 빌드·git add/commit/push를 하지 않았다.


## 2026-10-16 — v2.6.3 소스 Opening 오류 진단 / 실패 증거 보존 안정화

- Opening 분석에 한해 실제 TEMP 볼륨에서 DB+WAL 복사량과 max(64MiB, 10%) 여유분을 검사한다. 공간 부족·실제 OSError의 단계, errno/WinError, 정제 stack 위치와 연쇄 예외를 새 run의 `radar/opening_hour_diagnostics.json`에 atomic 저장한다. 자유형 메시지·소스 줄·locals는 제외하고 임시 파일은 자신의 생성분만 정리한다. 기존 연구 identity·DB schema·수집/전략 정책은 유지한다.
- 정상 ZIP 필수 15종을 유지한다. `opening_failed_before_database_v1` 진단 정책만 Opening DB 부재 + 동일 run/date의 영속 오류 근거를 허용하며, manifest에 부재와 export omissions를 명시한다. 필수 Radar/Paper/Rotation 원본·integrity·identity·재export 비교·CRC/SHA·개인정보·provenance 검증 실패는 운영 완료를 차단한다. 연구 FAILED와 보존 VERIFIED를 분리하고 최초 publisher/진단 재시도 예외를 각각 보존한다. 빈 DB 생성이나 원본 수정은 없다.
- 최초 회귀 109개 중 108 PASS/1 FAIL은 새 진단 필드에 대한 이전 기대값이었다. 확대 277개 중 276 PASS/1 FAIL은 Controller 전체 바이트 불변 검사였다. 허용된 Opening 후처리 함수만 비교 예외로 하고 나머지 Controller·전략·스케줄 비교를 유지했다. 최종 영향 범위 **169 PASS, FAIL/ERROR/SKIP 0**. 새 실패·공간·archive 회귀는 22개이며 합성 DB, 모의 디스크 오류, mock OS만 사용했다. 근거: `build/opening_preservation_20261016_040342/`.
- 10월 15일 정확한 OSError는 미확정이며 디스크 부족은 유력한 추정이다. Paper 2초 지연은 별도 후속 작업이다. 실제 원본 run/cache는 재분석·ZIP 재생성하지 않았고 live·주문·Windows 종료·EXE 재빌드·Git commit/push를 하지 않았다. v2.6.3 표시와 기존 EXE를 유지하며 수정은 재시작한 소스에만 적용된다.

## 2026-10-15 — Market Radar v2.6.3 / GUI integration 2 / archive schema 2

- 연구 판정과 안전한 운영 종료 판정을 분리했다. Paper/Rotation/coverage FAIL은 그대로 보존한다. 계획된 65분 수집, clean shutdown, worker/DB 종료, 필수 증거·원본 해시·ZIP 검증·flush, 사용자 활성화와 open+70분을 만족하면 검증된 FAILED 진단 ZIP도 PC 종료를 허용한다. 취소·미시작·중도 중단·필수 증거/원본/ZIP 오류는 차단한다. 종료 없는 테스트와 `/a` 실패 경고·한 번만 요청하는 동작을 유지한다.
- 실제 10월 14일 run 폴더는 `run_804c9f63ce6348c3802c767d88b87469`이며 요청문 경로의 누락 문자와 Controller run ID `7f569cbc615448aabf91695ef8fe6605`를 대조했다. 9,282 snapshots / 116 candidates, clean shutdown, WS peak 133·slot unavailable 0·WS gap/raw drop 0을 확인했다. Paper marks 105개, 진입 0개, 09:31:35.633873 ET의 processing delay와 마지막 가격 이후 2.200초 간격을 확인했다. 이 차이는 당시 큐 대기 실측값이 아니다.
- 10월 13일은 09:31:02.553447 ET의 같은 오류, Paper marks 479개·진입 0개였다. 두 날 모두 원시 가격 prefix와 기록 가격의 순서·시각·값이 일치한다. 원시 508/113 tick 재생에서 2초 오류는 재현되지 않아 CPU/디스크/SQL/스케줄링 근본 원인은 미확정이다. 최초 13일 replay의 sidecar_error 1회는 재검사와 최종 재생에서 재현되지 않았으며 결과 파일을 숨기지 않았다. 운영 2초 gate·SQLite journal/synchronous·전략은 바꾸지 않았다.
- opt-in bounded 계측으로 실제 수신/등록/꺼냄/처리 완료 시각, queue wait/depth/peak, v1/v2 처리, SQL 실행·행 읽기·실제 commit을 기록한다. 평균/최대/count는 관측값이며 live p95/p99는 고정 크기 1.08 비율 histogram의 상한 근사다. 첫 오류 주변 64개를 보존하고 5초마다 별도 writer로 저장하며 종료 시 join한다. 일반 Regular/Capacity/독립 runner는 계측 기본 OFF다.
- CSV 누락은 TC 회사명 `TOKEN CAT LIMITED SPON ADS EACH REP 4800`의 TOKEN 오탐이었다. candidates 1행/snapshots 8행/Opening 후보 1행의 설명 JSON(Opening은 두 열)이 걸렸다. 원본은 보존하고 ZIP 복사본의 해당 설명 값만 `[REDACTED]`로 바꾼다. strict scanner를 그대로 적용하며 실제 인증값도 제거/차단한다. 가격·식별·분봉은 수정하지 않는다. 행/열·원본 SHA256·변환·보존 경로를 manifest에 남긴다.
- 실제 장애 복사본에서 **35개 항목, 누락 0개**의 v2 진단 ZIP을 생성했다. 로컬 원본 39개 해시와 정제 결과를 재검증했으며 연구는 FAILED, 보존은 VERIFIED다. 실제 worker 상태는 역사적 lifecycle을 재구성한 offline 검증이며 당시 OS 종료를 재현한 것이 아니다. 정상 합성 ZIP도 35개 항목으로 별도 검증했다.
- Opening all 325/eligible 289/full 60m 49, 실제 분봉 7,378개, RVOL 1.4329%, stale opportunity 20.5784%를 그대로 유지한다. 발견 후 eligible 10,935분은 구독 확인 5,577/경계 209/미구독 5,149분이며, 구독 확인 OHLCV 4,238/5,577=75.9907%다. 명시적 슬롯 부족은 0분이다. 캐시 분봉 부족을 WS 틱 유실이나 슬롯 부족으로 바꾸지 않는다.
- 오프라인 unittest **642개 고유 검사 통과**(641개 격리 일괄 실행 + 기본 경로를 확인하는 읽기 전용 1개 별도 실행), 실패/오류/skip 0. 기존 main 회귀 스크립트 **12개 exit 0**. 정상 합성 ZIP·실제 장애 복사본의 진단 ZIP을 CRC/SHA256/identity/원본 해시로 검증했다. 계측을 켠 실제 PairedRuntime·SQLite·writer 스레드 합성 smoke도 통과했다. 실제 API/Windows 종료 호출은 0회다. 최종 취소 상태 보완 후 OS 경계·GUI 19개, 정제 내역 순서 보완 후 보존·민감정보·계측 17개를 추가 재실행해 모두 통과했다. 보관 폴더의 두 ZIP도 새 Python 프로세스에서 다시 검증했다.
- 첫 전체 실행은 642개 중 639개 통과였다. 1개는 격리 APP_ROOT와 원래 기본 경로 비교 차이, 1개는 허용된 Controller 계측 연결의 바이트 변경 검사, 1개는 합성 Controller의 Opening 후처리 일시적 INCOMPLETE였다. 기본 경로는 원래 경로의 읽기 전용 검사로 분리했고 Controller 비교는 계측 연결 두 추가부만 제외한 나머지 전체 바이트를 계속 확인한다. Opening 검사는 단독·최종 전체 재실행에서 통과했으며 원인이 확정되지 않아 제품 코드를 추정 수정하지 않았다. 기존 GUI main 테스트의 120초 대기는 sandbox 밖 소스 폴더에 임시 폴더를 만들던 경로였고 테스트 scratch만 격리해 해결했다.
- 원본 84개(run DB/WAL/SHM/CSV/log/ZIP와 두 cache DB), `.env`, PROJECT_DIRECTION DOCX/MD SHA256을 전후 대조했다. 작업 시작 시 PID 20688/24264를 확인했고 직접 중단/재시작하지 않았다. 최종 조회에서는 해당 앱 프로세스가 보이지 않았으며 종료 원인은 확인하지 않았다. 소스 실행 경로는 launcher → .venv-paddle → live_ui.py이며 기존 EXE는 재빌드/덮어쓰기하지 않았다. 현재 창에 자동 적용됐거나 오늘 무인 종료가 검증됐다고 주장하지 않는다. Git add/commit/push 없음. 결과와 재현 스크립트: `build/market_radar_one_hour/verification_20261015/`.


## 2026-10-14 — Market Radar v2.6.2 / GUI 첫 1시간 자동 실행 integration 1

- 기존 Market Radar GUI의 기본 OFF 체크박스와 종료 없는 테스트 옵션. Regular/150/Paper/Rotation/Opening ON, 실제 주문 OFF. 동일 Controller 한 개를 사용하며 독립 EXE 실행 없음.
- open+65분 정상 stop, 후처리·ZIP이 늦어지면 대기. 성공/worker·파일 종료/ZIP 재검증 및 open+70분의 UTC·monotonic 조건 후 `shutdown.exe /s /f /t 60` 한 번 요청. `/a` 취소 실패·불확실한 OS 결과·상태 기록 실패의 pending을 보존한다. OS 명령 대기는 10초다.
- Runner v1.0.1 artifacts/diagnostics/quality policy 2를 그대로 재사용한다. 구독 표본 coverage 기준·기존 Regular Validator FAIL을 유지하며 성공/FAILED 진단 ZIP을 구분한다. 기존 Collector/strategy/schedule/storage/Validator와 runner 코드는 변경하지 않았다.
- GUI 설정 원복·별도 실행 DB·중복 실행 방지·취소/닫기·GUI 파일 작업 대기. StockHelper.spec의 Kiwoom 계약/시간대/달력 data 포함과 psutil 의존성을 명시했다.
- 기존 unittest 회귀 597개와 신규 GUI 통합 테스트 28개(총 625개 고유 검사)를 여러 실행에 걸쳐 통과했다. 별도 Stock Helper 회귀 main 스크립트 12개도 exit 0이다. 최초 전체 실행은 619개 중 618개 통과, 1개는 테스트 APP_ROOT 격리 때문에 기본 경로 비교가 실패했다. 해당 읽기 전용 경로 검사를 원래 경로에서 재실행해 1/1 통과했다(실제 data 쓰기 차단 유지). 최종 변경 후 신규 28개와 Dashboard 27개를 다시 실행해 55/55 통과(31.032초), 실패/오류/skip 0. GUI 한글 렌더링·기본 OFF·옵션 적용/복원도 확인했다. 실제 시장 수집·Windows 종료·EXE 재빌드는 수행하지 않았다. 검증 로그/JSON/GUI 캡처는 `build/market_radar_one_hour/verification_20261014/`에 보관한다. 실행 중인 기존 pythonw 프로세스를 유지했고 StockHelper.exe를 재빌드하지 않았다.

### 이전 HANDOFF의 독립 실행기 검증 근거 보존

## 독립 One Hour Research Runner v1.0.1 소스 / 테스트 EXE v1.0.1

- 첫 실제 실행은 ET 2026-10-13, dist/OneHourResearch/data/one_hour_runner/run_ed65d7836036428e803dbe2bc35e6140, controller run_id=57600423f8434165bfb7e00b8c3e35c5이다. 09:31:02.553447 ET에 Paper/Rotation WATCHING이 paper_processing_delay로 INVALIDATED됐고 c.paper.error가 최종 ZIP 전 검사를 차단했다. Radar는 예정된 10:35까지 수집·정상 종료했다. 거래 진입은 0건이며 오류 세션을 정상 0거래로 해석하지 않는다.
- **기존 Market Radar v2.6.1, Paper/Rotation 엔진·runtime·저장 설정·전략·2초 제한은 변경하지 않았다.** 실행기 영역에 재생 측정 도구와 실패 진단 ZIP을 추가하고 품질 정책을 2로 보완했다. [운영·측정 안내](tools/one_hour_runner/README.md).
- 실제 입력 508개 tick을 복사본에서 수신 순서·간격대로 재생했다. 기존 DELETE와 별도 실험 DB의 WAL/FULL 모두 508개를 순서대로 처리했으며 자연적인 2초 초과는 재현되지 않았다. 기본 큐 대기 평균/p95/p99/max=49.059/217.822/342.665/431.606ms, Paper/Rotation commit 평균=3.224/3.289ms였다. 저장 비용은 확인했으나 당시 CPU·디스크·Radar/REST 부하와 enqueue 시각이 없어 근본 병목은 미확정이다. WAL은 운영에 적용하지 않았다.
- quality policy 2는 전체 후보 관측 범위와 실제 구독 표본을 분리한다. 기존 4,230/10,500분(40.29%)은 유지한다. 10,500분 중 구독 확인 5,154분/경계 167분/미구독 5,179분이며 명시적 슬롯 부족 근거는 0분이다. 구독 확인 구간의 OHLCV 3,943/5,154분(76.50%)도 기준 미달이다. 전체 95%·종목별 90%·연속 결측 2분 기준을 구독 확인 분모에 유지하고 종목별 fresh receipt도 검사한다. 미구독을 장애나 슬롯 부족으로 단정하지 않는다.
- Paper/Rotation 및 export 검증 실패는 별도 one_hour_diagnostic ZIP으로 보존한다. FAILED/자동 종료 불가, 오류 이벤트·원래 Validator·범위·누락 목록과 allowlist CSV/JSON만 포함한다. CRC/SHA256/identity·민감정보 검사 후 공개하며 DB/WAL/cache/로그/.env는 제외한다. Paper/Rotation 플래그뿐 아니라 공개 view의 Rotation ERROR도 검사한다. 진단 ZIP은 성공 ZIP 검증을 통과하지 못하고 PC 종료로 이어지지 않는다.
- 실제 장애 자료의 별도 복사본에서도 진단 ZIP 34개 항목을 생성·재검증했다. [진단 ZIP](build/one_hour_runner/stabilization_20261014_005110/actual_incident_copy/one_hour_diagnostic_2026-10-13_57600423f8434165bfb7e00b8c3e35c5.zip). 원본 run 폴더에는 추가 출력하지 않았다. sidecar_error의 당시 메모리 값은 미기록이므로 복원 진단에는 null로 남겼다.
- 최신 배포 검증 근거: [빌드·597개 회귀·EXE smoke·보존 기록](build/one_hour_runner/predeploy_v1_0_1_20261014_020318/). 기존 Market Radar 관련 479(82.141초), runner 118(기존 96+신규 22, 200.625초), 총 597/597 통과, 실패/오류/skip 0. 추가로 EXE에서 추출한 실행기 코드로 118/118 통과했다. 실제 socket/Windows shutdown 경계를 차단한 합성 검증이며 live 검증이 아니다. 이전 안정화 측정·검증 이력은 CHANGELOG에 보존한다.
- 최신 EXE는 `dist/OneHourResearch_v1_0_1_TEST/OneHourResearch.exe`, SHA256 `d5fb809ccf683417648ffad5729f677c076d2b9fe527191568f0111d7c866bff`다. 기존 build.ps1/spec/requirements와 Python 코드를 수정하지 않고 별도 작업 폴더의 임시 spec으로 COLLECT 이름·프로젝트 위치·replay_profile hidden import만 지정했다. 내장 실행기 소스 11개(entry 포함)와 현재 소스의 code object가 일치하며 프로젝트 모듈 55개도 대조했다. GPU/OCR 의존성·인증파일·실제 데이터는 포함하지 않았다.
- 새 EXE GUI/headless dry-run은 모두 exit 0, COMPLETE_NO_SHUTDOWN, 정상 ZIP 36개 항목과 내부 Review 8개 항목 검증 PASS다. EXE 추출 코드의 별도 합성 실패 5경로(Paper/Rotation/품질/ZIP 쓰기/ZIP 검증)를 확인했고, 앞의 3경로는 34개 항목 FAILED 진단 ZIP을 보존한다. 모든 실패 경로에서 종료 요청 0건이다. 실패 주입은 외부 harness이며 배포 EXE의 실패 CLI 모드가 아니다.
- 프로그램 v1.0.1 / manifest·summary 1 / diagnostic 1 / quality policy 2. 기존 `dist/OneHourResearch/`의 v1.0.0 SHA256 `cbbe5f1ea4b605c93cae0cdc1dffbe516153979c5c12b98cb3101cd05a13de42`, 실제 데이터·방향 문서·바로가기는 보존한다. 이번 빌드 작업은 안내·상태 문서 5개만 갱신하며 원본/소스 전후 비교는 `final_validation.json`에 기록한다.
- 다음 운영 작업: 운영자가 새 EXE 폴더의 인증을 별도로 설정하고 개장 30분 전까지 **`--no-shutdown`**으로 첫 실제 시장 테스트를 진행한다. 기존 cache/data/.env는 복사하지 않았으므로 새 폴더는 별도 수집 환경이다. v1.0.1 실제 시장 수집, 지연 장애 해소, 실제 Windows 종료는 미검증이다. 이번 작업에서 실제 API·자동 종료·commit/push는 실행하지 않았다.


## 2026-10-14 — One Hour Research Runner v1.0.1 별도 EXE 재빌드·배포 전 검증

- 기존 `dist/OneHourResearch/`의 v1.0.0 EXE와 실제 수집 데이터를 보존하고 `dist/OneHourResearch_v1_0_1_TEST/OneHourResearch.exe`를 생성했다. SHA256 `d5fb809ccf683417648ffad5729f677c076d2b9fe527191568f0111d7c866bff`. Python 코드·기존 build.ps1/spec/requirements는 변경하지 않았다. 별도 build 작업 폴더의 임시 spec에서 프로젝트 위치·COLLECT 이름·독립 replay_profile hidden import만 지정했다.
- 고정 의존성의 전용 최소 venv로 빌드했다. 내장 실행기 소스 11개(entry 포함)의 code object가 현재 소스와 일치하며 프로젝트 모듈 55개도 대조했다. diagnostics·quality policy 2·replay_profile이 포함됐다. GPU/PaddleOCR/EasyOCR/Torch, .env, 원본 DB/cache/export/log/ZIP은 배포에서 제외했다. 수집된 인증값을 읽어 비교하지 않고 빌드 입력·파일 목록·알려진 민감 literal 패턴을 검사했다.
- 전체 회귀 **597/597 통과**: Market Radar 관련 479(82.141초), runner 118(기존 96+신규 22, 200.625초), 실패/오류/skip 0. 추가로 EXE에서 추출한 실행기 코드의 회귀 **118/118 통과**, 실제 network/Windows shutdown 시도 0.
- 실제 EXE GUI/headless `--dry-run`은 각각 exit 0. 독립 cwd와 정리한 환경에서 동작하고 모듈 경로는 배포 폴더 안이었다. GUI STARTING→ARMED→COLLECTING→FINALIZING→COMPLETE_NO_SHUTDOWN 표시와 캡처를 확인했다. 각각 정상 ZIP 36개 항목, 내부 Review ZIP 8개 항목, SHA256/CRC/날짜/ID/allowlist/내부·외부 동일성 검증 PASS. v1.0.1·quality policy 2·합성 자료·자동 종료 비활성화를 확인했다. 원래 full-session Validator FAIL은 그대로 보존한다.
- EXE 추출 코드에 합성 Paper 오류·Rotation 오류·품질 미달을 주입해 각각 34개 항목 FAILED 진단 ZIP을 생성·재검증했다. 오류 원인·원래 Validator·자동 종료 불가 상태를 보존하며 정상 ZIP 검증기는 이를 거부한다. ZIP 쓰기·검증 실패에는 ZIP을 공개하지 않고 ERROR를 유지했다. 5경로 모두 mock 종료 요청 0, 실제 API/종료/.env 읽기 0. 실패 주입은 외부 harness이며 배포 EXE의 별도 실패 CLI가 아니다.
- 근거는 `build/one_hour_runner/predeploy_v1_0_1_20261014_020318/`의 package_audit/project_code_audit, 회귀 JSON, exe_smoke_results, frozen_failure_results 및 final_validation.json에 보관한다. 기존 배포·실제 데이터·방향 DOCX/MD·바탕화면 바로가기는 보존한다. 이번 작업의 프로젝트 변경은 안내·상태 문서 5개뿐이며 기존 미커밋 변경을 보존했다.
- 첫 실제 시장 테스트는 새 배포 폴더에서 운영자가 인증을 별도로 설정한 뒤 `--no-shutdown`으로 실행한다. Regular Session/150 WS/Paper v1/Rotation v2/Opening Hour/XNYS open+65분 조건은 유지한다. v1.0.1의 실제 시장 안정성, 기존 Paper 지연의 해소, 실제 Windows 종료는 검증하지 않았다. 실제 API·Windows 종료·git add/commit/push를 실행하지 않았다.

## 2026-10-14 — One Hour Research Runner v1.0.1 소스 안정화

- 2026-10-13 ET 실제 실행에서 09:31:02.553447 paper_processing_delay와 진단 ZIP 누락을 조사했다. 원시 tick과 Paper marks 479개가 순서·시각·가격까지 일치하며 이후에도 수신은 지속됐다. 다음 원시 tick과 오류의 차이 2.047033초는 기록 대조값이며 당시 큐 대기 실측값은 아니다.
- offline replay_profile을 추가해 기존 PairedRuntime/엔진과 508 tick을 복사본에서 재생하고 enqueue/queue/dequeue·Paper·Rotation·SQL read/write·실제 commit의 평균/p95/p99/max를 측정했다. 기본 DELETE와 실험 WAL/FULL 모두 508개를 순서대로 처리했고 자연적인 2초 장애는 재현되지 않았다. 기본 큐 대기 49.059/217.822/342.665/431.606ms, WAL 비교 10.670/50.445/146.188/160.546ms다. 저장 비용은 확인됐지만 당시 CPU/디스크/Radar 부하가 없어 근본 병목은 미확정이다. WAL은 운영에 적용하지 않았다.
- quality policy 2는 미구독/구독 경계/발견 전/증거 미상/구독 중 캐시 부족을 분리하며 슬롯 부족은 명시적 근거만 인정한다. 전체 후보 4,230/10,500분(40.29%)은 범위 정보로 보존한다. 구독 5,154분/경계 167분/미구독 5,179분, 구독 중 완전 OHLCV 3,943분(76.50%)으로 이번 자료는 여전히 FAIL이다. 구독 확인 분모의 전체 95%·종목별 90%·연속 결측 2분과 종목별 fresh receipt 검사를 유지한다.
- Paper/Rotation 또는 검증 실패에 별도 FAILED 진단 ZIP을 보존한다. 원래 Validator, 오류 이벤트, 사용 범위·누락 사유와 allowlist export를 담고 민감정보·CRC·SHA256·identity를 검사한다. DB/WAL/cache/로그/.env는 제외한다. 진단 생성·검증 실패 모두 자동 종료를 허용하지 않는다. SQLite 조회는 임시 DB/WAL 복사본을 사용한다. PairedEngine 공개 view에만 나타나는 Rotation ERROR도 실행기에서 차단한다.
- 수정 전 기존 575/575 통과. 최종 **597/597 통과**: 기존 Market Radar 관련 479(96.062초), 기존 runner 96+신규 22=118(264.421초). 실패/오류/skip 0, 실제 네트워크/Windows 종료 시도 0. 변경된 정책의 감사 fixture 두 개는 명시적으로 구독 상태를 설정해 구독 중 결측 검사 의미를 유지했다. 실패 ZIP 검증은 새 진단 검증기를 사용한다.
- 실제 장애의 별도 복사본에서 34개 항목 진단 ZIP을 생성·검증하고 ERROR/자동 종료 차단을 확인했다. 원본 run 폴더에는 출력하지 않았다. 근거는 build/one_hour_runner/stabilization_20261014_005110의 profile_v2_default/profile.json, profile_v2_wal_full/profile.json, actual_quality_v2.json, 회귀·보존 JSON과 actual_incident_copy의 ZIP에 있다.
- 원본 data/dist 데이터 306개(6,991,016,367 bytes)의 목록·SHA256·크기·mtime, 보호 코드·방향 DOCX/MD·기존 EXE의 해시가 작업 전후 동일하다. 변경 범위는 runner 8개, 테스트 2개, 상태·기술 문서 4개이며 기존 미커밋 변경을 보존했다.
- 기존 Market Radar v2.6.1·Paper/Rotation 엔진/runtime/저장 설정·전략·2초 제한·PROJECT_DIRECTION DOCX/MD는 변경하지 않았다. 실행기 소스 v1.0.1 / manifest·summary 1 / diagnostic 1 / quality policy 2다. 기존 배포 EXE와 바로가기는 그대로이며 EXE는 v1.0.0이다. 사용자 data를 덮어쓰지 않는 별도 경로 재빌드와 오프라인 smoke가 필요하다. 실제 시장 안정성 검증은 완료되지 않았다. 실제 API/Windows 종료/commit/push를 실행하지 않았다.



## 2026-10-13 — One Hour Research Runner v1.0.0 감사 보완 반영 재빌드

- `tests/one_hour_runner_audit_test.py`, `__main__.py`, spec, build.ps1, requirements.txt와 실제 entry→Runner→export/quality 경로를 직접 재검토했다. 추가 코드 문제는 발견하지 않았고 이번 빌드 작업에서 애플리케이션·테스트·빌드 소스를 수정하지 않았다. Market Radar v2.6.1과 전략·schema·방향 문서는 그대로다.
- 앞선 감사 보완은 종료 요청 직후 시계/저장 예외의 예약 가능 상태와 취소 실패 경고 유지, 첫 60분 WS/구독/관측·발견 후 OHLCV coverage 별도 검사, 미달 시 진단 ZIP 보존·PC 종료 차단이다. 합성 TP/SL·Rotation switch·post-exit·CSV/SQLite 일치와 ZIP 재검증 테스트도 포함됐다. 품질 정책 1과 manifest/summary schema 1은 전략 수익성 PASS가 아니다.
- 전체 회귀를 다시 실행해 **575/575 통과**: Market Radar 관련 479(111.500초), 실행기 기존 62+감사 34의 96(176.406초), 실패/오류/skip 0. 실제 socket 연결과 Windows shutdown 실행을 금지한 audit hook의 시도 계수는 모두 0이다.
- 기존 배포 폴더 943개 파일이 이전 PyInstaller TOC+사용 안내와 정확히 같아 사용자 파일이 없음을 확인한 뒤, 8개 직접 의존성 고정 버전/pip check가 일치하는 전용 venv에서 `build.ps1 -SkipInstall`로 재빌드했다(76.046초). 네트워크 설치·기존 Stock Helper 빌드 호출은 없다. 최신 EXE는 `dist/OneHourResearch/OneHourResearch.exe`, SHA256은 `cbbe5f1ea4b605c93cae0cdc1dffbe516153979c5c12b98cb3101cd05a13de42`다.
- EXE 내부 실행기 9개 모듈의 실행 코드가 현재 소스와 일치하고 새 quality 모듈이 포함됐음을 확인했다. 추출한 EXE 코드로 감사 테스트 **34/34**를 재실행했다(37.995초, 고유 테스트 수에 중복 합산하지 않음). 시계 예외·취소 실패·품질 미달·거래/CSV 변조 차단을 mock/합성 자료로 검증했다.
- 실제 EXE GUI/headless `--dry-run` 모두 exit 0. Python PATH/PYTHONHOME/PYTHONPATH 없는 별도 cwd, 번들 내부 module search path, `frozen=true`, 창 표시와 `COMPLETE_NO_SHUTDOWN`을 확인했다. 두 합성 실행 모두 통합 ZIP 36개/Review ZIP 8개, CRC/manifest SHA256/ID·날짜/중복 byte 일치 PASS, 첫 60분의 120개 구간과 60개 분봉 coverage PASS, shutdown disabled 및 예약 파일 미생성이다. 기존 official-close Validator FAIL과 full_session=false는 그대로 유지했다.
- 배포 폴더는 943개 파일/168,772,372 bytes다. `dist/OneHourResearch_v1.0.0_20261013_163923.zip`(70,105,316 bytes)과 `.zip.sha256`을 생성하고 943개 항목 CRC·SHA256을 번들과 대조했다. ZIP SHA256은 `add9242ed97a857dec7b296a24e2f8ac6413a04867e87786c4b519af325fb4e1`다. 인증정보·원본 DB/data/log·테스트·OCR/Paddle/Torch/NH 실행 모듈은 포함하지 않았다. `.env`는 운영 PC에서 별도로 설정해야 한다.
- 기존 data 216개/5,813,929,270 bytes의 전체 SHA256·경로·크기·mtime, 방향 DOCX/MD와 기존 소스·앞선 미커밋 변경을 보존했다. 문서 갱신 전 174개 소스 해시가 모두 같았으며 이번 문서 변경은 HANDOFF/CHANGELOG의 현재 검증 기록뿐이다. `.env` 인증값 출력·편집은 하지 않았다. 증거는 `build/one_hour_runner/predeploy_20261013_163923_d685af/`에 두며 git add/commit/push는 하지 않았다.
- 남은 운영 검증: 새 PC에 폴더 전체 배포 후 `--no-shutdown`으로 개장 전 준비부터 open+65분까지 실제 Kiwoom 세션을 관측해야 한다. 실제 API/주문/Windows 종료와 다른 PC 검증은 실행하지 않았다. EXE는 NotSigned이며 기존 legacy 앱과의 원자적 상호 배제 제약도 유지한다.

## 2026-10-13 — One Hour Research Runner v1.0.0 / manifest·summary schema 1

- 기준 HEAD `6895584e10903be80d480b67250926ac5ab780a4`. Market Radar v2.6.1, research schema 4, Paper schema 2, Rotation extension 1, Opening Hour schema 1 및 기존 코드·fixtures·방향 DOCX/MD·기존 EXE 빌드를 보존했다. 신규 `tools/one_hour_runner/` 12개 파일과 `tests/one_hour_runner_test.py`, 안내/현재 상태 문서 4개가 범위다.
- 독립 PySide6 창과 공개 CollectorController 조합이다. 기존 CONFIG 정책에서 경로·Regular Session·150 slots만 지정하고 Paper v1·Rotation v2·Opening을 켠다. XNYS 공식 개장+65분에 공개 `stop()`을 요청하며 첫 60분만 분석한다. 공식 close 기준의 기존 protocol/Validator FAIL과 Paper 조기중단·post-exit 결측은 변조하지 않는다.
- 실행별 새 Radar/Paper/Rotation/Opening/Master DB와 기존 shared historical cache 정책을 사용한다. 단축 실행 요약에 계획/실제 구간·첫/마지막 관측·coverage·실패 사유 및 Controller UUID/기존 Master 내용 ID 매핑을 남긴다. legacy DB migration·새 REST/WS 구현·실제 주문은 없다.
- 통합 ZIP은 radar/paper_v1/rotation_v2/opening_hour/manifest/summary의 allowlist다. 원래 exporter가 DB 복사본에서 재생성한 모든 연구 결과와 원본 export를 대조하며, 기존 Radar Review ZIP은 동일 Radar export 7개만 중복 포함한다. 필수 파일/날짜/ID/CRC/SHA256/nested 동등성/민감 필드를 검증한 임시 파일만 atomic publish한다. 원본 DB/cache/.env/log는 공유 ZIP에서 제외한다.
- clean shutdown·모든 관련 thread 종료·정시 준비/시작/중지·실제 연구 관측·필수 export/Opening 후처리·ZIP 검증 후에만 `/s /f /t 60`을 1회 요청한다. UI 또는 `/a` 취소가 가능하다. late/clock/resume/WS gap/저장/분석/Windows 요청 오류는 종료를 차단하고 원본·실패 요약을 보존한다. 정리 중 강제 process kill은 없다. 취소 실패 시 실제 예약 유지 가능성을 표시하고 countdown은 monotonic 기준이다.
- offline 신규 **62/62 통과**(116.896초), 기존 **479/479 통과**(78.585초). 기존 Collector를 합성 provider로 개장 전 arm한 뒤 연구 gate/세 옵션/종료 순서를 확인했고, 별도 가속 모의 실행에서 기존 exporter/Validator/연구 모듈과 통합 ZIP을 검사했다. DST·휴장·조기폐장·+60/+65 경계·원래 FAIL·0거래·PARTIAL/EMPTY·누락/오염/손상·중복·취소 경합·오류별 shutdown 0회/성공 1회를 검증했다. Windows 명령은 모두 mock이다. 초기 신규 fixture의 ws_factory 누락과 신규 ZIP fsync 핸들 문제는 신규 파일에서만 수정했다.
- 전용 최소 venv + 로컬 캐시 wheel로 Windows onedir **`dist/OneHourResearch/OneHourResearch.exe` 빌드 성공**. 기존 빌드 스크립트는 사용하지 않았다. Python PATH/PYTHONHOME/PYTHONPATH를 제거하고 다른 cwd에서 EXE `--dry-run --smoke-dir ...` 합성 smoke를 실행했다. 화면의 모의 실행/자동 종료 비활성화와 ZIP 검증을 확인했다. OCR/Paddle/Torch 및 인증파일/실제 DB를 배포물에서 제외한다. source Python 설치를 제거한 다른 PC나 실제 Kiwoom/NH/live shadow/Windows 종료 동작은 미검증이다.
- 기존 추적 파일 159개의 시작 SHA256과 원래 data 216개(5,813,929,270 bytes)의 목록·크기·mtime를 기록했다. 허용한 문서 4개 외 기존 파일/방향 DOCX·MD·fixtures를 보존하고 원래 data의 목록·크기·mtime가 동일함을 확인했다. DOCX SHA256은 `eb06f5add0fde0f5fecc424e00977932831d2f6d3279f10e9d67fe2c947641ef`다. 실제 인증값 출력·수정, git add/commit/push는 하지 않았다. 빌드/ZIP/로그/화면/테스트 산출물은 build/dist ignore 범위다.
- 제약: 기존 legacy 앱은 전역 mutex에 참여하지 않는다. 서로 다른 폴더의 legacy 앱이 뒤늦게 시작하는 경우까지 원자적 상호 배제를 보장할 수는 없어 알려진 프로세스 감지 시 중지·종료 차단한다. 실제 운용 시 Collector 하나만 실행한다. 첫 운영 검증은 `--no-shutdown`으로 수행하며, 단축 실행의 VERIFIED를 full-session 준수나 전략 수익성 PASS로 해석하지 않는다.
- 최종 감사에서 문서 4개 외 기존 추적 155개 SHA256 동일, 신규 파일 13개, data 목록·크기·mtime 동일, 상대 링크·버전·민감 대입 패턴·공백·`git diff --check` 및 staging 없음 확인. 최종 EXE는 exit 0, STARTING/ARMED/COLLECTING/FINALIZING/COMPLETE_NO_SHUTDOWN 화면과 36항목 합성 ZIP 검증을 기록했다. module search path는 전부 배포물 `_internal`이고 배포물은 943개 파일/168,759,873 bytes다. 인증파일·DB·log·OCR/Paddle/Torch/NH 실행 모듈이 없다.

## 2026-10-12 — Market Radar v2.6.1 / Paper schema 2 / opening_hour_v1_1

- GitHub checkpoint `66589272e013644a94d2e206c4cddbcfa1951383` 기준의 연구 evidence 보존 보완이다. Paper v1 후보/진입/TP/SL, Rotation material evidence dominance, Opening 60분 계산식·경계, Current/Emerging 순위와 수집 정책은 유지했다.
- Paper marks에 monotonic AUTOINCREMENT observation_id와 run ID를 두어 동일 시각 복수 가격·exact duplicate를 모두 도착 순서로 보존한다. live의 연속 exact duplicate 판단 생략은 유지한다. 종료 후 horizon은 같은 run/date/ticker의 target 이후 tolerance 안 첫 timestamp/observation_id 표본이며 Radar와 같은 기본 30초를 사용한다. sample timestamp/offset/selection/ID/tolerance·NULL 사유를 DB/CSV에 남기고 보간·pre-target·최고가격 탐색은 하지 않는다. Baseline/sidecar export에 marks/horizons를 보존한다.
- Paper 공통 schema는 1→2, rotation extension은 1 유지다. 기존 schema 1은 DB/WAL 임시 복사 검사 후 migration 필요 오류로 거부하며 자동 변경하지 않는다. 별도 복사본 offline migration은 후속이고 유실된 틱은 복원하지 않는다. 작업 전 data에서 실제 Paper/Opening DB가 없음을 확인했다. 공통 복사 검사 helper를 분리해 기존 source 검사도 동일 방식으로 유지한다.
- Opening feature는 `opening_hour_v1`→`opening_hour_v1_1`, DB schema는 1 유지다. 물리 source DB/cache/Paper 경로를 logical fingerprint·metadata에서 제외하고 동일 자료의 root 이동에도 ID·export를 유지한다. 내용 변경은 새 ID이며 기존 feature dataset을 덮어쓰지 않는다. source_cache_path 열은 신규 dataset에서 NULL이다.
- 모든 raw 후보·제외 유형·미구독·결측을 보존하고 기존 stock_type_policy로 analysis_eligible/제외 사유를 기록한다. all/eligible/excluded 수·사유별 수·양쪽 coverage를 분리하며 outcome median/표본은 eligible 기준이다. 미구독·결측을 분모에서 제거하지 않는다. COMPLETE/PARTIAL/EMPTY를 관측 coverage로 정의하고 처리 오류 INCOMPLETE와 구별한다. Dashboard는 all/eligible 수와 full coverage를 표시한다.
- Offline **479 통과**: 신규 evidence 31 + 기존 448(Opening 64, Paper 59, Rotation 30, NH 48, Radar/Kiwoom/UI/scheduling/Validator/audit 247). 같은시각 TP/SL·중복·표본 선택·WAL 보존·경로 이동·metadata·eligible/all 및 checkpoint replay 전체 결과 동일을 검증했다. 첫 병렬 회귀의 기존 slow-fetch UI 테스트에서 임시 DB 정리 WinError 32가 1건 있었고, 코드 변경 없이 247개 묶음 독립 재실행 PASS다. 실제 REST/WS subscription/order **0**, live smoke·보관 데이터 신규 분석은 미실행이다.
- 실제 data 214개(5,813,926,042 bytes)의 SHA256/크기/mtime와 보호 코드·방향 문서 21개가 전후 동일하다. 사용자 교체본 PROJECT_DIRECTION.docx SHA256 `eb06f5add0fde0f5fecc424e00977932831d2f6d3279f10e9d67fe2c947641ef`, MD `50029336dea7d83563c4912d74a34509c8da35f7f3cf551e2d0ef496b0df840c`를 보존했다. .env/실제 인증값은 읽거나 수정하지 않았으며 git add/commit/push/reset/restore/checkout/clean도 실행하지 않았다.

- 최종 git diff --check·상대 링크·현재 버전·수정/추가 21개 파일의 인증값 패턴 검사 PASS. 사용자 방향 파일 2개를 제외한 작업 범위는 추적 수정 19개와 미추적 신규 2개다.

## 2026-10-12 — Market Radar v2.6.0 / Opening Hour Research v1 / opening hour schema 1

- 기존 candidate/discovery·완료 1m·shared minute cache를 read-only 분석하는 `opening_hour_v1`을 추가했다. 기본 OFF/Regular Session 전용이며 collector/Paper 종료 후에만 실행한다. Capacity/Manual 자동 OFF, 종료된 과거 자료는 별도 CLI로 명시적으로 분석한다. 추가 REST·WS subscription·backfill·실제 주문 **0건**, live strategy/ranking 입력 변경 없음.
- XNYS open~open+60분, 09:30~10:29 시작 1m 60개와 3m 20개/5m 12개, 늦은 발견 pre_detection, 원본 OHLCV·shape·anchor·break·HOD/LOD·구조 count·volume·구간 집계·7개 open snapshot·6개 detection horizon 및 first-hour outcome을 보존한다. 결측을 0으로 보간하지 않으며 incomplete aggregate는 NULL이다. cache가 없을 때 Bar에 없는 시가를 만들지 않는다. 공식 minute notional 부재로 NULL을 유지한다.
- 분봉 event/extrema는 1분 구간 정밀도다. gap 이전의 first break를 확정하지 않으며 분 중간 detection의 exact MFE/MAE는 NULL이다. 창 안의 완전 봉 부분 관측과 이전 완료 봉 extreme→후속 봉 extreme pullback/rebound 정의를 따로 기록한다. 해석 tag·BUY/SELL·점수·확률·패턴 추천 없이 기술통계와 표본 수만 제공한다.
- 독립 `data/opening_hour_research/opening_hour.db` schema 1과 ET date/research ID별 8 CSV+3 JSON export를 추가했다. 같은 evidence/version은 동일 ID/재현 가능한 export, 바뀐 evidence는 새 dataset으로 보존한다. created_at은 DB 최초 저장 UTC다. source DB/WAL 임시 복사본만 SQLite로 읽어 원본 DB/SHM/cache/export를 변경하지 않는다. Paper는 same day/run/ticker reference ID만 연결한다.
- Offline **448 통과**: Opening Hour 64, Paper v1 59, Rotation 30, NH 48, Radar/Kiwoom/Dashboard/scheduling/metadata/Validator/audit 247. 신규 전체에서 DB roundtrip/export 결정성·원본 bytes/mtime·WAL/SHM·no REST/WS/NH·CLI 합성 smoke·offscreen UI·collector/Paper 종료 순서를 확인했다. 새 종료 순서 fixture가 ws_factory를 제공하지 않아 최초 1건 실패했고 synthetic provider fixture를 보완 후 신규 64개 전체 통과했다. 기존 구현을 실패에 맞춰 변경하지 않았다.
- Paper/Rotation/post-exit 동일 fixture 결과는 research 전후 동일하다. `paper/`, `paper_rotation_v2/`, NH execution, Kiwoom provider, 기존 fixtures/tests, runtime/scheduling의 작업 전후 SHA256 동일. PROJECT_DIRECTION.docx SHA256은 `a92ad593d07d54475268c449374d0bbf1581cc55c9a681e74df3c77564e8175b`로 동일하고 방향 MD도 변경하지 않았다. 원본 data/환경 인증값을 입력·출력으로 사용하지 않았으며 실제 Kiwoom/NH/live shadow/보관 실데이터 신규 분석은 미실행이다. git add/commit/push 없음.
- 최종 `git diff --check`, 현재 버전·상대 링크·15개 신규/갱신 파일의 공백/인증값 패턴 검사 PASS. 임시 설정/DB를 사용한 Regular ENABLED·Capacity OFF의 UI 렌더를 확인했다. 기존 미커밋 변경을 포함한 status는 추적 수정 10개/미추적 항목 15개이며 이번 신규 status 항목은 Opening 모듈·CLI·test 3개다.

## 2026-10-12 — Market Radar v2.5.0 / paper_rotation_v2 / rotation extension schema 1

- 기존 `market_radar/paper/` 7개 파일, v1 fixture/test는 작업 시작 시 SHA256과 동일하게 보존했다. v1 전략·paper schema 1·원본 export를 유지하고, 별도 RotationEngine/RotationStore/PostExitTracker/paired consumer/UI/replay를 추가했다. 사용자 미커밋 NH/Paper/metadata 작업은 보존하며 git add/commit/push는 하지 않았다. PROJECT_DIRECTION.docx SHA256은 `a92ad593d07d54475268c449374d0bbf1581cc55c9a681e74df3c77564e8175b`로 전후 동일하다.
- 같은 frame 평가시각과 순서가 보존된 FE를 두 전략에 전달한다. v2만 양쪽 TOP10·양쪽 rank/완료 1m/5m return 우위·v1 confirmation·fresh/정상 WS·미거래 조건과 현재 약화 증거를 모두 확인해 **material evidence dominance**로 전환한다. signal < old exit < new entry 시각을 강제하고 새 종목의 과거 tick을 재사용하지 않는다. 각 전략에 1종목·일일 4회·재진입 금지를 독립 적용한다. 확률·실제 주문 구현은 없다.
- TP/SL/SESSION_END/SWITCH 및 무효 종료 뒤에도 원래 entry/TP/SL 기준의 가격경로·hit와 시각·TP 추가 상승/시간 순서상 반락·SL 회복·deterministic tag를 별도 sidecar에 보존한다. official close까지 기존 보호 입력을 연장하되 슬롯/rank 알고리즘은 유지한다. gap·조기 중지·restart는 INCOMPLETE, SESSION_END 뒤 창은 NO_POST_EXIT_WINDOW이며 후속가격이 decision에 전달되지 않는다.
- 별도 `paper_rotation_v2/review.db`와 trades/post_exit/marks/switches/comparison CSV 및 daily summary JSON을 추가했다. 동일 시작을 exact identity로 연결하고 v2 chain 수익률·새 종목 MFE/MAE·원래 TP 도달을 보존한다. 새 종목 exit와 exact 동일시각의 원래 종목 가격이 있을 때만 비교 손익을 산출한다. 비용·체결 가능성은 모델링하지 않는다. Baseline v1 / Rotation v2 UI 분리, v2 기본 OFF, Capacity 모두 OFF다.
- Offline 회귀: Paper v1 **59**, NH **48**, Radar/Kiwoom/Dashboard/scheduling/metadata/Validator/audit 전체 **247**, 신규 rotation **30** 통과(총 **384**). v1 단독 replay와 paired rotation ON/OFF의 baseline 결과 동일, 신규 paired replay 반복 동일, 공유 background consumer 및 offscreen UI 확인. 초기 신규 테스트에서 sidecar 읽기 검사 연결 미종료로 임시 DB 정리 실패 1건을 발견해 명시적 close로 수정 후 통과했다. 실제 live shadow·Kiwoom/NH smoke는 실행하지 않았고 실제 주문 호출 **0건**이다.
- 이전 v2.4.2의 8/59/48/208 통과, scheduling 5회 반복, 10,000회 metadata 교체 중 일시적 WinError 5 5건 재시도 복구, 원본 data 201개 크기/mtime 보존 사실은 아래 해당 버전 이력에 보존한다. 이번 작업의 원본 data/환경 인증값은 읽거나 편집하지 않았으며 테스트·replay·UI 이미지는 임시 폴더만 사용했다.

버전별 개발·검증 이력을 보존한다. 현재 실행법은 [README](README.md), 기술 계약은 [Market Radar README](market_radar/README.md), 현재 개발 상태는 [CODEX_HANDOFF](CODEX_HANDOFF.md)를 따른다.

날짜는 확인된 Git commit 날짜와 실제 검증 시각을 구분한다. 별도 릴리스 날짜·commit이 확인되지 않은 단계는 임의로 채우지 않는다. 프로그램 버전과 research schema 버전은 별개다. 아래 Known limitations는 각 버전 당시의 상태이며 이후 해결 여부는 다음 버전을 참조한다. 로컬 data 경로는 증거 위치이며 Git에 포함하지 않는다. 공용 tests_summary.json은 후속 실행으로 갱신되므로 과거 테스트 수는 당시 commit의 문서 기록을 근거로 한다.

## 2026-10-08 — Market Radar v2.4.2: Windows metadata 교체 오류 복구

- 수정 전 별도 scheduling 27개×8회는 통과했지만, lifecycle 저장 반복의 910번째 호출에서 `dashboard_run.json.tmp` → `dashboard_run.json`의 `os.replace()` WinError 5를 재현했다. 이전 controller PermissionError와 같은 오류 유형/저장 경로를 확인했으며 외부 점유 프로세스는 특정하지 못했다.
- controller lifecycle/최종 protocol JSON의 atomic replace에 Windows PermissionError 5/32/33만 25·50·100ms, 최대 3회 재시도를 추가했다. 영구 거부·다른 오류는 실패로 남기고 기존 공개 파일을 보존한다. 안전한 파일명/작업 단계/OS 번호를 오류 이벤트에 추가하며 예외 원문과 전체 경로는 표시하지 않는다. Capacity/scheduling 정책·허용 오차·Paper/NH/전략/schema는 변경하지 않았다.
- 수정 후 10,000회 연속 저장 중 실제 WinError 5 5회를 모두 복구했다. 신규 metadata 회귀 **8**, Paper **59**, NH **48**, 기존 Radar/Kiwoom/Dashboard/scheduling/Validator **208** 통과. scheduling 27개는 전체 회귀 외 별도 프로세스로 추가 5회 통과했다. 신규 테스트의 SQLite fixture 연결 미종료는 명시적으로 닫도록 수정 후 재검증했다. 실시간 연결/토큰 발급/주문 **0건**, 첫 live shadow는 미실행이다.
- `.env`·방향 DOCX/MD·NH 및 기존 Paper/runtime/UI/provider/scheduling/Validator 원본 보존. data 201개 파일 크기/mtime와 목록 동일. diff --check·상대 링크·인증값 대조 PASS. 기존 사용자 변경 유지, git add/commit/push 없음.

## 2026-10-08 — Market Radar v2.4.1: Paper 경계 조건 재검토

- Paper DB 복원을 기존 Kiwoom collector lock 확보 후로 옮기고, Paper 저장/종료 후 provider가 잠금을 해제하도록 했다. 시작에 실패한 중복 실행이 다른 실행의 OPEN을 INTERRUPTED로 바꾸는 위험을 제거했다. Capacity는 기존처럼 Paper를 시작하지 않는다.
- 최초 중지 요청시각을 비동기로 고정하고 중지 뒤 신규 frame/signal과 뒤늦은 tick을 차단했다. 장 종료 직전 수동 중지가 느린 shutdown 때문에 SESSION_END로 바뀌지 않는다. 동일 수신시각의 서로 다른 FE 가격은 순서대로 처리하며 동일 시각·동일 가격 중복만 제외한다. 전략/TP/SL/schema/순위 공식은 변경하지 않았다.
- Paper **59 통과**, NH **48 통과**, 기존 회귀의 scheduling 외 **181 통과**. scheduling 27개는 최초 및 일부 반복에서 간헐 실패했고 이후 같은 프로세스 5회·별도 프로세스 4회 통과했다. 실패 시 controller PermissionError, Paper OFF Capacity의 PREOPEN_WAIT 대기 실패/ERROR 종료를 관측했다. 정확한 파일 접근 원인은 미확정이며 통과한 반복만으로 해결됐다고 보지 않는다. 별도 추적 과제로 HANDOFF에 남겼다.
- 합성 replay 재현성 PASS. 실제 네트워크/토큰 발급/주문 0건. NH 파일 캐시 비활성화로 새 probe 프로세스마다 발급 알림이 발생할 수 있음을 문서화했으며 실제 인증값·캐시 설정·.env는 변경하지 않았다. 기존 사용자 변경 보존, git add/commit/push 없음.

## 2026-10-08 — Market Radar v2.4.0 / Paper Trading v1 / paper schema 1

- Experimental baseline strategy `paper_v1_baseline`을 별도 engine/policy/DB/consumer로 추가했다. 양쪽 TOP10 → Emerging → Current의 deterministic priority, 새 완료 1m 봉·양수 1m/5m momentum·watch 가격 초과·freshness·WS gate만 사용한다. RVOL/OR/HOD는 불변 근거로 저장하며 가중 점수/확률/시장 참여자 의도를 생성하지 않는다. **Strategy profitability = not established**.
- 신호 평가 시각 뒤 첫 유효 Kiwoom FE 수신가격으로 OPEN하고 +15%/-10% 첫 crossing의 관측가격으로 닫는다. 1종목/ET 일일 4회/동일 ticker 재진입 금지, MFE/MAE와 시각, SESSION_END/INVALID/INTERRUPTED 구분·재시작 복원·gap 원인/범위를 저장한다. 신호와 체결에 같은 snapshot을 사용하거나 threshold 가격으로 소급하지 않는다.
- Paper 탭 Enable 기본 OFF, 실행 전에 선택, Capacity Test 강제 OFF. 기존 WS raw sink와 worker의 확정 ranked rows를 opt-in으로 별도 bounded queue에 전달하며 snapshot UI 큐를 체결용으로 쓰지 않는다. provider/scheduling/slot/cache/rank/research schema 4/Validator 1은 그대로 유지했다. 앱 v2.4.0, Collector v2.1, Master schema 1, NH adapter v1.1.
- 별도 paper schema 1: sessions/trades/events/marks/horizons. 정확한 entry 이후 관측시각 일치만 post-session horizon에 사용하고 누락은 NULL이다. 전용 CSV/JSON export와 일일 통계, 비용 제외·전액 재투자 가정을 분리한 복리를 제공한다. 기본 계좌·NH 연결은 사용하지 않고 **actual orders = disabled / not implemented**, 실제 주문 호출 **0건**이다.
- Offline Paper **52**, NH **48**, 기존 Radar/Kiwoom/Dashboard/scheduling/Validator **208/7 suites** 통과. 합성 replay 동일 결과, UI polling 없이 동작하는 consumer의 평가 시각/첫 후속 tick과 offscreen UI 렌더를 확인했다. 합성/임시 DB 검증이며 실제 live shadow·기존 session DB replay·NH 재조회는 실행하지 않았다. 2026-10-07 clock_jump Capacity 자료는 수익성 표본으로 사용하지 않았다.
- 기존 미커밋 NH 변경을 유지하고 방향 DOCX/MD·.env·원본 DB/export/cache/ZIP을 편집·삭제하지 않았다. git add/commit/push/reset/restore/checkout/clean 없음. 다음 단계는 새 Regular Session의 명시적 live shadow다.
- diff --check·문서 상대 링크·인증값 대조 PASS. 방향/환경/NH/기존 provider·scheduling·연구 schema·Validator 해시를 보존했다. data 201개 중 200개 크기/mtime 동일, 기존 SHM 1개는 크기 동일/mtime 변동(원인 미확정)이며 복구·정리하지 않았다. Paper DB는 임시 폴더에서만 테스트했고 원래 data에 새 파일을 생성하지 않았다.

## 2026-10-08 — NH read-only adapter v1.1: 해외주식 계좌 조회

- 공식 gbstock openapi.json과 nhplug SDK balance/buyable_amount 예제를 기준으로 독립 계좌 probe를 추가했다. 운영 계좌별 미국/USD 잔고를 연속 조회하며 정상 빈 잔고·명시적 계좌 거절·통신/업무 실패를 구분한다. 매수가능수량은 `pcs_dit="2"`, 원화증거금·지정가 기준과 NH AAPL REST 현재가만 사용한다. 주문 경로와 다른 pcs_dit는 허용하지 않는다.
- 실제 운영 3개 계좌 모두 잔고 PASS(보유 종목 0·0·1개), AAPL 가격 336.265 USD 기준 매수가능수량 조회 PASS. 각 계좌의 `byn_pbl_qty`/`orr_pbl_qty`/`max_pbl_qty` 모두 0이었다. 원본 계좌·응답은 저장하지 않고 끝 4자리와 이번 응답 순서 구분자만 출력했다. 여러 계좌가 사용 가능하므로 기본 계좌는 선택하지 않았으며 `.env`/`NHPLUG_DEFAULT_ACCOUNT`는 변경하지 않았다.
- 전송 경로별 실제 횟수: OAuth 1, 계좌목록 1, 잔고 3, 현재가 1, 매수가능수량 3. **실제 주문 0건**, RC 0회. RC/WSS10012는 보류하며 기존 구현을 유지했다. Paper Trading·실주문 및 Kiwoom/Collector/Validator/schema/launcher 변경은 없다.
- NH offline **48 통과**, 기존 Kiwoom/Radar/Dashboard/scheduling 회귀 **186 통과/6 suites**. 실제 smoke 출력의 인증값·전체 계좌 미노출을 메모리 대조했다. 사용자 미커밋 변경을 보존하고 git add/commit/push하지 않았다.
- diff --check·상대 링크·인증값 검사 PASS. `.env`·DOCX 해시와 RC 함수 원문 동일. data 201개 중 기존 200개 크기/mtime 동일, 이전에도 관측한 SHM 1개 mtime 변동(크기 동일)은 원인 미확정으로 기록하며 파일을 되돌리지 않았다.

## 2026-10-08 — NH RC 구독 키 감사·재시험

- 기존 구현이 AAPL ticker를 tr_key로 직접 보내지 않고 공식 SDK 종목마스터의 GIC를 사용했음을 확인했다. 공식 gbstock openapi.json의 x-realtime-channels는 RC/7080/gicz15, rc는 미국·중국 지연정보다. 공식 m_gtsstock.h/.mst의 AAPL/USA 행은 하나이며 sGIC 필드 폭 15바이트 중 유효 값 7문자/뒤 공백 8개였다. 공식 SDK의 우측 공백 제거를 유지하고 임의 prefix·padding·rc fallback은 추가하지 않았다.
- 전송 직렬화 결과가 resolver의 GIC/RC와 일치하는지 검사하고 키 형식/길이·ACK code만 출력하도록 보강했다. 합성 테스트에서 실제 resolver/serializer를 연결해 ticker 직접 전송·모호한 마스터·WSS10012 오분류를 방지했다.
- AAPL 한 종목만 실제 RC 재시험: connected=true, key_length=7, master_field_width=15, key_is_ticker=false, ACK **WSS10012 재현**, subscribed=false, tick 없음, 해제 전송/close 확인·해제 ACK 미확인. 이용권한/유료시세 약정은 원인 후보이며 확정하지 않았다. 인증 외 REST와 계좌·주문은 재호출하지 않았다.
- NH offline **34 통과**, Kiwoom/Radar/Dashboard/scheduling 핵심 회귀 **186 통과/6 suites** 재실행. .env·DOCX 해시 유지, data 200개 크기/mtime 동일·동일 SHM 1개 mtime 변동 관측을 기록하며 원본을 복구·정리하지 않았다. 기존 미커밋 변경 보존, add/commit/push 없음.

## 2026-10-08 — 독립 NH Namuh read-only adapter v1

- 공식 nhplug 0.4.1과 Windows TLS용 truststore 0.10.4, websocket-client 1.9.2를 `.venv-paddle`에 추가했다. optional requirements를 분리하고 기존 패키지 전체 upgrade는 하지 않았다. Market Radar 앱 v2.3.0 / Collector v2.1 / Validator 1 / research schema 4 / Master schema 1 유지.
- OAuth·해외 현재가·계좌목록만 허용하는 REST adapter와 명시적 CLI probe를 추가했다. 전송 계층에도 endpoint allowlist·redirect 차단을 적용하고 주문 메서드는 만들지 않았다. SDK token 파일 캐시·원본 응답 출력은 끄고 계좌는 끝 4자리만 표시한다. GIC는 공식 종목마스터에서 확인하며 가격 필드는 공식 명세의 REST trdprc/RC trdprc_1z17만 사용한다. 대소문자 채널 변환과 지연채널 fallback은 없다.
- 실제 기본 smoke **PASS**: 인증, AAPL 현재가(마지막 조회 336.75), 계좌목록 6개/운영 유형 3개. 추가 RC WebSocket은 연결 성공 후 **WSS10012 구독 거절로 FAIL**, tick·해제 ACK 미확인, close 확인. 공식 명세는 RC 유료시세 약정을 요구하지만 해당 오류 코드와의 정확한 관계는 미확인이다. 실제 주문 호출 0건, 주문가능수량은 공식 API 존재만 확인했다. Paper Trading/실주문은 미구현.
- NH 합성 오프라인 **30 통과**, 기존 핵심 회귀 **186 통과/6 suites**(Radar 31, Kiwoom provider 33/quality 33/stability 35, Dashboard 27, scheduling 27). live 출력에서 실제 key/secret/token 값과 전체 계좌 형태가 나오지 않는지 메모리 내 대조했다. pip check와 diff --check 통과. 기존 data 테스트 로그는 덮어쓰지 않았다.
- .env/DOCX 해시 보존. data 201개 중 200개 크기/mtime 동일, 최근 세션의 `stock_helper_market_kiwoom_v2_1.db-shm` 1개 mtime 변동을 관측했다(크기 동일). 기존 pythonw 실행도 확인했지만 원인은 미확정이며 파일을 되돌리지 않았다. NH 코드에는 기존 data 접근이 없고 기존 scheduling/clock_jump/Kiwoom/Validator 기능을 수정하지 않았다. 시작 HEAD 3adacfd/main ahead 1을 유지하며 add/commit/push하지 않았다.

## 2026-10-07 문서 정합성 검수 — 사용자 교체 DOCX 기준

- 사용자가 교체한 PROJECT_DIRECTION.docx(작성 기준 2026-10-07)를 최상위 방향 원본으로 적용했다. DOCX는 읽기 전용이며 SHA256 `a92ad593d07d54475268c449374d0bbf1581cc55c9a681e74df3c77564e8175b`를 작업 전후 보존했다. 아래 v2.3.0의 최초 DOCX 생성·50문단 동등성 기록은 교체 전 파일에 관한 과거 이력이다.
- MD에 Current/Emerging 역할 분리, NULL 원칙, preopen 준비와 regular research 분리, +30s~D+3 결과 추적, 기존 가격구조와 독립·결합 비교, 최종 GUI·기능 선택 기준 등 원본의 빠진 의미를 보완했다. START_HERE의 읽는 순서와 DOCX 우선 원칙, AGENTS의 방향 변경·출처 생성 금지 규칙을 명확히 했다. README 2종에 현재 수집 기능과 장기 의사결정 보조 목표를 구분했다.
- HANDOFF를 현재 버전·shared cache·scheduled Regular/Capacity·Validator·기존 150-slot WARN·Phase A 마무리~B 시작·Next Work 중심으로 정리했다. 교체 전 DOCX 부재/생성·렌더 제한과 긴 검증 경과는 기존 CHANGELOG 이력에서 확인한다. RESEARCH_BASIS 7개 연구 축과 TEST_PROTOCOL은 정합성을 확인하고 유지했다. 검증되지 않은 citation을 추가하지 않았다.
- 문서 전용 작업이며 앱 v2.3.0 / Collector v2.1 / Validator 1 / research schema 4 / Master schema 1 유지. 이전 256 통과 기록을 다시 실행한 것처럼 기록하지 않는다. 기존 validation JSON만 읽었으며 전체 회귀·live smoke·세션 재검증은 실행하지 않았다.
- 검증: DOCX SHA256 동일, MD 핵심 의미/START_HERE 링크/Next Work/버전 일치, diff --check 및 변경 내용 인증값 패턴 확인. 기존 Python/launcher 코드와 .env 해시, data 파일 목록·크기·mtime 보존 확인. 기존 미커밋 변경을 유지하고 git add/commit/push하지 않았다.

## Market Radar v2.3.0 — Automatic Session Validator와 프로젝트 방향 기준서

개발·보관 실데이터 재감사: 2026-10-07 KST. **앱 v2.3.0 / Validator 1 / Collector v2.1 / kiwoom_radar_v2_1 / research schema 4 / Master schema 1**. 시작 시 launcher 4개와 README/CHANGELOG/HANDOFF의 기존 미커밋 변경이 있었고 그대로 보존했다. ranking/RVOL/NULL/protected tracking/result sampling/provider/OCR/벽/Toss/halt 공식·동작은 변경하지 않았다.

### 구현

- 정상 Dashboard 종료의 FINALIZING을 export·quality·Master import·Validator·JSON/Master 저장까지 유지한다. 검증기 오류는 원본 metadata·export·수집 성공을 바꾸지 않는다. 수집/등록 실패 시에도 연구 DB가 있으면 검증 JSON 산출을 시도한다.
- Validator는 XNYS/timing/준수/clean shutdown, preopen 연구 오염, SQLite integrity/필수 표/FK/연구 참조, metadata·quality 재계산, CSV 4종의 헤더·행 수·내용, capacity peak/last, 데이터 품질, horizon·sample timestamp/offset을 검사한다. timing·horizon 상수는 기존 scheduler/engine에서 재사용한다.
- PASS/WARN/FAIL과 사용 policy·근거 fingerprint를 `validation_summary.json`에 atomic 저장한다. WARN 임계값은 RVOL <50%, stale 관측비율 >5%, missing ≥1이며 전략 점수가 아니다. Worker/DB 오류로 유실된 batch도 v1은 보수적으로 FAIL 처리한다. 종료 뒤 목표인 pending은 정상으로 유지한다.
- 원본 DB/WAL을 임시 복사해 query_only로 검증하여 원본 SHM까지 보존한다. logical DB 내용과 export/lifecycle 해시로 재import·ZIP의 validation freshness를 검사한다. 단순 SQLite checkpoint는 무효화하지 않으며 D+n 등 원본 변화는 재검증을 요구한다.
- History Validation 열·Validation Summary 탭·Revalidate Session, Live 간단 상태를 추가했다. Master는 기존 `quality.summary_json.validation` 사용, 별도 column/migration 없음. ZIP allowlist와 manifest에 validation을 추가하면서 기존 민감정보/경로/hash/atomic 규칙을 유지한다. 명시적 CLI 검증 명령도 제공한다.
- 원래 PROJECT_DIRECTION DOCX/MD 및 RESEARCH_BASIS가 저장소에 없음을 재확인했다. 사용자 요청의 방향을 기준으로 DOCX/MD를 새 작성하고 START_HERE와 RESEARCH_BASIS를 추가했다. 데이터 기반 의사결정 보조 목표, Phase A~H, 충분한 검증 후 Candidate Rank, 추천의 의미, 1주 첫 표본/추가 기간 검증, 입력 비교 가능성을 명시했다. 외부 citation은 만들지 않고 출처 검증 필요로 표시했다. `tools/sync_project_direction.py`가 MD의 모든 문단을 DOCX로 동기화하고 재개봉·텍스트 일치를 확인한다.

### 실제 150-slot 첫 validation baseline

원본: `data/20261006_184830_801a37e9`, **2026-10-06 ET**, run_id `cec24441029dc588e58bf6c97d51028b`. 새 live smoke가 아니라 이미 수집된 DB/export/log의 read-only 재감사다. 결과 **WARN**. Protocol / Premarket Leak / DB·export Integrity / Capacity는 PASS이며 errors 0건이다.

| 항목 | 원본 재계산 |
|---|---:|
| 시작 / 종료 지연 | 57.473ms / 56.438ms |
| snapshots / candidates / unique snapshot tickers | 8,584 / 91 / 231 |
| events / results | 1,481 / 1,001 |
| Max WS / peak Active / last Active | 150 / 106 / 105 |
| peak / last Protected | 89 / 78 |
| peak / last Discovery | 80 / 74 |
| peak / last saturation | 70.6667% / 70% |
| slot unavailable event / peak / last | 0 / 0 / 0 |
| RVOL available / warming / incomplete_history | 0% / 1,163 / 7,421 |
| return 1m / 5m available | 27.2251% / 23.8117% |
| stale / unique stale tickers / stale 관측비율 | 723 / 59 / 7.7683% |
| REST total / deterministic / transient / rate limited | 53 / 53 / 0 / 0 |
| REST unsupported / no_data / suppressed | 30 / 23 / 36 |
| backfill incomplete attempts | 3,813 |
| WS gap / worker / DB / export error | 0 / 0 / 0 / 0 |
| complete post_target / pre_target_grace | 482 / 5 |
| result missing / reasons | 37 / stale_price 36 + not_subscribed 1 |

WARN 사유: RVOL coverage low, stale coverage high, REST errors, deterministic suppressed, incomplete history, missing results. stale 비율은 723/(8,584+723)이며 snapshot에 저장되지 않은 stale 관측을 분모에 포함한다. 로그의 stale 723건·shutdown_complete/program_stop 각 1건·DB/calculation/export 오류 0건과 대조했다.

| Horizon | complete | missing | pending | outside_session |
|---|---:|---:|---:|---:|
| +30s | 87 | 3 | 1 | 0 |
| +1m | 88 | 2 | 1 | 0 |
| +3m | 78 | 11 | 2 | 0 |
| +5m | 81 | 8 | 2 | 0 |
| +10m | 78 | 9 | 4 | 0 |
| +30m | 63 | 3 | 25 | 0 |
| +60m | 12 | 1 | 78 | 0 |

EOD/D+1/D+2/D+3는 각각 pending 91. missing expected horizon=0, invalid sample=0, overdue pending=0이다. +60m pending 78개는 종료 이후 목표이며 FAIL이 아니다. 150은 이 거래일의 슬롯 부족이 관측되지 않은 운영 후보이지 영구 최적값이나 통제된 인과 결론이 아니다.

### 검증과 제약

- 전체 회귀 **256 통과 / 실패 suite 0**. 기존 suite 전체와 Validator 22개·FINALIZING/검증 실패 격리 1개를 추가 실행했다. 근거: `data/kiwoom_provider_validation/tests_summary.json`. 합성 DB/fake clock/offscreen UI/replay/OCR 초기화 검사이며 실제 새 네트워크 수집은 하지 않았다. Current/Emerging·scheduled collection·shared cache·OCR·벽·Toss·halt 회귀 포함.
- 전체 회귀 후 History 열 너비 설정 키 분리·테스트 QSettings 격리·검증 완료 시 진행 중 History 조회 뒤 재갱신 보강을 했다. 관련 Dashboard 27개/Validator 22개/Live UI script를 다시 통과했으며 중복 집계하지 않았다.
- 첫 전체 실행에서 SQLite checkpoint에 따른 stale 오탐 1개를 발견했다. DB/WAL 파일 바이트 대신 logical DB fingerprint로 수정 후 관련 suite와 전체를 재실행하여 통과했다. 정상 PASS/WARN/FAIL, preopen 세 표, DB 손상/FK/표 누락, CSV 행 수·같은 행 수 내용 변조, metadata/quality, slot/RVOL/stale, pending, 결과 sample, 재검증/atomic 실패/Master/UI/ZIP/원본 WAL·SHM 보존을 확인했다.
- 실제 원본 **12개 파일 SHA256 전후 동일**. 원본 세션에는 validation_summary만 새로 생성하고 기존 DB/WAL/SHM/export 6종/lifecycle/log/ZIP은 보존했다. 운영 Master에는 해당 run_id의 validation summary만 갱신했다. 새 포맷 ZIP 검사는 `data/session_validator_checks/review/`의 별도 ZIP으로 수행하여 기존 실제 ZIP을 덮어쓰지 않았다.
- DOCX ZIP/XML·python-docx 재개봉·MD 전체 문단 동등성 통과. `render_docx.py` 실행은 Windows runtime의 LibreOffice/soffice 부재로 실패했으므로 페이지 렌더·시각 검증 완료라고 주장하지 않는다. 원본 방향 문서가 없어 기존 문서와의 diff도 불가능했다.
- 현재 연구 단계는 Phase A 마무리~Phase B 시작. 150-slot 실데이터 수집은 이미 시작됐으나 표준 full-session 여러 거래일·1주 축적·다른 시장 상태 검증은 남아 있다. 미래 D+n 자동 수집, 임의 BUY 점수, 주문/계좌 기능은 추가하지 않았다. `.env` 읽기·수정과 git add/commit/push 없음.

## Windows Quick Launch v1 — Stock Helper 바탕화면 실행

개발: 2026-10-03 KST, 기반 HEAD `52e0dec`. 실행 도구 버전 v1이며 **Market Radar v2.2.2 / Collector v2.1 / research schema 4 / Master schema 1**은 유지한다.

- 일반/Debug CMD와 공통 PowerShell launcher를 추가했다. 스크립트 위치에서 프로젝트 root와 작업 폴더를 계산하고 로컬 `.venv-paddle`만 사용한다. 일반은 pythonw 우선/python fallback·숨김 콘솔, Debug는 python·콘솔 유지다. 필수 파일 누락과 비정상 종료를 안내하며 별도 인증정보/로그 파일을 만들지 않는다.
- Desktop SpecialFolder와 WScript.Shell로 `Stock Helper.lnk`를 생성·갱신한다. 같은 프로젝트 대상만 갱신하며 다른 동명 대상은 보존한다. 선택적 ico/기존 Python/Windows 아이콘을 사용한다. 저장소 이동 후 이전 대상의 바로가기는 사용자가 이름을 바꾼 뒤 다시 생성한다.
- PyInstaller 패키징/의존성 설치/기존 앱·Dashboard·Collector·ranking·OCR·CLI 코드는 변경하지 않았다. 기존 single collector lock을 유지하며 앱 창의 중복 실행 제한은 추가하지 않았다.
- 이전 v2.2.2의 전체 회귀 233 통과와 evidence/Master 38개 파일 SHA256 보존 기록은 이전 작업 결과다. 이번 실행 도구 검증과 구분한다.
- 이번 검증: 복사한 실행 도구/실제 venv Python/가짜 entry point를 사용해 공백·한글·`&` 경로, 프로젝트 기준 cwd/argv, pythonw 우선·python fallback, Debug pause/exit code, 필수 파일 누락을 확인했다. 임시 Desktop의 실제 COM 바로가기 생성·재생성·동명 다른 대상 보존도 통과했다. 실제 Desktop 바로가기를 두 번 생성·갱신하고 필드를 검증했다.
- 실제 `Stock Helper.lnk` → 기존 UI의 응답하는 창을 확인한 뒤 정상 닫기, launcher exit 0 및 생성 프로세스 잔존 없음 확인. Market Radar/Kiwoom 수집은 시작하지 않았다. `live_ui_dashboard_test.py`, `ui_registry_integration_test.py` 통과(offscreen), `python -m market_radar --help` 성공. 전체 suite/150-slot/live collection 검증은 이번에 실행하지 않았다.

## Market Radar v2.2.2 — XNYS scheduled collection

개발: 2026-10-03 KST, 이번 변경 미커밋. 기반 HEAD `faedfa2`에 이전 Dashboard/shared storage 작업이 보존되어 있다. **앱 v2.2.2 / Collector v2.1 / kiwoom_radar_v2_1 / research schema 4 / Master schema 1**. ranking/RVOL/NULL/보호 추적/주문·계좌 범위는 변경하지 않았다.

### Changed

- Manual / Regular Session / Capacity Test 선택, 별도 ARMED/PREPARING/PREOPEN_WAIT/REGULAR_LIVE/FINALIZING/STOPPED/ERROR session state를 추가했다. collector state는 별도로 표시한다.
- 기존 XNYS ExchangeCalendar로 휴일/주말/DST/조기폐장을 처리한다. preopen_prepare_minutes 기본 30, 공식 open에 gate 개방, Capacity open+65분/Regular 공식 close에 자동 graceful stop한다.
- 개장 전 auth/WS LOGIN/catalog/type/과거 backfill을 허용하고 provider source refresh/배정 및 worker 연구 저장을 차단한다. 개장 후 새 universe부터 시작하며 actual gate/source refresh/first batch 시각을 구분한다. incomplete cache는 시작을 지연시키지 않는다.
- Capacity 장중 arm 거부, Regular late 시작 허용+비준수, 사용자 stop/clock jump/resume/연결 지연/종료 지연 사유를 기록한다. 운영 tolerance와 판단 기준을 TEST_PROTOCOL.md에 고정했다. 마지막 15초 새 준비 요청 보류는 진행 요청 완료를 보장하지 않으므로 실제 source refresh 지연도 공개한다.
- collection_protocol을 lifecycle/research metadata/quality/export metadata/ZIP manifest에 저장한다. Master quality.summary_json과 기존 schema 1을 사용하며 legacy unknown을 유지하고 ALTER하지 않는다. FINALIZING은 최종 export와 Master import까지 유지한다. import 오류는 수집 성공 여부와 별도로 표시한다.
- Session History에 mode/compliant/scheduled·actual/delay/reason을 추가했다. Current/Last health와 shared cache/session 분리를 유지한다. 한 arm은 한 거래일이며 주간 자동 반복/절전 깨우기/다중 슬롯 offline replay는 추가하지 않았다.

### Protocol / evidence interpretation

- [TEST_PROTOCOL](TEST_PROTOCOL.md)에 Capacity v1과 Weekly Research Collection을 고정했다. 비준수/Manual/late 자료는 진단용이며 정량 capacity 비교에서 제외한다.
- 기존 **20/50/100-slot saturation 증거는 유지**한다. 서로 다른 거래일/구간/시작 시각의 자료를 새 표준 프로토콜의 완전 통제 비교로 소급 지정하지 않는다. 특히 100-slot은 13:37:50 UTC 시작으로 새 Capacity 기준에 맞지 않는다. 기존 원본/지표/결과를 수정하지 않았다.
- 다음 실제 실행은 **Capacity Test / Max WS 150 / preopen arm → official open → +65분 자동 종료**다. CLI Manual 실행을 표준 Capacity로 안내하지 않는다. 150/200을 최적값으로 채택하지 않았다.

### Validation

- 최종 전체 회귀 **233 통과 / 실패 suite 0**. 근거는 `data/kiwoom_provider_validation/tests_summary.json`과 suite 로그다. 기존 207 단위와 신규 schedule 26개(가짜 시계/합성 live provider/임시 DB/offscreen UI)를 실행했다. OCR 초기화/Registry/벽/Toss/halt/CLI/replay/계산/보호/샘플링/REST/ZIP/cache 회귀를 포함한다.
- 신규 검증은 주말/휴일/DST/조기폐장, preopen 빈 연구 테이블, 개장 후 새 source/최초 포착, 자동 종료, late 거부/기록, connection 지연, clock jump/resume, user stop, 준비 중 취소, FINALIZING/Master, schema 1 호환, metadata/quality/ZIP 일치, UI WAIT health다. 실제 네트워크/장중 실행을 검증한 뜻은 아니다.
- 기존 evidence/운영 Master **38개 파일 SHA256 전후 동일**. `.env` 수정 없음. git diff --check/변경 범위/문서 링크/버전/비밀정보 패턴 점검을 완료했다. add/commit/push하지 않았다.
- **이번 작업에서 live smoke/150-slot 실장 수집은 실행하지 않았다.** 실제 시간 정확도·개장 REST/WS 지연·cache coverage 축적은 다음 사용자 실행에서 확인한다.

## Market Radar v2.2.1 — Persistent cache / Current vs Last Health

개발·read-only 감사: 2026-10-03 KST, 미커밋. 앱 **v2.2.1**, Collector v2.1 / test_version=`kiwoom_radar_v2_1` / research schema=4 / master schema=1 / OHLCV schema=1 유지.

### Changed

- 기존 provider가 `config.data_dir/market_radar_cache/kiwoom_v2.db`를 사용하므로 Dashboard 실행별 새 폴더마다 cache도 새로 생기는 것을 코드와 100-slot 실제 파일에서 확인했다.
- `RadarConfig.historical_cache_path`를 분리했다. Dashboard/controller는 기본으로 `APP_ROOT/data/market_radar_cache/kiwoom_v2.db`를 공유하고 session research DB/export/log와 Master는 기존 역할을 유지한다. Data root가 바뀌어도 canonical cache는 이동하지 않는다.
- canonical cache는 이번 감사 시 없었다. 다음 실제 수집에서 생성하며 이번 작업에서 운영 cache를 생성하거나 legacy cache를 복사/병합하지 않았다. 기존 provider/mode/schema/bar_policy 검증을 DDL 전에 그대로 수행한다. incompatible cache는 ALTER하지 않는다. legacy 이관이 필요하면 별도 명시적 import/migration으로 검토한다.
- CLI `--data-dir`의 기존 cache 위치는 유지한다. `--cache-path <DB 파일>`을 명시한 경우만 그 cache를 사용한다. 기존 CONFIG max WS=200 및 사용자 저장값은 변경하지 않았으며 150/200을 자동 권장/default로 설정하지 않는다. 다음 테스트는 사용자가 150을 명시한다.
- `Current Connected`와 `Current Active WS`는 controller가 provider의 실제 WS ack/connection 상태를 읽어 표시한다. STOPPED는 No/0, 마지막 batch gauge는 `Last Active WS/Protected/Discovery/Slot unavailable/Slot saturation %`로 보존한다. RUNNING의 protected/discovery/slot unavailable은 5초 DB 갱신 기준이며 Current Saturation은 현재 ack 수 기준이다. STOPPING은 종료 중 실제 연결을 표시하고 인위적 지연을 추가하지 않는다.
- `Stopped cleanly · shutdown … ms`를 별도 label로 유지해 auto-import 메시지에도 사라지지 않는다. 이 시간은 final CSV/첫 quality export까지다. Historical Cache 경로는 짧은 label+전체 경로 tooltip으로 표시한다.
- Session History 첫 열 묶음에 Max WS/Candidates/Snapshots/Max Saturation/Slot unavailable events/REST/RVOL/Clean Shutdown을 배치했다. 기존 quality.slot_unavailable_count는 **이벤트 수**, summary_json.tracking은 **마지막 gauge**다. 기존 Master에 필요한 값이 이미 있어 schema bump/운영 DB 갱신은 하지 않았다.
- 순위·RVOL NULL·MIXED_OR_UNCERTAIN·보호 추적·샘플링·REST 억제·정상 종료·Review ZIP allowlist/검사/manifest/hash/atomic replace는 유지했다. shared cache 전체는 ZIP에 넣지 않는다. 기존 cache 안의 raw_records도 공유 DB에 남으며 raw 삭제/분리는 이번 범위 밖이다.

### Actual 100-slot capacity evidence

원본 `data/20261002_223750_e6b4cd6b`, **2026-10-02 ET** 운영. 이번 작업은 보관 evidence를 읽은 감사이며 새 live 수집이 아니다. research DB의 SQL/batch metadata/counter, CSV 4종, quality_summary.json, dashboard_run.json, session log, cache REST_ERROR raw, 운영 Master를 대조했다. summary 재계산과 export가 동일했고 임시 Master 재import의 주요 12개 필드 및 결과 1,067행이 운영 Master와 일치했다.

| 항목 | 원본 재계산 |
|---|---:|
| 실행 UTC | 13:37:50.300549 ~ 14:39:13.611360 |
| duration (controller lifecycle) | 3,683.310811초 (61분 23.311초) |
| max_ws_symbols | 100 |
| candidates / snapshots / unique snapshot tickers | 97 / 8,644 / 163 |
| 최대 Active WS / Protected / Discovery | 100 / 97 / 80 |
| 마지막 Active WS / Protected / Discovery | 100 / 82 / 77 |
| 최대 / 마지막 slot unavailable gauge | 15 / 15 |
| slot unavailable 이벤트 | 86 |
| 최대 / 마지막 saturation | 100% / 100% |
| REST 분류 total / deterministic / transient / unclassified | 33 / 33 / 0 / 0 |
| REST unsupported / no_data / suppressed | 21 / 12 / 26 |
| API error / transport / parse | 21 / 0 / 0 |
| stale / WS gaps / raw dropped | 913 / 0 / 0 |
| RVOL available / warming / incomplete_history snapshots | 0 (0%) / 1,509 / 7,135 |
| DB / worker / export errors | 0 / 0 / 0 |
| clean_shutdown / 표시 shutdown elapsed | true / 422ms |
| 로그 shutdown_complete / program_stop까지 | 약 484ms / 약 484ms |
| 종료 시 취소 historical jobs | 159 |

REST raw 33건도 deterministic unsupported 21 + no_data 12로 일치했다. 21건은 HTTP 200/top-level 7/embedded 1903, 12건은 HTTP 200/return_code 0의 no_data이며 API 오류 21건과 구분한다. TR별 usa06011=29, usa06012=2, usa20100=2. ticker별 NSTR=14, ACCV=12, SAIQ=7. cache_days는 complete 148/incomplete 723이며 unique cache day와 backfill 시도 횟수는 다르다. RVOL 0%를 cache 분리만으로 해결했다고 주장하지 않는다.

| Horizon | complete | missing | pending | outside_session |
|---|---:|---:|---:|---:|
| +30s | 94 | 3 | 0 | 0 |
| +1m | 92 | 5 | 0 | 0 |
| +3m | 89 | 8 | 0 | 0 |
| +5m | 90 | 6 | 1 | 0 |
| +10m | 87 | 8 | 2 | 0 |
| +30m | 74 | 5 | 18 | 0 |
| +60m | 16 | 1 | 80 | 0 |

missing 총 36: stale_price=34, not_subscribed=1, no_snapshot_within_tolerance=1. EOD/D+1/D+2/D+3는 각각 pending 97. complete 542건 중 post_target=532/pre_target_grace=10이며 원본 결과 의미는 바꾸지 않았다. 종료 요청 14:39:13.122361 UTC, export 완료 13.543, shutdown_complete 13.605, program_stop 13.606. summary의 422ms를 전체 종료시간으로 오인하지 않는다.

**Capacity 결론:** 20 부족, 50 saturation에 이어 100도 실제 100%에 도달했다. 100을 전체 후보 추적용으로 충분하다고 확정할 수 없다. 다음 사용자 실장 테스트는 **max_ws_symbols=150**이며 Active/protected/discovery peak, saturation, slot unavailable, REST/WS, CPU/UI 응답, shared cache 재사용과 RVOL 상태를 확인한다. 150이 충분하면 1주 운영 후보로 검토하고 포화되면 200 테스트를 검토한다. 200은 공식 hard limit이며 최적값 판정이 아니다.

### Validation / limitations

- 최종 전체 회귀 **207 통과 / 실패 suite 0** (기존 194 + 신규 13). `tools/kiwoom_provider/run_validation.py`로 기존 OCR 초기화·Registry·벽·Toss·halt·CLI·Current/Emerging·RVOL NULL·보호 추적·result sampling·REST suppression·graceful shutdown·Master·Review ZIP 회귀를 유지했다. 근거는 `data/kiwoom_provider_validation/tests_summary.json` 및 suite 로그다. 기존 assert 스크립트 단위와 메서드 수의 합이며 재실행은 중복 집계하지 않았다.
- 신규 cache/current-last 회귀 12개 및 기존 Dashboard suite의 원본/Master gauge 보존 테스트 1개를 추가했다. 실제 네트워크 대신 fake REST/WS와 실제 임시 SQLite를 사용해 두 Dashboard 실행의 cache 재사용·research DB 분리·persistent Master·계약 거부/무변경·CLI 호환·NULL coverage·UI 상태를 확인했다.
- 실제 session/cache/export/log/Review ZIP 및 운영 Master는 read-only로 감사했다. 임시 Master/ZIP만 테스트에 썼으며 기존 source를 수정하거나 legacy cache를 합치지 않았다.
- 기존 week_v2/50-slot/100-slot evidence 및 운영 Master 등 38개 파일 SHA256 전후 동일. `git diff --check`, 상대 링크 23개, 앱/collector/schema 버전 일치와 비밀정보 패턴 검사를 통과했다. `.env` 수정 및 git add/commit/push 없음.
- 새 live smoke 또는 150-slot 수집은 실행하지 않았다. shared cache의 실장중 누적 효과·CPU/UI 응답 및 1주 안정성은 다음 사용자 검증 대상이다. 기존 cache/raw 보관·크기 관리도 남아 있다.

## Market Radar v2.2 — Dashboard / Master Research DB

개발: 2026-10-02 KST, 미커밋. 앱 v2.2 / Collector 연구 v2.1 / test_version=`kiwoom_radar_v2_1` / research schema=4 유지 / **master_schema_version=1**.

### Added

- 기존 Stock Helper에서 여는 Market Radar Dashboard: 별도 Current Leaders/Emerging, Start/Stop, max WS/data root/grace 설정, System Health, Session History, Candidate History, 폴더 열기.
- Qt 밖 lifecycle controller와 research executor. 수집 시작마다 새 폴더, 종료/export 후 Master 자동 등록, 실패 시 수동 import 재시도. 정상 종료 중 중복 Stop을 무시한다.
- SQLite Master sessions/candidates/results/quality, 초기 schema migration·version guard·날짜/ticker/session/horizon index. 원본 경로/identity와 NULL/result sample 의미를 보존한다.
- 연구 DB schema 1–4의 읽기 전용 import, DB identity+최초 batch+ET 날짜 기반 중복 방지와 원자적 갱신. D+n 결과 재import 가능. 원본 증거를 수정하거나 raw snapshot 전체를 복제하지 않는다.
- Review ZIP allowlist, 선택 날짜 log, manifest/크기/SHA256/누락 파일 목록, 민감정보 패턴 검사와 atomic publication. DB/cache/raw/인증 파일 제외, 검사 실패 시 기존 ZIP 유지.
- `radar_ticker_selected(str)` 연결: 기존 분석 카드 선택 또는 clipboard ticker 준비. 자동 캡처 없이 기존 OCR 흐름 사용. QSettings에 일반 UI 설정만 저장.

### Changed

- runtime의 수집/종료 phase 표시만 추가했다. CLI, provider REST/WS/backfill, Current Leaders/Emerging 순위, protected tracking, schema 4와 cache는 변경하지 않았다.
- GUI도 기존 단일 collector lock과 graceful export 경로를 사용한다. 비동기 초기화에서 기존 worker=None 계약과 종료 동작을 보존했다.
- 기존 문서 역할과 사용자 미커밋 문서를 유지하며 README/기술 문서/HANDOFF를 현재 상태로 갱신했다. PROJECT_DIRECTION 및 RESEARCH_BASIS 파일은 저장소에 없어 수정하지 않았다.

### Validated

- 신규 offline 테스트 26개 통과: Master 생성/version/index·동시 초기화, import·중복·복사 identity·여러 날짜·NULL·D+n 갱신·rollback·원본 보존, ZIP allowlist/민감정보/manifest/hash/선택 파일 누락/실패 시 기존 ZIP 보존, history, controller 중복 stop/초기화 중 stop/lock 충돌/자동 import/실패 격리, UI thread responsiveness, CLI replay.
- 최종 전체 회귀 **194 통과 / 실패 suite 0** (기존 168 + 신규 26). `tools/kiwoom_provider/run_validation.py` 실행으로 기존 UI audit 19개, CLI/replay, Current/Emerging·protected tracking, OCR 초기화·Registry·벽·Toss·halt까지 통과했다. 메서드 수와 기존 assert 스크립트 단위를 합친 프로젝트 집계이며 재실행은 중복 집계하지 않았다. 근거: `data/kiwoom_provider_validation/tests_summary.json` 및 suite 로그. 최초 전체 회귀에서 시작 전 worker/close 계약 문제가 발견돼 수정 후 전체 재실행했다.
- `git diff --check`, 상대 링크 23개, 현재 앱/collector/schema 버전 구분, 변경 파일 범위 및 비밀정보 패턴 검사 통과. .env·provider·engine·storage·cache 변경 없음. GUI import와 offscreen 이벤트/표·선택·종료 검증을 수행했다.
- 실제 보관 세션 4개를 **임시 Master DB**에 read-only import하고 같은 입력 재import·ZIP 생성을 검증했다. 운영 master에 테스트 세션을 넣지 않았다. 원본 DB/cache/raw/export/log 등 **50개 파일 SHA256 전후 동일**, Master integrity_check=ok. 새 live 수집은 실행하지 않았다.

| 보관 원본 | ET 날짜 | Research schema | Max WS | Candidates | Snapshots | Missing Results | Max saturation |
|---|---|---:|---:|---:|---:|---:|---:|
| kiwoom_week_01 | 2026-09-28 | 2 | 20 | 20 | 676 | 15 | unknown |
| kiwoom_week_v2 | 2026-09-30 | 3 | 20 | 33 | 4,595 | 5 | 100% |
| kiwoom_smoke_v2_1 | 2026-09-30 | 4 | 20 | 19 | 378 | 0 | 100% |
| kiwoom_capacity_50_20261001 | 2026-10-01 | 4 | 50 | 50 | 5,244 | 10 | 100% |

이 수치는 원본 보관 데이터를 다시 읽은 결과이며 새 capacity/live smoke가 아니다. 과거 clean_shutdown은 명시적 lifecycle 부재로 NULL이다. 50-slot saturation 확인에 따라 다음 검증은 100-slot capacity test이며 최적값으로 단정하지 않는다.

### Known limitations

- GUI live Start→시장 수집→Stop 실장중 smoke와 EXE 배포는 아직 검증하지 않았다. mock/replay/offscreen 검증과 구분한다.
- legacy DB는 실행별 식별자가 없어 같은 DB의 ET 날짜로 요약한다. 실행시간은 관측 snapshot 범위이며 clean_shutdown/없는 counter는 unknown이다. 최초 batch가 없는 legacy 세션은 경로 기반 보조 identity를 쓴다.
- live Health는 5초 주기 DB 기준이며 export 묶음의 원자성은 없다. 종료 후 ZIP 전달을 권장한다. 과거 날짜 로그는 같은 날 여러 실행을 포함할 수 있다.
- 세션 목록은 200개/page, 종목 결과는 최근 1,000 horizon 행. 원본은 별도 보존한다. 원본 여러 DB를 한 폴더에 넣으면 DB별 import transaction이며 중간 오류 시 성공한 DB는 재import로 중복 없이 복구한다.
- 민감정보 검사는 알려진 필드 패턴과 경로 allowlist 기반이며 무표식 임의 문자열까지 식별하지는 못한다. live 수집 원본의 기존 공개 필드 정책은 유지한다.

## Market Radar v2.1

Git: 2026-10-01, `330492a` — Stabilize Kiwoom market radar v2.1. test_version=`kiwoom_radar_v2_1`, research schema=4. 실제 smoke 운영일은 2026-09-30 ET다.

### Added

- REST deterministic/transient/unclassified 실패 분류와 공개 오류 metadata, raw REST_ERROR 기록.
- ET 날짜·TR·ticker/exchange/date/query·cursor 단위 negative cache. 확정 실패 TTL 6시간, 날짜 변경/TTL 이후 재검증, 성공 시 회복. 영구 symbol blacklist 없음.
- `result_pre_target_grace_seconds=0.25`와 result의 sample_timestamp/sample_offset_seconds/sample_selection 감사 정보.
- quality summary v2의 REST 분류·상위 10개 차원별 집계, shutdown·result_sampling·slot_saturation_pct.

### Changed

- 새 연구 DB `stock_helper_market_kiwoom_v2_1.db` 사용. 이전 연구 DB는 ALTER하지 않고 schema/identity 불일치를 거부한다.
- cache OHLCV schema 1/assumed_et_start_v1 계약은 유지한다. 실제 smoke에는 별도 새 cache를 사용했다.
- API/transport/parse 오류와 historical backfill incomplete·deterministic_unavailable·취소를 분리했다. 정상 빈 initial chart는 no_data이며 API 오류가 아니다.
- FIRST_DETECTED부터 +60m까지 보호 추적, ETF/warrant 등 확정 유형 필터, 기존 Current Leaders/Emerging 공식과 RVOL NULL 정책을 유지했다.

### Fixed

- 실패 종류와 무관한 즉시 3회 retry 및 동일 요청 재순회로 인한 retry storm. 확인된 transient만 제한 재시도하며 미확인 실패는 60초 cooldown으로 둔다.
- stop 이후 scheduling/page/retry/WS REG를 막고 pending historical jobs를 취소한다. KeyboardInterrupt 수신·종료 phase를 기록하며 반복/중첩 Ctrl+C와 provider close 실패 표시를 보강했다.
- 목표 직전 수 ms의 snapshot과 목표+30초를 넘긴 다음 snapshot 사이에서 생기던 결과 경계 문제. 유효 post-target을 우선하고 해당 창 종료 후에만 pre-target grace를 사용한다. 기존 30초 tolerance와 실제 가격 기준을 유지하며 보간하지 않는다.

### Validated

기존 133개 + stability 35개 = **168 tests passed / 0 failed**. 기존 OCR 초기화·Registry·UI·Toss·halt 및 +60m 보호 회귀를 유지했다. 버전·schema·경로 기대값만 갱신했으며 실패 테스트를 삭제하지 않았다. 모의 active request + 대기 historical 300건 + 반복 stop은 78ms, 300건 취소, final export 및 DB integrity 통과였다.

#### FFR 조사와 REST 오류 증거

v2 REST 시도 오류 1,020건은 usa20100 474건 + usa06011 546건이다. retry=2로 각각 158/182개의 실패 cycle이 3번씩 호출됐다. 백필 오류의 ticker 분포는 FFR 174, GTNA 3, RZAI 3, GOW 2다. quote 오류는 ticker가 로그에 없어서 474건 모두를 FFR로 확정하지 않는다. worker_error 158건은 quote 실패가 scheduler의 광범위 exception handler까지 전파된 기록과 일치한다. 실패 분류 없이 즉시 3회 retry하고, quote는 다음 순회마다/백필은 60초 뒤 같은 요청을 재시도하는 구조가 오류를 증폭했다.

**v2 증거 한계:** 실패 응답은 raw에 저장되지 않았고 HTTP status/return_code/message/timeout/connection/parse 구분이 없었다. 과거 1,020건의 해당 차원은 unknown이며 모두를 1903으로 소급 분류하지 않는다. endpoint는 기존 allowlist상 usa20100 `/api/us/mrkcond`, usa06011 `/api/us/chart`다.

FFR은 첫 universe에서 CHG+VOL, 일간 합집합에 SPIKE로도 발견됐다. 거래소는 ND이며 FE가 실제 수신됐다. usa10099 catalog에 없어 type=unknown으로 유지했다. usa20100/usa06011 성공 raw는 없었다. FE 가격은 있지만 REST previous_close/completed 1m history가 없어 day_change_pct/volume/RVOL이 NULL이다. FE 필드로 기존 계산 정의를 대체하지 않았다.

정규장 중 허용 read-only probe를 7회 실행했다: ND/NY/NA usa10099 각 1회, FFR/NVDA usa20100·usa06011 각 1회. 공개 결과는 `data/kiwoom_v2_1_validation/public_failure_probe.json`이다.

- catalog: HTTP 200 / return_code 0, 각각 5,206 / 5,579 / 1,947행. 세 거래소 모두 FFR entry 없음.
- FFR 현재가·분봉: HTTP 200 / return_code **7**, message 내 **1903: 종목 정보가 없습니다**. 입력 ND/FFR, 분봉 0행.
- 같은 ND의 NVDA: 두 TR 모두 HTTP 200 / return_code 0, 분봉 100행.
- 확인된 현상은 **후보/FE에는 존재하지만 해당 REST catalog/조회에서는 symbol을 찾지 못하는 불일치**다. 공급자 내부 원인, 상장상태, 유형, 거래소 이동을 추측하지 않는다. ticker 하드코딩/제외/영구 blacklist는 없다.

분류 근거는 [Kiwoom 공식 오류 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/kiwoom/core/errors.py)이다. top-level 7만으로 transient를 판정하지 않는다. 공식 1901/1902/1903은 symbol lookup 오류, 1700/1701/1702는 rate limit, 8005/8031/8103은 인증 회복 대상이다. 그 외 확인되지 않은 service error는 unclassified로 남긴다.

#### 종료 지연 조사

기존 pending jobs는 FIFO 전체를 drain하는 구조가 아니라 IO thread가 페이지 단위로 처리하는 dict였다. 기존 stop_event도 있었다. 따라서 '기존 종료가 반드시 queue 전체를 처리했다'고 주장하지 않는다. scheduler 내부 loop/page/구독 선택의 stop 확인이 부족했고 stop 예외가 오류로 집계될 수 있었다. v2에는 Ctrl+C 요청 시각이 없어 전체 지연을 재구성할 수 없다. 마지막 WS disconnect 15:27:27.612 UTC→final export 15:27:40.307은 12.695초, program_stop까지는 12.709초지만 Ctrl+C부터의 시간은 아니다.

#### 실제 +30s 경계 재검산

원본 missing 4건의 가까운 pre-target offset은 LIQT -0.001633s, NVDA -0.003049s, PACB -0.002346s, FRGT -0.002783s다. 다음 snapshot은 각각 +30.001094/+30.020835/+30.006155/+30.003765s였다. 원본 결과는 수정하지 않았다. `counterfactual_sampling.json`의 읽기 전용 재검산에서는 기존 complete 선택이 전부 동일하고 이 4건만 grace로 복구 가능했다. 운영 증거 전체 SHA256도 작업 전후 동일하다.

#### 실제 Windows CTRL_C_EVENT smoke

실제 v2.1 smoke: **완료**. 새 `data/kiwoom_smoke_v2_1`, 20슬롯, production provider. 약 10분(collector 596.875초; interpreter/초기화 포함 driver 600초) 동안 수집했다. windows_ctrlc_smoke.py가 숨김 콘솔에 실제 Windows CTRL_C_EVENT를 전달했고 정상 KeyboardInterrupt handler가 수신했다. ordinary timer의 worker.stop/강제 종료로 대체하지 않았다. driver의 child exit code=0, forced_termination=false다.

| 실제 smoke 항목 | 수치 |
|---|---:|
| 실행 UTC | 2026-09-30 16:53:57.439569 ~ 17:03:56.713607 |
| snapshot / unique ticker / candidate | 378 / 21 / 19 |
| +30s / +1m / +3m / +5m complete | 각각 19/19, missing 0 |
| +10m / +30m / +60m | 각각 pending 19 (짧은 smoke) |
| result_sampling post-target / pre-grace | 76 / 0 |
| REST errors / deterministic / transient / unclassified | 17 / 17 / 0 / 0 |
| FFR usa20100 / usa06012 / usa06011 | 1 / 1 / 15 |
| 동일 요청 재시도 / 억제 호출 | 0 / 134 |
| historical backfill complete / incomplete / deterministic_unavailable | 56 / 179 / 14 |
| DB / worker / export / transport / parse 오류 | 모두 0 |
| WS gap / raw dropped / stale | 0 / 0 / 1 |
| ack add / remove 종목 수 | 21 / 21 |
| slot saturation / active / protected / unavailable 마지막 gauge | 100% / 20 / 19 / 37 |
| slot unavailable 진입 이벤트 | 77 |
| RVOL available / warming / incomplete_history | 0 / 92 / 286 |
| 제외 유형 snapshot | 0 |
| FFR FE raw | 110 |
| 연구 DB / cache integrity_check | 둘 다 ok |
| 종료 취소 historical jobs / stop 이후 새 백필 | 8 / 0 |

17건 모두 HTTP 200 / top-level 7 / embedded 1903 / attempt=1이며 FFR이다. 분봉 15건은 당일+이전 14거래일 각각 1회다. 각 날짜의 backfill_start도 1회씩이므로 같은 query를 반복한 15회가 아니다. API 오류 자체는 숨기지 않고 남겼다. 실제 오류 class 분류와 retry 억제가 확인됐다. 신규 캐시·짧은 수집에서 RVOL available=0이므로 RVOL 개선을 주장하지 않는다. 실제 grace 사용은 0건이어서 경계 개선 근거는 자동 테스트와 원본 read-only 재검산이며 live pre-grace 사용 검증이라고 주장하지 않는다.

| 실제 종료 단계 | UTC |
|---|---|
| 마지막 새 historical backfill 시작 | 17:03:54.692 |
| shutdown_requested, reason=KeyboardInterrupt | 17:03:56.377905 |
| WS REMOVE 20개 | 17:03:56.432 |
| WS disconnect | 17:03:56.439 |
| shutdown_export_start | 17:03:56.523 |
| final CSV export | 17:03:56.662 |
| shutdown_complete / program_stop | 17:03:56.707 / 17:03:56.708 |

Ctrl+C 수신→program_stop은 로그 기준 **0.331초**다. driver 신호 전송 helper 반환→process exit 관측은 0.640초다. summary의 last_shutdown_elapsed_ms=281은 CSV 및 첫 quality export까지의 측정이며, 이후 timing gauge를 반영한 작은 summary 재출력/DB close는 phase 로그의 328ms 및 program_stop에 포함된다. 이 측정 범위를 전체 shutdown 시간과 혼동하지 않는다.

근거: smoke_summary.json, ctrlc_driver.json, export/2026-09-30/quality_summary.json, logs/2026-09-30.log 및 `data/kiwoom_v2_1_validation/smoke_audit.json`. raw/log/export에 실제 인증값 문자열이 없고 기존 v2 evidence SHA256가 전부 동일함을 확인했다.

smoke 시작 당시 166개가 통과했고, 수집 중 최종 검토에서 **중첩 Ctrl+C handler 재진입 방지**와 **provider close 실패 표시**만 추가했다. 두 예외 경로는 이후 mock 테스트를 추가해 stability 35개/전체 168개로 확인했다. 정상 live 수집·REST·sampling 경로는 smoke 이후 변경하지 않았다. 실제 연속 두 번 Ctrl+C나 provider close 실패를 live에서 재현했다고 주장하지 않는다.

### Known limitations

- 실제 smoke의 pre_target_grace 사용은 0건이다. 경계 개선은 자동 테스트·원본 read-only 재검산으로 확인했으며 live grace 사용 검증으로 주장하지 않는다.
- 20 WS slots saturation은 남는다. 전체 후보 추적을 위한 50/100/200 capacity는 별도 검증 대상이다.
- RVOL missing-minute 의미는 MIXED_OR_UNCERTAIN이다. 무체결을 0-volume으로 채우지 않는다. 새 cache의 짧은 smoke에서 RVOL available=0이었고 개선을 주장하지 않는다.
- FFR 공급자 내부 원인 및 과거 v2 오류 전체의 HTTP/code/parse 원인은 복원 불가다. 6시간 negative TTL 중 데이터 회복을 즉시 감지하지 못할 수 있다.
- +60m는 기존 v2 장시간 evidence와 자동 회귀가 근거다. v2.1의 약 10분 smoke로 새 장시간 검증을 했다고 주장하지 않는다. EOD/D+n은 provisional/future pending이며 1주 연속 안정성을 보증하지 않는다.

## Market Radar v2

기록 기준: 종전 HANDOFF 갱신 2026-09-30 KST. 별도 v2 commit/릴리스 날짜는 확인되지 않으며 v2 변경은 v2.1 commit `330492a`에 함께 보존돼 있다. test_version=`kiwoom_radar_v2`, research schema=3.

### Added

- FIRST_DETECTED의 tracking_protected_until/candidate_tracking_state 및 재시작 복원. 기존 보호 구독 → 추가 보호 대상 → discovery → 기타 추적 순서.
- radar_discoveries, quality_counters, rvol_status/symbol_type_evidence 및 result missing_reason.
- Daily Quality Summary와 ET 날짜별 구조화 counter, backfill eligibility 설정.
- 공식 public fields/명시적 상품명에 근거한 보수적 ETF/ETN/warrant/unit-other 분류.

### Changed

- 새 연구 DB `stock_helper_market_kiwoom_v2.db`와 별도 v2 cache 경로. cache OHLCV schema 1은 유지하되 DDL 이전 identity 검증을 강화했다.
- eligibility는 과거 RVOL backfill만 제어하고 discovery/당일 분봉/결과 추적은 유지한다. day-volume·price 기본 threshold는 0이다.
- FIRST_DETECTED가 없는 discovery, 실제 active 구독, 보호 후보, 슬롯 미배정을 구분했다.

### Fixed

- 단순 source/rank 이탈로 보호 후보를 제거하던 구독 churn.
- 정상 초기 warm-up의 오류/traceback 취급을 INFO로 변경. 첫 성공 이후 오래된 universe는 WARNING으로 구분했다.

### Validated

#### 당시 133개 자동 검증과 production smoke

최종 전체 자동 검증: **133 통과 / 0 실패**. 기존 assert 스크립트 12, Radar 31, audit 19, provider 33, 신규 quality 33, probe 4, 실제 GPU OCR 초기화 1의 합계다. 재실행은 중복 집계하지 않았다. 근거: data/kiwoom_provider_validation/tests_summary.json 및 suite별 로그.

신규 검증은 warm-up INFO/stale WARNING/실제 오류 ERROR, 불확실 분 누락·실제 zero-volume 봉 보존, eligibility·RVOL 상태, 보호 구독·20슬롯 고갈·재시작 복원·60분 결과 완료 후 release, 미등록 discovery, missing_reason, ETF/warrant/unit/common/unknown, 기존 순위 공식, 일별 summary 수치·ET 날짜·CSV, schema v2/미확인 cache 거부를 포함한다. 기존 OCR/Registry/UI/Toss/halt 회귀도 통과했다. 실제 화면의 모든 OCR 인식 정확도를 검증한 뜻은 아니다.
실제 smoke_v2: **2026-09-29 19:11:10~19:31:12 UTC / 15:11~15:31 ET**, 약 1200초. production provider, 20슬롯, 새 data/kiwoom_smoke_v2 사용. benchmark/후보 선택 override 없이 실행했고 정상 종료했다. 실행 중 추가된 cache metadata 없는 DB 거부 guard는 새 cache 정상 경로를 바꾸지 않으며 offline 회귀로 별도 검증했다.

| v2 실제 결과 | 수치 |
|---|---:|
| snapshot / unique ticker / candidate | 751 / 22 / 20 |
| RVOL available / warming / incomplete_history | 40 / 112 / 599 |
| RVOL non-null | 5.3262% |
| return_1m / return_5m non-null | 91.4780% / 92.2770% |
| +30s complete/missing | 20 / 0 |
| +1m complete/missing | 19 / 1 |
| +3m complete/missing | 20 / 0 |
| +5m complete/missing | 19 / 1 |
| +10m complete/missing | 20 / 0 |
| +30m pending / +60m outside_session | 20 / 20 |
| EOD/D+1/D+2/D+3 pending | 각각 20 |
| historical backfill complete/incomplete/error 시도 | 60 / 352 / 0 |
| cache unique day complete/incomplete | 60 / 225 |
| ws_gap / stale / raw_dropped | 1 / 27 / 0 |
| ack subscription add/remove 종목 수 (종료 포함) | 22 / 22 |
| slot_unavailable 진입 이벤트 / 마지막 미배정 수 | 109 / 43 |
| 마지막 protected / discovery / active | 20 / 79 / 20 |
| 일간 발견 ETF/warrant 제외 | 14 / 9 (총 23) |
| 제외 유형 snapshot | 0 |
| 보호 이후 선택 슬롯 이탈 | 0 |
| universe 밖 보호 종목이 계속 선택된 관측 | 60 |
| API / DB / worker / export 오류 | 0 / 0 / 0 / 0 |
| raw FE / REST / UNIVERSE | 27,977 / 2,436 / 20 |

ws_gap 1회는 최초 구독 이전 연결 초기화 중 기록됐다. 단기 missing 2건은 MF +5m stale_price와 QSI +1m no_snapshot_within_tolerance다. 현재 시세를 채워 결과를 수정하지 않았다. 마지막 tracking gauge active=20은 마지막 batch 값이며 종료 REMOVE 20개를 포함한 최종 remove counter는 22다.

v1 단기 missing은 15/99=15.15%, v2는 2/100=2%. RVOL available은 v1 8.28%, v2 5.33%다. 서로 다른 거래일·종목·신규 캐시이므로 통제된 성능 비교나 RVOL 개선으로 주장하지 않는다. 보호 추적과 필터의 실제 불변조건을 별도로 확인했다.

정상 REMOVE/WS close/worker stop 및 최종 export 성공. CSV snapshots/candidates/events/results 행 수 751/20/201/220이 DB와 일치한다. quality summary의 count/비율도 DB와 일치하며 두 DB integrity_check=ok다. 증거: data/kiwoom_smoke_v2/smoke_summary.json, export/2026-09-29/quality_summary.json, data/kiwoom_v2_validation/smoke_checks.json.
실제 public type probe는 9개 요청 표본 모두 확보했고 API 오류 없음. 주문/계좌 API는 사용하지 않았다.

기존 v1 provider의 2026-09-28 150초 AAPL/SOAR 진단 smoke와 이번 첫 운영 baseline/새 v2 smoke는 서로 다른 evidence다. 과거 DB를 v2 결과로 변형하지 않았다.

#### 이후 장시간 운영 baseline — 2026-09-30 ET

아래 수치는 v2.1 작업에서 v2 운영 자료를 다시 계산한 기록이다.

`data/kiwoom_week_v2` 전체를 읽기 전용으로 분석했다. SQLite는 mode=ro; 시작 파일 SHA256은 `data/kiwoom_v2_1_validation/evidence_hashes.json`, SQL/CSV/log 재계산 결과는 같은 폴더 `baseline.json`이다. 원본 export/DB/cache를 재생성하거나 ALTER하지 않았다.

| 항목 | 직접 확인한 수치 |
|---|---:|
| snapshot / unique ticker / FIRST_DETECTED | 4,595 / 40 / 33 |
| CSV candidate / snapshot / event / result | 33 / 4,595 / 1,365 / 363 |
| RVOL available | 266 / 4,595 = 5.7889% |
| +30s complete / missing | 29 / 4 |
| +1m / +3m complete | 각각 33/33 |
| +5m complete / missing | 32 / 1 |
| +10m complete / pending | 32 / 1 |
| +30m complete / pending | 30 / 3 |
| +60m complete / missing / pending | 20 / 0 / 13 |
| 첫 포착 순 첫 20개 +60m | 20/20 complete |
| ws_gap / DB error / export error | 0 / 0 / 0 |
| tracking_slot_unavailable 이벤트 / 마지막 gauge | 674 / 39 |
| 마지막 active / protected / discovery | 20 / 13 / 74 |
| historical backfill complete / incomplete / error | 91 / 1,421 / 182 |
| runtime API error / worker error | 1,020 / 158 |
| FFR snapshot / FE raw | 158 / 7,821 |
| FFR day_change_pct / volume / RVOL non-null | 모두 0 |

기존 summary와 SQL/CSV/로그 수치가 일치했다. **1,421건 incomplete가 API 1,020건에 포함된 것은 아니다.** v2도 incomplete를 별도 WARNING/counter로 집계했다. v2.1은 이를 유지하고 오류 분류를 확장했다.

#### 누락 분 규약 검증

**MIXED_OR_UNCERTAIN**. 누락을 거래량 0으로 확정할 증거가 부족하다.

공식 usa06011은 OHLCV/체결시간/영업일자와 pagination을 설명하지만 무체결 분 생략/0 봉 규약을 명시하지 않는다. v1 raw에서 정규장 시간만 비교했다.

| 표본 / 2026-09-25 | 정규장 봉 / 예상 390 | 정규장 volume 합 | 일봉 acc_trde_qty |
|---|---:|---:|---:|
| NVDA | 390 | 45,609,301 | 89,947,712 |
| SOAR | 250 | 471,229 | 1,028,603 |
| ARBEW | 13 | 33,429 | 95,554 |
| WALDW | 13 | 50,736 | 142,194 |
| SHFSW | 3 | 2,958 | 12,839 |

표본에서 반환된 regular zero-volume 봉은 0개다. 저유동 표본은 앞뒤 유효 봉 사이 timestamp가 생략돼 있지만, 개별 누락 분에 체결이 없었다는 독립 증거가 없다. 고유동 NVDA도 분봉합과 일봉량이 다르므로 일봉량 일치로 증명할 수 없다. feed/세션 범위 차이가 미확정이다. extended-hours를 제외하고도 regular 구간 누락이 존재한다. BZFDW는 날짜별 regular 봉 0개여서 raw 페이지/coverage 문제도 배제할 수 없다. 이런 빈 집합의 관측 합 0은 정상 일간 거래량 0 판정이 아니다.

따라서 volume timeline의 0 보완도 구현하지 않았다. 실제 제공된 유효 0-volume 봉만 사용하며 OHLC/high/low/close/HOD/OR 생성은 없다. 미확정 누락은 incomplete/NULL이다. 무체결 확정을 활성화하는 사용자 토글도 두지 않았다.

#### 종목 유형 실제 표본

공식 mkgb는 거래소명, upgb는 업종명, isEtf는 ETF 여부다. 실제 read-only usa10099 재조회 결과는 data/kiwoom_v2_validation/public_type_probe.json에 public fields/분류/수신시각만 저장했다. 인증값/토큰은 없다.

| 실제 표본 | 공식 필드/상품명 | v2 판정 |
|---|---|---|
| HYG, TQQQ, SOXL | isEtf=Y | ETF, 제외 |
| ARBEW, WALDW, BZFDW, SHFSW | isEtf=N, 한국명 워런트 + 영문명 C/WTS | warrant, 제외 |
| FVNNU (v1 catalog) | 한국명 유닛 + 영문명 UNITS | other, 제외 |
| AAPL, NVDA | isEtf=N, 거래소/업종만 제공 | unknown, 포함 |

워런트/유닛은 공식 상품명에 명시된 양 언어 descriptor의 일관성에 근거한다. 공식 전용 type enum이나 ticker suffix 판정이 아니다. N만으로 보통주라 표시하지 않는다. 명시적 COMMON STOCK/ORDINARY SHARES, ETN descriptor 처리도 구현했으나 이 규칙의 실제 해당 표본 검증은 남아 있다. type evidence는 source/rule/public fields다.

stock_type_policy=common_and_unknown은 common/unknown만 포함하고 확정된 ETF/ETN/warrant/other는 제외한다. all도 가능하다. 제외 종목의 raw/catalog/discovery는 보존하며 excluded_symbol_type을 기록한다.

추가 liquidity 관측: FVNNU 당일량 2,173~2,185, SOAR 608,226,198~613,819,995였다. 당일량이 큰 SOAR도 과거 이력은 불완전해 임의 양수 eligibility threshold의 근거로 쓰지 않았다. ticker별 원자료는 당시 baseline JSON에 있다.

### Known limitations

- 20슬롯 고갈 및 낮은 RVOL availability. 다른 거래일·종목·새 cache인 v1/v2 missing 비율을 통제된 개선 비교로 해석하지 않았다.
- v2의 QSI +1m missing은 목표 직전 2.6ms/허용 끝 직후 8.9ms 표본이 관측됐다. 장시간 v2의 +30s missing 4건과 REST 반복 오류는 v2.1에서 다뤘다.
- 20분 smoke 자체로 +60m를 검증하지 않았다. 후속 장시간 v2에서 첫 20개 +60m complete를 별도로 확인했다.
- 무체결 분·공식 timezone/봉 라벨/24시 표기/FID290 enum 미확정. exact VWAP와 공식 확정 EOD, 기업행동 보정/완료 cache 정정 refresh, raw 보관·압축·자동 삭제는 미구현이다.

## Kiwoom Provider v1

Git: 2026-09-29, `9698b12` — Add Kiwoom market radar provider and validation. 당시 HANDOFF도 2026-09-29 KST로 기록돼 있다. test_version=`kiwoom_radar_v1`, research schema=2. 실제 smoke 운영일은 2026-09-28 ET다.

### Added

- Kiwoom real read-only REST 9개 시장 TR allowlist, 단일 WebSocket FE/optional FT, ack 기반 REG/REMOVE, PING, reconnect/gap 및 로컬 collector lock.
- 네 candidate source 합집합, ticker dedup, 별도 REST/cache worker, 완료 1분봉 정규화, XNYS 달력.
- 연구 DB와 historical/raw/cache DB 분리, provider/source/cutoff/price age provenance 및 일봉 EOD/D+n provisional 결과.
- 기존 UI에서 독립 RadarPanel을 여는 연결은 앞 Market Radar 단계의 16줄 변경이었다. provider 작업은 기존 root 기능 코드를 다시 수정하지 않았다.

### Changed

- Kiwoom RVOL과 1m/5m return은 completed_1m 기준. generic/replay 기존 snapshot fallback과 분리했다.
- exact VWAP는 NULL, cntr_tm은 assumed_et_start_v1 연구 가정, 진행 봉은 재조회 없이 완료 승격하지 않는다.
- schema 1 legacy DB를 자동 ALTER하지 않고 별도 schema 2 DB를 사용했다. cache schema는 1이다.

### Validated

당시 commit의 HANDOFF 기준 **100 통과 / 0 실패**: 기존 assert 스크립트 12, Radar 31, audit 19, provider 33, probe 4, 실제 GPU OCR 초기화 1. 전체 영웅문 화면 OCR 정확도나 실계좌 동작을 검증한 뜻은 아니다.

2026-09-28 **19:17:47~19:20:17 UTC / 15:17~15:20 ET**, 약 150초 실서버 진단:

| 항목 | 결과 |
|---|---|
| OAuth / 4개 source / 9개 시장 TR | 성공 |
| universe / 제한 구독 | 78종목 / AAPL benchmark + 실제 거래량 후보 SOAR, 최대 2 |
| FE / FT / PING / REST 요청 | 1,250 / 0(OFF) / 14 / 227 |
| historical cache | AAPL 14일 complete, SOAR 1일 complete·나머지 incomplete/NULL |
| snapshot / 5m return non-null / RVOL non-null / VWAP non-null | 8 / 8 / 2 / 0 |
| candidate / event / 단기 complete result | 2 / 14 / 4 |
| CSV / DB·cache integrity / 정상 REMOVE·export·종료 | 4종 / 둘 다 ok / 성공 |
| worker_error / failed_batches | NULL / 0 |

AAPL은 실제 VALUE source에도 포함됐고 SOAR는 CHG+VOL이었다. 진단의 선택 override는 종목 수를 제한한 것으로 일반 production 슬롯 배정을 검증한 것은 아니다. AAPL cutoff 2026-09-28 15:19 ET의 RVOL은 **0.7414334602**, 15:19:19와 15:19:49 snapshot에서 동일했다. SOAR는 과거 coverage 부족으로 NULL이었다. 미래 EOD/D+n은 미검증이다.

#### 첫 운영 baseline — 2026-09-28 ET

입력 data/kiwoom_week_01은 읽기 전용 evidence다. SQLite mode=ro 및 파일 SHA256으로 감사했다. 재현 도구 tools/kiwoom_provider/audit.py, 결과 data/kiwoom_v2_validation/baseline.json. 날짜는 ET 2026-09-28이다.

| 항목 | 수정 전 실제 수치 |
|---|---:|
| snapshot / unique ticker / FIRST_DETECTED candidate | 676 / 22 / 20 |
| RVOL non-null | 56 / 676 = 8.2840% |
| return_1m non-null | 629 / 676 = 93.0473% |
| return_5m non-null | 618 / 676 = 91.4201% |
| cache day complete / incomplete | 90 / 249 |
| 로그 backfill complete / incomplete 시도 | 524 / 440 |
| stale price 경고 | 29 |
| ws disconnect / raw dropped | 0 / 0 |
| subscription add/remove 이벤트 | 21 / 21 |
| subscription add/remove 종목 수 | 41 / 41 |
| REST 오류 / api_error | 0 / 1 (초기 warm-up traceback) |
| 실제 DB / worker / export 오류 기록 | 0 / 0 / 0 |
| raw FE / REST | 76,765 / 2,643 |

complete 로그 524에는 현재세션 완료도 포함된다. cache day 90과 다른 단위다. v1의 오류/구독 횟수는 당시 structured counter가 없어서 로그를 보조 근거로 썼고, v2는 DB counter를 쓴다.

| horizon | complete | missing | pending | outside_session |
|---|---:|---:|---:|---:|
| +30s | 17 | 3 | 0 | 0 |
| +1m | 17 | 3 | 0 | 0 |
| +3m | 16 | 4 | 0 | 0 |
| +5m | 17 | 3 | 0 | 0 |
| +10m | 17 | 2 | 0 | 1 |
| +30m | 0 | 0 | 0 | 20 |
| +60m | 0 | 0 | 0 | 20 |
| EOD, D+1, D+2, D+3 각각 | 0 | 0 | 20 | 0 |

단기 평가 가능 99건 중 missing 15건(15.15%). 종가 이후 목표 41건은 missing과 구분한다. 첫 운영에서 구독 churn으로 추적 가격이 없어질 수 있는 구조, 저유동 과거 이력 불완전, ETF/워런트 포함, 정상 warm-up ERROR, 일별 품질 집계 부재를 확인했다.

### Known limitations

- 당시 공식 type enum 미확정으로 type_unknown을 포함해 ETF/워런트가 들어올 수 있었다. 슬롯 부족으로 추적 후보의 단기 결과가 missing이 될 수 있었다.
- 무료 feed를 전 거래소 SIP로 보장하지 않으며 봉 라벨/무체결 규약/FE14 notional 의미와 확정 공식 종가는 미검증이었다.
- 일반 200종목 부하, 실제 강제 단절/재연결, 1주 연속 수집 및 Radar EXE 배포는 이 150초 진단으로 검증하지 않았다.

## Market Radar v1

별도 구현·릴리스 날짜는 확인되지 않는다. `9698b12`에 포함된 첫 Radar 코드와 당시 HANDOFF에서 Kiwoom Provider v1보다 앞선 단계로 확인된다. generic/replay test_version=`market_radar_v1`, research schema=1.

### Added

- 독립 market_radar 모듈, connect/fetch/close provider 계약, MarketBatch 모델, 명시적 JSONL replay와 별도 replay DB.
- Current Leaders / Emerging TOP10, 지표 계산, FIRST_DETECTED·순위 진입·HOD/VWAP event, SQLite/CSV 및 +30s~+60m/EOD/D+1~D+3 사후 결과.
- Qt 밖 RadarWorker, 크기 1 메시지 큐, 별도 RadarPanel과 기존 UI의 lazy 연결.

### Validated

- 후속 Provider v1의 전체 검증에서 기존 Radar 31개와 audit 19개가 보존돼 통과한 기록이 있다. 별도의 v1 단독 전체 테스트 수나 실제 Kiwoom 연결 검증을 추정하지 않는다.

### Known limitations

- generic/replay 기반 계산과 Kiwoom의 완료 1분봉 정책은 구분해야 한다. replay 합성 fixture는 실시간 시장 수집 증거가 아니다.
- 자동 매수/매도·계좌 기능은 없다.

## 초기 Stock Helper

Git 최초 commit `71822e0`: 2026-08-23. 초기 구현 commit `dac8a33`: 같은 날. 별도 프로그램 semver/릴리스 날짜는 기록에서 확인되지 않는다.

### Added

- `dac8a33`: PySide6 종목 카드, live capture, OCR 파싱/검증, StockRegistry, Toss 현재가 및 진단 도구.
- `0b22ef2` (2026-08-25): live capture UI와 종목 추적 통합, clipboard ticker 관련 캡처·해결 테스트, pending captures/price refresh.
- `0910c54` (2026-08-25): 거래정지 모니터와 일간 벽 누적.
- `189b412` (2026-08-28): 관리자 권한 EXE와 캡처 갤러리.
- `e9cc76c` (2026-09-02): GPU OCR 지원과 추적 개선.

### Changed

- `8cadc11` (2026-08-25): 종목 카드 차트·추적 UI 정리.
- `3cc22c5` (2026-08-25): tests/tools 구조 정리.
- `e293037` (2026-08-28), `b90d354` (2026-08-29): 캡처 UI 최적화, 일간 범위의 접촉한 벽 추적·보존.
- `7033a17` (2026-09-29): 캡처·UI workflow 갱신.

### Fixed

- `766ee03` (2026-08-29): rebuild 중 패키징 앱 보호.
- `3116e7c` (2026-09-11): endgame OCR 및 CPU fallback 수정.

### Validated

위 초기 이력은 commit 제목·변경 파일에서 확인한 범위만 기록했다. 각 commit 당시 전체 테스트 수나 실장중 성공 여부는 임의로 부여하지 않았다.
