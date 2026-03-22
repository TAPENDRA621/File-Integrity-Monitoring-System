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

## Docker (Cross-Platform)

From the project root, start both server and agent:

```bash
docker compose up --build
```

Run in detached mode:

```bash
docker compose up -d --build
```

Stop containers:

```bash
docker compose down
```

## Agent Configuration (Environment Variables)

Sample file:
- `agent_config.example.env`

- `FIM_SERVER_BASE_URL` (default: `http://localhost:5000`)
- `FIM_AGENT_ID` (default: hostname)
- `FIM_HEARTBEAT_SECONDS` (default: 30)
- `FIM_MONITOR_PATHS` (default: `./test_monitor,./important_files`)

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

### Docker mediator port mapping

In Docker compose, sensor is exposed for agent ingestion on `:5000`, UDP syslog on `:5514/udp`, and central dashboard/server is exposed on `:5001`.

- Remote/other-network agents -> `http://<host-ip>:5000`
- Network devices syslog target -> `<host-ip>:5514/udp`
- Dashboard -> `http://<host-ip>:5001`

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
