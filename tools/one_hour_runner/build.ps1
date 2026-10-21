param([string]$Python = "python", [switch]$SkipInstall, [string]$Wheelhouse = "")
$ErrorActionPreference = "Stop"
$Project = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$BuildRoot = Join-Path $Project "build/one_hour_runner"
$Venv = Join-Path $BuildRoot "venv"
$VenvPython = Join-Path $Venv "Scripts/python.exe"
$Output = Join-Path $Project "dist/OneHourResearch"
# PyInstaller replaces its onedir target. Refuse to overwrite runtime evidence
# or user-managed authentication if somebody has already operated that copy.
if ((Test-Path -LiteralPath (Join-Path $Output "data")) -or
    (Test-Path -LiteralPath (Join-Path $Output ".env"))) {
    throw "Output contains runtime data or user settings; preserve it and choose a clean deployment folder before rebuilding"
}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }
}
if (-not $SkipInstall) {
    $InstallOptions = @()
    if ($Wheelhouse) { $InstallOptions = @("--no-index", "--find-links", $Wheelhouse) }
    & $VenvPython -m pip install @InstallOptions -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
}
& $VenvPython -c "import importlib.util,sys; assert sys.prefix != sys.base_prefix; assert not any(importlib.util.find_spec(n) for n in ('paddle','paddleocr','easyocr','torch','cv2'))"
if ($LASTEXITCODE -ne 0) { throw "Build environment contains excluded dependencies" }
Push-Location $Project
try {
    & $VenvPython -m PyInstaller --noconfirm --clean --distpath (Join-Path $Project "dist") --workpath (Join-Path $BuildRoot "pyinstaller") (Join-Path $PSScriptRoot "OneHourResearch.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    # Only package code/runtime resources. No credentials, original data, models or logs are copied.
    $Forbidden = Get-ChildItem -LiteralPath $Output -Recurse -File | Where-Object {
        $_.Name -eq ".env" -or $_.Extension -in ".db", ".sqlite", ".sqlite3", ".log" -or
        $_.FullName -match '(?i)[\\/](paddle|paddleocr|easyocr|torch|torchvision|\.paddle-cache)([\\/]|$)'
    }
    if ($Forbidden) { throw "Package audit found forbidden runtime data/dependencies" }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination (Join-Path $Output "USER_GUIDE.md")
    Write-Output (Join-Path $Output "OneHourResearch.exe")
} finally { Pop-Location }
