"""CC Lab · Crazyflie Pilot — управление одним Crazyflie с клавиатуры или пульта через Crazyradio.

    python main.py              найти дрон (на всех каналах) и подключиться; ждать его сколько нужно
    python main.py --sim        симулятор: проверить управление без дрона
    python main.py --scan       найти дроны на всех каналах и выйти
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import warnings
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
# Пульт должен работать, даже если окно программы не в фокусе.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
# Во весь экран картинка растягивается — пусть плавно, а не «лесенкой».
os.environ.setdefault("SDL_RENDER_SCALE_QUALITY", "linear")

import pygame  # noqa: E402

from cfpilot.app import App, ExitReason, SaveProfile, poll_events  # noqa: E402
from cfpilot.channels import distinct_drones, parse_radio_uri, scan_channels  # noqa: E402
from cfpilot.config import (  # noqa: E402
    DEFAULT_URI,
    INPUTS,
    MODES,
    AppConfig,
    ConfigError,
    ControllerProfile,
    FlightLimits,
    load_config,
    save_controller_profile,
    save_preferences,
    save_trim,
    save_uri,
)
from cfpilot.connection import Connector  # noqa: E402
from cfpilot.driver import DriverJob, install_driver, run_elevated  # noqa: E402
from cfpilot.flight import FlightMode  # noqa: E402
from cfpilot.i18n import LANGUAGES, N_, language, set_language, t  # noqa: E402
from cfpilot.link import CrazyflieLink, DeckInfo, radio_status, reboot_drone  # noqa: E402
from cfpilot.lobby import Lobby  # noqa: E402
from cfpilot.sim import SimLink  # noqa: E402
from cfpilot.theme import DANGER, icon_surface  # noqa: E402
from cfpilot.ui import Dashboard  # noqa: E402
from cfpilot.window import is_fullscreen, open_window  # noqa: E402

ROOT = Path(__file__).resolve().parent
BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))  # файлы, упакованные в .exe (PyInstaller)
FROZEN = getattr(sys, "frozen", False)  # запущены как .exe, а не из исходников
DRIVER_DIR = BUNDLE / "driver" if FROZEN else ROOT / "installer" / "driver"
APP_FOLDER = "Crazyflie Pilot"
WINDOW_TITLE = "CC Lab · Crazyflie Pilot"
WINDOW_ICON_SIZE = 64
NOTICE_FPS = 20
MB_ICONERROR = 0x10
DRIVER_HINT = N_(
    "Crazyradio не виден программе. Почти всегда это драйвер: запусти программу без --scan —\n"
    "она поставит драйвер сама (Windows спросит права администратора). Или вручную: Zadig,\n"
    "Options -> List All Devices, Crazyradio (USB ID 1915 7777), libusb-win32, Install/Replace Driver."
)
MODE_HINTS = (
    N_("hover — нужен Flow deck, Lighthouse или Loco; althold — ещё подойдёт Z-ranger."),
    N_("Убери --mode / поставь \"mode\": \"auto\" — программа выберет режим сама."),
    "",
    N_("Любая клавиша — выход"),
)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Управление Crazyflie с клавиатуры или пульта")
    parser.add_argument("--config", help="файл настроек (по умолчанию — config.json, см. data_dir)")
    parser.add_argument("--uri", help="адрес дрона, например radio://0/80/2M/E7E7E7E7E7")
    parser.add_argument("--mode", choices=MODES, help="режим полёта (auto — выбрать по декам)")
    parser.add_argument("--input", choices=INPUTS, help="чем управлять (auto — пульт, если подключён)")
    parser.add_argument("--sim", action="store_true", help="симулятор вместо настоящего дрона")
    parser.add_argument("--scan", action="store_true", help="найти дроны на всех каналах и выйти")
    parser.add_argument("--lang", choices=LANGUAGES, help="язык окна (в программе — F2)")
    parser.add_argument("--fullscreen", action="store_true", help="во весь экран (в программе — F11)")
    # Программа сама перезапускает себя с этим флагом и правами администратора, чтобы поставить драйвер.
    parser.add_argument("--install-driver", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def settings_root(platform: str = sys.platform) -> Path:
    """Папка для настроек программ: Windows — %APPDATA%, macOS — ~/Library/Application Support."""
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("APPDATA", str(Path.home())))


def data_dir() -> Path:
    """Где config.json и кэш: из исходников — рядом с main.py, у .exe / .app — в папке настроек пользователя
    (рядом с программой писать можно не всегда, а трим и раскладки пультов сохраняются в config.json)."""
    if not FROZEN:
        return ROOT
    folder = settings_root() / APP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    config = folder / "config.json"
    if not config.exists():
        shutil.copyfile(BUNDLE / "config.json", config)  # настройки по умолчанию из .exe
    return folder


def _message_box(message: str) -> None:
    if sys.platform == "darwin":
        # Строки AppleScript понимают те же \" и \n, что JSON; кириллицу оставляем как есть.
        title, text = json.dumps(WINDOW_TITLE, ensure_ascii=False), json.dumps(message, ensure_ascii=False)
        script = f"display alert {title} message {text} as critical"
        subprocess.run(["osascript", "-e", script], check=False)
        return
    if sys.platform != "win32":
        return  # остальным системам хватает текста в консоли
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, MB_ICONERROR)


def report_fatal(message: str) -> None:
    """Ошибка, после которой программа закрывается. У .exe нет консоли — показываем окном."""
    print(message, file=sys.stderr)
    if FROZEN:
        _message_box(message)


def save_crash_log(text: str) -> Path:
    """error.log рядом с настройками, а если туда не пишется (папка профиля только для чтения) — во временную."""
    try:
        log = data_dir() / "error.log"
        log.write_text(text, encoding="utf-8")
    except OSError:
        log = Path(tempfile.gettempdir()) / "crazyflie-pilot-error.log"
        log.write_text(text, encoding="utf-8")
    return log


def apply_overrides(config: AppConfig, args: argparse.Namespace) -> AppConfig:
    given = (("uri", args.uri), ("mode", args.mode), ("input", args.input), ("language", args.lang),
             ("fullscreen", args.fullscreen))
    return replace(config, **{name: value for name, value in given if value})


def choose_mode(config_mode: str, decks: DeckInfo) -> FlightMode:
    return decks.best_mode() if config_mode == "auto" else FlightMode(config_mode)


def mode_problem(mode: FlightMode, decks: DeckInfo) -> str:
    """Принудительный режим без нужных дек = полёт вслепую. Пустая строка — всё в порядке."""
    if decks.supports(mode):
        return ""
    return t("Режиму {mode} нужны деки, которых нет на дроне (найдено: {decks})", mode=mode.value,
             decks=decks.describe())


def run_scan(uri: str) -> int:
    """Дроны с адресом из настроек на всех каналах; адрес не радио (usb://…) — ищем с адресом по умолчанию."""
    status = radio_status()
    print("Радиоинтерфейсы:")
    print(status)
    if "Crazyradio not found" in status:
        print(f"\n{t(DRIVER_HINT)}")
        return 1
    target = parse_radio_uri(uri) or parse_radio_uri(DEFAULT_URI)
    drones = distinct_drones(scan_channels(str(target)), target)
    if not drones:
        print(f"\nНи на одном канале нет дрона с адресом {target.address:010X}. Дрон включён? "
              "Адрес другой — укажи его: --uri radio://0/80/2M/<адрес>")
        return 1
    print("\nНайдены дроны (программа подключится сама, а из нескольких даст выбрать):")
    for drone in drones:
        print(f"  {drone.uri}   {drone.describe()}")
    return 0


def install_driver_elevated() -> int:
    """Запускает эту же программу с правами администратора: она ставит драйвер и выходит."""
    if FROZEN:
        return run_elevated(sys.executable, "--install-driver")
    return run_elevated(sys.executable, f'"{ROOT / "main.py"}" --install-driver')


def profile_saver(config_path: str) -> SaveProfile:
    def save(device: str, profile: ControllerProfile) -> str:
        try:
            save_controller_profile(config_path, device, profile)
        except ConfigError as error:
            return str(error)
        return ""

    return save


def show_error(dashboard: Dashboard, title: str, error: str, hints: tuple[str, ...]) -> None:
    dashboard.draw_notice(title, (error.splitlines()[0],) + ("",) + hints, DANGER)
    clock = pygame.time.Clock()
    while not any(event.type in (pygame.QUIT, pygame.KEYDOWN) for event in poll_events()):
        clock.tick(NOTICE_FPS)


def keep_trim(path: str, before: FlightLimits, after: FlightLimits) -> None:
    """Трим, подстроенный в полёте, пишем в config.json — в следующий раз он уже будет."""
    roll, pitch = after.trim_roll, after.trim_pitch
    if (roll, pitch) == (before.trim_roll, before.trim_pitch):
        return
    try:
        save_trim(path, roll, pitch)
    except ConfigError as error:
        print(f"{error}\nВпиши вручную в config.json -> flight: \"trim_roll\": {roll}, \"trim_pitch\": {pitch}",
              file=sys.stderr)
        return
    print(f"Трим сохранён в {Path(path).name}: вправо {roll:+.1f}°, вперёд {pitch:+.1f}°")


def keep_preferences(path: str, before: AppConfig) -> None:
    """Язык (F2) и весь экран (F11), переключённые в программе, пишем в config.json — так она и откроется."""
    now = (language(), is_fullscreen())
    if now == (before.language, before.fullscreen):
        return
    try:
        save_preferences(path, *now)
    except ConfigError as error:
        print(error, file=sys.stderr)


def after_session(reason: ExitReason, link_error: str | None) -> tuple[bool, str] | None:
    """Что делать после сеанса полёта: None — выйти; иначе (перезагрузить ли дрон, что сказать при подключении)."""
    if reason is ExitReason.QUIT:
        return None
    if reason is ExitReason.REBOOT:
        return True, ""
    return False, link_error or t("Связь с дроном потеряна")


def remember_uri(config_path: str, config: AppConfig, uri: str) -> AppConfig:
    """Дрон нашёлся на другом канале — пишем его адрес в config.json: в следующий раз подключимся сразу."""
    if uri == config.uri:
        return config
    try:
        save_uri(config_path, uri)
    except ConfigError as error:
        print(f"{error}\nВпиши вручную в config.json -> \"uri\": \"{uri}\"", file=sys.stderr)
    return replace(config, uri=uri)


def connect_drone(config: AppConfig, dashboard: Dashboard, config_path: str, save_profile: SaveProfile,
                  reason: str, reboot: bool):
    """Экран подключения: ищет дрон на всех каналах, сколько нужно. (связь, деки, настройки) или None — окно закрыли."""
    cache_dir = str(Path(config_path).parent / "cache")
    connector = Connector(config.uri, lambda uri: CrazyflieLink(uri, config.flight, cache_dir=cache_dir),
                          scan=scan_channels if parse_radio_uri(config.uri) is not None else None,
                          reboot=reboot_drone, reboot_first=reboot)
    lobby = Lobby(dashboard, config, connector, driver=DriverJob(install_driver_elevated),
                  save_profile=save_profile, reason=reason)
    connected = lobby.run()
    if connected is None:
        return None
    link, decks = connected
    return link, decks, remember_uri(config_path, lobby.config, link.uri)


def fly(config: AppConfig, use_sim: bool, config_path: str) -> int:
    pygame.init()
    pygame.display.set_caption(WINDOW_TITLE)
    pygame.display.set_icon(icon_surface(WINDOW_ICON_SIZE))
    screen = open_window(config.fullscreen)
    dashboard = Dashboard(screen)
    settings_before = config
    trim_before = config.flight
    save_profile = profile_saver(config_path)
    reason, reboot = "", False
    try:
        while True:  # сеанс за сеансом: связь пропала или дрон перезагрузили — подключаемся снова
            if use_sim:
                link = SimLink(FlightMode.HOVER if config.mode == "auto" else FlightMode(config.mode))
                decks = link.connect()
            else:
                connected = connect_drone(config, dashboard, config_path, save_profile, reason, reboot)
                if connected is None:
                    return 0
                link, decks, config = connected
            try:
                mode = choose_mode(config.mode, decks)
                problem = mode_problem(mode, decks)
                if problem:
                    print(problem, file=sys.stderr)
                    show_error(dashboard, t("Неподходящий режим"), problem, tuple(t(hint) for hint in MODE_HINTS))
                    return 1
                app = App(config, link, mode, decks, config.input, screen, save_profile=save_profile)
                try:
                    app.run()
                finally:
                    app.shutdown()  # если вышли посреди полёта — сначала сажаем
                    config = app.config
            finally:
                link_error = link.error
                link.close()
            next_step = after_session(app.exit_reason, link_error)
            if next_step is None:
                return 0
            reboot, reason = next_step
    finally:
        if not use_sim:  # в симуляторе сноса нет — его трим настоящему дрону не подходит
            keep_trim(config_path, trim_before, config.flight)
        keep_preferences(config_path, settings_before)
        pygame.quit()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    warnings.filterwarnings("ignore", category=DeprecationWarning)  # шум из cflib, к нам не относится
    if args.install_driver:
        return install_driver(DRIVER_DIR)
    config_path = args.config or str(data_dir() / "config.json")
    if args.lang:
        set_language(args.lang)  # ошибку в самих настройках — уже на нужном языке
    try:
        config = apply_overrides(load_config(config_path), args)
        set_language(config.language)
    except ConfigError as error:
        report_fatal(t("Ошибка в настройках ({path}): {error}\n"
                       "Исправь файл или удали его — тогда будут настройки по умолчанию.", path=config_path, error=error))
        return 2
    if args.scan:
        return run_scan(config.uri)
    try:
        return fly(config, use_sim=args.sim, config_path=config_path)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        if not FROZEN:
            raise
        # Без консоли трассировка пропала бы — сохраняем её и говорим где.
        log = save_crash_log(traceback.format_exc())
        report_fatal(t("Программа упала с ошибкой. Подробности в файле:\n{log}", log=log))
        sys.exit(1)
