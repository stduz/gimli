/* Управление: виртуальный джойстик (тач/мышь), клавиатура, RadioMaster TX12.
   TX12 в режиме USB Joystick (EdgeTX → System → USB → Joystick) виден браузеру
   как обычный HID-геймпад. Читаем его через Gamepad API.
   Конфиг хранится в localStorage. v2 сбрасывает старое автоподключение TX12.
*/
(function (global) {
  const DEFAULT_TX12 = {
    enabled: false,
    throttleAxis: 2,
    throttleInvert: true,
    steeringAxis: 0,
    steeringInvert: false,
    armButton: 4,
    armRequired: true,
    estopButton: 7,
    deadzone: 0.06,
  };
  const TX12_KEY = "gimli.tx12.v2";
  function loadCfg() {
    try {
      const raw = localStorage.getItem(TX12_KEY);
      if (!raw) return Object.assign({}, DEFAULT_TX12);
      return Object.assign({}, DEFAULT_TX12, JSON.parse(raw));
    } catch (e) { return Object.assign({}, DEFAULT_TX12); }
  }
  function saveCfg(cfg) {
    try { localStorage.setItem(TX12_KEY, JSON.stringify(cfg)); } catch (e) {}
  }

  class Joystick {
    constructor(rootEl, knobEl, onMove, onEnd) {
      this.root = rootEl;
      this.knob = knobEl;
      this.onMove = onMove;
      this.onEnd = onEnd;
      this.maxR = rootEl.clientWidth / 2 - knobEl.clientWidth / 2;
      this.active = false;
      this.pointerId = null;
      this.keys = { w: 0, a: 0, s: 0, d: 0 };
      this.tx12 = loadCfg();
      this.tx12State = { axes: [], buttons: [], connected: false };
      this._bind();
      this._installKeyboard();
      this._installTx12();
    }

    getTx12Config() { return Object.assign({}, this.tx12); }
    setTx12Config(patch) {
      this.tx12 = Object.assign({}, this.tx12, patch);
      saveCfg(this.tx12);
    }
    onTx12Frame(cb) { this._frameCb = cb; }

    _bind() {
      this.root.addEventListener("pointerdown", (e) => this._down(e));
      this.root.addEventListener("pointermove", (e) => this._move(e));
      this.root.addEventListener("pointerup", (e) => this._up(e));
      this.root.addEventListener("pointercancel", (e) => this._up(e));
      this.root.addEventListener("pointerleave", (e) => this._up(e));
    }
    _down(e) {
      this.active = true;
      this.pointerId = e.pointerId;
      this.root.setPointerCapture(e.pointerId);
      this._move(e);
    }
    _move(e) {
      if (!this.active || e.pointerId !== this.pointerId) return;
      const r = this.root.getBoundingClientRect();
      let dx = e.clientX - (r.left + r.width / 2);
      let dy = e.clientY - (r.top + r.height / 2);
      const dist = Math.hypot(dx, dy);
      if (dist > this.maxR) { dx = (dx / dist) * this.maxR; dy = (dy / dist) * this.maxR; }
      this._setKnob(dx, dy);
      this.onMove({ throttle: -dy / this.maxR, steering: dx / this.maxR });
    }
    _up(e) {
      if (!this.active) return;
      this.active = false;
      this.pointerId = null;
      this._setKnob(0, 0);
      this.onEnd();
    }
    _setKnob(dx, dy) { this.knob.style.transform = "translate(" + dx + "px, " + dy + "px)"; }

    _installKeyboard() {
      const map = { w: "w", arrowup: "w", s: "s", arrowdown: "s",
                    a: "a", arrowleft: "a", d: "d", arrowright: "d" };
      const tick = () => {
        const throttle = (this.keys.w ? 1 : 0) + (this.keys.s ? -1 : 0);
        const steering = (this.keys.d ? 1 : 0) + (this.keys.a ? -1 : 0);
        if (throttle || steering) { this.onMove({ throttle, steering }); }
        else if (this._wasKeyboard) { this.onEnd(); }
        this._wasKeyboard = !!(throttle || steering);
      };
      window.addEventListener("keydown", (e) => {
        const k = map[e.key.toLowerCase()]; if (!k) return;
        this.keys[k] = 1; tick();
      });
      window.addEventListener("keyup", (e) => {
        const k = map[e.key.toLowerCase()]; if (!k) return;
        this.keys[k] = 0; tick();
      });
    }

    _installTx12() {
      let last = { throttle: 0, steering: 0, active: false };
      const poll = () => {
        const pads = navigator.getGamepads ? navigator.getGamepads() : [];
        const pad = pads && pads[0];
        if (pad && this.tx12.enabled) {
          const cfg = this.tx12;
          const axes = pad.axes || [];
          const btns = (pad.buttons || []).map(function (b) { return !!b.pressed; });
          this.tx12State = {
            axes: Array.from(axes),
            buttons: btns,
            connected: true,
            id: pad.id,
          };

          if (btns[cfg.estopButton]) {
            if (last.active || last.throttle || last.steering) {
              this.onEnd();
              last = { throttle: 0, steering: 0, active: false };
            }
            if (this._frameCb) this._frameCb(this.tx12State);
            requestAnimationFrame(poll);
            return;
          }

          const armed = !cfg.armRequired || !!btns[cfg.armButton];

          let throttle = axes[cfg.throttleAxis] || 0;
          let steering = axes[cfg.steeringAxis] || 0;
          if (cfg.throttleInvert) throttle = -throttle;
          if (cfg.steeringInvert) steering = -steering;
          if (Math.abs(throttle) < cfg.deadzone) throttle = 0;
          if (Math.abs(steering) < cfg.deadzone) steering = 0;
          if (!armed) throttle = 0;

          if (throttle || steering) {
            this.onMove({ throttle, steering });
            last = { throttle, steering, active: true };
          } else if (last.active) {
            this.onEnd();
            last.active = false;
          }
        } else {
          if (this.tx12State.connected) this.tx12State = { axes: [], buttons: [], connected: false };
        }
        if (this._frameCb) this._frameCb(this.tx12State);
        requestAnimationFrame(poll);
      };
      requestAnimationFrame(poll);
    }
  }
  global.Joystick = Joystick;
})(window);
