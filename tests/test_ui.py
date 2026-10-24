import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from cfpilot.calibration import Calibrator  # noqa: E402
from cfpilot.channels import OnAir, RadioUri  # noqa: E402
from cfpilot.connection import Stage, Status  # noqa: E402
from cfpilot.controls import Sticks  # noqa: E402
from cfpilot.flight import FlightMode, Phase  # noqa: E402
from cfpilot.link import Telemetry  # noqa: E402
from cfpilot.screens import ConnectView  # noqa: E402
from cfpilot.theme import DANGER, FONT_DIR, LOGO_SIZE, OK, icon_surface, logo_surface  # noqa: E402
from cfpilot.ui import WINDOW_SIZE, Dashboard, View, battery_color, battery_parts, battery_percent  # noqa: E402


@pytest.mark.parametrize(
    "volts, percent",
    [(4.25, 100), (4.20, 100), (4.10, 90), (4.00, 70), (3.92, 50), (3.78, 10), (3.00, 0), (2.80, 0),
     (float("nan"), 0)],
)
def test_battery_percent_follows_lipo_discharge_curve(volts, percent):
    assert battery_percent(volts) == percent


def test_battery_percent_interpolates_between_points():
    assert battery_percent(3.94) == 55


def test_on_ground_battery_is_shown_in_percent():
    assert battery_parts(4.0, before_flight=None) == ("70%", "4.00 В")
    assert battery_color(4.0, before_flight=None) == OK


def test_in_flight_sagging_voltage_is_not_shown_as_empty_battery():
    # 3.70 В под нагрузкой — норма для полёта, а по таблице «в покое» это было бы ~5%.
    assert battery_parts(3.70, before_flight=4.0) == ("3.70 В", "70% перед взлётом")
    assert battery_color(3.70, before_flight=4.0) == OK
    assert battery_color(3.20, before_flight=4.0) == DANGER


def test_unknown_battery():
    assert battery_parts(None, None) == ("—", "")


def test_brand_fonts_are_shipped():
    for name in ("Unbounded-SemiBold.ttf", "Manrope-Medium.ttf", "Manrope-Bold.ttf", "JetBrainsMono-Medium.ttf"):
        assert (FONT_DIR / name).exists()


@pytest.fixture
def dashboard():
    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    yield Dashboard(screen), screen
    pygame.quit()


def test_logo_keeps_its_proportions():
    logo = logo_surface(120)
    assert logo.get_height() == 120
    assert logo.get_width() == round(LOGO_SIZE[0] * 120 / LOGO_SIZE[1])
    assert icon_surface(64).get_size() == (64, 64)


@pytest.mark.parametrize("mode", list(FlightMode))
@pytest.mark.parametrize("monitor", [False, True])
def test_flight_screen_draws_in_every_mode(dashboard, mode, monitor):
    dash, screen = dashboard
    view = View(uri="radio://0/80/2M/E7E7E7E7E7", mode=mode, decks="Flow", phase=Phase.KILLED, input_source="пульт",
                telemetry=Telemetry(battery=3.9, height=0.4, link_quality=30.0), sticks=Sticks(roll=1.0, throttle=-1.0),
                target_height=0.5, thrust=40000 if mode is FlightMode.MANUAL else None,
                trim=None if mode is FlightMode.HOVER else (0.5, -0.5), message="сообщение " * 30,
                warning="предупреждение", monitor=monitor, joystick_name="RadioMaster TX16S Joystick",
                axes=(0.1,) * 10, buttons=(True,) * 40)
    dash.draw(view)
    assert screen.get_at((5, 5))[:3] != (0, 0, 0)  # фон CC Lab, а не пустой чёрный


def test_without_height_sensor_height_tile_is_replaced_by_link(dashboard):
    dash, _ = dashboard
    view = View(uri="radio://0/80/2M/E7E7E7E7E7", mode=FlightMode.MANUAL, decks="нет", phase=Phase.FLYING,
                input_source="клавиатура", telemetry=Telemetry(height=742.3, link_quality=88.0), sticks=Sticks(),
                target_height=None, thrust=30000, height_sensor=False)
    labels = [tile[0] for tile in dash._tiles(view)]
    assert "высота" not in labels
    assert "связь" in labels


@pytest.mark.parametrize("stage", list(Stage))
def test_connect_screen_draws_every_stage(dashboard, stage):
    dash, _ = dashboard
    dash.draw_connect(ConnectView(uri="radio://0/80/2M/E7E7E7E7E7", status=Status(stage, 3, "Дрон не отвечает"),
                                  reason="Связь потеряна", driver="Ставлю драйвер", message="Пульт настроен",
                                  controller="DualSense — свой профиль", elapsed=1.5))


def test_choice_screen_lists_every_drone(dashboard):
    dash, _ = dashboard
    drones = tuple(OnAir(RadioUri(0, channel), 1.0 if channel % 20 else 0.3) for channel in range(0, 120, 10))
    dash.draw_connect(ConnectView(uri="radio://0/80/2M/E7E7E7E7E7", status=Status(Stage.CHOOSING, 3, drones=drones),
                                  reason="Связь потеряна", controller="DualSense — свой профиль", elapsed=1.5))


def test_single_drone_on_other_channel_asks_for_confirmation(dashboard, monkeypatch):
    dash, _ = dashboard
    titles, draw_text = [], dash._painter.text
    monkeypatch.setattr(dash._painter, "text", lambda text, *args, **kwargs: titles.append(text) or draw_text(
        text, *args, **kwargs))
    drone = OnAir(RadioUri(0, 85), 1.0)
    dash.draw_connect(ConnectView(uri="radio://0/80/2M/E7E7E7E7E7", status=Status(Stage.CHOOSING, 1, drones=(drone,))))
    assert "Это твой дрон?" in titles
    assert drone.describe() in titles


def test_calibration_screen_draws_without_controller(dashboard):
    dash, _ = dashboard
    dash.draw_calibration(Calibrator(lambda: []).view())


def test_notice_screen_draws(dashboard):
    dash, _ = dashboard
    dash.draw_notice("Неподходящий режим", ("Режиму hover нужны деки", "", "Любая клавиша — выход"), DANGER)
