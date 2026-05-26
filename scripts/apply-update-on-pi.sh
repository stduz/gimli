#!/usr/bin/env bash
# Apply an uploaded Gimli Rover tree from /home/gimli1mb/gimli to /opt/gimli
# without losing runtime settings, camera passwords, Wi-Fi, or calibration files.

set -euo pipefail

SRC_DIR="${SRC_DIR:-/home/gimli1mb/gimli}"
PROJECT_DIR="${PROJECT_DIR:-/opt/gimli}"
BACKUP_DIR="$(mktemp -d)"

restore_runtime() {
  for file in settings.json go2rtc.yaml compass_calibration.json sensor_state.json rc_state.json control_state.json; do
    if [[ -f "$BACKUP_DIR/$file" ]]; then
      cp "$BACKUP_DIR/$file" "$PROJECT_DIR/config/$file"
    fi
  done
  rm -rf "$BACKUP_DIR"
}

trap restore_runtime EXIT

mkdir -p "$BACKUP_DIR"
if [[ -d "$PROJECT_DIR/config" ]]; then
  for file in settings.json go2rtc.yaml compass_calibration.json sensor_state.json rc_state.json control_state.json; do
    if [[ -f "$PROJECT_DIR/config/$file" ]]; then
      cp "$PROJECT_DIR/config/$file" "$BACKUP_DIR/$file"
    fi
  done
fi

sudo rsync -a --delete \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  "$SRC_DIR/" "$PROJECT_DIR/"

sudo cp "$PROJECT_DIR/config/"*.service /etc/systemd/system/
sudo install -m 0755 "$PROJECT_DIR/scripts/gimli-network-fallback.sh" /usr/local/sbin/gimli-network-fallback
sudo chmod 0755 "$PROJECT_DIR/scripts/preflight-check.sh" "$PROJECT_DIR/scripts/gimli-network-fallback.sh"
sudo systemctl daemon-reload
sudo systemctl enable gimli-rover.service gimli-sensors.service gimli-mavlink.service gimli-rc-input.service go2rtc.service gimli-network-fallback.service gimli-wifi-bootstrap.service
sudo systemctl restart gimli-rover.service gimli-sensors.service gimli-mavlink.service gimli-rc-input.service go2rtc.service
