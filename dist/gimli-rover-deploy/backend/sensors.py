from __future__ import annotations

import json
import math
import os
import select
import struct
import time
from pathlib import Path
from typing import Any

import fcntl

from backend.settings import COMPASS_CALIBRATION_FILE, SENSOR_COMMAND_FILE, SENSOR_STATE_FILE, load_settings


GPS_DEVICE = os.environ.get("GIMLI_GPS_DEVICE", "/dev/serial0")
I2C_DEVICE = os.environ.get("GIMLI_I2C_DEVICE", "/dev/i2c-1")
COMPASS_ADDR = int(os.environ.get("GIMLI_COMPASS_ADDR", "0x0d"), 0)
I2C_SLAVE = 0x0703


def main() -> None:
    gps = GpsReader(GPS_DEVICE)
    compass = Qmc5883l(I2C_DEVICE, COMPASS_ADDR)
    power_sensor: Ina228 | None = None
    calibrator = CompassCalibrator(compass)
    state: dict[str, Any] = {
        "source": "auto",
        "gps_enabled": False,
        "gps_safe": False,
        "gps_warning": "",
        "fix_type": 0,
        "satellites": 0,
        "latitude": None,
        "longitude": None,
        "altitude_m": 0.0,
        "heading_deg": None,
        "mag_raw": None,
        "groundspeed_m_s": 0.0,
    }
    last_write = 0.0
    smooth_heading: float | None = None

    while True:
        nav_cfg = load_settings().get("navigation", {})
        power_cfg = load_settings().get("power", {}).get("current_sensor", {})
        if power_sensor is None and power_cfg.get("enabled", True):
            power_sensor = Ina228(
                str(power_cfg.get("bus", "/dev/i2c-1")),
                int(str(power_cfg.get("address", "0x45")), 0),
                float(power_cfg.get("shunt_ohms", 0.001) or 0.001),
                float(power_cfg.get("current_lsb_a", 0.001) or 0.001),
            )
        gps_update = gps.poll()
        if gps_update:
            state.update(_filter_gps_update(gps_update, nav_cfg))
        elif nav_cfg.get("source") == "off" or nav_cfg.get("gps_trust") == "disabled":
            state.update(_disabled_gps_state("gps disabled"))

        command = _pop_command()
        if command == "start_mag_cal":
            calibrator.start()
        elif command == "cancel_mag_cal":
            calibrator.cancel()
        elif command == "accept_mag_cal":
            calibrator.accept()

        raw_mag = compass.read_uncalibrated()
        calibrated_mag = compass.read_raw()
        if calibrated_mag is not None:
            transformed = _transform_mag(calibrated_mag, nav_cfg)
            heading = (math.degrees(math.atan2(transformed[1], transformed[0])) + 360.0) % 360.0
            heading = (heading + float(nav_cfg.get("heading_offset_deg", 0.0) or 0.0)) % 360.0
            smoothing = max(0.0, min(1.0, float(nav_cfg.get("heading_smoothing", 0.25) or 0.25)))
            smooth_heading = _smooth_angle(smooth_heading, heading, smoothing)
            state["heading_deg"] = smooth_heading
        if raw_mag is not None:
            raw_mag = _transform_mag(raw_mag, nav_cfg)
            state["mag_raw"] = [int(raw_mag[0]), int(raw_mag[1]), int(raw_mag[2])]

        cal_state = calibrator.poll()
        power_state = power_sensor.read() if power_sensor else {}

        now = time.time()
        if now - last_write >= 0.5:
            _write_state({"navigation": state, "power": power_state, "compass_calibration": cal_state, "updated_at": now})
            last_write = now

        time.sleep(0.05)


class GpsReader:
    def __init__(self, device: str) -> None:
        self.fd: int | None = None
        self.buf = bytearray()
        try:
            self.fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            print(f"GPS open failed: {exc}", flush=True)

    def poll(self) -> dict[str, Any] | None:
        if self.fd is None:
            return None
        ready, _, _ = select.select([self.fd], [], [], 0)
        if not ready:
            return None
        try:
            chunk = os.read(self.fd, 4096)
        except OSError:
            return None
        if not chunk:
            return None
        self.buf.extend(chunk)
        update: dict[str, Any] = {}
        while b"\n" in self.buf:
            raw, _, rest = self.buf.partition(b"\n")
            self.buf = bytearray(rest)
            line = raw.decode("ascii", errors="ignore").strip()
            parsed = _parse_nmea(line)
            if parsed:
                update.update(parsed)
        return update or None


class Qmc5883l:
    def __init__(self, device: str, address: int) -> None:
        self.device = device
        self.address = address
        self.fd: int | None = None
        self.last_open_attempt = 0.0
        self.offset = [0.0, 0.0, 0.0]
        self.scale = [1.0, 1.0, 1.0]
        self._load_calibration()
        self._open()

    def _open(self) -> None:
        self.last_open_attempt = time.time()
        try:
            self.fd = os.open(self.device, os.O_RDWR)
            fcntl.ioctl(self.fd, I2C_SLAVE, self.address)
            self._write_reg(0x0B, 0x01)
            self._write_reg(0x09, 0x1D)
            print(f"QMC5883L compass ready at {hex(self.address)}", flush=True)
        except OSError as exc:
            print(f"Compass open/init failed: {exc}", flush=True)
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None

    def _ensure_open(self) -> bool:
        if self.fd is not None:
            return True
        if time.time() - self.last_open_attempt >= 2.0:
            self._open()
        return self.fd is not None

    def heading_deg(self) -> float | None:
        raw = self.read_raw()
        if raw is None:
            return None
        x, y, _z = raw
        return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    def read_raw(self) -> tuple[float, float, float] | None:
        if not self._ensure_open():
            return None
        try:
            self._write(bytes([0x00]))
            data = os.read(self.fd, 6)
        except OSError:
            return None
        if len(data) != 6:
            return None
        x, y, _z = struct.unpack("<hhh", data)
        if x == 0 and y == 0 and _z == 0:
            return None
        return (
            (float(x) - self.offset[0]) * self.scale[0],
            (float(y) - self.offset[1]) * self.scale[1],
            (float(_z) - self.offset[2]) * self.scale[2],
        )

    def read_uncalibrated(self) -> tuple[float, float, float] | None:
        if not self._ensure_open():
            return None
        try:
            self._write(bytes([0x00]))
            data = os.read(self.fd, 6)
        except OSError:
            return None
        if len(data) != 6:
            return None
        x, y, z = struct.unpack("<hhh", data)
        if x == 0 and y == 0 and z == 0:
            return None
        return float(x), float(y), float(z)

    def apply_calibration(self, offset: list[float], scale: list[float]) -> None:
        self.offset = offset
        self.scale = scale
        COMPASS_CALIBRATION_FILE.write_text(
            json.dumps({"offset": offset, "scale": scale}, indent=2) + "\n",
            encoding="utf-8",
        )

    def _load_calibration(self) -> None:
        try:
            if not COMPASS_CALIBRATION_FILE.exists():
                return
            data = json.loads(COMPASS_CALIBRATION_FILE.read_text(encoding="utf-8"))
            self.offset = [float(v) for v in data.get("offset", self.offset)]
            self.scale = [float(v) for v in data.get("scale", self.scale)]
        except Exception as exc:
            print(f"Compass calibration load failed: {exc}", flush=True)

    def _write_reg(self, reg: int, value: int) -> None:
        self._write(bytes([reg, value]))

    def _write(self, data: bytes) -> None:
        if self.fd is None:
            return
        os.write(self.fd, data)


class CompassCalibrator:
    def __init__(self, compass: Qmc5883l) -> None:
        self.compass = compass
        self.active = False
        self.accepted = False
        self.samples = 0
        self.started_at = 0.0
        self.min_v = [float("inf"), float("inf"), float("inf")]
        self.max_v = [float("-inf"), float("-inf"), float("-inf")]
        self.last_result: dict[str, Any] | None = None

    def start(self) -> None:
        self.active = True
        self.accepted = False
        self.samples = 0
        self.started_at = time.time()
        self.min_v = [float("inf"), float("inf"), float("inf")]
        self.max_v = [float("-inf"), float("-inf"), float("-inf")]
        self.last_result = None
        print("Compass calibration started", flush=True)

    def cancel(self) -> None:
        if self.active:
            print("Compass calibration cancelled", flush=True)
        self.active = False
        self.accepted = False

    def accept(self) -> None:
        if self.last_result:
            self.compass.apply_calibration(self.last_result["offset"], self.last_result["scale"])
            self.accepted = True
            print("Compass calibration accepted", flush=True)

    def poll(self) -> dict[str, Any]:
        if not self.active:
            if self.last_result:
                return {"active": False, "status": "success", "progress": 100, **self.last_result}
            return {"active": False, "status": "idle", "progress": 0}

        raw = self.compass.read_uncalibrated()
        if raw:
            self.samples += 1
            for i, value in enumerate(raw):
                self.min_v[i] = min(self.min_v[i], value)
                self.max_v[i] = max(self.max_v[i], value)

        ranges = [max(0.0, self.max_v[i] - self.min_v[i]) for i in range(3)]
        coverage = min(1.0, min(ranges) / 600.0) if self.samples > 10 else 0.0
        sample_progress = min(1.0, self.samples / 300.0)
        progress = int(min(99.0, max(coverage, sample_progress * 0.75) * 100.0))

        if self.samples >= 300 and coverage >= 0.65:
            self.active = False
            offset = [(self.max_v[i] + self.min_v[i]) / 2.0 for i in range(3)]
            radii = [max(1.0, (self.max_v[i] - self.min_v[i]) / 2.0) for i in range(3)]
            avg = sum(radii) / 3.0
            scale = [avg / r for r in radii]
            self.last_result = {
                "offset": offset,
                "scale": scale,
                "fitness": round(max(0.0, 1.0 - abs(max(radii) - min(radii)) / max(radii)), 3),
                "samples": self.samples,
            }
            self.compass.apply_calibration(offset, scale)
            print("Compass calibration completed", flush=True)
            return {"active": False, "status": "success", "progress": 100, **self.last_result}

        return {
            "active": True,
            "status": "running",
            "progress": progress,
            "samples": self.samples,
            "ranges": ranges,
        }


class Ina228:
    def __init__(self, device: str, address: int, shunt_ohms: float, current_lsb_a: float) -> None:
        self.device = device
        self.address = address
        self.shunt_ohms = shunt_ohms
        self.current_lsb_a = current_lsb_a
        self.fd: int | None = None
        self.last_open_attempt = 0.0
        self._open()

    def _open(self) -> None:
        self.last_open_attempt = time.time()
        try:
            self.fd = os.open(self.device, os.O_RDWR)
            fcntl.ioctl(self.fd, I2C_SLAVE, self.address)
            manufacturer = self._read_u16(0xFE)
            device_id = self._read_u16(0xFF)
            print(f"INA228 power sensor ready at {hex(self.address)} mfg={hex(manufacturer)} id={hex(device_id)}", flush=True)
        except OSError as exc:
            print(f"INA228 open/read failed: {exc}", flush=True)
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None

    def read(self) -> dict[str, Any]:
        if not self._ensure_open():
            return {}
        try:
            vbus_raw = self._read_u24(0x05)
            current_raw = self._read_s24(0x07)
            power_raw = self._read_u24(0x08)
        except OSError as exc:
            print(f"INA228 read failed: {exc}", flush=True)
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None
            return {}
        voltage = vbus_raw * 195.3125e-6
        current = current_raw * self.current_lsb_a
        power = power_raw * 3.2 * self.current_lsb_a
        return {
            "battery_voltage": round(voltage, 3),
            "current_a": round(current, 3),
            "power_w": round(power, 3),
        }

    def _ensure_open(self) -> bool:
        if self.fd is not None:
            return True
        if time.time() - self.last_open_attempt >= 2.0:
            self._open()
        return self.fd is not None

    def _read_u16(self, reg: int) -> int:
        self._write_reg(reg)
        return int.from_bytes(os.read(self.fd, 2), "big")

    def _read_u24(self, reg: int) -> int:
        self._write_reg(reg)
        return int.from_bytes(os.read(self.fd, 3), "big") >> 4

    def _read_s24(self, reg: int) -> int:
        value = self._read_u24(reg)
        if value & (1 << 19):
            value -= 1 << 20
        return value

    def _write_reg(self, reg: int) -> None:
        if self.fd is None:
            raise OSError("INA228 is not open")
        os.write(self.fd, bytes([reg]))


def _parse_nmea(line: str) -> dict[str, Any] | None:
    if not line.startswith("$") or "*" not in line:
        return None
    body, checksum = line[1:].split("*", 1)
    if not _valid_checksum(body, checksum[:2]):
        return None
    fields = body.split(",")
    msg = fields[0][2:]
    if msg == "GGA" and len(fields) >= 10:
        fix = int(fields[6] or 0)
        return {
            "gps_enabled": True,
            "fix_type": 3 if fix else 0,
            "satellites": int(fields[7] or 0),
            "latitude": _nmea_coord(fields[2], fields[3]),
            "longitude": _nmea_coord(fields[4], fields[5]),
            "altitude_m": float(fields[9] or 0.0),
        }
    if msg == "RMC" and len(fields) >= 9:
        valid = fields[2] == "A"
        course = None
        try:
            course = float(fields[8]) if fields[8] else None
        except ValueError:
            course = None
        return {
            "gps_enabled": True,
            "fix_type": 3 if valid else 0,
            "latitude": _nmea_coord(fields[3], fields[4]),
            "longitude": _nmea_coord(fields[5], fields[6]),
            "groundspeed_m_s": float(fields[7] or 0.0) * 0.514444,
            "gps_course_deg": course,
        }
    return None


def _filter_gps_update(update: dict[str, Any], nav_cfg: dict[str, Any]) -> dict[str, Any]:
    source = str(nav_cfg.get("source", "auto"))
    trust = str(nav_cfg.get("gps_trust", "auto"))
    if source == "off" or trust == "disabled":
        return _disabled_gps_state("gps disabled")

    lat = update.get("latitude")
    lon = update.get("longitude")
    warning = ""
    safe = bool(update.get("fix_type", 0) >= 2 and lat is not None and lon is not None)

    home_lat = nav_cfg.get("home_latitude")
    home_lon = nav_cfg.get("home_longitude")
    if trust != "trusted" and safe and home_lat is not None and home_lon is not None:
        distance = _haversine_km(float(home_lat), float(home_lon), float(lat), float(lon))
        if distance > float(nav_cfg.get("max_jump_km", 5.0) or 5.0):
            warning = f"gps spoof/suspicious distance {distance:.1f} km"
            safe = False

    if not safe:
        filtered = _disabled_gps_state(warning or "gps unsafe")
        filtered["satellites"] = int(update.get("satellites", 0) or 0)
        return filtered

    update["source"] = source
    update["gps_safe"] = True
    update["gps_warning"] = ""
    return update


def _disabled_gps_state(reason: str) -> dict[str, Any]:
    return {
        "gps_enabled": False,
        "gps_safe": False,
        "gps_warning": reason,
        "fix_type": 0,
        "latitude": None,
        "longitude": None,
        "altitude_m": 0.0,
        "groundspeed_m_s": 0.0,
        "gps_course_deg": None,
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lam / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _smooth_angle(previous: float | None, current: float, alpha: float) -> float:
    if previous is None:
        return current
    prev_rad = math.radians(previous)
    cur_rad = math.radians(current)
    x = (1.0 - alpha) * math.cos(prev_rad) + alpha * math.cos(cur_rad)
    y = (1.0 - alpha) * math.sin(prev_rad) + alpha * math.sin(cur_rad)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _transform_mag(vector: tuple[float, float, float], nav_cfg: dict[str, Any]) -> tuple[float, float, float]:
    return (
        _axis_value(vector, str(nav_cfg.get("compass_x_axis", "x") or "x")),
        _axis_value(vector, str(nav_cfg.get("compass_y_axis", "y") or "y")),
        _axis_value(vector, str(nav_cfg.get("compass_z_axis", "z") or "z")),
    )


def _axis_value(vector: tuple[float, float, float], axis: str) -> float:
    sign = -1.0 if axis.startswith("-") else 1.0
    name = axis[1:] if axis.startswith("-") else axis
    idx = {"x": 0, "y": 1, "z": 2}.get(name, 0)
    return sign * float(vector[idx])


def _valid_checksum(body: str, checksum: str) -> bool:
    value = 0
    for char in body:
        value ^= ord(char)
    try:
        return value == int(checksum, 16)
    except ValueError:
        return False


def _nmea_coord(value: str, hemisphere: str) -> float | None:
    if not value or not hemisphere:
        return None
    raw = float(value)
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    coord = degrees + minutes / 60.0
    if hemisphere in {"S", "W"}:
        coord *= -1
    return coord


def _write_state(state: dict[str, Any]) -> None:
    SENSOR_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SENSOR_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SENSOR_STATE_FILE)


def _pop_command() -> str | None:
    try:
        if not SENSOR_COMMAND_FILE.exists():
            return None
        data = json.loads(SENSOR_COMMAND_FILE.read_text(encoding="utf-8"))
        SENSOR_COMMAND_FILE.unlink(missing_ok=True)
        return str(data.get("command") or "")
    except Exception:
        return None


if __name__ == "__main__":
    main()
