"""Read two PWM receiver channels and forward local RC control to the backend."""

from __future__ import annotations

import json
import os
import select
import termios
import time
from dataclasses import dataclass
from typing import Any

from backend.settings import CONTROL_COMMAND_FILE, RC_STATE_FILE, load_settings, write_runtime_json
from backend.watchdog import SystemdWatchdog


LOCAL_RC_SOURCE = "local_rc"
_CONTROL_SEQ = 0
LOG_ACTIVE_THRESHOLD = 0.03
LOG_DELTA_THRESHOLD = 0.10
LOG_KEEPALIVE_S = 10.0
STATE_KEEPALIVE_S = 2.0
STATE_DELTA_THRESHOLD = 0.02
CONTROL_ACTIVE_THRESHOLD = 0.04
_LAST_RC_STATE_SIG: str | None = None
_LAST_RC_STATE_WRITE = 0.0


@dataclass
class Channel:
    gpio: int
    rise_tick: int | None = None
    pulse_us: float | None = None
    updated_at: float = 0.0


def main() -> None:
    watchdog = SystemdWatchdog()
    cfg = load_settings().get("rc_input", {})
    if not cfg.get("enabled", True):
        print("RC input disabled in settings", flush=True)
        watchdog.ready()
        while True:
            watchdog.ping()
            time.sleep(60)
    if str(cfg.get("mode", "serial")) == "serial":
        run_serial_loop(cfg, watchdog)
        return

    try:
        import lgpio  # type: ignore
    except Exception as exc:
        raise SystemExit(f"python3-lgpio is required for RC input: {exc}")

    steering = Channel(int(cfg.get("steering_gpio", 5)))
    throttle = Channel(int(cfg.get("throttle_gpio", 6)))
    channels = {steering.gpio: steering, throttle.gpio: throttle}

    chip = lgpio.gpiochip_open(0)
    callbacks = []

    def edge(_chip: int, gpio: int, level: int, tick: int) -> None:
        ch = channels.get(gpio)
        if ch is None:
            return
        if level == 1:
            ch.rise_tick = tick
            return
        if level == 0 and ch.rise_tick is not None:
            width = tick - ch.rise_tick
            if width < 0:
                return
            width_us = width / 1000.0 if width > 20_000 else float(width)
            if 750.0 <= width_us <= 2250.0:
                ch.pulse_us = width_us
                ch.updated_at = time.monotonic()

    try:
        for gpio in channels:
            lgpio.gpio_claim_input(chip, gpio)
            callbacks.append(lgpio.callback(chip, gpio, lgpio.BOTH_EDGES, edge))

        print(
            f"RC input started: steering GPIO{steering.gpio}, throttle GPIO{throttle.gpio}",
            flush=True,
        )
        watchdog.ready()
        run_loop(steering, throttle, cfg, watchdog)
    finally:
        for cb in callbacks:
            try:
                cb.cancel()
            except Exception:
                pass
        lgpio.gpiochip_close(chip)


def run_loop(steering: Channel, throttle: Channel, cfg: dict[str, Any], watchdog: SystemdWatchdog) -> None:
    send_interval = 1.0 / max(1, min(50, int(cfg.get("send_hz", 25) or 25)))
    timeout = max(0.05, float(cfg.get("signal_timeout_s", 0.35) or 0.35))
    stopped = True
    last_log = 0.0
    last_log_thr: float | None = None
    last_log_steer: float | None = None

    while True:
        watchdog.ping()
        now = time.monotonic()
        valid = (
            steering.pulse_us is not None
            and throttle.pulse_us is not None
            and now - steering.updated_at <= timeout
            and now - throttle.updated_at <= timeout
        )
        if valid:
            steer = scale_pwm(float(steering.pulse_us), cfg)
            thr = scale_pwm(float(throttle.pulse_us), cfg)
            if cfg.get("steering_invert", False):
                steer = -steer
            if cfg.get("throttle_invert", False):
                thr = -thr
            active = is_control_active(thr, steer)
            if active:
                post_control({"cmd": "drive", "source": LOCAL_RC_SOURCE, "throttle": thr, "steering": steer})
                stopped = False
            elif not stopped:
                post_control({"cmd": "drive", "source": LOCAL_RC_SOURCE, "throttle": 0.0, "steering": 0.0})
                stopped = True
            if should_log_control(now, last_log, last_log_thr, last_log_steer, thr, steer):
                print(
                    f"rc pwm throttle={throttle.pulse_us:.0f}us steering={steering.pulse_us:.0f}us -> "
                    f"thr={thr:.2f} steer={steer:.2f}",
                    flush=True,
                )
                last_log = now
                last_log_thr = thr
                last_log_steer = steer
        elif not stopped:
            post_control({"cmd": "stop", "source": LOCAL_RC_SOURCE})
            print("rc signal lost -> stop", flush=True)
            stopped = True
        time.sleep(send_interval)


def run_serial_loop(cfg: dict[str, Any], watchdog: SystemdWatchdog) -> None:
    port = str(cfg.get("serial_port", "/dev/ttyUSB0") or "/dev/ttyUSB0")
    baud = int(cfg.get("baud", 115200) or 115200)
    send_interval = 1.0 / max(1, min(50, int(cfg.get("send_hz", 25) or 25)))
    timeout = max(0.05, float(cfg.get("signal_timeout_s", 0.35) or 0.35))
    write_rc_state(source="serial", ok=False, port=port, throttle=0.0, steering=0.0)
    watchdog.ready()
    while True:
        watchdog.ping()
        try:
            fd = open_serial(port, baud)
        except OSError as exc:
            print(f"RC serial waiting for {port}: {exc}", flush=True)
            write_rc_state(source="serial", ok=False, port=port, error=str(exc), throttle=0.0, steering=0.0)
            time.sleep(2.0)
            continue
        print(f"RC serial input started: {port} @ {baud}", flush=True)
        try:
            _run_open_serial_loop(fd, port, cfg, send_interval, timeout, watchdog)
        except OSError as exc:
            print(f"RC serial disconnected: {exc}", flush=True)
            write_rc_state(source="serial", ok=False, port=port, error=str(exc), throttle=0.0, steering=0.0)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        time.sleep(1.0)


def _run_open_serial_loop(fd: int, port: str, cfg: dict[str, Any], send_interval: float, timeout: float, watchdog: SystemdWatchdog) -> None:
    buf = b""
    last_valid = 0.0
    last_send = 0.0
    stopped = True
    last_log = 0.0
    last_log_thr: float | None = None
    last_log_steer: float | None = None
    last_state = 0.0
    throttle_us = 1500.0
    steering_us = 1500.0
    while True:
            watchdog.ping()
            now = time.monotonic()
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                data = os.read(fd, 1024)
                if data:
                    buf += data
                    while b"\n" in buf:
                        raw, buf = buf.split(b"\n", 1)
                        parsed = parse_rc_line(raw.decode(errors="ignore").strip())
                        if parsed is None:
                            continue
                        throttle_raw, steering_raw, ok = parsed
                        if ok:
                            if 800 <= throttle_raw <= 2200:
                                throttle_us = float(throttle_raw)
                            else:
                                throttle_us = 1500.0
                            if 800 <= steering_raw <= 2200:
                                steering_us = float(steering_raw)
                            else:
                                steering_us = 1500.0
                            last_valid = now
            if now - last_send < send_interval:
                continue
            last_send = now
            valid = now - last_valid <= timeout
            if valid:
                primary = scale_pwm(throttle_us, cfg)
                secondary = scale_pwm(steering_us, cfg)
                if str(cfg.get("mix_mode", "tracks")) == "tracks":
                    # Some RC transmitters already mix throttle/steering into left/right tracks.
                    # Undo that mix before forwarding the normal rover throttle/steering command.
                    thr = (primary + secondary) / 2.0
                    steer = (primary - secondary) / 2.0
                else:
                    thr = primary
                    steer = secondary
                if cfg.get("throttle_invert", False):
                    thr = -thr
                if cfg.get("steering_invert", False):
                    steer = -steer
                active = is_control_active(thr, steer)
                if active:
                    post_control({"cmd": "drive", "source": LOCAL_RC_SOURCE, "throttle": thr, "steering": steer})
                    stopped = False
                elif not stopped:
                    post_control({"cmd": "drive", "source": LOCAL_RC_SOURCE, "throttle": 0.0, "steering": 0.0})
                    stopped = True
                if now - last_state >= 0.25:
                    write_rc_state(
                        source="serial",
                        ok=True,
                        port=port,
                        throttle_us=throttle_us,
                        steering_us=steering_us,
                        throttle=thr,
                        steering=steer,
                        mix_mode=str(cfg.get("mix_mode", "tracks")),
                    )
                    last_state = now
                if should_log_control(now, last_log, last_log_thr, last_log_steer, thr, steer):
                    print(
                        f"rc serial throttle={throttle_us:.0f}us steering={steering_us:.0f}us -> "
                        f"thr={thr:.2f} steer={steer:.2f} mix={cfg.get('mix_mode', 'tracks')}",
                        flush=True,
                    )
                    last_log = now
                    last_log_thr = thr
                    last_log_steer = steer
            elif not stopped:
                post_control({"cmd": "stop", "source": LOCAL_RC_SOURCE})
                print("rc serial signal lost -> stop", flush=True)
                write_rc_state(source="serial", ok=False, port=port, throttle=0.0, steering=0.0)
                stopped = True


def open_serial(port: str, baud: int) -> int:
    baud_map = {
        9600: termios.B9600,
        19200: termios.B19200,
        38400: termios.B38400,
        57600: termios.B57600,
        115200: termios.B115200,
    }
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[4] = baud_map.get(baud, termios.B115200)
    attrs[5] = baud_map.get(baud, termios.B115200)
    attrs[2] |= termios.CLOCAL | termios.CREAD
    attrs[2] &= ~termios.PARENB
    attrs[2] &= ~termios.CSTOPB
    attrs[2] &= ~termios.CSIZE
    attrs[2] |= termios.CS8
    attrs[3] &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
    attrs[0] &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
    attrs[1] &= ~termios.OPOST
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def parse_rc_line(line: str) -> tuple[int, int, bool] | None:
    if not line.startswith("RC,"):
        return None
    parts = line.split(",")
    if len(parts) < 4:
        return None
    try:
        throttle = int(float(parts[1]))
        steering = int(float(parts[2]))
        ok = int(float(parts[3])) != 0
        return throttle, steering, ok
    except ValueError:
        return None


def should_log_control(
    now: float,
    last_log: float,
    last_thr: float | None,
    last_steer: float | None,
    thr: float,
    steer: float,
) -> bool:
    if max(abs(thr), abs(steer)) < LOG_ACTIVE_THRESHOLD:
        return False
    if last_thr is None or last_steer is None:
        return True
    if now - last_log >= LOG_KEEPALIVE_S:
        return True
    return abs(thr - last_thr) >= LOG_DELTA_THRESHOLD or abs(steer - last_steer) >= LOG_DELTA_THRESHOLD


def is_control_active(thr: float, steer: float) -> bool:
    return max(abs(thr), abs(steer)) >= CONTROL_ACTIVE_THRESHOLD


def write_rc_state(**patch: Any) -> None:
    global _LAST_RC_STATE_SIG, _LAST_RC_STATE_WRITE
    state = dict(patch)
    now = time.time()
    sig = rc_state_signature(state)
    if sig == _LAST_RC_STATE_SIG and now - _LAST_RC_STATE_WRITE < STATE_KEEPALIVE_S:
        return
    state["updated_ts"] = now
    try:
        RC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = RC_STATE_FILE.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
            f.write("\n")
        tmp.replace(RC_STATE_FILE)
        _LAST_RC_STATE_SIG = sig
        _LAST_RC_STATE_WRITE = now
    except Exception as exc:
        print(f"rc state write failed: {exc}", flush=True)


def rc_state_signature(state: dict[str, Any]) -> str:
    stable: dict[str, Any] = {}
    for key, value in state.items():
        if key == "updated_ts":
            continue
        if isinstance(value, float):
            if key.endswith("_us"):
                stable[key] = round(value)
            else:
                stable[key] = round(value / STATE_DELTA_THRESHOLD) * STATE_DELTA_THRESHOLD
        else:
            stable[key] = value
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


def scale_pwm(value_us: float, cfg: dict[str, Any]) -> float:
    minimum = float(cfg.get("min_us", 1000) or 1000)
    center = float(cfg.get("center_us", 1500) or 1500)
    maximum = float(cfg.get("max_us", 2000) or 2000)
    deadzone = max(0.0, float(cfg.get("deadzone", 0.06) or 0.0))
    if value_us >= center:
        denom = max(1.0, maximum - center)
        value = (value_us - center) / denom
    else:
        denom = max(1.0, center - minimum)
        value = -((center - value_us) / denom)
    value = max(-1.0, min(1.0, value))
    return 0.0 if abs(value) < deadzone else value


def post_control(payload: dict[str, Any]) -> None:
    global _CONTROL_SEQ
    _CONTROL_SEQ += 1
    payload = dict(payload)
    payload["seq"] = _CONTROL_SEQ
    payload["updated_ts"] = time.time()
    try:
        write_runtime_json(CONTROL_COMMAND_FILE, payload)
    except Exception as exc:
        print(f"rc control command write failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
