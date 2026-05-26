"""VESC UART motor driver for differential rover drive."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any


COMM_SET_DUTY = 5
COMM_SET_CURRENT = 6
COMM_SET_RPM = 8
COMM_FORWARD_CAN = 34
WATCHDOG_TIMEOUT_S = 0.5


@dataclass
class MotorState:
    left: float = 0.0
    right: float = 0.0
    throttle: float = 0.0
    steering: float = 0.0
    last_update_ts: float = 0.0


class VescDrive:
    def __init__(self, config: dict[str, Any] | None = None, watchdog_timeout_s: float = WATCHDOG_TIMEOUT_S) -> None:
        cfg = dict(config or {})
        self._watchdog_timeout_s = watchdog_timeout_s
        self._max_duty = _clamp(float(cfg.get("max_duty", 0.12) or 0.12), 0.0, 1.0)
        self._current_mode = str(cfg.get("control_mode", "current") or "current").strip().lower()
        self._max_current_a = max(0.0, float(cfg.get("max_current_a", 20.0) or 20.0))
        self._max_rpm = max(0.0, float(cfg.get("max_rpm", 3000.0) or 3000.0))
        self._left_invert = bool(cfg.get("left_invert", False))
        self._right_invert = bool(cfg.get("right_invert", False))
        self._serial = _VescSerial(str(cfg.get("port") or cfg.get("left_port") or ""), int(cfg.get("baud", 115200) or 115200))
        self._left_can_id = _optional_int(cfg.get("left_can_id"))
        self._right_can_id = _optional_int(cfg.get("right_can_id"))
        self.state = MotorState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    def drive(self, throttle: float, steering: float) -> MotorState:
        throttle = _clamp(throttle, -1.0, 1.0)
        steering = _clamp(steering, -1.0, 1.0)
        left = throttle + steering
        right = throttle - steering
        peak = max(abs(left), abs(right), 1.0)
        left /= peak
        right /= peak
        with self._lock:
            self._apply(left, right)
            self.state = MotorState(
                left=left,
                right=right,
                throttle=throttle,
                steering=steering,
                last_update_ts=time.time(),
            )
            return self.state

    def stop(self) -> None:
        with self._lock:
            self._apply(0.0, 0.0)
            self.state = MotorState(last_update_ts=time.time())

    def shutdown(self) -> None:
        self._stop_event.set()
        self.stop()
        self._serial.close()

    def _apply(self, left: float, right: float) -> None:
        if self._left_invert:
            left = -left
        if self._right_invert:
            right = -right
        self._send_side(self._left_can_id, left)
        self._send_side(self._right_can_id, right)

    def _send_side(self, can_id: int | None, value: float) -> None:
        if self._current_mode == "duty":
            payload = _set_duty_payload(value * self._max_duty)
        elif self._current_mode == "rpm":
            rpm = int(value * max(0.0, float(getattr(self, "_max_rpm", 3000.0))))
            payload = _set_rpm_payload(rpm)
        else:
            payload = _set_current_payload(value * self._max_current_a)
        if can_id is None:
            self._serial.send_payload(payload)
        else:
            self._serial.forward_can(can_id, payload)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.1)
            with self._lock:
                age = time.time() - self.state.last_update_ts
                moving = self.state.left != 0.0 or self.state.right != 0.0
            if moving and age > self._watchdog_timeout_s:
                self.stop()


class _VescSerial:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port.strip()
        self.baud = baud
        self._fd: int | None = None
        self._lock = threading.Lock()

    def set_duty(self, duty: float) -> None:
        if not self.port:
            return
        self.send_payload(_set_duty_payload(duty))

    def set_current(self, amps: float) -> None:
        if not self.port:
            return
        self.send_payload(_set_current_payload(amps))

    def forward_can(self, can_id: int, payload: bytes) -> None:
        if not self.port:
            return
        self.send_payload(bytes([COMM_FORWARD_CAN, can_id & 0xFF]) + payload)

    def send_payload(self, payload: bytes) -> None:
        if not self.port:
            return
        self._write(_packet(payload))

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def _write(self, packet: bytes) -> None:
        with self._lock:
            fd = self._open()
            os.write(fd, packet)

    def _open(self) -> int:
        if self._fd is not None:
            return self._fd
        import termios

        baud_const = {
            9600: termios.B9600,
            19200: termios.B19200,
            38400: termios.B38400,
            57600: termios.B57600,
            115200: termios.B115200,
            230400: termios.B230400,
        }.get(self.baud, termios.B115200)
        fd = os.open(self.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = baud_const
        attrs[5] = baud_const
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        self._fd = fd
        return fd


def _packet(payload: bytes) -> bytes:
    if len(payload) > 255:
        raise ValueError("VESC short packet supports payloads up to 255 bytes")
    crc = _crc16_xmodem(payload)
    return bytes([2, len(payload)]) + payload + crc.to_bytes(2, "big") + bytes([3])


def _set_duty_payload(duty: float) -> bytes:
    duty_i = int(_clamp(duty, -1.0, 1.0) * 100000)
    return bytes([COMM_SET_DUTY]) + duty_i.to_bytes(4, "big", signed=True)


def _set_current_payload(amps: float) -> bytes:
    current_i = int(amps * 1000)
    return bytes([COMM_SET_CURRENT]) + current_i.to_bytes(4, "big", signed=True)


def _set_rpm_payload(rpm: int) -> bytes:
    return bytes([COMM_SET_RPM]) + int(rpm).to_bytes(4, "big", signed=True)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
