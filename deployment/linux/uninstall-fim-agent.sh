#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Uninstall FIM agent binary and systemd service.

Usage:
  uninstall-fim-agent.sh [--install-dir /opt/fim-agent] [--service-name fim-agent]
EOF
}

INSTALL_DIR="/opt/fim-agent"
SERVICE_NAME="fim-agent"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --service-name)
      SERVICE_NAME="$2"
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

if [[ "$(id -u)" -ne 0 ]]; then
  echo "[ERROR] Run as root." >&2
  exit 1
fi

SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
STATE_DIR="/var/lib/fim-agent/state/${SERVICE_NAME}"
LOG_DIR="/var/log/fim-agent/${SERVICE_NAME}"

if command -v systemctl >/dev/null 2>&1; then
  systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  systemctl disable "$SERVICE_NAME" >/dev/null 2>&1 || true
fi

if [[ -f "$SERVICE_PATH" ]]; then
  rm -f "$SERVICE_PATH"
  echo "[INFO] Removed service file: $SERVICE_PATH"
fi

if [[ -d "$INSTALL_DIR" ]]; then
  rm -rf "$INSTALL_DIR"
  echo "[INFO] Removed install dir: $INSTALL_DIR"
fi

if [[ -d "$STATE_DIR" ]]; then
  rm -rf "$STATE_DIR"
  echo "[INFO] Removed state dir: $STATE_DIR"
fi

if [[ -d "$LOG_DIR" ]]; then
  rm -rf "$LOG_DIR"
  echo "[INFO] Removed log dir: $LOG_DIR"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
fi

echo "[INFO] Uninstall completed."
