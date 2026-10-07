[CmdletBinding()]
param([string]$IconPath)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot 'launch_stock_helper.ps1'
$shellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'The current user Desktop folder is unavailable.'
}
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw 'The Stock Helper launcher is missing.'
}
$shortcutPath = Join-Path $desktop 'Stock Helper.lnk'
$arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $launcher
$wsh = New-Object -ComObject WScript.Shell
$shortcut = $null
try {
    $shortcut = $wsh.CreateShortcut($shortcutPath)
    if (Test-Path -LiteralPath $shortcutPath) {
        if ($shortcut.TargetPath -ine $shellPath -or
            $shortcut.WorkingDirectory -ine $projectRoot -or
            $shortcut.Arguments -ine $arguments) {
            throw 'Stock Helper.lnk already belongs to another target. Rename that shortcut before retrying; it has not been changed.'
        }
    }
    if (-not $IconPath) {
        $IconPath = Join-Path $projectRoot 'assets\stock_helper.ico'
    } elseif (-not [IO.Path]::IsPathRooted($IconPath)) {
        $IconPath = Join-Path $projectRoot $IconPath
    }
    if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
        $IconPath = Join-Path $projectRoot '.venv-paddle\Scripts\pythonw.exe'
    }
    if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
        $IconPath = Join-Path $projectRoot '.venv-paddle\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $IconPath -PathType Leaf)) {
        $IconPath = Join-Path $env:SystemRoot 'System32\shell32.dll'
    }
    $shortcut.TargetPath = $shellPath
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = 'Stock Helper / Market Radar'
    $shortcut.IconLocation = "$IconPath,0"
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    [pscustomobject]@{
        Shortcut = $shortcutPath
        Target = $shortcut.TargetPath
        WorkingDirectory = $shortcut.WorkingDirectory
        Arguments = $shortcut.Arguments
        IconLocation = $shortcut.IconLocation
    }
} finally {
    if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($wsh)
}
