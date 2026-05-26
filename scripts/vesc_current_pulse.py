from __future__ import annotations

import argparse
import time

from backend.vesc import _VescSerial


COMM_SET_CURRENT = 6
COMM_SET_CURRENT_BRAKE = 7


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--can-id", type=int, default=None)
    parser.add_argument("--amps", type=float, default=1.0)
    parser.add_argument("--seconds", type=float, default=1.0)
    args = parser.parse_args()
    set_current.can_id = args.can_id  # type: ignore[attr-defined]

    vesc = _VescSerial(args.port, 115200)
    try:
        target = f"CAN {args.can_id}" if args.can_id is not None else "local"
        print(f"current pulse start {args.port} {target} amps={args.amps} seconds={args.seconds}", flush=True)
        deadline = time.time() + max(0.1, args.seconds)
        while time.time() < deadline:
            set_current(vesc, args.amps)
            time.sleep(0.05)
        set_current(vesc, 0.0)
        set_brake(vesc, 0.0)
        print("current pulse stop", flush=True)
    finally:
        vesc.close()


def set_current(vesc: _VescSerial, amps: float) -> None:
    current_i = int(amps * 1000)
    payload = bytes([COMM_SET_CURRENT]) + current_i.to_bytes(4, "big", signed=True)
    if getattr(set_current, "can_id", None) is None:
        vesc.send_payload(payload)
    else:
        vesc.forward_can(getattr(set_current, "can_id"), payload)


def set_brake(vesc: _VescSerial, amps: float) -> None:
    current_i = int(amps * 1000)
    payload = bytes([COMM_SET_CURRENT_BRAKE]) + current_i.to_bytes(4, "big", signed=True)
    if getattr(set_current, "can_id", None) is None:
        vesc.send_payload(payload)
    else:
        vesc.forward_can(getattr(set_current, "can_id"), payload)


def crc_payload(payload: bytes) -> bytes:
    crc = 0
    for b in payload:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc.to_bytes(2, "big")


if __name__ == "__main__":
    main()
