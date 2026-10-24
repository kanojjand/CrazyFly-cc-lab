"""Окно полёта: телеметрия, стики, пульт и подсказки — в стиле CC Lab (pygame)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pygame

from cfpilot.calibration import CalibrationView
from cfpilot.controls import Sticks
from cfpilot.flight import FlightMode, Phase
from cfpilot.i18n import N_, t
from cfpilot.link import Telemetry
from cfpilot.screens import ConnectView, draw_calibration, draw_connect, draw_notice, window_keys
from cfpilot.theme import ACCENT, BG, CARD, DANGER, DIM, LINE, MUTED, OK, TEXT, WARN, Painter

WINDOW_SIZE = (960, 620)
PHASE_COLORS = {
    Phase.IDLE: MUTED,
    Phase.ARMING: WARN,
    Phase.THROW_READY: WARN,
    Phase.TAKING_OFF: ACCENT,
    Phase.FLYING: OK,
    Phase.LANDING: ACCENT,
    Phase.FLIPPING: ACCENT,
    Phase.KILLED: DANGER,
}
MODE_TITLES = {
    FlightMode.HOVER: N_("держит место и высоту"),
    FlightMode.ALTHOLD: N_("держит высоту"),
    FlightMode.MANUAL: N_("ручной газ"),
    FlightMode.ACRO: N_("FPV: полное ручное (acro)"),
}
COMMON_KEYS = (("T", N_("взлёт")), ("L", N_("посадка")), (N_("Пробел"), N_("стоп моторов")),
               ("Esc", N_("посадить и выйти")))
MOVE_KEYS = {
    FlightMode.HOVER: ((N_("Стрелки"), N_("вперёд и вбок")), ("A D", N_("поворот")), ("W S", N_("выше / ниже"))),
    FlightMode.ALTHOLD: ((N_("Стрелки"), N_("наклон")), ("A D", N_("поворот")), ("W S", N_("выше / ниже")),
                         (N_("Shift+стрелки"), N_("трим"))),
    FlightMode.MANUAL: ((N_("Стрелки"), N_("наклон")), ("A D", N_("поворот")), ("W S", N_("газ")),
                        (N_("Shift+стрелки"), N_("трим")), ("Num 8 2 4 6", N_("сальто"))),
    FlightMode.ACRO: ((N_("Стрелки"), N_("вращение (rate)")), ("A D", N_("поворот")), ("W S", N_("газ")),
                      ("Num 8 2 4 6", N_("сальто"))),
}
TOOL_KEYS = (("F", N_("режим FPV")), ("B", N_("бросок")), ("C", N_("калибровка")), ("J", N_("монитор пульта")), ("R", N_("перезагрузка")))
BATTERY_OK = 30  # %
BATTERY_LOW = 10  # %
FLIGHT_BATTERY_OK = 3.5  # В под нагрузкой в полёте
FLIGHT_BATTERY_LOW = 3.3  # В под нагрузкой — пора садиться (прошивка считает 3.2 В низким зарядом)
LINK_GOOD = 70  # % качества связи
LINK_POOR = 40
# Разряд 1S LiPo: (вольты, %) — точки таблицы из прошивки Crazyflie, между ними — линейно.
# В полёте напряжение проседает под нагрузкой, и процент вместе с ним — это нормально.
BATTERY_CURVE = (
    (3.00, 0), (3.78, 10), (3.83, 20), (3.87, 30), (3.89, 40), (3.92, 50),
    (3.96, 60), (4.00, 70), (4.04, 80), (4.10, 90), (4.20, 100),
)
# Разметка окна: левая колонка — состояние и телеметрия, правая — стики и пульт, внизу — клавиши.
LEFT, LEFT_WIDTH = 24, 504
RIGHT, RIGHT_WIDTH = 552, 384
TOP = 78
STICK_BOX = 150
MONITOR_AXES = 6


@dataclass(frozen=True)
class View:
    uri: str
    mode: FlightMode
    decks: str
    phase: Phase
    input_source: str
    telemetry: Telemetry
    sticks: Sticks
    target_height: float | None
    thrust: int | None
    trim: tuple[float, float] | None = None  # вправо, вперёд — град; None в hover
    battery_before_flight: float | None = None  # В на земле перед взлётом; None — дрон не в воздухе
    message: str = ""
    error: str | None = None
    warning: str = ""
    monitor: bool = False
    joystick_name: str = ""
    controller_profile: bool = False  # у подключённого пульта своя раскладка из калибровки
    axes: tuple[float, ...] = ()
    buttons: tuple[bool, ...] = ()
    height_sensor: bool = True  # есть чем мерить высоту (Flow, Z-ranger, Lighthouse, Loco)


def _fmt(value: float | None, pattern: str) -> str:
    return "—" if value is None else pattern.format(value)


def battery_percent(volts: float) -> int:
    if not math.isfinite(volts) or volts <= BATTERY_CURVE[0][0]:
        return 0
    for (low_v, low_p), (high_v, high_p) in zip(BATTERY_CURVE, BATTERY_CURVE[1:]):
        if volts <= high_v:
            return round(low_p + (volts - low_v) / (high_v - low_v) * (high_p - low_p))
    return 100


def battery_parts(volts: float | None, before_flight: float | None) -> tuple[str, str]:
    """(крупно, мелко). На земле — процент. В полёте напряжение проседает под нагрузкой, и процент
    по нему врал бы, поэтому крупно — живое напряжение, мелко — процент перед взлётом."""
    if volts is None:
        return "—", ""
    if before_flight is None:
        return f"{battery_percent(volts)}%", t("{volts:.2f} В", volts=volts)
    return t("{volts:.2f} В", volts=volts), t("{percent}% перед взлётом", percent=battery_percent(before_flight))


def battery_color(volts: float | None, before_flight: float | None) -> tuple[int, int, int]:
    if volts is None:
        return MUTED
    if before_flight is not None:  # в полёте — по напряжению под нагрузкой
        if volts >= FLIGHT_BATTERY_OK:
            return OK
        return WARN if volts >= FLIGHT_BATTERY_LOW else DANGER
    percent = battery_percent(volts)
    if percent >= BATTERY_OK:
        return OK
    return WARN if percent >= BATTERY_LOW else DANGER


def link_color(quality: float | None) -> tuple[int, int, int]:
    if quality is None:
        return DIM
    if quality >= LINK_GOOD:
        return OK
    return WARN if quality >= LINK_POOR else DANGER


class Dashboard:
    def __init__(self, screen: pygame.Surface) -> None:
        self._painter = Painter(screen)

    # ---------- экраны ----------

    def draw(self, view: View) -> None:
        painter = self._painter
        painter.background()
        painter.header("Crazyflie Pilot")
        self._draw_link(view)
        self._draw_phase(view)
        self._draw_telemetry(view)
        self._draw_notices(view)
        self._draw_sticks(view.sticks, view.mode)
        self._draw_controller(view)
        self._draw_help(view.mode)
        pygame.display.flip()

    def draw_notice(self, title: str, lines: tuple[str, ...], color=TEXT) -> None:
        """Экран-сообщение: например, режим не подходит к декам."""
        draw_notice(self._painter, title, lines, color)
        pygame.display.flip()

    def draw_connect(self, view: ConnectView) -> None:
        draw_connect(self._painter, view)
        pygame.display.flip()

    def draw_calibration(self, view: CalibrationView) -> None:
        draw_calibration(self._painter, view)
        pygame.display.flip()

    # ---------- части окна полёта ----------

    def _draw_link(self, view: View) -> None:
        painter = self._painter
        quality = view.telemetry.link_quality
        right = painter.screen.get_width() - LEFT
        chip = painter.spaced(t("связь {quality}", quality=_fmt(quality, "{:.0f}%")), (right, 31), MUTED,
                             anchor="midright")
        pygame.draw.circle(painter.screen, link_color(quality), (chip.left - 10, chip.centery), 4)
        painter.text(view.uri, (chip.left - 26, 31), painter.fonts.mono, DIM, anchor="midright")

    def _draw_phase(self, view: View) -> None:
        painter = self._painter
        card = painter.card((LEFT, TOP, LEFT_WIDTH, 86))
        right = card.right - 16
        pill = painter.pill(view.mode.value, (right, card.y + 14), TEXT, anchor="topright")
        detail = painter.text(t(MODE_TITLES[view.mode]), (right, pill.bottom + 6), painter.fonts.small, MUTED,
                              anchor="topright")
        decks = painter.text(t("деки: {decks}", decks=view.decks), (right, detail.bottom + 1), painter.fonts.small, DIM,
                             anchor="topright")
        painter.eyebrow(t("Состояние"), (card.x + 16, card.y + 14))
        room = min(detail.left, decks.left, pill.left) - card.x - 32
        phase = t(view.phase.value)
        font = painter.fonts.hero if painter.fonts.hero.size(phase)[0] <= room else painter.fonts.title
        painter.text(phase, (card.x + 16, card.y + 34), font, PHASE_COLORS[view.phase])

    def _tiles(self, view: View) -> list[tuple[str, str, str, tuple[int, int, int]]]:
        """Плитки телеметрии: (подпись, значение, пояснение, цвет значения)."""
        telemetry = view.telemetry
        value, note = battery_parts(telemetry.battery, view.battery_before_flight)
        target = "" if view.target_height is None else t("цель {height:.2f} м", height=view.target_height)
        link = (t("связь"), _fmt(telemetry.link_quality, "{:.0f}%"), t("качество радио"),
                link_color(telemetry.link_quality))
        height = (t("высота"), _fmt(telemetry.height, t("{:.2f} м")), target, TEXT) if view.height_sensor else link
        tiles = [
            (t("батарея"), value, note, battery_color(telemetry.battery, view.battery_before_flight)),
            height,
            (t("курс"), _fmt(telemetry.yaw, "{:+.0f}°"), "", TEXT),
            (t("крен"), _fmt(telemetry.roll, "{:+.1f}°"), "", TEXT),
            (t("тангаж"), _fmt(telemetry.pitch, "{:+.1f}°"), "", TEXT),
        ]
        trim = "" if view.trim is None else t("трим {roll:+.1f}° / {pitch:+.1f}°", roll=view.trim[0],
                                                 pitch=view.trim[1])
        if view.thrust is not None:
            tiles.append((t("тяга"), str(view.thrust), trim, TEXT))
        elif view.trim is not None:
            tiles.append((t("трим"), f"{view.trim[0]:+.1f}° {view.trim[1]:+.1f}°", t("вправо / вперёд"), TEXT))
        else:
            tiles.append(link)
        return tiles

    def _draw_telemetry(self, view: View) -> None:
        painter = self._painter
        card = painter.card((LEFT, TOP + 98, LEFT_WIDTH, 200))
        width, height = card.width // 3, card.height // 2
        for column in (1, 2):
            x = card.x + column * width
            pygame.draw.line(painter.screen, LINE, (x, card.y + 14), (x, card.bottom - 14))
        pygame.draw.line(painter.screen, LINE, (card.x + 16, card.centery), (card.right - 16, card.centery))
        for index, (label, value, note, color) in enumerate(self._tiles(view)):
            x = card.x + (index % 3) * width + 16
            y = card.y + (index // 3) * height + 16
            painter.spaced(label, (x, y), DIM)
            painter.text(value, (x, y + 22), painter.fonts.value, color)
            if note:
                painter.text(note, (x, y + 54), painter.fonts.label, DIM)

    def _draw_notices(self, view: View) -> None:
        painter = self._painter
        notices = []
        if view.error:
            notices.append((DANGER, view.error))
        elif view.warning:
            notices.append((WARN, view.warning))
        if view.message:
            notices.append((TEXT, view.message))
        y = TOP + 312
        for color, text in notices:
            painter.star((LEFT + 5, y + 9), 10, color)
            y = painter.paragraph(text, (LEFT + 20, y), LEFT_WIDTH - 24, painter.fonts.small, color, max_lines=2) + 6

    def _draw_stick(self, x: int, y: int, horizontal: float, vertical: float, label: str) -> None:
        painter = self._painter
        box = painter.card((x, y, STICK_BOX, STICK_BOX), fill=BG, border=LINE, cut=10)
        pygame.draw.line(painter.screen, LINE, (box.centerx, box.y + 10), (box.centerx, box.bottom - 10))
        pygame.draw.line(painter.screen, LINE, (box.x + 10, box.centery), (box.right - 10, box.centery))
        pygame.draw.circle(painter.screen, DIM, box.center, 22, 1)
        reach = STICK_BOX // 2 - 14
        painter.star((box.centerx + horizontal * reach, box.centery - vertical * reach), 20, ACCENT)
        painter.spaced(label, (box.centerx, box.bottom + 10), DIM, anchor="midtop", tracking=1.0)

    def _draw_sticks(self, sticks: Sticks, mode: FlightMode) -> None:
        painter = self._painter
        card = painter.card((RIGHT, TOP, RIGHT_WIDTH, 214))
        painter.eyebrow(t("Стики"), (card.x + 16, card.y + 14))
        throttle = t("газ") if mode is FlightMode.MANUAL else t("выше-ниже")
        self._draw_stick(card.x + 16, card.y + 36, sticks.yaw, sticks.throttle, t("поворот · {throttle}", throttle=throttle))
        self._draw_stick(card.right - 16 - STICK_BOX, card.y + 36, sticks.roll, sticks.pitch, t("вбок · вперёд"))

    def _draw_controller(self, view: View) -> None:
        painter = self._painter
        card = painter.card((RIGHT, TOP + 226, RIGHT_WIDTH, 184))
        x, width = card.x + 16, card.width - 32
        painter.eyebrow(t("Пульт"), (x, card.y + 14))
        if view.monitor:
            bottom = painter.bars(view.axes, (x, card.y + 34), width, 16, limit=MONITOR_AXES)
            painter.buttons(view.buttons, (x, bottom + 6), size=16, gap=6)
            return
        if not view.joystick_name:
            painter.text(t("Не подключён"), (x, card.y + 38), painter.fonts.strong, MUTED)
            painter.paragraph(t("Подключи геймпад или пульт по USB — программа найдёт его сама."),
                              (x, card.y + 66), width, painter.fonts.small, DIM, max_lines=2)
        else:
            name = painter.wrap(view.joystick_name, painter.fonts.strong, width)[0]
            painter.text(name, (x, card.y + 38), painter.fonts.strong, TEXT)
            if view.controller_profile:
                painter.pill(t("свой профиль"), (x, card.y + 66), OK)
            else:
                painter.pill(t("общая раскладка — нажми C"), (x, card.y + 66), WARN)
        painter.paragraph(t("Управление: {source}", source=view.input_source), (x, card.y + 118), width, painter.fonts.small, MUTED,
                          max_lines=2)

    def _draw_help(self, mode: FlightMode) -> None:
        painter = self._painter
        card = painter.card((LEFT, 500, painter.screen.get_width() - 2 * LEFT, 104), fill=CARD)
        for row, keys in enumerate((COMMON_KEYS, MOVE_KEYS[mode], TOOL_KEYS + window_keys())):
            painter.hints([(t(key), t(action)) for key, action in keys], (card.x + 16, card.y + 12 + row * 30))
