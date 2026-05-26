# Gimli Rover

Raspberry Pi rover stack for field testing: one-page setup web UI, two Dahua IP cameras, VESC motor control, GPS/compass telemetry, local RC input, and QGroundControl over MAVLink.

## Current Rover

- Raspberry Pi host: `gimli-rover.YOUR-TAILNET.ts.net`
- Web UI: `http://gimli-rover.YOUR-TAILNET.ts.net:8080/`
- go2rtc UI: `http://gimli-rover.YOUR-TAILNET.ts.net:1984/`
- QGroundControl listens on UDP `14550`
- Camera 1/front: `192.168.1.108`
- Camera 2/rear: `192.168.1.109`
- Camera login: `admin / CHANGE_ME_camera_password`
- VESC local USB: right side
- VESC CAN ID `68`: left side
- Local RC receiver: receiver -> Arduino -> Pi USB serial `/dev/ttyUSB0`

## Services

```bash
systemctl status gimli-rover.service
systemctl status gimli-sensors.service
systemctl status gimli-mavlink.service
systemctl status gimli-rc-input.service
systemctl status go2rtc.service
systemctl status gimli-network-fallback.service
```

Useful logs:

```bash
journalctl -u gimli-rover -f
journalctl -u gimli-sensors -f
journalctl -u gimli-mavlink -f
journalctl -u gimli-rc-input -f
journalctl -u go2rtc -f
```

Run a quick readiness check:

```bash
bash /opt/gimli/scripts/preflight-check.sh
```

## Hardware

GPS on Raspberry Pi UART:

| GPS | Raspberry Pi |
| --- | --- |
| TX | GPIO15 / RXD |
| RX | GPIO14 / TXD |
| GND | GND |
| VCC | module-specific 3.3V/5V |

Compass on Raspberry Pi I2C:

| Compass | Raspberry Pi |
| --- | --- |
| SDA | GPIO2 / SDA |
| SCL | GPIO3 / SCL |
| GND | GND |
| VCC | 3.3V |

Current sensor, if installed, shares the I2C bus with the compass. Keep SDA/SCL common, use a separate I2C address, and keep power wiring away from the compass.

VESC:

- Pi USB -> local VESC.
- Local VESC CAN -> second VESC.
- `settings.json`: `motors.type = "vesc"`.
- Local/right VESC uses USB, left VESC uses CAN ID `68`.
- Use UART-only app mode on VESC for Pi control. PPM+UART caused unstable behavior during testing.

Local RC:

- The small receiver is decoded by Arduino.
- Arduino sends serial lines to Pi: `RC,throttle_us,steering_us,ok`.
- Pi reads the stable CH340 path `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` at `115200`.
- Current mapping uses `mix_mode = "tracks"` because the receiver outputs track-style mixed channels.
- Local RC commands are sent as `source=local_rc` and have priority over QGroundControl, web control, and ARM state. This makes the physical nearby transmitter the always-available manual override.

## Web UI

The web UI is one page and is kept as the Pi setup panel:

- two camera views
- audio button through go2rtc WebRTC
- camera day/night, IR light, reboot, quality controls
- OSD for voltage, current, GPS, compass, ARM, RC, buttons, active mode
- Wi-Fi scan/connect and fallback setup AP
- QGroundControl/MAVLink destination list
- VESC/motor settings
- Pi reboot and shutdown buttons

If no normal network is available, the fallback setup AP can be enabled:

```text
SSID: Gimli-Rover-Setup
Password: gimli1234
Web: http://10.42.0.1:8080/
```

Current rover networking is LAN-first for Starlink:

- `eth0` is always kept on DHCP with route metric `10`.
- If LAN gets gateway `192.168.1.1`, the setup AP is stopped and internet goes through LAN.
- If LAN is missing or Starlink is still booting, `Gimli-Rover-Setup` is raised on `wlan0` for local setup.
- While the setup AP is active, the service keeps retrying LAN DHCP in the background.
- In `link_mode=lan`, saved Wi-Fi client profiles are not auto-connected; `wlan0` is reserved for the setup AP fallback.

## QGroundControl

Create a UDP Comm Link:

- Type: UDP
- Listening port: `14550`
- Name: any rover name

The Pi MAVLink bridge sends telemetry to the configured QGC UDP targets. Manual driving is not tied to GPS. GPS is telemetry only.

MAVLink button mapping:

| Button bit | Function |
| --- | --- |
| 0 | ARM / disarm |
| 1 | day/night mode for cameras |
| 2 | quality main/sub |
| 3 | active camera cam1/cam2 |

When ARM is off, motor output is stopped.

## Compass And GPS

Current Matek M10-5883 compass axes:

```json
{
  "compass_x_axis": "-y",
  "compass_y_axis": "x",
  "compass_z_axis": "-z"
}
```

Compass heading offset is stored in `/opt/gimli/config/settings.json` under:

```json
{
  "navigation": {
    "heading_offset_deg": 191.85
  }
}
```

If heading drifts after mounting, first move the compass away from motor/power wiring, then adjust offset. A software offset fixes a constant rotation, not magnetic interference.

GPS spoofing guard can be handled by disabling GPS telemetry or using guarded mode:

```json
{
  "navigation": {
    "source": "off",
    "gps_trust": "disabled"
  }
}
```

Starlink does not provide rover GPS data. It only provides internet connectivity. Position still comes from the rover GPS module unless GPS is disabled.

## Cameras

Dahua RTSP examples:

```text
rtsp://admin:CHANGE_ME@192.168.1.108:554/cam/realmonitor?channel=1&subtype=0
rtsp://admin:CHANGE_ME@192.168.1.109:554/cam/realmonitor?channel=1&subtype=0
```

go2rtc exposes streams for the web UI and QGroundControl. Use sub-stream quality on weak links or Starlink-constrained links.

Current Starlink/low profile:

- Web UI prefers WebRTC from go2rtc.
- MJPEG is only a fallback and is reduced to `640px / 2 fps`.
- go2rtc uses Dahua `subtype=1` first for `active`, `cam1`, and `cam2`.
- Camera sub streams are currently `704x576`, `H.264`, `CBR`, `256 kbps`, `10 fps`.
- QGroundControl video stream metadata advertises low profile as about `640x360`, `10 fps`, `500 kbps`.

For about `3 Mbit/s` Starlink upload, keep total video below roughly `1 Mbit/s` when driving. Use one active camera in QGroundControl when possible; two simultaneous web views plus QGC can still add up because every viewer is a separate outgoing stream.

## Field Test Checklist

1. Power the rover and wait for Pi boot.
2. Open the web UI and confirm voltage/current are visible.
3. Confirm both cameras show live video.
4. Press `Звук` and confirm camera audio if needed.
5. Confirm ARM/RC status appears in the top bar or OSD.
6. Lift wheels before motor tests.
7. Test local RC: forward/back should spin both sides, steering should spin sides differentially.
8. Open QGroundControl and confirm MAVLink telemetry.
9. Test camera day/night, quality, and active camera buttons.
10. Stop motors, disarm, then use web shutdown before cutting Pi power when possible.

For normal shutdown, prefer the web shutdown button. Cutting power usually works, but it risks filesystem corruption and can hide low-voltage boot issues.

## Deploy On Current Pi

From the laptop:

```powershell
# Replace YOUR-TAILNET with your actual Tailscale tail-id, and use your own SSH password.
scp -r frontend backend config scripts README.md gimli1mb@gimli-rover.YOUR-TAILNET.ts.net:/home/gimli1mb/gimli/
```

On the Pi:

```bash
sudo bash /home/gimli1mb/gimli/scripts/apply-update-on-pi.sh
bash /opt/gimli/scripts/preflight-check.sh
```

Do not use a plain `rsync --delete` onto `/opt/gimli` without backing up `/opt/gimli/config/settings.json`, because that file contains live camera, Wi-Fi, VESC, compass, and MAVLink settings.

If the live settings file is lost, `config/settings.current.example.json` contains the current known rover values. Copy it to `/opt/gimli/config/settings.json`, then update Wi-Fi/passwords if they changed.

## Fresh Pi Install

Copy the deploy package to the new Pi, unpack it, then run:

```bash
bash scripts/install.sh
sudo tailscale up --ssh
```

After install, edit `/opt/gimli/config/settings.json` or use the web UI for:

- Wi-Fi SSID/password
- camera IPs/passwords
- QGroundControl UDP targets
- VESC serial/CAN settings
- compass offset after mounting

Then reboot and run:

```bash
bash /opt/gimli/scripts/preflight-check.sh
```

## System Tuning

One-shot tuning script for latency and SD-card wear. Safe to run multiple times. Run once after a fresh install, then reboot.

```bash
sudo bash /opt/gimli/scripts/tune-pi.sh
sudo reboot
```

It sets:

- CPU governor to `performance` (less jitter under WebRTC + control bursts)
- UDP socket buffers (`net.core.rmem_max`, `net.core.wmem_max`)
- WiFi powersave off on all NetworkManager Wi-Fi profiles
- `journald` in RAM with 50 MB cap (reduces SD writes)
- `tmpfs` on `/tmp` (200 MB)
- `cake` qdisc on `tailscale0` (anti-bufferbloat for video + control)
- `Nice=-5` for `gimli-mavlink` and `gimli-rover`
- disables `bluetooth`, `ModemManager`, `apt-daily*` timers and similar
- `gpu_mem=16` in `config.txt` (headless, returns RAM to CPU)

Before running, sanity-check thermal throttling:

```bash
vcgencmd measure_temp
vcgencmd get_throttled   # 0x0 = no throttle
```

If `get_throttled` is non-zero under load, fix cooling first; software tuning will not save you from a thermally limited CPU.
                                  