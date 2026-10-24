"""Экран подключения: подставные «подключатель», экран и пульт."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from cfpilot.channels import OnAir, RadioUri  # noqa: E402
from cfpilot.config import AppConfig, ControllerProfile, JoystickSettings  # noqa: E402
from cfpilot.connection import Stage, Status  # noqa: E402
from cfpilot.link import DeckInfo  # noqa: E402
from cfpilot.lobby import Lobby  # noqa: E402

DT = 0.02


class FakeConnector:
    can_reboot = True

    def __init__(self, stage=Stage.SEARCHING):
        self.status = Status(stage, 1)
        self.result = None
        self.reboots = 0
        self.chosen = []
        self.cancelled = False

    def choose(self, uri):
        self.chosen.append(uri)

    @property
    def connected(self):
        return self.result is not None

    def take(self):
        result, self.result = self.result, None
        return result

    def request_reboot(self):
        self.reboots += 1

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True


class FakeDashboard:
    def __init__(self):
        self.screens = []

    def draw_connect(self, view):
        self.screens.append(("connect", view))

    def draw_calibration(self, view):
        self.screens.append(("calibration", view))


class FakeDriver:
    def __init__(self):
        self.attempts = 0

    def start(self):
        self.attempts += 1

    def describe(self):
        return "ставлю" if self.attempts else ""


class Pad:
    def __init__(self, name="DualSense Wireless Controller"):
        self.name = name

    def get_name(self):
        return self.name

    def get_instance_id(self):
        return 1

    def get_numaxes(self):
        return 4

    def get_axis(self, index):
        return 0.0

    def get_numbuttons(self):
        return 4

    def get_button(self, index):
        return False


def key(lobby, scancode):
    lobby.handle_event(pygame.event.Event(pygame.KEYDOWN, scancode=scancode, key=0, mod=0))


@pytest.fixture
def make_lobby():
    def factory(stage=Stage.SEARCHING, config=AppConfig(), joysticks=(), saved=None):
        connector, dashboard, driver = FakeConnector(stage), FakeDashboard(), FakeDriver()

        def save(device, profile):
            saved.append((device, profile))
            return ""

        lobby = Lobby(dashboard, config, connector, driver=driver, save_profile=save if saved is not None else None,
                      joysticks=lambda: list(joysticks), reason="Связь потеряна", clock=lambda: 0.0)
        return lobby, connector, dashboard, driver

    return factory


def test_hands_over_drone_once_connected(make_lobby):
    lobby, connector, dashboard, _ = make_lobby()
    assert lobby.tick(DT) is None
    assert dashboard.screens[-1][1].reason == "Связь потеряна"
    connector.result = ("link", DeckInfo(flow=True))
    assert lobby.tick(DT) == ("link", DeckInfo(flow=True))


def test_missing_driver_is_installed_once_by_itself(make_lobby):
    lobby, _, dashboard, driver = make_lobby(Stage.NO_DRIVER)
    lobby.tick(DT)
    lobby.tick(DT)
    assert driver.attempts == 1  # Windows спросит права один раз, а не каждый кадр
    assert dashboard.screens[-1][1].driver == "ставлю"
    key(lobby, pygame.KSCAN_D)
    assert driver.attempts == 2


def test_digit_picks_drone_from_list(make_lobby):
    lobby, connector, _, _ = make_lobby()
    drones = (OnAir(RadioUri(0, 20), 1.0), OnAir(RadioUri(0, 100), 0.6))
    connector.status = Status(Stage.CHOOSING, 2, drones=drones)
    key(lobby, pygame.KSCAN_3)  # третьего дрона нет — ничего не происходит
    key(lobby, pygame.KSCAN_KP_2)
    assert connector.chosen == ["radio://0/100/2M/E7E7E7E7E7"]


def test_digits_do_nothing_while_searching(make_lobby):
    lobby, connector, _, _ = make_lobby()
    key(lobby, pygame.KSCAN_1)
    assert connector.chosen == []


def test_screen_shows_address_being_tried(make_lobby):
    lobby, connector, dashboard, _ = make_lobby()
    lobby.tick(DT)
    assert dashboard.screens[-1][1].uri == AppConfig().uri  # пока не нашли — адрес из настроек
    connector.status = Status(Stage.SEARCHING, 2, uri="radio://0/85/2M/E7E7E7E7E7")
    lobby.tick(DT)
    assert dashboard.screens[-1][1].uri == "radio://0/85/2M/E7E7E7E7E7"


def test_r_asks_to_reboot_drone(make_lobby):
    lobby, connector, _, _ = make_lobby()
    key(lobby, pygame.KSCAN_R)
    assert connector.reboots == 1


@pytest.mark.parametrize("event", [pygame.event.Event(pygame.QUIT),
                                   pygame.event.Event(pygame.KEYDOWN, scancode=pygame.KSCAN_ESCAPE, key=0, mod=0)])
def test_window_close_or_escape_quits(make_lobby, event):
    lobby, *_ = make_lobby()
    lobby.handle_event(event)
    assert lobby.quit


def test_connected_drone_waits_for_calibration_to_finish(make_lobby):
    pad, saved = Pad(), []
    lobby, connector, dashboard, _ = make_lobby(joysticks=[pad], saved=saved)
    key(lobby, pygame.KSCAN_C)
    connector.result = ("link", DeckInfo())
    assert lobby.tick(DT) is None
    assert dashboard.screens[-1][0] == "calibration"
    key(lobby, pygame.KSCAN_ESCAPE)  # Esc в мастере — отмена калибровки, а не выход
    assert not lobby.quit
    assert lobby.tick(DT) == ("link", DeckInfo())
    assert dashboard.screens[-1][1].message == "Калибровка отменена"


def test_finished_calibration_updates_and_saves_config(make_lobby, monkeypatch):
    pad, saved = Pad(), []
    lobby, _, dashboard, _ = make_lobby(joysticks=[pad], saved=saved)
    key(lobby, pygame.KSCAN_C)
    profile = ControllerProfile()
    calibrator = lobby._calibrator
    calibrator.joystick = pad
    monkeypatch.setattr(calibrator, "update", lambda dt: setattr(calibrator, "finished", True))
    monkeypatch.setattr(calibrator, "profile", lambda: profile)
    lobby.tick(DT)
    assert saved == [(pad.name, profile)]
    assert lobby.config.joystick.profile(pad.name) == profile
    view = dashboard.screens[-1][1]
    assert "настроен и сохранён" in view.message
    assert view.controller == f"{pad.name} — свой профиль"


def test_controller_line_suggests_calibration_for_new_pad(make_lobby):
    lobby, _, dashboard, _ = make_lobby(joysticks=[Pad("Xbox Controller")])
    lobby.tick(DT)
    assert "C — настроить" in dashboard.screens[-1][1].controller


def test_known_pad_is_named_first(make_lobby):
    config = AppConfig(joystick=JoystickSettings().with_profile("Pad B", ControllerProfile()))
    lobby, _, dashboard, _ = make_lobby(config=config, joysticks=[Pad("Pad A"), Pad("Pad B")])
    lobby.tick(DT)
    assert dashboard.screens[-1][1].controller == "Pad B — свой профиль"


def test_closing_window_in_the_frame_drone_connects_leaves_link_for_cleanup(make_lobby):
    lobby, connector, _, _ = make_lobby()
    connector.result = ("link", DeckInfo())
    lobby.handle_event(pygame.event.Event(pygame.QUIT))
    assert lobby.tick(DT) is None
    assert connector.connected  # связь не забрана — её закроет cancel() при выходе из run()


def test_lost_removal_during_calibration_restarts_choice_if_pad_is_gone(make_lobby):
    pad = Pad()
    plugged = [pad]
    lobby, _, _, _ = make_lobby(joysticks=plugged)
    key(lobby, pygame.KSCAN_C)
    lobby.tick(DT)
    assert lobby._calibrator.joystick is pad
    plugged.clear()
    lobby.handle_event(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=-1))
    assert lobby._calibrator.joystick is None
