param(
    [Parameter(Mandatory = $true)]
    [string]$DomainFqdn,

    [string]$SourceExePath = ".\\dist\\fim-agent.exe",

    [Parameter(Mandatory = $true)]
    [string]$ServerBaseUrl,

    [Parameter(Mandatory = $true)]
    [string[]]$MonitorPaths,

    [string]$ShareSubPath = "scripts\\FIMAgent",
    [string]$StartupScriptName = "Deploy-FIMAgent-Startup.ps1",
    [string]$InstallDir = "C:\\Program Files\\FIMAgent",
    [string]$TaskName = "FIM Agent",
    [int]$HeartbeatSeconds = 30,
    [int]$PollSeconds = 15
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -Path $SourceExePath)) {
    throw "Source EXE not found: $SourceExePath"
}

if (-not $ServerBaseUrl.StartsWith("http://") -and -not $ServerBaseUrl.StartsWith("https://")) {
    throw "ServerBaseUrl must start with http:// or https://"
}

if ($MonitorPaths.Count -eq 0) {
    throw "MonitorPaths cannot be empty. Provide one or more file/directory paths."
}

$deployScriptSource = Join-Path -Path $PSScriptRoot -ChildPath "Deploy-FIMAgent-GPOStartup.ps1"
if (-not (Test-Path -Path $deployScriptSource)) {
    throw "Deploy script not found beside this file: $deployScriptSource"
}

$normalizedSubPath = $ShareSubPath -replace "^\\+", "" -replace "^/+", ""
$normalizedSubPath = $normalizedSubPath -replace "/", "\\"
$sysvolBase = "\\\\$DomainFqdn\\SYSVOL\\$DomainFqdn"
$publishRoot = Join-Path -Path $sysvolBase -ChildPath $normalizedSubPath

New-Item -ItemType Directory -Path $publishRoot -Force | Out-Null

$exeTargetPath = Join-Path -Path $publishRoot -ChildPath "fim-agent.exe"
$deployScriptTargetPath = Join-Path -Path $publishRoot -ChildPath "Deploy-FIMAgent-GPOStartup.ps1"
$startupScriptPath = Join-Path -Path $publishRoot -ChildPath $StartupScriptName

Copy-Item -Path $SourceExePath -Destination $exeTargetPath -Force
Copy-Item -Path $deployScriptSource -Destination $deployScriptTargetPath -Force

$monitorPathArrayLiteral = ($MonitorPaths | ForEach-Object {
    $escaped = $_ -replace "'", "''"
    "'{0}'" -f $escaped
}) -join ", "

$serverLiteral = $ServerBaseUrl.TrimEnd("/") -replace "'", "''"
$installLiteral = $InstallDir -replace "'", "''"
$taskLiteral = $TaskName -replace "'", "''"

$wrapperContent = @"
`$ErrorActionPreference = 'Stop'

`$deployScript = Join-Path -Path `$PSScriptRoot -ChildPath 'Deploy-FIMAgent-GPOStartup.ps1'
`$exePath = Join-Path -Path `$PSScriptRoot -ChildPath 'fim-agent.exe'

`$logRoot = 'C:\\ProgramData\\FIMAgent'
New-Item -ItemType Directory -Path `$logRoot -Force | Out-Null
`$logPath = Join-Path -Path `$logRoot -ChildPath 'gpo-startup.log'

try {
    & `$deployScript `
        -SourceExePath `$exePath `
        -ServerBaseUrl '$serverLiteral' `
        -InstallDir '$installLiteral' `
        -TaskName '$taskLiteral' `
        -HeartbeatSeconds $HeartbeatSeconds `
        -PollSeconds $PollSeconds `
        -MonitorPaths @($monitorPathArrayLiteral)

    "[$(Get-Date -Format s)] SUCCESS: FIM Agent deployment completed." | Out-File -FilePath `$logPath -Append -Encoding utf8
}
catch {
    "[$(Get-Date -Format s)] ERROR: $($_.Exception.Message)" | Out-File -FilePath `$logPath -Append -Encoding utf8
    throw
}
"@

Set-Content -Path $startupScriptPath -Value $wrapperContent -Encoding UTF8

Write-Host "[INFO] Published EXE to: $exeTargetPath"
Write-Host "[INFO] Published deploy script to: $deployScriptTargetPath"
Write-Host "[INFO] Generated startup wrapper: $startupScriptPath"
Write-Host ""
Write-Host "[NEXT] In GPMC, add this as Computer Startup PowerShell script:"
Write-Host "       $startupScriptPath"
Write-Host "[NEXT] Link the GPO to the target OU and run gpupdate /force or reboot clients."
