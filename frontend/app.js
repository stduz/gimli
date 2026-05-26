/* Головний скрипт: WebSocket -> бекенд, WebRTC -> go2rtc, джойстик/TX12 -> команди. */

(function () {
  const $ = (id) => document.getElementById(id);
  let rebootRequestInFlight = false;

  async function requestPiReboot(status) {
    if (rebootRequestInFlight) return;
    const yes = window.confirm("Перезапустити Raspberry Pi? Веб-інтерфейс зникне на 1-2 хвилини.");
    if (!yes) return;
    rebootRequestInFlight = true;
    if (status) status.textContent = "надсилаю перезапуск...";
    try {
      const resp = await fetch("/api/system/reboot", { method: "POST", cache: "no-store" });
      const data = await resp.json();
      if (status) status.textContent = data.ok ? "Pi перезапускається..." : "перезапуск не вдався";
    } catch (e) {
      if (status) status.textContent = "Pi перезапускається...";
    }
  }

  document.addEventListener("click", (event) => {
    const target = event.target && event.target.closest ? event.target.closest("#reboot-pi") : null;
    if (!target) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    requestPiReboot($("settings-status"));
  }, true);

  // ---- WebSocket до бекенду --------------------------------------------------
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
        const latency = (performance.now() - lastSentAt).toFixed(0) + " мс";
        $("latency").textContent = latency;
        $("osd-latency").textContent = latency;
      } catch (e) {}
    };
  }
  function setWsState(connected) {
    const dot = $("ws-dot"), txt = $("ws-text");
    dot.classList.toggle("on", connected);
    dot.classList.toggle("off", !connected);
    txt.textContent = connected ? "зв'язок є" : "нема зв'язку";
    $("osd-link").textContent = connected ? "онлайн" : "офлайн";
  }
  function updateReadout(s) {
    $("r-throttle").textContent = (+s.throttle || 0).toFixed(2);
    $("r-steering").textContent = (+s.steering || 0).toFixed(2);
    $("r-left").textContent = (+s.left || 0).toFixed(2);
    $("r-right").textContent = (+s.right || 0).toFixed(2);
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

  // ---- джойстик/TX12 → команды (с heartbeat) ---------------------------------
  let current = { throttle: 0, steering: 0, active: false };
  setInterval(() => {
    if (current.active) send({ cmd: "drive", throttle: current.throttle, steering: current.steering });
  }, 100);

  const j = new Joystick($("joystick"), $("joy-knob"),
    ({ throttle, steering }) => {
      current = { throttle, steering, active: true };
      send({ cmd: "drive", throttle, steering });
    },
    () => {
      current = { throttle: 0, steering: 0, active: false };
      send({ cmd: "drive", throttle: 0, steering: 0 });
    },
  );

  // E-STOP (кнопка + пробіл)
  $("estop").addEventListener("click", () => send({ cmd: "stop" }));
  window.addEventListener("keydown", (e) => {
    if (e.key === " " || e.code === "Space") send({ cmd: "stop" });
  });

  setupTx12Panel(j);
  setupSettingsPanel();
  setupCameraSwitcher();
  setupAudio();
  setupCameraControls();
  setupCompassCalibration();
  startTelemetry();
  connectWs();

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

    const fmtAxis = (v) => Array.isArray(v) ? v.map((x) => Number(x || 0).toFixed(0)).join(" / ") : "—";
    const setTone = (el, quality) => {
      if (!el) return;
      el.classList.remove("ok", "warn", "bad");
      if (!quality) return;
      if (String(quality).includes("добре")) el.classList.add("ok");
      else if (String(quality).includes("погано") || String(quality).includes("метал")) el.classList.add("bad");
      else el.classList.add("warn");
    };

    async function poke(path) {
      try {
        const r = await fetch(path, { method: "POST" });
        const j = await r.json();
        const text = j.message || (j.ok ? "команду прийнято" : String(r.status));
        if (messageEl) messageEl.textContent = text;
        status.textContent = j.ok ? text : "помилка: " + text;
        if (!j.ok) setTone(messageEl, "погано");
      } catch (e) {
        status.textContent = "збій: " + e;
        if (messageEl) messageEl.textContent = String(e);
      }
    }

    async function refresh() {
      try {
        const r = await fetch("/api/compass/status", { cache: "no-store" });
        const s = await r.json();
        const active = !!s.active;
        const progress = Number(s.progress || 0);
        const st = s.status || "—";
        const samples = s.samples != null ? `, samples=${s.samples}` : "";
        status.textContent = `${st}  ${progress}%${samples}`;
        if (qualityEl) {
          qualityEl.textContent = s.quality || (st === "success" ? `fitness ${s.fitness || "—"}` : "—");
          setTone(qualityEl, qualityEl.textContent);
        }
        if (rawEl) rawEl.textContent = fmtAxis(s.raw);
        if (rangesEl) rangesEl.textContent = fmtAxis(s.ranges);
        if (minmaxEl) {
          const mn = Array.isArray(s.min) ? fmtAxis(s.min) : "—";
          const mx = Array.isArray(s.max) ? fmtAxis(s.max) : "—";
          minmaxEl.textContent = `${mn} / ${mx}`;
        }
        if (warningsEl) {
          const warnings = Array.isArray(s.warnings) ? s.warnings.filter(Boolean) : [];
          warningsEl.textContent = warnings.length ? warnings.join(" ") : "—";
          setTone(warningsEl, warnings.length ? "погано" : "добре");
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
        status.textContent = "статус недоступний";
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
      if (messageEl) messageEl.textContent = "Виставлено Matek M10Q: X=+X, Y=-Y, Z=-Z, зсув=0. Натисни «Зберегти».";
    });
    presetRotated?.addEventListener("click", () => {
      $("set-compass-x").value = "y";
      $("set-compass-y").value = "-x";
      $("set-compass-z").value = "-z";
      $("set-heading-offset").value = 0;
      if (messageEl) messageEl.textContent = "Виставлено Matek 90°: X=+Y, Y=-X, Z=-Z, зсув=0. Натисни «Зберегти».";
    });

    refresh();
    setInterval(refresh, 3000); // тихий пинг чтоб поймать стейт после reload
  }

  // ---- TX12 calibration panel -----------------------------------------------
  function setupTx12Panel(joystick) {
    const panel = $("tx12-panel");
    const idEl = $("tx12-id");
    const axesEl = $("tx12-axes");
    const btnsEl = $("tx12-buttons");
    const rTx12 = $("r-tx12");

    $("tx12-toggle").addEventListener("click", () => panel.classList.toggle("hidden"));
    $("tx12-close").addEventListener("click", () => panel.classList.add("hidden"));

    const cfg = joystick.getTx12Config();
    const enabledEl = $("cfg-tx12-enabled");
    const thrAxis = $("cfg-thr-axis"), strAxis = $("cfg-str-axis");
    for (let i = 0; i < 8; i++) {
      thrAxis.appendChild(new Option("axes[" + i + "]", String(i)));
      strAxis.appendChild(new Option("axes[" + i + "]", String(i)));
    }
    thrAxis.value = String(cfg.throttleAxis);
    strAxis.value = String(cfg.steeringAxis);
    enabledEl.checked = !!cfg.enabled;
    $("cfg-thr-inv").checked = cfg.throttleInvert;
    $("cfg-str-inv").checked = cfg.steeringInvert;
    $("cfg-arm-btn").value = cfg.armButton;
    $("cfg-arm-req").checked = cfg.armRequired;
    $("cfg-estop-btn").value = cfg.estopButton;
    $("cfg-dz").value = cfg.deadzone;

    const apply = () => {
      const wasEnabled = joystick.getTx12Config().enabled;
      joystick.setTx12Config({
        enabled: enabledEl.checked,
        throttleAxis: parseInt(thrAxis.value, 10),
        throttleInvert: $("cfg-thr-inv").checked,
        steeringAxis: parseInt(strAxis.value, 10),
        steeringInvert: $("cfg-str-inv").checked,
        armButton: parseInt($("cfg-arm-btn").value, 10) || 0,
        armRequired: $("cfg-arm-req").checked,
        estopButton: parseInt($("cfg-estop-btn").value, 10) || 0,
        deadzone: parseFloat($("cfg-dz").value) || 0,
      });
      if (wasEnabled && !enabledEl.checked) {
        current = { throttle: 0, steering: 0, active: false };
        if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ cmd: "stop" }));
      }
    };
    ["cfg-tx12-enabled", "cfg-thr-axis", "cfg-str-axis", "cfg-thr-inv", "cfg-str-inv",
     "cfg-arm-btn", "cfg-arm-req", "cfg-estop-btn", "cfg-dz"]
      .forEach((id) => $(id).addEventListener("change", apply));

    joystick.onTx12Frame((s) => {
      if (!joystick.getTx12Config().enabled) {
        idEl.textContent = "веб-керування вимкнено";
        rTx12.textContent = "вимк.";
        return;
      }
      if (!s.connected) {
        idEl.textContent = "не підключено (натисни будь-яку кнопку на TX12 — браузер побачить)";
        rTx12.textContent = "—";
        return;
      }
      idEl.textContent = s.id || "";
      rTx12.textContent = "підключено";

      if (axesEl.children.length !== s.axes.length) {
        axesEl.innerHTML = "";
        for (let i = 0; i < s.axes.length; i++) {
          const row = document.createElement("div");
          row.className = "tx12-axis-row";
          row.innerHTML =
            '<span class="lbl">' + i + '</span>' +
            '<span class="tx12-bar"><span class="fill"></span></span>' +
            '<span class="val">0.00</span>';
          axesEl.appendChild(row);
        }
      }
      s.axes.forEach((v, i) => {
        const row = axesEl.children[i]; if (!row) return;
        const fill = row.querySelector(".fill");
        const val = row.querySelector(".val");
        const pct = Math.abs(v) * 50;
        if (v >= 0) { fill.style.left = "50%"; fill.style.width = pct + "%"; }
        else        { fill.style.left = (50 - pct) + "%"; fill.style.width = pct + "%"; }
        val.textContent = v.toFixed(2);
      });

      if (btnsEl.children.length !== s.buttons.length) {
        btnsEl.innerHTML = "";
        for (let i = 0; i < s.buttons.length; i++) {
          const b = document.createElement("span");
          b.className = "tx12-btn";
          b.textContent = i;
          btnsEl.appendChild(b);
        }
      }
      s.buttons.forEach((pressed, i) => {
        btnsEl.children[i].classList.toggle("on", pressed);
      });
    });
  }

  // ---- Панель налаштувань ---------------------------------------------------
  function setupSettingsPanel() {
    const panel = $("settings-panel");
    const form = $("settings-form");
    const status = $("settings-status");
    let current = null;

    $("settings-toggle").addEventListener("click", async () => {
      panel.classList.toggle("hidden");
      if (!panel.classList.contains("hidden")) await loadSettings();
    });
    $("settings-close").addEventListener("click", () => panel.classList.add("hidden"));
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
      s.textContent = "перезапускаю...";
      try {
        const resp = await fetch("/api/mavlink/restart", { method: "POST" });
        const data = await resp.json();
        s.textContent = data.ok ? "перезапущено" : ("помилка: " + (data.message || ""));
      } catch (e) { s.textContent = "помилка: " + e; }
    });

    $("reboot-pi").addEventListener("click", async () => {
      const yes = window.confirm("Перезапустити Raspberry Pi? Веб-інтерфейс зникне на 1-2 хвилини.");
      if (!yes) return;
      status.textContent = "надсилаю перезапуск...";
      try {
        const resp = await fetch("/api/system/reboot", { method: "POST" });
        const data = await resp.json();
        status.textContent = data.ok ? "Pi перезапускається..." : "перезапуск не вдався";
      } catch (e) {
        status.textContent = "Pi перезапускається...";
      }
    });

    $("poweroff-pi").addEventListener("click", async () => {
      const yes = window.confirm("Вимкнути Raspberry Pi? Після цього веб-інтерфейс зникне до ручного увімкнення живлення.");
      if (!yes) return;
      status.textContent = "надсилаю вимкнення...";
      try {
        const resp = await fetch("/api/system/poweroff", { method: "POST" });
        const data = await resp.json();
        status.textContent = data.ok ? "Pi вимикається..." : "вимкнення не вдалося";
      } catch (e) {
        status.textContent = "Pi вимикається...";
      }
    });

    const applyVideoProfile = $("apply-video-profile");
    applyVideoProfile?.addEventListener("click", async () => {
      const videoStatus = $("video-profile-status") || status;
      videoStatus.textContent = "застосовую профіль...";
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
        const parts = Object.keys(cams).map((name) => name + ": " + (cams[name].ok ? "ok" : "помилка"));
        videoStatus.textContent = data.ok ? "застосовано: " + parts.join(", ") : "не вдалося: " + parts.join(", ");
        if (data.settings) {
          current = data.settings;
          fillSettings(current);
        }
      } catch (e) {
        videoStatus.textContent = "помилка: " + e;
      }
    });

    async function loadSettings() {
      status.textContent = "завантажую...";
      try {
        const resp = await fetch("/api/settings");
        current = await resp.json();
        fillSettings(current);
        status.textContent = "";
      } catch (e) {
        status.textContent = "не вдалося завантажити";
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
        pass.placeholder = cam.password_set ? "збережено; порожньо = не міняти" : "не задано";
      });
      const network = settings.network || {};
      const power = settings.power || {};
      const nav = settings.navigation || {};
      const wg = network.wireguard || {};
      const mav = settings.mavlink || {};
      $("set-profile").value = network.profile || "balanced";
      $("set-kbps").value = network.target_kbps || 1800;
      $("set-link-mode").value = network.link_mode || "auto";
      $("set-wifi-ssid").value = network.wifi_ssid || "";
      $("set-wifi-password").value = "";
      $("set-wifi-password").placeholder = network.wifi_password_set ? "збережено; порожньо = не міняти" : "не задано";
      const ap = network.setup_ap || {};
      $("set-ap-enabled").checked = ap.enabled !== false;
      $("set-ap-ssid").value = ap.ssid || "Gimli-Rover-Setup";
      $("set-ap-password").value = "";
      $("set-ap-password").placeholder = ap.password_set ? "збережено; порожньо = не міняти" : "gimli1234";
      $("set-wg-enabled").checked = !!wg.enabled;
      $("set-wg-interface").value = wg.interface || "wg0";
      $("set-wg-address").value = wg.address || "";
      $("set-wg-private").value = "";
      $("set-wg-private").placeholder = wg.private_key_set ? "збережено; порожньо = не міняти" : "не задано";
      $("set-wg-peer-public").value = wg.peer_public_key || "";
      $("set-wg-endpoint").value = wg.peer_endpoint || "";
      $("set-wg-allowed").value = wg.allowed_ips || "0.0.0.0/0";
      $("set-wg-keepalive").value = wg.persistent_keepalive || 25;
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
      next.network.wireguard = next.network.wireguard || {};
      next.network.wireguard.enabled = $("set-wg-enabled").checked;
      next.network.wireguard.interface = $("set-wg-interface").value.trim() || "wg0";
      next.network.wireguard.address = $("set-wg-address").value.trim();
      next.network.wireguard.private_key = $("set-wg-private").value.trim();
      next.network.wireguard.peer_public_key = $("set-wg-peer-public").value.trim();
      next.network.wireguard.peer_endpoint = $("set-wg-endpoint").value.trim();
      next.network.wireguard.allowed_ips = $("set-wg-allowed").value.trim() || "0.0.0.0/0";
      next.network.wireguard.persistent_keepalive = parseInt($("set-wg-keepalive").value, 10) || 25;
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
      status.textContent = "зберігаю...";
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
        status.textContent = data.go2rtc_restarted ? "збережено, відеоміст перезапущено" : "збережено; потрібен перезапуск";
        updateTelemetry(data.settings);
      } catch (err) {
        status.textContent = "не вдалося зберегти: " + (err?.message || err);
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
      status.textContent = "зберігаю GPS...";
      try {
        const resp = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(readSettings()),
        });
        const data = await resp.json();
        current = data.settings;
        fillSettings(current);
        status.textContent = source === "off" ? "GPS вимкнено" : "GPS збережено";
      } catch (e) {
        status.textContent = "GPS не збережено";
      }
    }
  }

  async function scanWifi() {
    const status = $("wifi-scan-status");
    const list = $("wifi-networks");
    status.textContent = "сканую...";
    list.innerHTML = "";
    try {
      const resp = await fetch("/api/network/wifi-scan");
      const data = await resp.json();
      const networks = data.networks || [];
      status.textContent = networks.length ? networks.length + " мереж" : "нічого не знайдено";
      networks.forEach((net) => {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "wifi-network";
        item.innerHTML =
          '<span>' + escapeHtml(net.ssid || "") + '</span>' +
          '<span>' + (net.signal == null ? "—" : net.signal.toFixed(0) + " dBm") + '</span>' +
          '<span>' + (net.security || "open") + '</span>';
        item.addEventListener("click", () => {
          $("set-wifi-ssid").value = net.ssid || "";
          $("set-link-mode").value = "wifi";
        });
        list.appendChild(item);
      });
    } catch (e) {
      status.textContent = "сканування не вдалося";
    }
  }

  async function connectWifi() {
    const status = $("wifi-scan-status");
    const ssid = $("set-wifi-ssid").value.trim();
    const password = $("set-wifi-password").value;
    if (!ssid) {
      status.textContent = "вкажи SSID";
      return;
    }
    const yes = window.confirm("Підключитись до Wi-Fi '" + ssid + "'? Зв'язок може зникнути на кілька секунд.");
    if (!yes) return;
    status.textContent = "підключаю...";
    try {
      const resp = await fetch("/api/network/wifi-connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ssid, password, interface: "wlan0" }),
      });
      const data = await resp.json();
      status.textContent = data.ok ? "підключено" : ("помилка: " + (data.message || ""));
      setTimeout(loadNetworkStatus, 2000);
    } catch (e) {
      status.textContent = "команда відправлена; перевір зв'язок";
    }
  }

  async function setupAp(action) {
    const status = $("wifi-scan-status");
    const text = action === "start" ? "Увімкнути setup AP? Поточний Wi-Fi може відключитись." : "Вимкнути setup AP?";
    if (!window.confirm(text)) return;
    status.textContent = action === "start" ? "вмикаю AP..." : "вимикаю AP...";
    try {
      const resp = await fetch("/api/network/setup-ap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      const data = await resp.json();
      status.textContent = data.ok ? data.message : ("помилка: " + (data.message || ""));
      setTimeout(loadNetworkStatus, 2000);
    } catch (e) {
      status.textContent = "команда відправлена; перевір зв'язок";
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
      $("network-status").textContent = names ? "мережа: " + names : "мережа: —";
    } catch (e) {}
  }

  // ---- Telemetry / OSD -------------------------------------------------------
  function startTelemetry() {
    const tick = async () => {
      try {
        const resp = await fetch("/api/telemetry");
        updateTelemetry(await resp.json());
      } catch (e) {}
    };
    tick();
    setInterval(tick, 3000);
  }

  function updateTelemetry(t) {
    const link = t.link || t.network || {};
    const power = t.power || {};
    const nav = t.navigation || {};
    const cameras = t.cameras || {};
    const motors = t.motors || {};
    const control = t.control || {};
    const rc = t.rc_input || {};
    const volts = power.battery_voltage;
    const wg = link.wireguard_enabled ? " / WG" : "";
    const mav = t.mavlink && t.mavlink.enabled ? " / MAV" + t.mavlink.system_id : "";
    const armText = control.armed == null ? "—" : (control.armed ? "увімк." : "вимк.");
    const rcText = rc.ok == null ? "—" : (rc.ok ? "OK" : "LOST");
    const modeText = control.daynight ? (control.daynight === "night" ? "ніч" : "день") : "—";
    const buttonsText = control.buttons_hex || "—";
    $("osd-link").textContent = (link.link_mode || "auto") + " / " + (link.profile || "balanced") + " / " + (link.target_kbps || 0) + " kbps" + wg + mav;
    $("osd-voltage").textContent = volts == null ? "— V" : Number(volts).toFixed(1) + " V";
    $("osd-voltage").classList.toggle("bad", volts != null && volts <= (power.low_voltage || 0));
    $("osd-current").textContent = power.current_a == null ? "— A" : Number(power.current_a).toFixed(2) + " A";
    $("osd-power").textContent = power.power_w == null ? "— W" : Number(power.power_w).toFixed(1) + " W";
    $("osd-arm").textContent = armText;
    $("osd-arm").classList.toggle("bad", control.armed === false);
    $("osd-rc").textContent = rcText;
    $("osd-rc").classList.toggle("bad", rc.ok === false);
    $("osd-daynight").textContent = modeText;
    $("osd-buttons").textContent = buttonsText;
    $("top-arm").textContent = armText;
    $("top-arm").classList.toggle("ok", control.armed === true);
    $("top-arm").classList.toggle("bad", control.armed === false);
    $("top-rc").textContent = rcText;
    $("top-rc").classList.toggle("ok", rc.ok === true);
    $("top-rc").classList.toggle("bad", rc.ok === false);
    $("top-daynight").textContent = modeText;
    $("top-buttons").textContent = buttonsText;
    $("osd-gps").textContent = gpsText(nav);
    $("osd-heading").textContent = nav.heading_deg == null ? "—°" : Number(nav.heading_deg).toFixed(0) + "°";
    $("osd-cam1").textContent = cameraText(cameras.cam1);
    $("osd-cam2").textContent = cameraText(cameras.cam2);
    $("osd-motor").textContent = motors.mock ? "тест" : "реальні";
    if (t.video && t.video.active_stream) applyActiveCamera(t.video.active_stream, false);
  }

  function cameraText(cam) {
    if (!cam) return "—";
    if (!cam.enabled) return "вимк.";
    return (cam.host || "no host") + " / " + (cam.preferred || "main");
  }

  function gpsText(nav) {
    if (!nav || !nav.gps_enabled) return nav && nav.gps_warning ? nav.gps_warning : "вимк.";
    const fix = Number(nav.fix_type || 0);
    const sats = Number(nav.satellites || 0);
    if (fix < 2) return "нема фікса / " + sats + " суп.";
    const label = fix >= 3 ? "3D" : "2D";
    if (nav.latitude == null || nav.longitude == null) return label + " / " + sats + " суп.";
    return label + " / " + sats + " суп. / " + Number(nav.latitude).toFixed(5) + ", " + Number(nav.longitude).toFixed(5);
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
  let currentAudioStream = "active";

  function setupAudio() {
    const btn = $("audio-toggle");
    const status = $("audio-status");
    if (!btn || !status) return;
    btn.addEventListener("click", async () => {
      audioEnabled = !audioEnabled;
      if (audioEnabled) {
        btn.classList.add("primary");
        status.textContent = "підключаю звук...";
        await startAudio(currentAudioStream);
      } else {
        btn.classList.remove("primary");
        stopAudio();
        status.textContent = "звук вимк.";
      }
    });
  }

  async function startAudio(streamName) {
    stopAudio();
    currentAudioStream = streamName || "active";
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
      if (status) status.textContent = "звук: " + currentAudioStream;
    } catch (e) {
      if (status) status.textContent = "звук недоступний";
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
    if (audioEl) {
      try { audioEl.pause(); audioEl.srcObject = null; } catch (e) {}
    }
    audioEl = null;
  }

  function setupCameraControls() {
    document.querySelectorAll(".cam-controls").forEach((root) => {
      const camera = root.dataset.camera;
      const level = root.querySelector("[data-light-level]");
      const status = root.querySelector("[data-camera-status]");
      root.querySelectorAll("[data-light]").forEach((button) => {
        button.addEventListener("click", async () => {
          const mode = button.dataset.light;
          status.textContent = "надсилаю...";
          try {
            const resp = await fetch("/api/camera/" + camera + "/control", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                action: "light",
                mode,
                level: parseInt(level.value, 10) || 60,
              }),
            });
            const data = await resp.json();
            status.textContent = data.ok ? "ок" : "помилка";
            if (!data.ok && data.message) console.warn("camera control:", data.message);
          } catch (e) {
            status.textContent = "нема зв'язку";
          }
        });
      });
    });
  }

  function applyActiveCamera(name, saving) {
    const active = name === "cam2" ? "cam2" : "cam1";
    const inactive = active === "cam1" ? "cam2" : "cam1";
    $("tile-" + active).classList.add("active");
    $("tile-" + active).classList.remove("pip");
    $("tile-" + inactive).classList.add("pip");
    $("tile-" + inactive).classList.remove("active");
    $("active-cam1").classList.toggle("primary", active === "cam1");
    $("active-cam2").classList.toggle("primary", active === "cam2");
    const label = active === "cam2" ? "задня" : "передня";
    $("active-camera-status").textContent = (saving ? "зберігаю: " : "активна: ") + label;
    if (audioEnabled && currentAudioStream !== active) startAudio(active);
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
      $("active-camera-status").textContent = "перемикання не вдалося";
    }
  }

  startCamera($("cam1"), "cam1").catch((e) => console.error("cam1:", e));
  startCamera($("cam2"), "cam2").catch((e) => console.error("cam2:", e));
})();
