"""Окно программы: обычное или во весь экран (F11). Картинка рисуется в WINDOW_SIZE и растягивается
под окно или экран с полями по краям (pygame.SCALED) — вёрстка одна на любой монитор."""

from __future__ import annotations

import logging

import pygame

from cfpilot.ui import WINDOW_SIZE

log = logging.getLogger(__name__)

_state = {"fullscreen": False}


def open_window(fullscreen: bool) -> pygame.Surface:
    # RESIZABLE и во весь экран: после F11 окно снова можно растянуть мышкой.
    flags = pygame.SCALED | pygame.RESIZABLE | (pygame.FULLSCREEN if fullscreen else 0)
    try:
        screen = pygame.display.set_mode(WINDOW_SIZE, flags)
    except pygame.error as error:  # растягивать умеет не всякая видеокарта/VM — тогда обычное окно
        log.warning("Окно с масштабированием не открылось (%s) — открываю обычное", error)
        screen = pygame.display.set_mode(WINDOW_SIZE, 0)
        fullscreen = False
    _state["fullscreen"] = fullscreen
    return screen


def is_fullscreen() -> bool:
    return _state["fullscreen"]


def toggle_fullscreen() -> None:
    try:
        pygame.display.toggle_fullscreen()  # pygame 2.6 возвращает 0 и при успехе — смотрим на само окно
    except pygame.error as error:  # нет окна или видеодрайвер не умеет — остаёмся как есть
        log.warning("Не удалось переключить полноэкранный режим: %s", error)
        return
    surface = pygame.display.get_surface()
    if surface is not None:
        _state["fullscreen"] = bool(surface.get_flags() & pygame.FULLSCREEN)
