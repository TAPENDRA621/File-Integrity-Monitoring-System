param(
    [switch]$Build = $true,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'

function Test-DockerReady {
    try {
        docker info | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Start-DockerDesktop {
    $desktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $desktopExe)) {
        throw "Docker Desktop is not installed at expected path: $desktopExe"
    }

    $desktopRunning = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue
    if (-not $desktopRunning) {
        Write-Host 'Starting Docker Desktop...'
        Start-Process -FilePath $desktopExe | Out-Null
    }

    try {
        Set-Service -Name 'com.docker.service' -StartupType Automatic -ErrorAction Stop
    }
    catch {
    }

    try {
        Start-Service -Name 'com.docker.service' -ErrorAction Stop
    }
    catch {
    }
}

Write-Host 'Checking Docker engine status...'
if (-not (Test-DockerReady)) {
    Start-DockerDesktop
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while (-not (Test-DockerReady)) {
    if ((Get-Date) -gt $deadline) {
        throw "Docker engine did not become ready within $TimeoutSeconds seconds. Open Docker Desktop once and verify it is set to Linux containers + WSL2 engine."
    }
    Start-Sleep -Seconds 2
}

Write-Host 'Docker engine is ready.'

if ($Build) {
    docker compose up --build
}
else {
    docker compose up
}
