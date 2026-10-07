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

## Current Version

| 구분 | 현재 값 |
|---|---|
| Market Radar 앱 버전 | **v2.3.0** (Automatic Session Validator) |
| Session Validator | **1** (수집 품질 검증; 전략 판단 아님) |
| Collector 연구 버전 | **v2.1** |
| test_version | `kiwoom_radar_v2_1` |
| Research schema | **4** |
| Master schema | **1** |
| Windows Quick Launch | **v1** (`.venv-paddle` 실행, EXE 패키징 없음) |

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

새 개발 대화는 [START_HERE](START_HERE.md)에서 시작합니다. 최상위 방향은 [PROJECT_DIRECTION](PROJECT_DIRECTION.md), 연구 가설과 출처 확인 수준은 [RESEARCH_BASIS](RESEARCH_BASIS.md)를 따릅니다. 현재는 Phase A 마무리~Phase B 시작으로, 고정 슬롯의 Regular Session을 preopen arm해 공식 close까지 비교 가능한 데이터를 축적합니다. 1주는 첫 분석 표본입니다. Capacity 비교를 추가할 때에는 **Capacity Test / Max WS 지정 / preopen arm → open+65분 자동 종료**를 사용합니다. 기존 20/50/100은 새로운 표준 통제 비교로 소급 지정하지 않습니다. 자세한 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md)을 따릅니다.

- [Market Radar 기술 문서](market_radar/README.md): provider·지표·추적·품질·CLI·제한
- [CHANGELOG.md](CHANGELOG.md): 영구 버전별 개발·검증 이력
- [CODEX_HANDOFF.md](CODEX_HANDOFF.md): 현재 개발 상태와 다음 작업
- [AGENTS.md](AGENTS.md): 프로젝트 작업·문서 갱신 규칙

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

## 관리자 권한 Windows EXE 빌드

빌드 도구를 설치하고 PowerShell에서 빌드합니다.

```powershell
& .\.venv-paddle\Scripts\python.exe -m pip install -r .\requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

결과물은 `dist\StockHelper\StockHelper.exe`입니다. EXE에는 관리자 권한 요청
매니페스트가 포함되어 실행할 때 UAC 승인 창이 표시됩니다. `_internal`과
`.paddle-cache`도 실행에 필요하므로 `dist\StockHelper` 폴더 전체를 함께
이동해야 합니다.

빌드할 때 프로젝트 루트의 `.env`가 있으면 EXE 옆으로 복사됩니다. 배포 폴더를
다른 사람에게 전달할 때는 API 인증정보가 든 `.env`를 반드시 제거하십시오.

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
