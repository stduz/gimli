from __future__ import annotations

import argparse
import time

from backend.vesc import VescDrive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--left-can-id", type=int, default=68)
    parser.add_argument("--right-can-id", type=int, default=None)
    parser.add_argument("--amps", type=float, default=15.0)
    parser.add_argument("--mode", choices=["current", "duty", "rpm"], default="current")
    parser.add_argument("--duty", type=float, default=0.08)
    parser.add_argument("--rpm", type=float, default=1200)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--throttle", type=float, default=1.0)
    parser.add_argument("--steering", type=float, default=0.0)
    args = parser.parse_args()

    drive = VescDrive(
        {
            "port": args.port,
            "left_can_id": args.left_can_id,
            "right_can_id": args.right_can_id,
            "control_mode": args.mode,
            "max_current_a": args.amps,
            "max_duty": args.duty,
            "max_rpm": args.rpm,
        }
    )
    try:
        print(
            f"drive test start mode={args.mode} amps={args.amps} duty={args.duty} rpm={args.rpm} seconds={args.seconds} "
            f"throttle={args.throttle} steering={args.steering}",
            flush=True,
        )
        deadline = time.time() + max(0.1, args.seconds)
        while time.time() < deadline:
            drive.drive(args.throttle, args.steering)
            time.sleep(0.05)
        drive.stop()
        print("drive test stop", flush=True)
    finally:
        drive.shutdown()


if __name__ == "__main__":
    main()
