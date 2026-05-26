#!/usr/bin/env bash
# Установка Gimli Rover на свежий Raspberry Pi OS (Bookworm, 64-bit).
# Запускать ОТ ПОЛЬЗОВАТЕЛЯ gimli1mb (НЕ от root):
#     bash scripts/install.sh
#
# Что делает:
#   1. ставит системные пакеты
#   2. кладёт проект в /opt/gimli
#   3. создаёт venv и ставит python-зависимости
#   4. качает бинарник go2rtc
#   5. ставит Tailscale (если ещё не стоит)
#   6. включает systemd-юниты
#   7. подсказывает следующие шаги

set -euo pipefail

PROJECT_USER="${PROJECT_USER:-gimli1mb}"
PROJECT_DIR="/opt/gimli"
ARCH="$(dpkg --print-architecture)"   # arm64 на Pi 4 с 64-bit OS

if [[ "$(id -un)" != "$PROJECT_USER" ]]; then
  echo "Запусти от пользователя $PROJECT_USER (текущий: $(id -un))"; exit 1
fi

echo "==> apt: системные пакеты"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  python3-lgpio \
  git curl ca-certificates rsync \
  wireguard-tools iproute2 iw \
  ffmpeg

echo "==> группы для GPIO"
sudo usermod -aG gpio,dialout "$PROJECT_USER"

echo "==> копируем проект в $PROJECT_DIR"
sudo mkdir -p "$PROJECT_DIR"
sudo chown "$PROJECT_USER:$PROJECT_USER" "$PROJECT_DIR"
# rsync исходников из текущей директории (где лежит этот скрипт)
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_BACKUP="$(mktemp -d)"
if [[ -f "$PROJECT_DIR/config/settings.json" ]]; then
  cp "$PROJECT_DIR/config/settings.json" "$RUNTIME_BACKUP/settings.json"
fi
if [[ -f "$PROJECT_DIR/config/go2rtc.yaml" ]]; then
  cp "$PROJECT_DIR/config/go2rtc.yaml" "$RUNTIME_BACKUP/go2rtc.yaml"
fi
rsync -a --delete --exclude='.git' --exclude='__pycache__' --exclude='.venv' "$SRC_DIR/" "$PROJECT_DIR/"
if [[ -f "$RUNTIME_BACKUP/settings.json" ]]; then
  cp "$RUNTIME_BACKUP/settings.json" "$PROJECT_DIR/config/settings.json"
fi
if [[ -f "$RUNTIME_BACKUP/go2rtc.yaml" ]]; then
  cp "$RUNTIME_BACKUP/go2rtc.yaml" "$PROJECT_DIR/config/go2rtc.yaml"
fi
rm -rf "$RUNTIME_BACKUP"

echo "==> python venv (с доступом к system site-packages для lgpio)"
cd "$PROJECT_DIR"
python3 -m venv --system-site-packages .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt

echo "==> go2rtc"
GO2RTC_VER="1.9.4"
case "$ARCH" in
  arm64) GO2RTC_BIN="go2rtc_linux_arm64" ;;
  armhf) GO2RTC_BIN="go2rtc_linux_arm" ;;
  amd64) GO2RTC_BIN="go2rtc_linux_amd64" ;;
  *) echo "unsupported arch: $ARCH"; exit 1 ;;
esac
if ! command -v go2rtc >/dev/null; then
  curl -fsSL -o /tmp/go2rtc \
    "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VER}/${GO2RTC_BIN}"
  chmod +x /tmp/go2rtc
  sudo mv /tmp/go2rtc /usr/local/bin/go2rtc
fi

echo "==> Tailscale"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
echo "    После установки выполни:  sudo tailscale up --ssh"
echo "    И запиши имя хоста — это и будет адрес ровера."

echo "==> systemd"
sudo install -m 0755 "$PROJECT_DIR/scripts/gimli-network-fallback.sh" /usr/local/sbin/gimli-network-fallback
sudo cp "$PROJECT_DIR/config/gimli-rover.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/config/go2rtc.service"     /etc/systemd/system/
sudo cp "$PROJECT_DIR/config/gimli-mavlink.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/config/gimli-sensors.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/config/gimli-rc-input.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/config/gimli-network-fallback.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR/config/gimli-wifi-bootstrap.service" /etc/systemd/system/
sudo tee /etc/sudoers.d/gimli-rover >/dev/null <<EOF
$PROJECT_USER ALL=(root) NOPASSWD: /bin/systemctl restart go2rtc.service, /usr/bin/systemctl restart go2rtc.service, /bin/systemctl restart gimli-mavlink.service, /usr/bin/systemctl restart gimli-mavlink.service, /bin/systemctl poweroff, /usr/bin/systemctl poweroff, /bin/systemctl reboot, /usr/bin/systemctl reboot, /usr/sbin/iw dev wlan0 scan, /sbin/iw dev wlan0 scan, /usr/bin/nmcli dev wifi connect *, /usr/local/sbin/gimli-network-fallback start, /usr/local/sbin/gimli-network-fallback stop
EOF
sudo chmod 0440 /etc/sudoers.d/gimli-rover
sudo systemctl daemon-reload
sudo systemctl enable go2rtc.service gimli-rover.service gimli-sensors.service gimli-mavlink.service gimli-rc-input.service gimli-network-fallback.service gimli-wifi-bootstrap.service
sudo systemctl restart go2rtc.service
sudo systemctl restart gimli-rover.service
sudo systemctl restart gimli-sensors.service
sudo systemctl restart gimli-mavlink.service
sudo systemctl restart gimli-rc-input.service

echo
echo "===== ГОТОВО ====="
echo "Веб-интерфейс:  http://$(hostname -I | awk '{print $1}'):8080"
echo "go2rtc UI:      http://$(hostname -I | awk '{print $1}'):1984"
echo "Логи бэка:      journalctl -u gimli-rover -f"
echo "Логи камер:     journalctl -u go2rtc -f"
echo "Логи пульта:    journalctl -u gimli-rc-input -f"
echo
echo "Дальше:"
echo "  1. отредактируй $PROJECT_DIR/config/go2rtc.yaml — IP и пароли камер"
echo "     sudo systemctl restart go2rtc"
echo "  2. подними Tailscale:  sudo tailscale up --ssh"
echo "  3. с телефона/ноута зайди через Tailscale-имя ровера, порт 8080"
