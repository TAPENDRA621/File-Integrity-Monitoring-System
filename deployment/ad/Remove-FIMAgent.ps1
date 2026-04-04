param(
    [string]$InstallDir = "C:\Program Files\FIMAgent",
    [string]$TaskName = "FIM Agent"
)

$ErrorActionPreference = "Continue"

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "[INFO] Removed scheduled task: $TaskName"
}
catch {
    Write-Host "[WARN] Scheduled task not removed or not found: $TaskName"
}

if (Test-Path -Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "[INFO] Removed install directory: $InstallDir"
}
else {
    Write-Host "[INFO] Install directory not found: $InstallDir"
}
