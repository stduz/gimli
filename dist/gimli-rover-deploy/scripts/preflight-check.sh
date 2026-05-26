#!/usr/bin/env bash
# Quick rover readiness check before field tests.

set -euo pipefail

WEB="${WEB:-http://127.0.0.1:8080}"

echo "== Services =="
systemctl --no-pager --plain --type=service --state=running \
  status gimli-rover.service gimli-sensors.service gimli-mavlink.service gimli-rc-input.service go2rtc.service \
  | sed -n '1,80p' || true

echo
echo "== USB devices =="
ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true
ls -l /dev/serial/by-id/ 2>/dev/null || true

echo
echo "== Telemetry =="
curl -fsS "$WEB/api/telemetry" >/tmp/gimli-telemetry.json
python3 - <<'PY'
import json
from pathlib import Path

d = json.loads(Path("/tmp/gimli-telemetry.json").read_text())
p = d.get("power", {})
n = d.get("navigation", {})
r = d.get("rc_input", {})
c = d.get("control", {})
m = d.get("motors", {})
v = m.get("vesc", {})
cams = d.get("cameras", {})

print(f"voltage={p.get('battery_voltage')}V current={p.get('current_a')}A power={p.get('power_w')}W")
print(f"heading={n.get('heading_deg')} gps_fix={n.get('fix_type')} sats={n.get('satellites')}")
print(f"rc_ok={r.get('ok')} source={r.get('source')} thr={r.get('throttle')} steer={r.get('steering')} port={r.get('port')}")
print(f"armed={c.get('armed')} buttons={c.get('buttons')} source={c.get('source')}")
print(f"motors={m.get('type')} mode={v.get('control_mode')} max_current={v.get('max_current_a')} max_duty={v.get('max_duty')}")
for name, cam in cams.items():
    print(f"{name}: enabled={cam.get('enabled')} host={cam.get('host')} quality={cam.get('preferred')}")
PY

echo
echo "== Recent warnings =="
journalctl -u gimli-rover -u gimli-sensors -u gimli-mavlink -u gimli-rc-input -b -n 80 --no-pager \
  | grep -Ei "error|failed|traceback|unreachable|timeout|i/o" || true

echo
echo "== Done =="
