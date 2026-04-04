import io
import json
import os
import zipfile
from typing import Dict, List, Optional, Tuple

from utils.windows_context import path_requires_user_context


def _slug(value: str) -> str:
    cleaned = [ch.lower() if ch.isalnum() or ch in ("-", "_") else "-" for ch in (value or "")]
    compact = "".join(cleaned).strip("-")
    return compact or "agent"


def _artifact_path(filename: str) -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "dist", filename)


def _load_binary(platform_name: str) -> Tuple[str, bytes]:
    if platform_name == "windows":
        binary_name = "fim-agent.exe"
    elif platform_name == "linux":
        binary_name = "fim-agent"
    else:
        raise ValueError("Unsupported platform")

    path = _artifact_path(binary_name)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Build artifact is missing: {path}")

    with open(path, "rb") as handle:
        return binary_name, handle.read()


def build_agent_config(profile: Dict, server_base_url: str, server_base_urls: Optional[List[str]] = None) -> Dict:
    monitor_paths: List[str] = list(profile.get("monitor_paths") or [])
    recommend_user_mode = any(path_requires_user_context(path) for path in monitor_paths)

    normalized_urls: List[str] = []
    for value in [server_base_url] + list(server_base_urls or []):
        cleaned = str(value or "").strip().rstrip("/")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            continue
        if any(existing.lower() == lowered for existing in normalized_urls):
            continue
        normalized_urls.append(cleaned)

    primary_server_url = normalized_urls[0] if normalized_urls else str(server_base_url or "").strip().rstrip("/")

    return {
        "server_base_url": primary_server_url,
        "server_base_urls": normalized_urls,
        "agent_name": profile.get("agent_name"),
        "agent_id": profile.get("agent_id"),
        "agent_port": None,
        "heartbeat_seconds": int(profile.get("heartbeat_seconds") or 30),
        "poll_seconds": int(profile.get("poll_seconds") or 15),
        "monitor_mode": "multiple_paths",
        "monitor_targets": monitor_paths,
        "scan_paths": monitor_paths,
        "monitored_files": [],
        "risk_label": profile.get("risk_label") or "",
        "enrollment_token": profile.get("enrollment_token"),
        "installer_recommend_user_mode": recommend_user_mode,
    }


def _windows_install_ps1(agent_id: str) -> str:
    safe_id = _slug(agent_id)
    return f"""$ErrorActionPreference = 'Stop'

try {{
    $packageExe = Join-Path $PSScriptRoot 'fim-agent.exe'
    $packageConfig = Join-Path $PSScriptRoot 'config.json'

    if (-not (Test-Path -LiteralPath $packageExe)) {{
        throw 'Missing fim-agent.exe in extracted package.'
    }}
    if (-not (Test-Path -LiteralPath $packageConfig)) {{
        throw 'Missing config.json in extracted package.'
    }}

    foreach ($packageFile in @($packageExe, $packageConfig)) {{
        try {{
            Unblock-File -Path $packageFile -ErrorAction SilentlyContinue
        }} catch {{
            # Continue even if ADS operations are restricted.
        }}
    }}

    function Test-FimPathRiskForSystem([string]$PathValue) {{
        if ([string]::IsNullOrWhiteSpace($PathValue)) {{ return $false }}
        if ($PathValue -match '^[A-Za-z]:\\\\Users\\\\') {{ return $true }}
        if ($PathValue -match '(?i)\\\\OneDrive\\\\') {{ return $true }}
        if ($PathValue -match '(?i)\\\\Desktop\\\\|\\\\Documents\\\\') {{ return $true }}
        if ($PathValue -match '^\\\\\\\\') {{ return $true }}
        if ($PathValue -match '^[A-Za-z]:\\\\') {{
            try {{
                $root = $PathValue.Substring(0,2) + '\\'
                $drive = New-Object System.IO.DriveInfo($root)
                if ($drive.DriveType -eq [System.IO.DriveType]::Network) {{ return $true }}
            }} catch {{
                # Ignore drive inspection errors.
            }}
        }}
        return $false
    }}

    $configObj = Get-Content -LiteralPath $packageConfig -Raw | ConvertFrom-Json
    $scanPaths = @($configObj.scan_paths)
    $recommendedUserMode = $false
    if ($configObj.PSObject.Properties.Name -contains 'installer_recommend_user_mode') {{
        $recommendedUserMode = [bool]$configObj.installer_recommend_user_mode
    }}

    $validPathCount = 0
    foreach ($scanPath in $scanPaths) {{
        $pathText = [string]$scanPath
        if ([string]::IsNullOrWhiteSpace($pathText)) {{
            Write-Host "[WARN] Empty monitor path configured; it will be skipped at runtime."
            continue
        }}
        if (Test-Path -LiteralPath $pathText) {{
            $validPathCount += 1
        }} else {{
            Write-Host "[WARN] Monitor path does not exist on this endpoint: $pathText"
        }}
        if (Test-FimPathRiskForSystem -PathValue $pathText) {{
            $recommendedUserMode = $true
            Write-Host "[WARN] Configured path may be invalid under SYSTEM context: $pathText"
        }}
    }}

    if ($recommendedUserMode) {{
        Write-Host "[WARN] Installer detected user-profile or network monitor paths."
        Write-Host "[HINT] Recommended safe local fallback path for SYSTEM mode: C:\\FIMTest"
    }}

    if ($validPathCount -eq 0) {{
        Write-Host "[WARN] No monitor paths currently exist/access on this endpoint. Agent will run in degraded mode until valid paths are provided."
    }}

    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {{
        throw 'Administrator rights are required. Run install.bat as Administrator.'
    }}

    $agentDir = $null
    foreach ($root in @('C:\\Program Files\\FIMAgent', "$env:ProgramData\\FIMAgent")) {{
        try {{
            New-Item -ItemType Directory -Path $root -Force | Out-Null
            $candidate = Join-Path $root '{safe_id}'
            New-Item -ItemType Directory -Path $candidate -Force | Out-Null
            $agentDir = $candidate
            break
        }} catch {{
            # Try next root
        }}
    }}

    if (-not $agentDir) {{
        throw 'Unable to create install directory in Program Files or ProgramData.'
    }}

    $exePath = Join-Path $agentDir 'fim-agent.exe'
    $configPath = Join-Path $agentDir 'config.json'
    $taskName = 'FIM Agent - {safe_id}'

    # Upgrade-safe install: stop previous startup task and any running process before replacing files.
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {{
        Write-Host "[INFO] Found existing task: $taskName. Stopping it for upgrade..."
        try {{
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        }} catch {{
            # Continue; process kill below is the hard stop.
        }}
        Start-Sleep -Seconds 2
    }}

    try {{
        $runningAgentProcesses = Get-CimInstance Win32_Process -Filter "Name='fim-agent.exe'" -ErrorAction SilentlyContinue | Where-Object {{
            $_.ExecutablePath -and ($_.ExecutablePath -ieq $exePath)
        }}

        foreach ($proc in $runningAgentProcesses) {{
            Write-Host "[INFO] Stopping running fim-agent.exe process PID $($proc.ProcessId)..."
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }}
    }} catch {{
        # If process enumeration is blocked, copy retry loop below still provides resilience.
    }}

    $copied = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {{
        try {{
            Copy-Item -Path $packageExe -Destination $exePath -Force
            Copy-Item -Path $packageConfig -Destination $configPath -Force
            $copied = $true
            break
        }} catch {{
            if ($attempt -eq 5) {{
                throw
            }}
            Write-Host "[WARN] Copy attempt $attempt failed because files are busy. Retrying..."
            Start-Sleep -Seconds 2
        }}
    }}

    if (-not $copied) {{
        throw 'Unable to copy agent files after multiple attempts.'
    }}

    foreach ($installedFile in @($exePath, $configPath)) {{
        try {{
            Unblock-File -Path $installedFile -ErrorAction SilentlyContinue
        }} catch {{
            # Continue even if ADS operations are restricted.
        }}
    }}

    $runnerPath = Join-Path $agentDir 'run-agent.ps1'
    $runnerScript = @'
$ErrorActionPreference = 'Continue'

$agentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exePath = Join-Path $agentDir 'fim-agent.exe'
$logRoot = Join-Path $env:ProgramData 'FIMAgent\\logs\\{safe_id}'
if (-not (Test-Path -LiteralPath $logRoot)) {{
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
}}
$logPath = Join-Path $logRoot 'agent-runtime.log'
$stdoutPath = Join-Path $logRoot 'agent-stdout.log'
$stderrPath = Join-Path $logRoot 'agent-stderr.log'
$env:FIM_AGENT_LOG_DIR = $logRoot
$env:FIM_AGENT_STATE_DIR = Join-Path $env:ProgramData 'FIMAgent\\state\\{safe_id}'

while ($true) {{
    Add-Content -Path $logPath -Value ("[" + (Get-Date -Format s) + "] Starting fim-agent.exe")
    try {{
        if (Test-Path -LiteralPath $stdoutPath) {{ Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue }}
        if (Test-Path -LiteralPath $stderrPath) {{ Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue }}
        $proc = Start-Process -FilePath $exePath -WorkingDirectory $agentDir -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -Wait
        $exitCode = $proc.ExitCode
    }} catch {{
        Add-Content -Path $logPath -Value ("[" + (Get-Date -Format s) + "] Wrapper error: " + $_.Exception.Message)
        $exitCode = 1
    }}

    if (Test-Path -LiteralPath $stdoutPath) {{
        $stdoutTail = Get-Content -LiteralPath $stdoutPath -Tail 20 -ErrorAction SilentlyContinue
        foreach ($line in $stdoutTail) {{ Add-Content -Path $logPath -Value ("[STDOUT] " + $line) }}
    }}
    if (Test-Path -LiteralPath $stderrPath) {{
        $stderrTail = Get-Content -LiteralPath $stderrPath -Tail 20 -ErrorAction SilentlyContinue
        foreach ($line in $stderrTail) {{ Add-Content -Path $logPath -Value ("[STDERR] " + $line) }}
    }}

    Add-Content -Path $logPath -Value ("[" + (Get-Date -Format s) + "] fim-agent.exe exited with code " + $exitCode + ". Restarting in 5 seconds.")
    Start-Sleep -Seconds 5
}}
'@

    Set-Content -Path $runnerPath -Value $runnerScript -Encoding UTF8

    try {{
        Unblock-File -Path $runnerPath -ErrorAction SilentlyContinue
    }} catch {{
        # Continue even if ADS operations are restricted.
    }}

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runnerPath`"" -WorkingDirectory $agentDir
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

    $trigger = $null
    $principal = $null
    $preferInteractiveUser = $recommendedUserMode

    if ($preferInteractiveUser) {{
        $runAsUser = "$env:USERDOMAIN\\$env:USERNAME"
        if ([string]::IsNullOrWhiteSpace($runAsUser)) {{
            $runAsUser = $env:USERNAME
        }}

        try {{
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $runAsUser
            $principal = New-ScheduledTaskPrincipal -UserId $runAsUser -LogonType InteractiveToken -RunLevel Highest
            Write-Host "[INFO] Using interactive user task mode for user-scoped monitor paths: $runAsUser"
        }} catch {{
            Write-Host "[WARN] Failed to configure interactive-user task mode. Falling back to SYSTEM startup mode."
            $trigger = $null
            $principal = $null
        }}
    }} else {{
        Write-Host '[INFO] Monitor paths look safe for SYSTEM mode. Using startup service context unless task registration fails.'
    }}

    if (-not $trigger -or -not $principal) {{
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Write-Host '[INFO] Using SYSTEM startup task mode.'
    }}

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName

    Write-Host '[SUCCESS] FIM Agent installed and started.' -ForegroundColor Green
    Write-Host "[INFO] Installed at: $agentDir"
    Write-Host "[INFO] Scheduled Task: $taskName"
    Write-Host "[INFO] Runtime log: $(Join-Path (Join-Path $env:ProgramData 'FIMAgent\\logs\\{safe_id}') 'agent-runtime.log')"
    Write-Host '[INFO] No Python installation is required on this endpoint.'
    exit 0
}} catch {{
    Write-Host "[ERROR] Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}}
"""


def _windows_install_bat() -> str:
    return (
        "@echo off\r\n"
        "setlocal\r\n"
        "set \"NO_WAIT=\"\r\n"
        "if /I \"%~1\"==\"--quiet\" set \"NO_WAIT=1\"\r\n"
        "set \"LOG_FILE=%~dp0install.log\"\r\n"
        "echo [INFO] Installing FIM Agent...\r\n"
        "powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"%~dp0install.ps1\" > \"%LOG_FILE%\" 2>&1\r\n"
        "set \"EXITCODE=%ERRORLEVEL%\"\r\n"
        "echo [INFO] Installer log: %LOG_FILE%\r\n"
        "type \"%LOG_FILE%\"\r\n"
        "if \"%EXITCODE%\"==\"0\" (\r\n"
        "  echo [SUCCESS] Installation finished.\r\n"
        ") else (\r\n"
        "  echo [ERROR] Installation failed. See message output above.\r\n"
        "  echo [HINT] Right-click install.bat and choose ^\"Run as administrator^\".\r\n"
        ")\r\n"
        "if not defined NO_WAIT (\r\n"
        "  echo [INFO] Press any key to close this window...\r\n"
        "  pause >nul\r\n"
        ")\r\n"
        "exit /b %EXITCODE%\r\n"
    )


def _linux_install_sh(agent_id: str) -> str:
    safe_id = _slug(agent_id)
    return f"""#!/usr/bin/env bash
set -Eeuo pipefail

on_error() {{
    local line="$1"
    echo "[ERROR] Installation failed near line $line." >&2
    exit 1
}}
trap 'on_error $LINENO' ERR

echo "[INFO] Installing FIM Agent..."

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[ERROR] Run this installer as root (sudo)." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "[ERROR] systemctl is not available. This installer requires a systemd-based Linux host." >&2
    exit 1
fi

package_dir="$(cd "$(dirname "$0")" && pwd)"
package_bin="$package_dir/fim-agent"
package_config="$package_dir/config.json"

if [[ ! -f "$package_bin" ]]; then
    echo "[ERROR] Missing fim-agent binary in extracted package." >&2
    exit 1
fi
if [[ ! -f "$package_config" ]]; then
    echo "[ERROR] Missing config.json in extracted package." >&2
    exit 1
fi

mapfile -t scan_paths < <(awk '
    /"scan_paths"[[:space:]]*:[[:space:]]*\[/ {{ in_array=1; next }}
    in_array && /\]/ {{ in_array=0; exit }}
    in_array {{
        line=$0
        sub(/^[[:space:]]+/, "", line)
        sub(/[[:space:]]+$/, "", line)
        sub(/,$/, "", line)
        if (line ~ /^".*"$/) {{
            sub(/^"/, "", line)
            sub(/"$/, "", line)
            gsub(/\\\\"/, "\"", line)
            gsub(/\\\\\\\\/, "\\", line)
            print line
        }}
    }}
' "$package_config")

if [[ ${{#scan_paths[@]}} -eq 0 ]]; then
    echo "[WARN] No scan_paths found in config.json. Agent will run in degraded mode until valid paths are configured."
fi

valid_scan_path_count=0
for scan_path in "${{scan_paths[@]}}"; do
    if [[ -z "$scan_path" ]]; then
        continue
    fi
    if [[ ! -e "$scan_path" ]]; then
        echo "[WARN] Configured monitor path does not exist on endpoint: $scan_path"
        continue
    fi
    if [[ -d "$scan_path" ]]; then
        if [[ -r "$scan_path" && -x "$scan_path" ]]; then
            valid_scan_path_count=$((valid_scan_path_count + 1))
        else
            echo "[WARN] Monitor directory is not readable/traversable: $scan_path"
        fi
    elif [[ -f "$scan_path" ]]; then
        if [[ -r "$scan_path" ]]; then
            valid_scan_path_count=$((valid_scan_path_count + 1))
        else
            echo "[WARN] Monitor file is not readable: $scan_path"
        fi
    else
        echo "[WARN] Monitor path is not a regular file/directory and may be unsupported: $scan_path"
    fi
done

if [[ "$valid_scan_path_count" -eq 0 ]]; then
    echo "[WARN] No validated monitor paths were found. Agent will still start and continue heartbeat in degraded mode."
fi

install_root="/opt/fim-agent/{safe_id}"
if ! mkdir -p "$install_root" 2>/dev/null; then
    install_root="/var/lib/fim-agent/{safe_id}"
    mkdir -p "$install_root"
fi

state_root="/var/lib/fim-agent/state/{safe_id}"
log_root="/var/log/fim-agent/{safe_id}"
mkdir -p "$state_root" "$log_root"

service_name="fim-agent-{safe_id}"
service_path="/etc/systemd/system/${{service_name}}.service"

install -m 0755 "$package_bin" "$install_root/fim-agent"
install -m 0644 "$package_config" "$install_root/config.json"

cat > "$service_path" <<EOF
[Unit]
Description=FIM Agent ({safe_id})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$install_root
ExecStart=$install_root/fim-agent
Restart=always
RestartSec=5
User=root
Group=root
Environment=FIM_AGENT_STATE_DIR=$state_root
Environment=FIM_AGENT_LOG_DIR=$log_root
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$service_name"

if ! systemctl is-active --quiet "$service_name"; then
    echo "[ERROR] Service did not start successfully. Check: journalctl -u $service_name -n 50" >&2
    exit 1
fi

echo "[SUCCESS] FIM Agent installed and started."
echo "[INFO] Installed at: $install_root"
echo "[INFO] Service: $service_name"
echo "[INFO] Agent state dir: $state_root"
echo "[INFO] Agent log dir: $log_root"
echo "[INFO] Journal logs: journalctl -u $service_name -n 100"
echo "[INFO] No Python installation is required on this endpoint."
"""


def _linux_uninstall_sh(agent_id: str) -> str:
    safe_id = _slug(agent_id)
    return f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[ERROR] Run this script as root (sudo)." >&2
  exit 1
fi

install_root="/opt/fim-agent/{safe_id}"
fallback_install_root="/var/lib/fim-agent/{safe_id}"
state_root="/var/lib/fim-agent/state/{safe_id}"
log_root="/var/log/fim-agent/{safe_id}"
service_name="fim-agent-{safe_id}"
service_path="/etc/systemd/system/${{service_name}}.service"

systemctl stop "$service_name" >/dev/null 2>&1 || true
systemctl disable "$service_name" >/dev/null 2>&1 || true
rm -f "$service_path"
rm -rf "$install_root"
rm -rf "$fallback_install_root"
rm -rf "$state_root"
rm -rf "$log_root"
systemctl daemon-reload

echo "[SUCCESS] FIM Agent removed."
"""


def build_install_command(profile: Dict, platform_name: str) -> str:
    safe_id = _slug(profile.get("agent_id") or "agent")
    if platform_name == "windows":
        return f"Extract ZIP and run install.bat as Administrator (agent: {safe_id})"
    if platform_name == "linux":
        return f"Extract ZIP and run: sudo bash install.sh (agent: {safe_id})"
    raise ValueError("platform must be 'windows' or 'linux'")


def build_agent_package(
    profile: Dict,
    platform_name: str,
    server_base_url: str,
    server_base_urls: Optional[List[str]] = None,
) -> Tuple[str, bytes]:
    if platform_name not in {"windows", "linux"}:
        raise ValueError("platform must be 'windows' or 'linux'")

    binary_name, binary_bytes = _load_binary(platform_name)
    config_payload = build_agent_config(profile, server_base_url, server_base_urls=server_base_urls)
    config_bytes = json.dumps(config_payload, indent=2).encode("utf-8")

    package_name = f"fim-agent-{_slug(profile.get('agent_id') or 'agent')}-{platform_name}.zip"

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(binary_name, binary_bytes)
        archive.writestr("config.json", config_bytes)

        if platform_name == "windows":
            archive.writestr("install.ps1", _windows_install_ps1(profile.get("agent_id") or "agent"))
            archive.writestr("install.bat", _windows_install_bat())
        else:
            archive.writestr("install.sh", _linux_install_sh(profile.get("agent_id") or "agent"))
            archive.writestr("uninstall.sh", _linux_uninstall_sh(profile.get("agent_id") or "agent"))

        archive.writestr(
            "README.txt",
            "This package is auto-generated by the FIM dashboard.\n"
            "Install steps:\n"
            f"- {build_install_command(profile, platform_name)}\n",
        )

    output.seek(0)
    return package_name, output.read()
