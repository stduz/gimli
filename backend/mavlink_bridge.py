from __future__ import annotations

import json
import math
import socket
import struct
import time
import urllib.error
import urllib.request
from typing import Any

# чтобы при первом запросе из QGC отдать актуальные настройки видео,
# не блокируя цикл — кэшируем имя хоста.
_FQDN_CACHE: str | None = None
_FQDN_CACHE_TS = 0.0
_FQDN_CACHE_TTL_S = 30.0

from backend.settings import CONTROL_COMMAND_FILE, CONTROL_STATE_FILE, SENSOR_STATE_FILE, load_settings, telemetry, write_runtime_json, write_sensor_command
from backend.watchdog import SystemdWatchdog


BACKEND_SETTINGS_URL = "http://127.0.0.1:8080/api/settings"
BACKEND_CAMERA_URL = "http://127.0.0.1:8080/api/camera/{camera}/control"
_CONTROL_SEQ = 0

MAV_TYPE_GROUND_ROVER = 10
MAV_TYPE_CAMERA = 30
MAV_AUTOPILOT_GENERIC = 0
MAV_AUTOPILOT_INVALID = 8
MAV_MODE_FLAG_SAFETY_ARMED = 128
MAV_MODE_FLAG_MANUAL_INPUT_ENABLED = 64
MAV_STATE_ACTIVE = 4
MAV_CMD_COMPONENT_ARM_DISARM = 400
MAV_CMD_DO_FLIGHTTERMINATION = 185
MAV_CMD_PREFLIGHT_CALIBRATION = 241
MAV_CMD_DO_START_MAG_CAL = 42424
MAV_CMD_DO_ACCEPT_MAG_CAL = 42425
MAV_CMD_DO_CANCEL_MAG_CAL = 42426
MAV_RESULT_ACCEPTED = 0
MAV_RESULT_UNSUPPORTED = 3
MAG_CAL_RUNNING_STEP_ONE = 2
MAG_CAL_SUCCESS = 4
MAG_CAL_FAILED = 5
MAV_SYS_STATUS_SENSOR_3D_GYRO = 1
MAV_SYS_STATUS_SENSOR_3D_ACCEL = 2
MAV_SYS_STATUS_SENSOR_3D_MAG = 4
MAV_SYS_STATUS_SENSOR_GPS = 32
MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS = 32768

CRC_EXTRA = {
    0: 50,    # HEARTBEAT
    1: 124,   # SYS_STATUS
    27: 144,  # RAW_IMU
    30: 39,   # ATTITUDE
    20: 214,  # PARAM_REQUEST_READ
    21: 159,  # PARAM_REQUEST_LIST
    22: 220,  # PARAM_VALUE
    23: 168,  # PARAM_SET
    24: 24,   # GPS_RAW_INT
    43: 132,  # MISSION_REQUEST_LIST
    44: 221,  # MISSION_COUNT
    45: 232,  # MISSION_CLEAR_ALL
    47: 153,  # MISSION_ACK
    33: 104,  # GLOBAL_POSITION_INT
    69: 243,  # MANUAL_CONTROL
    74: 20,   # VFR_HUD
    76: 152,  # COMMAND_LONG
    77: 143,  # COMMAND_ACK
    147: 154, # BATTERY_STATUS
    253: 83,  # STATUSTEXT
    191: 92,  # MAG_CAL_PROGRESS
    192: 36,  # MAG_CAL_REPORT
    259: 92,  # CAMERA_INFORMATION (v2 only)
    269: 109, # VIDEO_STREAM_INFORMATION (v2 only)
}

# Camera Protocol constants
MAV_CMD_REQUEST_MESSAGE = 512
MAV_CMD_REQUEST_CAMERA_INFORMATION = 521          # legacy
MAV_CMD_REQUEST_VIDEO_STREAM_INFORMATION = 2504   # legacy
MAV_CMD_GIMLI_CONTROL = 31010
GIMLI_ACTION_CAMERA = 1
GIMLI_ACTION_QUALITY = 2
GIMLI_ACTION_DAYNIGHT = 3
GIMLI_ACTION_PARKTRONIC = 4
CAMERA_CAP_FLAGS_HAS_VIDEO_STREAM = 256
VIDEO_STREAM_TYPE_RTSP = 0
VIDEO_STREAM_STATUS_FLAGS_RUNNING = 1
VIDEO_STREAM_STATUS_FLAGS_THERMAL = 2
MAV_COMP_ID_CAMERA = 100   # отдельный компонент-камера

PARAMS: list[tuple[str, float, int]] = [
    ("SYSID_THISMAV", 1.0, 2),
    ("SYSID_MYGCS", 255.0, 2),
    ("FRAME_CLASS", 2.0, 2),
    ("FRAME_TYPE", 1.0, 2),
    ("COMPASS_USE", 1.0, 2),
    ("COMPASS_OFS_X", 0.0, 9),
    ("COMPASS_OFS_Y", 0.0, 9),
    ("COMPASS_OFS_Z", 0.0, 9),
    ("GPS_TYPE", 1.0, 2),
    ("BATT_MONITOR", 4.0, 2),
    ("BATT_LOW_VOLT", 11.1, 9),
    ("ARMING_CHECK", 0.0, 2),
    ("RCMAP_ROLL", 1.0, 2),
    ("RCMAP_PITCH", 2.0, 2),
    ("RCMAP_THROTTLE", 3.0, 2),
    ("RCMAP_YAW", 4.0, 2),
    ("RC0_MIN", 1000.0, 2),
    ("RC0_MAX", 2000.0, 2),
    ("RC0_TRIM", 1500.0, 2),
    ("RC1_MIN", 1000.0, 2),
    ("RC1_MAX", 2000.0, 2),
    ("RC1_TRIM", 1500.0, 2),
    ("RC2_MIN", 1000.0, 2),
    ("RC2_MAX", 2000.0, 2),
    ("RC2_TRIM", 1500.0, 2),
    ("RC3_MIN", 1000.0, 2),
    ("RC3_MAX", 2000.0, 2),
    ("RC3_TRIM", 1500.0, 2),
    ("RC4_MIN", 1000.0, 2),
    ("RC4_MAX", 2000.0, 2),
    ("RC4_TRIM", 1500.0, 2),
    ("FS_OPTIONS", 0.0, 2),
    ("FS_GCS_TIMEOUT", 5.0, 9),
    ("FS_GCS_ENABLE", 0.0, 2),
    ("FLTMODE1", 0.0, 2),
    ("FLTMODE2", 0.0, 2),
    ("FLTMODE3", 0.0, 2),
    ("FLTMODE4", 0.0, 2),
    ("FLTMODE5", 0.0, 2),
    ("FLTMODE6", 0.0, 2),
    ("COMPASS_DEV_ID", 1.0, 6),
    ("COMPASS_DEV_ID2", 0.0, 6),
    ("COMPASS_DEV_ID3", 0.0, 6),
    ("INS_ACCOFFS_X", 0.01, 9),
    ("INS_ACCOFFS_Y", -0.02, 9),
    ("INS_ACCOFFS_Z", 0.03, 9),
    ("INS_ACCSCAL_X", 1.001, 9),
    ("INS_ACCSCAL_Y", 0.999, 9),
    ("INS_ACCSCAL_Z", 1.002, 9),
    ("INS_ACC_ID", 1.0, 6),
    ("INS_ACC2_ID", 0.0, 6),
    ("INS_ACC3_ID", 0.0, 6),
    ("INS_USE", 1.0, 2),
    ("INS_USE2", 0.0, 2),
    ("INS_USE3", 0.0, 2),
    ("INS_GYR_ID", 1.0, 6),
    ("INS_GYR2_ID", 0.0, 6),
    ("INS_GYR3_ID", 0.0, 6),
    ("INS_GYROFFS_X", 0.0, 9),
    ("INS_GYROFFS_Y", 0.0, 9),
    ("INS_GYROFFS_Z", 0.0, 9),
    ("COMPASS_OFS2_X", 0.0, 9),
    ("COMPASS_OFS2_Y", 0.0, 9),
    ("COMPASS_OFS2_Z", 0.0, 9),
    ("COMPASS_OFS3_X", 0.0, 9),
    ("COMPASS_OFS3_Y", 0.0, 9),
    ("COMPASS_OFS3_Z", 0.0, 9),
    ("COMPASS_DEC", 0.0, 9),
    ("COMPASS_AUTODEC", 1.0, 2),
    ("AHRS_ORIENTATION", 0.0, 2),
    ("GIMLI_CAM", 1.0, 2),       # 1=front/cam1, 2=rear/cam2
    ("GIMLI_LIGHT", 0.0, 2),     # 0=auto, 1=on, 2=off
    ("GIMLI_DAYNIGHT", 0.0, 2),  # 0=auto, 1=day/color, 2=night/bw
    ("GIMLI_QUALITY", 1.0, 2),   # 0=sub, 1=main
    ("GIMLI_PARK", 0.0, 2),      # 0=parking overlay off, 1=on
]

GIMLI_PARAM_VALUES: dict[str, float] = {
    "GIMLI_CAM": 1.0,
    "GIMLI_LIGHT": 0.0,
    "GIMLI_DAYNIGHT": 0.0,
    "GIMLI_QUALITY": 1.0,
    "GIMLI_PARK": 0.0,
}

BUTTON_STATE: dict[str, Any] = {
    "last_buttons": 0,
    "armed_switch": None,
    "daynight_switch": None,
    "cam_switch": None,
    "quality_switch": None,
    "quality_low": False,
    "cam1_light": False,
    "cam2_light": False,
    "lights": False,
    "parktronic": False,
    "source": "mavlink",
}

BTN_ARM = 0
BTN_DAYNIGHT = 1
BTN_QUALITY_TOGGLE = 2
BTN_CAMERA_TOGGLE = 3
BTN_CAM1 = 4
BTN_CAM2 = 5
BTN_QUALITY_SUB = 8
BTN_QUALITY_MAIN = 9
BTN_CAM1_LIGHT = 6
BTN_CAM2_LIGHT = 7
BTN_LIGHT_TOGGLE = 10


class MavUdp:
    def __init__(self, connection: str, system_id: int, component_id: int, extra_connections: list[str] | None = None) -> None:
        self.system_id = system_id
        self.component_id = component_id
        self.seq = 0
        connections = [connection] + list(extra_connections or [])
        self.targets = [_parse_udpout(conn) for conn in connections if str(conn or "").strip()]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.setblocking(False)
        self.parser = MavParser()
        self.tcp_clients: list[socket.socket] = []
        self.tcp_server: socket.socket | None = None
        try:
            self.tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.tcp_server.bind(("0.0.0.0", 5760))
            self.tcp_server.listen(2)
            self.tcp_server.setblocking(False)
            print("MAVLink TCP server listening on 0.0.0.0:5760", flush=True)
        except OSError as exc:
            self.tcp_server = None
            print(f"MAVLink TCP server disabled: {exc}", flush=True)

    def send(self, msg_id: int, payload: bytes) -> None:
        packet = _pack_v2(msg_id, payload, self.seq, self.system_id, self.component_id)
        self.seq = (self.seq + 1) % 256
        for target in self.targets:
            try:
                self.sock.sendto(packet, target)
            except OSError as exc:
                print(f"mavlink send failed to {target}: {exc}", flush=True)
        self._send_tcp(packet)

    def send_v2(self, msg_id: int, payload: bytes, component_id: int | None = None) -> None:
        comp = self.component_id if component_id is None else component_id
        packet = _pack_v2(msg_id, payload, self.seq, self.system_id, comp)
        self.seq = (self.seq + 1) % 256
        for target in self.targets:
            try:
                self.sock.sendto(packet, target)
            except OSError as exc:
                print(f"mavlink send failed to {target}: {exc}", flush=True)
        self._send_tcp(packet)

    def recv(self) -> list[tuple[int, bytes]]:
        out: list[tuple[int, bytes]] = []
        self._accept_tcp()
        while True:
            try:
                data, _addr = self.sock.recvfrom(4096)
            except BlockingIOError:
                break
            out.extend(self.parser.feed(data))
        for client in list(self.tcp_clients):
            while True:
                try:
                    data = client.recv(4096)
                except BlockingIOError:
                    break
                except OSError:
                    self._close_tcp_client(client)
                    break
                if not data:
                    self._close_tcp_client(client)
                    break
                out.extend(self.parser.feed(data))
        return out

    def _accept_tcp(self) -> None:
        if not self.tcp_server:
            return
        while True:
            try:
                client, addr = self.tcp_server.accept()
            except BlockingIOError:
                return
            except OSError as exc:
                print(f"MAVLink TCP accept failed: {exc}", flush=True)
                return
            client.setblocking(False)
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.tcp_clients.append(client)
            print(f"MAVLink TCP client connected: {addr[0]}:{addr[1]}", flush=True)

    def _send_tcp(self, packet: bytes) -> None:
        for client in list(self.tcp_clients):
            try:
                client.sendall(packet)
            except OSError:
                self._close_tcp_client(client)

    def _close_tcp_client(self, client: socket.socket) -> None:
        try:
            self.tcp_clients.remove(client)
        except ValueError:
            pass
        try:
            client.close()
        except OSError:
            pass


class MavParser:
    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buf.extend(data)
        messages: list[tuple[int, bytes]] = []
        while self.buf:
            magic = self.buf[0]
            if magic not in (0xFE, 0xFD):
                del self.buf[0]
                continue
            needed = 8 if magic == 0xFE else 12
            if len(self.buf) < needed:
                break
            length = self.buf[1]
            if magic == 0xFE:
                frame_len = 6 + length + 2
            else:
                incompat = self.buf[2]
                signature_len = 13 if incompat & 1 else 0
                frame_len = 10 + length + 2 + signature_len
            if len(self.buf) < frame_len:
                break
            frame = bytes(self.buf[:frame_len])
            del self.buf[:frame_len]
            parsed = _parse_frame(frame)
            if parsed:
                messages.append(parsed)
        return messages


def main() -> None:
    watchdog = SystemdWatchdog()
    settings = load_settings()
    _sync_video_state_from_settings(settings)
    mav_cfg = settings.get("mavlink", {})
    if not mav_cfg.get("enabled", True):
        print("MAVLink disabled in settings")
        watchdog.ready()
        while True:
            watchdog.ping()
            time.sleep(10)

    system_id = int(mav_cfg.get("system_id", 1))
    component_id = int(mav_cfg.get("component_id", 1))
    connection = str(mav_cfg.get("connection", "udpout:127.0.0.1:14550"))
    extra_connections = _extra_connections(mav_cfg)
    link = MavUdp(connection, system_id, component_id, extra_connections)
    print(f"MAVLink bridge started: sysid={system_id} connection={connection} extra={extra_connections}")
    _write_control_state(source="mavlink", armed=False, buttons=0)

    last_heartbeat = 0.0
    last_status = 0.0
    last_nav = 0.0
    last_attitude = 0.0
    last_battery = 0.0
    last_cal = 0.0
    last_video_info = 0.0
    param_stream: dict[str, Any] | None = None
    last_command = 0.0
    armed = False
    watchdog.ready()

    while True:
        watchdog.ping()
        now = time.time()
        if now - last_heartbeat >= 1.0:
            _send_heartbeat(link, armed)
            last_heartbeat = now
        if now - last_status >= 2.0:
            _send_status(link)
            last_status = now
        if now - last_nav >= 1.0:
            _send_navigation(link)
            last_nav = now
        if now - last_attitude >= 0.2:
            _send_attitude(link)
            last_attitude = now
        if now - last_battery >= 1.0:
            _send_battery_status(link)
            last_battery = now
        if now - last_cal >= 1.0:
            _send_mag_cal_status(link)
            last_cal = now
        if now - last_video_info >= 5.0:
            _send_camera_information(link)
            _send_video_stream_information(link, 0)
            last_video_info = now
        if param_stream and now >= param_stream["next_at"]:
            _send_param_value(link, int(param_stream["index"]))
            param_stream["index"] += 1
            param_stream["next_at"] = now + 0.03
            if param_stream["index"] >= len(PARAMS):
                param_stream = None

        for msg_id, payload in link.recv():
            result = _handle_message(link, msg_id, payload, last_command, armed)
            last_command = result[0]
            armed = result[2]
            if result[1]:
                param_stream = {"index": 0, "next_at": now}

        if last_command and now - last_command > 0.7:
            _post_control({"cmd": "drive", "throttle": 0, "steering": 0})
            last_command = 0.0

        time.sleep(0.02)


def _send_heartbeat(link: MavUdp, armed: bool) -> None:
    base_mode = MAV_MODE_FLAG_MANUAL_INPUT_ENABLED
    if armed:
        base_mode |= MAV_MODE_FLAG_SAFETY_ARMED
    payload = struct.pack(
        "<IBBBBB",
        0,
        MAV_TYPE_GROUND_ROVER,
        MAV_AUTOPILOT_INVALID,
        base_mode,
        0,
        MAV_STATE_ACTIVE,
    )
    link.send(0, payload)
    # Heartbeat от камеры-компонента — чтобы QGC увидел её и запросил CAMERA_INFORMATION.
    cam_payload = struct.pack(
        "<IBBBBB",
        0,
        MAV_TYPE_CAMERA,
        MAV_AUTOPILOT_INVALID,
        0,
        0,
        MAV_STATE_ACTIVE,
    )
    link.send_v2(0, cam_payload, component_id=MAV_COMP_ID_CAMERA)


def _send_status(link: MavUdp) -> None:
    t = telemetry()
    power = t.get("power", {})
    voltage = power.get("battery_voltage")
    current = power.get("current_a")
    voltage_mv = 65535 if voltage is None else max(0, min(65534, int(float(voltage) * 1000)))
    current_ca = -1 if current is None else max(-32768, min(32767, int(float(current) * 100)))
    sensors = (
        MAV_SYS_STATUS_SENSOR_3D_GYRO
        | MAV_SYS_STATUS_SENSOR_3D_ACCEL
        | MAV_SYS_STATUS_SENSOR_3D_MAG
        | MAV_SYS_STATUS_SENSOR_GPS
        | MAV_SYS_STATUS_SENSOR_MOTOR_OUTPUTS
    )
    payload = struct.pack(
        "<IIIHHhBHHHHHH",
        sensors,
        sensors,
        sensors,
        500,
        voltage_mv,
        current_ca,
        255,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    link.send(1, payload)


def _send_battery_status(link: MavUdp) -> None:
    power = telemetry().get("power", {})
    voltage = power.get("battery_voltage")
    current = power.get("current_a")
    voltage_mv = 65535 if voltage is None else max(0, min(65534, int(float(voltage) * 1000)))
    current_ca = -1 if current is None else max(-32768, min(32767, int(float(current) * 100)))
    voltages = [voltage_mv] + [65535] * 9
    payload = struct.pack(
        "<iih10HhBBBb",
        -1,
        -1,
        32767,
        *voltages,
        current_ca,
        0,
        0,
        0,
        -1,
    )
    link.send(147, payload)


def _send_navigation(link: MavUdp) -> None:
    t = telemetry()
    nav = t.get("navigation", {})
    now_us = int(time.time() * 1_000_000)
    boot_ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
    enabled = bool(nav.get("gps_enabled", False))
    fix_type = _clamp_int(nav.get("fix_type", 0), 0, 6) if enabled else 0
    # Keep satellite visibility even when the position is marked unsafe.
    # This lets QGC show that the receiver sees GNSS while we still suppress
    # spoofed/untrusted coordinates by sending fix_type=0.
    satellites = _clamp_int(nav.get("satellites", 0), 0, 255)
    lat = _coord_to_int(nav.get("latitude"))
    lon = _coord_to_int(nav.get("longitude"))
    alt_mm = int(float(nav.get("altitude_m") or 0.0) * 1000)
    heading_deg = nav.get("heading_deg")
    heading_cd = 65535 if heading_deg in ("", None) else int(float(heading_deg) % 360 * 100)
    heading = 0 if heading_deg in ("", None) else int(float(heading_deg) % 360)
    gps_course = nav.get("gps_course_deg")
    gps_course_enabled = bool(nav.get("gps_course_enabled", False))
    gps_cog_cd = 65535
    if gps_course_enabled and gps_course not in ("", None):
        gps_cog_cd = int(float(gps_course) % 360 * 100)
    groundspeed = max(0.0, float(nav.get("groundspeed_m_s") or 0.0))
    groundspeed_cms = int(groundspeed * 100)

    gps_payload = struct.pack(
        "<QiiiHHHHBB",
        now_us,
        lat,
        lon,
        alt_mm,
        100 if enabled else 65535,
        100 if enabled else 65535,
        groundspeed_cms,
        gps_cog_cd,
        fix_type,
        satellites,
    )
    link.send(24, gps_payload)

    global_payload = struct.pack(
        "<IiiiihhhH",
        boot_ms,
        lat,
        lon,
        alt_mm,
        alt_mm,
        0,
        0,
        0,
        heading_cd,
    )
    link.send(33, global_payload)

    hud_payload = struct.pack(
        "<ffffhH",
        groundspeed,
        groundspeed,
        float(nav.get("altitude_m") or 0.0),
        0.0,
        heading,
        0,
    )
    link.send(74, hud_payload)


def _send_attitude(link: MavUdp) -> None:
    t = telemetry()
    nav = t.get("navigation", {})
    boot_ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
    heading_deg = nav.get("heading_deg")
    yaw = 0.0 if heading_deg in ("", None) else math.radians(float(heading_deg) % 360.0)
    attitude_payload = struct.pack(
        "<Iffffff",
        boot_ms,
        0.0,
        0.0,
        yaw,
        0.0,
        0.0,
        0.0,
    )
    link.send(30, attitude_payload)

    mag = nav.get("mag_raw") or [0, 0, 0]
    raw_imu_payload = struct.pack(
        "<Qhhhhhhhhh",
        int(time.time() * 1_000_000),
        0,
        0,
        1000,
        0,
        0,
        0,
        _clamp_int(mag[0], -32768, 32767),
        _clamp_int(mag[1], -32768, 32767),
        _clamp_int(mag[2], -32768, 32767),
    )
    link.send(27, raw_imu_payload)


def _handle_message(link: MavUdp, msg_id: int, payload: bytes, last_command: float, armed: bool) -> tuple[float, bool, bool]:
    if msg_id == 69 and len(payload) >= 11:
        x, y, z, r, _buttons, _target = struct.unpack_from("<hhhhHB", payload)
        _write_control_state(source="mavlink", manual_x=x, manual_y=y, manual_z=z, manual_r=r, buttons=_buttons)
        armed = _handle_manual_buttons(link, _buttons, armed)
        if not armed:
            _post_control({"cmd": "stop", "source": "mavlink"})
            return last_command, False, armed
        throttle, steering = _manual_drive_axes(x, y, z, r)
        _write_control_state(throttle=throttle, steering=steering)
        _post_control({"cmd": "drive", "source": "mavlink", "throttle": throttle, "steering": steering})
        return time.time(), False, armed
    if msg_id == 21 and len(payload) >= 2:
        return last_command, True, armed
    if msg_id == 20 and len(payload) >= 20:
        _handle_param_request_read(link, payload)
        return last_command, False, armed
    if msg_id == 23 and len(payload) >= 23:
        _handle_param_set(link, payload)
        return last_command, False, armed
    if msg_id == 43 and len(payload) >= 2:
        _send_mission_count(link, payload)
        return last_command, False, armed
    if msg_id == 45 and len(payload) >= 2:
        _send_mission_ack(link, payload)
        return last_command, False, armed
    if msg_id == 76 and len(payload) >= 30:
        fields = struct.unpack_from("<fffffffHBBB", payload.ljust(33, b"\x00"))
        command = fields[7]
        param1 = fields[0]
        param2 = fields[1]
        param5 = fields[4]
        if command in (MAV_CMD_COMPONENT_ARM_DISARM, MAV_CMD_DO_FLIGHTTERMINATION):
            armed = float(param1) != 0
            if not armed:
                _post_control({"cmd": "stop", "source": "mavlink"})
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            print(f"armed={armed}", flush=True)
            _write_control_state(source="qgroundcontrol", armed=armed)
            _send_status_text(link, f"ARM {'ON' if armed else 'OFF'}")
            return time.time(), False, armed
        if command == MAV_CMD_DO_START_MAG_CAL or (command == MAV_CMD_PREFLIGHT_CALIBRATION and float(param2) != 0):
            _write_sensor_command("start_mag_cal")
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            return time.time(), False, armed
        if command == MAV_CMD_DO_ACCEPT_MAG_CAL:
            _write_sensor_command("accept_mag_cal")
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            return time.time(), False, armed
        if command == MAV_CMD_DO_CANCEL_MAG_CAL:
            _write_sensor_command("cancel_mag_cal")
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            return time.time(), False, armed
        if command == MAV_CMD_PREFLIGHT_CALIBRATION and float(param5) != 0:
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            return time.time(), False, armed
        if command == MAV_CMD_REQUEST_MESSAGE:
            requested = int(param1)
            if requested == 259:
                _send_camera_information(link)
                _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
                return last_command, False, armed
            if requested == 269:
                _send_video_stream_information(link, int(param2))
                _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
                return last_command, False, armed
        if command == MAV_CMD_REQUEST_CAMERA_INFORMATION:
            _send_camera_information(link)
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            return last_command, False, armed
        if command == MAV_CMD_REQUEST_VIDEO_STREAM_INFORMATION:
            _send_video_stream_information(link, int(param1))
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED)
            return last_command, False, armed
        if command == MAV_CMD_GIMLI_CONTROL:
            ok, message = _handle_gimli_command(int(round(param1)), param2)
            _send_command_ack(link, command, MAV_RESULT_ACCEPTED if ok else MAV_RESULT_UNSUPPORTED)
            if message:
                _send_status_text(link, message)
            return last_command, False, armed
        _send_command_ack(link, command, MAV_RESULT_UNSUPPORTED)
    return last_command, False, armed


def _manual_drive_axes(x: int, y: int, z: int, r: int) -> tuple[float, float]:
    axes = {"x": x, "y": y, "z": z, "r": r}
    control = load_settings().get("mavlink", {}).get("control", {})
    throttle_axis = str(control.get("throttle_axis", "y") or "y").lower()
    steering_axis = str(control.get("steering_axis", "x") or "x").lower()
    throttle = _scale_axis(axes.get(throttle_axis, y))
    steering = _scale_axis(axes.get(steering_axis, x))
    throttle *= max(0.0, min(1.0, float(control.get("throttle_scale", 1.0) or 1.0)))
    steering *= max(0.0, min(1.0, float(control.get("steering_scale", 1.0) or 1.0)))
    if control.get("throttle_invert", False):
        throttle = -throttle
    if control.get("steering_invert", False):
        steering = -steering
    return throttle, steering


def _handle_param_request_read(link: MavUdp, payload: bytes) -> None:
    param_index = struct.unpack_from("<h", payload, 0)[0]
    target_system, target_component = struct.unpack_from("<BB", payload, 2)
    if target_system not in (0, link.system_id):
        return
    if target_component not in (0, link.component_id):
        return
    param_id = payload[4:20].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    if param_index >= 0:
        _send_param_value(link, param_index)
        return
    for index, (name, _value, _ptype) in enumerate(PARAMS):
        if name == param_id:
            _send_param_value(link, index)
            return


def _handle_param_set(link: MavUdp, payload: bytes) -> None:
    target_system, target_component = struct.unpack_from("<BB", payload, 0)
    if target_system not in (0, link.system_id):
        return
    if target_component not in (0, link.component_id):
        return
    value = struct.unpack_from("<f", payload, 2)[0]
    param_id = payload[6:22].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
    for index, (name, _value, _ptype) in enumerate(PARAMS):
        if name == param_id:
            if name.startswith("GIMLI_"):
                _handle_gimli_param_set(name, value)
            _send_param_value(link, index)
            return


def _send_param_value(link: MavUdp, index: int) -> None:
    if index < 0 or index >= len(PARAMS):
        return
    name, value, param_type = PARAMS[index]
    if name in GIMLI_PARAM_VALUES:
        value = GIMLI_PARAM_VALUES[name]
    param_id = name.encode("ascii")[:16].ljust(16, b"\x00")
    payload = struct.pack("<fHH16sB", float(value), len(PARAMS), index, param_id, param_type)
    link.send(22, payload)


def _handle_gimli_param_set(name: str, value: float) -> None:
    ivalue = int(round(value))
    if name == "GIMLI_CAM":
        ivalue = max(1, min(2, ivalue))
        _set_active_camera(ivalue)
        return
    if name == "GIMLI_QUALITY":
        ivalue = 1 if ivalue else 0
        profile = "high" if ivalue == 1 else "low"
        _set_video_profile(profile)
        return
    if name == "GIMLI_LIGHT":
        ivalue = max(0, min(2, ivalue))
        GIMLI_PARAM_VALUES[name] = float(ivalue)
        mode = {0: "auto", 1: "on", 2: "off"}[ivalue]
        _post_camera(_active_camera(), {"action": "light", "mode": mode, "level": 80})
        return
    if name == "GIMLI_DAYNIGHT":
        ivalue = max(0, min(2, ivalue))
        GIMLI_PARAM_VALUES[name] = float(ivalue)
        mode = {0: "auto", 1: "day", 2: "night"}[ivalue]
        _post_camera(_active_camera(), {"action": "daynight", "mode": mode})
        return
    if name == "GIMLI_PARK":
        _set_parktronic(bool(ivalue))


def _handle_gimli_command(action: int, value: float) -> tuple[bool, str]:
    ivalue = int(round(value))
    if action == GIMLI_ACTION_CAMERA:
        if ivalue not in (1, 2):
            ivalue = 2 if _current_active_cam_switch() == 1 else 1
        _set_active_camera(ivalue)
        return True, f"ACTIVE CAM {ivalue}"
    if action == GIMLI_ACTION_QUALITY:
        if ivalue not in (0, 1):
            ivalue = 0 if not _current_quality_low() else 1
        profile = "high" if ivalue == 1 else "low"
        _set_video_profile(profile)
        return True, f"STREAM {'MAIN' if profile == 'high' else 'SUB'}"
    if action == GIMLI_ACTION_DAYNIGHT:
        ivalue = max(0, min(2, ivalue))
        mode = {0: "auto", 1: "day", 2: "night"}[ivalue]
        GIMLI_PARAM_VALUES["GIMLI_DAYNIGHT"] = float(ivalue)
        BUTTON_STATE["daynight_switch"] = mode == "night"
        _post_camera("cam1", {"action": "daynight", "mode": mode})
        _post_camera("cam2", {"action": "daynight", "mode": mode})
        _write_control_state(daynight=mode)
        return True, f"CAM MODE {mode.upper()}"
    if action == GIMLI_ACTION_PARKTRONIC:
        enabled = not bool(BUTTON_STATE.get("parktronic", False)) if ivalue not in (0, 1) else bool(ivalue)
        _set_parktronic(enabled)
        return True, f"PARK {'ON' if enabled else 'OFF'}"
    return False, ""


def _send_mission_count(link: MavUdp, request_payload: bytes) -> None:
    target_system, target_component = struct.unpack_from("<BB", request_payload, 0)
    mission_type = request_payload[2] if len(request_payload) >= 3 else 0
    if target_system not in (0, link.system_id):
        return
    if target_component not in (0, link.component_id):
        return
    payload = struct.pack("<HBB", 0, 255, 0)
    if len(request_payload) >= 3:
        payload += struct.pack("<B", mission_type)
    link.send(44, payload)


def _send_mission_ack(link: MavUdp, request_payload: bytes) -> None:
    mission_type = request_payload[2] if len(request_payload) >= 3 else 0
    payload = struct.pack("<BBB", 255, 0, 0)
    if len(request_payload) >= 3:
        payload += struct.pack("<B", mission_type)
    link.send(47, payload)


def _send_command_ack(link: MavUdp, command: int, result: int) -> None:
    link.send(77, struct.pack("<HB", int(command), int(result)))


def _send_status_text(link: MavUdp, text: str, severity: int = 6) -> None:
    data = text.encode("utf-8", errors="ignore")[:50].ljust(50, b"\x00")
    link.send(253, struct.pack("<B50s", int(severity), data))


def _write_control_state(**patch: Any) -> None:
    state = dict(BUTTON_STATE)
    state.update(patch)
    state["updated_ts"] = time.time()
    buttons = int(state.get("last_buttons") or state.get("buttons") or 0)
    state["buttons"] = buttons
    state["buttons_hex"] = f"0x{buttons:04x}"
    try:
        CONTROL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONTROL_STATE_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(CONTROL_STATE_FILE)
    except Exception as exc:
        print(f"control state write failed: {exc}", flush=True)


def _handle_manual_buttons(link: MavUdp, buttons: int, armed: bool) -> bool:
    previous = int(BUTTON_STATE.get("last_buttons", 0))
    buttons = int(buttons or 0)
    if buttons != previous:
        print(f"manual buttons={buttons} changed={buttons ^ previous}", flush=True)
    BUTTON_STATE["last_buttons"] = buttons

    arm_switch = _button(buttons, BTN_ARM)
    if BUTTON_STATE.get("armed_switch") is None or BUTTON_STATE.get("armed_switch") != arm_switch:
        armed = arm_switch
        BUTTON_STATE["armed_switch"] = armed
        print(f"armed={bool(armed)} by switch {BTN_ARM + 1}", flush=True)
        _send_status_text(link, f"ARM {'ON' if armed else 'OFF'}")

    armed = bool(armed)
    if not armed:
        _post_control({"cmd": "stop"})

    daynight_on = _button(buttons, BTN_DAYNIGHT)
    if BUTTON_STATE.get("daynight_switch") is None or BUTTON_STATE.get("daynight_switch") != daynight_on:
        BUTTON_STATE["daynight_switch"] = daynight_on
        mode = "night" if daynight_on else "day"
        _post_camera("cam1", {"action": "daynight", "mode": mode})
        _post_camera("cam2", {"action": "daynight", "mode": mode})
        GIMLI_PARAM_VALUES["GIMLI_DAYNIGHT"] = 2.0 if mode == "night" else 1.0
        print(f"camera daynight={mode} by button {BTN_DAYNIGHT + 1}", flush=True)
        _send_status_text(link, f"CAM MODE {mode.upper()}")

    if _rising(previous, buttons, BTN_QUALITY_TOGGLE):
        next_low = not _current_quality_low()
        profile = "low" if next_low else "high"
        _set_video_profile(profile)
        preferred = "sub" if profile == "low" else "main"
        print(f"camera stream={preferred} by button {BTN_QUALITY_TOGGLE + 1}", flush=True)
        _send_status_text(link, f"STREAM {preferred.upper()}")

    if _rising(previous, buttons, BTN_LIGHT_TOGGLE):
        BUTTON_STATE["lights"] = not bool(BUTTON_STATE.get("lights", False))
        BUTTON_STATE["cam1_light"] = bool(BUTTON_STATE["lights"])
        BUTTON_STATE["cam2_light"] = bool(BUTTON_STATE["lights"])
        mode = "on" if BUTTON_STATE["lights"] else "off"
        _post_camera("cam1", {"action": "light", "mode": mode, "level": 80})
        _post_camera("cam2", {"action": "light", "mode": mode, "level": 80})
        GIMLI_PARAM_VALUES["GIMLI_LIGHT"] = 1.0 if mode == "on" else 2.0
        print(f"camera lights={mode} by button {BTN_LIGHT_TOGGLE + 1}", flush=True)
        _send_status_text(link, f"LIGHTS {mode.upper()}")

    if _rising(previous, buttons, BTN_CAMERA_TOGGLE):
        current_cam = _current_active_cam_switch()
        cam_switch = 2 if current_cam == 1 else 1
        _set_active_camera(cam_switch)
        print(f"active camera=cam{cam_switch} by button {BTN_CAMERA_TOGGLE + 1}", flush=True)
        _send_status_text(link, f"ACTIVE CAM {cam_switch}")

    cam_switch = 2 if _button(buttons, BTN_CAM2) else 1 if _button(buttons, BTN_CAM1) else 0
    if cam_switch and BUTTON_STATE.get("cam_switch") != cam_switch:
        _set_active_camera(cam_switch)
        print(f"active camera=cam{cam_switch}", flush=True)
        _send_status_text(link, f"ACTIVE CAM {cam_switch}")

    quality_switch = -1 if _button(buttons, BTN_QUALITY_SUB) else 1 if _button(buttons, BTN_QUALITY_MAIN) else 0
    if quality_switch and BUTTON_STATE.get("quality_switch") != quality_switch:
        profile = "high" if quality_switch > 0 else "low"
        _set_video_profile(profile)
        preferred = "main" if quality_switch > 0 else "sub"
        print(f"camera stream={preferred}", flush=True)
        _send_status_text(link, f"STREAM {preferred.upper()}")

    if _rising(previous, buttons, BTN_CAM1_LIGHT):
        BUTTON_STATE["cam1_light"] = not bool(BUTTON_STATE.get("cam1_light", False))
        BUTTON_STATE["lights"] = bool(BUTTON_STATE.get("cam1_light")) or bool(BUTTON_STATE.get("cam2_light"))
        mode = "on" if BUTTON_STATE["cam1_light"] else "off"
        _post_camera("cam1", {"action": "light", "mode": mode, "level": 80})
        print(f"cam1 light={mode}", flush=True)
        _send_status_text(link, f"CAM1 LIGHT {mode.upper()}")

    if _rising(previous, buttons, BTN_CAM2_LIGHT):
        BUTTON_STATE["cam2_light"] = not bool(BUTTON_STATE.get("cam2_light", False))
        BUTTON_STATE["lights"] = bool(BUTTON_STATE.get("cam1_light")) or bool(BUTTON_STATE.get("cam2_light"))
        mode = "on" if BUTTON_STATE["cam2_light"] else "off"
        _post_camera("cam2", {"action": "light", "mode": mode, "level": 80})
        print(f"cam2 light={mode}", flush=True)
        _send_status_text(link, f"CAM2 LIGHT {mode.upper()}")

    _write_control_state(
        source="mavlink",
        last_buttons=buttons,
        buttons=buttons,
        armed=armed,
        active_camera="cam2" if BUTTON_STATE.get("cam_switch") == 2 else "cam1",
        daynight="night" if BUTTON_STATE.get("daynight_switch") else "day",
        quality="sub" if BUTTON_STATE.get("quality_switch") == -1 else "main",
        quality_low=bool(BUTTON_STATE.get("quality_low")),
        parktronic=bool(BUTTON_STATE.get("parktronic")),
        lights=bool(BUTTON_STATE.get("lights")),
        cam1_light=bool(BUTTON_STATE.get("cam1_light")),
        cam2_light=bool(BUTTON_STATE.get("cam2_light")),
    )
    return armed


def _button(buttons: int, bit: int) -> bool:
    return bool(buttons & (1 << bit))


def _rising(previous: int, current: int, bit: int) -> bool:
    mask = 1 << bit
    return not (previous & mask) and bool(current & mask)


def _get_video_host() -> str:
    """Хост для RTSP URL. Берём из settings.video.stream_host, иначе FQDN."""
    global _FQDN_CACHE, _FQDN_CACHE_TS
    try:
        settings = load_settings()
    except Exception:
        settings = {}
    host = str(settings.get("video", {}).get("stream_host", "")).strip()
    if host:
        return host
    now = time.monotonic()
    if _FQDN_CACHE is None or now - _FQDN_CACHE_TS >= _FQDN_CACHE_TTL_S:
        try:
            _FQDN_CACHE = socket.getfqdn() or "localhost"
        except Exception:
            _FQDN_CACHE = "localhost"
        _FQDN_CACHE_TS = now
    return _FQDN_CACHE


def _get_video_port() -> int:
    try:
        return int(load_settings().get("video", {}).get("stream_port", 8554))
    except Exception:
        return 8554


def _build_stream_uri(stream_name: str) -> str:
    return f"rtsp://{_get_video_host()}:{_get_video_port()}/{stream_name}"


def _send_camera_information(link: MavUdp) -> None:
    """CAMERA_INFORMATION (id 259). MAVLink v2. Wire-order fields sorted by size."""
    boot_ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
    vendor = b"Gimli".ljust(32, b"\x00")
    model = b"Rover".ljust(32, b"\x00")
    cam_def_uri = b"".ljust(140, b"\x00")
    payload = struct.pack(
        "<IIfffIHHH32s32sB140s",
        boot_ms,
        0,
        0.0,
        0.0,
        0.0,
        CAMERA_CAP_FLAGS_HAS_VIDEO_STREAM,
        0, 0,
        0,
        vendor, model,
        0,
        cam_def_uri,
    )
    link.send_v2(259, payload, component_id=MAV_COMP_ID_CAMERA)


def _send_video_stream_information(link: MavUdp, stream_id: int = 0) -> None:
    """VIDEO_STREAM_INFORMATION (id 269). Если stream_id==0, шлём оба потока."""
    settings = load_settings()
    profile = settings.get("network", {}).get("profile", "balanced")
    if profile == "low":
        framerate, bitrate, width, height = 10.0, 500_000, 640, 360
    elif profile == "high":
        framerate, bitrate, width, height = 25.0, 4_000_000, 1920, 1080
    else:
        framerate, bitrate, width, height = 12.0, 1_200_000, 1280, 720
    streams = [("qgc", "qgc", False), ("cam2", "rear", True), ("cam1", "front", False)]
    if stream_id == 0:
        targets = [(i + 1, streams[i]) for i in range(len(streams))]
    elif 1 <= stream_id <= len(streams):
        targets = [(stream_id, streams[stream_id - 1])]
    else:
        return
    count = len(streams)
    for sid, (slug, label, is_secondary) in targets:
        uri = _build_stream_uri(slug).encode("ascii", errors="ignore")[:159]
        name = label.encode("ascii", errors="ignore")[:31]
        flags = VIDEO_STREAM_STATUS_FLAGS_RUNNING
        if is_secondary:
            flags |= VIDEO_STREAM_STATUS_FLAGS_THERMAL
        payload = struct.pack(
            "<fIHHHHHBBB32s160s",
            framerate,
            bitrate,
            flags,
            width, height,
            0,
            90,
            sid,
            count,
            VIDEO_STREAM_TYPE_RTSP,
            name.ljust(32, b"\x00"),
            uri.ljust(160, b"\x00"),
        )
        link.send_v2(269, payload, component_id=MAV_COMP_ID_CAMERA)


def _send_mag_cal_status(link: MavUdp) -> None:
    cal = _load_sensor_calibration()
    status = str(cal.get("status", "idle"))
    if status == "running":
        progress = _clamp_int(cal.get("progress", 0), 0, 99)
        mask = _completion_mask(progress)
        payload = struct.pack(
            "<fffBBBBB10B",
            0.0,
            0.0,
            0.0,
            0,
            1,
            MAG_CAL_RUNNING_STEP_ONE,
            1,
            progress,
            *mask,
        )
        link.send(191, payload)
    elif status == "success" and cal.get("progress") == 100:
        offset = cal.get("offset") or [0.0, 0.0, 0.0]
        scale = cal.get("scale") or [1.0, 1.0, 1.0]
        fitness = float(cal.get("fitness", 0.0) or 0.0)
        payload = struct.pack(
            "<ffffffffffBBBB",
            fitness,
            float(offset[0]),
            float(offset[1]),
            float(offset[2]),
            float(scale[0]),
            float(scale[1]),
            float(scale[2]),
            0.0,
            0.0,
            0.0,
            0,
            1,
            MAG_CAL_SUCCESS,
            1,
        )
        link.send(192, payload)


def _load_sensor_calibration() -> dict[str, Any]:
    try:
        if not SENSOR_STATE_FILE.exists():
            return {}
        data = json.loads(SENSOR_STATE_FILE.read_text(encoding="utf-8"))
        return data.get("compass_calibration", {})
    except Exception:
        return {}


def _write_sensor_command(command: str) -> None:
    try:
        write_sensor_command(command)
        print(f"sensor command: {command}", flush=True)
    except Exception as exc:
        print(f"sensor command failed: {exc}", flush=True)


def _completion_mask(progress: int) -> list[int]:
    filled = max(0, min(80, int(progress * 80 / 100)))
    bits = []
    for byte_idx in range(10):
        value = 0
        for bit in range(8):
            if byte_idx * 8 + bit < filled:
                value |= 1 << bit
        bits.append(value)
    return bits


def _pack_v1(msg_id: int, payload: bytes, seq: int, system_id: int, component_id: int) -> bytes:
    header = struct.pack("<BBBBB", len(payload), seq, system_id, component_id, msg_id)
    crc = _x25_crc(header + payload + bytes([CRC_EXTRA[msg_id]]))
    return b"\xFE" + header + payload + struct.pack("<H", crc)


def _pack_v2(msg_id: int, payload: bytes, seq: int, system_id: int, component_id: int) -> bytes:
    """MAVLink v2 frame (no signing). Применяет zero-truncation, как требует spec."""
    pl = bytes(payload).rstrip(b"\x00") or b"\x00"
    header = struct.pack(
        "<BBBBBBBBB",
        len(pl), 0, 0, seq, system_id, component_id,
        msg_id & 0xFF, (msg_id >> 8) & 0xFF, (msg_id >> 16) & 0xFF,
    )
    crc_extra = CRC_EXTRA.get(msg_id, 0)
    crc = _x25_crc(header + pl + bytes([crc_extra]))
    return b"\xFD" + header + pl + struct.pack("<H", crc)


def _parse_frame(frame: bytes) -> tuple[int, bytes] | None:
    magic = frame[0]
    length = frame[1]
    if magic == 0xFE:
        msg_id = frame[5]
        payload = frame[6:6 + length]
        checksum = struct.unpack_from("<H", frame, 6 + length)[0]
        crc_extra = CRC_EXTRA.get(msg_id)
        if crc_extra is None:
            return msg_id, payload
        crc = _x25_crc(frame[1:6] + payload + bytes([crc_extra]))
        return (msg_id, payload) if crc == checksum else None
    msg_id = frame[7] | (frame[8] << 8) | (frame[9] << 16)
    payload = frame[10:10 + length]
    checksum = struct.unpack_from("<H", frame, 10 + length)[0]
    crc_extra = CRC_EXTRA.get(msg_id)
    if crc_extra is None:
        return msg_id, payload
    crc = _x25_crc(frame[1:10] + payload + bytes([crc_extra]))
    return (msg_id, payload) if crc == checksum else None


def _x25_crc(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def _parse_udpout(connection: str) -> tuple[str, int]:
    if not connection.startswith("udpout:"):
        raise ValueError("only udpout:HOST:PORT is supported for the MVP bridge")
    rest = connection[len("udpout:"):]
    host, port = rest.rsplit(":", 1)
    return host, int(port)


def _extra_connections(mav_cfg: dict[str, Any]) -> list[str]:
    raw = mav_cfg.get("extra_connections", [])
    if isinstance(raw, str):
        items = raw.replace("\n", ",").split(",")
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    out: list[str] = []
    for item in items:
        conn = str(item or "").strip()
        if not conn:
            continue
        if ":" in conn and not conn.startswith("udpout:"):
            conn = "udpout:" + conn
        elif not conn.startswith("udpout:"):
            conn = f"udpout:{conn}:14550"
        out.append(conn)
    return out


def _scale_axis(value: int) -> float:
    return max(-1.0, min(1.0, float(value) / 1000.0))


def _coord_to_int(value: Any) -> int:
    if value in ("", None):
        return 0
    return int(float(value) * 10_000_000)


def _clamp_int(value: Any, low: int, high: int) -> int:
    return max(low, min(high, int(value or 0)))


def _post_control(payload: dict[str, Any]) -> None:
    global _CONTROL_SEQ
    _CONTROL_SEQ += 1
    payload = dict(payload)
    payload["seq"] = _CONTROL_SEQ
    payload["updated_ts"] = time.time()
    try:
        write_runtime_json(CONTROL_COMMAND_FILE, payload)
    except Exception as exc:
        print(f"control command write failed: {exc}", flush=True)


def _post_settings(payload: dict[str, Any]) -> None:
    _post_json(BACKEND_SETTINGS_URL, payload, timeout=1.5)


def _post_camera(camera: str, payload: dict[str, Any]) -> None:
    _post_json(BACKEND_CAMERA_URL.format(camera=camera), payload, timeout=2.5)


def _set_active_camera(cam_switch: int) -> None:
    cam_switch = 2 if int(cam_switch) == 2 else 1
    BUTTON_STATE["cam_switch"] = cam_switch
    GIMLI_PARAM_VALUES["GIMLI_CAM"] = float(cam_switch)
    _post_settings({"video": {"active_stream": "cam2" if cam_switch == 2 else "cam1"}})


def _set_video_profile(profile: str) -> None:
    preferred = "sub" if str(profile).lower() == "low" else "main"
    BUTTON_STATE["quality_low"] = preferred == "sub"
    BUTTON_STATE["quality_switch"] = -1 if preferred == "sub" else 1
    GIMLI_PARAM_VALUES["GIMLI_QUALITY"] = 0.0 if preferred == "sub" else 1.0
    _post_settings({"cameras": {"cam1": {"preferred": preferred}, "cam2": {"preferred": preferred}}})


def _set_parktronic(enabled: bool) -> None:
    BUTTON_STATE["parktronic"] = bool(enabled)
    GIMLI_PARAM_VALUES["GIMLI_PARK"] = 1.0 if enabled else 0.0
    _write_control_state(parktronic=bool(enabled))


def _sync_video_state_from_settings(settings: dict[str, Any] | None = None) -> None:
    settings = settings or load_settings()
    cam_switch = 2 if settings.get("video", {}).get("active_stream") == "cam2" else 1
    BUTTON_STATE["cam_switch"] = cam_switch
    GIMLI_PARAM_VALUES["GIMLI_CAM"] = float(cam_switch)
    cam1 = settings.get("cameras", {}).get("cam1", {})
    low = str(cam1.get("preferred", "main") or "main").lower() == "sub"
    BUTTON_STATE["quality_low"] = low
    BUTTON_STATE["quality_switch"] = -1 if low else 1
    GIMLI_PARAM_VALUES["GIMLI_QUALITY"] = 0.0 if low else 1.0


def _current_active_cam_switch() -> int:
    try:
        active = load_settings().get("video", {}).get("active_stream", "cam1")
        return 2 if active == "cam2" else 1
    except Exception:
        return 2 if int(GIMLI_PARAM_VALUES.get("GIMLI_CAM", 1.0) or 1) == 2 else 1


def _current_quality_low() -> bool:
    try:
        settings = load_settings()
        cam1 = settings.get("cameras", {}).get("cam1", {})
        return str(cam1.get("preferred", "main") or "main").lower() == "sub"
    except Exception:
        return bool(BUTTON_STATE.get("quality_low", False))


def _post_json(url: str, payload: dict[str, Any], timeout: float = 0.5) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
    except (urllib.error.URLError, TimeoutError):
        pass


def _active_camera() -> str:
    return "cam2" if int(GIMLI_PARAM_VALUES.get("GIMLI_CAM", 1.0)) == 2 else "cam1"


if __name__ == "__main__":
    main()
