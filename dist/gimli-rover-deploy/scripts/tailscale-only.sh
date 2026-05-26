#!/usr/bin/env bash
# Минимальный шаг 1: подними туннель.
# Запускать НА Pi (192.168.1.50), под gimli1mb. Sudo попросит пароль.
#
#   ssh gimli1mb@192.168.1.50
#   curl -fsSL https://example/tailscale-only.sh | bash      # или просто скопировать
#   bash tailscale-only.sh
#
# После запуска откроется ссылка в браузере для авторизации — открой её
# с любого устройства, на котором у тебя залогинен Tailscale. После
# авторизации скрипт допечатает hostname и IP — это адрес ровера в тайлнете.

set -euo pipefail

echo "==> apt update"
sudo apt-get update

echo "==> Tailscale: установка"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
else
  echo "    Tailscale уже установлен ($(tailscale version | head -1))"
fi

echo "==> Включаем IPv4 forwarding (на случай subnet router)"
sudo sed -i 's/^#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' /etc/sysctl.conf || true
sudo sysctl -p >/dev/null

echo "==> tailscale up --ssh --hostname=gimli-rover"
echo "    (откроется ссылка для авторизации — открой её в браузере)"
sudo tailscale up --ssh --hostname=gimli-rover --accept-routes

echo
echo "===== ГОТОВО ====="
echo "Hostname в тайлнете:  $(sudo tailscale status --self --json | python3 -c 'import sys,json;d=json.load(sys.stdin);s=d.get(\"Self\",{});print(s.get(\"HostName\",\"?\"))')"
echo "IP в тайлнете:        $(tailscale ip -4 2>/dev/null | head -1)"
echo
echo "Проверь с другого устройства (которое уже в твоём тайлнете):"
echo "   ping gimli-rover"
echo "   ssh gimli1mb@gimli-rover"
echo
echo "Когда увидишь Pi в тайлнете — скажи, и пойдём ставить ровер."
