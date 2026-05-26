#!/usr/bin/env bash
# Системная оптимизация Pi для Gimli Rover.
# Идемпотентно — можно запускать многократно.
#   sudo bash scripts/tune-pi.sh
# Что делает:
#   1. CPU governor → performance (меньше jitter)
#   2. UDP-буферы ядра (для WebRTC + MAVLink)
#   3. WiFi power-save → off (если есть wlan0)
#   4. journald в RAM c лимитом 50M (меньше износ SD)
#   5. tmpfs на /tmp (200M)
#   6. cake qdisc на tailscale0 если есть (антибуферблоат)
#   7. nice/io приоритеты для gimli-mavlink и gimli-rover
#   8. отключение лишних сервисов (bluetooth, modemmanager, и т.п.)
#   9. gpu_mem=16 в config.txt (headless — отдаёт RAM CPU)

set -u

if [[ $EUID -ne 0 ]]; then
    echo "Запускай через sudo: sudo bash scripts/tune-pi.sh" >&2
    exit 1
fi

log() { printf '\033[1;34m[tune]\033[0m %s\n' "$*"; }
ok()  { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m  ..\033[0m %s\n' "$*"; }

# ---------- 1. CPU governor ----------
log "CPU governor → performance"
if [[ -d /sys/devices/system/cpu/cpu0/cpufreq ]]; then
    for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo performance > "$c" 2>/dev/null || true
    done
    if ! command -v cpufreq-set >/dev/null 2>&1; then
        apt-get install -y -q cpufrequtils >/dev/null 2>&1 || warn "cpufrequtils не установлен"
    fi
    echo 'GOVERNOR="performance"' > /etc/default/cpufrequtils
    systemctl enable --now cpufrequtils >/dev/null 2>&1 || true
    ok "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
else
    warn "cpufreq не доступен"
fi

# ---------- 2. UDP буферы ----------
log "Сетевые буферы UDP"
cat > /etc/sysctl.d/99-gimli.conf << 'SYSCTL'
# Gimli Rover: WebRTC + MAVLink буферы
net.core.rmem_default = 2097152
net.core.rmem_max     = 16777216
net.core.wmem_default = 2097152
net.core.wmem_max     = 16777216
net.core.netdev_max_backlog = 5000
SYSCTL
sysctl --system >/dev/null
ok "sysctl применён"

# ---------- 3. WiFi power-save ----------
log "WiFi power-save"
if iw dev wlan0 info >/dev/null 2>&1; then
    iw dev wlan0 set power_save off 2>/dev/null || true
    # для NetworkManager — на все Wi-Fi соединения
    if command -v nmcli >/dev/null 2>&1; then
        for con in $(nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="802-11-wireless"{print $1}'); do
            nmcli connection modify "$con" 802-11-wireless.powersave 2 2>/dev/null || true
        done
    fi
    ok "wlan0: $(iw dev wlan0 get power_save 2>/dev/null || echo 'n/a')"
else
    warn "wlan0 отсутствует (езернет?)"
fi

# ---------- 4. journald в RAM ----------
log "journald в RAM, лимит 50M"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/gimli.conf << 'JCONF'
[Journal]
Storage=volatile
RuntimeMaxUse=50M
RuntimeMaxFileSize=10M
JCONF
systemctl restart systemd-journald
ok "journald перезапущен"

# ---------- 5. tmpfs /tmp ----------
log "tmpfs на /tmp (200M)"
if ! grep -qE '^\s*tmpfs\s+/tmp\s' /etc/fstab; then
    echo 'tmpfs /tmp tmpfs defaults,noatime,nosuid,size=200m 0 0' >> /etc/fstab
    ok "запись добавлена в fstab (применится после reboot)"
else
    ok "уже в fstab"
fi

# ---------- 6. cake qdisc ----------
log "cake qdisc на tailscale0 (анти-bufferbloat)"
if ip link show tailscale0 >/dev/null 2>&1; then
    if tc -V 2>/dev/null | grep -q .; then
        tc qdisc replace dev tailscale0 root cake 2>/dev/null \
          && ok "cake применён на tailscale0" \
          || warn "cake недоступен, пробую fq_codel" && \
             tc qdisc replace dev tailscale0 root fq_codel 2>/dev/null \
             && ok "fq_codel применён" \
             || warn "не получилось"
    else
        warn "iproute2 не установлен"
    fi
else
    warn "tailscale0 не поднят, пропускаю"
fi

# ---------- 7. nice/io priorities ----------
log "Приоритеты systemd-юнитов"
for unit in gimli-mavlink gimli-rover; do
    if systemctl list-unit-files | grep -q "^${unit}.service"; then
        mkdir -p "/etc/systemd/system/${unit}.service.d"
        cat > "/etc/systemd/system/${unit}.service.d/priority.conf" << OVR
[Service]
Nice=-5
IOSchedulingClass=best-effort
IOSchedulingPriority=2
CPUSchedulingPolicy=other
OVR
        ok "${unit}: приоритет повышен"
    fi
done
systemctl daemon-reload

# ---------- 7b. runtime tuning after every boot ----------
log "Runtime tuning service"
cat > /usr/local/sbin/gimli-runtime-tune << 'RUNTIME'
#!/usr/bin/env bash
set -u

for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    [[ -e "$c" ]] && echo performance > "$c" 2>/dev/null || true
done

if /usr/sbin/iw dev wlan0 info >/dev/null 2>&1; then
    /usr/sbin/iw dev wlan0 set power_save off 2>/dev/null || true
fi

if /usr/sbin/ip link show tailscale0 >/dev/null 2>&1; then
    /usr/sbin/tc qdisc replace dev tailscale0 root cake 2>/dev/null || \
    /usr/sbin/tc qdisc replace dev tailscale0 root fq_codel 2>/dev/null || true
fi
RUNTIME
chmod 0755 /usr/local/sbin/gimli-runtime-tune
cat > /etc/systemd/system/gimli-runtime-tune.service << 'UNIT'
[Unit]
Description=Gimli runtime performance tuning
After=network-online.target tailscaled.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/gimli-runtime-tune
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now gimli-runtime-tune.service >/dev/null 2>&1 || true
ok "gimli-runtime-tune.service enabled"

# ---------- 8. отключить ненужные сервисы ----------
log "Лишние сервисы"
for svc in bluetooth.service hciuart.service triggerhappy.service \
           ModemManager.service apt-daily-upgrade.timer apt-daily.timer; do
    if systemctl list-unit-files | grep -q "^${svc}"; then
        systemctl disable --now "$svc" >/dev/null 2>&1 && ok "выключен: $svc" || true
    fi
done

# ---------- 9. gpu_mem=16 (headless) ----------
log "gpu_mem=16 в /boot/firmware/config.txt"
CONFIG_TXT=""
for p in /boot/firmware/config.txt /boot/config.txt; do
    [[ -f "$p" ]] && CONFIG_TXT="$p" && break
done
if [[ -n "$CONFIG_TXT" ]]; then
    if grep -qE '^\s*gpu_mem\s*=' "$CONFIG_TXT"; then
        sed -i 's|^\s*gpu_mem\s*=.*|gpu_mem=16|' "$CONFIG_TXT"
        ok "gpu_mem обновлён в $CONFIG_TXT (применится после reboot)"
    else
        echo 'gpu_mem=16' >> "$CONFIG_TXT"
        ok "gpu_mem=16 добавлен в $CONFIG_TXT"
    fi
else
    warn "config.txt не найден"
fi

# ---------- финал ----------
echo
log "Готово. Часть изменений требует перезагрузки:"
echo "  - tmpfs /tmp"
echo "  - gpu_mem=16"
echo "  - WiFi powersave (для нового профиля nmcli)"
echo
log "Проверка термотроттлинга прямо сейчас:"
if command -v vcgencmd >/dev/null 2>&1; then
    echo "  temp: $(vcgencmd measure_temp)"
    echo "  throttled: $(vcgencmd get_throttled)   # 0x0 = всё ок"
fi
echo
log "Перезагрузка: sudo reboot"
