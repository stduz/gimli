from __future__ import annotations

import argparse
import os
import select
import termios
import time


COMM_GET_VALUES = 4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=1.5)
    args = parser.parse_args()

    fd = open_serial(args.port, args.baud)
    try:
        os.write(fd, packet(bytes([COMM_GET_VALUES])))
        data = read_all(fd, args.timeout)
        print(f"read {len(data)} bytes: {data.hex()}")
        payload = parse_packet(data)
        if not payload:
            print("no valid VESC packet")
            return
        print(f"payload {len(payload)} bytes: {payload.hex()}")
        if payload[0] != COMM_GET_VALUES:
            print(f"unexpected command: {payload[0]}")
            return
        values = parse_values(payload[1:])
        for key, value in values.items():
            print(f"{key}: {value}")
    finally:
        os.close(fd)


def parse_values(data: bytes) -> dict[str, float | int]:
    o = 0

    def i16(scale: float = 1.0) -> float:
        nonlocal o
        v = int.from_bytes(data[o : o + 2], "big", signed=True) / scale
        o += 2
        return v

    def i32(scale: float = 1.0) -> float:
        nonlocal o
        v = int.from_bytes(data[o : o + 4], "big", signed=True) / scale
        o += 4
        return v

    def u8() -> int:
        nonlocal o
        v = data[o] if o < len(data) else -1
        o += 1
        return v

    vals: dict[str, float | int] = {}
    try:
        vals["temp_mos_c"] = i16(10)
        vals["temp_motor_c"] = i16(10)
        vals["current_motor_a"] = i32(100)
        vals["current_in_a"] = i32(100)
        vals["id_a"] = i32(100)
        vals["iq_a"] = i32(100)
        vals["duty"] = i16(1000)
        vals["rpm"] = i32(1)
        vals["voltage_in_v"] = i16(10)
        vals["amp_hours"] = i32(10000)
        vals["amp_hours_charged"] = i32(10000)
        vals["watt_hours"] = i32(10000)
        vals["watt_hours_charged"] = i32(10000)
        vals["tachometer"] = i32(1)
        vals["tachometer_abs"] = i32(1)
        vals["fault_code"] = u8()
    except Exception as exc:
        vals["parse_error"] = str(exc)
        vals["parsed_until"] = o
        vals["payload_len"] = len(data)
    return vals


def open_serial(port: str, baud: int) -> int:
    baud_const = {
        9600: termios.B9600,
        19200: termios.B19200,
        38400: termios.B38400,
        57600: termios.B57600,
        115200: termios.B115200,
        230400: termios.B230400,
        460800: getattr(termios, "B460800", termios.B115200),
    }.get(baud, termios.B115200)
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[4] = baud_const
    attrs[5] = baud_const
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def read_all(fd: int, timeout: float) -> bytes:
    deadline = time.time() + timeout
    data = bytearray()
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            chunk = os.read(fd, 4096)
            if chunk:
                data.extend(chunk)
    return bytes(data)


def packet(payload: bytes) -> bytes:
    crc = crc16_xmodem(payload)
    return bytes([2, len(payload)]) + payload + crc.to_bytes(2, "big") + bytes([3])


def parse_packet(data: bytes) -> bytes | None:
    for i, b in enumerate(data):
        if b != 2 or i + 5 > len(data):
            continue
        size = data[i + 1]
        end = i + 2 + size + 2
        if end >= len(data) or data[end] != 3:
            continue
        payload = data[i + 2 : i + 2 + size]
        crc = int.from_bytes(data[i + 2 + size : i + 2 + size + 2], "big")
        if crc == crc16_xmodem(payload):
            return payload
    return None


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


if __name__ == "__main__":
    main()
