# stock_helper

작업 시작 전:

git pull

작업 완료 후:

git add .

git commit -m "작업 내용"

git push

## 환경변수 설정

프로젝트 루트에 `.env` 파일을 생성하고 아래 형식으로 토스증권 Open API 정보를 입력합니다.

````env
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=


## 새 PC / 노트북에서 시작하기

### 저장소 받기

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/asdfzd/stock_helper.git
cd stock_helper
code .

메인 가상환경 생성
py -3.11 -m venv .venv
./.venv/Scripts/python.exe -m pip install --upgrade pip
./.venv/Scripts/python.exe -m pip install -r requirements.txt

PaddleOCR 가상환경 생성
py -3.11 -m venv .venv-paddle
./.venv-paddle/Scripts/python.exe -m pip install --upgrade pip
./.venv-paddle/Scripts/python.exe -m pip install -r requirements-paddle.txt

--------------------------------

Parser-only 테스트
./.venv-paddle/Scripts/python.exe ./live_parser_test.py

실제 Live Capture 테스트
./.venv-paddle/Scripts/python.exe ./live_capture_test.py


````
