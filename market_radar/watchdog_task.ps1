param(
    [ValidateSet('register', 'query', 'delete')][string]$Operation,
    [ValidatePattern('^MarketRadarFamily_(TEST_)?[0-9]{8}_[a-f0-9]{32}$')][string]$TaskName,
    [string]$XmlPath
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $folder = $service.GetFolder('\')
    if ($Operation -eq 'register') {
        $definition = [System.IO.File]::ReadAllText($XmlPath)
        # TASK_CREATE only. Never overwrite another task, even one with our prefix.
        $null = $folder.RegisterTask($TaskName, $definition, 2, $null, $null, 3, $null)
    } elseif ($Operation -eq 'delete') {
        $folder.DeleteTask($TaskName, 0)
    }
    $xml = $null
    $policy = $null
    if ($Operation -ne 'delete') {
        try {
            $task = $folder.GetTask($TaskName)
            $xml = $task.Xml
            $definition = $task.Definition
            $policy = @{
                'Settings/StartWhenAvailable' = $definition.Settings.StartWhenAvailable.ToString().ToLowerInvariant()
                'Settings/WakeToRun' = $definition.Settings.WakeToRun.ToString().ToLowerInvariant()
                'Settings/Enabled' = $definition.Settings.Enabled.ToString().ToLowerInvariant()
                'Principals/Principal/RunLevel' = $(if ($definition.Principal.RunLevel -eq 0) { 'LeastPrivilege' } else { 'HighestAvailable' })
            }
        }
        catch {
            if ($Operation -ne 'query' -or $_.Exception.HResult -ne -2147024894) { throw }
        }
    }
    @{ xml = $xml; policy = $policy } | ConvertTo-Json -Compress -Depth 4
} catch {
    @{ error = 'task_operation_failed'; code = $_.Exception.HResult } | ConvertTo-Json -Compress
    exit 1
}
