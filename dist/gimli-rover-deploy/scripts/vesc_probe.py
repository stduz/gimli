from __future__ import annotations

import argparse
import os
import select
import termios
import time


COMM_FW_VERSION = 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=1.5)
    args = parser.parse_args()

    fd = open_serial(args.port, args.baud)
    try:
        os.write(fd, packet(bytes([COMM_FW_VERSION])))
        deadline = time.time() + args.timeout
        data = bytearray()
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                chunk = os.read(fd, 4096)
                if chunk:
                    data.extend(chunk)
        print(f"read {len(data)} bytes: {data.hex()}")
        parsed = parse_packet(bytes(data))
        if parsed:
            print(f"payload {len(parsed)} bytes: {parsed.hex()}")
            if parsed[0] == COMM_FW_VERSION and len(parsed) >= 3:
                print(f"fw version: {parsed[1]}.{parsed[2]}")
        else:
            print("no valid VESC packet")
    finally:
        os.close(fd)


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
