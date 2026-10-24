"""Экран подключения: ищет дрон на всех каналах, пока не найдёт (несколько — выбрать цифрой). Тем временем
можно откалибровать пульт, перезагрузить дрон (R) или поставить драйвер Crazyradio (ставится сам, D — ещё раз)."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable, Sequence

import pygame

from cfpilot.app import (
    CALIBRATE_KEYS,
    LOST_REMOVAL,
    QUIT_KEYS,
    REBOOT_KEYS,
    SaveProfile,
    all_joysticks,
    calibration_key,
    poll_events,
    unplugged,
)
from cfpilot.calibration import Calibrator, Joystick
from cfpilot.config import AppConfig
from cfpilot.connection import Connector, Link, Stage, Status
from cfpilot.driver import DriverJob
from cfpilot.i18n import t
from cfpilot.link import DeckInfo
from cfpilot.screens import MAX_CHOICES, ConnectView
from cfpilot.ui import Dashboard

LOBBY_FPS = 30
DRIVER_KEYS = frozenset({pygame.KSCAN_D})
CHOICE_KEYS = {  # дрон из списка — цифрой сверху или на цифровом блоке
    **{getattr(pygame, f"KSCAN_{digit}"): digit - 1 for digit in range(1, MAX_CHOICES + 1)},
    **{getattr(pygame, f"KSCAN_KP_{digit}"): digit - 1 for digit in range(1, MAX_CHOICES + 1)},
}


class Lobby:
    def __init__(self, dashboard: Dashboard, config: AppConfig, connector: Connector, *,
                 driver: DriverJob | None = None, save_profile: SaveProfile | None = None,
                 joysticks: Callable[[], Sequence[Joystick]] = all_joysticks, reason: str = "",
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config  # с раскладками пультов, откалиброванных здесь
        self.quit = False
        self._dashboard = dashboard
        self._connector = connector
        self._driver = driver
        self._save_profile = save_profile
        self._joysticks = joysticks
        self._reason = reason
        self._clock = clock
        self._started = clock()
        self._calibrator: Calibrator | None = None
        self._message = ""
        self._controllers: tuple[str, ...] | None = None  # имена пультов; None — перечитать

    def run(self) -> tuple[Link, DeckInfo] | None:
        """Подключённый дрон или None, если окно закрыли."""
        self._connector.start()
        clock = pygame.time.Clock()
        try:
            while True:
                dt = clock.tick(LOBBY_FPS) / 1000.0
                for event in poll_events():
                    self.handle_event(event)
                result = self.tick(dt)
                if self.quit:
                    return None
                if result is not None:
                    return result
        finally:
            # Забранную связь cancel() не трогает; остальное (фоновые попытки, неотданная связь) — закрывает.
            self._connector.cancel()

    # ---------- события ----------

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.quit = True
        elif event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            self._controllers = None
            calibrator = self._calibrator
            if event.type != pygame.JOYDEVICEREMOVED or calibrator is None:
                return
            if event.instance_id != LOST_REMOVAL:
                calibrator.controller_removed(event.instance_id)
            elif unplugged(calibrator.joystick, self._joysticks):
                calibrator.controller_lost()
        elif event.type == pygame.KEYDOWN:
            self._on_key_down(event.scancode)

    def _on_key_down(self, scancode: int) -> None:
        if self._calibrator is not None:
            calibration_key(self._calibrator, scancode)
        elif scancode in QUIT_KEYS:
            self.quit = True
        elif scancode in CALIBRATE_KEYS:
            self._calibrator = Calibrator(self._joysticks)
            self._message = ""
        elif scancode in REBOOT_KEYS:
            self._connector.request_reboot()
        elif scancode in DRIVER_KEYS and self._driver is not None:
            self._driver.start()
        elif scancode in CHOICE_KEYS:
            self._choose(CHOICE_KEYS[scancode])

    def _choose(self, index: int) -> None:
        status = self._connector.status
        if status.stage is Stage.CHOOSING and index < min(len(status.drones), MAX_CHOICES):
            self._connector.choose(str(status.drones[index].uri))

    # ---------- кадр ----------

    def tick(self, dt: float) -> tuple[Link, DeckInfo] | None:
        status = self._connector.status
        driver = self._driver
        if driver is not None and status.stage is Stage.NO_DRIVER and driver.attempts == 0:
            driver.start()  # один раз сами; если отказались — по клавише D
        if self._calibrator is not None:
            self._update_calibration(dt)
        if self._calibrator is not None:
            self._dashboard.draw_calibration(self._calibrator.view())
            return None  # дрон подождёт, пока закончат калибровку
        self._dashboard.draw_connect(self.view(status))
        if self.quit or not self._connector.connected:
            return None  # окно закрывают — связь не забираем, её закроет cancel() в run()
        return self._connector.take()

    def _update_calibration(self, dt: float) -> None:
        calibrator = self._calibrator
        calibrator.update(dt)
        if not (calibrator.finished or calibrator.cancelled):
            return
        self._calibrator = None
        self._controllers = None
        result = calibrator.result
        if result is None:
            self._message = t("Калибровка отменена")
            return
        device, profile = result
        self.config = replace(self.config, joystick=self.config.joystick.with_profile(device, profile))
        error = self._save_profile(device, profile) if self._save_profile is not None else ""
        if error:
            self._message = t("Пульт «{device}» настроен, но не сохранён: {error}", device=device, error=error)
        else:
            self._message = t("Пульт «{device}» настроен и сохранён", device=device)

    def _controller_text(self) -> str:
        if self._controllers is None:
            self._controllers = tuple(joystick.get_name() for joystick in self._joysticks())
        if not self._controllers:
            return ""
        known = [name for name in self._controllers if self.config.joystick.profile(name) is not None]
        if known:
            return t("{name} — свой профиль", name=known[0])
        return t("{name} — общая раскладка (C — настроить)", name=self._controllers[0])

    def view(self, status: Status) -> ConnectView:
        return ConnectView(
            uri=status.uri or self.config.uri,
            status=status,
            reason=self._reason,
            driver=self._driver.describe() if self._driver is not None else "",
            message=self._message,
            controller=self._controller_text(),
            can_reboot=self._connector.can_reboot,
            elapsed=self._clock() - self._started,
        )
