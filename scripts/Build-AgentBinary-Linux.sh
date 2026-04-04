#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
OUTPUT_NAME="${OUTPUT_NAME:-fim-agent}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[ERROR] Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

echo "[INFO] Installing/updating PyInstaller"
"$PYTHON_BIN" -m pip install --upgrade pip pyinstaller

echo "[INFO] Building Linux binary"
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean --onefile --name "$OUTPUT_NAME" agent.py

if [[ ! -f "dist/$OUTPUT_NAME" ]]; then
  echo "[ERROR] Build succeeded but binary not found: dist/$OUTPUT_NAME" >&2
  exit 1
fi

echo "[INFO] Build completed: dist/$OUTPUT_NAME"
