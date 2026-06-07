from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
RUNTIME_DIR = Path(os.environ.get("GIMLI_RUNTIME_DIR", CONFIG_DIR if os.name == "nt" else "/run/gimli"))
SETTINGS_FILE = Path(os.environ.get("GIMLI_SETTINGS_FILE", CONFIG_DIR / "settings.json"))
GO2RTC_FILE = Path(os.environ.get("GIMLI_GO2RTC_FILE", CONFIG_DIR / "go2rtc.yaml"))
SENSOR_STATE_FILE = Path(os.environ.get("GIMLI_SENSOR_STATE_FILE", RUNTIME_DIR / "sensor_state.json"))
SENSOR_COMMAND_FILE = Path(os.environ.get("GIMLI_SENSOR_COMMAND_FILE", RUNTIME_DIR / "sensor_command.json"))
CONTROL_STATE_FILE = Path(os.environ.get("GIMLI_CONTROL_STATE_FILE", RUNTIME_DIR / "control_state.json"))
CONTROL_COMMAND_FILE = Path(os.environ.get("GIMLI_CONTROL_COMMAND_FILE", RUNTIME_DIR / "control_command.json"))
RC_STATE_FILE = Path(os.environ.get("GIMLI_RC_STATE_FILE", RUNTIME_DIR / "rc_state.json"))
COMPASS_CALIBRATION_FILE = Path(os.environ.get("GIMLI_COMPASS_CALIBRATION_FILE", CONFIG_DIR / "compass_calibration.json"))

SETTINGS_CACHE_TTL_S = float(os.environ.get("GIMLI_SETTINGS_CACHE_TTL_S", "2.0") or "2.0")
_SETTINGS_CACHE: dict[str, Any] | None = None
_SETTINGS_CACHE_TS = 0.0
_SETTINGS_CACHE_MTIME = 0.0


DEFAULT_SETTINGS: dict[str, Any] = {
    "cameras": {
        "cam1": {
            "label": "передня",
            "enabled": True,
            "host": "192.168.1.108",
            "username": "admin",
            "password": "",
            "main_path": "/media/video1",
            "sub_path": "/media/video2",
            "preferred": "main",
        },
        "cam2": {
            "label": "задня",
            "enabled": True,
            "host": "192.168.1.109",
            "username": "admin",
            "password": "",
            "main_path": "/media/video1",
            "sub_path": "/media/video2",
            "preferred": "main",
        },
    },
    "network": {
        "profile": "balanced",
        "target_kbps": 1800,
        "link_mode": "auto",
        "wifi_ssid": "",
        "wifi_password": "",
        "wireguard": {
            "enabled": False,
            "interface": "wg0",
            "address": "",
            "private_key": "",
            "peer_public_key": "",
            "peer_endpoint": "",
            "allowed_ips": "0.0.0.0/0",
            "persistent_keepalive": 25,
        },
        "setup_ap": {
            "enabled": True,
            "ssid": "Gimli-Rover-Setup",
            "password": "gimli1234",
        },
    },
    "mavlink": {
        "enabled": True,
        "system_id": 1,
        "component_id": 1,
        "connection": "udpout:127.0.0.1:14550",
        "extra_connections": [],
        "vehicle_name": "Gimli Rover 1",
        "control": {
            "throttle_axis": "y",
            "steering_axis": "x",
            "throttle_invert": False,
            "steering_invert": False,
            "throttle_scale": 1.0,
            "steering_scale": 1.0,
        },
    },
    "video": {
        "stream_host": "",
        "stream_port": 8554,
        "active_stream": "cam1",
    },
    "power": {
        "battery_voltage": None,
        "low_voltage": 11.1,
        "current_a": None,
        "power_w": None,
        "current_sensor": {
            "enabled": True,
            "type": "ina228",
            "bus": "/dev/i2c-1",
            "address": "0x45",
            "shunt_ohms": 0.001,
            "current_lsb_a": 0.001,
        },
    },
    "navigation": {
        "source": "auto",
        "gps_enabled": False,
        "gps_trust": "auto",
        "home_latitude": None,
        "home_longitude": None,
        "max_jump_km": 5.0,
        "fix_type": 0,
        "satellites": 0,
        "latitude": None,
        "longitude": None,
        "altitude_m": 0.0,
        "heading_deg": None,
        "heading_offset_deg": 0.0,
        "heading_smoothing": 0.25,
        "compass_x_axis": "x",
        "compass_y_axis": "-y",
        "compass_z_axis": "-z",
        "gps_course_enabled": False,
        "gps_course_deg": None,
        "groundspeed_m_s": 0.0,
    },
    "motors": {
        "type": "gpio",
        "mock": False,
        "watchdog_timeout_s": 0.5,
        "vesc": {
            "port": "",
            "left_port": "",
            "right_port": "",
            "left_can_id": None,
            "right_can_id": 68,
            "baud": 115200,
            "max_duty": 0.12,
            "control_mode": "current",
            "max_current_a": 20.0,
            "max_rpm": 1200.0,
            "start_current_a": 0.0,
            "current_expo": 1.0,
            "command_ramp_per_s": 0.0,
            "failsafe_brake_current_a": 12.0,
            "neutral_deadzone": 0.06,
            "left_invert": False,
            "right_invert": False,
        },
        "pins": {
            "left_in1": 17,
            "left_in2": 27,
            "left_en": 18,
            "right_in1": 22,
            "right_in2": 23,
            "right_en": 13,
        },
    },
    "rc_input": {
        "enabled": True,
        "mode": "serial",
        "mix_mode": "tracks",
        "serial_port": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
        "baud": 115200,
        "steering_gpio": 5,
        "throttle_gpio": 6,
        "min_us": 1000,
        "center_us": 1500,
        "max_us": 2000,
        "deadzone": 0.06,
        "throttle_invert": False,
        "steering_invert": False,
        "send_hz": 25,
        "signal_timeout_s": 0.35,
    },
}


def load_settings() -> dict[str, Any]:
    global _SETTINGS_CACHE, _SETTINGS_CACHE_TS, _SETTINGS_CACHE_MTIME
    now = time.monotonic()
    try:
        mtime = SETTINGS_FILE.stat().st_mtime if SETTINGS_FILE.exists() else 0.0
    except OSError:
        mtime = 0.0
    if (
        _SETTINGS_CACHE is not None
        and now - _SETTINGS_CACHE_TS < SETTINGS_CACHE_TTL_S
        and mtime == _SETTINGS_CACHE_MTIME
    ):
        return deepcopy(_SETTINGS_CACHE)
    settings = deepcopy(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        with SETTINGS_FILE.open("r", encoding="utf-8") as f:
            _deep_update(settings, json.load(f))
    _SETTINGS_CACHE = settings
    _SETTINGS_CACHE_TS = now
    _SETTINGS_CACHE_MTIME = mtime
    return deepcopy(settings)


def save_settings(incoming: dict[str, Any]) -> dict[str, Any]:
    global _SETTINGS_CACHE, _SETTINGS_CACHE_TS, _SETTINGS_CACHE_MTIME
    current = load_settings()
    normalized = _normalize_settings(incoming, current)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_FILE.open("w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
        f.write("\n")
    write_go2rtc_config(normalized)
    write_wireguard_config(normalized)
    _SETTINGS_CACHE = deepcopy(normalized)
    _SETTINGS_CACHE_TS = time.monotonic()
    try:
        _SETTINGS_CACHE_MTIME = SETTINGS_FILE.stat().st_mtime
    except OSError:
        _SETTINGS_CACHE_MTIME = 0.0
    return normalized


def write_sensor_command(command: str) -> None:
    SENSOR_COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"command": command, "time": time.time()}) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".sensor_command.", suffix=".tmp", dir=str(SENSOR_COMMAND_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_name).replace(SENSOR_COMMAND_FILE)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass


def write_runtime_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        Path(tmp_name).replace(path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass


def public_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(settings or load_settings())
    for cam in data.get("cameras", {}).values():
        password = cam.get("password", "")
        cam["password"] = ""
        cam["password_set"] = bool(password)
    network = data.get("network", {})
    wifi_password = network.get("wifi_password", "")
    network["wifi_password"] = ""
    network["wifi_password_set"] = bool(wifi_password)
    wg = network.get("wireguard", {})
    private_key = wg.get("private_key", "")
    wg["private_key"] = ""
    wg["private_key_set"] = bool(private_key)
    ap = network.get("setup_ap", {})
    ap_password = ap.get("password", "")
    ap["password"] = ""
    ap["password_set"] = bool(ap_password)
    return data


def telemetry(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    data = settings or load_settings()
    navigation = deepcopy(data.get("navigation", DEFAULT_SETTINGS["navigation"]))
    sensor_state = _load_sensor_state()
    control_state = _load_control_state()
    rc_state = _load_json_state(RC_STATE_FILE)
    if sensor_state.get("navigation"):
        _deep_update(navigation, sensor_state["navigation"])
    power = deepcopy(data.get("power", {}))
    sensor_power = sensor_state.get("power", {})
    if sensor_power:
        _deep_update(power, sensor_power)
    return {
        "link": {
            "profile": data.get("network", {}).get("profile", "balanced"),
            "target_kbps": data.get("network", {}).get("target_kbps", 1800),
            "link_mode": data.get("network", {}).get("link_mode", "auto"),
            "wireguard_enabled": data.get("network", {}).get("wireguard", {}).get("enabled", False),
        },
        "power": {
            "battery_voltage": power.get("battery_voltage"),
            "low_voltage": power.get("low_voltage", 11.1),
            "current_a": power.get("current_a"),
            "power_w": power.get("power_w"),
        },
        "navigation": navigation,
        "cameras": {
            name: {
                "enabled": cam.get("enabled", True),
                "host": cam.get("host", ""),
                "preferred": _effective_preferred(cam, data),
            }
            for name, cam in data.get("cameras", {}).items()
        },
        "motors": {
            "type": data.get("motors", {}).get("type", "gpio"),
            "mock": data.get("motors", {}).get("mock", False),
            "watchdog_timeout_s": data.get("motors", {}).get("watchdog_timeout_s", 0.5),
            "vesc": data.get("motors", {}).get("vesc", DEFAULT_SETTINGS["motors"]["vesc"]),
        },
        "mavlink": data.get("mavlink", DEFAULT_SETTINGS["mavlink"]),
        "video": data.get("video", DEFAULT_SETTINGS["video"]),
        "control": control_state,
        "rc_input": rc_state,
    }


def write_go2rtc_config(settings: dict[str, Any] | None = None) -> None:
    settings = settings or load_settings()
    lines = [
        "# Generated by Gimli Rover settings UI.",
        "# Edit camera settings from the web interface instead of changing this file by hand.",
        "",
        "api:",
        '  listen: ":1984"',
        "",
        "webrtc:",
        '  listen: ":8555/tcp"',
        "  candidates:",
        "    - stun:8555",
        "",
        "streams:",
    ]

    active_stream = str(settings.get("video", {}).get("active_stream", "cam1") or "cam1")
    active_cam = settings.get("cameras", {}).get(active_stream)
    if active_cam and active_cam.get("enabled", True):
        active_urls = _camera_urls(active_cam, settings)
        active_audio_urls = _camera_urls(active_cam, settings, media="audio")
        lines.append("  active:")
        if active_urls:
            for url in active_urls:
                lines.append(f"    - {url}")
        else:
            lines.append("    - ffmpeg:blank")
        lines.append("")
        lines.append("  qgc:")
        if active_urls:
            lines.append(f"    - {active_urls[0]}")
        else:
            lines.append("    - ffmpeg:blank")
        lines.append("")
        lines.append("  active_audio:")
        if active_audio_urls:
            for url in active_audio_urls:
                lines.append(f"    - {url}")
        else:
            lines.append("    - ffmpeg:blank")
        lines.append("")

    for name, cam in settings.get("cameras", {}).items():
        if not cam.get("enabled", True):
            continue
        urls = _camera_urls(cam, settings)
        lines.append(f"  {name}:")
        if urls:
            for url in urls:
                lines.append(f"    - {url}")
        else:
            lines.append("    - ffmpeg:blank")
        lines.append("")

    lines.extend(["log:", "  level: info", ""])
    GO2RTC_FILE.parent.mkdir(parents=True, exist_ok=True)
    GO2RTC_FILE.write_text("\n".join(lines), encoding="utf-8")


def write_wireguard_config(settings: dict[str, Any] | None = None) -> None:
    settings = settings or load_settings()
    wg = settings.get("network", {}).get("wireguard", {})
    path = CONFIG_DIR / f"{wg.get('interface', 'wg0')}.conf"
    if not wg.get("enabled"):
        return
    lines = [
        "[Interface]",
        f"Address = {wg.get('address', '')}",
        f"PrivateKey = {wg.get('private_key', '')}",
        "",
        "[Peer]",
        f"PublicKey = {wg.get('peer_public_key', '')}",
        f"Endpoint = {wg.get('peer_endpoint', '')}",
        f"AllowedIPs = {wg.get('allowed_ips', '0.0.0.0/0')}",
        f"PersistentKeepalive = {int(wg.get('persistent_keepalive', 25))}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def network_status() -> dict[str, Any]:
    return {
        "interfaces": _run_json(["ip", "-j", "addr"]),
        "routes": _run_json(["ip", "-j", "route"]),
        "wifi": _run_text(["iw", "dev"]),
        "wireguard": _run_text(["wg", "show"]),
    }


def wifi_scan(interface: str = "wlan0") -> dict[str, Any]:
    output = _run_text(["sudo", "-n", "iw", "dev", interface, "scan"])
    if not output:
        output = _run_text(["iw", "dev", interface, "scan"])
    return {"interface": interface, "networks": _parse_iw_scan(output)}


def wifi_connect(ssid: str, password: str = "", interface: str = "wlan0") -> tuple[bool, str]:
    ssid = str(ssid or "").strip()
    password = str(password or "")
    interface = str(interface or "wlan0").strip()
    if not ssid:
        return False, "SSID is required"

    setup_ap_name = "gimli-setup-ap"
    profile_name = f"gimli-wifi-{ssid}"
    commands: list[list[str]] = [
        ["sudo", "-n", "nmcli", "con", "delete", profile_name],
        ["sudo", "-n", "nmcli", "con", "add", "type", "wifi", "ifname", interface, "con-name", profile_name, "ssid", ssid],
        ["sudo", "-n", "nmcli", "con", "modify", profile_name, "connection.autoconnect", "yes", "connection.autoconnect-priority", "100", "ipv4.route-metric", "50", "ipv6.method", "disabled"],
    ]
    if password:
        commands.append(["sudo", "-n", "nmcli", "con", "modify", profile_name, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])
    else:
        commands.append(["sudo", "-n", "nmcli", "con", "modify", profile_name, "wifi-sec.key-mgmt", "none"])
    commands.extend([
        ["sudo", "-n", "nmcli", "con", "down", setup_ap_name],
        ["sudo", "-n", "nmcli", "con", "up", profile_name],
    ])
    try:
        result = None
        for cmd in commands:
            check = not (cmd[3:5] == ["con", "delete"] or cmd[3:5] == ["con", "down"])
            result = subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=45)
        settings = load_settings()
        network = settings.setdefault("network", deepcopy(DEFAULT_SETTINGS["network"]))
        network["wifi_ssid"] = ssid
        if password:
            network["wifi_password"] = password
        network["link_mode"] = "wifi"
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_FILE.open("w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True, ((result.stdout if result else "") or "connected").strip()
    except FileNotFoundError:
        return False, "nmcli/sudo is not available"
    except subprocess.CalledProcessError as exc:
        subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/gimli-network-fallback", "start"],
            capture_output=True, text=True, timeout=30,
        )
        return False, (exc.stderr or exc.stdout or str(exc)).strip()
    except subprocess.TimeoutExpired:
        return False, "Wi-Fi connect timed out"


def setup_ap_control(action: str) -> tuple[bool, str]:
    action = str(action or "").strip().lower()
    if action not in {"start", "stop"}:
        return False, "action must be start or stop"
    cmd = ["sudo", "-n", "/usr/local/sbin/gimli-network-fallback", action]
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=45)
        return True, (result.stdout or f"setup ap {action} requested").strip()
    except FileNotFoundError:
        return False, "fallback script/sudo is not available"
    except subprocess.CalledProcessError as exc:
        return False, (exc.stderr or exc.stdout or str(exc)).strip()
    except subprocess.TimeoutExpired:
        return False, "setup ap command timed out"


def restart_go2rtc() -> tuple[bool, str]:
    cmd = ["sudo", "-n", "/bin/systemctl", "restart", "go2rtc.service"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=15)
        return True, "go2rtc restarted"
    except FileNotFoundError:
        return False, "systemctl/sudo is not available here"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return False, detail or "go2rtc restart failed"
    except subprocess.TimeoutExpired:
        return False, "go2rtc restart timed out"


def poweroff_pi() -> tuple[bool, str]:
    cmd = ["sudo", "-n", "/bin/systemctl", "poweroff"]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "poweroff requested"
    except FileNotFoundError:
        return False, "systemctl/sudo is not available here"
    except Exception as exc:
        return False, str(exc)


def reboot_pi() -> tuple[bool, str]:
    cmd = ["sudo", "-n", "/bin/systemctl", "reboot"]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, "reboot requested"
    except FileNotFoundError:
        return False, "systemctl/sudo is not available here"
    except Exception as exc:
        return False, str(exc)


def motor_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return deepcopy((settings or load_settings()).get("motors", DEFAULT_SETTINGS["motors"]))


def _normalize_settings(incoming: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    settings = deepcopy(current)
    _deep_update(settings, incoming)

    for name, default_cam in DEFAULT_SETTINGS["cameras"].items():
        cam = settings.setdefault("cameras", {}).setdefault(name, deepcopy(default_cam))
        old_password = current.get("cameras", {}).get(name, {}).get("password", "")
        if cam.get("password", "") == "":
            cam["password"] = old_password
        cam["enabled"] = bool(cam.get("enabled", True))
        cam["label"] = str(cam.get("label", default_cam["label"]))
        cam["host"] = str(cam.get("host", "")).strip()
        cam["username"] = str(cam.get("username", "admin")).strip()
        cam["main_path"] = _clean_path(cam.get("main_path", default_cam["main_path"]))
        cam["sub_path"] = _clean_path(cam.get("sub_path", default_cam["sub_path"]))
        cam["preferred"] = _choice(cam.get("preferred", "main"), {"main", "sub", "auto"}, "main")

    network = settings.setdefault("network", deepcopy(DEFAULT_SETTINGS["network"]))
    network["profile"] = _choice(network.get("profile", "balanced"), {"low", "balanced", "high"}, "balanced")
    network["target_kbps"] = int(network.get("target_kbps", 1800))
    network["link_mode"] = _choice(network.get("link_mode", "auto"), {"auto", "lan", "wifi"}, "auto")
    network["wifi_ssid"] = str(network.get("wifi_ssid", "")).strip()
    wifi_password = network.get("wifi_password", "")
    old_wifi_password = current.get("network", {}).get("wifi_password", "")
    network["wifi_password"] = old_wifi_password if wifi_password == "" else str(wifi_password)
    wg = network.setdefault("wireguard", deepcopy(DEFAULT_SETTINGS["network"]["wireguard"]))
    old_wg = current.get("network", {}).get("wireguard", {})
    wg["enabled"] = bool(wg.get("enabled", False))
    wg["interface"] = str(wg.get("interface", "wg0") or "wg0").strip()
    wg["address"] = str(wg.get("address", "")).strip()
    wg["private_key"] = old_wg.get("private_key", "") if wg.get("private_key", "") == "" else str(wg.get("private_key", "")).strip()
    wg["peer_public_key"] = str(wg.get("peer_public_key", "")).strip()
    wg["peer_endpoint"] = str(wg.get("peer_endpoint", "")).strip()
    wg["allowed_ips"] = str(wg.get("allowed_ips", "0.0.0.0/0") or "0.0.0.0/0").strip()
    wg["persistent_keepalive"] = int(wg.get("persistent_keepalive", 25))
    ap = network.setdefault("setup_ap", deepcopy(DEFAULT_SETTINGS["network"]["setup_ap"]))
    old_ap = current.get("network", {}).get("setup_ap", {})
    ap["enabled"] = bool(ap.get("enabled", True))
    ap["ssid"] = str(ap.get("ssid", "Gimli-Rover-Setup") or "Gimli-Rover-Setup").strip()
    ap_password = ap.get("password", "")
    ap["password"] = old_ap.get("password", "gimli1234") if ap_password == "" else str(ap_password)

    power = settings.setdefault("power", deepcopy(DEFAULT_SETTINGS["power"]))
    voltage = power.get("battery_voltage")
    power["battery_voltage"] = None if voltage in ("", None) else float(voltage)
    power["low_voltage"] = float(power.get("low_voltage", 11.1))
    power["current_a"] = _optional_float(power.get("current_a"))
    power["power_w"] = _optional_float(power.get("power_w"))
    sensor = power.setdefault("current_sensor", deepcopy(DEFAULT_SETTINGS["power"]["current_sensor"]))
    sensor["enabled"] = bool(sensor.get("enabled", True))
    sensor["type"] = str(sensor.get("type", "ina228") or "ina228").strip().lower()
    sensor["bus"] = str(sensor.get("bus", "/dev/i2c-1") or "/dev/i2c-1").strip()
    sensor["address"] = str(sensor.get("address", "0x45") or "0x45").strip()
    sensor["shunt_ohms"] = float(sensor.get("shunt_ohms", 0.001) or 0.001)
    sensor["current_lsb_a"] = float(sensor.get("current_lsb_a", 0.001) or 0.001)

    navigation = settings.setdefault("navigation", deepcopy(DEFAULT_SETTINGS["navigation"]))
    navigation["source"] = _choice(navigation.get("source", "auto"), {"auto", "gps", "off"}, "auto")
    navigation["gps_enabled"] = bool(navigation.get("gps_enabled", False))
    navigation["gps_trust"] = _choice(navigation.get("gps_trust", "auto"), {"auto", "trusted", "disabled"}, "auto")
    navigation["home_latitude"] = _optional_float(navigation.get("home_latitude"))
    navigation["home_longitude"] = _optional_float(navigation.get("home_longitude"))
    navigation["max_jump_km"] = float(navigation.get("max_jump_km", 5.0) or 5.0)
    navigation["fix_type"] = int(navigation.get("fix_type", 0))
    navigation["satellites"] = int(navigation.get("satellites", 0))
    navigation["latitude"] = _optional_float(navigation.get("latitude"))
    navigation["longitude"] = _optional_float(navigation.get("longitude"))
    navigation["altitude_m"] = float(navigation.get("altitude_m", 0.0) or 0.0)
    navigation["heading_deg"] = _optional_float(navigation.get("heading_deg"))
    navigation["heading_offset_deg"] = float(navigation.get("heading_offset_deg", 0.0) or 0.0)
    navigation["heading_smoothing"] = max(0.0, min(1.0, float(navigation.get("heading_smoothing", 0.25) or 0.25)))
    axis_values = {"x", "y", "z", "-x", "-y", "-z"}
    navigation["compass_x_axis"] = _choice(navigation.get("compass_x_axis", "x"), axis_values, "x")
    navigation["compass_y_axis"] = _choice(navigation.get("compass_y_axis", "-y"), axis_values, "-y")
    navigation["compass_z_axis"] = _choice(navigation.get("compass_z_axis", "-z"), axis_values, "-z")
    navigation["gps_course_enabled"] = bool(navigation.get("gps_course_enabled", False))
    navigation["gps_course_deg"] = _optional_float(navigation.get("gps_course_deg"))
    navigation["groundspeed_m_s"] = float(navigation.get("groundspeed_m_s", 0.0) or 0.0)

    mavlink = settings.setdefault("mavlink", deepcopy(DEFAULT_SETTINGS["mavlink"]))
    mavlink["enabled"] = bool(mavlink.get("enabled", True))
    mavlink["system_id"] = int(mavlink.get("system_id", 1))
    mavlink["component_id"] = int(mavlink.get("component_id", 1))
    mavlink["connection"] = str(mavlink.get("connection", DEFAULT_SETTINGS["mavlink"]["connection"])).strip()
    mavlink["extra_connections"] = _normalize_connections(mavlink.get("extra_connections", []))
    mavlink["vehicle_name"] = str(mavlink.get("vehicle_name", "Gimli Rover")).strip()
    mav_control = mavlink.setdefault("control", deepcopy(DEFAULT_SETTINGS["mavlink"]["control"]))
    mav_control["throttle_axis"] = _choice(mav_control.get("throttle_axis", "y"), {"x", "y", "z", "r"}, "y")
    mav_control["steering_axis"] = _choice(mav_control.get("steering_axis", "x"), {"x", "y", "z", "r"}, "x")
    mav_control["throttle_invert"] = bool(mav_control.get("throttle_invert", False))
    mav_control["steering_invert"] = bool(mav_control.get("steering_invert", False))
    mav_control["throttle_scale"] = max(0.0, min(1.0, float(mav_control.get("throttle_scale", 1.0) or 1.0)))
    mav_control["steering_scale"] = max(0.0, min(1.0, float(mav_control.get("steering_scale", 1.0) or 1.0)))

    video = settings.setdefault("video", deepcopy(DEFAULT_SETTINGS["video"]))
    video["stream_host"] = str(video.get("stream_host", "")).strip()
    video["stream_port"] = int(video.get("stream_port", 8554) or 8554)
    active = str(video.get("active_stream", "cam1")).strip()
    video["active_stream"] = active if active in ("cam1", "cam2") else "cam1"

    motors = settings.setdefault("motors", deepcopy(DEFAULT_SETTINGS["motors"]))
    motors["type"] = _choice(motors.get("type", "gpio"), {"gpio", "vesc"}, "gpio")
    motors["mock"] = bool(motors.get("mock", False))
    motors["watchdog_timeout_s"] = float(motors.get("watchdog_timeout_s", 0.5))
    vesc = motors.setdefault("vesc", deepcopy(DEFAULT_SETTINGS["motors"]["vesc"]))
    vesc["port"] = str(vesc.get("port", "") or vesc.get("left_port", "") or "").strip()
    vesc["left_port"] = str(vesc.get("left_port", "") or "").strip()
    vesc["right_port"] = str(vesc.get("right_port", "") or "").strip()
    vesc["left_can_id"] = _optional_int(vesc.get("left_can_id"))
    vesc["right_can_id"] = _optional_int(vesc.get("right_can_id"))
    vesc["baud"] = int(vesc.get("baud", 115200) or 115200)
    vesc["max_duty"] = max(0.0, min(1.0, float(vesc.get("max_duty", 0.12) or 0.12)))
    vesc["control_mode"] = _choice(vesc.get("control_mode", "current"), {"current", "duty", "rpm"}, "current")
    vesc["max_current_a"] = max(0.0, float(vesc.get("max_current_a", 20.0) or 20.0))
    vesc["max_rpm"] = max(0.0, float(vesc.get("max_rpm", 1200.0) or 1200.0))
    vesc["start_current_a"] = max(0.0, min(vesc["max_current_a"], float(vesc.get("start_current_a", 0.0) or 0.0)))
    vesc["current_expo"] = max(0.2, min(8.0, float(vesc.get("current_expo", 1.0) or 1.0)))
    vesc["command_ramp_per_s"] = max(0.0, min(10.0, float(vesc.get("command_ramp_per_s", 0.0) or 0.0)))
    vesc["failsafe_brake_current_a"] = max(0.0, min(80.0, float(vesc.get("failsafe_brake_current_a", 12.0) or 12.0)))
    vesc["neutral_deadzone"] = max(0.0, min(0.3, float(vesc.get("neutral_deadzone", 0.06) or 0.06)))
    vesc["left_invert"] = bool(vesc.get("left_invert", False))
    vesc["right_invert"] = bool(vesc.get("right_invert", False))
    pins = motors.setdefault("pins", {})
    for key, value in DEFAULT_SETTINGS["motors"]["pins"].items():
        pins[key] = int(pins.get(key, value))

    rc_input = settings.setdefault("rc_input", deepcopy(DEFAULT_SETTINGS["rc_input"]))
    rc_input["enabled"] = bool(rc_input.get("enabled", True))
    rc_input["mode"] = _choice(rc_input.get("mode", "serial"), {"gpio", "serial"}, "serial")
    rc_input["mix_mode"] = _choice(rc_input.get("mix_mode", "tracks"), {"axes", "tracks"}, "tracks")
    rc_input["serial_port"] = str(rc_input.get("serial_port", "/dev/ttyUSB0") or "/dev/ttyUSB0").strip()
    rc_input["baud"] = int(rc_input.get("baud", 115200) or 115200)
    rc_input["steering_gpio"] = int(rc_input.get("steering_gpio", 5) or 5)
    rc_input["throttle_gpio"] = int(rc_input.get("throttle_gpio", 6) or 6)
    rc_input["min_us"] = int(rc_input.get("min_us", 1000) or 1000)
    rc_input["center_us"] = int(rc_input.get("center_us", 1500) or 1500)
    rc_input["max_us"] = int(rc_input.get("max_us", 2000) or 2000)
    rc_input["deadzone"] = max(0.0, min(0.5, float(rc_input.get("deadzone", 0.06) or 0.06)))
    rc_input["throttle_invert"] = bool(rc_input.get("throttle_invert", False))
    rc_input["steering_invert"] = bool(rc_input.get("steering_invert", False))
    rc_input["send_hz"] = max(1, min(50, int(rc_input.get("send_hz", 25) or 25)))
    rc_input["signal_timeout_s"] = max(0.05, min(3.0, float(rc_input.get("signal_timeout_s", 0.35) or 0.35)))

    return settings


def _camera_urls(cam: dict[str, Any], settings: dict[str, Any], media: str = "video") -> list[str]:
    host = str(cam.get("host", "")).strip()
    if not host:
        return []
    user = quote(str(cam.get("username", "admin")), safe="")
    password = quote(str(cam.get("password", "")), safe="")
    auth = f"{user}:{password}@" if password else f"{user}@"
    base = f"rtsp://{auth}{host}:554"
    main = _clean_path(cam.get("main_path", ""))
    sub = _clean_path(cam.get("sub_path", ""))
    preferred = _effective_preferred(cam, settings)
    paths = [sub] if preferred == "sub" else [main]
    suffix = "#media=audio#backchannel=0" if media == "audio" else "#media=video#backchannel=0"
    return [base + path + suffix for path in paths if path]


def _effective_preferred(cam: dict[str, Any], settings: dict[str, Any]) -> str:
    return _choice(cam.get("preferred", "main"), {"main", "sub", "auto"}, "main")


def _clean_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    return path if path.startswith("/") else "/" + path


def _deep_update(target: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _choice(value: Any, allowed: set[str], fallback: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in allowed else fallback


def _optional_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if value in ("", None):
        return None
    return int(value)


def _normalize_connections(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace("\n", ",").split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        conn = str(item or "").strip()
        if not conn:
            continue
        if ":" in conn and not conn.startswith("udpout:"):
            conn = "udpout:" + conn
        elif not conn.startswith("udpout:"):
            conn = f"udpout:{conn}:14550"
        if conn not in seen:
            seen.add(conn)
            out.append(conn)
    return out


def _run_json(cmd: list[str]) -> Any:
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=5)
        return json.loads(result.stdout)
    except Exception:
        return []


def _run_text(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except Exception:
        return ""


def _parse_iw_scan(output: str) -> list[dict[str, Any]]:
    networks_by_ssid: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("BSS "):
            if current:
                _remember_network(networks_by_ssid, current)
            current = {"ssid": "", "signal": None, "security": "open"}
        elif current is not None and line.startswith("SSID:"):
            current["ssid"] = line.split("SSID:", 1)[1].strip()
        elif current is not None and line.startswith("signal:"):
            value = line.split("signal:", 1)[1].strip().split(" ")[0]
            try:
                current["signal"] = float(value)
            except ValueError:
                current["signal"] = None
        elif current is not None and ("WPA:" in line or "RSN:" in line):
            current["security"] = "secured"
    if current:
        _remember_network(networks_by_ssid, current)
    return sorted(networks_by_ssid.values(), key=lambda n: n.get("signal") or -999, reverse=True)


def _remember_network(networks: dict[str, dict[str, Any]], network: dict[str, Any]) -> None:
    ssid = str(network.get("ssid", "")).strip()
    if not ssid:
        return
    previous = networks.get(ssid)
    if previous is None or (network.get("signal") or -999) > (previous.get("signal") or -999):
        networks[ssid] = network


def _load_sensor_state() -> dict[str, Any]:
    try:
        if not SENSOR_STATE_FILE.exists():
            return {}
        with SENSOR_STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_control_state() -> dict[str, Any]:
    return _load_json_state(CONTROL_STATE_FILE)


def _load_json_state(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
