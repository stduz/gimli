from __future__ import annotations

import argparse
import time

from backend.vesc import VescDrive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--duty", type=float, default=0.03)
    parser.add_argument("--seconds", type=float, default=1.0)
    args = parser.parse_args()

    drive = VescDrive(
        {
            "left_port": args.port,
            "right_port": "",
            "baud": 115200,
            "max_duty": abs(args.duty),
        }
    )
    try:
        print(f"pulse start {args.port} duty={args.duty} seconds={args.seconds}", flush=True)
        drive.drive(1.0 if args.duty >= 0 else -1.0, 0.0)
        time.sleep(max(0.1, args.seconds))
        drive.stop()
        print("pulse stop", flush=True)
    finally:
        drive.shutdown()


if __name__ == "__main__":
    main()
