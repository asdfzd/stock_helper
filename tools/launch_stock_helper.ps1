[CmdletBinding()]
param([switch]$DebugMode)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot

function Show-LaunchError([string]$Message) {
    $notice = "$Message`r`nRun launch_stock_helper_debug.cmd to inspect errors."
    if ($DebugMode) {
        [Console]::Error.WriteLine($notice)
    } else {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show(
            $notice, 'Stock Helper', 'OK', 'Error')
    }
}

try {
    Set-Location -LiteralPath $projectRoot
    $entryPoint = Join-Path $projectRoot 'live_ui.py'
    $venvScripts = Join-Path $projectRoot '.venv-paddle\Scripts'
    $python = Join-Path $venvScripts 'python.exe'
    $pythonw = Join-Path $venvScripts 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
        Show-LaunchError 'live_ui.py is missing from the project folder.'
        exit 1
    }
    if (-not (Test-Path -LiteralPath $venvScripts -PathType Container)) {
        Show-LaunchError 'The .venv-paddle environment is missing. See README installation instructions.'
        exit 1
    }
    if (-not $DebugMode -and (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        $python = $pythonw
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Show-LaunchError 'The required Python executable is missing from .venv-paddle\Scripts.'
        exit 1
    }
    if ($DebugMode) {
        & $python $entryPoint
        exit $LASTEXITCODE
    }
    # Keep the fallback python.exe console hidden too. Observe failures without
    # recording output or exposing configuration/credentials in a message box.
    $appProcess = Start-Process -FilePath $python -ArgumentList ('"{0}"' -f $entryPoint) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $appProcess.WaitForExit()
    $appExitCode = $appProcess.ExitCode
    if ($appExitCode -ne 0) {
        Show-LaunchError "Stock Helper exited with code $appExitCode."
    }
    exit $appExitCode
} catch {
    Show-LaunchError 'Stock Helper could not be launched.'
    exit 1
}
