/* Settings/telemetry panel for the rover. Video and web driving stay disabled here. */

(function () {
  const $ = (id) => document.getElementById(id);
  let rebootRequestInFlight = false;

  async function requestPiReboot(status) {
    if (rebootRequestInFlight) return;
    const yes = window.confirm("������������� Raspberry Pi? ���-��������� ������ �� 1-2 �������.");
    if (!yes) return;
    rebootRequestInFlight = true;
    if (status) status.textContent = "�������� ����������...";
    try {
      const resp = await fetch("/api/system/reboot", { method: "POST", cache: "no-store" });
      const data = await resp.json();
      if (status) status.textContent = data.ok ? "Pi ���������������..." : "���������� �� ������";
    } catch (e) {
      if (status) status.textContent = "Pi ���������������...";
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target && event.target.closest ? event.target.closest("#reboot-pi") : null;
    if (!target) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    requestPiReboot($("settings-status"));
  }, true);

  // ---- WebSocket �� ������� --------------------------------------------------
  const wsUrl = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/control";
  let ws = null;
  let lastSentAt = 0;
  const SEND_HZ = 30;
  const MIN_INTERVAL = 1000 / SEND_HZ;

  function connectWs() {
    ws = new WebSocket(wsUrl);
    ws.onopen = () => setWsState(true);
    ws.onclose = () => { setWsState(false); setTimeout(connectWs, 1000); };
    ws.onerror = () => { try { ws.close(); } catch (e) {} };
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data && data.state) updateReadout(data.state);
        const latency = (performance.now() - lastSentAt).toFixed(0) + " ��";
        setText("latency", latency);
        setText("osd-latency", latency);
      } catch (e) {}
    };
  }
  function setWsState(connected) {
    const dot = $("ws-dot"), txt = $("ws-text");
    dot?.classList.toggle("on", connected);
    dot?.classList.toggle("off", !connected);
    if (txt) txt.textContent = connected ? "��'���� �" : "���� ��'����";
    setText("osd-link", connected ? "������" : "������");
  }
  function updateReadout(s) {
    setText("r-throttle", (+s.throttle || 0).toFixed(2));
    setText("r-steering", (+s.steering || 0).toFixed(2));
    setText("r-left", (+s.left || 0).toFixed(2));
    setText("r-right", (+s.right || 0).toFixed(2));
  }
  function webControlEnabled() {
    const el = $("cfg-tx12-enabled");
    return !!(el && el.checked);
  }
  function send(msg) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (msg.cmd === "drive" && !webControlEnabled()) return;
    const now = performance.now();
    if (msg.cmd === "drive" && now - lastSentAt < MIN_INTERVAL) return;
    lastSentAt = now;
    ws.send(JSON.stringify(msg));
  }

  // ---- Web driving is intentionally disabled on this page ---------------------
  let current = { throttle: 0, steering: 0, active: false };

  // E-STOP (������ + �����)
  $("estop")?.addEventListener("click", () => send({ cmd: "stop" }));
  window.addEventListener("keydown", (e) => {
    if (e.key === " " || e.code === "Space") send({ cmd: "stop" });
  });

  setupSettingsPanel();
  setupCameraControls();
  setupCompassCalibration();
  startTelemetry();
  setWsState(false);
  setText("ws-text", "��������� Pi");
  setText("latency", "HTTP");

  // ---- Compass calibration --------------------------------------------------
  function setupCompassCalibration() {
    const btnStart = $("compass-cal-start");
    const btnAccept = $("compass-cal-accept");
    const btnCancel = $("compass-cal-cancel");
    const status = $("compass-cal-status");
    const qualityEl = $("compass-cal-quality");
    const rawEl = $("compass-cal-raw");
    const rangesEl = $("compass-cal-ranges");
    const minmaxEl = $("compass-cal-minmax");
    const warningsEl = $("compass-cal-warnings");
    const messageEl = $("compass-cal-message");
    if (!btnStart || !status) return;

    let pollTimer = null;
    let lastActive = false;

    const fmtAxis = (v) => Array.isArray(v) ? v.map((x) => Number(x || 0).toFixed(0)).join(" / ") : "�";
    const setTone = (el, quality) => {
      if (!el) return;
      el.classList.remove("ok", "warn", "bad");
      if (!quality) return;
      if (String(quality).includes("�����")) el.classList.add("ok");
      else if (String(quality).includes("������") || String(quality).includes("�����")) el.classList.add("bad");
      else el.classList.add("warn");
    };

    async function poke(path) {
      try {
        const r = await fetch(path, { method: "POST" });
        const j = await r.json();
        const text = j.message || (j.ok ? "������� ��������" : String(r.status));
        if (messageEl) messageEl.textContent = text;
        status.textContent = j.ok ? text : "�������: " + text;
        if (!j.ok) setTone(messageEl, "������");
      } catch (e) {
        status.textContent = "���: " + e;
        if (messageEl) messageEl.textContent = String(e);
      }
    }

    async function refresh() {
      try {
        const r = await fetch("/api/compass/status", { cache: "no-store" });
        const s = await r.json();
        const active = !!s.active;
        const progress = Number(s.progress || 0);
        const st = s.status || "�";
        const samples = s.samples != null ? `, samples=${s.samples}` : "";
        status.textContent = `${st}  ${progress}%${samples}`;
        if (qualityEl) {
          qualityEl.textContent = s.quality || (st === "success" ? `fitness ${s.fitness || "�"}` : "�");
          setTone(qualityEl, qualityEl.textContent);
        }
        if (rawEl) rawEl.textContent = fmtAxis(s.raw);
        if (rangesEl) rangesEl.textContent = fmtAxis(s.ranges);
        if (minmaxEl) {
          const mn = Array.isArray(s.min) ? fmtAxis(s.min) : "�";
          const mx = Array.isArray(s.max) ? fmtAxis(s.max) : "�";
          minmaxEl.textContent = `${mn} / ${mx}`;
        }
        if (warningsEl) {
          const warnings = Array.isArray(s.warnings) ? s.warnings.filter(Boolean) : [];
          warningsEl.textContent = warnings.length ? warnings.join(" ") : "�";
          setTone(warningsEl, warnings.length ? "������" : "�����");
        }
        btnAccept.disabled = !(st === "success" || (!active && progress >= 100));
        btnCancel.disabled = !active;
        btnStart.disabled = !!active;
        if (active !== lastActive) {
          lastActive = active;
          if (active && !pollTimer) {
            pollTimer = setInterval(refresh, 500);
          } else if (!active && pollTimer) {
            clearInterval(pollTimer); pollTimer = null;
          }
        }
      } catch (e) {
        status.textContent = "������ �����������";
      }
    }

    btnStart.addEventListener("click", async () => {
      await poke("/api/compass/start");
      lastActive = false;
      refresh();
      if (!pollTimer) pollTimer = setInterval(refresh, 500);
    });
    btnAccept.addEventListener("click", async () => {
      await poke("/api/compass/accept");
      setTimeout(refresh, 300);
    });
    btnCancel.addEventListener("click", async () => {
      await poke("/api/compass/cancel");
      setTimeout(refresh, 300);
    });
    const presetMatek = $("compass-preset-matek");
    const presetRotated = $("compass-preset-rotated");
    presetMatek?.addEventListener("click", () => {
      $("set-compass-x").value = "x";
      $("set-compass-y").value = "-y";
      $("set-compass-z").value = "-z";
      $("set-heading-offset").value = 0;
      if (messageEl) messageEl.textContent = "���������� Matek M10Q: X=+X, Y=-Y, Z=-Z, ����=0. ������� ���������.";
    });
    presetRotated?.addEventListener("click", () => {
      $("set-compass-x").value = "y";
      $("set-compass-y").value = "-x";
      $("set-compass-z").value = "-z";
      $("set-heading-offset").value = 0;
      if (messageEl) messageEl.textContent = "���������� Matek 90�: X=+Y, Y=-X, Z=-Z, ����=0. ������� ���������.";
    });

    refresh();
    setInterval(refresh, 3000); // ����� ���� ���� ������� ����� ����� reload
  }

  // ---- ������ ����������� ---------------------------------------------------
  function setupSettingsPanel() {
    const panel = $("settings-panel");
    const form = $("settings-form");
    const status = $("settings-status");
    let current = null;

    $("scan-wifi").addEventListener("click", scanWifi);
    $("connect-wifi").addEventListener("click", connectWifi);
    $("start-setup-ap").addEventListener("click", () => setupAp("start"));
    $("stop-setup-ap").addEventListener("click", () => setupAp("stop"));
    $("gps-off").addEventListener("click", () => setGpsMode("off", "disabled"));
    $("gps-auto").addEventListener("click", () => setGpsMode("auto", "auto"));
    $("gps-trusted").addEventListener("click", () => setGpsMode("gps", "trusted"));
    // ---- MAVLink helpers --------------------------------------------------
    $("set-mav-useip").addEventListener("click", async () => {
      try {
        const resp = await fetch("/api/network/client-ip");
        const data = await resp.json();
        if (data.ip) $("set-mav-host").value = data.ip;
      } catch (e) { /* no-op */ }
    });
    $("set-mav-extra-useip").addEventListener("click", async () => {
      try {
        const resp = await fetch("/api/network/client-ip");
        const data = await resp.json();
        if (data.ip) addMavExtraHost(data.ip);
      } catch (e) { /* no-op */ }
    });
    $("mav-restart").addEventListener("click", async () => {
      const s = $("mav-restart-status");
      s.textContent = "������������...";
      try {
        const resp = await fetch("/api/mavlink/restart", { method: "POST" });
        const data = await resp.json();
        s.textContent = data.ok ? "������������" : ("�������: " + (data.message || ""));
      } catch (e) { s.textContent = "�������: " + e; }
    });
    $("invert-throttle-axis")?.addEventListener("click", () => toggleCheckbox("set-mav-throttle-invert"));
    $("invert-steering-axis")?.addEventListener("click", () => toggleCheckbox("set-mav-steering-invert"));
    $("invert-left-motor")?.addEventListener("click", () => toggleCheckbox("set-vesc-left-invert"));
    $("invert-right-motor")?.addEventListener("click", () => toggleCheckbox("set-vesc-right-invert"));

    $("reboot-pi").addEventListener("click", async () => {
      const yes = window.confirm("������������� Raspberry Pi? ���-��������� ������ �� 1-2 �������.");
      if (!yes) return;
      status.textContent = "�������� ����������...";
      try {
        const resp = await fetch("/api/system/reboot", { method: "POST" });
        const data = await resp.json();
        status.textContent = data.ok ? "Pi ���������������..." : "���������� �� ������";
      } catch (e) {
        status.textContent = "Pi ���������������...";
      }
    });

    $("poweroff-pi").addEventListener("click", async () => {
      const yes = window.confirm("�������� Raspberry Pi? ϳ��� ����� ���-��������� ������ �� ������� ��������� ��������.");
      if (!yes) return;
      status.textContent = "�������� ���������...";
      try {
        const resp = await fetch("/api/system/poweroff", { method: "POST" });
        const data = await resp.json();
        status.textContent = data.ok ? "Pi ����������..." : "��������� �� �������";
      } catch (e) {
        status.textContent = "Pi ����������...";
      }
    });

    const applyVideoProfile = $("apply-video-profile");
    applyVideoProfile?.addEventListener("click", async () => {
      const videoStatus = $("video-profile-status") || status;
      videoStatus.textContent = "���������� ������...";
      try {
        const resp = await fetch("/api/cameras/tune-video", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            profile: $("set-profile").value,
            target_kbps: parseInt($("set-kbps").value, 10) || 900,
          }),
        });
        const data = await resp.json();
        const cams = data.results || {};
        const parts = Object.keys(cams).map((name) => name + ": " + (cams[name].ok ? "ok" : "�������"));
        videoStatus.textContent = data.ok ? "�����������: " + parts.join(", ") : "�� �������: " + parts.join(", ");
        if (data.settings) {
          current = data.settings;
          fillSettings(current);
        }
      } catch (e) {
        videoStatus.textContent = "�������: " + e;
      }
    });

    async function loadSettings() {
      status.textContent = "����������...";
      try {
        const resp = await fetch("/api/settings");
        current = await resp.json();
        fillSettings(current);
        status.textContent = "";
      } catch (e) {
        status.textContent = "�� ������� �����������";
      }
    }

    function fillSettings(settings) {
      ["cam1", "cam2"].forEach((name) => {
        const cam = settings.cameras[name] || {};
        const root = panel.querySelector('[data-camera="' + name + '"]');
        root.querySelector('[data-field="enabled"]').checked = !!cam.enabled;
        ["label", "host", "username", "main_path", "sub_path", "preferred"].forEach((field) => {
          root.querySelector('[data-field="' + field + '"]').value = cam[field] || "";
        });
        const pass = root.querySelector('[data-field="password"]');
        pass.value = "";
        pass.placeholder = cam.password_set ? "���������; �������� = �� �����" : "�� ������";
        ensureCameraActions(root, name);
      });
      const network = settings.network || {};
      const power = settings.power || {};
      const nav = settings.navigation || {};
      const mav = settings.mavlink || {};
      $("set-profile").value = network.profile || "balanced";
      $("set-kbps").value = network.target_kbps || 1800;
      $("set-link-mode").value = network.link_mode || "auto";
      $("set-wifi-ssid").value = network.wifi_ssid || "";
      $("set-wifi-password").value = "";
      $("set-wifi-password").placeholder = network.wifi_password_set ? "���������; �������� = �� �����" : "�� ������";
      const ap = network.setup_ap || {};
      $("set-ap-enabled").checked = ap.enabled !== false;
      $("set-ap-ssid").value = ap.ssid || "Gimli-Rover-Setup";
      $("set-ap-password").value = "";
      $("set-ap-password").placeholder = ap.password_set ? "���������; �������� = �� �����" : "gimli1234";
      $("set-voltage").value = power.battery_voltage == null ? "" : power.battery_voltage;
      $("set-low-voltage").value = power.low_voltage || 11.1;
      const currentSensor = power.current_sensor || {};
      $("set-current-enabled").checked = currentSensor.enabled !== false;
      $("set-current-address").value = currentSensor.address || "0x45";
      $("set-current-lsb").value = currentSensor.current_lsb_a || 0.001;
      $("set-gps-source").value = nav.source || "auto";
      $("set-gps-trust").value = nav.gps_trust || "auto";
      $("set-gps-enabled").checked = !!nav.gps_enabled;
      $("set-gps-fix").value = String(nav.fix_type || 0);
      $("set-gps-sat").value = nav.satellites || 0;
      $("set-gps-lat").value = nav.latitude == null ? "" : nav.latitude;
      $("set-gps-lon").value = nav.longitude == null ? "" : nav.longitude;
      $("set-gps-alt").value = nav.altitude_m || 0;
      $("set-home-lat").value = nav.home_latitude == null ? "" : nav.home_latitude;
      $("set-home-lon").value = nav.home_longitude == null ? "" : nav.home_longitude;
      $("set-max-jump").value = nav.max_jump_km || 5;
      $("set-heading").value = nav.heading_deg == null ? "" : nav.heading_deg;
      $("set-heading-offset").value = nav.heading_offset_deg || 0;
      $("set-heading-smoothing").value = nav.heading_smoothing == null ? 0.25 : nav.heading_smoothing;
      $("set-compass-x").value = nav.compass_x_axis || "x";
      $("set-compass-y").value = nav.compass_y_axis || "y";
      $("set-compass-z").value = nav.compass_z_axis || "z";
      $("set-groundspeed").value = nav.groundspeed_m_s || 0;
      $("set-mav-enabled").checked = !!mav.enabled;
      $("set-mav-system").value = mav.system_id || 1;
      $("set-mav-component").value = mav.component_id || 1;
      const connStr = mav.connection || "";
      $("set-mav-connection").value = connStr;
      const m = /^udpout:([^:]+):(\d+)$/.exec(connStr);
      if (m) {
        $("set-mav-host").value = m[1];
        $("set-mav-port").value = m[2];
      } else {
        $("set-mav-host").value = "";
        $("set-mav-port").value = 14550;
      }
      $("set-mav-name").value = mav.vehicle_name || "";
      $("set-mav-extra").value = Array.isArray(mav.extra_connections) ? mav.extra_connections.join(", ") : (mav.extra_connections || "");
      const mavControl = mav.control || {};
      $("set-mav-throttle-axis").value = mavControl.throttle_axis || "y";
      $("set-mav-steering-axis").value = mavControl.steering_axis || "x";
      $("set-mav-throttle-invert").checked = !!mavControl.throttle_invert;
      $("set-mav-steering-invert").checked = !!mavControl.steering_invert;
      $("set-mav-throttle-scale").value = mavControl.throttle_scale == null ? 1 : mavControl.throttle_scale;
      $("set-mav-steering-scale").value = mavControl.steering_scale == null ? 1 : mavControl.steering_scale;
      const motors = settings.motors || {};
      $("set-mock").checked = !!motors.mock;
      $("set-watchdog").value = motors.watchdog_timeout_s || 0.5;
      $("set-motor-type").value = motors.type || "gpio";
      const vesc = motors.vesc || {};
      $("set-vesc-port").value = vesc.port || vesc.left_port || "";
      $("set-vesc-left-can").value = vesc.left_can_id ?? "";
      $("set-vesc-right-can").value = vesc.right_can_id ?? "";
      $("set-vesc-mode").value = vesc.control_mode || "current";
      $("set-vesc-current").value = vesc.max_current_a || 20;
      $("set-vesc-rpm").value = vesc.max_rpm || 1200;
      $("set-vesc-start-current").value = vesc.start_current_a == null ? 0 : vesc.start_current_a;
      $("set-vesc-current-expo").value = vesc.current_expo == null ? 1 : vesc.current_expo;
      $("set-vesc-ramp").value = vesc.command_ramp_per_s == null ? 0 : vesc.command_ramp_per_s;
      $("set-vesc-brake-current").value = vesc.failsafe_brake_current_a == null ? 12 : vesc.failsafe_brake_current_a;
      $("set-vesc-neutral-deadzone").value = vesc.neutral_deadzone == null ? 0.06 : vesc.neutral_deadzone;
      $("set-vesc-baud").value = vesc.baud || 115200;
      $("set-vesc-duty").value = vesc.max_duty || 0.12;
      $("set-vesc-left-invert").checked = !!vesc.left_invert;
      $("set-vesc-right-invert").checked = !!vesc.right_invert;
      const pins = motors.pins || {};
      panel.querySelectorAll("[data-pin]").forEach((input) => {
        input.value = pins[input.dataset.pin] || 0;
      });
    }

    function readSettings() {
      const next = current || { cameras: {}, network: {}, power: {}, motors: { pins: {} } };
      next.cameras = next.cameras || {};
      ["cam1", "cam2"].forEach((name) => {
        const root = panel.querySelector('[data-camera="' + name + '"]');
        const cam = next.cameras[name] || {};
        cam.enabled = root.querySelector('[data-field="enabled"]').checked;
        ["label", "host", "username", "password", "main_path", "sub_path", "preferred"].forEach((field) => {
          cam[field] = root.querySelector('[data-field="' + field + '"]').value.trim();
        });
        next.cameras[name] = cam;
      });
      next.network = next.network || {};
      next.network.profile = $("set-profile").value;
      next.network.target_kbps = parseInt($("set-kbps").value, 10) || 1800;
      next.network.link_mode = $("set-link-mode").value;
      next.network.wifi_ssid = $("set-wifi-ssid").value.trim();
      next.network.wifi_password = $("set-wifi-password").value;
      next.network.setup_ap = next.network.setup_ap || {};
      next.network.setup_ap.enabled = $("set-ap-enabled").checked;
      next.network.setup_ap.ssid = $("set-ap-ssid").value.trim() || "Gimli-Rover-Setup";
      next.network.setup_ap.password = $("set-ap-password").value;
      next.power = next.power || {};
      next.power.battery_voltage = $("set-voltage").value === "" ? null : parseFloat($("set-voltage").value);
      next.power.low_voltage = parseFloat($("set-low-voltage").value) || 11.1;
      next.power.current_sensor = next.power.current_sensor || {};
      next.power.current_sensor.enabled = $("set-current-enabled").checked;
      next.power.current_sensor.type = "ina228";
      next.power.current_sensor.bus = "/dev/i2c-1";
      next.power.current_sensor.address = $("set-current-address").value.trim() || "0x45";
      next.power.current_sensor.current_lsb_a = parseFloat($("set-current-lsb").value) || 0.001;
      next.navigation = next.navigation || {};
      next.navigation.source = $("set-gps-source").value;
      next.navigation.gps_trust = $("set-gps-trust").value;
      next.navigation.gps_enabled = $("set-gps-enabled").checked;
      next.navigation.fix_type = parseInt($("set-gps-fix").value, 10) || 0;
      next.navigation.satellites = parseInt($("set-gps-sat").value, 10) || 0;
      next.navigation.latitude = $("set-gps-lat").value === "" ? null : parseFloat($("set-gps-lat").value);
      next.navigation.longitude = $("set-gps-lon").value === "" ? null : parseFloat($("set-gps-lon").value);
      next.navigation.altitude_m = parseFloat($("set-gps-alt").value) || 0;
      next.navigation.home_latitude = $("set-home-lat").value === "" ? null : parseFloat($("set-home-lat").value);
      next.navigation.home_longitude = $("set-home-lon").value === "" ? null : parseFloat($("set-home-lon").value);
      next.navigation.max_jump_km = parseFloat($("set-max-jump").value) || 5;
      next.navigation.heading_deg = $("set-heading").value === "" ? null : parseFloat($("set-heading").value);
      next.navigation.heading_offset_deg = parseFloat($("set-heading-offset").value) || 0;
      next.navigation.heading_smoothing = parseFloat($("set-heading-smoothing").value);
      if (Number.isNaN(next.navigation.heading_smoothing)) next.navigation.heading_smoothing = 0.25;
      next.navigation.compass_x_axis = $("set-compass-x").value;
      next.navigation.compass_y_axis = $("set-compass-y").value;
      next.navigation.compass_z_axis = $("set-compass-z").value;
      next.navigation.groundspeed_m_s = parseFloat($("set-groundspeed").value) || 0;
      next.mavlink = next.mavlink || {};
      next.mavlink.enabled = $("set-mav-enabled").checked;
      next.mavlink.system_id = parseInt($("set-mav-system").value, 10) || 1;
      next.mavlink.component_id = parseInt($("set-mav-component").value, 10) || 1;
      const host = $("set-mav-host").value.trim();
      const port = parseInt($("set-mav-port").value, 10) || 14550;
      next.mavlink.connection = host ? ("udpout:" + host + ":" + port) : $("set-mav-connection").value.trim();
      next.mavlink.extra_connections = $("set-mav-extra").value
        .split(/[\n,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      next.mavlink.vehicle_name = $("set-mav-name").value.trim();
      next.mavlink.control = next.mavlink.control || {};
      next.mavlink.control.throttle_axis = $("set-mav-throttle-axis").value;
      next.mavlink.control.steering_axis = $("set-mav-steering-axis").value;
      next.mavlink.control.throttle_invert = $("set-mav-throttle-invert").checked;
      next.mavlink.control.steering_invert = $("set-mav-steering-invert").checked;
      next.mavlink.control.throttle_scale = parseFloat($("set-mav-throttle-scale").value) || 1;
      next.mavlink.control.steering_scale = parseFloat($("set-mav-steering-scale").value) || 1;
      next.motors = next.motors || {};
      next.motors.type = $("set-motor-type").value;
      next.motors.mock = $("set-mock").checked;
      next.motors.watchdog_timeout_s = parseFloat($("set-watchdog").value) || 0.5;
      next.motors.vesc = next.motors.vesc || {};
      next.motors.vesc.port = $("set-vesc-port").value.trim();
      next.motors.vesc.left_port = $("set-vesc-port").value.trim();
      next.motors.vesc.right_port = "";
      next.motors.vesc.left_can_id = $("set-vesc-left-can").value === "" ? null : parseInt($("set-vesc-left-can").value, 10);
      next.motors.vesc.right_can_id = $("set-vesc-right-can").value === "" ? null : parseInt($("set-vesc-right-can").value, 10);
      next.motors.vesc.control_mode = $("set-vesc-mode").value;
      next.motors.vesc.max_current_a = parseFloat($("set-vesc-current").value) || 20;
      next.motors.vesc.max_rpm = parseFloat($("set-vesc-rpm").value) || 1200;
      next.motors.vesc.start_current_a = parseFloat($("set-vesc-start-current").value) || 0;
      next.motors.vesc.current_expo = parseFloat($("set-vesc-current-expo").value) || 1;
      next.motors.vesc.command_ramp_per_s = parseFloat($("set-vesc-ramp").value) || 0;
      next.motors.vesc.failsafe_brake_current_a = parseFloat($("set-vesc-brake-current").value) || 0;
      next.motors.vesc.neutral_deadzone = parseFloat($("set-vesc-neutral-deadzone").value) || 0;
      next.motors.vesc.baud = parseInt($("set-vesc-baud").value, 10) || 115200;
      next.motors.vesc.max_duty = parseFloat($("set-vesc-duty").value) || 0.12;
      next.motors.vesc.left_invert = $("set-vesc-left-invert").checked;
      next.motors.vesc.right_invert = $("set-vesc-right-invert").checked;
      next.motors.pins = next.motors.pins || {};
      panel.querySelectorAll("[data-pin]").forEach((input) => {
        next.motors.pins[input.dataset.pin] = parseInt(input.value, 10) || 0;
      });
      return next;
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      status.textContent = "�������...";
      try {
        const payload = readSettings();
        const resp = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || data.ok === false) throw new Error(data.message || `HTTP ${resp.status}`);
        current = data.settings;
        fillSettings(current);
        status.textContent = data.go2rtc_restarted ? "���������, ������� ������������" : "���������; ������� ����������";
        updateTelemetry(data.settings);
      } catch (err) {
        status.textContent = "�� ������� ��������: " + (err?.message || err);
      }
    });

    loadNetworkStatus();
    loadSettings();

    async function setGpsMode(source, trust) {
      if (!current) await loadSettings();
      current = current || {};
      current.navigation = current.navigation || {};
      current.navigation.source = source;
      current.navigation.gps_trust = trust;
      fillSettings(current);
      status.textContent = "������� GPS...";
      try {
        const resp = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(readSettings()),
        });
        const data = await resp.json();
        current = data.settings;
        fillSettings(current);
        status.textContent = source === "off" ? "GPS ��������" : "GPS ���������";
      } catch (e) {
        status.textContent = "GPS �� ���������";
      }
    }
  }

  async function scanWifi() {
    const status = $("wifi-scan-status");
    const list = $("wifi-networks");
    status.textContent = "������...";
    list.innerHTML = "";
    try {
      const resp = await fetch("/api/network/wifi-scan");
      const data = await resp.json();
      const networks = data.networks || [];
      status.textContent = networks.length ? networks.length + " �����" : "����� �� ��������";
      networks.forEach((net) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "wifi-network";
        item.innerHTML =
          '<span>' + escapeHtml(net.ssid || "") + '</span>' +
          '<span>' + (net.signal == null ? "�" : net.signal.toFixed(0) + " dBm") + '</span>' +
          '<span>' + (net.security || "open") + '</span>';
        item.addEventListener("click", () => {
          $("set-wifi-ssid").value = net.ssid || "";
          $("set-link-mode").value = "wifi";
        });
        list.appendChild(item);
      });
    } catch (e) {
      status.textContent = "���������� �� �������";
    }
  }

  async function connectWifi() {
    const status = $("wifi-scan-status");
    const ssid = $("set-wifi-ssid").value.trim();
    const password = $("set-wifi-password").value;
    if (!ssid) {
      status.textContent = "����� SSID";
      return;
    }
    const yes = window.confirm("ϳ���������� �� Wi-Fi '" + ssid + "'? ��'���� ���� �������� �� ����� ������.");
    if (!yes) return;
    status.textContent = "��������...";
    try {
      const resp = await fetch("/api/network/wifi-connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ssid, password, interface: "wlan0" }),
      });
      const data = await resp.json();
      status.textContent = data.ok ? "���������" : ("�������: " + (data.message || ""));
      setTimeout(loadNetworkStatus, 2000);
    } catch (e) {
      status.textContent = "������� ����������; ������ ��'����";
    }
  }

  async function setupAp(action) {
    const status = $("wifi-scan-status");
    const text = action === "start" ? "�������� setup AP? �������� Wi-Fi ���� �����������." : "�������� setup AP?";
    if (!window.confirm(text)) return;
    status.textContent = action === "start" ? "������ AP..." : "������� AP...";
    try {
      const resp = await fetch("/api/network/setup-ap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await resp.json();
      status.textContent = data.ok ? data.message : ("�������: " + (data.message || ""));
      setTimeout(loadNetworkStatus, 2000);
    } catch (e) {
      status.textContent = "������� ����������; ������ ��'����";
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[ch]);
  }

  function toggleCheckbox(id) {
    const input = $(id);
    if (!input) return;
    input.checked = !input.checked;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function setStatusChip(id, value, tone) {
    const el = $(id);
    if (!el) return;
    el.textContent = value;
    el.classList.toggle("ok", tone === "ok");
    el.classList.toggle("bad", tone === "bad");
  }

  function addMavExtraHost(host) {
    const el = $("set-mav-extra");
    const value = String(host || "").trim();
    if (!value) return;
    const normalized = value.includes(":") ? value : value + ":14550";
    const parts = el.value
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const keys = new Set(parts.map((s) => s.replace(/^udpout:/, "")));
    if (!keys.has(normalized)) parts.push(normalized);
    el.value = parts.join(", ");
  }

  async function loadNetworkStatus() {
    try {
      const resp = await fetch("/api/network/status");
      const data = await resp.json();
      const names = (data.interfaces || []).map((i) => i.ifname).filter(Boolean).join(", ");
      $("network-status").textContent = names ? "������: " + names : "������: �";
    } catch (e) {}
  }

  // ---- Telemetry / OSD -------------------------------------------------------
  function startTelemetry() {
    const tick = async () => {
      const started = performance.now();
      try {
        const resp = await fetch("/api/telemetry");
        const latencyMs = Math.round(performance.now() - started);
        updateTelemetry(await resp.json());
        setStatusChip("status-latency", String(latencyMs), latencyMs <= 250 ? "ok" : (latencyMs <= 800 ? "" : "bad"));
      } catch (e) {
        setStatusChip("status-latency", "OFF", "bad");
      }
    };
    tick();
    setInterval(tick, 3000);
  }

  function updateTelemetry(t) {
    const link = t.link || t.network || {};
    const power = t.power || {};
    const nav = t.navigation || {};
    const motors = t.motors || {};
    const control = t.control || {};
    const rc = t.rc_input || {};
    const volts = power.battery_voltage;
    const armText = control.armed == null ? "�" : (control.armed ? "����." : "����.");
    const rcText = rc.ok == null ? "�" : (rc.ok ? "OK" : "LOST");
    const modeText = control.daynight ? (control.daynight === "night" ? "��" : "����") : "�";
    const buttonsText = control.buttons_hex || "�";
    const sats = Number(nav.satellites || 0);
    setStatusChip("status-sats", sats ? String(sats) : "�", sats >= 8 ? "ok" : (sats > 0 ? "" : "bad"));
    setStatusChip("status-voltage", volts == null ? "�" : Number(volts).toFixed(1), volts != null && volts <= (power.low_voltage || 0) ? "bad" : "ok");
    setText("osd-link", (link.link_mode || "auto") + " / " + (link.profile || "balanced") + " / " + (link.target_kbps || 0) + " kbps");
    setText("osd-voltage", volts == null ? "� V" : Number(volts).toFixed(1) + " V");
    setText("osd-current", power.current_a == null ? "� A" : Number(power.current_a).toFixed(2) + " A");
    setText("osd-power", power.power_w == null ? "� W" : Number(power.power_w).toFixed(1) + " W");
    setText("osd-arm", armText);
    setText("osd-rc", rcText);
    setText("osd-daynight", modeText);
    setText("osd-buttons", buttonsText);
    setText("top-arm", armText);
    setText("top-rc", rcText);
    setText("top-daynight", modeText);
    setText("top-buttons", buttonsText);
    setText("osd-gps", gpsText(nav));
    setText("osd-heading", nav.heading_deg == null ? "��" : Number(nav.heading_deg).toFixed(0) + "�");
    const vesc = motors.vesc || {};
    const motorMode = motors.type === "vesc" ? (vesc.control_mode || "current") : (motors.type || "gpio");
    setText("osd-motor", (motors.mock ? "����" : motorMode) +
      (motors.type === "vesc" && vesc.max_rpm ? " / " + Number(vesc.max_rpm).toFixed(0) + " rpm" : ""));
    if (t.video && t.video.active_stream) applyActiveCamera(t.video.active_stream, false);
  }

  function cameraText(cam) {
    if (!cam) return "�";
    if (!cam.enabled) return "����.";
    return (cam.host || "no host") + " / " + (cam.preferred || "main");
  }

  function gpsText(nav) {
    if (!nav || !nav.gps_enabled) return nav && nav.gps_warning ? nav.gps_warning : "����.";
    const fix = Number(nav.fix_type || 0);
    const sats = Number(nav.satellites || 0);
    if (fix < 2) return "���� ���� / " + sats + " ���.";
    const label = fix >= 3 ? "3D" : "2D";
    if (nav.latitude == null || nav.longitude == null) return label + " / " + sats + " ���.";
    return label + " / " + sats + " ���. / " + Number(nav.latitude).toFixed(5) + ", " + Number(nav.longitude).toFixed(5);
  }

  // ---- Video: two proxied MJPEG streams from the backend ---------------------
  const cameraRefreshTimers = {};
  const cameraPcs = {};

  async function startCamera(videoEl, streamName) {
    const fallback = $(streamName + "-fallback");
    if (!fallback) return;
    stopCamera(streamName, videoEl);
    fallback.removeAttribute("src");
    fallback.classList.add("hidden");
    try {
      await startWebrtcCamera(videoEl, streamName, fallback);
    } catch (e) {
      console.warn("webrtc camera fallback:", streamName, e);
      startMjpegFallback(fallback, streamName);
    }
  }

  function stopCamera(streamName, videoEl) {
    if (cameraRefreshTimers[streamName]) clearInterval(cameraRefreshTimers[streamName]);
    delete cameraRefreshTimers[streamName];
    if (cameraPcs[streamName]) {
      try { cameraPcs[streamName].close(); } catch (e) {}
      delete cameraPcs[streamName];
    }
    if (videoEl) {
      try { videoEl.pause(); videoEl.removeAttribute("src"); videoEl.srcObject = null; } catch (e) {}
    }
    const fallback = $(streamName + "-fallback");
    if (fallback) {
      fallback.onerror = null;
      fallback.classList.add("hidden");
      fallback.removeAttribute("src");
    }
  }

  async function startWebrtcCamera(videoEl, streamName, fallback) {
    const pc = new RTCPeerConnection({ iceServers: [] });
    cameraPcs[streamName] = pc;
    let gotTrack = false;
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = (event) => {
      gotTrack = true;
      videoEl.srcObject = event.streams[0];
      videoEl.play().catch(() => {});
      fallback.classList.add("hidden");
      fallback.removeAttribute("src");
    };
    pc.onconnectionstatechange = () => {
      if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
        setTimeout(() => startCamera(videoEl, streamName).catch(() => startMjpegFallback(fallback, streamName)), 1500);
      }
    };
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const resp = await fetch("/api/webrtc?src=" + encodeURIComponent(streamName), {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: offer.sdp,
    });
    if (!resp.ok) throw new Error(await resp.text());
    const answer = await resp.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
    setTimeout(() => {
      if (!gotTrack && cameraPcs[streamName] === pc) {
        try { pc.close(); } catch (e) {}
        delete cameraPcs[streamName];
        startMjpegFallback(fallback, streamName);
      }
    }, 8000);
  }

  function startMjpegFallback(fallback, streamName) {
    restartMjpeg(fallback, streamName);
    fallback.onerror = () => restartMjpeg(fallback, streamName);
    if (cameraRefreshTimers[streamName]) clearInterval(cameraRefreshTimers[streamName]);
    cameraRefreshTimers[streamName] = setInterval(() => restartMjpeg(fallback, streamName), 30000);
    fallback.classList.remove("hidden");
  }

  function restartMjpeg(img, streamName) {
    const src = "/api/video/" + encodeURIComponent(streamName) + ".mjpeg?t=" + Date.now();
    img.removeAttribute("src");
    setTimeout(() => { img.src = src; }, 150);
  }

  function setupCameraSwitcher() {
    $("active-cam1").addEventListener("click", () => setActiveCamera("cam1"));
    $("active-cam2").addEventListener("click", () => setActiveCamera("cam2"));
  }

  let audioPc = null;
  let audioEl = null;
  let audioEnabled = false;
  let currentAudioStream = "active_audio";
  let activeCameraName = "cam1";
  let audioCameraName = "";

  function setupAudio() {
    const btn = $("audio-toggle");
    const status = $("audio-status");
    if (!btn || !status) return;
    btn.addEventListener("click", async () => {
      audioEnabled = !audioEnabled;
      if (audioEnabled) {
        btn.classList.add("primary");
        status.textContent = "�������� ����...";
        await startAudio("active_audio");
      } else {
        btn.classList.remove("primary");
        stopAudio();
        status.textContent = "���� ����.";
      }
    });
  }

  async function startAudio(streamName) {
    stopAudio();
    currentAudioStream = streamName || "active_audio";
    audioCameraName = activeCameraName;
    const status = $("audio-status");
    try {
      audioEl = new Audio();
      audioEl.autoplay = true;
      audioEl.controls = false;
      audioPc = new RTCPeerConnection({ iceServers: [] });
      audioPc.addTransceiver("audio", { direction: "recvonly" });
      audioPc.ontrack = (event) => {
        audioEl.srcObject = event.streams[0];
        audioEl.play().catch(() => {});
      };
      const offer = await audioPc.createOffer();
      await audioPc.setLocalDescription(offer);
      const resp = await fetch("/api/webrtc?src=" + encodeURIComponent(currentAudioStream), {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: offer.sdp,
      });
      if (!resp.ok) throw new Error(await resp.text());
      const answer = await resp.text();
      await audioPc.setRemoteDescription({ type: "answer", sdp: answer });
      if (status) status.textContent = "����: " + currentAudioStream;
    } catch (e) {
      if (status) status.textContent = "���� �����������";
      console.warn("audio:", e);
      stopAudio();
      audioEnabled = false;
      const btn = $("audio-toggle");
      if (btn) btn.classList.remove("primary");
    }
  }

  function stopAudio() {
    if (audioPc) {
      try { audioPc.close(); } catch (e) {}
    }
    audioPc = null;
    audioCameraName = "";
    if (audioEl) {
      try { audioEl.pause(); audioEl.srcObject = null; } catch (e) {}
    }
    audioEl = null;
  }

  function ensureCameraActions(root, camera) {
    if (!root || root.querySelector("[data-camera-actions]")) return;
    const actions = document.createElement("div");
    actions.className = "camera-actions";
    actions.dataset.cameraActions = "1";
    actions.innerHTML = `
      <div class="camera-action-title">${camera === "cam2" ? "����� ������" : "������� ������"}</div>
      <button type="button" data-light="forceon">ForceOn</button>
      <button type="button" data-light="manual">Manual</button>
      <button type="button" data-light="auto">Auto</button>
      <button type="button" data-light="smart">Smart/AI</button>
      <button type="button" data-light="forceoff">ForceOff</button>
      <button type="button" data-light="off">Off</button>
      <label>����� <input type="range" min="0" max="100" value="80" data-light-level></label>
      <button type="button" data-daynight="day">����</button>
      <button type="button" data-daynight="night">ͳ�</button>
      <button type="button" data-daynight="auto">����/�� ����</button>
      <button type="button" data-sync-cameras>�����. ���</button>
      <span data-camera-status></span>
    `;
    root.appendChild(actions);
  }

  function setupCameraControls() {
    document.addEventListener("click", async (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest("[data-light], [data-daynight], [data-sync-cameras]")
        : null;
      if (!button) return;
      const root = button.closest("[data-camera]");
      const status = (root && root.querySelector("[data-camera-status]")) || $("video-profile-status") || $("settings-status");
      if (status) status.textContent = "��������...";
      try {
        if (button.hasAttribute("data-sync-cameras")) {
          const resp = await fetch("/api/cameras/sync-time", { method: "POST" });
          const data = await resp.json();
          if (status) status.textContent = data.ok ? "��� �������������" : "������� ����";
          return;
        }
        const camera = root && root.dataset.camera;
        const level = root && root.querySelector("[data-light-level]");
        const body = button.hasAttribute("data-daynight")
          ? { action: "daynight", mode: button.dataset.daynight }
          : { action: "light", mode: button.dataset.light, level: parseInt(level?.value, 10) || 80 };
        const resp = await fetch("/api/camera/" + camera + "/control", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = await resp.json();
        if (status) status.textContent = data.ok ? "��" : "�������";
        if (!data.ok && data.message) console.warn("camera control:", data.message);
      } catch (e) {
        if (status) status.textContent = "���� ��'����";
      }
    });
  }

  function applyActiveCamera(name, saving) {
    const active = name === "cam2" ? "cam2" : "cam1";
    activeCameraName = active;
    const inactive = active === "cam1" ? "cam2" : "cam1";
    $("tile-" + active)?.classList.add("active");
    $("tile-" + active)?.classList.remove("pip");
    $("tile-" + inactive)?.classList.add("pip");
    $("tile-" + inactive)?.classList.remove("active");
    $("active-cam1")?.classList.toggle("primary", active === "cam1");
    $("active-cam2")?.classList.toggle("primary", active === "cam2");
    const label = active === "cam2" ? "�����" : "�������";
    if ($("active-camera-status")) $("active-camera-status").textContent = (saving ? "�������: " : "�������: ") + label;
    if (audioEnabled && audioCameraName !== active) startAudio("active_audio");
  }

  async function setActiveCamera(name) {
    const active = name === "cam2" ? "cam2" : "cam1";
    applyActiveCamera(active, true);
    try {
      const resp = await fetch("/api/settings");
      const settings = await resp.json();
      settings.video = settings.video || {};
      settings.video.active_stream = active;
      const save = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      const data = await save.json();
      applyActiveCamera((data.settings && data.settings.video && data.settings.video.active_stream) || active, false);
    } catch (e) {
      $("active-camera-status").textContent = "����������� �� �������";
    }
  }

  stopCamera("cam1", $("cam1"));
  stopCamera("cam2", $("cam2"));
})();
