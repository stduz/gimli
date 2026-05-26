"""
Управление двумя DC-моторами через PWM-драйвер (L298N/TB6612 и аналоги).

Распиновка (BCM):
    Левый мотор:  IN1=17, IN2=27, ENA=18 (PWM)
    Правый мотор: IN3=22, IN4=23, ENB=13 (PWM)

Изменить пины — внизу файла, словарь PINS.

Управление: команда (throttle, steering), оба в диапазоне [-1, 1].
    throttle:  +1 = полный вперёд, -1 = полный назад
    steering:  +1 = резко вправо,  -1 = резко влево
Микширование — стандартный differential drive.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from gpiozero import Motor, PWMOutputDevice

# ---- настройка пинов (BCM) ----------------------------------------------------
PINS = {
    "left_in1": 17,
    "left_in2": 27,
    "left_en": 18,   # должен быть hardware PWM-capable
    "right_in1": 22,
    "right_in2": 23,
    "right_en": 13,  # должен быть hardware PWM-capable
}

# Если команда не приходит дольше этого времени — глушим моторы (failsafe).
WATCHDOG_TIMEOUT_S = 0.5


@dataclass
class MotorState:
    left: float = 0.0
    right: float = 0.0
    throttle: float = 0.0
    steering: float = 0.0
    last_update_ts: float = 0.0


class RoverDrive:
    def __init__(self, pins: dict[str, int] | None = None, watchdog_timeout_s: float = WATCHDOG_TIMEOUT_S) -> None:
        self._pins = dict(PINS)
        if pins:
            self._pins.update(pins)
        self._watchdog_timeout_s = watchdog_timeout_s
        self._left = Motor(forward=self._pins["left_in1"], backward=self._pins["left_in2"], pwm=False)
        self._right = Motor(forward=self._pins["right_in1"], backward=self._pins["right_in2"], pwm=False)
        self._left_en = PWMOutputDevice(self._pins["left_en"], frequency=1000)
        self._right_en = PWMOutputDevice(self._pins["right_en"], frequency=1000)
        self.state = MotorState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog.start()

    # ---- public API ----------------------------------------------------------
    def drive(self, throttle: float, steering: float) -> MotorState:
        throttle = _clamp(throttle, -1.0, 1.0)
        steering = _clamp(steering, -1.0, 1.0)
        # differential drive mix
        left = throttle + steering
        right = throttle - steering
        # нормализуем чтобы не вылезти за 1.0
        peak = max(abs(left), abs(right), 1.0)
        left /= peak
        right /= peak
        with self._lock:
            self._apply(left, right)
            self.state = MotorState(
                left=left,
                right=right,
                throttle=throttle,
                steering=steering,
                last_update_ts=time.time(),
            )
            return self.state

    def stop(self) -> None:
        with self._lock:
            self._apply(0.0, 0.0)
            self.state = MotorState(last_update_ts=time.time())

    def shutdown(self) -> None:
        self._stop_event.set()
        self.stop()
        self._left.close()
        self._right.close()
        self._left_en.close()
        self._right_en.close()

    # ---- internal ------------------------------------------------------------
    def _apply(self, left: float, right: float) -> None:
        _set_motor(self._left, self._left_en, left)
        _set_motor(self._right, self._right_en, right)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.1)
            with self._lock:
                age = time.time() - self.state.last_update_ts
                moving = self.state.left != 0.0 or self.state.right != 0.0
            if moving and age > self._watchdog_timeout_s:
                # связь пропала — глушим
                self.stop()


def _set_motor(motor: Motor, pwm_enable: PWMOutputDevice, value: float) -> None:
    if value > 0:
        motor.forward()
    elif value < 0:
        motor.backward()
    else:
        motor.stop()
    pwm_enable.value = min(abs(value), 1.0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
