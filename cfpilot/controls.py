"""Превращение клавиатуры/пульта в положение «стиков» -1..1 (раскладка Mode 2)."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Sequence

from cfpilot.config import AxisRange, Binding, Bindings, JoystickSettings
from cfpilot.i18n import t


@dataclass(frozen=True)
class Sticks:
    roll: float = 0.0  # + вправо
    pitch: float = 0.0  # + вперёд
    yaw: float = 0.0  # + поворот вправо (по часовой)
    throttle: float = 0.0  # + вверх, -1 = газ в самом низу


@dataclass(frozen=True)
class KeyState:
    forward: bool = False
    back: bool = False
    left: bool = False
    right: bool = False
    up: bool = False
    down: bool = False
    yaw_left: bool = False
    yaw_right: bool = False


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def apply_deadzone(value: float, deadzone: float) -> float:
    """Обнуляет дрожание у центра и растягивает остаток, чтобы не было скачка."""
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    scaled = (magnitude - deadzone) / (1.0 - deadzone)
    return scaled if value > 0 else -scaled


def apply_expo(value: float, expo: float) -> float:
    """Мягче у центра, тот же максимум на краях — как expo на пульте."""
    return (1.0 - expo) * value + expo * value**3


def _pair(positive: bool, negative: bool) -> float:
    return float(positive) - float(negative)


def step_throttle_level(level: float, keys: KeyState, dt: float, rate: float) -> float:
    """Газ без пружины для режима manual: W/S двигают его, отпустил — остался на месте."""
    return clamp(level + _pair(keys.up, keys.down) * rate * dt)


def keyboard_sticks(keys: KeyState, throttle_level: float | None) -> Sticks:
    """throttle_level=None — газ «на пружине»: W = +1, S = -1, ничего = 0 (держать высоту)."""
    throttle = _pair(keys.up, keys.down) if throttle_level is None else throttle_level
    return Sticks(
        roll=_pair(keys.right, keys.left),
        pitch=_pair(keys.forward, keys.back),
        yaw=_pair(keys.yaw_right, keys.yaw_left),
        throttle=throttle,
    )


def calibrate(value: float, calibration: AxisRange | None, centered: bool = True) -> float:
    """Сырое значение оси -> -1..1 по калибровке: упоры в ±1, положение покоя в 0.

    centered=False — газ: весь ход low..high растягивается на -1..1 (у газа без пружины покой — внизу).
    """
    if calibration is None:
        return clamp(value)
    low, center, high = calibration.low, calibration.center, calibration.high
    if not centered:
        return clamp(2.0 * (value - low) / (high - low) - 1.0)
    span = high - center if value >= center else center - low
    if span <= 0.0:
        return 0.0
    return clamp((value - center) / span)


def read_axis(axes: Sequence[float], index: int | None, invert: bool, calibration: AxisRange | None = None,
              centered: bool = True) -> float:
    if index is None or not 0 <= index < len(axes):
        return 0.0
    value = calibrate(axes[index], calibration, centered)
    return -value if invert else value


def joystick_sticks(axes: Sequence[float], buttons: Sequence[bool], settings: JoystickSettings) -> Sticks:
    ranges = {item.axis: item for item in settings.calibration}

    def shaped(index: int | None, invert: bool) -> float:
        raw = read_axis(axes, index, invert, ranges.get(index))
        return apply_expo(apply_deadzone(raw, settings.deadzone), settings.expo)

    bindings = settings.bindings
    yaw_buttons = _pair(binding_active(bindings.yaw_right, axes, buttons),
                        binding_active(bindings.yaw_left, axes, buttons))
    # Газ отдаём как есть: в manual он абсолютный, а мёртвую зону для hover
    # применяет flight.step (у стика газа на пульте нет пружины).
    throttle = settings.axes.throttle
    return Sticks(
        roll=shaped(settings.axes.roll, settings.invert.roll),
        pitch=shaped(settings.axes.pitch, settings.invert.pitch),
        yaw=clamp(shaped(settings.axes.yaw, settings.invert.yaw) + yaw_buttons),
        throttle=read_axis(axes, throttle, settings.invert.throttle, ranges.get(throttle), centered=False),
    )


def binding_problems(bindings: Bindings, num_axes: int, num_buttons: int) -> list[str]:
    """Привязки, указывающие на несуществующую ось/кнопку, молча не работали бы — сообщаем."""
    problems = []
    for item in fields(bindings):
        action = item.name
        binding = getattr(bindings, action)
        if binding is None:
            continue
        if binding.button is not None and binding.button >= num_buttons:
            problems.append(t("{action}: у пульта нет кнопки {button} (всего {count})", action=action,
                               button=binding.button, count=num_buttons))
        if binding.axis is not None and binding.axis >= num_axes:
            problems.append(t("{action}: у пульта нет оси {axis} (всего {count})", action=action, axis=binding.axis,
                               count=num_axes))
    return problems


def binding_active(binding: Binding | None, axes: Sequence[float], buttons: Sequence[bool]) -> bool:
    if binding is None:
        return False
    if binding.button is not None:
        return 0 <= binding.button < len(buttons) and bool(buttons[binding.button])
    if not 0 <= binding.axis < len(axes):
        return False
    value = axes[binding.axis]
    if binding.above is not None:
        return value > binding.above
    return value < binding.below
