param(
    [Parameter(Mandatory = $true)]
    [string]$SourceExePath,

    [Parameter(Mandatory = $true)]
    [string]$ServerBaseUrl,

    [string]$InstallDir = "C:\Program Files\FIMAgent",
    [string]$TaskName = "FIM Agent",
    [int]$HeartbeatSeconds = 30,
    [int]$PollSeconds = 15,
    [Parameter(Mandatory = $true)]
    [string[]]$MonitorPaths
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -Path $SourceExePath)) {
    throw "Source EXE not found: $SourceExePath"
}

if (-not $ServerBaseUrl.StartsWith("http://") -and -not $ServerBaseUrl.StartsWith("https://")) {
    throw "ServerBaseUrl must start with http:// or https://"
}

if ($MonitorPaths.Count -eq 0) {
    throw "MonitorPaths cannot be empty. Provide one or more file/directory paths to monitor."
}

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$targetExe = Join-Path -Path $InstallDir -ChildPath "fim-agent.exe"
Copy-Item -Path $SourceExePath -Destination $targetExe -Force

$agentId = $env:COMPUTERNAME
$config = [ordered]@{
    server_base_url   = $ServerBaseUrl.TrimEnd("/")
    agent_name        = $agentId
    agent_id          = $agentId
    agent_port        = $null
    heartbeat_seconds = $HeartbeatSeconds
    poll_seconds      = $PollSeconds
    monitor_mode      = "multiple_paths"
    monitor_targets   = $MonitorPaths
    scan_paths        = $MonitorPaths
    monitored_files   = @()
}

$configPath = Join-Path -Path $InstallDir -ChildPath "config.json"
$config | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding UTF8

$action = New-ScheduledTaskAction -Execute $targetExe
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "[INFO] FIM Agent deployed successfully"
Write-Host "[INFO] EXE: $targetExe"
Write-Host "[INFO] Config: $configPath"
Write-Host "[INFO] Scheduled Task: $TaskName"
