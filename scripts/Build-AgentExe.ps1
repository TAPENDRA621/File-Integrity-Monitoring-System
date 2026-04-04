param(
    [string]$PythonExe = ".\\venv\\Scripts\\python.exe",
    [string]$OutputName = "fim-agent",
    [switch]$NoConsole
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -Path $PythonExe)) {
    throw "Python executable not found: $PythonExe"
}

Write-Host "[INFO] Installing/Updating PyInstaller"
& $PythonExe -m pip install --upgrade pip pyinstaller
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install PyInstaller"
}

$buildArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", $OutputName,
    "agent.py"
)

if ($NoConsole) {
    $buildArgs += "--noconsole"
}

Write-Host "[INFO] Building EXE with PyInstaller"
$previousErrorAction = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $PythonExe @buildArgs
$pyInstallerExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorAction

if ($pyInstallerExitCode -ne 0) {
    throw "PyInstaller build failed"
}

$exePath = Join-Path -Path (Join-Path -Path (Get-Location) -ChildPath "dist") -ChildPath ("{0}.exe" -f $OutputName)
if (-not (Test-Path -Path $exePath)) {
    throw "Build succeeded but EXE not found: $exePath"
}

Write-Host "[INFO] Build completed: $exePath"
Write-Host "[INFO] Copy this EXE to your AD/SYSVOL share for GPO deployment."
