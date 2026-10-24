"""Настройки: значения по умолчанию + необязательный config.json поверх них."""

from __future__ import annotations

import contextlib
import json
import math
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Callable

from cfpilot.i18n import LANGUAGES, t

DEFAULT_URI = "radio://0/80/2M/E7E7E7E7E7"
MODES = ("auto", "hover", "althold", "manual", "acro")
INPUTS = ("auto", "keyboard", "joystick")
MAX_THRUST = 0xFFFF
MAX_JOYSTICK_INDEX = 63
MAX_TRIM = 10.0  # град — больше трима не бывает нужно, это уже кривая рама
MIN_AXIS_RANGE = 0.2  # ход оси при калибровке меньше этого — стик явно не двигали
# Проверка ручных правок профиля (мастер калибровки требует больше): центр стика почти в упоре
# или газ с крошечным ходом — дрожание оси превращалось бы в полный наклон или полный газ.
MIN_STICK_HALF_SPAN = 0.15
MIN_THROTTLE_SPAN = 0.5

# Разумные пределы для комнатных полётов: (минимум, максимум, целое ли число)
_FLIGHT_BOUNDS = {
    "takeoff_height": (0.1, 5.0, False),
    "min_height": (0.0, 5.0, False),
    "max_height": (0.1, 5.0, False),
    "takeoff_speed": (0.05, 1.5, False),
    "land_speed": (0.05, 1.5, False),
    "max_speed": (0.05, 3.0, False),
    "climb_rate": (0.05, 2.0, False),
    "max_yaw_rate": (1.0, 360.0, False),
    "max_angle": (1.0, 45.0, False),
    "arm_delay": (0.0, 10.0, False),
    "thrust_min": (0, MAX_THRUST, True),
    "thrust_max": (1, MAX_THRUST, True),
    "land_thrust_ramp": (0.5, 30.0, False),
    "hover_throttle_deadzone": (0.0, 0.5, False),
    "trim_roll": (-MAX_TRIM, MAX_TRIM, False),
    "trim_pitch": (-MAX_TRIM, MAX_TRIM, False),
    "max_acro_rate": (50.0, 1000.0, False),
    "acro_expo": (0.0, 1.0, False),
    "acro_acceleration": (30.0, 3000.0, False),
    "flip_rate": (180.0, 1800.0, False),
    "flip_angle": (90.0, 720.0, False),
    "flip_boost_time": (0.0, 1.0, False),
    "flip_boost": (1.0, 2.0, False),
    "flip_spin_thrust": (0.2, 1.5, False),
    "flip_recover_time": (0.05, 2.0, False),
    "dash_angle": (10.0, 60.0, False),
    "dash_boost": (1.0, 2.0, False),
    "dash_rate": (90.0, 1500.0, False),
}


class ConfigError(ValueError):
    """Ошибка в config.json — текст показывается пользователю как есть."""


def _check_number(name: str, value: Any, low: float, high: float, integer: bool = False) -> None:
    kinds = (int,) if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or not math.isfinite(value):
        kind = t("целое число") if integer else t("число")
        raise ConfigError(t("{name}: нужно {kind}, а не {value!r}", name=name, kind=kind, value=value))
    if not low <= value <= high:
        raise ConfigError(t("{name} должно быть от {low} до {high}, сейчас {value}", name=name, low=low, high=high,
                             value=value))


def _check_bool(name: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ConfigError(t("{name}: нужно true или false, а не {value!r}", name=name, value=value))


def _check_index(name: str, value: Any) -> None:
    _check_number(name, value, 0, MAX_JOYSTICK_INDEX, integer=True)


@dataclass(frozen=True)
class FlightLimits:
    takeoff_height: float = 0.5  # м
    min_height: float = 0.2  # м, ниже стиком не опустить (посадка — клавишей L)
    max_height: float = 1.5  # м
    takeoff_speed: float = 0.4  # м/с
    land_speed: float = 0.3  # м/с
    max_speed: float = 0.5  # м/с, горизонтальная скорость в режиме hover
    climb_rate: float = 0.4  # м/с, набор/снижение стиком газа
    max_yaw_rate: float = 90.0  # град/с
    max_angle: float = 15.0  # град, наклон в режимах althold/manual
    arm_delay: float = 1.0  # с, пауза после арминга перед взлётом
    thrust_min: int = 10001  # тяга, с которой моторы начинают крутиться (manual)
    thrust_max: int = 50000  # потолок тяги в manual (абсолютный максимум 65535)
    land_thrust_ramp: float = 4.0  # с, за сколько газ уходит с максимума в ноль при посадке (manual)
    hover_throttle_deadzone: float = 0.1  # центр стика газа, где высота держится
    trim_roll: float = 0.0  # град, постоянный наклон вправо (+) против сноса (althold/manual)
    trim_pitch: float = 0.0  # град, постоянный наклон вперёд (+)
    max_acro_rate: float = 90.0  # град/с, мягкий предел крена/тангажа в Acro
    acro_expo: float = 0.6  # мягче у центра; применяется только к вращению в Acro
    acro_acceleration: float = 240.0  # град/с² — плавный набор угловой скорости
    # Сальто / бочка (manual и acro): подскок -> вращение -> выравнивание.
    flip_rate: float = 720.0  # град/с — скорость вращения
    flip_angle: float = 360.0  # град — сколько крутить (недокручивает — прибавь, перекручивает — убавь)
    flip_boost_time: float = 0.2  # с — подскок перед вращением, чтобы был запас высоты
    flip_boost: float = 1.35  # во сколько раз тяга подскока и выравнивания больше текущей
    flip_spin_thrust: float = 0.6  # во сколько раз тяга во вращении от текущей (меньше — не «выстрелит» вбок)
    flip_recover_time: float = 0.4  # с — выравнивание с добавкой тяги после вращения
    # Рывок 45° (курки R2 / L2): резкий наклон, чтобы быстро набрать скорость.
    dash_angle: float = 45.0  # град
    dash_boost: float = 1.3  # добавка тяги в наклоне (manual), чтобы не терять высоту
    dash_rate: float = 450.0  # град/с — как быстро наклоняется в acro

    def __post_init__(self) -> None:
        for name, (low, high, integer) in _FLIGHT_BOUNDS.items():
            _check_number(f"flight.{name}", getattr(self, name), low, high, integer)
        if not self.min_height <= self.takeoff_height <= self.max_height:
            raise ConfigError(t("нужно flight.min_height <= flight.takeoff_height <= flight.max_height"))
        if self.thrust_min >= self.thrust_max:
            raise ConfigError(t("нужно flight.thrust_min < flight.thrust_max"))


@dataclass(frozen=True)
class KeyboardSettings:
    throttle_rate: float = 0.6  # ход «стика» газа в секунду при удержании W/S (manual)

    def __post_init__(self) -> None:
        _check_number("keyboard.throttle_rate", self.throttle_rate, 0.05, 5.0)


@dataclass(frozen=True)
class AxisMap:
    # Порядок каналов AETR, как у EdgeTX/RadioMaster по умолчанию: CH1..CH4 -> оси 0..3
    roll: int | None = 0
    pitch: int | None = 1
    throttle: int = 2
    yaw: int | None = 3  # None — ось не читается (например, поворот — кнопками L1/R1)

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None and item.name != "throttle":  # без газа в manual был бы вечный средний газ
                continue
            _check_index(f"joystick.axes.{item.name}", value)


@dataclass(frozen=True)
class InvertMap:
    roll: bool = False
    pitch: bool = False
    throttle: bool = False
    yaw: bool = False

    def __post_init__(self) -> None:
        for item in fields(self):
            _check_bool(f"joystick.invert.{item.name}", getattr(self, item.name))


@dataclass(frozen=True)
class Binding:
    """Кнопка пульта или «переключатель на оси» (EdgeTX отдаёт тумблеры CH5+ как оси)."""

    button: int | None = None
    axis: int | None = None
    above: float | None = None
    below: float | None = None

    def __post_init__(self) -> None:
        if (self.button is None) == (self.axis is None):
            raise ConfigError(t("привязка должна содержать либо 'button', либо 'axis'"))
        if self.button is not None:
            _check_index("button", self.button)
            return
        _check_index("axis", self.axis)
        if (self.above is None) == (self.below is None):
            raise ConfigError(t("для 'axis' укажи ровно один порог: 'above' или 'below'"))
        threshold = self.above if self.above is not None else self.below
        _check_number(t("порог above/below"), threshold, -1.0, 1.0)


@dataclass(frozen=True)
class AxisRange:
    """Калибровка оси пульта: сырые значения в крайних положениях и в покое (центр пружины)."""

    axis: int
    low: float
    center: float
    high: float

    def __post_init__(self) -> None:
        _check_index("calibration.axis", self.axis)
        for name in ("low", "center", "high"):
            _check_number(f"calibration.{name}", getattr(self, name), -1.0, 1.0)
        if self.high - self.low < MIN_AXIS_RANGE:
            raise ConfigError(t("калибровка оси {axis}: слишком маленький ход ({low}..{high})", axis=self.axis,
                                 low=self.low, high=self.high))
        if not self.low <= self.center <= self.high:
            raise ConfigError(t("калибровка оси {axis}: center должен быть между low и high", axis=self.axis))


def _check_calibration(ranges: Any, axes: AxisMap, name: str) -> None:
    if not isinstance(ranges, tuple) or not all(isinstance(item, AxisRange) for item in ranges):
        raise ConfigError(t("{name}: нужен список калибровок осей", name=name))
    if len({item.axis for item in ranges}) != len(ranges):
        raise ConfigError(t("{name}: одна ось откалибрована дважды", name=name))
    sticks = {axes.roll, axes.pitch, axes.yaw} - {None}
    for item in ranges:
        if item.axis == axes.throttle and item.high - item.low < MIN_THROTTLE_SPAN:
            raise ConfigError(t("{name}: у газа (ось {axis}) слишком маленький ход — перекалибруй пульт (C)",
                                 name=name, axis=item.axis))
        if item.axis in sticks and min(item.center - item.low, item.high - item.center) < MIN_STICK_HALF_SPAN:
            raise ConfigError(t("{name}: центр оси {axis} почти в упоре — перекалибруй пульт (C)", name=name,
                                 axis=item.axis))


@dataclass(frozen=True)
class Bindings:
    takeoff: Binding | None = None
    land: Binding | None = None
    kill: Binding | None = None
    hold: Binding | None = None  # удержание газа в manual (вкл/выкл)
    yaw_left: Binding | None = None  # поворот на месте, пока зажата
    yaw_right: Binding | None = None
    throw: Binding | None = None  # на земле — ждать броска, в ожидании — отмена
    flip_forward: Binding | None = None  # сальто 360° вперёд (стрелка ↑)
    flip_backward: Binding | None = None  # сальто назад (стрелка ↓)
    flip_left: Binding | None = None  # бочка 360° влево (стрелка ←)
    flip_right: Binding | None = None  # бочка вправо (стрелка →)
    dash_forward: Binding | None = None  # рывок 45° вперёд, пока зажат (R2)
    dash_backward: Binding | None = None  # рывок 45° назад (L2)

    def __post_init__(self) -> None:
        # Одна кнопка на две команды (например, взлёт и аварийный стоп) — опасно.
        # Один тумблер на оси может давать две команды только в разные стороны (above / below).
        seen: dict[tuple, str] = {}
        for item in fields(self):
            binding = getattr(self, item.name)
            if binding is None:
                continue
            key = ("button", binding.button) if binding.button is not None else (
                "axis", binding.axis, binding.above is None)
            if key in seen:
                raise ConfigError(t("bindings: {first} и {second} назначены на одну кнопку или тумблер",
                                     first=seen[key], second=item.name))
            seen[key] = item.name


@dataclass(frozen=True)
class ControllerProfile:
    """Раскладка одного пульта (по имени устройства). Её пишет мастер калибровки (клавиша C)."""

    axes: AxisMap = field(default_factory=AxisMap)
    invert: InvertMap = field(default_factory=InvertMap)
    calibration: tuple[AxisRange, ...] = ()
    bindings: Bindings = field(default_factory=Bindings)

    def __post_init__(self) -> None:
        _check_calibration(self.calibration, self.axes, "calibration")


@dataclass(frozen=True)
class JoystickSettings:
    name_filter: str = ""  # часть имени устройства, например "RadioMaster"; пусто = любое
    axes: AxisMap = field(default_factory=AxisMap)
    invert: InvertMap = field(default_factory=InvertMap)
    deadzone: float = 0.05
    expo: float = 0.3
    bindings: Bindings = field(default_factory=Bindings)
    calibration: tuple[AxisRange, ...] = ()
    # Раскладки откалиброванных пультов: (имя устройства, профиль). Остальным пультам — поля выше.
    profiles: tuple[tuple[str, ControllerProfile], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name_filter, str):
            raise ConfigError(t("joystick.name_filter должен быть строкой"))
        _check_number("joystick.deadzone", self.deadzone, 0.0, 0.5)
        _check_number("joystick.expo", self.expo, 0.0, 1.0)
        _check_calibration(self.calibration, self.axes, "joystick.calibration")

    def profile(self, device: str) -> ControllerProfile | None:
        return dict(self.profiles).get(device)

    def for_controller(self, device: str) -> JoystickSettings:
        """Настройки для подключённого пульта: его профиль из калибровки, а нет профиля — общие."""
        profile = self.profile(device)
        if profile is None:
            return self
        return replace(self, axes=profile.axes, invert=profile.invert, calibration=profile.calibration,
                       bindings=profile.bindings)

    def with_profile(self, device: str, profile: ControllerProfile) -> JoystickSettings:
        others = tuple(item for item in self.profiles if item[0] != device)
        return replace(self, profiles=others + ((device, profile),))


@dataclass(frozen=True)
class AppConfig:
    uri: str = DEFAULT_URI
    mode: str = "auto"
    input: str = "auto"
    flight: FlightLimits = field(default_factory=FlightLimits)
    keyboard: KeyboardSettings = field(default_factory=KeyboardSettings)
    joystick: JoystickSettings = field(default_factory=JoystickSettings)
    language: str = "ru"  # язык окна; переключается и в программе — F2
    fullscreen: bool = False  # во весь экран; переключается и в программе — F11

    def __post_init__(self) -> None:
        if self.language not in LANGUAGES:
            raise ConfigError(t("language должен быть одним из: {options}", options=", ".join(LANGUAGES)))
        _check_bool("fullscreen", self.fullscreen)
        if not isinstance(self.uri, str) or not self.uri:
            raise ConfigError(t("uri должен быть строкой вида radio://0/80/2M/E7E7E7E7E7"))
        if self.mode not in MODES:
            raise ConfigError(t("mode должен быть одним из: {options}", options=", ".join(MODES)))
        if self.input not in INPUTS:
            raise ConfigError(t("input должен быть одним из: {options}", options=", ".join(INPUTS)))


def _section(data: Any, name: str) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(t("'{name}' должен быть объектом {{...}}", name=name))
    return data


def _build(cls: type, data: Any, name: str, nested: dict | None = None):
    """Создаёт dataclass из словаря, ругаясь на опечатки в ключах."""
    values = dict(_section(data, name))
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ConfigError(t("неизвестные ключи в '{name}': {keys}", name=name, keys=", ".join(unknown)))
    values.update(nested or {})
    return cls(**values)


def _binding(data: Any, name: str) -> Binding | None:
    return None if data is None else _build(Binding, data, name)


# До версии с настоящим сальто flip_* на курках R2/L2 означали короткий рывок — теперь это dash_*.
LEGACY_TRIGGER_BINDINGS = {"flip_forward": "dash_forward", "flip_backward": "dash_backward"}


def _migrate_bindings(raw: dict) -> dict:
    """Старые flip_* на осях (курки) переносим в dash_*: иначе курок вместо рывка крутил бы сальто."""
    migrated = dict(raw)
    for old, new in LEGACY_TRIGGER_BINDINGS.items():
        value = raw.get(old)
        if isinstance(value, dict) and "axis" in value and new not in raw:
            migrated[new] = migrated.pop(old)
    return migrated


def _bindings(data: Any, name: str) -> Bindings:
    raw = _migrate_bindings(_section(data, name))
    return _build(Bindings, raw, name, {key: _binding(value, f"{name}.{key}") for key, value in raw.items()})


def _calibration(data: Any, name: str) -> tuple[AxisRange, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ConfigError(t("'{name}' должен быть списком [...]", name=name))
    return tuple(_build(AxisRange, item, f"{name}[{index}]") for index, item in enumerate(data))


def _layout(raw: dict, name: str) -> dict:
    """Части, общие у раскладки по умолчанию и у профиля конкретного пульта."""
    return {
        "axes": _build(AxisMap, raw.get("axes"), f"{name}.axes"),
        "invert": _build(InvertMap, raw.get("invert"), f"{name}.invert"),
        "calibration": _calibration(raw.get("calibration"), f"{name}.calibration"),
        "bindings": _bindings(raw.get("bindings"), f"{name}.bindings"),
    }


# Стрелки DualSense в pygame (SDL2) — кнопки 11..14: ↑ ↓ ← →.
DUALSENSE_DPAD = {"flip_forward": 11, "flip_backward": 12, "flip_left": 13, "flip_right": 14}


def with_dualsense_flips(device: str, bindings: Bindings) -> Bindings:
    """Профилю DualSense из прошлой версии (сальто там не было) сразу даём сальто на стрелках."""
    if "dualsense" not in device.lower() or any(getattr(bindings, action) for action in DUALSENSE_DPAD):
        return bindings
    used = {getattr(bindings, item.name).button for item in fields(bindings) if getattr(bindings, item.name)}
    free = {action: Binding(button=button) for action, button in DUALSENSE_DPAD.items() if button not in used}
    return replace(bindings, **free)


def _profiles(data: Any) -> tuple[tuple[str, ControllerProfile], ...]:
    profiles = []
    for device, raw in _section(data, "joystick.profiles").items():
        name = f'joystick.profiles["{device}"]'
        profile = _build(ControllerProfile, raw, name, _layout(_section(raw, name), name))
        raw_bindings = _section(_section(raw, name).get("bindings"), f"{name}.bindings")
        if _migrate_bindings(raw_bindings) != raw_bindings:  # профиль из прошлой версии
            profile = replace(profile, bindings=with_dualsense_flips(device, profile.bindings))
        profiles.append((device, profile))
    return tuple(profiles)


def _joystick(data: Any) -> JoystickSettings:
    raw = _section(data, "joystick")
    nested = {**_layout(raw, "joystick"), "profiles": _profiles(raw.get("profiles"))}
    return _build(JoystickSettings, raw, "joystick", nested)


def parse_config(data: Any) -> AppConfig:
    raw = _section(data, "config")
    nested = {
        "flight": _build(FlightLimits, raw.get("flight"), "flight"),
        "keyboard": _build(KeyboardSettings, raw.get("keyboard"), "keyboard"),
        "joystick": _joystick(raw.get("joystick")),
    }
    return _build(AppConfig, raw, "config", nested)


def load_config(path: str) -> AppConfig:
    file = Path(path)
    if not file.exists():
        return AppConfig()
    try:
        data = json.loads(file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ConfigError(t("{file}: ошибка JSON в строке {line}: {error}", file=file.name, line=error.lineno,
                             error=error.msg)) from error
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(t("{file}: не удалось прочитать файл: {error}", file=file.name, error=error)) from error
    try:
        return parse_config(data)
    except TypeError as error:
        raise ConfigError(t("{file}: неверное значение: {error}", file=file.name, error=error)) from error


def _rewrite(path: str, change: Callable[[dict], dict], what: str) -> None:
    """Меняет часть config.json, остальные настройки оставляет как есть."""
    file = Path(path)
    temporary = file.with_name(file.name + ".tmp")
    try:
        data = _section(json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}, "config")
        text = json.dumps(change(data), ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(file)  # целиком или никак — config.json не останется полузаписанным
    except (OSError, ValueError) as error:  # ValueError — битый JSON, ConfigError
        with contextlib.suppress(OSError):  # уборка «по возможности»; главная ошибка — ниже
            temporary.unlink(missing_ok=True)
        raise ConfigError(t("{file}: не удалось сохранить {what}: {error}", file=file.name, what=what,
                             error=error)) from error


def save_trim(path: str, roll: float, pitch: float) -> None:
    def change(data: dict) -> dict:
        flight = {**_section(data.get("flight"), "flight"), "trim_roll": roll, "trim_pitch": pitch}
        return {**data, "flight": flight}

    _rewrite(path, change, t("трим"))


def save_preferences(path: str, language: str, fullscreen: bool) -> None:
    """Язык (F2) и полноэкранный режим (F11): какими их оставили, такой программа и откроется."""
    _rewrite(path, lambda data: {**data, "language": language, "fullscreen": fullscreen},
             t("язык и полноэкранный режим"))


def save_uri(path: str, uri: str) -> None:
    """Адрес дрона, найденного на другом канале: в следующий раз программа подключится к нему сразу."""
    _rewrite(path, lambda data: {**data, "uri": uri}, t("адрес дрона"))


def profile_to_json(profile: ControllerProfile) -> dict:
    bindings = {}
    for item in fields(Bindings):
        binding = getattr(profile.bindings, item.name)
        if binding is not None:
            bindings[item.name] = {key: value for key, value in asdict(binding).items() if value is not None}
    return {
        "axes": asdict(profile.axes),
        "invert": asdict(profile.invert),
        "calibration": [asdict(item) for item in profile.calibration],
        "bindings": bindings,
    }


def save_controller_profile(path: str, device: str, profile: ControllerProfile) -> None:
    """Раскладку пульта пишем в joystick.profiles под его именем — при следующем подключении она подхватится."""

    def change(data: dict) -> dict:
        joystick = _section(data.get("joystick"), "joystick")
        profiles = {**_section(joystick.get("profiles"), "joystick.profiles"), device: profile_to_json(profile)}
        return {**data, "joystick": {**joystick, "profiles": profiles}}

    _rewrite(path, change, t("калибровку пульта"))
