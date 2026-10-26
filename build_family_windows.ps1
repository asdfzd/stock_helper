[CmdletBinding()]
param([string]$OutputName = '', [switch]$PreflightOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot '.venv-paddle\Scripts\python.exe'
Push-Location $projectRoot
try {
    $version = (& $python -B -c "from market_radar import __version__; print(__version__)").Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read version' }
    if (-not $OutputName) { $OutputName = "MarketRadarFamily_v$($version.Replace('.', '_'))" }
    if ($OutputName -notmatch '^MarketRadarFamily_[A-Za-z0-9_]+$') { throw 'Invalid new output name' }
    $destination = Join-Path $projectRoot "dist\$OutputName"
    $work = Join-Path $projectRoot "build\$OutputName"
    if ((Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath $work)) { throw 'Output already exists' }
    & $python -B -c "import PyInstaller, PySide6, exchange_calendars, psutil, shutil; assert shutil.disk_usage('.').free > 3*1024**3"
    if ($LASTEXITCODE -ne 0) { throw 'Dependencies or free space preflight failed' }
    if ($PreflightOnly) { return }
    $stage = Join-Path $work 'distribution'
    & $python -B -m PyInstaller --distpath $stage --workpath (Join-Path $work 'pyinstaller') (Join-Path $projectRoot 'MarketRadarFamily.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
    $built = [IO.Path]::GetFullPath((Join-Path $stage 'MarketRadarFamily'))
    $resolvedDestination = [IO.Path]::GetFullPath($destination)
    if (-not $built.StartsWith([IO.Path]::GetFullPath((Join-Path $projectRoot 'build')) + '\', [StringComparison]::OrdinalIgnoreCase) -or
        -not $resolvedDestination.StartsWith([IO.Path]::GetFullPath((Join-Path $projectRoot 'dist')) + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Output escaped workspace'
    }
    Move-Item -LiteralPath $built -Destination $resolvedDestination
    & $python -B -m tools.family_smoke --manifest $resolvedDestination
    if ($LASTEXITCODE -ne 0) { throw 'Manifest failed' }
    Write-Host "Built $resolvedDestination\MarketRadarFamily.exe (entire onedir required; no credentials included)"
} finally { Pop-Location }
