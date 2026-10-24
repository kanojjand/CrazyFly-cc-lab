"""Экраны вокруг полёта: подключение (с повторами), калибровка пульта, сообщения. Стиль — CC Lab."""

from __future__ import annotations

import math
from dataclasses import dataclass

from cfpilot.calibration import CalibrationView
from cfpilot.channels import RELIABLE
from cfpilot.connection import Stage, Status
from cfpilot.i18n import LANGUAGE_NAMES, N_, language, next_language, t
from cfpilot.theme import (
    ACCENT,
    CARD,
    DANGER,
    DIM,
    LOGO_SIZE,
    LOGO_STAR_CENTER,
    LOGO_STAR_SIZE,
    MUTED,
    OK,
    PROSE,
    TEXT,
    WARN,
    Painter,
)

LOGO_POS, LOGO_HEIGHT = (96, 150), 240
TEXT_X, TEXT_WIDTH = 440, 496
SPIN_SPEED = 1.6  # рад/с — звезда логотипа крутится, пока программа работает
PULSE_SPEED = 3.0
STAGE_TEXTS = {
    Stage.NO_RADIO: (N_("Вставь Crazyradio"), N_("Радио не видно в USB. Вставь его — программа подхватит сама, "
                                                 "перезапускать ничего не нужно.")),
    Stage.NO_DRIVER: (N_("Нужен драйвер"), N_("Crazyradio вставлен, но без драйвера libusb-win32. Ставлю его сам — "
                                              "Windows спросит разрешения.")),
    Stage.SEARCHING: (N_("Ищу дрон"), N_("Включи дрон и поставь его ровно. Ищу на всех каналах и подключусь, как "
                                         "только он ответит, — включать можно в любой момент. Первое подключение "
                                         "занимает 10–20 секунд.")),
    Stage.CHOOSING: (N_("Выбери дрон"), N_("В эфире несколько дронов. Нажми цифру своего — подключусь и запомню его. "
                                           "Ближний к радио обычно отвечает лучше.")),
    Stage.REBOOTING: (N_("Перезагружаю дрон"), N_("Перезапускаю дрон по радио — это снимает блокировку прошивки. "
                                                  "Не трогай его пару секунд.")),
    Stage.CONNECTED: (N_("Подключено"), N_("Дрон на связи.")),
}
# Один дрон, но не на канале из настроек: адрес у всех дронов с завода один — вдруг он соседский.
ONE_DRONE_TEXT = (N_("Это твой дрон?"), N_("Нашёл дрон на другом канале, не на том, что в настройках. Если он "
                                           "твой — нажми 1: подключусь и запомню канал, дальше спрашивать не буду."))
STAGE_COLORS = {Stage.NO_RADIO: WARN, Stage.NO_DRIVER: WARN, Stage.CONNECTED: OK}
SPINNING = frozenset({Stage.SEARCHING, Stage.REBOOTING})
MAX_CHOICES = 9  # столько дронов помещается на экран и выбирается клавишами 1–9 (см. lobby)


def window_keys() -> tuple[tuple[str, str], ...]:
    """F11 и F2 работают на любом экране (см. app.window_key). Язык подписан на нём самом."""
    return ("F11", t("весь экран")), ("F2", LANGUAGE_NAMES[next_language(language())])


@dataclass(frozen=True)
class ConnectView:
    uri: str
    status: Status
    reason: str = ""  # почему мы здесь: связь потеряна, дрон перезагружают
    driver: str = ""  # как идёт установка драйвера
    message: str = ""  # итог калибровки и т.п.
    controller: str = ""  # «DualSense — свой профиль»; пусто — пульта нет
    can_reboot: bool = True
    elapsed: float = 0.0  # с — для анимации


def _draw_mark(painter: Painter, stage: Stage, elapsed: float) -> None:
    """Логотип, у которого звезда крутится и «мерцает», пока идёт работа (как на главной сайта)."""
    painter.logo(LOGO_POS, LOGO_HEIGHT, with_star=False)
    scale = LOGO_HEIGHT / LOGO_SIZE[1]
    center = (LOGO_POS[0] + LOGO_STAR_CENTER[0] * scale, LOGO_POS[1] + LOGO_STAR_CENTER[1] * scale)
    size = LOGO_STAR_SIZE * scale
    angle = 0.0
    if stage in SPINNING:
        angle = elapsed * SPIN_SPEED
        size *= 0.85 + 0.15 * (0.5 + 0.5 * math.sin(elapsed * PULSE_SPEED))
    painter.star(center, size, STAGE_COLORS.get(stage, ACCENT), angle)


def _row(painter: Painter, label: str, value: str, y: int, color=TEXT) -> int:
    painter.spaced(label, (TEXT_X, y + 2), DIM)
    painter.text(value, (TEXT_X + 96, y), painter.fonts.mono, color)
    return y + 24


def _notice(painter: Painter, text: str, y: int, color) -> int:
    painter.star((TEXT_X + 5, y + 9), 10, color)
    return painter.paragraph(text, (TEXT_X + 20, y), TEXT_WIDTH - 20, painter.fonts.small, color, max_lines=3) + 8


def draw_connect(painter: Painter, view: ConnectView) -> None:
    painter.background()
    painter.header("Crazyflie Pilot")
    stage = view.status.stage
    _draw_mark(painter, stage, view.elapsed)
    choices = view.status.drones[:MAX_CHOICES] if stage is Stage.CHOOSING else ()
    title, body = (t(text) for text in (ONE_DRONE_TEXT if len(choices) == 1 else STAGE_TEXTS[stage]))
    y = 96
    if view.reason:
        y = _notice(painter, view.reason, y, WARN) + 6
    painter.eyebrow(t("Подключение"), (TEXT_X, max(y, 120)))
    y = max(y, 120) + 24
    painter.text(title, (TEXT_X, y), painter.fonts.hero, STAGE_COLORS.get(stage, TEXT))
    y = painter.paragraph(body, (TEXT_X, y + 46), TEXT_WIDTH, painter.fonts.body, PROSE) + 16
    for number, drone in enumerate(choices, start=1):
        y = _row(painter, str(number), drone.describe(), y, OK if drone.answers >= RELIABLE else WARN)
    if not choices:
        y = _row(painter, t("адрес"), view.uri, y)
    if view.status.attempt and not choices:
        y = _row(painter, t("попытка"), str(view.status.attempt), y)
    y = _row(painter, t("пульт"), view.controller or t("не подключён"), y, TEXT if view.controller else DIM) + 8
    if view.status.error and stage is not Stage.CONNECTED:
        y = _notice(painter, view.status.error, y, MUTED)
    for text in (view.driver, view.message):
        if text:
            y = _notice(painter, text, y, TEXT)
    keys = [("1" if len(choices) == 1 else f"1–{len(choices)}", t("выбрать дрон"))] if choices else []
    keys.append(("C", t("калибровка пульта")))
    if view.can_reboot:
        keys.append(("R", t("перезагрузить дрон")))
    if stage is Stage.NO_DRIVER or view.driver:
        keys.append(("D", t("поставить драйвер")))
    keys.extend(window_keys())
    keys.append(("Esc", t("выход")))
    card = painter.card((24, 540, painter.screen.get_width() - 48, 56), fill=CARD)
    painter.hints(keys, (card.x + 16, card.y + 17))


def draw_calibration(painter: Painter, view: CalibrationView) -> None:
    painter.background()
    painter.header(t("Калибровка пульта"))
    width = painter.screen.get_width()
    if view.controller:
        painter.text(view.controller, (width - 24, 31), painter.fonts.mono, MUTED, anchor="midright")
    _draw_step(painter, view)
    _draw_inputs(painter, view)
    card = painter.card((24, 500, width - 48, 104), fill=CARD)
    painter.hints([(t(key), t(action)) for key, action in view.keys], (card.x + 16, card.y + 16))
    painter.star((card.x + 21, card.y + 70), 10, DANGER)
    painter.text(t("Пробел на клавиатуре глушит моторы и во время калибровки. Летать с пульта можно после неё."),
                 (card.x + 36, card.y + 70), painter.fonts.small, DIM, anchor="midleft")


def _draw_step(painter: Painter, view: CalibrationView) -> None:
    card = painter.card((24, 78, 504, 410))
    x, width = card.x + 20, card.width - 40
    painter.eyebrow(t("Шаг {step} из {total}", step=view.step, total=view.total), (x, card.y + 16))
    painter.progress((x, card.y + 38, width, 4), view.step / view.total)
    painter.text(view.title, (x, card.y + 54), painter.fonts.hero, TEXT)
    y = painter.paragraph(view.instruction, (x, card.y + 100), width, painter.fonts.body, PROSE)
    if view.progress is not None:
        painter.progress((x, y + 10, width, 8), view.progress, OK)
        y += 30
    if view.message:
        painter.star((x + 5, y + 13), 10, WARN)
        y = painter.paragraph(view.message, (x + 20, y + 4), width - 20, painter.fonts.small, WARN, max_lines=2)
    y = max(y + 12, card.y + 250)
    if view.controllers and not view.controller:
        painter.eyebrow(t("Подключены"), (x, y))
        for row, name in enumerate(view.controllers[:5]):
            painter.text(name, (x, y + 22 + row * 22), painter.fonts.small, TEXT)
    elif view.done:
        painter.eyebrow(t("Назначено"), (x, y))
        column_width = width // 2
        for index, (what, how) in enumerate(view.done[:10]):
            column, row = divmod(index, 5)
            left = x + column * column_width
            top = y + 22 + row * 22
            painter.text(what, (left, top), painter.fonts.small, MUTED)
            painter.text(how, (left + column_width - 12, top), painter.fonts.mono, TEXT, anchor="topright")


def _draw_inputs(painter: Painter, view: CalibrationView) -> None:
    card = painter.card((552, 78, 384, 410))
    x, width = card.x + 16, card.width - 32
    painter.eyebrow(t("Оси"), (x, card.y + 14))
    if not view.axes and not view.buttons:
        painter.paragraph(t("Здесь появятся оси и кнопки, когда пульт будет выбран."), (x, card.y + 40), width,
                          painter.fonts.small, DIM)
        return
    bottom = painter.bars(view.axes, (x, card.y + 34), width, 24, ranges=view.ranges, active=view.active_axis)
    painter.eyebrow(t("Кнопки"), (x, bottom + 14))
    painter.buttons(view.buttons, (x, bottom + 36))


def draw_notice(painter: Painter, title: str, lines: tuple[str, ...], color=TEXT) -> None:
    painter.background()
    painter.header("Crazyflie Pilot")
    painter.logo(LOGO_POS, LOGO_HEIGHT)
    painter.eyebrow(t("Внимание"), (TEXT_X, 150), color)
    painter.text(title, (TEXT_X, 174), painter.fonts.hero, color)
    y = 228
    for line in lines:
        if not line:
            y += 12
            continue
        y = painter.paragraph(line, (TEXT_X, y), TEXT_WIDTH, painter.fonts.body, PROSE)
