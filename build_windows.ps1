[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$OutputName = "",
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-paddle\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Paddle virtual environment not found: $python"
}
Push-Location $projectRoot
try {
    $version = (& $python -B -c "from market_radar import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0) { throw "Cannot read application version." }
    if (-not $OutputName) { $OutputName = "StockHelper_v$($version.Replace('.', '_'))_TEST" }
    if ($OutputName -notmatch '^StockHelper_[A-Za-z0-9_]+$') { throw "Use a distinct StockHelper_ output name." }
    $distribution = Join-Path $projectRoot "dist\$OutputName"
    $work = Join-Path $projectRoot "build\$OutputName"
    # Never move user data, overwrite a previous distribution, or copy credentials.
    if ((Test-Path -LiteralPath $distribution) -or (Test-Path -LiteralPath $work)) {
        throw "Output/work path already exists; select a new OutputName."
    }
    $modelSourceRoot = Join-Path $projectRoot ".paddle-cache\official_models"
    $modelNames = @("PP-OCRv5_mobile_det", "korean_PP-OCRv5_mobile_rec")
    foreach ($modelName in $modelNames) {
        foreach ($file in @("inference.json", "inference.pdiparams", "inference.yml")) {
            $modelFile = Join-Path $modelSourceRoot "$modelName\$file"
            if (-not (Test-Path -LiteralPath $modelFile -PathType Leaf)) { throw "OCR model file missing: $modelFile" }
        }
    }
    & $python -B -c "import importlib.util, shutil; names=['PyInstaller','PySide6','paddle','paddleocr','paddlex','exchange_calendars','tzdata','websockets','psutil']; missing=[n for n in names if importlib.util.find_spec(n) is None]; print('Missing dependencies:', missing); free=shutil.disk_usage('.').free; print('Free bytes:',free); raise SystemExit(bool(missing) or free < 10*1024**3)"
    if ($LASTEXITCODE -ne 0) { throw "Preflight failed (dependencies or less than 10 GiB free)." }
    if ($PreflightOnly) { return }
    $stage = Join-Path $work "distribution"
    $arguments = @("-B", "-m", "PyInstaller", "--distpath", $stage, "--workpath", (Join-Path $work "pyinstaller"))
    if ($Clean) { $arguments += "--clean" }
    $arguments += (Join-Path $projectRoot "StockHelper.spec")
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
    $built = [IO.Path]::GetFullPath((Join-Path $stage "StockHelper"))
    $destination = [IO.Path]::GetFullPath($distribution)
    $allowedBuild = [IO.Path]::GetFullPath((Join-Path $projectRoot "build")) + [IO.Path]::DirectorySeparatorChar
    $allowedDist = [IO.Path]::GetFullPath((Join-Path $projectRoot "dist")) + [IO.Path]::DirectorySeparatorChar
    if (-not $built.StartsWith($allowedBuild, [StringComparison]::OrdinalIgnoreCase) -or
        -not $destination.StartsWith($allowedDist, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build output escaped workspace."
    }
    Move-Item -LiteralPath $built -Destination $destination
    $modelDestination = Join-Path $distribution ".paddle-cache\official_models"
    New-Item -ItemType Directory -Path $modelDestination -Force | Out-Null
    foreach ($modelName in $modelNames) {
        $target = Join-Path $modelDestination $modelName
        New-Item -ItemType Directory -Path $target | Out-Null
        foreach ($file in @("inference.json", "inference.pdiparams", "inference.yml")) {
            Copy-Item -LiteralPath (Join-Path $modelSourceRoot "$modelName\$file") -Destination $target
        }
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $distribution
    & $python -B -m tools.packaged_smoke --manifest $distribution
    if ($LASTEXITCODE -ne 0) { throw "Build manifest failed." }
    Write-Host "Build complete: $(Join-Path $distribution 'StockHelper.exe')"
    Write-Host "Distribute the entire folder, including _internal and .paddle-cache. No .env was copied."
} finally {
    Pop-Location
}
