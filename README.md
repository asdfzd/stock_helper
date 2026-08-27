# stock_helper

Windows에서 영웅문 Global Tooltip을 캡처·OCR하고, Toss Open API 현재가와 결합해 종목 카드로 표시하는 주식 매매 보조 도구입니다.

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

## 실행

메인 UI:

```bash
./.venv-paddle/Scripts/python.exe ./live_ui.py
```

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
- `tests/`: 외부 서비스 없이 반복 실행하는 자동·회귀 테스트
- `tools/`: live capture, OCR 비교, 저장 JSON 파싱, Toss API 수동 진단 도구

대표 회귀 테스트:

```bash
./.venv-paddle/Scripts/python.exe ./tests/ui_registry_integration_test.py
./.venv-paddle/Scripts/python.exe ./tests/parser_regression_test.py
```

OCR 엔진 비교(PowerShell 스크립트를 Git Bash에서 실행):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./tools/run_ocr_comparison.ps1
```
