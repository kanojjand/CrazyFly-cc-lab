"""Оформление в стиле CC Lab: монохром, шестиугольный логотип со звездой, скошенные углы.

Всё взято с сайта лаборатории (тёмная тема): фон #050505, карточки #0d0d0d, линии — белый 10–24 %,
акцент — белый; заголовки Unbounded, текст Manrope, подписи JetBrains Mono капсом с разрядкой.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

import pygame

from cfpilot.i18n import t

BG = (5, 5, 5)
CARD = (13, 13, 13)
CARD_HI = (22, 22, 22)
GRID = (24, 24, 24)
LINE = (31, 31, 31)  # rgba(255, 255, 255, .10) поверх фона
LINE_STRONG = (65, 65, 65)  # .24
TEXT = (244, 244, 242)
PROSE = (214, 214, 210)
MUTED = (166, 166, 162)
DIM = (113, 113, 109)
ACCENT = (255, 255, 255)
OK = (139, 227, 176)
DANGER = (255, 122, 122)
WARN = (243, 201, 122)  # на сайте жёлтого нет — тёплый тон в той же пастели, что ok и danger
Color = tuple[int, int, int]

CHAMFER = 12  # px — срез угла у карточек, как --chamfer на сайте
HEADER_HEIGHT = 62
GRID_STEP = 40
TEXT_CACHE_LIMIT = 800
SUPERSAMPLE = 4  # логотип рисуем крупнее и уменьшаем — так края гладкие

FONT_DIR = Path(__file__).with_name("fonts")
DISPLAY_FONT = "Unbounded-SemiBold.ttf"
BODY_FONT = "Manrope-Medium.ttf"
BODY_BOLD_FONT = "Manrope-Bold.ttf"
MONO_FONT = "JetBrainsMono-Medium.ttf"
FALLBACK_FONTS = "segoeui,arial,dejavusans"

# Логотип с сайта (svg, viewBox 0 0 321 345): большая «C», малая «C» и звезда.
LOGO_SIZE = (321, 345)
LOGO_BIG_C = (
    (229.8, 1), (320.2, 79), (320.2, 135.6), (278, 135.6), (278, 97.8), (229.1, 55.8), (119.6, 143.2),
    (119.6, 207.5), (216.3, 290.2), (278, 240.6), (278, 206.1), (320.2, 206.1), (320.2, 260), (215.5, 344.5),
    (75.6, 226.2), (75.6, 125.8),
)
LOGO_SMALL_C = (
    (92, 0.3), (147.9, 47.8), (115.5, 74.7), (92.4, 55.1), (43, 96.1), (43, 250.9), (89.5, 290.6),
    (110.1, 274.4), (140.9, 300.7), (88.4, 344.6), (0.3, 269.3), (0.3, 77.7),
)
LOGO_STAR = ((219, 123.8), (229.9, 163.8), (269, 175.2), (230.2, 186.7), (219, 226.5), (208.1, 186.9),
             (167.8, 175.2), (208.1, 164.1))
LOGO_STAR_CENTER = (218.4, 175.2)
LOGO_STAR_SIZE = 102.0
# Четырёхконечная звезда из CSS сайта (clip-path) — в долях стороны квадрата.
STAR = ((0.5, 0.0), (0.61, 0.39), (1.0, 0.5), (0.61, 0.61), (0.5, 1.0), (0.39, 0.61), (0.0, 0.5), (0.39, 0.39))


def mix(base: Color, top: Color, amount: float) -> Color:
    amount = max(0.0, min(1.0, amount))
    return tuple(round(a + (b - a) * amount) for a, b in zip(base, top))


def load_font(name: str, size: int) -> pygame.font.Font:
    try:
        return pygame.font.Font(str(FONT_DIR / name), size)
    except (OSError, pygame.error):  # шрифт не упакован — лучше системный, чем падение
        return pygame.font.SysFont(FALLBACK_FONTS, size)


@lru_cache(maxsize=32)
def logo_surface(height: int, color: Color = ACCENT, with_star: bool = True) -> pygame.Surface:
    """Логотип CC Lab высотой height пикселей на прозрачном фоне."""
    scale = height * SUPERSAMPLE / LOGO_SIZE[1]
    big = pygame.Surface((math.ceil(LOGO_SIZE[0] * scale) + 1, math.ceil(LOGO_SIZE[1] * scale) + 1), pygame.SRCALPHA)
    shapes = (LOGO_BIG_C, LOGO_SMALL_C) + ((LOGO_STAR,) if with_star else ())
    for shape in shapes:
        pygame.draw.polygon(big, color, [(x * scale, y * scale) for x, y in shape])
    width = round(LOGO_SIZE[0] * height / LOGO_SIZE[1])
    return pygame.transform.smoothscale(big, (width, height))


def icon_surface(size: int) -> pygame.Surface:
    """Значок окна и .exe: белый логотип на чёрном скруглённом квадрате — как картинка-логотип CC Lab."""
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.rect(surface, BG, surface.get_rect(), border_radius=size // 5)
    logo = logo_surface(round(size * 0.68))
    surface.blit(logo, logo.get_rect(center=surface.get_rect().center))
    return surface


def star_points(center: tuple[float, float], size: float, angle: float = 0.0) -> list[tuple[float, float]]:
    cx, cy = center
    cos, sin = math.cos(angle), math.sin(angle)
    return [(cx + ((x - 0.5) * cos - (y - 0.5) * sin) * size, cy + ((x - 0.5) * sin + (y - 0.5) * cos) * size)
            for x, y in STAR]


def draw_star(surface: pygame.Surface, center: tuple[float, float], size: float, color: Color,
              angle: float = 0.0) -> None:
    points = star_points(center, size, angle)
    pygame.draw.polygon(surface, color, points)
    pygame.draw.aalines(surface, color, True, points)


def chamfer_points(rect: pygame.Rect, cut: int) -> list[tuple[int, int]]:
    """Срезаны левый верхний и правый нижний углы — как у кнопок на сайте."""
    right, bottom = rect.right - 1, rect.bottom - 1
    return [(rect.x + cut, rect.y), (right, rect.y), (right, bottom - cut), (right - cut, bottom),
            (rect.x, bottom), (rect.x, rect.y + cut)]


def grid_background(size: tuple[int, int], focus: tuple[float, float] = (0.7, 0.4)) -> pygame.Surface:
    """Сетка как на главной сайта: тонкие линии, гаснущие от фокуса к краям."""
    width, height = size
    surface = pygame.Surface(size)
    surface.fill(BG)
    fx, fy = focus[0] * width, focus[1] * height
    reach = math.hypot(width, height) * 0.6

    def shade(x: float, y: float) -> Color:
        return mix(BG, GRID, 1.0 - math.hypot(x - fx, y - fy) / reach)

    for x in range(0, width, GRID_STEP):
        for y in range(0, height, GRID_STEP):
            pygame.draw.line(surface, shade(x, y + GRID_STEP / 2), (x, y), (x, y + GRID_STEP))
            pygame.draw.line(surface, shade(x + GRID_STEP / 2, y), (x, y), (x + GRID_STEP, y))
    return surface


class Fonts:
    def __init__(self) -> None:
        self.hero = load_font(DISPLAY_FONT, 28)
        self.title = load_font(DISPLAY_FONT, 21)
        self.value = load_font(DISPLAY_FONT, 18)
        self.brand = load_font(DISPLAY_FONT, 17)
        self.body = load_font(BODY_FONT, 16)
        self.small = load_font(BODY_FONT, 14)
        self.strong = load_font(BODY_BOLD_FONT, 16)
        self.mono = load_font(MONO_FONT, 13)
        self.label = load_font(MONO_FONT, 11)


class Painter:
    """Рисует элементы в стиле CC Lab; тексты кэширует — их перерисовывают 50 раз в секунду."""

    def __init__(self, screen: pygame.Surface) -> None:
        self.screen = screen
        self.fonts = Fonts()
        self._texts: dict = {}
        self._background: pygame.Surface | None = None

    # ---------- текст ----------

    def render(self, text: str, font: pygame.font.Font, color: Color) -> pygame.Surface:
        key = (text, id(font), color)
        surface = self._texts.get(key)
        if surface is None:
            if len(self._texts) > TEXT_CACHE_LIMIT:
                self._texts.clear()
            surface = self._texts[key] = font.render(text, True, color)
        return surface

    def text(self, text: str, pos: tuple[float, float], font: pygame.font.Font | None = None, color: Color = TEXT,
             anchor: str = "topleft") -> pygame.Rect:
        surface = self.render(text, font or self.fonts.body, color)
        rect = surface.get_rect(**{anchor: (round(pos[0]), round(pos[1]))})
        self.screen.blit(surface, rect)
        return rect

    def spaced(self, text: str, pos: tuple[float, float], color: Color = MUTED, font: pygame.font.Font | None = None,
               tracking: float = 1.5, anchor: str = "topleft") -> pygame.Rect:
        """Моно-подпись капсом с разрядкой (у pygame нет letter-spacing — ставим буквы по одной)."""
        font = font or self.fonts.label
        glyphs = [self.render(char, font, color) for char in text.upper()]
        width = sum(glyph.get_width() for glyph in glyphs) + tracking * max(0, len(glyphs) - 1)
        rect = pygame.Rect(0, 0, round(width), font.get_height())
        setattr(rect, anchor, (round(pos[0]), round(pos[1])))
        x = float(rect.x)
        for glyph in glyphs:
            self.screen.blit(glyph, (round(x), rect.y))
            x += glyph.get_width() + tracking
        return rect

    def eyebrow(self, text: str, pos: tuple[float, float], color: Color = MUTED) -> pygame.Rect:
        """Звёздочка и подпись капсом — как .eyebrow на сайте."""
        x, y = pos
        draw_star(self.screen, (x + 4.5, y + self.fonts.label.get_height() / 2), 9, color)
        return self.spaced(text, (x + 17, y), color)

    def wrap(self, text: str, font: pygame.font.Font, width: int) -> list[str]:
        lines, line = [], ""
        for word in text.split():
            candidate = f"{line} {word}".strip()
            if line and font.size(candidate)[0] > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        return lines + [line] if line else lines

    def paragraph(self, text: str, pos: tuple[float, float], width: int, font: pygame.font.Font | None = None,
                  color: Color = PROSE, max_lines: int | None = None, spacing: int = 4) -> int:
        """Текст с переносом по словам. Возвращает y под последней строкой."""
        font = font or self.fonts.body
        x, y = pos
        lines = self.wrap(text, font, width)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [lines[max_lines - 1] + "…"]
        for line in lines:
            self.text(line, (x, y), font, color)
            y += font.get_height() + spacing
        return round(y)

    # ---------- формы ----------

    def background(self) -> None:
        size = self.screen.get_size()
        if self._background is None or self._background.get_size() != size:
            self._background = grid_background(size)
        self.screen.blit(self._background, (0, 0))

    def card(self, rect, fill: Color = CARD, border: Color | None = LINE_STRONG, cut: int = CHAMFER) -> pygame.Rect:
        rect = pygame.Rect(rect)
        points = chamfer_points(rect, cut)
        pygame.draw.polygon(self.screen, fill, points)
        if border is not None:
            pygame.draw.aalines(self.screen, border, True, points)
        return rect

    def logo(self, pos: tuple[int, int], height: int, color: Color = ACCENT, with_star: bool = True) -> pygame.Rect:
        surface = logo_surface(height, color, with_star)
        return self.screen.blit(surface, pos)

    def star(self, center: tuple[float, float], size: float, color: Color = ACCENT, angle: float = 0.0) -> None:
        draw_star(self.screen, center, size, color, angle)

    def progress(self, rect, fraction: float, color: Color = ACCENT) -> None:
        rect = pygame.Rect(rect)
        pygame.draw.rect(self.screen, LINE, rect, border_radius=rect.height // 2)
        filled = rect.copy()
        filled.width = round(rect.width * max(0.0, min(1.0, fraction)))
        if filled.width:
            pygame.draw.rect(self.screen, color, filled, border_radius=rect.height // 2)

    def pill(self, text: str, pos: tuple[float, float], color: Color = MUTED, anchor: str = "topleft") -> pygame.Rect:
        """Метка-«таблетка» с рамкой — как .tag на сайте."""
        label = self.render(text.upper(), self.fonts.label, color)
        rect = pygame.Rect(0, 0, label.get_width() + 20, label.get_height() + 10)
        setattr(rect, anchor, (round(pos[0]), round(pos[1])))
        pygame.draw.rect(self.screen, mix(BG, color, 0.55), rect, 1, border_radius=rect.height // 2)
        self.screen.blit(label, label.get_rect(center=rect.center))
        return rect

    def keycap(self, key: str, pos: tuple[float, float]) -> pygame.Rect:
        label = self.render(key, self.fonts.label, TEXT)
        rect = pygame.Rect(round(pos[0]), round(pos[1]), max(label.get_width() + 14, 24), 22)
        pygame.draw.rect(self.screen, CARD_HI, rect, border_radius=4)
        pygame.draw.rect(self.screen, LINE_STRONG, rect, 1, border_radius=4)
        self.screen.blit(label, label.get_rect(center=rect.center))
        return rect

    def hints(self, items: Iterable[tuple[str, str]], pos: tuple[float, float], gap: int = 22) -> int:
        """Ряд подсказок «клавиша — действие». Возвращает x правого края."""
        x, y = pos
        for key, action in items:
            cap = self.keycap(key, (x, y))
            text = self.text(action, (cap.right + 8, cap.centery), self.fonts.small, MUTED, anchor="midleft")
            x = text.right + gap
        return round(x - gap)

    def header(self, subtitle: str) -> None:
        self.logo((24, 15), 32)
        self.text("CC Lab", (62, 12), self.fonts.brand, TEXT)
        self.spaced(subtitle, (63, 37), DIM, tracking=1.8)
        pygame.draw.line(self.screen, LINE, (0, HEADER_HEIGHT), (self.screen.get_width(), HEADER_HEIGHT))

    def bars(self, values: Sequence[float], pos: tuple[int, int], width: int, row: int,
             ranges: Sequence[tuple[float, float]] = (), active: int | None = None, limit: int = 8) -> int:
        """Оси пульта полосками: ход за калибровку, текущее положение, выделенная ось. Возвращает низ."""
        x, y = pos
        for index, value in enumerate(values[:limit]):
            highlighted = index == active
            color = ACCENT if highlighted else MUTED
            self.spaced(t("ось {axis}", axis=index), (x, y + row // 2), color, anchor="midleft", tracking=1.0)
            track = pygame.Rect(x + 56, y + row // 2 - 3, width - 120, 6)
            pygame.draw.rect(self.screen, LINE, track, border_radius=3)
            if index < len(ranges):
                low, high = ranges[index]
                span = pygame.Rect(track.x + round((low + 1) / 2 * track.width), track.y,
                                   max(2, round((high - low) / 2 * track.width)), track.height)
                pygame.draw.rect(self.screen, LINE_STRONG, span, border_radius=3)
            pygame.draw.line(self.screen, DIM, (track.centerx, track.y - 3), (track.centerx, track.bottom + 2))
            marker = track.x + round((max(-1.0, min(1.0, value)) + 1) / 2 * track.width)
            draw_star(self.screen, (marker, track.centery), 12 if highlighted else 10, ACCENT if highlighted else TEXT)
            self.text(f"{value:+.2f}", (x + width, y + row // 2), self.fonts.mono, color, anchor="midright")
            y += row
        return y

    def buttons(self, pressed: Sequence[bool], pos: tuple[int, int], per_row: int = 16, size: int = 19,
                gap: int = 3, limit: int = 32) -> int:
        """Кнопки пульта квадратиками с номерами; нажатые — белые. Возвращает низ."""
        x0, y = pos
        for index, down in enumerate(pressed[:limit]):
            column, line = index % per_row, index // per_row
            rect = pygame.Rect(x0 + column * (size + gap), y + line * (size + gap), size, size)
            pygame.draw.rect(self.screen, ACCENT if down else CARD_HI, rect, border_radius=3)
            pygame.draw.rect(self.screen, ACCENT if down else LINE_STRONG, rect, 1, border_radius=3)
            label = self.render(str(index), self.fonts.label, BG if down else DIM)
            self.screen.blit(label, label.get_rect(center=rect.center))
        rows = math.ceil(min(len(pressed), limit) / per_row)
        return y + rows * (size + gap)
