"""Окно: обычное или во весь экран; переключение F11 (подменённый pygame.display)."""

from types import SimpleNamespace

import pygame
import pytest

from cfpilot import window
from cfpilot.ui import WINDOW_SIZE


@pytest.fixture
def fake_display(monkeypatch):
    modes = []
    monkeypatch.setattr(pygame.display, "set_mode", lambda size, flags=0: modes.append((size, flags)) or "screen")
    yield modes
    window.open_window(False)  # не оставляем «весь экран» следующим тестам


def test_window_scales_picture_and_can_start_full_screen(fake_display):
    assert window.open_window(True) == "screen"
    size, flags = fake_display[-1]
    assert size == WINDOW_SIZE
    assert flags & pygame.SCALED and flags & pygame.FULLSCREEN
    assert window.is_fullscreen()
    window.open_window(False)
    assert not fake_display[-1][1] & pygame.FULLSCREEN
    assert not window.is_fullscreen()


def test_window_stays_resizable_after_leaving_full_screen(fake_display):
    window.open_window(True)
    assert fake_display[-1][1] & pygame.RESIZABLE  # иначе после F11 окно было бы не растянуть


def test_without_scaling_support_a_plain_window_opens(monkeypatch):
    modes = []

    def set_mode(size, flags=0):
        modes.append(flags)
        if flags & pygame.SCALED:
            raise pygame.error("no fast renderer available")
        return "screen"

    monkeypatch.setattr(pygame.display, "set_mode", set_mode)
    assert window.open_window(True) == "screen"  # программа запускается, а не падает
    assert modes[-1] == 0
    assert not window.is_fullscreen()


def test_toggle_follows_real_window_state(fake_display, monkeypatch):
    window.open_window(False)
    monkeypatch.setattr(pygame.display, "toggle_fullscreen", lambda: 0)  # pygame 2.6: 0 и при успехе
    monkeypatch.setattr(pygame.display, "get_surface", lambda: SimpleNamespace(get_flags=lambda: pygame.FULLSCREEN))
    window.toggle_fullscreen()
    assert window.is_fullscreen()


def test_toggle_that_is_not_supported_changes_nothing(fake_display, monkeypatch):
    window.open_window(False)

    def unsupported():
        raise pygame.error("That operation is not supported")

    monkeypatch.setattr(pygame.display, "toggle_fullscreen", unsupported)
    window.toggle_fullscreen()  # не падаем
    assert not window.is_fullscreen()
