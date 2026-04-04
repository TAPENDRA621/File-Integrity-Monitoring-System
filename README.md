# File Integrity Monitoring System (Agent-Based)

This project implements an agent-based File Integrity Monitoring System using:
- Python
- Flask + Flask-SocketIO
- SQLite
- watchdog
- Bootstrap + Chart.js

## Project Structure

```
app.py
server.py
agent.py
models.py
database/
  db.py
routes/
  api.py
  web.py
utils/
  risk.py
  time_utils.py
templates/
  base.html
  dashboard.html
  agents.html
  agent_logs.html
  events.html
static/
  css/site.css
  js/dashboard.js
```

## Features

- Lightweight agent/sensor architecture
- Agent registration, heartbeat, and file event reporting
- UTC timestamp handling
- Risk classification utility in a dedicated module
- Real-time dashboard updates with Socket.IO
- Agent status tracking (Active/Inactive)
- Event filtering by agent, risk, and event type
- Dashboard pie charts and timeline
- CSV log download (all events and per-agent)
- Nepali time display (NPT) in dashboard tables
- Dashboard-driven agent enrollment and package generation (Windows/Linux)
- Token-based silent first registration for preconfigured agents

## Beginner-Friendly Deployment (Dashboard Controlled)

New deployments are now managed from the server dashboard at:

- `/deploy`

Workflow for non-technical users:

1. Open **Deploy Agent** page.
2. Create agent profile (name, optional ID, monitor paths, heartbeat, polling).
3. Download generated package:
  - Windows ZIP (includes `fim-agent.exe`, `config.json`, `install.ps1`, `install.bat`)
  - Linux ZIP (includes `fim-agent`, `config.json`, `install.sh`, `uninstall.sh`)
4. Run installer on endpoint.
5. Agent registers using enrollment token and appears in dashboard as Active/Offline.

No endpoint setup wizard is required for this flow.

## Deployment APIs (Dashboard Onboarding)

- `GET /api/deploy/agents`
- `POST /api/deploy/agents`
- `GET /api/deploy/agents/<agent_id>`
- `PUT /api/deploy/agents/<agent_id>`
- `PATCH /api/deploy/agents/<agent_id>/enabled`
- `POST /api/deploy/agents/<agent_id>/token`
- `DELETE /api/deploy/agents/<agent_id>`
- `GET /api/deploy/agents/<agent_id>/config`
- `GET /api/deploy/agents/<agent_id>/install-command?platform=windows|linux`
- `GET /api/deploy/agents/<agent_id>/package?platform=windows|linux`

## Testing the New Deployment Flow

1. Start server:

```bash
python server.py
```

2. Open dashboard deployment page:

- `http://127.0.0.1:5000/deploy`

3. Create a profile with at least one monitor path.

4. Download Windows package and extract:

- Run `install.bat` as Administrator on test endpoint.

5. Download Linux package and extract:

- Run `sudo bash install.sh` on test endpoint.

6. Validate enrollment and heartbeat:

- Check `/agents` page status
- Check `/events` page for new file events

7. Negative test (silent behavior):

- Run agent executable without `config.json`
- Confirm it exits with clear configuration message and no interactive prompt

## Setup

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Start server

```bash
python server.py
```

3. Start agent on the same machine

```bash
python agent.py
```

4. Open dashboard

- http://localhost:5000

## Agent Configuration (Environment Variables)

Sample file:
- `agent_config.example.env`

- `FIM_SERVER_BASE_URL` (default: `http://localhost:5000`)
- `FIM_AGENT_NAME` (default: hostname)
- `FIM_AGENT_ID` (default: hostname)
- `FIM_AGENT_PORT` (optional)
- `FIM_HEARTBEAT_SECONDS` (default: 30)
- `FIM_MONITOR_PATHS` (required in non-interactive mode; comma-separated file/directory paths)

### Agent Setup Wizard (local config)

`agent.py` supports an interactive setup wizard that saves local settings in `config.json`.

- First run with no `config.json`: setup wizard starts automatically (TTY mode).
- Reconfigure manually: `python agent.py --reconfigure`
- Show active config: `python agent.py --show-config`
- Reset saved config: `python agent.py --reset-config`

The wizard asks for:

- Server base URL
- Agent name
- Agent ID
- Agent port (optional)
- Monitoring mode: single file, single directory, or multiple paths
- Existing file/folder path(s) to monitor (validated before saving)

In interactive runs, the agent also asks for confirmation before connecting to the configured server.

Examples:

```bash
export FIM_SERVER_BASE_URL=http://192.168.1.50:5000
export FIM_AGENT_ID=lab-agent-01
export FIM_MONITOR_PATHS=./critical,./configs
python agent.py
```

```cmd
set FIM_SERVER_BASE_URL=http://192.168.1.50:5000
set FIM_AGENT_ID=lab-agent-01
set FIM_MONITOR_PATHS=.\critical,.\configs
python agent.py
```

## AD Deployment Using EXE (No Python Command on Endpoints)

You can deploy the agent from one central AD server/share without running `python agent.py` manually on each client.

### 1) Build agent EXE once

From project root:

```powershell
.\scripts\Build-AgentExe.ps1 -NoConsole
```

Output:

- `dist\fim-agent.exe`

### 2) Copy EXE to SYSVOL or central share

Example path:

- `\\<domain>\SYSVOL\<domain>\scripts\fim-agent.exe`

### 3) Use GPO Computer Startup Script

Assign this script as a **Computer Configuration -> Windows Settings -> Scripts (Startup)** script:

- `deployment\ad\Deploy-FIMAgent-GPOStartup.ps1`

Recommended parameters:

```powershell
-SourceExePath "\\<domain>\SYSVOL\<domain>\scripts\fim-agent.exe" \
-ServerBaseUrl "http://<central-server-ip>:5000" \
-MonitorPaths "C:\critical-data","C:\configs"
```

What startup script does on each endpoint:

- Copies EXE to `C:\Program Files\FIMAgent\fim-agent.exe`
- Writes `C:\Program Files\FIMAgent\config.json` with machine-specific `agent_id` (`%COMPUTERNAME%`)
- Registers and starts scheduled task `FIM Agent` as `SYSTEM` at boot

### 4) Remove agent from endpoints (optional)

Use:

- `deployment\ad\Remove-FIMAgent.ps1`

This removes both the scheduled task and local install directory.

### Notes

- Agent process still runs locally on each endpoint (required for local file monitoring), but deployment/management is centralized through AD.
- `agent.py` is EXE-safe: when packaged, it stores `config.json` next to the EXE, not in a temporary runtime folder.

### AD Automation (Publish Once, Deploy via GPO)

Use this script on a domain admin machine to publish the EXE and generate a ready startup wrapper script in SYSVOL:

- `deployment\ad\Publish-FIMAgentToSysvol.ps1`

Example:

```powershell
.\deployment\ad\Publish-FIMAgentToSysvol.ps1 `
  -DomainFqdn "corp.example.local" `
  -SourceExePath ".\dist\fim-agent.exe" `
  -ServerBaseUrl "http://192.168.1.50:5000" `
  -MonitorPaths "C:\critical-data","C:\configs"
```

This automation script will:

- Copy `fim-agent.exe` into SYSVOL
- Copy `Deploy-FIMAgent-GPOStartup.ps1` into SYSVOL
- Generate a wrapper startup script (for GPO startup assignment)
- Print the exact SYSVOL script path to assign in GPMC

After running it, add the generated wrapper path as a **Computer Startup PowerShell script** in GPO and link the GPO to your target OU.

## Linux Deployment Using Binary + systemd (No Python Command on Endpoints)

For Linux endpoints, deploy a prebuilt agent binary and run it as a systemd service.

### 1) Build Linux agent binary once (on a Linux build machine)

From project root:

```bash
./scripts/Build-AgentBinary-Linux.sh
```

Output:

- `dist/fim-agent`

### 2) Copy binary to Linux endpoint(s)

Example:

```bash
scp dist/fim-agent admin@linux-host:/tmp/fim-agent
```

### 3) Install as systemd service

On each Linux endpoint:

```bash
sudo bash deployment/linux/install-fim-agent.sh \
  --source-binary /tmp/fim-agent \
  --server-base-url http://<central-server-ip>:5000 \
  --monitor-paths /etc,/var/www,/opt/critical
```

What the installer does:

- Copies binary to `/opt/fim-agent/fim-agent`
- Writes `/opt/fim-agent/config.json`
- Creates and enables `fim-agent` systemd service
- Starts service immediately

Optional install flags:

- `--service-name fim-agent`
- `--install-dir /opt/fim-agent`
- `--agent-user root`
- `--agent-group root`
- `--agent-id <custom-id>`
- `--agent-name <custom-name>`
- `--heartbeat-seconds 30`
- `--poll-seconds 15`

### 4) Verify and troubleshoot on Linux endpoint

```bash
sudo systemctl status fim-agent
sudo journalctl -u fim-agent -f
```

### 5) Remove from Linux endpoint (optional)

```bash
sudo bash deployment/linux/uninstall-fim-agent.sh
```

## Mixed Fleet Rollout (Windows + Linux)

- Windows domain-joined endpoints: use AD/GPO scripts in `deployment/ad/`.
- Linux endpoints: use `deployment/linux/install-fim-agent.sh` with the Linux binary.
- Both platforms report to the same central server/dashboard.
- No Python command is required on endpoints after packaging.

## Sensor Relay (for multi-network agents)

Use `sensor.py` as an intermediate collector when agents are behind different NAT/firewall boundaries.

Flow:

`Agent(s) -> Sensor Relay -> Central FIM Server -> Dashboard`

### 1) Start central server

```bash
python server.py
```

### 2) Start sensor relay

```bash
set FIM_UPSTREAM_BASE_URL=http://<central-server-ip>:5000
set FIM_SENSOR_PORT=5100
python sensor.py
```

Linux/macOS shell:

```bash
export FIM_UPSTREAM_BASE_URL=http://<central-server-ip>:5000
export FIM_SENSOR_PORT=5100
python sensor.py
```

### 3) Point agents to sensor instead of central server

```bash
set FIM_SERVER_BASE_URL=http://<sensor-ip>:5100
python agent.py
```

### 4) Verify relay health

```bash
curl http://<sensor-ip>:5100/sensor/health
```

The central dashboard remains unchanged and will show forwarded events normally.

### 5) Collect syslog from network devices (new)

`sensor.py` now includes a built-in UDP syslog listener that converts incoming device logs into FIM events and forwards them to the central server.

Environment variables:

- `FIM_SENSOR_SYSLOG_ENABLED` (default: `true`)
- `FIM_SENSOR_SYSLOG_HOST` (default: `0.0.0.0`)
- `FIM_SENSOR_SYSLOG_PORT` (default: `5514`)

Direct run example:

```bash
set FIM_SENSOR_SYSLOG_ENABLED=true
set FIM_SENSOR_SYSLOG_HOST=0.0.0.0
set FIM_SENSOR_SYSLOG_PORT=5514
python sensor.py
```

Configure your router/switch/firewall to send syslog to:

- `<sensor-ip>:5514/udp`

Then check:

- `http://<sensor-ip>:5100/sensor/health` for `syslog.received_count` and `syslog.enqueued_count`
- Central dashboard/events page for new entries under agent IDs like `net-<device-host>`

## API Endpoints

- `POST /api/agents/register`
- `POST /api/agents/heartbeat`
- `POST /api/events`
- `GET /api/agents`
- `GET /api/agents/<agent_id>`
- `GET /api/agents/<agent_id>/events`
- `GET /api/events`
- `GET /api/events/download`
- `GET /api/agents/<agent_id>/events/download`
- `GET /api/dashboard/summary`

## Database

The SQLite database is initialized automatically in:

- `database/fims.db`

Tables:
- `agents`
- `events`

## Notes for Extension

- Risk logic is centralized in `utils/risk.py`.
- You can add authentication (API key/JWT) in `routes/api.py`.
- You can add pagination for large event tables in `models.py` query methods.
- You can send alert notifications for HIGH risk events from `create_event` route.
