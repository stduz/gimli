#!/usr/bin/env bash
set -euo pipefail

# Keep the rover responsive during field tests: trim logs and remove stale
# MJPEG fallback encoders left behind by broken browser/network sessions.

/usr/bin/journalctl --vacuum-size=32M >/dev/null 2>&1 || true

now="$(date +%s)"
pgrep -af "ffmpeg .*127\\.0\\.0\\.1:8554/.* -f mpjpeg" | while read -r pid _cmd; do
  [ -n "${pid:-}" ] || continue
  etimes="$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')"
  [ -n "${etimes:-}" ] || continue
  # A healthy fallback is refreshed by the web UI; anything this old is almost
  # always an abandoned client and costs a full CPU core on Pi.
  if [ "$etimes" -gt 180 ]; then
    kill "$pid" 2>/dev/null || true
  fi
done || true

find /tmp -maxdepth 1 -type f \( -name 'gimli-*.tmp' -o -name 'uvicorn-*.tmp' \) -mmin +30 -delete 2>/dev/null || true
