"""
FastAPI бэкенд для ровера.

Эндпоинты:
    GET  /              — статичный index.html
    GET  /static/*      — JS/CSS
    GET  /api/state     — текущее состояние моторов
    GET  /api/health    — для systemd watchdog / Tailscale healthcheck
    WS   /ws/control    — поток команд джойстика {throttle, steering}

Запуск:
    uvicorn backend.main:app --host 0.0.0.0 --port 8080

ENV:
    GIMLI_MOCK_MOTORS=1   — без GPIO, для отладки на ноуте
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
import urllib.request
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import subprocess

from backend.settings import (
    CONTROL_COMMAND_FILE,
    SENSOR_STATE_FILE,
    load_settings,
    motor_settings,
    network_status,
    poweroff_pi,
    public_settings,
    reboot_pi,
    restart_go2rtc,
    save_settings,
    setup_ap_control,
    telemetry,
    wifi_connect,
    wifi_scan,
    write_sensor_command,
)
from backend.watchdog import SystemdWatchdog

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ---- мотор-драйвер: реальный или mock ----------------------------------------
class MockDrive:
    """Заглушка для разработки без Pi."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {"left": 0, "right": 0, "throttle": 0, "steering": 0}

    def drive(self, throttle: float, steering: float) -> dict[str, float]:
        left = max(-1.0, min(1.0, throttle + steering))
        right = max(-1.0, min(1.0, throttle - steering))
        self.last = {"left": left, "right": right, "throttle": throttle, "steering": steering}
        return self.last

    def stop(self) -> None:
        self.last = {"left": 0, "right": 0, "throttle": 0, "steering": 0}

    def shutdown(self) -> None:
        pass

    @property
    def state(self) -> Any:
        return type("S", (), self.last)()


def make_drive():
    settings = load_settings()
    motors = motor_settings(settings)
    if os.environ.get("GIMLI_MOCK_MOTORS") == "1" or motors.get("mock"):
        return MockDrive()
    if motors.get("type") == "vesc":
        from backend.vesc import VescDrive
        return VescDrive(
            config=motors.get("vesc", {}),
            watchdog_timeout_s=float(motors.get("watchdog_timeout_s", 0.5)),
        )
    from backend.motors import RoverDrive
    return RoverDrive(
        pins=motors.get("pins"),
        watchdog_timeout_s=float(motors.get("watchdog_timeout_s", 0.5)),
    )


def apply_control_payload(app: FastAPI, payload: dict[str, Any]) -> dict[str, Any]:
    drive = app.state.drive
    cmd = payload.get("cmd", "drive")
    source = str(payload.get("source", "api") or "api")
    now = time.monotonic()
    local_until = float(getattr(app.state, "local_rc_until", 0.0) or 0.0)
    if source == "local_rc":
        app.state.local_rc_until = now + 0.75
    elif now < local_until:
        return {"ok": True, "state": "local_rc_priority"}
    if cmd == "stop":
        drive.stop()
        return {"ok": True, "state": "stopped"}
    if cmd != "drive":
        return {"ok": False, "error": "unknown command"}

    throttle = float(payload.get("throttle", 0))
    steering = float(payload.get("steering", 0))
    state = drive.drive(throttle, steering)
    data = state if isinstance(state, dict) else {
        "left": state.left,
        "right": state.right,
        "throttle": state.throttle,
        "steering": state.steering,
    }
    return {"ok": True, "state": data}


def control_command_loop(app: FastAPI, stop_event: threading.Event) -> None:
    last_seq: int | None = None
    while not stop_event.is_set():
        try:
            if CONTROL_COMMAND_FILE.exists():
                payload = json.loads(CONTROL_COMMAND_FILE.read_text(encoding="utf-8"))
                seq = int(payload.get("seq", 0) or 0)
                if seq != last_seq:
                    last_seq = seq
                    apply_control_payload(app, payload)
        except Exception as exc:
            print(f"control command loop failed: {exc}", flush=True)
        stop_event.wait(0.02)


async def watchdog_task(watchdog: SystemdWatchdog) -> None:
    while True:
        watchdog.ping()
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    watchdog = SystemdWatchdog()
    app.state.drive = make_drive()
    stop_event = threading.Event()
    app.state.control_command_stop = stop_event
    app.state.control_command_thread = threading.Thread(
        target=control_command_loop,
        args=(app, stop_event),
        daemon=True,
    )
    app.state.control_command_thread.start()
    app.state.watchdog_task = asyncio.create_task(watchdog_task(watchdog))
    watchdog.ready()
    try:
        yield
    finally:
        watchdog.stopping()
        app.state.watchdog_task.cancel()
        try:
            await app.state.watchdog_task
        except asyncio.CancelledError:
            pass
        stop_event.set()
        app.state.control_command_thread.join(timeout=1.0)
        app.state.drive.shutdown()


app = FastAPI(title="Gimli Rover", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def get_state() -> JSONResponse:
    s = app.state.drive.state
    return JSONResponse(
        {
            "left": getattr(s, "left", 0),
            "right": getattr(s, "right", 0),
            "throttle": getattr(s, "throttle", 0),
            "steering": getattr(s, "steering", 0),
        }
    )


@app.post("/api/control")
async def api_control(payload: dict[str, Any]) -> JSONResponse:
    result = apply_control_payload(app, payload)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.get("/api/settings")
def get_settings() -> JSONResponse:
    return JSONResponse(public_settings())


@app.get("/api/telemetry")
def get_telemetry() -> JSONResponse:
    return JSONResponse(telemetry())


@app.get("/api/network/status")
def get_network_status() -> JSONResponse:
    return JSONResponse(network_status())


@app.get("/api/network/wifi-scan")
def get_wifi_scan(interface: str = "wlan0") -> JSONResponse:
    return JSONResponse(wifi_scan(interface))


@app.post("/api/network/wifi-connect")
async def post_wifi_connect(payload: dict[str, Any]) -> JSONResponse:
    ok, message = wifi_connect(
        ssid=str(payload.get("ssid", "")),
        password=str(payload.get("password", "")),
        interface=str(payload.get("interface", "wlan0")),
    )
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 400)


@app.post("/api/network/setup-ap")
async def post_setup_ap(payload: dict[str, Any]) -> JSONResponse:
    ok, message = setup_ap_control(str(payload.get("action", "start")))
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 400)


@app.post("/api/settings")
async def update_settings(ws_payload: dict[str, Any]) -> JSONResponse:
    old_motor_settings = motor_settings(load_settings())
    settings = save_settings(ws_payload)
    restarted, message = restart_go2rtc()
    motors_reloaded = False
    if motor_settings(settings) != old_motor_settings:
        old_drive = app.state.drive
        app.state.drive = make_drive()
        motors_reloaded = True
        try:
            old_drive.shutdown()
        except Exception:
            pass
    return JSONResponse(
        {
            "ok": True,
            "settings": public_settings(settings),
            "go2rtc_restarted": restarted,
            "motors_reloaded": motors_reloaded,
            "message": message,
            "backend_restart_required": False,
        }
    )


@app.post("/api/system/poweroff")
def system_poweroff() -> JSONResponse:
    ok, message = poweroff_pi()
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 500)


@app.post("/api/system/reboot")
def system_reboot() -> JSONResponse:
    ok, message = reboot_pi()
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 500)


@app.get("/api/network/client-ip")
def get_client_ip(request: Request) -> JSONResponse:
    """Возвращает IP, с которого пришёл запрос. Удобно для автозаполнения
    MAVLink connection: пользователь жмёт 'Use my IP' в UI, ровер видит его
    Tailscale-адрес и подставляет в udpout:<ip>:14550."""
    host = request.client.host if request.client else ""
    return JSONResponse({"ip": host})


@app.post("/api/mavlink/restart")
def restart_mavlink() -> JSONResponse:
    """Перезапускает gimli-mavlink.service, чтобы применить новые настройки
    без SSH. Требует sudoers-правила (см. scripts/install.sh)."""
    try:
        result = subprocess.run(
            ["sudo", "-n", "/bin/systemctl", "restart", "gimli-mavlink.service"],
            capture_output=True, text=True, timeout=10,
        )
        ok = result.returncode == 0
        message = result.stderr.strip() or result.stdout.strip() or ("restarted" if ok else "failed")
        return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 500)
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)



@app.post("/api/compass/start")
def post_compass_start() -> JSONResponse:
    """Запускает hard-iron калибровку компаса. После вызова ровер нужно
    плавно вращать на 360° (1-2 оборота) на ровной поверхности. Прогресс
    читается из /api/compass/status. По завершении вызвать /api/compass/accept
    чтобы записать offset/scale в compass_calibration.json."""
    try:
        write_sensor_command("start_mag_cal")
        return JSONResponse({"ok": True, "message": "calibration started — rotate the rover 360°"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


@app.post("/api/compass/accept")
def post_compass_accept() -> JSONResponse:
    """Применяет результаты последней удачной калибровки."""
    try:
        write_sensor_command("accept_mag_cal")
        return JSONResponse({"ok": True, "message": "calibration accepted"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


@app.post("/api/compass/cancel")
def post_compass_cancel() -> JSONResponse:
    """Прерывает текущую калибровку без применения."""
    try:
        write_sensor_command("cancel_mag_cal")
        return JSONResponse({"ok": True, "message": "calibration cancelled"})
    except Exception as exc:
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)


@app.get("/api/compass/status")
def get_compass_status() -> JSONResponse:
    """Возвращает текущее состояние калибровки (active/progress/status/offset/scale)."""
    try:
        if SENSOR_STATE_FILE.exists():
            data = json.loads(SENSOR_STATE_FILE.read_text(encoding="utf-8"))
            return JSONResponse(data.get("compass_calibration") or {})
    except Exception:
        pass
    return JSONResponse({})


@app.post("/api/webrtc")
async def webrtc_offer(request: Request, src: str = "active") -> PlainTextResponse:
    """Proxy WebRTC SDP offers to go2rtc so the web UI can play camera audio."""
    if src not in {"cam1", "cam2", "active"}:
        return PlainTextResponse("unknown stream", status_code=404)
    offer = await request.body()
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:1984/api/webrtc?src=" + quote(src, safe=""),
            data=offer,
            headers={"Content-Type": "application/sdp"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            answer = resp.read().decode("utf-8", errors="replace")
        return PlainTextResponse(answer, media_type="application/sdp")
    except Exception as exc:
        return PlainTextResponse(str(exc), status_code=502)


@app.get("/api/video/{stream}.mjpeg")
def video_mjpeg(stream: str) -> StreamingResponse:
    if stream not in {"cam1", "cam2", "active"}:
        return JSONResponse({"ok": False, "message": "unknown stream"}, status_code=404)

    def chunks():
        proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-rtsp_transport", "tcp",
                "-i", f"rtsp://127.0.0.1:8554/{stream}",
                "-an",
                "-vf", "fps=1,scale=480:-1",
                "-q:v", "16",
                "-f", "mpjpeg",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            if proc.stdout is None:
                return
            while True:
                data = proc.stdout.read(65536)
                if not data:
                    break
                yield data
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

    return StreamingResponse(
        chunks(),
        media_type="multipart/x-mixed-replace; boundary=ffmpeg",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/camera/{camera}/control")
def camera_control(camera: str, payload: dict[str, Any]) -> JSONResponse:
    settings = load_settings()
    cam = settings.get("cameras", {}).get(camera)
    if camera not in {"cam1", "cam2"} or not cam:
        return JSONResponse({"ok": False, "message": "unknown camera"}, status_code=404)
    if not cam.get("enabled", True):
        return JSONResponse({"ok": False, "message": "camera disabled"}, status_code=400)

    action = str(payload.get("action", "")).strip().lower()
    if action not in {"light", "daynight"}:
        return JSONResponse({"ok": False, "message": "unknown action"}, status_code=400)

    if action == "daynight":
        mode = str(payload.get("mode", "auto")).strip().lower()
        ok, message = _set_camera_daynight(cam, mode)
        return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 500)

    mode = str(payload.get("mode", "auto")).strip().lower()
    level = int(payload.get("level", 60) or 60)
    level = max(0, min(100, level))
    ok, message = _set_camera_light(cam, mode, level)
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 500)


@app.post("/api/cameras/tune-video")
def tune_camera_video(payload: dict[str, Any] | None = None) -> JSONResponse:
    settings = load_settings()
    network = settings.get("network", {})
    requested = str((payload or {}).get("profile") or network.get("profile") or "low").lower()
    target_kbps = int((payload or {}).get("target_kbps") or network.get("target_kbps") or 900)
    profile = "low" if requested == "low" or target_kbps <= 1500 else "balanced" if requested == "balanced" else "high"

    results: dict[str, Any] = {}
    ok_any = False
    for name, cam in settings.get("cameras", {}).items():
        if name not in {"cam1", "cam2"} or not cam.get("enabled", True):
            continue
        ok, message = _tune_dahua_video(cam, profile, target_kbps)
        results[name] = {"ok": ok, "message": message}
        ok_any = ok_any or ok

    if ok_any:
        for cam in settings.get("cameras", {}).values():
            cam["preferred"] = "sub" if profile == "low" else cam.get("preferred", "main")
        settings.setdefault("network", {})["profile"] = profile
        settings.setdefault("network", {})["target_kbps"] = target_kbps
        settings = save_settings(settings)
        restarted, restart_message = restart_go2rtc()
    else:
        restarted, restart_message = False, "camera tuning failed"

    return JSONResponse(
        {
            "ok": ok_any,
            "profile": profile,
            "target_kbps": target_kbps,
            "results": results,
            "go2rtc_restarted": restarted,
            "message": restart_message,
            "settings": public_settings(settings),
        },
        status_code=200 if ok_any else 500,
    )


def _tune_dahua_video(cam: dict[str, Any], profile: str, target_kbps: int) -> tuple[bool, str]:
    host = str(cam.get("host", "")).strip()
    username = str(cam.get("username", "admin") or "admin")
    password = str(cam.get("password", "") or "")
    if not host or not password:
        return False, "camera host/password is missing"

    if profile == "low":
        main_bitrate = max(500, min(1200, target_kbps))
        main_fps, main_gop = 10, 20
        sub_bitrate, sub_fps, sub_gop = 350, 8, 16
    elif profile == "high":
        main_bitrate, main_fps, main_gop = 3500, 20, 40
        sub_bitrate, sub_fps, sub_gop = 700, 10, 20
    else:
        main_bitrate, main_fps, main_gop = 1600, 12, 24
        sub_bitrate, sub_fps, sub_gop = 450, 8, 16

    query_sets: list[list[tuple[str, str]]] = [
        _dahua_encode_pairs("MainFormat", main_bitrate, main_fps, main_gop),
        _dahua_encode_pairs("ExtraFormat", sub_bitrate, sub_fps, sub_gop),
        [
            ("Encode[0].MainFormat[0].Video.BitRateControl", "CBR"),
            ("Encode[0].ExtraFormat[0].Video.BitRateControl", "CBR"),
        ],
        [
            ("Encode[0].MainFormat[0].Video.Compression", "H.264"),
            ("Encode[0].ExtraFormat[0].Video.Compression", "H.264"),
        ],
        [
            ("Encode[0].MainFormat[0].Video.Profile", "Baseline"),
            ("Encode[0].ExtraFormat[0].Video.Profile", "Baseline"),
        ],
        [
            ("Encode[0].MainFormat[0].Video.SmartCodec", "false"),
            ("Encode[0].ExtraFormat[0].Video.SmartCodec", "false"),
        ],
    ]
    accepted = 0
    last = ""
    for pairs in query_sets:
        ok, message = _camera_set_config(host, username, password, pairs)
        last = message
        if ok:
            accepted += 1
    if accepted:
        return True, f"{profile}: accepted {accepted}/{len(query_sets)} groups"
    return False, last or "camera rejected video profile"


def _dahua_encode_pairs(fmt: str, bitrate: int, fps: int, gop: int) -> list[tuple[str, str]]:
    head = f"Encode[0].{fmt}[0].Video"
    return [
        (f"{head}.BitRate", str(bitrate)),
        (f"{head}.FPS", str(fps)),
        (f"{head}.GOP", str(gop)),
    ]


def _camera_set_config(host: str, username: str, password: str, pairs: list[tuple[str, str]]) -> tuple[bool, str]:
    query = "&".join(f"{quote(k, safe='[]')}={quote(v)}" for k, v in pairs)
    url = f"http://{host}/cgi-bin/configManager.cgi?action=setConfig&{query}"
    result = subprocess.run(
        ["curl", "--globoff", "--digest", "-u", f"{username}:{password}", "-sS", "--max-time", "5", url],
        capture_output=True,
        text=True,
        timeout=7,
    )
    output = (result.stdout + result.stderr).strip()
    ok = result.returncode == 0 and ("true" in output.lower() or output.strip().upper() == "OK")
    return ok, output or f"curl exit {result.returncode}"


def _set_camera_daynight(cam: dict[str, Any], mode: str) -> tuple[bool, str]:
    host = str(cam.get("host", "")).strip()
    username = str(cam.get("username", "admin") or "admin")
    password = str(cam.get("password", "") or "")
    if not host or not password:
        return False, "camera host/password is missing"

    if mode in {"day", "color", "colour"}:
        dahua_modes = ["Color"]
    elif mode in {"night", "bw", "blackwhite", "black_white"}:
        dahua_modes = ["BlackWhite", "B/W"]
    elif mode in {"auto", "smart"}:
        dahua_modes = ["Auto"]
    else:
        return False, "unknown day/night mode"

    query_sets: list[list[tuple[str, str]]] = []
    for dahua_mode in dahua_modes:
        for profile in (0, 1, 2):
            query_sets.append([(f"VideoInMode[0].Config[{profile}]", dahua_mode)])
            query_sets.append([(f"VideoInMode[0][{profile}].Mode", dahua_mode)])
            query_sets.append([(f"VideoInOptions[0].DayNightMode", dahua_mode)])
            query_sets.append([(f"VideoInDayNight[0][{profile}].Mode", dahua_mode)])

    last = ""
    accepted = 0
    for pairs in query_sets:
        query = "&".join(f"{quote(k, safe='[]')}={quote(v)}" for k, v in pairs)
        url = f"http://{host}/cgi-bin/configManager.cgi?action=setConfig&{query}"
        result = subprocess.run(
            ["curl", "--globoff", "--digest", "-u", f"{username}:{password}", "-sS", "--max-time", "5", url],
            capture_output=True,
            text=True,
            timeout=7,
        )
        output = (result.stdout + result.stderr).strip()
        last = output or f"curl exit {result.returncode}"
        if result.returncode == 0 and ("true" in output.lower() or output.strip().upper() == "OK"):
            accepted += 1

    if accepted:
        return True, f"daynight {mode} accepted {accepted}"
    return False, last or "camera rejected day/night command"


def _set_camera_light(cam: dict[str, Any], mode: str, level: int) -> tuple[bool, str]:
    host = str(cam.get("host", "")).strip()
    username = str(cam.get("username", "admin") or "admin")
    password = str(cam.get("password", "") or "")
    if not host or not password:
        return False, "camera host/password is missing"

    if mode in {"on", "manual", "white"}:
        dahua_modes = ["Manual", "ForceOn"]
    elif mode in {"off", "disable"}:
        dahua_modes = ["Off", "ForceOff"]
    elif mode in {"auto", "smart"}:
        dahua_modes = ["Auto"]
    else:
        return False, "unknown light mode"

    query_sets: list[list[tuple[str, str]]] = []
    for dahua_mode in dahua_modes:
        for prefix in ("", "All."):
            for profile in (0, 1, 2):  # day, night, general/scene depending on firmware
                for light_index in (0, 1):  # most Dahua firmwares use [0], some dual-light models use [1]
                    head = f"{prefix}Lighting_V2[0][{profile}][{light_index}]"
                    query_sets.append(
                        [
                            (f"{head}.Mode", dahua_mode),
                            (f"{head}.Light", str(level)),
                            (f"{head}.Brightness", str(level)),
                            (f"{head}.MiddleLight[0].Light", str(level)),
                            (f"{head}.NearLight[0].Light", str(level)),
                            (f"{head}.FarLight[0].Light", str(level)),
                        ]
                    )
                    query_sets.append([(f"{head}.Mode", dahua_mode)])
            for profile in (0, 1, 2):
                head = f"{prefix}Lighting[0][{profile}]"
                query_sets.append(
                    [
                        (f"{head}.Mode", dahua_mode),
                        (f"{head}.Light", str(level)),
                        (f"{head}.Brightness", str(level)),
                        (f"{head}.MiddleLight[0].Light", str(level)),
                        (f"{head}.NearLight[0].Light", str(level)),
                        (f"{head}.FarLight[0].Light", str(level)),
                    ]
                )
                query_sets.append([(f"{head}.Mode", dahua_mode)])

    last = ""
    accepted = 0
    accepted_keys: list[str] = []
    for pairs in query_sets:
        query = "&".join(f"{quote(k, safe='[]')}={quote(v)}" for k, v in pairs)
        url = f"http://{host}/cgi-bin/configManager.cgi?action=setConfig&{query}"
        result = subprocess.run(
            ["curl", "--globoff", "--digest", "-u", f"{username}:{password}", "-sS", "--max-time", "5", url],
            capture_output=True,
            text=True,
            timeout=7,
        )
        output = (result.stdout + result.stderr).strip()
        last = output or f"curl exit {result.returncode}"
        if result.returncode == 0 and ("true" in output.lower() or output.strip().upper() == "OK"):
            accepted += 1
            accepted_keys.append(pairs[0][0])

    if accepted:
        sample = ", ".join(accepted_keys[:4])
        more = "" if accepted <= 4 else f" +{accepted - 4}"
        return True, f"light {mode} {level}% accepted {accepted}: {sample}{more}"
    return False, last or "camera rejected light command"


@app.websocket("/ws/control")
async def ws_control(ws: WebSocket) -> None:
    await ws.accept()
    drive = app.state.drive
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            cmd = msg.get("cmd", "drive")
            if cmd == "stop":
                drive.stop()
                await ws.send_json({"ok": True, "state": "stopped"})
                continue
            if cmd != "drive":
                continue

            throttle = float(msg.get("throttle", 0))
            steering = float(msg.get("steering", 0))
            state = drive.drive(throttle, steering)
            # на mock возвращается dict, на реальном — dataclass
            payload = state if isinstance(state, dict) else {
                "left": state.left,
                "right": state.right,
                "throttle": state.throttle,
                "steering": state.steering,
            }
            await ws.send_json({"ok": True, "state": payload})
    except WebSocketDisconnect:
        # клиент отвалился — failsafe в motors.py также сработает по watchdog
        drive.stop()
    except Exception:
        drive.stop()
        raise


# ---- статика (фронт) ---------------------------------------------------------
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")
