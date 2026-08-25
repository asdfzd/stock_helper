$ErrorActionPreference = "Stop"

$toolsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $toolsRoot
$easyPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$paddlePython = Join-Path $projectRoot ".venv-paddle\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $easyPython)) {
    throw "EasyOCR 가상환경을 찾을 수 없습니다: $easyPython"
}
if (-not (Test-Path -LiteralPath $paddlePython)) {
    throw "PaddleOCR 가상환경을 찾을 수 없습니다: $paddlePython"
}

& $easyPython (Join-Path $toolsRoot "ocr_compare_easyocr.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $paddlePython (Join-Path $toolsRoot "ocr_compare_paddleocr.py")
exit $LASTEXITCODE
