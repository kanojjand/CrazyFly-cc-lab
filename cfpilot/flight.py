"""Логика полёта: взлёт, полёт, посадка, аварийная остановка.

Чистые функции без радио — каждый тик step() получает стики и команды
и возвращает новое состояние + что отправить дрону.
Знаки setpoint-ов уже в соглашениях cflib (проверено по cfclient):
  hover:     vx + вперёд, vy + влево, yawrate + против часовой
  zdistance: roll + вправо, pitch + назад, yawrate + против часовой
  setpoint:  roll + вправо, pitch + вперёд, yawrate + по часовой
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Union

from cfpilot.config import FlightLimits
from cfpilot.controls import Sticks, apply_deadzone, apply_expo, clamp
from cfpilot.i18n import N_, t

LANDED_HEIGHT = 0.05  # м — ниже этого считаем, что сели, и глушим моторы
THROTTLE_LOW = -0.9  # стик газа «внизу» — условие взлёта в manual
MOTOR_OFF_FRACTION = 0.02  # газ почти в ноль = моторы стоят
KILL_REPEAT = 0.5  # с — сколько повторять стоп после kill (на случай потери пакета)
KILL_COOLDOWN = 1.0  # с — после kill взлёт заблокирован (дрон ещё падает)
ESTIMATOR_TIMEOUT = 5.0  # с сверх arm_delay — ждать, пока Lighthouse/LPS найдут положение
THROW_WAIT = 20.0  # с — дольше моторы взведёнными в руке не держим
THROW_MIN_THROTTLE = 0.0  # manual: газ ниже середины — ловить нечем, дрон просто упадёт
# После ловли арминг повторяем: прошивка, снявшая «аварию», могла не принять его с первого пакета.
CATCH_REARM = 0.3  # с
# Сальто (петля): подскок -> вращение на 360° по угловой скорости -> выравнивание с запасом тяги.
THRUST_CAP = 60000  # потолок тяги в манёврах (60000 — «полный газ» по документации cflib)
FLIP_SWITCH_TIMEOUT = 0.6  # с — столько ждём, пока прошивка перейдёт на угловые скорости
DASH_MAX_TIME = 1.5  # с — дольше рывок 45° не держим, даже если курок зажат
STALE_HOLD = 0.25  # с без команд от главного цикла -> failsafe зависает на месте
STALE_LAND = 3.0  # с без команд -> failsafe сам садится
_EPS = 1e-9  # защита от накопления ошибки при сложении float
NEUTRAL = Sticks()


class FlightMode(str, Enum):
    HOVER = "hover"  # Flow deck / Lighthouse / LPS: держит место и высоту
    ALTHOLD = "althold"  # только Z-ranger: держит высоту, наклон — стиками
    MANUAL = "manual"  # без дек: стики = наклон и газ, как обычный квадрик
    ACRO = "acro"  # FPV: полное ручное управление по угловой скорости (rate mode)


class Phase(Enum):
    IDLE = N_("На земле")
    ARMING = N_("Арминг")
    THROW_READY = N_("Жду броска")  # моторы взведены, но стоят; падение = бросок, ловим
    TAKING_OFF = N_("Взлёт")
    FLYING = N_("Полёт")
    LANDING = N_("Посадка")
    FLIPPING = N_("Сальто")
    KILLED = N_("МОТОРЫ ОСТАНОВЛЕНЫ")


ACTIVE_PHASES = frozenset({Phase.ARMING, Phase.THROW_READY, Phase.TAKING_OFF, Phase.FLYING, Phase.LANDING, Phase.FLIPPING})


@dataclass(frozen=True)
class HoverSetpoint:
    vx: float
    vy: float
    yawrate: float
    z: float


@dataclass(frozen=True)
class ZDistanceSetpoint:
    roll: float
    pitch: float
    yawrate: float
    z: float


@dataclass(frozen=True)
class AttitudeSetpoint:
    roll: float
    pitch: float
    yawrate: float
    thrust: int
    rate: bool = False


@dataclass(frozen=True)
class StopSetpoint:
    pass


Setpoint = Union[HoverSetpoint, ZDistanceSetpoint, AttitudeSetpoint, StopSetpoint]


class Action(Enum):
    ARM = "arm"
    # Те же пакеты, что ARM, но остановку failsafe-потоком не снимает: так подтверждаем уже взятый арминг.
    REARM = "rearm"
    DISARM = "disarm"
    RESET_ESTIMATOR = "reset_estimator"


@dataclass(frozen=True)
class Command:
    takeoff: bool = False
    land: bool = False
    kill: bool = False
    throw: bool = False
    flip_forward: bool = False  # сальто (петля 360°) вперёд — нажатие
    flip_backward: bool = False
    flip_left: bool = False  # бочка 360° влево
    flip_right: bool = False
    dash: int = 0  # рывок 45°, пока зажат курок: +1 — вперёд, -1 — назад
    rate_ready: bool = True  # прошивка понимает крен/тангаж как угловые скорости (нужно для вращения)
    estimator_ready: bool = True  # False, пока Lighthouse/LPS не нашли положение после сброса
    thrown: bool = False  # дрон подбросили: толчок рукой и сразу невесомость
    height: float | None = None  # м — сколько сейчас до пола; None — не знаем


@dataclass(frozen=True)
class Output:
    setpoint: Setpoint | None = None  # None — в этот тик ничего не шлём
    actions: tuple[Action, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class FlightState:
    phase: Phase = Phase.IDLE
    height: float = 0.0  # целевая высота, м (hover/althold)
    timer: float = 0.0  # время в фазе ARMING / KILLED, с
    thrust: int = 0  # последняя тяга (manual), нужна для плавной посадки
    rearm: float = 0.0  # с — сколько ещё повторять арминг после ловли броска
    # В ожидании броска дрон хоть раз был «в руке» — флаг броска, оставшийся с прошлого раза, не в счёт.
    in_hand: bool = False
    flip_axis: tuple[int, int] = (0, 0)  # (крен, тангаж): (0, 1) — сальто вперёд, (1, 0) — бочка вправо
    flip_stage: str = ""  # boost -> spin -> recover
    flip_target: float = 0.0  # град — на сколько повернуть
    flip_turned: float = 0.0  # град — сколько уже скомандовали
    flip_rate: float = 0.0  # град/с — скорость вращения этого манёвра
    flip_boost_thrust: int = 0  # тяга подскока и выравнивания
    flip_spin_thrust: int = 0  # тяга во вращении
    flip_recover: bool = True  # после вращения — выравнивание (у рывка 45° в acro его нет)
    dash: int = 0  # направление рывка 45° в прошлом тике (чтобы поймать нажатие)
    dash_time: float = 0.0  # с — сколько уже держим рывок
    acro_rates: tuple[float, float, float] = (0.0, 0.0, 0.0)


def throttle_to_thrust(throttle: float, limits: FlightLimits) -> int:
    fraction = (clamp(throttle) + 1.0) / 2.0
    if fraction < MOTOR_OFF_FRACTION:
        return 0
    return round(limits.thrust_min + fraction * (limits.thrust_max - limits.thrust_min))


def _height_setpoint(mode: FlightMode, sticks: Sticks, height: float, limits: FlightLimits) -> Setpoint:
    yawrate = -sticks.yaw * limits.max_yaw_rate
    if mode is FlightMode.HOVER:
        return HoverSetpoint(
            vx=sticks.pitch * limits.max_speed,
            vy=-sticks.roll * limits.max_speed,
            yawrate=yawrate,
            z=height,
        )
    return ZDistanceSetpoint(
        roll=sticks.roll * limits.max_angle + limits.trim_roll,
        pitch=-(sticks.pitch * limits.max_angle + limits.trim_pitch),
        yawrate=yawrate,
        z=height,
    )


def _attitude_setpoint(sticks: Sticks, thrust: int, limits: FlightLimits) -> AttitudeSetpoint:
    return AttitudeSetpoint(
        roll=sticks.roll * limits.max_angle + limits.trim_roll,
        pitch=sticks.pitch * limits.max_angle + limits.trim_pitch,
        yawrate=sticks.yaw * limits.max_yaw_rate,
        thrust=thrust,
    )


# Первый setpoint с нулевой тягой снимает защиту прошивки от случайного газа.
_UNLOCK_THRUST = AttitudeSetpoint(0.0, 0.0, 0.0, 0)


def _arming_setpoint(mode: FlightMode) -> Setpoint | None:
    if mode is FlightMode.ACRO:
        return replace(_UNLOCK_THRUST, rate=True)
    return _UNLOCK_THRUST if mode in (FlightMode.MANUAL, FlightMode.ACRO) else None


def _motors_off(message: str = "") -> tuple[FlightState, Output]:
    return FlightState(phase=Phase.IDLE), Output(StopSetpoint(), (Action.DISARM,), message)


def _takeoff_refusal(sticks: Sticks, mode: FlightMode, limits: FlightLimits) -> str:
    """Пустая строка — можно взлетать, иначе — почему нельзя."""
    if mode in (FlightMode.MANUAL, FlightMode.ACRO):
        return "" if sticks.throttle <= THROTTLE_LOW else t("Опусти газ в самый низ, потом взлетай")
    if apply_deadzone(sticks.throttle, limits.hover_throttle_deadzone) != 0.0:
        return t("Поставь газ в центр, потом взлетай")
    return ""


def _arming_actions(mode: FlightMode) -> tuple[Action, ...]:
    return (Action.ARM,) if mode in (FlightMode.MANUAL, FlightMode.ACRO) else (Action.ARM, Action.RESET_ESTIMATOR)


def _on_ground(state, dt, sticks, command, mode, limits):
    if command.throw:
        return _wait_for_throw(state, sticks, mode, limits)
    if not command.takeoff:
        return state, Output()
    refusal = _takeoff_refusal(sticks, mode, limits)
    if refusal:
        return state, Output(message=refusal)
    return FlightState(phase=Phase.ARMING), Output(_arming_setpoint(mode), _arming_actions(mode))


def _wait_for_throw(state, sticks, mode, limits):
    if mode is FlightMode.ACRO:
        return state, Output(message=t("Acro: бросок отключён — взлетай с земли с газом внизу"))
    if mode in (FlightMode.MANUAL, FlightMode.ACRO):
        # Газ в manual/acro можно выставить и после: моторы всё равно стоят, пока дрон в руке.
        message = t("Бросок: подними газ до висения и подбрось дрон — поймаю. B — отмена")
    else:
        refusal = _takeoff_refusal(sticks, mode, limits)
        if refusal:
            return state, Output(message=refusal)
        message = t("Бросок: подбрось дрон — поймаю и повисну на этой высоте. B — отмена")
    return FlightState(phase=Phase.THROW_READY), Output(_arming_setpoint(mode), _arming_actions(mode), message)


def _throw_ready(state, dt, sticks, command, mode, limits):
    if command.throw or command.land:
        return _motors_off(t("Бросок отменён"))
    timer = state.timer + dt
    if timer > THROW_WAIT:
        return _motors_off(t("Броска не было {seconds:.0f} с — моторы выключены", seconds=THROW_WAIT))
    waiting_state = replace(state, timer=timer, in_hand=state.in_hand or not command.thrown)
    # Арминг — каждый тик: прошивка снимает его, если дрон в руке перевернуть или долго не взлетать.
    waiting = Output(_arming_setpoint(mode), (Action.REARM,))
    if not (command.thrown and state.in_hand) or not command.estimator_ready:
        return waiting_state, waiting
    if mode is FlightMode.MANUAL and sticks.throttle < THROW_MIN_THROTTLE:
        return waiting_state, replace(waiting, message=t("Газ ниже середины — ловить нечем, подними газ"))
    return _caught(sticks, command, mode, limits)


def _caught(sticks, command, mode, limits):
    """Дрон падает после броска: взводим моторы прямо в воздухе, стабилизатор сам выравнивает его."""
    message = t("Поймал! Дальше — как обычно")
    if mode in (FlightMode.MANUAL, FlightMode.ACRO):
        thrust = throttle_to_thrust(sticks.throttle, limits)
        state = FlightState(phase=Phase.FLYING, thrust=thrust, rearm=CATCH_REARM)
        return state, Output(_attitude_setpoint(sticks, thrust, limits), (Action.ARM,), message)
    height = limits.takeoff_height if command.height is None else command.height
    height = clamp(height, limits.min_height, limits.max_height)
    state = FlightState(phase=Phase.FLYING, height=height, rearm=CATCH_REARM)
    return state, Output(_height_setpoint(mode, sticks, height, limits), (Action.ARM,), message)


def _killed(state, dt, sticks, command, mode, limits):
    timer = state.timer + dt
    if timer + _EPS >= KILL_COOLDOWN:
        return _on_ground(replace(state, timer=timer), dt, sticks, command, mode, limits)
    message = t("Подожди секунду после аварийной остановки") if command.takeoff else ""
    setpoint = StopSetpoint() if timer < KILL_REPEAT else None
    return replace(state, timer=timer), Output(setpoint, message=message)


def _arming(state, dt, sticks, command, mode, limits):
    if command.land:
        return _motors_off()
    if mode in (FlightMode.MANUAL, FlightMode.ACRO) and sticks.throttle > THROTTLE_LOW:
        return _motors_off(t("Газ подняли во время арминга — взлёт отменён"))
    timer = state.timer + dt
    if timer + _EPS < limits.arm_delay:
        return replace(state, timer=timer), Output(_arming_setpoint(mode))
    if mode in (FlightMode.MANUAL, FlightMode.ACRO):
        return FlightState(phase=Phase.FLYING), Output(_arming_setpoint(mode))
    if not command.estimator_ready:
        if timer > limits.arm_delay + ESTIMATOR_TIMEOUT:
            return _motors_off(t("Дрон не смог определить своё положение — взлёт отменён"))
        return replace(state, timer=timer), Output(message=t("Жду, пока дрон определит положение…"))
    return FlightState(phase=Phase.TAKING_OFF), Output(_height_setpoint(mode, NEUTRAL, 0.0, limits))


def _taking_off(state, dt, sticks, command, mode, limits):
    if command.land:
        return _landing(replace(state, phase=Phase.LANDING), dt, sticks, command, mode, limits)
    height = state.height + limits.takeoff_speed * dt
    if height + _EPS >= limits.takeoff_height:
        height = limits.takeoff_height
        phase = Phase.FLYING
    else:
        phase = Phase.TAKING_OFF
    return replace(state, phase=phase, height=height), Output(_height_setpoint(mode, NEUTRAL, height, limits))


def _soft_acro_rate(previous: float, target: float, dt: float, acceleration: float) -> float:
    """Ramp into rotation; release and reduced input take effect immediately."""
    if previous * target < 0:
        previous = 0.0
    if abs(target) <= abs(previous):
        return target
    change = acceleration * max(0.0, dt)
    return previous + clamp(target - previous, -change, change)


def _flying(state, dt, sticks, command, mode, limits):
    if command.land:
        return _landing(replace(state, phase=Phase.LANDING), dt, sticks, command, mode, limits)
    axis = _flip_axis(command)
    if axis != (0, 0):
        return _start_flip(state, sticks, mode, limits, axis)
    state, dash = _dash(state, dt, sticks, command, mode, limits)
    if dash is not None:
        return state, dash
    if mode is FlightMode.ACRO:
        thrust = throttle_to_thrust(sticks.throttle, limits)
        targets = (
            apply_expo(clamp(sticks.roll), limits.acro_expo) * limits.max_acro_rate,
            apply_expo(clamp(sticks.pitch), limits.acro_expo) * limits.max_acro_rate,
            apply_expo(clamp(sticks.yaw), limits.acro_expo) * limits.max_yaw_rate,
        )
        rates = tuple(_soft_acro_rate(previous, target, dt, limits.acro_acceleration)
                      for previous, target in zip(state.acro_rates, targets)) if thrust else (0.0, 0.0, 0.0)
        roll_rate, pitch_rate, yaw_rate = rates
        acro_setpoint = AttitudeSetpoint(roll=roll_rate, pitch=pitch_rate, yawrate=yaw_rate, thrust=thrust, rate=True)
        return _rearm_after_catch(replace(state, thrust=thrust, acro_rates=rates), Output(acro_setpoint), dt)
    if mode is FlightMode.MANUAL:
        thrust = throttle_to_thrust(sticks.throttle, limits)
        return _rearm_after_catch(replace(state, thrust=thrust), Output(_attitude_setpoint(sticks, thrust, limits)), dt)
    climb = apply_deadzone(sticks.throttle, limits.hover_throttle_deadzone) * limits.climb_rate
    height = clamp(state.height + climb * dt, limits.min_height, limits.max_height)
    return _rearm_after_catch(replace(state, height=height), Output(_height_setpoint(mode, sticks, height, limits)), dt)


def _rearm_after_catch(state: FlightState, output: Output, dt: float) -> tuple[FlightState, Output]:
    if state.rearm <= _EPS:
        return state, output
    return replace(state, rearm=state.rearm - dt), replace(output, actions=output.actions + (Action.REARM,))


def _landing(state, dt, sticks, command, mode, limits):
    if mode in (FlightMode.MANUAL, FlightMode.ACRO):
        # Без датчика высоты знать, где земля, нельзя — просто плавно убираем газ.
        thrust = round(state.thrust - limits.thrust_max / limits.land_thrust_ramp * dt)
        if thrust <= limits.thrust_min:
            return _motors_off()
        if mode is FlightMode.ACRO:
            return replace(state, thrust=thrust), Output(AttitudeSetpoint(0.0, 0.0, 0.0, thrust, rate=True))
        return replace(state, thrust=thrust), Output(_attitude_setpoint(sticks, thrust, limits))
    height = state.height - limits.land_speed * dt
    if height <= LANDED_HEIGHT:
        return _motors_off()
    return replace(state, height=height), Output(_height_setpoint(mode, NEUTRAL, height, limits))


_FLIP_LABELS = {(0, 1): N_("Сальто вперёд!"), (0, -1): N_("Сальто назад!"),
                (1, 0): N_("Бочка вправо!"), (-1, 0): N_("Бочка влево!")}


def _flip_axis(command: Command) -> tuple[int, int]:
    return (int(command.flip_right) - int(command.flip_left), int(command.flip_forward) - int(command.flip_backward))


def _boosted(thrust: int, factor: float, limits: FlightLimits) -> int:
    return max(limits.thrust_min, min(THRUST_CAP, round(thrust * factor)))


def _start_flip(state, sticks, mode, limits, axis):
    if axis[0] and axis[1]:
        return state, Output(message=t("Сальто: нажми одну стрелку"))
    if mode not in (FlightMode.MANUAL, FlightMode.ACRO):
        return state, Output(message=t("Сальто — только в режиме manual или FPV (F на земле)"))
    thrust = throttle_to_thrust(sticks.throttle, limits)
    if thrust == 0:
        return state, Output(message=t("Сальто: сначала подними газ"))
    flip = replace(state, phase=Phase.FLIPPING, timer=0.0, thrust=thrust, flip_axis=axis, flip_stage="boost",
                   flip_target=limits.flip_angle, flip_turned=0.0, flip_rate=limits.flip_rate,
                   flip_boost_thrust=_boosted(thrust, limits.flip_boost, limits),
                   flip_spin_thrust=_boosted(thrust, limits.flip_spin_thrust, limits), flip_recover=True)
    return flip, replace(_flip_output(flip, mode, limits), message=t(_FLIP_LABELS[axis]))


def _dash(state, dt, sticks, command, mode, limits):
    """Рывок 45°, пока зажат курок: дрон резко наклоняется и набирает скорость, выравнивает пилот.

    manual — наклон 45° с добавкой тяги, отпустил курок — стик снова управляет наклоном;
    althold — тот же наклон, высоту держит дрон; acro — быстрый доворот на 45° и дальше всё сам.
    Возвращает (состояние, Output) или (состояние, None) — рывка нет, летим как обычно.
    """
    direction = command.dash
    pressed = direction != 0 and direction != state.dash
    held = state.dash_time + dt if direction and not pressed else 0.0
    state = replace(state, dash=direction, dash_time=held)
    if direction == 0:
        return state, None
    label = t("Рывок 45° вперёд!") if direction > 0 else t("Рывок 45° назад!")
    if mode is FlightMode.HOVER:
        return state, (Output(message=t("Рывок 45° — в режимах althold, manual и FPV")) if pressed else None)
    thrust = throttle_to_thrust(sticks.throttle, limits)
    if mode is FlightMode.ACRO:
        if not pressed or thrust == 0:
            return state, None
        tilt = replace(state, phase=Phase.FLIPPING, timer=0.0, thrust=thrust, flip_axis=(0, direction),
                       flip_stage="spin", flip_target=limits.dash_angle, flip_turned=0.0, flip_rate=limits.dash_rate,
                       flip_boost_thrust=thrust, flip_spin_thrust=thrust, flip_recover=False)
        tilt, output = _spin(tilt, dt, sticks, Command(dash=direction), mode, limits)
        return tilt, replace(output, message=label)
    if state.dash_time > DASH_MAX_TIME or (mode is FlightMode.MANUAL and thrust == 0):
        return state, None
    angle = direction * limits.dash_angle
    yawrate = sticks.yaw * limits.max_yaw_rate
    if mode is FlightMode.ALTHOLD:
        setpoint = ZDistanceSetpoint(limits.trim_roll, -(angle + limits.trim_pitch), -yawrate, state.height)
    else:
        setpoint = AttitudeSetpoint(sticks.roll * limits.max_angle + limits.trim_roll, angle + limits.trim_pitch,
                                    yawrate, _boosted(thrust, limits.dash_boost, limits))
        state = replace(state, thrust=thrust)
    return _rearm_after_catch(state, Output(setpoint, message=label if pressed else ""), dt)


def _flip_output(state: FlightState, mode: FlightMode, limits: FlightLimits) -> Output:
    """Подскок и выравнивание. «0, 0» по скоростям безопасно, пока прошивка ещё в режиме углов: это «ровно»."""
    if state.flip_stage == "recover" and mode is FlightMode.MANUAL:
        return Output(AttitudeSetpoint(limits.trim_roll, limits.trim_pitch, 0.0, state.flip_boost_thrust))
    return Output(AttitudeSetpoint(0.0, 0.0, 0.0, state.flip_boost_thrust, rate=True))


def _spin(state, dt, sticks, command, mode, limits):
    """Вращение: угол набираем по скорости, последний пакет — ровно на остаток до цели."""
    left = state.flip_target - state.flip_turned
    if left <= _EPS:
        if not state.flip_recover:
            return _end_flip(state, dt, sticks, command, mode, limits, "")
        state = replace(state, flip_stage="recover", timer=0.0)
        return state, _flip_output(state, mode, limits)
    rate = min(state.flip_rate, left / dt) if dt > 0 else state.flip_rate
    state = replace(state, flip_turned=state.flip_turned + rate * dt)
    roll_axis, pitch_axis = state.flip_axis
    return state, Output(AttitudeSetpoint(roll_axis * rate, pitch_axis * rate, 0.0, state.flip_spin_thrust, rate=True))


def _end_flip(state, dt, sticks, command, mode, limits, message):
    state = replace(state, phase=Phase.FLYING, timer=0.0, flip_axis=(0, 0), flip_stage="", flip_turned=0.0,
                    acro_rates=(0.0, 0.0, 0.0))
    state, output = _flying(state, dt, sticks, Command(dash=command.dash), mode, limits)
    return state, replace(output, message=message or output.message)


def _flipping(state, dt, sticks, command, mode, limits):
    """Сальто: подскок (и переход прошивки на угловые скорости) -> вращение -> выравнивание с запасом тяги."""
    if command.land:
        return _landing(replace(state, phase=Phase.LANDING), dt, sticks, command, mode, limits)
    if throttle_to_thrust(sticks.throttle, limits) == 0:
        return _end_flip(state, dt, sticks, command, mode, limits, t("Газ в ноль — манёвр прерван"))
    if state.flip_stage == "spin":
        return _spin(state, dt, sticks, command, mode, limits)
    timer = state.timer + dt
    if state.flip_stage == "boost":
        if timer + _EPS >= limits.flip_boost_time and command.rate_ready:
            return _spin(replace(state, flip_stage="spin", timer=0.0), dt, sticks, command, mode, limits)
        if timer > limits.flip_boost_time + FLIP_SWITCH_TIMEOUT:
            return _end_flip(state, dt, sticks, command, mode, limits,
                             t("Дрон не перешёл на угловые скорости — сальто отменено"))
    elif timer + _EPS >= limits.flip_recover_time:
        return _end_flip(state, dt, sticks, command, mode, limits, t("Сальто готово!"))
    state = replace(state, timer=timer)
    return state, _flip_output(state, mode, limits)

_HANDLERS = {
    Phase.IDLE: _on_ground,
    Phase.KILLED: _killed,
    Phase.ARMING: _arming,
    Phase.THROW_READY: _throw_ready,
    Phase.TAKING_OFF: _taking_off,
    Phase.FLYING: _flying,
    Phase.LANDING: _landing,
    Phase.FLIPPING: _flipping,
}


def step(
    state: FlightState,
    dt: float,
    sticks: Sticks,
    command: Command,
    mode: FlightMode,
    limits: FlightLimits,
) -> tuple[FlightState, Output]:
    """Один тик управления. kill всегда главнее всего остального."""
    if command.kill:
        actions = () if state.phase is Phase.KILLED else (Action.DISARM,)
        return FlightState(phase=Phase.KILLED), Output(StopSetpoint(), actions)
    return _HANDLERS[state.phase](state, dt, sticks, command, mode, limits)


def failsafe_setpoint(last: Setpoint, stale: float, limits: FlightLimits) -> Setpoint:
    """Что слать, если главный цикл молчит `stale` секунд (например, окно тащат за заголовок).

    До STALE_HOLD повторяем последнюю команду, потом зависаем без движения,
    после STALE_LAND сами снижаемся и глушим моторы.
    «Без движения» — с тримом из limits (у failsafe-потока это трим из config.json на момент запуска).
    """
    if stale < STALE_HOLD or isinstance(last, StopSetpoint):
        return last
    descent_time = max(0.0, stale - STALE_LAND)
    if isinstance(last, HoverSetpoint):
        z = last.z - descent_time * limits.land_speed
        return HoverSetpoint(0.0, 0.0, 0.0, z) if z > LANDED_HEIGHT else StopSetpoint()
    if isinstance(last, ZDistanceSetpoint):
        z = last.z - descent_time * limits.land_speed
        level = ZDistanceSetpoint(limits.trim_roll, -limits.trim_pitch, 0.0, z)
        return level if z > LANDED_HEIGHT else StopSetpoint()
    thrust = round(last.thrust - descent_time * limits.thrust_max / limits.land_thrust_ramp)
    level = (AttitudeSetpoint(0.0, 0.0, 0.0, thrust, rate=True) if last.rate
             else AttitudeSetpoint(limits.trim_roll, limits.trim_pitch, 0.0, thrust))
    return level if thrust > limits.thrust_min else StopSetpoint()
