# One Hour Research Runner v1.0.1

Market Radar **v2.6.1**을 수정하지 않고 사용하는 독립 Windows 실행기입니다. Research schema **4**, Paper schema **2**, Rotation extension **1**, Opening Hour schema **1**, 실행기 manifest/summary schema **1**, diagnostic schema **1**, collection quality policy **2**입니다. 소스와 새 테스트 배포 EXE는 **v1.0.1**입니다. 기존 `dist/OneHourResearch/`의 v1.0.0과 실제 데이터는 보존했습니다. 실제 주문은 구현하지 않습니다.

## 실행

새 테스트 배포는 `dist/OneHourResearch_v1_0_1_TEST/`입니다. 첫 실제 시장 테스트에서는 아래처럼 **반드시 `--no-shutdown`**으로 실행합니다. 기본 더블클릭은 자동 종료가 활성화되므로 첫 테스트 실행 방법으로 사용하지 않습니다. `_internal`과 DLL을 EXE와 함께 보관합니다. Python, GPU, 관리자 권한은 필요하지 않습니다. 파일이 하나인 EXE 배포가 아닙니다.

```powershell
Set-Location 'C:\Users\Choi\projects\stock_helper\dist\OneHourResearch_v1_0_1_TEST'
& '.\OneHourResearch.exe' --no-shutdown
```

새 배포에는 `.env`와 기존 cache/data가 없습니다. 실제 실행 전 운영자가 새 폴더의 인증을 별도로 설정해야 합니다. 기존 파일은 자동 복사하지 않으며 새 수집 결과는 새 EXE 옆 `data/`에 기록합니다. 기존 바탕화면 바로가기는 변경하지 않았습니다.

운영자가 매 거래일 개장 **30분 전까지** 실행하는 것을 권장합니다. 한 번 실행하면 한 거래일만 처리합니다. 주말·휴일에는 다음 XNYS 거래일까지 기다립니다. 현재 거래일 개장 후 실행은 거부하고 자동 종료하지 않습니다. PC 깨우기·매일 반복 실행은 제공하지 않습니다.

인증은 기존 Kiwoom provider 방식대로 실행 PC에서 별도 설정합니다. EXE 옆 `.env`의 기존 `KIWOOM_MODE=real`, `APP_KEY`, `APP_SECRET` 설정 방식을 사용하며, 빌드는 인증파일을 읽거나 복사하지 않습니다. 개발용 인증값을 배포하지 마세요. 소스 실행에서는 저장소 루트가, EXE에서는 실행파일 옆이 runtime root입니다.

```powershell
# 실제 수집하되 PC 종료만 끔
.\OneHourResearch.exe --no-shutdown
# 합성 데이터만으로 export / Validator / ZIP 검사, 시장 연결과 PC 종료 없음
.\OneHourResearch.exe --dry-run
# 합성 검사 종료 후 UI 캡처와 결과를 지정 폴더에 기록
.\OneHourResearch.exe --dry-run --smoke-dir C:\Temp\OneHourSmoke
```

기본 실행은 모든 검증이 성공하면 `shutdown.exe /s /f /t 60`을 **한 번** 요청합니다. 저장하지 않은 다른 앱의 작업도 종료될 수 있습니다. 예약 중 창의 **컴퓨터 종료 취소**를 누르거나 Windows에서 `shutdown.exe /a`를 실행하면 취소합니다. 예약 중 창을 닫아도 취소를 요청하며, 취소 실패 시 예약 유지 가능성을 표시합니다. 수집 중 창 닫기/취소는 정상 정리를 요청하고 기다립니다.

## 고정 조건과 시간

`dataclasses.replace(CONFIG, ...)`로 운영 경로와 `Regular Session`, `max_ws_symbols=150`만 지정하고 Controller 시작 옵션으로 Paper v1·Rotation v2·Opening Hour를 모두 켭니다. 후보 순위·진입·TP/SL·Rotation·완료봉·NULL 정책은 기존 코드 그대로입니다. Capacity Test로 전환하지 않습니다.

`exchange_calendars` XNYS 공식 open → **open+65분**에 `CollectorController.stop()`을 호출합니다. 첫 60분이 연구 대상이고 5분은 완료봉 확보 여유입니다. DST·휴장·조기폐장은 달력으로 처리합니다. 개장 gate·종료 관측 허용 오차는 기존과 같은 1초이며, 첫 batch는 open 후 31초 이내, 마지막 batch는 stop 전 31초 이내여야 자동 종료가 가능합니다(기존 30초 수집 주기+1초). 실제 discovery 지연 때문에 이 조건을 만족하지 못하면 실패 사유를 보존하고 PC를 유지합니다.

기존 Regular Session은 official close 기준이므로 `user_requested`, `stop_time_mismatch`, Validator FAIL을 그대로 남깁니다. 별도 요약의 VERIFIED는 이 계획된 단축 실행의 처리 결과입니다. 정규장 전체 수집 준수나 전략 수익성 PASS가 아닙니다. Paper 조기중단·post-exit INCOMPLETE·미래 horizon pending을 정상 종가 완료로 바꾸지 않습니다.

## 저장과 통합 ZIP

EXE 옆 `data/one_hour_runner/run_<unique-id>/`에 Radar, Paper v1, Rotation v2, Opening Hour, Master DB를 각각 새로 만듭니다. 같은 날 재실행해도 DB를 재사용하지 않습니다. 기존 운영 Paper/Rotation DB의 legacy schema를 열거나 변환하지 않습니다. 공유 historical cache는 기존 `data/market_radar_cache/kiwoom_v2.db`입니다. 실제 수집 시 기존 provider가 이 cache를 정상 갱신하지만 삭제·초기화·병합하지 않습니다. dry-run은 별도 synthetic cache만 사용합니다.

최종 `one_hour_research_<ET-date>_<controller-run-id>.zip` 구조:

| 영역 | 내용 |
|---|---|
| `radar/` | candidates, snapshots, events, results CSV; metadata, quality_summary, validation_summary JSON; review.zip |
| `paper_v1/` | paper_trades, paper_trade_events, paper_horizons, paper_trade_marks CSV; summary, identity JSON |
| `rotation_v2/` | paper_trades, paper_rotation_switches, paper_rotation_comparison, paper_post_exit, paper_post_exit_marks, paper_trade_marks, paper_horizons CSV; paper_daily_summary, identity JSON |
| `opening_hour/` | candidates, bars_1m, bars_3m, bars_5m, events, time_snapshots, outcomes, volume_segments CSV; metadata, quality, summary JSON |
| 최상위 | manifest.json, one_hour_run_summary.json |

`run_id`는 Controller/Paper/Opening의 UUID입니다. 기존 Master/Validator의 내용 기반 ID는 `review_run_id`로 별도 기록·검증합니다. 서로 다른 두 ID를 억지로 동일하게 바꾸지 않습니다. `radar/review.zip`은 기존 `create_review_zip()`이 생성하며 바깥 Radar 7개 파일과 byte/hash가 정확히 같아야 합니다. 중복은 이 7개 export뿐이며 분석할 때 한 벌만 읽습니다.

임시 ZIP → allowlist·필수 파일·CRC·SHA256·날짜/ID·nested Review 동등성·민감 필드 검사 → 동일 폴더 atomic rename → 최종 파일 재검증 순입니다. manifest 자체를 제외한 모든 파일을 해시합니다. 실행별 DB identity·Paper CSV/DB 일치와 기존 Validator fingerprint를 먼저 검사합니다. Paper/Rotation/Opening DB의 일관된 임시 복사본을 기존 공개 exporter로 다시 내보내 원래 export 전부와 해시를 비교합니다. SQLite 원본·WAL·cache·로그·.env·임시파일은 공유 ZIP에 넣지 않습니다. 검사기는 알려진 민감 필드·JWT·private key 패턴을 차단하며, 실제 인증값을 읽어 대조하지 않습니다.

## 자동 종료 차단

Controller 종료, clean shutdown, provider IO/WS/Paper thread 종료, 모든 export와 Opening 후처리, 계획 시각 준수, 실제 snapshot/batch, ZIP 검사 성공이 필요합니다. 저장/분석 오류, clock jump·5초 초과 관측 공백·절전 복귀, 늦은 준비, 다른 Validator 오류, WS gap·raw drop·transport/parse/worker/DB/export 오류에서는 종료하지 않습니다. Opening PARTIAL도 아래 coverage 기준을 충족해야 자동 종료할 수 있습니다. 거래 0건의 header-only CSV와 실제 거래량 0인 정상 OHLCV 분봉은 허용합니다. Windows 요청 실패를 표시하고 직접 process kill은 하지 않습니다. 정리에 오래 걸려도 기다립니다.

실행기 전용 collection_quality.policy.version=2는 수집 신뢰성과 연구 관측 범위를 분리합니다. 원래 Validator·Opening 판정·전략·schema는 유지합니다.

| 구분 | 판정과 기록 |
|---|---|
| batch / WS 연결 | 첫 60분의 30초 구간 95% 이상, 시작·끝 포함 최대 증거 간격 91초 이내 |
| 구독 / 신선한 관측 | 첫 대상 발견 이후 완전한 30초 구간의 승인 active·연결·신선한 관측 증거 95% 이상, 최대 공백 91초 이내. 종목별 active 표본도 검사하여 다른 정상 종목이 누락을 가리지 못하게 함 |
| 신선한 가격 | 기존 90초 이내의 양수 가격·수신시각·WS gap 없음. 무체결 분에 거래를 만들지 않으며 가상매매 진입 0건 허용 |
| 구독 확인 분봉 | 발견 이후 분의 앞·뒤 30초 표본 모두 연결·active가 확인된 분만 OHLCV 기준 분모로 사용. 전체 95%, 종목별 90%, 연속 결측 2분 이하 유지 |
| 연구 관측 범위 | 전체 eligible 후보의 발견 이후 coverage는 종전 분모로 별도 보존. 미구독·구독 경계·발견 전 구간을 실제 구독 중 누락으로 취급하지 않음 |
| 슬롯 제한 | ticker별 tracking_slot_unavailable 또는 slot_unavailable 근거가 있는 경우만 명시. 발견 후보 수가 150보다 많다는 사실만으로 추정하지 않음 |
| 증거 부족 | active/연결 정보가 없으면 미구독으로 분류하지 않고 unknown으로 기록. 기존 cadence·연결·구독 검사를 통해 종료 차단 |

구독은 연속 구간의 인증이 아니라 저장된 30초 표본의 증거입니다. 신선한 가격 증거 부족도 무조건 WS 단절이라고 단정하지 않지만 자동 종료 기준은 통과시키지 않습니다. 캐시 분봉 부족은 tick 유실의 직접 증거가 아닙니다. 구독 확인 분의 캐시 부족은 연구 완전성 검사에서 차단하되 WS 장애와 별도 수치로 기록합니다. 미구독·경계 구간의 캐시 부족은 범위 제한으로 남깁니다. 마지막 분 중간 발견과 유형 제외 후보의 분모 면제는 유지합니다.

2026-10-13 ET의 4,230/10,500분(40.29%)은 eligible 255개 중 발견 이후 평가할 온전한 1분 구간이 남아 있는 251개 후보의 합계입니다. 정책 2에서도 이 수치를 보존합니다. 같은 10,500분은 구독 확인 5,154분, 경계 167분, 미구독 5,179분으로 나뉘며 명시적 슬롯 부족은 0분입니다. 구독 확인 구간의 완전 OHLCV도 3,943/5,154분(76.50%)으로 기준 미달입니다. 40%로 기준을 낮춰 성공 처리하지 않았습니다.

## 실패 진단 ZIP

실패 시 one_hour_diagnostic_<ET-date>_<run-id>.zip을 별도로 만듭니다. 정상 ZIP 검증기로는 이 ZIP을 성공 자료로 인정하지 않습니다. 종료한 worker의 allowlist export만 읽으며 SQLite는 DB/WAL 복사본에서 조회합니다.

- manifest.json: archive_kind=diagnostic, status=FAILED, shutdown_eligible=false, 항목별 SHA256/크기.
- diagnostics.json: 실패 코드, Paper/sidecar 플래그와 공개 view의 Rotation 오류(확인 불가 시 null), DB 오류 이벤트, 원래 Validator JSON, 영역별 포함 수와 사용 제한, 누락·거부 파일과 사유, 가능한 경우 정책 2 집계.
- one_hour_run_summary.json: FAILED/진단 구분과 자동 종료 불가 상태.
- radar/, paper_v1/, rotation_v2/, opening_hour/: 문법·identity·민감정보 검사를 통과한 CSV/JSON만 포함. DB·WAL·cache·로그·.env·중첩 ZIP 제외.

민감하거나 잘못된 파일은 내용 없이 누락 사유만 남깁니다. 진단 ZIP의 CRC/SHA256/identity 검증은 전체 DB/export 의미 검증이나 수집 성공의 대체가 아닙니다. 임시 ZIP과 공개 후 ZIP을 각각 검사하며 작성·검증 실패도 자동 종료를 차단합니다. 실행 중인 worker의 파일은 진단용으로도 스냅샷하지 않습니다. 원본 export를 고치거나 삭제하지 않습니다.

## Paper 지연 오프라인 측정

replay_profile.py는 기존 PairedRuntime·Paper·Rotation 엔진을 변경하지 않고 원시 tick의 수신 순서·시각을 재생합니다. DB/WAL 복사본에서 입력을 읽고 새 DB에만 출력합니다. enqueue 지연, 큐 대기, dequeue 시점의 수신 대비 지연, Paper/Rotation 처리, SQL read/write와 transaction commit을 평균/p95/p99/최대로 기록합니다. transaction이 없는 context 종료는 commit 통계에서 분리합니다.

~~~powershell
& .\.venv-paddle\Scripts\python.exe -B -m tools.one_hour_runner.replay_profile --run <원본실행폴더> --cache <원본cache.db> --output <존재하지않는별도폴더> --journal default
~~~

--journal wal_full은 새 측정 DB에만 WAL/FULL을 적용하는 비교 실험이며 운영 설정을 바꾸지 않습니다. 재생 대상은 첫 WATCHING 종목 BKYI의 오류 시점까지 508개 FE tick과 초기 frame입니다. 후속 Radar frame을 포함한 전체 65분 수집 부하를 재현한 것은 아닙니다. 최초 frame enqueue는 저장되지 않아 WATCHING 평가시각으로 근사하며 당시 Radar/REST 부하와 CPU·디스크 상태는 재현하지 못합니다. 계측 비용과 중첩 시간이 포함되므로 각 단계 시간을 단순 합산하지 않습니다.

508개 tick 재생은 기본 DELETE와 WAL/FULL 모두 순서대로 508개를 처리했고 자연적인 2초 오류는 재현되지 않았습니다. 기본 실험의 큐 대기 p95/p99/max는 217.822/342.665/431.606ms, Paper/Rotation commit 평균은 3.224/3.289ms였습니다. WAL 비교의 큐 대기는 50.445/146.188/160.546ms였습니다. 저장 비용은 측정됐지만 실제 장애의 유일한 원인이라는 증거는 아닙니다. 기존 엔진/runtime/저장 설정과 2초 제한을 변경하지 않았습니다. 별도 합성 테스트에서 2.047033초 실패와 정확히 2초인 경계를 검증합니다.

Windows 요청 직전부터 별도의 예약 가능 상태를 유지합니다. 요청 성공 후 시계 조회나 상태 저장에 예외가 발생하면 즉시 취소를 시도합니다. OS 호출 결과를 받지 못한 경우도 취소 대상입니다. 취소 실패 시 시각을 알 수 없어도 `SHUTDOWN_PENDING`과 경고를 유지하고 창 닫기로 경고가 사라지지 않도록 합니다. `shutdown_status.json`은 예약/취소 상태를 별도로 보존합니다. ZIP은 OS 요청 전 수집 검증 결과이므로 이후 Windows 종료 성공의 증거가 아닙니다.

전역 Windows mutex는 서로 다른 폴더의 이 실행기 중복을 막습니다. 기존 provider의 공개 CollectorLock과 알려진 StockHelper/Python launcher process도 검사하고, 기존 앱이 대기 중이어도 보수적으로 거부합니다. 기존 앱은 machine mutex를 사용하지 않으므로 **자동 실행기를 시작한 뒤 다른 복사본의 legacy 앱을 새로 켜는 경우의 원자적 상호 배제는 기존 코드를 수정하지 않고는 보장할 수 없습니다**. 실행 중 감지하면 정상 중지하고 자동 종료를 차단합니다. 운영 PC에서는 수집 앱 하나만 사용합니다.

## 빌드와 검증

2026-10-14 v1.0.1 배포 전 검증: 전체 회귀 597/597(기존 575+신규 22), EXE 내장 실행기 코드의 추가 회귀 118/118 통과. EXE의 GUI/headless `--dry-run` 정상 ZIP은 각각 36개 항목과 내부 Review 8개 항목을 검증했습니다. EXE에서 추출한 실행기 코드에 Paper/Rotation/품질 실패를 주입해 34개 항목 FAILED 진단 ZIP을 확인했고, ZIP 쓰기·검증 실패에서도 종료 요청은 0건이었습니다. 실패 주입은 외부 합성 harness이며 EXE의 별도 실패 CLI 모드가 아닙니다. 실제 API·PC 종료·v1.0.1 실제 시장 수집은 실행하지 않았습니다.

기존 `build.ps1`의 고정 출력 경로를 사용하지 않고 `build/one_hour_runner/predeploy_v1_0_1_20261014_020318/`의 임시 spec에서 프로젝트 위치, COLLECT 이름, 독립 `replay_profile` hidden import만 지정했습니다. 프로젝트의 빌드 스크립트/spec/requirements와 Python 코드는 변경하지 않았습니다. 새 EXE SHA256은 `d5fb809ccf683417648ffad5729f677c076d2b9fe527191568f0111d7c866bff`입니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\one_hour_runner\build.ps1 -Python .\.venv-paddle\Scripts\python.exe
& .\.venv-paddle\Scripts\python.exe -m unittest tests.one_hour_runner_test tests.one_hour_runner_audit_test tests.one_hour_runner_stability_test -q
```

빌드는 `build/one_hour_runner/venv`의 전용 최소 가상환경을 사용하고 `dist/OneHourResearch/OneHourResearch.exe`를 만듭니다. OCR/Torch/Paddle/NH 실행 코드는 제외합니다. 원래 `StockHelper.spec`, `build_windows.ps1`을 호출하지 않습니다. 출력 폴더에 `data/` 또는 `.env`가 있으면 재빌드를 거부하여 보존합니다. `-Wheelhouse <폴더>`로 로컬 wheel만 설치하거나 `-SkipInstall`로 이미 준비된 전용 환경을 사용할 수 있습니다. `ExecutionPolicy Bypass`는 이 빌드 프로세스에만 적용되며 Windows 설정을 영구 변경하지 않습니다. PyInstaller의 [onedir spec 구성](https://pyinstaller.org/en/stable/spec-files.html)을 사용합니다. `--dry-run`은 합성 Controller facade와 실제 기존 exporter/Validator/연구 모듈의 조합입니다. 이것이 실제 Kiwoom 수집이나 실제 Windows 종료 성공을 검증하지는 않습니다.
