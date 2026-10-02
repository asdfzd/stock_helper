# Stock Helper

미국 주식 단기매매 보조 및 시장 모니터링을 위한 개인용 Windows 데스크톱 프로그램입니다. 영웅문 Global 화면의 Tooltip을 캡처·OCR하고, 종목·가격 정보를 Toss 현재가와 결합해 종목 카드에 표시합니다.

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
| Market Radar 앱 버전 | **v2.2.2** (Dashboard / regular-session scheduling) |
| Collector 연구 버전 | **v2.1** |
| test_version | `kiwoom_radar_v2_1` |
| Research schema | **4** |
| Master schema | **1** |

프로그램 버전과 DB schema 버전은 별도로 관리합니다. 현재 다음 기능을 제공합니다.

- 실제 Kiwoom read-only REST/WebSocket 연결, 주문·계좌 기능 없음
- ETF·워런트 등 확인 가능한 비보통주 필터; 확인되지 않은 유형은 unknown으로 보존
- protected tracking과 Daily Quality Summary
- graceful Ctrl+C shutdown과 최종 export
- REST deterministic failure suppression 및 결과 샘플 감사 정보
- Dashboard Start/Stop, System Health, Session History, 종목별 결과 조회
- Manual / Regular Session / Capacity Test: XNYS 개장 준비·자동 시작·graceful 종료와 protocol 기록
- 읽기 전용 과거 세션 import, 별도 Master Research DB, 검수용 Review ZIP

20/50에 이어 실제 100-slot 세션도 saturation 100%에 도달했습니다. 다음 사용자 capacity 검증은 **150 slots**입니다. 기존 CONFIG/저장된 설정은 유지하며 150/200을 최적값으로 자동 선택하지 않습니다. 200은 공식 상한입니다. 기존 테스트·실제 검증의 범위와 제한은 [현재 개발 상태](CODEX_HANDOFF.md), 버전별 근거는 [CHANGELOG](CHANGELOG.md)를 참고하세요.

## 문서 안내

다음 150-slot 테스트는 Dashboard에서 **Capacity Test / Max WS 150**을 선택하고 **공식 개장 전에 Start Collection**으로 arm합니다. 개장 후 자동 시작하고 open+65분에 자동 종료합니다. 기존 20/50/100은 saturation 증거이며 새로운 표준 통제 비교로 소급 지정하지 않습니다. Regular Session은 공식 close(조기폐장 포함)에 종료하며 장중 시작은 비준수로 기록합니다. 자세한 실행 기준은 [TEST_PROTOCOL](TEST_PROTOCOL.md)을 따릅니다.

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
4. **Stop Collection** → STOPPING → STOPPED를 기다립니다. final export 후 Master DB에 자동 등록됩니다. 실패 시 **Import Existing Session**으로 다시 가져올 수 있습니다.
5. **Session History**에서 세션을 선택해 후보·결과·품질·원본 경로를 확인하고 **Create Review ZIP**을 누릅니다. ZIP은 해당 세션의 `reviews/`에 저장됩니다.
6. **Open Review ZIP Folder**에서 ZIP을 찾아 검수에 전달합니다. 기본 ZIP에는 DB·raw·인증 파일이 포함되지 않습니다. 민감정보 패턴이 발견되면 생성하지 않습니다.

Dashboard historical cache는 실행 폴더와 별도로 `data/market_radar_cache/kiwoom_v2.db`를 계속 재사용합니다. Data root를 바꿔도 cache는 이동하지 않으며 **Historical Cache** tooltip에서 전체 경로를 확인합니다. 호환되지 않는 cache는 거부하고 과거 session cache를 자동 병합하지 않습니다. 이번 감사 당시 canonical cache는 없었으므로 다음 실제 수집에서 생성됩니다. 공유해도 과거 coverage가 부족하면 RVOL은 계속 `-`입니다.

STOPPED의 **Current Active WS=0 / Current Connected=No**는 현재 연결 상태입니다. **Last Active WS / Last Protected / Last Slot saturation**은 마지막 batch 값이며 그대로 보존됩니다. 정상 종료의 `Stopped cleanly`와 shutdown 시간도 유지됩니다. Session History의 **Slot unavailable events**는 순간 종목 수가 아닌 누적 이벤트 수입니다.

기존 CLI는 `--data-dir` 아래 cache를 만드는 동작을 유지합니다. CLI에서도 공유하려면 `--cache-path data/market_radar_cache/kiwoom_v2.db`를 명시합니다. 다음 실장 테스트는 UI Max WS를 **150**으로 지정하고 수행하세요. 이번 패치 작업에서 live smoke/150-slot 테스트를 자동 수행하지 않았습니다.

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
