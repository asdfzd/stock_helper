[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv-paddle\Scripts\python.exe"
$spec = Join-Path $projectRoot "StockHelper.spec"
$distribution = Join-Path $projectRoot "dist\StockHelper"
$runtimeResults = Join-Path $distribution "ocr_results"
$runtimeBackup = Join-Path $projectRoot ".build-runtime-backup"
$modelSourceRoot = Join-Path $projectRoot ".paddle-cache\official_models"
$modelNames = @(
    "PP-OCRv5_mobile_det",
    "korean_PP-OCRv5_mobile_rec"
)

$runningInstances = @(Get-Process -Name "StockHelper" -ErrorAction SilentlyContinue)
if ($runningInstances.Count -gt 0) {
    $runningIds = ($runningInstances | ForEach-Object { $_.Id }) -join ", "
    throw "StockHelper is running (PID: $runningIds). Close it before building."
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Paddle virtual environment not found: $python"
}

foreach ($modelName in $modelNames) {
    $modelPath = Join-Path $modelSourceRoot $modelName
    if (-not (Test-Path -LiteralPath $modelPath -PathType Container)) {
        throw "OCR model not found: $modelPath"
    }
}

$projectRootResolved = [System.IO.Path]::GetFullPath($projectRoot)
$runtimeBackupResolved = [System.IO.Path]::GetFullPath($runtimeBackup)
if (-not $runtimeBackupResolved.StartsWith($projectRootResolved, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Runtime backup path escaped the project root: $runtimeBackupResolved"
}
if (Test-Path -LiteralPath $runtimeBackup) {
    if (Test-Path -LiteralPath $runtimeResults) {
        throw "Both runtime results and a stale backup exist: $runtimeBackup"
    }
    New-Item -ItemType Directory -Path $distribution -Force | Out-Null
    Move-Item -LiteralPath $runtimeBackup -Destination $runtimeResults
}
$runtimeResultsBackedUp = $false
if (Test-Path -LiteralPath $runtimeResults -PathType Container) {
    Move-Item -LiteralPath $runtimeResults -Destination $runtimeBackup
    $runtimeResultsBackedUp = $true
}

Push-Location $projectRoot
try {
    $pyinstallerArguments = @("-m", "PyInstaller", "--noconfirm")
    if ($Clean) {
        $pyinstallerArguments += "--clean"
    }
    $pyinstallerArguments += $spec
    & $python @pyinstallerArguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE."
    }

    $modelDestinationRoot = Join-Path $distribution ".paddle-cache\official_models"
    New-Item -ItemType Directory -Path $modelDestinationRoot -Force | Out-Null
    foreach ($modelName in $modelNames) {
        Copy-Item -LiteralPath (Join-Path $modelSourceRoot $modelName) -Destination $modelDestinationRoot -Recurse -Force
    }

    Copy-Item -LiteralPath ".env.example" -Destination $distribution -Force
    if (Test-Path -LiteralPath ".env" -PathType Leaf) {
        Copy-Item -LiteralPath ".env" -Destination $distribution -Force
    }

    if ($runtimeResultsBackedUp -and (Test-Path -LiteralPath $runtimeBackup)) {
        Move-Item -LiteralPath $runtimeBackup -Destination $runtimeResults
        $runtimeResultsBackedUp = $false
    }

    Write-Host "Build complete: $(Join-Path $distribution 'StockHelper.exe')"
}
finally {
    if ($runtimeResultsBackedUp -and (Test-Path -LiteralPath $runtimeBackup)) {
        New-Item -ItemType Directory -Path $distribution -Force | Out-Null
        if (-not (Test-Path -LiteralPath $runtimeResults)) {
            Move-Item -LiteralPath $runtimeBackup -Destination $runtimeResults
        }
    }
    if (
        (Test-Path -LiteralPath (Join-Path $projectRoot ".env") -PathType Leaf) -and
        (Test-Path -LiteralPath $distribution -PathType Container) -and
        -not (Test-Path -LiteralPath (Join-Path $distribution ".env") -PathType Leaf)
    ) {
        Copy-Item -LiteralPath (Join-Path $projectRoot ".env") -Destination $distribution -Force
    }
    Pop-Location
}
