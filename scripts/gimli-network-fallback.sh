#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/gimli}"
SETTINGS_FILE="$PROJECT_DIR/config/settings.json"
ACTION="${1:-monitor}"
IFACE="${GIMLI_AP_IFACE:-wlan0}"
CON_NAME="${GIMLI_AP_CON_NAME:-gimli-setup-ap}"
CHECK_INTERVAL="${GIMLI_NET_CHECK_INTERVAL:-30}"
BOOT_GRACE="${GIMLI_NET_BOOT_GRACE:-20}"
BOOT_ATTEMPTS="${GIMLI_WIFI_BOOT_ATTEMPTS:-6}"

read_setting() {
  local expr="$1"
  python3 - "$SETTINGS_FILE" "$expr" <<'PY'
import json
import sys
path, expr = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}
value = data
for part in expr.split("."):
    value = value.get(part, {}) if isinstance(value, dict) else {}
print(value if isinstance(value, (str, int, float, bool)) else "")
PY
}

reload_settings() {
  ap_enabled="$(read_setting network.setup_ap.enabled)"
  ap_ssid="$(read_setting network.setup_ap.ssid)"
  ap_password="$(read_setting network.setup_ap.password)"
  wifi_ssid="$(read_setting network.wifi_ssid)"
  wifi_password="$(read_setting network.wifi_password)"
  ap_ssid="${ap_ssid:-Gimli-Rover-Setup}"
  ap_password="${ap_password:-gimli1234}"
}

log() {
  echo "gimli-network: $*"
}

wait_for_networkmanager() {
  for _ in $(seq 1 30); do
    nmcli general status >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

internet_ok() {
  ping -I eth0 -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 && return 0
  ping -I "$IFACE" -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 && return 0
  ping -c 1 -W 2 100.100.100.100 >/dev/null 2>&1 && return 0
  return 1
}

network_ok() {
  wifi_connected && wifi_has_ip && return 0
  eth_has_ip && return 0
  return 1
}

has_ip() {
  ip -4 addr show dev eth0 2>/dev/null | grep -q 'inet ' && return 0
  ip -4 addr show dev "$IFACE" 2>/dev/null | grep -q 'inet ' && return 0
  return 1
}

eth_has_ip() {
  ip -4 addr show dev eth0 2>/dev/null | grep -q 'inet '
}

ap_active() {
  nmcli -t -f NAME,DEVICE con show --active | grep -Fxq "$CON_NAME:$IFACE"
}

wifi_connected() {
  [[ -n "${wifi_ssid:-}" ]] || return 1
  nmcli -t -f NAME,DEVICE con show --active | grep -Fxq "$wifi_ssid:$IFACE"
}

wifi_has_ip() {
  ip -4 addr show dev "$IFACE" 2>/dev/null | grep -q 'inet '
}

ensure_mavlink() {
  if systemctl list-unit-files gimli-mavlink.service >/dev/null 2>&1; then
    systemctl is-active --quiet gimli-mavlink.service || systemctl start gimli-mavlink.service || true
  fi
}

ensure_wifi_profile() {
  [[ -n "${wifi_ssid:-}" ]] || return 1
  if nmcli -t -f NAME con show | grep -Fxq "$wifi_ssid"; then
    return 0
  fi
  [[ -n "${wifi_password:-}" ]] || return 1
  log "creating saved Wi-Fi profile for $wifi_ssid"
  nmcli dev wifi connect "$wifi_ssid" password "$wifi_password" ifname "$IFACE" >/dev/null
}

try_wifi() {
  [[ -n "${wifi_ssid:-}" ]] || return 1
  nmcli radio wifi on || true
  nmcli dev set "$IFACE" managed yes || true
  nmcli con modify "$wifi_ssid" connection.autoconnect yes connection.autoconnect-priority 100 ipv4.route-metric 50 ipv6.method disabled 2>/dev/null || true
  if ap_active; then
    nmcli con down "$CON_NAME" || true
    sleep 3
  fi
  nmcli dev wifi rescan ifname "$IFACE" || true
  sleep 3
  ensure_wifi_profile || true
  if nmcli -t -f NAME con show | grep -Fxq "$wifi_ssid"; then
    log "connecting Wi-Fi profile $wifi_ssid"
    nmcli con up "$wifi_ssid" ifname "$IFACE" >/dev/null
  elif [[ -n "${wifi_password:-}" ]]; then
    log "connecting Wi-Fi SSID $wifi_ssid"
    nmcli dev wifi connect "$wifi_ssid" password "$wifi_password" ifname "$IFACE" >/dev/null
  else
    return 1
  fi
}

start_ap() {
  reload_settings
  if [[ "$ap_enabled" != "True" && "$ap_enabled" != "true" && "$ap_enabled" != "1" ]]; then
    log "setup AP disabled"
    return 0
  fi
  nmcli radio wifi on || true
  nmcli dev set "$IFACE" managed yes || true
  if nmcli -t -f NAME con show | grep -Fxq "$CON_NAME"; then
    nmcli con modify "$CON_NAME" \
      802-11-wireless.mode ap \
      802-11-wireless.ssid "$ap_ssid" \
      wifi-sec.key-mgmt wpa-psk \
      wifi-sec.psk "$ap_password" \
      connection.autoconnect no \
      ipv4.method shared \
      ipv6.method disabled || true
    nmcli con up "$CON_NAME"
  else
    nmcli dev wifi hotspot ifname "$IFACE" con-name "$CON_NAME" ssid "$ap_ssid" password "$ap_password"
    nmcli con modify "$CON_NAME" connection.autoconnect no ipv4.method shared ipv6.method disabled
  fi
  ip -4 addr show dev "$IFACE" | awk '/inet / {print "setup_ap_ip=" $2}'
}

stop_ap() {
  nmcli con down "$CON_NAME" || true
}

auto_once() {
  reload_settings
  wait_for_networkmanager || true
  sleep "$BOOT_GRACE"
  network_ok && return 0
  try_wifi || true
  sleep 10
  network_ok && return 0
  start_ap
}

boot() {
  reload_settings
  wait_for_networkmanager || true
  for attempt in $(seq 1 "$BOOT_ATTEMPTS"); do
    reload_settings
    if wifi_connected && wifi_has_ip; then
      log "Wi-Fi is ready on $IFACE"
      return 0
    fi
    log "boot Wi-Fi attempt $attempt/$BOOT_ATTEMPTS"
    try_wifi || true
    sleep 5
  done
  if wifi_connected && wifi_has_ip; then
    log "Wi-Fi is ready on $IFACE"
    return 0
  fi
  log "Wi-Fi did not get an address, starting setup AP"
  start_ap || true
}

monitor() {
  wait_for_networkmanager || true
  sleep "$BOOT_GRACE"
  while true; do
    reload_settings
    if network_ok; then
      ensure_mavlink
      sleep "$CHECK_INTERVAL"
      continue
    fi
    if ap_active; then
      if [[ -n "${wifi_ssid:-}" ]]; then
        log "setup AP active, retrying configured Wi-Fi in background"
        try_wifi || true
        sleep 10
        if wifi_connected && wifi_has_ip; then
          log "configured Wi-Fi recovered"
          ensure_mavlink
          sleep "$CHECK_INTERVAL"
          continue
        fi
        start_ap || true
      fi
      ensure_mavlink
      sleep "$CHECK_INTERVAL"
      continue
    fi
    log "network unavailable, trying configured Wi-Fi"
    try_wifi || true
    sleep 10
    if network_ok; then
      log "network recovered"
      ensure_mavlink
      sleep "$CHECK_INTERVAL"
      continue
    fi
    log "network still unavailable, starting setup AP"
    start_ap || true
    sleep "$CHECK_INTERVAL"
  done
}

case "$ACTION" in
  start)
    start_ap
    ;;
  stop)
    stop_ap
    ;;
  auto)
    auto_once
    ;;
  boot)
    boot
    ;;
  monitor)
    monitor
    ;;
  *)
    echo "usage: $0 [auto|boot|monitor|start|stop]" >&2
    exit 2
    ;;
esac
