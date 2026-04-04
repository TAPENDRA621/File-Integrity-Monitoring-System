#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install FIM agent binary as a systemd service.

Usage:
  install-fim-agent.sh \
    --source-binary /path/to/fim-agent \
    --server-base-url http://10.0.0.10:5000 \
    --monitor-paths /etc,/var/www,/opt/critical/file.txt \
    [--install-dir /opt/fim-agent] \
    [--service-name fim-agent] \
    [--agent-user root] \
    [--agent-group root] \
    [--agent-id HOSTNAME] \
    [--agent-name HOSTNAME] \
    [--heartbeat-seconds 30] \
    [--poll-seconds 15]
EOF
}

require_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "[ERROR] Run as root." >&2
    exit 1
  fi
}

trim() {
  local value="$1"
  value="${value#${value%%[![:space:]]*}}"
  value="${value%${value##*[![:space:]]}}"
  printf '%s' "$value"
}

abspath_existing() {
  local target="$1"
  if [[ -d "$target" ]]; then
    (cd "$target" && pwd -P)
  else
    local base
    base="$(basename "$target")"
    local dir
    dir="$(dirname "$target")"
    (cd "$dir" && printf '%s/%s\n' "$(pwd -P)" "$base")
  fi
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

json_array() {
  local items=("$@")
  local result=""
  local item
  for item in "${items[@]}"; do
    local escaped
    escaped="$(json_escape "$item")"
    if [[ -n "$result" ]]; then
      result+=", "
    fi
    result+="\"$escaped\""
  done
  printf '[%s]' "$result"
}

dedupe_lines() {
  awk '!seen[$0]++'
}

SOURCE_BINARY=""
SERVER_BASE_URL=""
MONITOR_PATHS_RAW=""
INSTALL_DIR="/opt/fim-agent"
SERVICE_NAME="fim-agent"
AGENT_USER="root"
AGENT_GROUP="root"
HEARTBEAT_SECONDS="30"
POLL_SECONDS="15"
AGENT_ID="$(hostname -s)"
AGENT_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-binary)
      SOURCE_BINARY="$2"
      shift 2
      ;;
    --server-base-url)
      SERVER_BASE_URL="$2"
      shift 2
      ;;
    --monitor-paths)
      MONITOR_PATHS_RAW="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --agent-user)
      AGENT_USER="$2"
      shift 2
      ;;
    --agent-group)
      AGENT_GROUP="$2"
      shift 2
      ;;
    --heartbeat-seconds)
      HEARTBEAT_SECONDS="$2"
      shift 2
      ;;
    --poll-seconds)
      POLL_SECONDS="$2"
      shift 2
      ;;
    --agent-id)
      AGENT_ID="$2"
      shift 2
      ;;
    --agent-name)
      AGENT_NAME="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

require_root

if [[ -z "$SOURCE_BINARY" || -z "$SERVER_BASE_URL" || -z "$MONITOR_PATHS_RAW" ]]; then
  echo "[ERROR] --source-binary, --server-base-url, and --monitor-paths are required." >&2
  usage
  exit 1
fi

if [[ "$SERVER_BASE_URL" != http://* && "$SERVER_BASE_URL" != https://* ]]; then
  echo "[ERROR] --server-base-url must start with http:// or https://" >&2
  exit 1
fi

if [[ -z "$AGENT_NAME" ]]; then
  AGENT_NAME="$AGENT_ID"
fi

if ! getent passwd "$AGENT_USER" >/dev/null 2>&1; then
  echo "[ERROR] --agent-user does not exist: $AGENT_USER" >&2
  exit 1
fi

if ! getent group "$AGENT_GROUP" >/dev/null 2>&1; then
  echo "[ERROR] --agent-group does not exist: $AGENT_GROUP" >&2
  exit 1
fi

if ! [[ "$HEARTBEAT_SECONDS" =~ ^[0-9]+$ ]] || [[ "$HEARTBEAT_SECONDS" -lt 1 ]]; then
  echo "[ERROR] --heartbeat-seconds must be a positive integer." >&2
  exit 1
fi

if ! [[ "$POLL_SECONDS" =~ ^[0-9]+$ ]] || [[ "$POLL_SECONDS" -lt 1 ]]; then
  echo "[ERROR] --poll-seconds must be a positive integer." >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "[ERROR] systemctl not found. This installer requires systemd." >&2
  exit 1
fi

if [[ ! -f "$SOURCE_BINARY" ]]; then
  echo "[ERROR] Source binary not found: $SOURCE_BINARY" >&2
  exit 1
fi

SERVER_BASE_URL="${SERVER_BASE_URL%/}"

IFS=',' read -r -a raw_targets <<< "$MONITOR_PATHS_RAW"
if [[ ${#raw_targets[@]} -eq 0 ]]; then
  echo "[ERROR] At least one monitor path is required." >&2
  exit 1
fi

monitor_targets=()
scan_paths=()
monitored_files=()

for item in "${raw_targets[@]}"; do
  target="$(trim "$item")"
  if [[ -z "$target" ]]; then
    continue
  fi

  if [[ ! -e "$target" ]]; then
    echo "[ERROR] Monitor path does not exist: $target" >&2
    exit 1
  fi

  if [[ -d "$target" ]]; then
    if [[ ! -r "$target" || ! -x "$target" ]]; then
      echo "[ERROR] Monitor directory is not readable/traversable: $target" >&2
      exit 1
    fi
  elif [[ -f "$target" ]]; then
    if [[ ! -r "$target" ]]; then
      echo "[ERROR] Monitor file is not readable: $target" >&2
      exit 1
    fi
  fi

  abs_target="$(abspath_existing "$target")"
  monitor_targets+=("$abs_target")

  if [[ -f "$abs_target" ]]; then
    monitored_files+=("$abs_target")
    scan_paths+=("$(dirname "$abs_target")")
  else
    scan_paths+=("$abs_target")
  fi
done

if [[ ${#monitor_targets[@]} -eq 0 ]]; then
  echo "[ERROR] No valid monitor paths were provided." >&2
  exit 1
fi

mapfile -t monitor_targets < <(printf '%s\n' "${monitor_targets[@]}" | dedupe_lines)
mapfile -t scan_paths < <(printf '%s\n' "${scan_paths[@]}" | dedupe_lines)
if [[ ${#monitored_files[@]} -gt 0 ]]; then
  mapfile -t monitored_files < <(printf '%s\n' "${monitored_files[@]}" | dedupe_lines)
fi

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "$SOURCE_BINARY" "$INSTALL_DIR/fim-agent"

STATE_DIR="/var/lib/fim-agent/state/${SERVICE_NAME}"
LOG_DIR="/var/log/fim-agent/${SERVICE_NAME}"
install -d -m 0755 "$STATE_DIR" "$LOG_DIR"

if [[ "$AGENT_USER" != "root" ]]; then
  chown -R "$AGENT_USER:$AGENT_GROUP" "$INSTALL_DIR" "$STATE_DIR" "$LOG_DIR"
fi

CONFIG_PATH="$INSTALL_DIR/config.json"
MONITOR_TARGETS_JSON="$(json_array "${monitor_targets[@]}")"
SCAN_PATHS_JSON="$(json_array "${scan_paths[@]}")"
MONITORED_FILES_JSON="$(json_array "${monitored_files[@]}")"

cat > "$CONFIG_PATH" <<EOF
{
  "server_base_url": "$(json_escape "$SERVER_BASE_URL")",
  "agent_name": "$(json_escape "$AGENT_NAME")",
  "agent_id": "$(json_escape "$AGENT_ID")",
  "agent_port": null,
  "heartbeat_seconds": $HEARTBEAT_SECONDS,
  "poll_seconds": $POLL_SECONDS,
  "monitor_mode": "multiple_paths",
  "monitor_targets": $MONITOR_TARGETS_JSON,
  "scan_paths": $SCAN_PATHS_JSON,
  "monitored_files": $MONITORED_FILES_JSON
}
EOF
chmod 0644 "$CONFIG_PATH"

SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=FIM Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/fim-agent
Restart=always
RestartSec=5
User=$AGENT_USER
Group=$AGENT_GROUP
Environment=FIM_AGENT_STATE_DIR=$STATE_DIR
Environment=FIM_AGENT_LOG_DIR=$LOG_DIR
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "[INFO] Installed binary: $INSTALL_DIR/fim-agent"
echo "[INFO] Wrote config: $CONFIG_PATH"
echo "[INFO] Installed service: $SERVICE_PATH"
echo "[INFO] Service status: systemctl status $SERVICE_NAME"
echo "[INFO] Agent state dir: $STATE_DIR"
echo "[INFO] Agent log dir: $LOG_DIR"
echo "[INFO] Journal logs: journalctl -u $SERVICE_NAME -n 100"
