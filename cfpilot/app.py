"""Главный цикл: ввод (клавиатура / пульт) -> логика полёта -> радио -> экран."""

from __future__ import annotations

import time
from dataclasses import fields, replace
from enum import Enum
from typing import Callable, Sequence

import pygame

from cfpilot.calibration import Calibrator, Joystick, read_buttons
from cfpilot.config import MAX_TRIM, AppConfig, ControllerProfile, FlightLimits
from cfpilot.controls import (
    KeyState,
    Sticks,
    binding_active,
    binding_problems,
    apply_deadzone,
    clamp,
    joystick_sticks,
    keyboard_sticks,
    step_throttle_level,
)
from cfpilot.flight import (
    ACTIVE_PHASES,
    THROW_MIN_THROTTLE,
    Command,
    FlightMode,
    FlightState,
    Phase,
    step,
    throttle_to_thrust,
)
from cfpilot.i18n import N_, language, next_language, set_language, t
from cfpilot.link import DeckInfo, Telemetry, supervisor_problem
from cfpilot.ui import Dashboard, View
from cfpilot.window import toggle_fullscreen

LOOP_HZ = 50  # setpoint-ы надо слать чаще, чем раз в 0.5 с, иначе прошивка выровняет/выключит дрон
MAX_DT = 0.1  # с — если кадр подвис, не делаем гигантский шаг
STALL_LIMIT = 0.5  # с — кадр дольше этого = окно подвисало, сажаем дрон
MESSAGE_SECONDS = 4.0
SHUTDOWN_LAND_TIMEOUT = 15.0  # с
HOLD_DEADZONE = 0.15  # в удержании стик газа у центра ничего не меняет
HOLD_THROTTLE_RATE = 0.3  # ход «газа» в секунду, если в удержании стик газа отклонён до упора
CATCH_BAND = 0.1  # после удержания стик газа «подхватывает» газ, когда подходит к нему ближе этого
# с — взлёт заблокирован в начале сеанса и сразу после калибровки: Enter — это и «сохранить»
# в мастере, и «взлёт», и лишнее нажатие (двойной Enter) не должно поднять дрон.
TAKEOFF_LOCK = 1.0
LOST_REMOVAL = -1  # instance_id: «что-то отключилось, но pygame не сказал что» (см. poll_events)

# Сканкоды = физические клавиши, поэтому всё работает и на русской раскладке.
KEY_FIELDS = {
    pygame.KSCAN_UP: "forward",
    pygame.KSCAN_DOWN: "back",
    pygame.KSCAN_LEFT: "left",
    pygame.KSCAN_RIGHT: "right",
    pygame.KSCAN_W: "up",
    pygame.KSCAN_S: "down",
    pygame.KSCAN_A: "yaw_left",
    pygame.KSCAN_D: "yaw_right",
}
TAKEOFF_KEYS = frozenset({pygame.KSCAN_T, pygame.KSCAN_RETURN, pygame.KSCAN_KP_ENTER})
LAND_KEYS = frozenset({pygame.KSCAN_L})
KILL_KEYS = frozenset({pygame.KSCAN_SPACE})
QUIT_KEYS = frozenset({pygame.KSCAN_ESCAPE})
MONITOR_KEYS = frozenset({pygame.KSCAN_J})
CALIBRATE_KEYS = frozenset({pygame.KSCAN_C})
REBOOT_KEYS = frozenset({pygame.KSCAN_R})
THROW_KEYS = frozenset({pygame.KSCAN_B})
FPV_KEYS = frozenset({pygame.KSCAN_F})
LANGUAGE_KEYS = frozenset({pygame.KSCAN_F2})
FULLSCREEN_KEYS = frozenset({pygame.KSCAN_F11})
# Сальто с клавиатуры — цифровой блок: 8 ↑, 2 ↓, 4 ←, 6 →.
FLIP_KEYS = {
    pygame.KSCAN_KP_8: "flip_forward",
    pygame.KSCAN_KP_2: "flip_backward",
    pygame.KSCAN_KP_4: "flip_left",
    pygame.KSCAN_KP_6: "flip_right",
}
SHIFT_KEYS = frozenset({pygame.KSCAN_LSHIFT, pygame.KSCAN_RSHIFT})
# Мастер калибровки: клавиша -> метод Calibrator (одни и те же на экране подключения и в полёте).
CALIBRATION_ACTIONS = {
    pygame.KSCAN_RETURN: "confirm",
    pygame.KSCAN_KP_ENTER: "confirm",
    pygame.KSCAN_BACKSPACE: "skip",
    pygame.KSCAN_LEFT: "back",
    pygame.KSCAN_ESCAPE: "cancel",
}
TRIM_STEP = 0.5  # град за одно нажатие Shift+стрелка
TRIM_KEYS = {  # Shift+стрелка -> (шаги трима вправо, шаги вперёд)
    pygame.KSCAN_UP: (0, 1),
    pygame.KSCAN_DOWN: (0, -1),
    pygame.KSCAN_LEFT: (-1, 0),
    pygame.KSCAN_RIGHT: (1, 0),
}
AIRBORNE = frozenset({Phase.TAKING_OFF, Phase.FLYING, Phase.LANDING, Phase.FLIPPING})
FLIP_ACTIONS = ("flip_forward", "flip_backward", "flip_left", "flip_right")
NO_KILL_SWITCH = N_("На пульте не назначен аварийный стоп (kill) — назначь его в калибровке (C)")
SaveProfile = Callable[[str, ControllerProfile], str]  # сохраняет раскладку пульта; текст ошибки или ""


class ExitReason(Enum):
    QUIT = "quit"
    LINK_LOST = "link_lost"  # связь пропала — переподключаемся
    REBOOT = "reboot"  # R — перезагрузить дрон по радио и подключиться снова


def key_state(held: frozenset[int]) -> KeyState:
    return KeyState(**{name: scancode in held for scancode, name in KEY_FIELDS.items()})


def calibration_key(calibrator: Calibrator, scancode: int) -> None:
    action = CALIBRATION_ACTIONS.get(scancode)
    if action is not None:
        getattr(calibrator, action)()


def all_joysticks() -> list[Joystick]:
    return [pygame.joystick.Joystick(index) for index in range(pygame.joystick.get_count())]


def poll_events() -> list[pygame.event.Event]:
    """pygame.event.get(), который переживает баг pygame 2.6.

    Событие «пульт отключён» для устройства, которое программа не открывала (бывает сразу при запуске:
    Windows показывает и тут же убирает какое-то HID-устройство), бросает KeyError внутри event.get() —
    программа падала на первом же кадре. Такие события забираем отдельно, чтобы остальные (клавиши!)
    не пропали вместе с ними.
    """
    try:
        removed = pygame.event.get(pygame.JOYDEVICEREMOVED)
    except (SystemError, KeyError):
        # Вместе с неоткрытым устройством могло отключиться и наше — получатель проверит сам.
        removed = [pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=LOST_REMOVAL)]
    events = removed + pygame.event.get(exclude=pygame.JOYDEVICEREMOVED)
    return [event for event in events if not window_key(event)]


def window_key(event: pygame.event.Event) -> bool:
    """F2 — язык, F11 — весь экран: работают на любом экране, дальше такое нажатие не идёт."""
    if event.type != pygame.KEYDOWN:
        return False
    if event.scancode in LANGUAGE_KEYS:
        set_language(next_language(language()))
        return True
    if event.scancode in FULLSCREEN_KEYS:
        toggle_fullscreen()
        return True
    return False


def unplugged(joystick: Joystick | None, list_joysticks: Callable[[], Sequence[Joystick]]) -> bool:
    """Отключён ли пульт — для случая, когда событие об этом потерялось (LOST_REMOVAL)."""
    if joystick is None:
        return False
    try:
        instance_id = joystick.get_instance_id()
    except pygame.error:
        return True
    return all(other.get_instance_id() != instance_id for other in list_joysticks())


class App:
    def __init__(self, config: AppConfig, link, mode: FlightMode, decks: DeckInfo, input_choice: str,
                 screen: pygame.Surface, *, save_profile: SaveProfile | None = None,
                 joysticks: Callable[[], Sequence[Joystick]] = all_joysticks) -> None:
        self._config = config
        self._limits = config.flight  # меняется только трим (Shift+стрелки)
        self._link = link
        self._mode = decks.best_mode() if mode is FlightMode.ACRO else mode
        self._base_mode = self._mode
        self._decks = decks
        self._input_choice = input_choice
        self._dashboard = Dashboard(screen)
        self._save_profile = save_profile
        self._list_joysticks = joysticks
        self._state = FlightState()
        self._held: frozenset[int] = frozenset()
        self._requests = Command()  # нажатия клавиш, накопленные до следующего тика
        self._joystick = None
        self._joystick_settings = config.joystick  # раскладка подключённого пульта (его профиль или общая)
        self._joystick_warning = ""
        self._joystick_scan_needed = input_choice != "keyboard"
        self._calibrator: Calibrator | None = None
        self._takeoff_lock = TAKEOFF_LOCK  # с — сколько ещё взлёт заблокирован
        self._previous_switches: dict[str, bool] = {}  # кнопки пульта в прошлом тике — ловим момент нажатия
        self._throttle_level = -1.0  # газ без пружины: клавиатура в manual или удержание на пульте
        self._hold = False  # удержание газа (кнопка hold на пульте, manual)
        self._catching = False  # удержание выключено, ждём, пока стик газа дойдёт до удерживаемого газа
        self._nudge_ready = False  # в удержании стик газа уже побывал в центре и может подправлять газ
        self._battery_on_ground: float | None = None  # напряжение без нагрузки — процент в полёте берём от него
        self._quitting = False
        self._show_monitor = False
        self._message = ""
        self._message_left = 0.0
        self._sticks = Sticks()
        self._input_source = t("клавиатура")
        self._axes: tuple[float, ...] = ()
        self._buttons: tuple[bool, ...] = ()
        self.running = True
        self.exit_reason = ExitReason.QUIT

    @property
    def phase(self) -> Phase:
        return self._state.phase

    @property
    def telemetry(self) -> Telemetry:
        return self._link.telemetry

    @property
    def thrust(self) -> int:
        return self._state.thrust

    @property
    def limits(self) -> FlightLimits:
        return self._limits

    @property
    def config(self) -> AppConfig:
        """Настройки с тримом и раскладками пультов, изменёнными за сеанс, — для следующего подключения."""
        return replace(self._config, flight=self._limits)

    @property
    def calibrating(self) -> bool:
        return self._calibrator is not None

    # ---------- события ----------

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.request_quit()
        elif event.type == pygame.WINDOWFOCUSLOST:
            self._on_focus_lost()
        elif event.type == pygame.KEYDOWN:
            self._on_key_down(event.scancode)
        elif event.type == pygame.KEYUP:
            self._held = self._held - {event.scancode}
        elif event.type == pygame.JOYDEVICEADDED:
            self._joystick_scan_needed = self._input_choice != "keyboard"
        elif event.type == pygame.JOYDEVICEREMOVED:
            if event.instance_id == LOST_REMOVAL:
                self._check_lost_removal()
            else:
                self._on_device_removed(event.instance_id)

    def _on_device_removed(self, instance_id: int) -> None:
        if self._calibrator is not None:
            self._calibrator.controller_removed(instance_id)
        self._on_joystick_removed(instance_id)

    def _check_lost_removal(self) -> None:
        """pygame потерял событие «пульт отключён» — проверяем свои пульты сами."""
        if self._calibrator is not None and unplugged(self._calibrator.joystick, self._list_joysticks):
            self._calibrator.controller_lost()
        if unplugged(self._joystick, self._list_joysticks):
            self._drop_joystick()

    def _on_key_down(self, scancode: int) -> None:
        if self._calibrator is not None:
            if scancode in KILL_KEYS:  # аварийный стоп работает всегда
                self._requests = replace(self._requests, kill=True)
            else:
                calibration_key(self._calibrator, scancode)
            return
        if scancode in TRIM_KEYS and self._held & SHIFT_KEYS:
            self._adjust_trim(*TRIM_KEYS[scancode])
            return  # стрелку не запоминаем: Shift+стрелка — только трим, без наклона
        repeated = scancode in self._held
        self._held = self._held | {scancode}
        if scancode in TAKEOFF_KEYS:
            self._requests = replace(self._requests, takeoff=True)
        elif scancode in LAND_KEYS:
            self._requests = replace(self._requests, land=True)
        elif scancode in FPV_KEYS:
            if not repeated:
                self._toggle_fpv()
        elif scancode in THROW_KEYS:
            self._requests = replace(self._requests, throw=True)
        elif scancode in FLIP_KEYS:
            if not repeated:
                self._requests = replace(self._requests, **{FLIP_KEYS[scancode]: True})
        elif scancode in KILL_KEYS:
            self._requests = replace(self._requests, kill=True)
        elif scancode in QUIT_KEYS:
            self.request_quit()
        elif scancode in MONITOR_KEYS:
            self._show_monitor = not self._show_monitor
        elif scancode in CALIBRATE_KEYS:
            self._start_calibration()
        elif scancode in REBOOT_KEYS:
            self.request_reboot()

    def _toggle_fpv(self) -> None:
        if self._state.phase in ACTIVE_PHASES:
            self._show(t("F: смена режима только на земле — сначала посади дрон"))
            return
        self._hold = self._catching = self._nudge_ready = False
        self._throttle_level = -1.0
        if self._mode is not FlightMode.ACRO:
            self._base_mode = self._mode
            self._mode = FlightMode.ACRO
            self._show(t("Включён режим FPV (Acro): полное ручное управление!"))
        else:
            self._mode = self._base_mode
            self._show(t("Режим стабилизации ({mode}) включён", mode=self._mode.value))

    def _adjust_trim(self, roll_steps: int, pitch_steps: int) -> None:
        if self._mode is FlightMode.ACRO:
            return
        if self._mode is FlightMode.HOVER:
            self._show(t("Трим не нужен: в hover дрон сам держит место"))
            return
        roll = clamp(self._limits.trim_roll + roll_steps * TRIM_STEP, -MAX_TRIM, MAX_TRIM)
        pitch = clamp(self._limits.trim_pitch + pitch_steps * TRIM_STEP, -MAX_TRIM, MAX_TRIM)
        self._limits = replace(self._limits, trim_roll=round(roll, 2), trim_pitch=round(pitch, 2))
        self._show(t("Трим: вправо {roll:+.1f}°, вперёд {pitch:+.1f}°", roll=roll, pitch=pitch))

    def _on_focus_lost(self) -> None:
        self._held = frozenset()  # иначе «залипнет» клавиша, отпущенная вне окна
        if self._joystick is None and self._state.phase in ACTIVE_PHASES:
            # С клавиатуры без фокуса дрон не остановить (даже Пробелом) — сажаем.
            self._land(t("Окно потеряло фокус — сажаю дрон"))

    def report_stall(self, seconds: float) -> None:
        """Кадр шёл слишком долго (окно тащили, система подвисла) — дрон сажаем."""
        if seconds > STALL_LIMIT and self._state.phase in AIRBORNE:
            self._land(t("Программа подвисала {seconds:.1f} с — сажаю дрон", seconds=seconds))

    def request_quit(self) -> None:
        if self._state.phase not in ACTIVE_PHASES or self._link.error is not None:
            self.running = False
            return
        self._quitting = True
        self._show(t("Сажаю дрон перед выходом…"))

    def request_reboot(self) -> None:
        """R: перезагрузить дрон по радио (снимает блокировку прошивки) и подключиться снова."""
        if self._state.phase in ACTIVE_PHASES:
            self._show(t("Перезагрузка — только на земле. Сначала посади дрон"))
            return
        self.exit_reason = ExitReason.REBOOT
        self.running = False

    def _land(self, message: str) -> None:
        self._requests = replace(self._requests, land=True)
        self._show(message)

    # ---------- пульт ----------

    def attach_joystick(self, joystick) -> None:
        if self._input_choice == "keyboard":
            return
        self._joystick = joystick
        name = joystick.get_name()
        self._joystick_settings = self._config.joystick.for_controller(name)
        axes, buttons = self._read_joystick()
        bindings = self._joystick_settings.bindings
        problems = binding_problems(bindings, len(axes), len(buttons))
        if bindings.kill is None:
            problems.append(t(NO_KILL_SWITCH))
        self._joystick_warning = "; ".join(problems)
        # Тумблер, уже включённый в момент подключения, взлёт не запускает.
        self._previous_switches = self._switches(axes, buttons)
        known = self._config.joystick.profile(name) is not None
        if known:
            self._show(t("Пульт подключён: {name}", name=name))
        else:
            self._show(t("Пульт подключён: {name} — нажми C, чтобы настроить под него кнопки", name=name))

    def _on_joystick_removed(self, instance_id: int) -> None:
        if self._joystick is not None and self._joystick.get_instance_id() == instance_id:
            self._drop_joystick()

    def _drop_joystick(self) -> None:
        """Пульт пропал: дальше — клавиатура (или ожидание пульта), а в воздухе — посадка."""
        self._joystick = None
        self._joystick_settings = self._config.joystick
        self._joystick_warning = ""
        self._joystick_scan_needed = True
        if self._state.phase in ACTIVE_PHASES:
            self._land(t("Пульт отключился — сажаю дрон"))
        else:
            self._show(t("Пульт отключился"))

    def _maybe_open_joystick(self) -> None:
        if not self._joystick_scan_needed or self._joystick is not None:
            return
        if self._state.phase in ACTIVE_PHASES:
            return  # не подменяем управление посреди полёта — подключим после посадки
        self._joystick_scan_needed = False
        wanted = self._config.joystick.name_filter.lower()
        matching = [joystick for joystick in self._list_joysticks() if wanted in joystick.get_name().lower()]
        known = [joystick for joystick in matching if self._config.joystick.profile(joystick.get_name())]
        if matching:
            self.attach_joystick((known or matching)[0])  # откалиброванный пульт — в приоритете

    def _read_joystick(self) -> tuple[tuple[float, ...], tuple[bool, ...]]:
        joystick = self._joystick
        if joystick is None:
            return (), ()
        try:
            axes = tuple(joystick.get_axis(i) for i in range(joystick.get_numaxes()))
            buttons = read_buttons(joystick)
        except pygame.error:  # устройство уже закрыто, а событие об отключении не дошло
            self._drop_joystick()
            return (), ()
        return axes, buttons

    # ---------- калибровка ----------

    def _start_calibration(self) -> None:
        if self._state.phase in ACTIVE_PHASES:
            self._show(t("Калибровка — только на земле. Сначала посади дрон"))
            return
        self._calibrator = Calibrator(self._list_joysticks)
        self._show_monitor = False

    def _update_calibration(self, dt: float) -> None:
        calibrator = self._calibrator
        calibrator.update(dt)
        if not (calibrator.finished or calibrator.cancelled):
            return
        self._calibrator = None
        self._takeoff_lock = TAKEOFF_LOCK
        result = calibrator.result
        if result is None:
            if self._joystick is not None:
                self.attach_joystick(self._joystick)  # кнопки, нажатые в мастере, не должны сработать как взлёт
            self._show(t("Калибровка отменена"))
            return
        device, profile = result
        self._config = replace(self._config, joystick=self._config.joystick.with_profile(device, profile))
        error = self._save_profile(device, profile) if self._save_profile is not None else ""
        self.attach_joystick(calibrator.joystick)
        if error:
            self._show(t("Пульт «{device}» настроен, но не сохранён: {error}", device=device, error=error))
        else:
            self._show(t("Пульт «{device}» настроен и сохранён", device=device))

    # ---------- один тик ----------

    def tick(self, dt: float) -> None:
        if not self.running:
            return  # выход уже решён (например, Esc и T в одном кадре) — ничего не шлём
        if self._link.error is not None and self._calibrator is None:
            self.exit_reason = ExitReason.LINK_LOST  # слать уже некуда — главный цикл переподключится
            self.running = False
            return
        if self._link.failsafe_stopped and self._state.phase in ACTIVE_PHASES:
            self._requests = replace(self._requests, kill=True)
            if self._state.phase is Phase.THROW_READY:
                self._show(t("Программа подвисала — бросок отменён, моторы выключены"))
            else:
                self._show(t("Программа подвисала — дрон сел сам. Взлетай заново (T)"))
        if self._calibrator is not None:
            self._update_calibration(dt)
        else:
            self._maybe_open_joystick()
        axes, buttons = self._read_joystick()
        if self._calibrator is not None:
            # Пока идёт калибровка, пульт ничем не управляет: нажатия — это ответы мастеру, а не взлёт.
            self._sticks = Sticks(throttle=-1.0) if self._mode is FlightMode.MANUAL else Sticks()
            self._input_source = t("калибровка пульта")
            command = Command(kill=self._requests.kill, estimator_ready=self._link.position_ready)
        else:
            self._sticks, self._input_source = self._read_sticks(axes, buttons, dt)
            command = self._command(axes, buttons)
        self._state, output = step(self._state, dt, self._sticks, command, self._mode, self._limits)
        self._link.execute(output)
        self._requests = Command()
        if output.message:
            self._show(output.message)
        self._message_left = max(0.0, self._message_left - dt)
        self._takeoff_lock = max(0.0, self._takeoff_lock - dt)
        if self._state.phase not in ACTIVE_PHASES:
            self._throttle_level = -1.0
            self._hold = self._catching = False
        if self._state.phase not in AIRBORNE:
            self._battery_on_ground = self._link.telemetry.battery
        if self._quitting and (self._state.phase not in ACTIVE_PHASES or self._link.error is not None):
            self.running = False
        self._axes, self._buttons = axes, buttons
        if self._calibrator is not None:
            self._dashboard.draw_calibration(self._calibrator.view())
        else:
            self._dashboard.draw(self.view())

    def _read_sticks(self, axes: tuple[float, ...], buttons: tuple[bool, ...], dt: float) -> tuple[Sticks, str]:
        manual = self._mode in (FlightMode.MANUAL, FlightMode.ACRO)
        if self._joystick is not None:
            sticks = self._held_throttle(joystick_sticks(axes, buttons, self._joystick_settings), dt)
            return sticks, t("пульт ({name})", name=self._joystick.get_name()) + self._hold_label()
        if self._input_choice == "joystick":
            return (Sticks(throttle=-1.0) if manual else Sticks()), t("ПУЛЬТ НЕ ПОДКЛЮЧЁН")
        keys = key_state(self._held)
        if not manual:
            return keyboard_sticks(keys, None), t("клавиатура")
        rate = self._config.keyboard.throttle_rate
        self._throttle_level = step_throttle_level(self._throttle_level, keys, dt, rate)
        return keyboard_sticks(keys, self._throttle_level), t("клавиатура")

    def _held_throttle(self, sticks: Sticks, dt: float) -> Sticks:
        """Удержание: газ стоит, где был, стик газа его только подправляет. После — «подхват»."""
        if self._hold:
            if abs(sticks.throttle) <= HOLD_DEADZONE:
                self._nudge_ready = True  # при включении стик ещё держал газ — подправлять начнём после центра
            if self._nudge_ready:
                nudge = apply_deadzone(sticks.throttle, HOLD_DEADZONE)
                self._throttle_level = clamp(self._throttle_level + nudge * HOLD_THROTTLE_RATE * dt)
            return replace(sticks, throttle=self._throttle_level)
        if self._catching:
            # Без подхвата пружинный стик в центре резко убавил бы газ — дрон бы рухнул.
            if abs(sticks.throttle - self._throttle_level) > CATCH_BAND:
                return replace(sticks, throttle=self._throttle_level)
            self._catching = False
            self._show(t("Газ снова у стика"))
        return sticks

    def _hold_label(self) -> str:
        if self._hold:
            return t(" — газ удерживается")
        return t(" — подведи стик газа к удерживаемому газу") if self._catching else ""

    def _toggle_hold(self) -> None:
        if self._mode is FlightMode.ACRO:
            self._show(t("Acro: газ управляется напрямую, удержание отключено"))
            return
        if self._mode is not FlightMode.MANUAL:
            self._show(t("В режиме {mode} дрон и так держит высоту — просто отпусти стики", mode=self._mode.value))
        elif self._state.phase not in (Phase.FLYING, Phase.THROW_READY):
            self._show(t("Удержание газа включается в полёте или перед броском"))
        elif self._hold:
            self._hold, self._catching = False, True
            self._show(t("Удержание выкл — подведи стик газа к текущему газу, чтобы взять управление"))
        else:
            self._hold, self._catching, self._nudge_ready = True, False, False
            self._throttle_level = self._sticks.throttle  # газ, который сейчас уходит дрону
            self._show(t("Удержание газа: можно отпустить стик, он только подправляет газ"))

    def _switches(self, axes, buttons) -> dict[str, bool]:
        bindings = self._joystick_settings.bindings
        return {item.name: binding_active(getattr(bindings, item.name), axes, buttons) for item in fields(bindings)}

    def _command(self, axes, buttons) -> Command:
        now = self._switches(axes, buttons)
        before = self._previous_switches
        self._previous_switches = now

        def pressed(action: str) -> bool:
            return now[action] and not before.get(action, False)

        if pressed("hold"):
            self._toggle_hold()
        requests = self._requests
        takeoff = self._allowed((requests.takeoff or pressed("takeoff")) and not self._quitting)
        throw = self._allowed((requests.throw or pressed("throw")) and not self._quitting)
        telemetry = self._link.telemetry
        if throw and self._state.phase not in ACTIVE_PHASES and telemetry.thrown is None:
            self._show(t("Дрон не присылает данные датчика ускорения — бросок сейчас не распознать"))
            throw = False
        flying = self._state.phase is Phase.FLYING
        flips = {action: flying and (getattr(requests, action) or pressed(action)) for action in FLIP_ACTIONS}
        return Command(
            takeoff=takeoff,
            land=requests.land or pressed("land") or self._quitting,
            kill=requests.kill or now["kill"],  # kill-тумблер действует, пока включён
            throw=throw,
            **flips,
            dash=int(now["dash_forward"]) - int(now["dash_backward"]),
            rate_ready=self._link.rate_ready,
            estimator_ready=self._link.position_ready,
            thrown=bool(telemetry.thrown),
            height=telemetry.height,
        )

    def _allowed(self, start: bool) -> bool:
        """Взлёт и бросок с земли — только если ничего не мешает (в воздухе те же кнопки — посадка/отмена)."""
        if not start or self._state.phase in ACTIVE_PHASES:
            return start
        blocker = self._takeoff_blocker()
        if blocker:
            self._show(blocker)
        return not blocker

    def _takeoff_blocker(self) -> str:
        """Почему взлетать нельзя; пустая строка — можно. Заряд не проверяем — пилот видит его в окне."""
        if self._takeoff_lock > 0:
            return t("Секунду: взлёт сразу после подключения и калибровки заблокирован — нажми ещё раз")
        return supervisor_problem(self._link.telemetry.supervisor)

    def _show(self, message: str) -> None:
        self._message = message
        self._message_left = MESSAGE_SECONDS

    def _shown_thrust(self) -> int:
        """В ожидании броска моторы стоят — показываем тягу, с которой дрон поймают."""
        if self._state.phase is Phase.THROW_READY:
            return throttle_to_thrust(self._sticks.throttle, self._limits)
        return self._state.thrust

    def _throw_warning(self) -> str:
        waiting = self._state.phase is Phase.THROW_READY and self._mode is FlightMode.MANUAL
        if waiting and self._sticks.throttle < THROW_MIN_THROTTLE:
            return t("Газ ниже середины — поймать будет нечем. Подними газ до висения, потом бросай")
        return ""

    def view(self) -> View:
        manual = self._mode in (FlightMode.MANUAL, FlightMode.ACRO)
        airborne = self._state.phase in AIRBORNE
        joystick_name = self._joystick.get_name() if self._joystick is not None else ""
        return View(
            uri=self._link.uri,
            mode=self._mode,
            decks=self._decks.describe(),
            phase=self._state.phase,
            input_source=self._input_source,
            telemetry=self._link.telemetry,
            sticks=self._sticks,
            target_height=self._state.height if airborne and not manual else None,
            thrust=self._shown_thrust() if (manual or self._mode is FlightMode.ACRO) else None,
            trim=None if self._mode in (FlightMode.HOVER, FlightMode.ACRO) else (self._limits.trim_roll, self._limits.trim_pitch),
            battery_before_flight=self._battery_on_ground if airborne else None,
            message=self._message if self._message_left > 0 else "",
            error=self._link.error,
            warning=self._throw_warning() or supervisor_problem(self._link.telemetry.supervisor)
            or self._joystick_warning,
            monitor=self._show_monitor,
            joystick_name=joystick_name,
            controller_profile=bool(joystick_name) and self._config.joystick.profile(joystick_name) is not None,
            axes=self._axes,
            buttons=self._buttons,
            height_sensor=self._decks.supports(FlightMode.ALTHOLD),
        )

    # ---------- запуск и выход ----------

    def run(self) -> None:
        clock = pygame.time.Clock()
        while self.running:
            frame = clock.tick(LOOP_HZ) / 1000.0
            self.report_stall(frame)
            for event in poll_events():
                self.handle_event(event)
            self.tick(min(frame, MAX_DT))

    def shutdown(self, sleep: Callable[[float], None] = time.sleep) -> None:
        """Выход посреди полёта (Ctrl+C, ошибка): сажаем без окна, максимум SHUTDOWN_LAND_TIMEOUT."""
        dt = 1.0 / LOOP_HZ
        sticks = Sticks(throttle=-1.0) if self._mode is FlightMode.MANUAL else Sticks()
        for _ in range(round(SHUTDOWN_LAND_TIMEOUT / dt)):
            if self._state.phase not in ACTIVE_PHASES or self._link.error is not None:
                return
            self._state, output = step(self._state, dt, sticks, Command(land=True), self._mode, self._limits)
            self._link.execute(output)
            sleep(dt)
