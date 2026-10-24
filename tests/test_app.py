"""Сквозные тесты главного цикла: окно без экрана + симулятор вместо дрона."""

import itertools
import os
from dataclasses import replace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from cfpilot import app as app_module  # noqa: E402
from cfpilot import i18n  # noqa: E402
from cfpilot.app import LOST_REMOVAL, TAKEOFF_LOCK, TRIM_STEP, App, ExitReason  # noqa: E402
from cfpilot.config import (  # noqa: E402
    MAX_TRIM,
    AppConfig,
    AxisMap,
    Binding,
    Bindings,
    ControllerProfile,
    InvertMap,
    JoystickSettings,
)
from cfpilot.flight import FlightMode, Phase  # noqa: E402
from cfpilot.sim import SIM_THROW_AFTER, SimLink  # noqa: E402

DT = 0.02


class FakeJoystick:
    def __init__(self, axes, buttons=(), name="RadioMaster TX16S Joystick", instance_id=7):
        self.axes = list(axes)
        self.buttons = list(buttons)
        self._name = name
        self._instance_id = instance_id

    def get_name(self):
        return self._name

    def get_instance_id(self):
        return self._instance_id

    def get_numaxes(self):
        return len(self.axes)

    def get_axis(self, index):
        return self.axes[index]

    def get_numbuttons(self):
        return len(self.buttons)

    def get_button(self, index):
        return self.buttons[index]


class StubLink(SimLink):
    """Симулятор, которому можно подсунуть failsafe, разряженный аккумулятор и т.п."""

    def __init__(self, mode, clock, battery=None, position_ready=True, supervisor=None):
        super().__init__(mode, clock=clock)
        self.failsafe = False
        self.ready = position_ready
        if battery is not None:
            self._telemetry = replace(self._telemetry, battery=battery)
        if supervisor is not None:
            self._telemetry = replace(self._telemetry, supervisor=supervisor)

    @property
    def failsafe_stopped(self):
        return self.failsafe

    @property
    def position_ready(self):
        return self.ready

    lost = None  # текст ошибки связи — как у CrazyflieLink.error

    @property
    def error(self):
        return self.lost


@pytest.fixture
def make_app(monkeypatch):
    pygame.init()
    monkeypatch.setattr(pygame.joystick, "get_count", lambda: 0)  # настоящий пульт у ноутбука не должен мешать
    screen = pygame.display.set_mode((960, 620))

    def factory(mode=FlightMode.HOVER, config=AppConfig(), input_choice="auto", joysticks=(), saved=None, settle=True,
                **link_options):
        ticks = itertools.count()
        link = StubLink(mode, lambda: next(ticks) * DT, **link_options)  # время сима = время тиков

        def save_profile(device, profile):
            if saved is None:
                return "нет доступа к config.json"
            saved.append((device, profile))
            return ""

        app = App(config, link, mode, link.connect(), input_choice, screen, save_profile=save_profile,
                  joysticks=lambda: list(joysticks))
        app.link = link
        if settle:
            run(app, TAKEOFF_LOCK)  # блокировка взлёта в начале сеанса — большинству тестов не нужна
        return app

    yield factory
    pygame.quit()


def key(app, scancode, down=True):
    event_type = pygame.KEYDOWN if down else pygame.KEYUP
    app.handle_event(pygame.event.Event(event_type, scancode=scancode, key=0, mod=0))


def press(app, scancode):
    key(app, scancode, down=True)
    key(app, scancode, down=False)


def run(app, seconds):
    for _ in range(round(seconds / DT)):
        app.tick(DT)


def fly(app):
    press(app, pygame.KSCAN_T)
    run(app, 3.0)
    assert app.phase is Phase.FLYING


def test_hover_takeoff_and_land(make_app):
    app = make_app()
    fly(app)
    assert app.telemetry.height > 0.3
    press(app, pygame.KSCAN_L)
    run(app, 3.0)
    assert app.phase is Phase.IDLE
    assert app.telemetry.height == 0.0


def test_arrow_key_flies_forward(make_app):
    app = make_app()
    fly(app)
    key(app, pygame.KSCAN_UP)
    app.tick(DT)
    assert app.telemetry.pitch > 0
    key(app, pygame.KSCAN_UP, down=False)
    app.tick(DT)
    assert app.telemetry.pitch == 0


def test_space_kills_motors(make_app):
    app = make_app()
    fly(app)
    press(app, pygame.KSCAN_SPACE)
    app.tick(DT)
    assert app.phase is Phase.KILLED
    assert app.telemetry.height == 0.0


def test_escape_on_ground_quits_at_once(make_app):
    app = make_app()
    press(app, pygame.KSCAN_ESCAPE)
    assert app.running is False


def test_escape_in_flight_lands_before_quitting(make_app):
    app = make_app()
    fly(app)
    press(app, pygame.KSCAN_ESCAPE)
    app.tick(DT)
    assert app.running is True
    assert app.phase is Phase.LANDING
    run(app, 3.0)
    assert app.running is False
    assert app.phase is Phase.IDLE


def test_window_close_in_flight_also_lands(make_app):
    app = make_app()
    fly(app)
    app.handle_event(pygame.event.Event(pygame.QUIT))
    run(app, 3.0)
    assert app.running is False


def test_focus_loss_releases_held_keys(make_app):
    app = make_app()
    fly(app)
    key(app, pygame.KSCAN_UP)
    app.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    app.tick(DT)
    assert app.telemetry.pitch == 0


def test_manual_keyboard_throttle_is_held(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    press(app, pygame.KSCAN_T)
    run(app, 1.2)
    assert app.phase is Phase.FLYING
    key(app, pygame.KSCAN_W)
    run(app, 1.0)
    key(app, pygame.KSCAN_W, down=False)
    run(app, 0.5)
    assert app.thrust > 0


def test_manual_throw_is_caught_with_pilots_throttle(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    press(app, pygame.KSCAN_B)
    run(app, DT)
    assert app.phase is Phase.THROW_READY
    key(app, pygame.KSCAN_W)  # газ до висения, пока дрон в руке: моторы стоят
    run(app, 1.8)  # с клавиатуры газ растёт плавно — до середины чуть меньше 2 с
    key(app, pygame.KSCAN_W, down=False)
    assert app.thrust == 0
    assert app.view().thrust > 0  # на экране — тяга, с которой его поймают
    run(app, SIM_THROW_AFTER)  # симулятор «подбрасывает» дрон сам
    assert app.phase is Phase.FLYING
    assert app.thrust > 0


def test_throw_with_throttle_down_warns(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    press(app, pygame.KSCAN_B)
    run(app, DT)
    assert "Газ" in app.view().warning


def test_throw_refused_while_accelerometer_is_silent(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    app.link._telemetry = replace(app.link._telemetry, thrown=None)  # частый лог не пришёл
    press(app, pygame.KSCAN_B)
    run(app, DT)
    assert app.phase is Phase.IDLE
    assert "датчика ускорения" in app.view().message


def test_second_b_cancels_throw(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    press(app, pygame.KSCAN_B)
    run(app, DT)
    press(app, pygame.KSCAN_B)
    run(app, DT)
    assert app.phase is Phase.IDLE


def test_joystick_throw_button(make_app):
    pad = FakeJoystick([0.0] * 4, buttons=[False] * 4)
    profile = ControllerProfile(bindings=Bindings(kill=Binding(button=0), throw=Binding(button=3)))
    config = AppConfig(joystick=JoystickSettings().with_profile(pad.get_name(), profile))
    app = make_app(config=config, joysticks=[pad])
    pad.buttons[3] = True
    run(app, DT)
    assert app.phase is Phase.THROW_READY


def test_shift_arrow_trims_instead_of_tilting(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    press(app, pygame.KSCAN_T)
    run(app, 1.2)
    key(app, pygame.KSCAN_LSHIFT)
    key(app, pygame.KSCAN_RIGHT)  # держим, но с Shift это только трим
    press(app, pygame.KSCAN_UP)
    app.tick(DT)
    assert (app.limits.trim_roll, app.limits.trim_pitch) == (TRIM_STEP, TRIM_STEP)
    assert app.telemetry.roll == pytest.approx(TRIM_STEP)
    assert app.telemetry.pitch == pytest.approx(TRIM_STEP)


def test_trim_is_clamped_and_shown(make_app):
    app = make_app(mode=FlightMode.ALTHOLD)
    key(app, pygame.KSCAN_RSHIFT)
    for _ in range(round(2 * MAX_TRIM / TRIM_STEP)):
        press(app, pygame.KSCAN_DOWN)
    assert app.limits.trim_pitch == -MAX_TRIM
    assert app.view().trim == (0.0, -MAX_TRIM)


def test_trim_is_not_used_in_hover(make_app):
    app = make_app()
    key(app, pygame.KSCAN_LSHIFT)
    press(app, pygame.KSCAN_LEFT)
    app.tick(DT)
    assert app.limits.trim_roll == 0.0
    assert app.view().trim is None
    assert "hover" in app.view().message


def test_shutdown_lands_drone(make_app):
    app = make_app()
    fly(app)
    app.shutdown(sleep=lambda _: None)
    assert app.phase is Phase.IDLE


def test_joystick_drives_sticks(make_app):
    app = make_app()
    app.attach_joystick(FakeJoystick([0.0, 0.6, 0.0, 0.0]))
    fly(app)
    assert app.telemetry.pitch > 0
    assert "RadioMaster" in app.view().input_source


def test_joystick_ignored_when_keyboard_is_forced(make_app):
    app = make_app(input_choice="keyboard")
    app.attach_joystick(FakeJoystick([0.0, 0.6, 0.0, 0.0]))
    fly(app)
    assert app.telemetry.pitch == 0


def test_joystick_kill_switch(make_app):
    config = AppConfig(joystick=JoystickSettings(bindings=Bindings(kill=Binding(axis=4, above=0.5))))
    app = make_app(config=config)
    joystick = FakeJoystick([0.0, 0.0, 0.0, 0.0, -1.0])
    app.attach_joystick(joystick)
    fly(app)
    joystick.axes[4] = 1.0
    app.tick(DT)
    assert app.phase is Phase.KILLED


def test_joystick_takeoff_switch_triggers_on_flip(make_app):
    config = AppConfig(joystick=JoystickSettings(bindings=Bindings(takeoff=Binding(button=0))))
    app = make_app(config=config)
    joystick = FakeJoystick([0.0, 0.0, 0.0, 0.0], buttons=[False])
    app.attach_joystick(joystick)
    app.tick(DT)
    joystick.buttons[0] = True
    run(app, 3.0)
    assert app.phase is Phase.FLYING


def test_switch_already_on_when_plugged_in_does_not_take_off(make_app):
    config = AppConfig(joystick=JoystickSettings(bindings=Bindings(takeoff=Binding(button=0))))
    app = make_app(config=config)
    app.attach_joystick(FakeJoystick([0.0, 0.0, 0.0, 0.0], buttons=[True]))
    run(app, 1.0)
    assert app.phase is Phase.IDLE


def test_joystick_land_switch(make_app):
    config = AppConfig(joystick=JoystickSettings(bindings=Bindings(land=Binding(axis=5, below=-0.5))))
    app = make_app(config=config)
    joystick = FakeJoystick([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    app.attach_joystick(joystick)
    fly(app)
    joystick.axes[5] = -1.0
    app.tick(DT)
    assert app.phase is Phase.LANDING


def test_forced_joystick_without_device_keeps_hover(make_app):
    app = make_app(input_choice="joystick")
    app.tick(DT)
    assert "НЕ ПОДКЛЮЧЁН" in app.view().input_source


def test_joystick_unplugged_in_flight_lands(make_app):
    app = make_app()
    app.attach_joystick(FakeJoystick([0.0, 0.0, 0.0, 0.0], instance_id=7))
    fly(app)
    app.handle_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=7))
    app.tick(DT)
    assert app.phase is Phase.LANDING
    assert "Пульт" in app.view().message


def test_focus_loss_with_keyboard_lands(make_app):
    app = make_app()
    fly(app)
    app.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    app.tick(DT)
    assert app.phase is Phase.LANDING
    assert "фокус" in app.view().message


def test_focus_loss_with_joystick_keeps_flying(make_app):
    app = make_app()
    app.attach_joystick(FakeJoystick([0.0, 0.0, 0.0, 0.0]))
    fly(app)
    app.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    app.tick(DT)
    assert app.phase is Phase.FLYING


def test_stall_makes_drone_land(make_app):
    app = make_app()
    fly(app)
    app.report_stall(0.2)
    app.tick(DT)
    assert app.phase is Phase.FLYING
    app.report_stall(0.8)
    app.tick(DT)
    assert app.phase is Phase.LANDING


def test_failsafe_landing_is_picked_up(make_app):
    app = make_app()
    fly(app)
    app.link.failsafe = True
    app.tick(DT)
    assert app.phase is Phase.KILLED
    assert "сел сам" in app.view().message


def test_low_battery_does_not_block_takeoff(make_app):
    app = make_app(battery=3.3)
    fly(app)


def test_battery_before_takeoff_is_kept_for_flight(make_app):
    app = make_app(battery=4.0)
    assert app.view().battery_before_flight is None  # на земле показываем живой процент
    fly(app)
    assert app.view().battery_before_flight == pytest.approx(4.0, abs=0.01)


PAD = AppConfig(joystick=JoystickSettings(bindings=Bindings(
    takeoff=Binding(button=0), hold=Binding(button=1), yaw_left=Binding(button=9), yaw_right=Binding(button=10),
)))
HOLD, YAW_LEFT, YAW_RIGHT = 1, 9, 10


def tap(app, pad, button):
    pad.buttons[button] = True
    app.tick(DT)
    pad.buttons[button] = False
    app.tick(DT)


def fly_manual_with_pad(make_app, throttle=0.5):
    """manual + геймпад (раскладка по умолчанию: газ — ось 2)."""
    app = make_app(mode=FlightMode.MANUAL, config=PAD)
    pad = FakeJoystick([0.0, 0.0, -1.0, 0.0], buttons=[False] * 11)
    app.attach_joystick(pad)
    tap(app, pad, 0)
    run(app, 1.2)
    pad.axes[2] = throttle
    run(app, 0.2)
    assert app.phase is Phase.FLYING
    return app, pad


def test_hold_keeps_throttle_when_stick_is_released(make_app):
    app, pad = fly_manual_with_pad(make_app)
    held = app.thrust
    tap(app, pad, HOLD)
    pad.axes[2] = 0.0  # пружина вернула стик в центр
    run(app, 1.0)
    assert app.thrust == held
    assert "удерж" in app.view().input_source


def test_stick_nudges_held_throttle_only_after_returning_to_center(make_app):
    app, pad = fly_manual_with_pad(make_app)
    tap(app, pad, HOLD)
    held = app.thrust
    pad.axes[2] = 1.0  # стик так и не отпустили после включения — это не подправка
    run(app, 0.5)
    assert app.thrust == held
    pad.axes[2] = 0.0
    app.tick(DT)
    pad.axes[2] = 1.0
    run(app, 0.5)
    assert app.thrust > held


def test_releasing_hold_waits_for_stick_to_catch_up(make_app):
    app, pad = fly_manual_with_pad(make_app)
    tap(app, pad, HOLD)
    pad.axes[2] = 0.0
    run(app, 0.2)
    held = app.thrust
    tap(app, pad, HOLD)  # удержание выключили, а стик в центре — газ не должен провалиться
    run(app, 0.5)
    assert app.thrust == held
    pad.axes[2] = 0.5  # подвели стик к уровню удержания — управление снова у стика
    run(app, 0.1)
    pad.axes[2] = 0.2
    run(app, 0.1)
    assert app.thrust < held


def test_hold_is_off_after_landing(make_app):
    app, pad = fly_manual_with_pad(make_app)
    tap(app, pad, HOLD)
    press(app, pygame.KSCAN_L)
    run(app, 5.0)
    assert app.phase is Phase.IDLE
    assert "удерж" not in app.view().input_source


def test_hold_in_hover_explains_it_is_not_needed(make_app):
    app = make_app(config=PAD)
    pad = FakeJoystick([0.0, 0.0, 0.0, 0.0], buttons=[False] * 11)
    app.attach_joystick(pad)
    fly(app)
    tap(app, pad, HOLD)
    assert "держит" in app.view().message


def test_shoulder_buttons_turn_in_place(make_app):
    app, pad = fly_manual_with_pad(make_app)
    pad.buttons[YAW_RIGHT] = True
    app.tick(DT)
    assert app.view().sticks.yaw == 1.0
    pad.buttons[YAW_RIGHT] = False
    pad.buttons[YAW_LEFT] = True
    app.tick(DT)
    assert app.view().sticks.yaw == -1.0


@pytest.mark.parametrize("supervisor, hint", [(0x40, "нажми R"), (0x20, "ровно")])
def test_drone_blocked_by_firmware_is_explained_and_not_armed(make_app, supervisor, hint):
    app = make_app(supervisor=supervisor)
    app.tick(DT)
    assert hint in app.view().warning  # видно сразу, ещё до попытки взлёта
    press(app, pygame.KSCAN_T)
    app.tick(DT)
    assert app.phase is Phase.IDLE
    assert hint in app.view().message


def test_takeoff_waits_for_position_estimate(make_app):
    app = make_app(position_ready=False)
    press(app, pygame.KSCAN_T)
    run(app, 2.0)
    assert app.phase is Phase.ARMING
    app.link.ready = True
    run(app, 3.0)
    assert app.phase is Phase.FLYING


def test_quit_and_takeoff_in_one_frame_does_not_arm(make_app):
    app = make_app()
    press(app, pygame.KSCAN_ESCAPE)
    press(app, pygame.KSCAN_T)
    app.tick(DT)
    assert app.running is False
    assert app.phase is Phase.IDLE


def test_joystick_without_kill_switch_warns(make_app):
    app = make_app()
    app.attach_joystick(FakeJoystick([0.0, 0.0, 0.0, 0.0]))
    assert "аварийный стоп" in app.view().warning


def test_joystick_binding_to_missing_axis_warns(make_app):
    config = AppConfig(joystick=JoystickSettings(bindings=Bindings(kill=Binding(axis=7, above=0.5))))
    app = make_app(config=config)
    app.attach_joystick(FakeJoystick([0.0] * 5))
    assert "нет оси 7" in app.view().warning


def test_joystick_throttle_up_blocks_hover_takeoff(make_app):
    app = make_app()
    app.attach_joystick(FakeJoystick([0.0, 0.0, 0.9, 0.0]))
    press(app, pygame.KSCAN_T)
    app.tick(DT)
    assert app.phase is Phase.IDLE
    assert "центр" in app.view().message


def test_monitor_toggle_renders(make_app):
    app = make_app()
    app.attach_joystick(FakeJoystick([0.1, -0.2, 0.3, 0.4], buttons=[False, True]))
    press(app, pygame.KSCAN_J)
    app.tick(DT)
    assert app.view().monitor is True
    assert app.view().buttons == (False, True)


# ---------- переподключение и перезагрузка ----------

def test_lost_link_ends_session_so_main_can_reconnect(make_app):
    app = make_app()
    fly(app)
    app.link.lost = "Связь с дроном потеряна: Too many packets lost"
    app.tick(DT)
    assert app.running is False
    assert app.exit_reason is ExitReason.LINK_LOST


def test_r_on_ground_asks_main_loop_to_reboot_drone(make_app):
    app = make_app()
    press(app, pygame.KSCAN_R)
    assert app.running is False
    assert app.exit_reason is ExitReason.REBOOT


def test_r_in_flight_is_refused(make_app):
    app = make_app()
    fly(app)
    press(app, pygame.KSCAN_R)
    app.tick(DT)
    assert app.running is True
    assert "посади" in app.view().message


def test_normal_quit_is_reported_as_quit(make_app):
    app = make_app()
    press(app, pygame.KSCAN_ESCAPE)
    assert app.exit_reason is ExitReason.QUIT


def test_session_config_keeps_trim(make_app):
    app = make_app(mode=FlightMode.MANUAL)
    key(app, pygame.KSCAN_LSHIFT)
    press(app, pygame.KSCAN_RIGHT)
    assert app.config.flight.trim_roll == TRIM_STEP


# ---------- калибровка пульта ----------

DUALSENSE = "DualSense Wireless Controller"


def fresh_pad(name=DUALSENSE, instance_id=7):
    return FakeJoystick([0.0, 0.0, 0.0, 0.0, -1.0, -1.0], buttons=[False] * 16, name=name, instance_id=instance_id)


def hold_axis(app, pad, axis, value, seconds=0.5):
    pad.axes[axis] = value
    run(app, seconds)
    pad.axes[axis] = 0.0
    app.tick(DT)


def calibrate(app, pad):
    """Проходит мастер как человек: покой, ход стиков, газ/вперёд/вправо, кнопки 0/2/3 — взлёт/посадка/стоп."""
    press(app, pygame.KSCAN_C)
    run(app, 1.5)
    for axis in range(4):
        for value in (-1.0, 1.0, 0.0):
            pad.axes[axis] = value
            app.tick(DT)
    tap(app, pad, 15)
    hold_axis(app, pad, 1, -1.0)
    hold_axis(app, pad, 3, -1.0)
    hold_axis(app, pad, 2, 1.0)
    press(app, pygame.KSCAN_BACKSPACE)  # поворот — без стика
    for button in (0, 2, 3):
        tap(app, pad, button)
    for _ in range(10):  # удержание, поворот влево/вправо, бросок, 4 сальто, 2 рывка — без кнопок
        press(app, pygame.KSCAN_BACKSPACE)
    press(app, pygame.KSCAN_RETURN)
    app.tick(DT)


def test_calibration_saves_profile_and_controller_flies_with_it(make_app):
    pad, saved = fresh_pad(), []
    app = make_app(joysticks=[pad], saved=saved)
    app.tick(DT)
    assert "нажми C" in app.view().message  # новый пульт — предлагаем настроить
    calibrate(app, pad)
    assert not app.calibrating
    assert app.phase is Phase.IDLE  # кнопки, нажатые в мастере, не подняли дрон
    assert saved[0][0] == DUALSENSE
    assert app.config.joystick.profile(DUALSENSE) == saved[0][1]
    assert app.view().controller_profile is True
    assert "аварийный стоп" not in app.view().warning
    run(app, TAKEOFF_LOCK)
    tap(app, pad, 0)  # кнопка, назначенная на взлёт
    assert app.phase is Phase.ARMING


def test_calibration_save_failure_is_reported(make_app):
    pad = fresh_pad()
    app = make_app(joysticks=[pad], saved=None)
    app.tick(DT)
    calibrate(app, pad)
    assert "не сохранён" in app.view().message
    assert app.view().controller_profile is True  # в этом сеансе раскладка всё равно работает


def test_calibration_only_on_ground(make_app):
    app = make_app()
    fly(app)
    press(app, pygame.KSCAN_C)
    assert not app.calibrating
    app.tick(DT)
    assert "на земле" in app.view().message


def test_escape_cancels_calibration_without_quitting(make_app):
    pad = fresh_pad()
    app = make_app(joysticks=[pad])
    app.tick(DT)
    press(app, pygame.KSCAN_C)
    app.tick(DT)
    press(app, pygame.KSCAN_ESCAPE)
    app.tick(DT)
    assert app.running is True
    assert not app.calibrating
    assert "отменена" in app.view().message


def test_space_still_kills_during_calibration(make_app):
    app = make_app(joysticks=[fresh_pad()])
    press(app, pygame.KSCAN_C)
    press(app, pygame.KSCAN_SPACE)
    app.tick(DT)
    assert app.phase is Phase.KILLED
    assert app.calibrating


def test_controller_with_profile_is_preferred_and_uses_its_layout(make_app):
    profile = ControllerProfile(bindings=Bindings(kill=Binding(button=3)))
    config = replace(AppConfig(), joystick=JoystickSettings().with_profile("Pad B", profile))
    first, second = fresh_pad("Pad A", 1), fresh_pad("Pad B", 2)
    app = make_app(config=config, joysticks=[first, second])
    app.tick(DT)
    assert app.view().joystick_name == "Pad B"
    assert app.view().controller_profile is True
    second.buttons[3] = True
    app.tick(DT)
    assert app.phase is Phase.KILLED  # кнопка стопа из профиля


def test_unplugging_controller_during_calibration_restarts_choice(make_app):
    pad = fresh_pad()
    plugged = [pad]
    app = make_app(joysticks=plugged)
    app.tick(DT)
    press(app, pygame.KSCAN_C)
    run(app, 0.1)
    plugged.clear()
    app.handle_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=pad.get_instance_id()))
    app.tick(DT)
    assert app.calibrating
    assert "Подключи" in app._calibrator.view().title


def test_poll_events_survives_pygame_joystick_removal_bug(monkeypatch):
    # pygame 2.6 бросает KeyError внутри event.get(), если отключилось неоткрытое устройство.
    key_press = pygame.event.Event(pygame.KEYDOWN, scancode=pygame.KSCAN_SPACE, key=0, mod=0)

    def get(eventtype=None, exclude=None):
        if eventtype == pygame.JOYDEVICEREMOVED:
            raise SystemError("<built-in function get> returned a result with an exception set")
        return [key_press]

    monkeypatch.setattr(pygame.event, "get", get)
    from cfpilot.app import poll_events

    lost, key_event = poll_events()
    assert key_event == key_press  # Пробел не потерялся
    assert lost.instance_id == LOST_REMOVAL  # а приложение проверит, на месте ли его пульт


# ---------- защита по итогам ревью ----------

def test_takeoff_is_blocked_for_a_second_after_session_start(make_app):
    app = make_app(settle=False)
    press(app, pygame.KSCAN_T)
    app.tick(DT)
    assert app.phase is Phase.IDLE
    assert "Секунду" in app.view().message
    run(app, TAKEOFF_LOCK)
    press(app, pygame.KSCAN_T)
    app.tick(DT)
    assert app.phase is Phase.ARMING


def test_double_enter_on_last_calibration_step_does_not_take_off(make_app):
    pad = fresh_pad()
    app = make_app(joysticks=[pad])
    app.tick(DT)
    calibrate(app, pad)  # последний шаг подтверждён Enter
    press(app, pygame.KSCAN_RETURN)  # «лишний» Enter — это ещё и клавиша взлёта
    app.tick(DT)
    assert app.phase is Phase.IDLE


def test_lost_removal_event_drops_controller_that_is_gone(make_app):
    pad = fresh_pad()
    plugged = [pad]
    app = make_app(joysticks=plugged)
    assert app.view().joystick_name
    plugged.clear()
    app.handle_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=LOST_REMOVAL))
    app.tick(DT)
    assert app.view().joystick_name == ""


def test_lost_removal_event_keeps_controller_that_is_still_there(make_app):
    pad = fresh_pad()
    app = make_app(joysticks=[pad])
    app.handle_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=LOST_REMOVAL))
    app.tick(DT)
    assert app.view().joystick_name == DUALSENSE


def test_controller_that_stops_answering_lands_drone(make_app):
    pad = fresh_pad()
    app = make_app(joysticks=[pad])
    fly(app)

    def dead(index):
        raise pygame.error("Joystick not initialized")

    pad.get_axis = dead
    app.tick(DT)
    assert app.view().joystick_name == ""
    assert app.phase is Phase.LANDING


def key_event(scancode):
    return pygame.event.Event(pygame.KEYDOWN, scancode=scancode, key=0, mod=0)


def test_f2_switches_language_and_f11_full_screen(monkeypatch):
    toggled = []
    monkeypatch.setattr(app_module, "toggle_fullscreen", lambda: toggled.append(True))
    try:
        assert app_module.window_key(key_event(pygame.KSCAN_F2))
        assert i18n.language() == "en"
        assert app_module.window_key(key_event(pygame.KSCAN_F2))
        assert i18n.language() == "ru"
        assert app_module.window_key(key_event(pygame.KSCAN_F11))
        assert toggled == [True]
        assert not app_module.window_key(key_event(pygame.KSCAN_T))  # остальные клавиши идут дальше
    finally:
        i18n.set_language("ru")


def test_f_key_toggles_fpv_mode(make_app):
    app = make_app()
    assert app.view().mode == FlightMode.HOVER

    press(app, pygame.KSCAN_F)
    assert app.view().mode == FlightMode.ACRO
    assert "FPV" in app.view().message

    press(app, pygame.KSCAN_F)
    assert app.view().mode == FlightMode.HOVER
    assert "стабилизации" in app.view().message


def test_f_key_repeat_does_not_toggle_back(make_app):
    app = make_app()
    key(app, pygame.KSCAN_F)
    key(app, pygame.KSCAN_F)
    assert app.view().mode is FlightMode.ACRO
    key(app, pygame.KSCAN_F, down=False)
    press(app, pygame.KSCAN_F)
    assert app.view().mode is FlightMode.HOVER


def test_f_key_does_not_change_mode_in_flight(make_app):
    app = make_app()
    fly(app)
    press(app, pygame.KSCAN_F)
    assert app.view().mode is FlightMode.HOVER
    assert "на земле" in app.view().message


def test_acro_startup_uses_stabilized_mode(make_app):
    app = make_app(mode=FlightMode.ACRO)
    assert app.view().mode is FlightMode.MANUAL
    press(app, pygame.KSCAN_F)
    assert app.view().mode is FlightMode.ACRO
    press(app, pygame.KSCAN_F)
    assert app.view().mode is FlightMode.MANUAL


def test_acro_keyboard_throttle_starts_at_zero_and_kill_works(make_app):
    app = make_app()
    press(app, pygame.KSCAN_F)
    press(app, pygame.KSCAN_T)
    run(app, 1.2)
    assert app.phase is Phase.FLYING
    assert app.thrust == 0
    key(app, pygame.KSCAN_W)
    run(app, 0.5)
    key(app, pygame.KSCAN_W, down=False)
    assert app.thrust > 0
    press(app, pygame.KSCAN_F)
    assert app.view().mode is FlightMode.ACRO
    press(app, pygame.KSCAN_SPACE)
    app.tick(DT)
    assert app.phase is Phase.KILLED


def flip_pad_config():
    return AppConfig(joystick=JoystickSettings(
        axes=AxisMap(roll=2, pitch=3, throttle=1, yaw=None), invert=InvertMap(pitch=True, throttle=True),
        bindings=Bindings(takeoff=Binding(button=0), kill=Binding(button=3), flip_forward=Binding(button=11),
                          flip_right=Binding(button=14), dash_forward=Binding(axis=5, above=0.5))))


def fly_manual_with_flip_pad(make_app):
    pad = FakeJoystick([0.0, 1.0, 0.0, 0.0, -1.0, -1.0], buttons=[False] * 16, name=DUALSENSE)  # газ внизу
    app = make_app(mode=FlightMode.MANUAL, config=flip_pad_config(), joysticks=[pad])
    tap(app, pad, 0)
    run(app, 1.5)
    pad.axes[1] = -0.2  # газ чуть выше середины
    run(app, 0.5)
    assert app.phase is Phase.FLYING
    return app, pad


def test_dpad_arrow_does_a_flip_and_returns_to_flight(make_app):
    app, pad = fly_manual_with_flip_pad(make_app)
    pad.buttons[11] = True
    app.tick(DT)
    assert app.phase is Phase.FLIPPING
    assert "Сальто вперёд" in app.view().message
    pad.buttons[11] = False
    run(app, 2.0)
    assert app.phase is Phase.FLYING


def test_numpad_does_a_roll_flip(make_app):
    app, _ = fly_manual_with_flip_pad(make_app)
    press(app, pygame.KSCAN_KP_6)
    app.tick(DT)
    assert app.phase is Phase.FLIPPING
    assert "Бочка вправо" in app.view().message


def test_r2_held_tilts_drone_45_degrees(make_app):
    app, pad = fly_manual_with_flip_pad(make_app)
    pad.axes[5] = 1.0
    run(app, 0.3)
    assert app.telemetry.pitch == pytest.approx(app.limits.dash_angle)
    pad.axes[5] = -1.0
    app.tick(DT)
    assert app.telemetry.pitch == pytest.approx(app.limits.trim_pitch)
