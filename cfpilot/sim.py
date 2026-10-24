"""Игрушечный симулятор вместо дрона — чтобы проверить управление без полёта."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable

from cfpilot.flight import (
    Action,
    AttitudeSetpoint,
    FlightMode,
    HoverSetpoint,
    Output,
    Setpoint,
    StopSetpoint,
    ZDistanceSetpoint,
)
from cfpilot.link import DeckInfo, Telemetry

HEIGHT_RESPONSE = 3.0  # 1/с, как быстро высота догоняет цель
TILT_PER_SPEED = 20.0  # град наклона на 1 м/с — только для картинки
HOVER_THRUST = 38000
THRUST_TO_CLIMB = 2.0 / 25000  # м/с на единицу тяги сверх висения
FALL_SPEED = 1.0  # м/с, если моторы выключены
BATTERY_FULL = 4.15
BATTERY_DRAIN = 0.003  # В/с при работающих моторах
# Программа ждёт броска — подтверждает арминг каждый тик и не даёт тяги. Рук у симулятора нет,
# поэтому через пару секунд такого ожидания он «подбрасывает» дрон сам.
SIM_THROW_AFTER = 2.0  # с
SIM_THROW_HEIGHT = 1.2  # м

_DECKS_FOR_MODE = {
    FlightMode.HOVER: DeckInfo(flow=True),
    FlightMode.ALTHOLD: DeckInfo(zranger=True),
    FlightMode.MANUAL: DeckInfo(),
    FlightMode.ACRO: DeckInfo(),
}


class SimLink:
    uri = "sim://simulator"

    def __init__(self, mode: FlightMode, clock: Callable[[], float] = time.monotonic) -> None:
        self._mode = mode
        self._clock = clock
        self._last_time: float | None = None
        self._throw_wait = 0.0  # с — сколько программа ждёт броска
        self._telemetry = Telemetry(battery=BATTERY_FULL, height=0.0, roll=0.0, pitch=0.0, yaw=0.0, link_quality=100.0,
                                    thrown=False)

    @property
    def telemetry(self) -> Telemetry:
        return self._telemetry

    @property
    def error(self) -> str | None:
        return None

    @property
    def position_ready(self) -> bool:
        return True

    @property
    def failsafe_stopped(self) -> bool:
        return False

    @property
    def rate_ready(self) -> bool:
        return True  # симулятор понимает флаг rate в каждом пакете — переключать нечего

    def connect(self) -> DeckInfo:
        return _DECKS_FOR_MODE[self._mode]

    def execute(self, output: Output) -> None:
        now = self._clock()
        dt = 0.0 if self._last_time is None else now - self._last_time
        self._last_time = now
        waiting = Action.REARM in output.actions and not _lifts(output.setpoint)
        self._throw_wait = self._throw_wait + dt if waiting else 0.0
        if self._throw_wait >= SIM_THROW_AFTER and self._telemetry.height == 0.0:
            self._telemetry = replace(self._telemetry, height=SIM_THROW_HEIGHT, thrown=True)
        if output.setpoint is not None:
            self._telemetry = _simulate(self._telemetry, output.setpoint, dt)
        if self._telemetry.thrown and (_lifts(output.setpoint) or self._telemetry.height == 0.0):
            self._telemetry = replace(self._telemetry, thrown=False)  # поймали или упал

    def close(self) -> None:
        self._last_time = None


def _lifts(setpoint: Setpoint | None) -> bool:
    if isinstance(setpoint, AttitudeSetpoint):
        return setpoint.thrust > 0
    return isinstance(setpoint, (HoverSetpoint, ZDistanceSetpoint))


def _approach(height: float, target: float, dt: float) -> float:
    return height + (target - height) * min(1.0, dt * HEIGHT_RESPONSE)


def _wrap(angle: float) -> float:
    """Угол после вращения (сальто) — в пределах -180..180, как у настоящего дрона."""
    return (angle + 180.0) % 360.0 - 180.0


def _drain(telemetry: Telemetry, dt: float) -> float:
    return telemetry.battery - BATTERY_DRAIN * dt


def _simulate(t: Telemetry, setpoint, dt: float) -> Telemetry:
    if isinstance(setpoint, StopSetpoint):
        return replace(t, height=0.0, roll=0.0, pitch=0.0)
    if isinstance(setpoint, HoverSetpoint):
        return replace(
            t,
            height=_approach(t.height, setpoint.z, dt),
            roll=-setpoint.vy * TILT_PER_SPEED,
            pitch=setpoint.vx * TILT_PER_SPEED,
            yaw=t.yaw + setpoint.yawrate * dt,
            battery=_drain(t, dt),
        )
    if isinstance(setpoint, ZDistanceSetpoint):
        return replace(
            t,
            height=_approach(t.height, setpoint.z, dt),
            roll=setpoint.roll,
            pitch=-setpoint.pitch,
            yaw=t.yaw + setpoint.yawrate * dt,
            battery=_drain(t, dt),
        )
    if isinstance(setpoint, AttitudeSetpoint):
        climb = (setpoint.thrust - HOVER_THRUST) * THRUST_TO_CLIMB if setpoint.thrust else -FALL_SPEED
        pitch = _wrap(t.pitch + setpoint.pitch * dt) if setpoint.rate else setpoint.pitch
        return replace(
            t,
            height=max(0.0, t.height + climb * dt),
            roll=_wrap(t.roll + setpoint.roll * dt) if setpoint.rate else setpoint.roll,
            pitch=pitch,
            yaw=t.yaw - setpoint.yawrate * dt,
            battery=_drain(t, dt) if setpoint.thrust else t.battery,
        )
    return t
