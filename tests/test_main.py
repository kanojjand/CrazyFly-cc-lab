import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pytest  # noqa: E402

import main  # noqa: E402
from cfpilot.app import ExitReason  # noqa: E402
from cfpilot.config import AppConfig, ControllerProfile, FlightLimits, JoystickSettings, load_config  # noqa: E402
from cfpilot.flight import FlightMode  # noqa: E402
from cfpilot.link import DeckInfo  # noqa: E402


def test_shipped_config_matches_defaults():
    # Свои у каждого: трим — у дрона (программа сама пишет его в config.json), раскладка — у пульта.
    # И адрес — у дрона: найденный на другом канале программа тоже пишет в config.json.
    shipped = load_config(str(main.ROOT / "config.json"))
    flight = replace(shipped.flight, trim_roll=0.0, trim_pitch=0.0)
    assert replace(shipped, uri=AppConfig().uri, flight=flight, joystick=JoystickSettings()) == AppConfig()


def test_changed_trim_is_saved(tmp_path, capsys):
    path = str(tmp_path / "config.json")
    main.keep_trim(path, FlightLimits(), FlightLimits(trim_roll=1.5))
    assert load_config(path).flight.trim_roll == 1.5
    assert "+1.5" in capsys.readouterr().out


def test_unchanged_trim_does_not_touch_config(tmp_path):
    path = tmp_path / "config.json"
    main.keep_trim(str(path), FlightLimits(), FlightLimits())
    assert not path.exists()


def test_language_and_fullscreen_from_command_line():
    config = main.apply_overrides(AppConfig(), main.parse_args(["--lang", "en", "--fullscreen"]))
    assert (config.language, config.fullscreen) == ("en", True)


def test_switched_language_and_fullscreen_are_remembered(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    monkeypatch.setattr(main, "language", lambda: "en")  # нажали F2
    monkeypatch.setattr(main, "is_fullscreen", lambda: True)  # нажали F11
    main.keep_preferences(path, AppConfig())
    config = load_config(path)
    assert (config.language, config.fullscreen) == ("en", True)


def test_unchanged_preferences_do_not_touch_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(main, "language", lambda: "ru")
    monkeypatch.setattr(main, "is_fullscreen", lambda: False)
    main.keep_preferences(str(path), AppConfig())
    assert not path.exists()


def test_window_opens_full_screen_when_asked(monkeypatch, tmp_path):
    opened = []
    real_open = main.open_window
    monkeypatch.setattr(main, "open_window", lambda fullscreen: opened.append(fullscreen) or real_open(False))
    monkeypatch.setattr(main, "connect_drone", lambda *args: None)
    main.fly(replace(AppConfig(), fullscreen=True), use_sim=False, config_path=str(tmp_path / "config.json"))
    assert opened == [True]


FOUND = "radio://0/85/2M/E7E7E7E7E7"


def test_drone_found_on_other_channel_is_remembered(tmp_path):
    path = str(tmp_path / "config.json")
    config = main.remember_uri(path, AppConfig(), FOUND)
    assert config.uri == FOUND
    assert load_config(path).uri == FOUND  # в следующий раз подключимся сразу


def test_same_address_does_not_touch_config(tmp_path):
    path = tmp_path / "config.json"
    assert main.remember_uri(str(path), AppConfig(), AppConfig().uri) == AppConfig()
    assert not path.exists()


def test_address_save_failure_is_reported_but_drone_is_used(tmp_path, capsys):
    folder = tmp_path / "config.json"
    folder.mkdir()
    assert main.remember_uri(str(folder), AppConfig(), FOUND).uri == FOUND
    assert FOUND in capsys.readouterr().err


class FakeLobby:
    def __init__(self, dashboard, config, connector, **options):
        self.config = config

    def run(self):
        return SimpleNamespace(uri=FOUND), DeckInfo()


@pytest.mark.parametrize("uri, searches", [("radio://0/80/2M/E7E7E7E7E7", True), ("usb://0", False)])
def test_connection_screen_searches_all_channels_for_radio(monkeypatch, tmp_path, uri, searches):
    made = {}
    monkeypatch.setattr(main, "Connector", lambda uri, make_link, **options: made.update(uri=uri, **options))
    monkeypatch.setattr(main, "Lobby", FakeLobby)
    path = str(tmp_path / "config.json")
    _, _, config = main.connect_drone(AppConfig(uri=uri), None, path, None, "", False)
    assert made["uri"] == uri
    assert (made["scan"] is main.scan_channels) is searches
    assert config.uri == load_config(path).uri == FOUND


def test_trim_save_failure_is_reported(tmp_path, capsys):
    folder = tmp_path / "config.json"
    folder.mkdir()
    main.keep_trim(str(folder), FlightLimits(), FlightLimits(trim_pitch=-1.0))
    assert "trim_pitch" in capsys.readouterr().err


def test_cli_overrides_config():
    args = main.parse_args(["--uri", "radio://0/90/2M/E7E7E7E701", "--mode", "manual", "--input", "keyboard"])
    config = main.apply_overrides(AppConfig(), args)
    assert (config.uri, config.mode, config.input) == ("radio://0/90/2M/E7E7E7E701", "manual", "keyboard")


def test_no_cli_flags_keep_config():
    assert main.apply_overrides(AppConfig(), main.parse_args([])) == AppConfig()


def test_choose_mode():
    assert main.choose_mode("auto", DeckInfo(flow=True)) is FlightMode.HOVER
    assert main.choose_mode("manual", DeckInfo(flow=True)) is FlightMode.MANUAL


def test_forced_mode_needs_matching_decks():
    assert main.mode_problem(FlightMode.HOVER, DeckInfo()) != ""
    assert main.mode_problem(FlightMode.ALTHOLD, DeckInfo()) != ""
    assert main.mode_problem(FlightMode.ALTHOLD, DeckInfo(zranger=True)) == ""
    assert main.mode_problem(FlightMode.HOVER, DeckInfo(lighthouse=True)) == ""
    assert main.mode_problem(FlightMode.MANUAL, DeckInfo()) == ""


def test_source_run_keeps_settings_next_to_main(monkeypatch):
    monkeypatch.setattr(main, "FROZEN", False)
    assert main.data_dir() == main.ROOT


def test_settings_live_in_appdata_on_windows_and_application_support_on_mac(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert main.settings_root("win32") == tmp_path
    assert main.settings_root("darwin") == Path.home() / "Library" / "Application Support"


def test_installed_app_keeps_settings_in_appdata(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "FROZEN", True)
    monkeypatch.setattr(main, "settings_root", lambda: tmp_path)
    folder = main.data_dir()
    assert folder == tmp_path / main.APP_FOLDER
    assert load_config(str(folder / "config.json")) == load_config(str(main.BUNDLE / "config.json"))
    (folder / "config.json").write_text('{"mode": "manual"}', encoding="utf-8")  # пользователь что-то поменял
    main.data_dir()
    assert load_config(str(folder / "config.json")).mode == "manual"  # второй запуск настройки не затирает


def test_fatal_error_is_shown_in_a_window_when_installed(monkeypatch):
    shown = []
    monkeypatch.setattr(main, "_message_box", shown.append)
    monkeypatch.setattr(main, "FROZEN", True)
    main.report_fatal("Ошибка в настройках")
    monkeypatch.setattr(main, "FROZEN", False)
    main.report_fatal("из консоли окно не нужно")
    assert shown == ["Ошибка в настройках"]


def test_crash_log_falls_back_to_temp_when_appdata_is_unwritable(tmp_path, monkeypatch):
    def unwritable():
        raise PermissionError("read-only profile")

    monkeypatch.setattr(main, "data_dir", unwritable)
    monkeypatch.setattr(main.tempfile, "gettempdir", lambda: str(tmp_path))
    log = main.save_crash_log("Traceback ...")
    assert log.parent == tmp_path
    assert log.read_text(encoding="utf-8") == "Traceback ..."


def test_bad_config_exits_with_code_2(tmp_path, capsys):
    bad = tmp_path / "config.json"
    bad.write_text('{"mode": "unknown"}', encoding="utf-8")
    assert main.main(["--config", str(bad)]) == 2
    err = capsys.readouterr().err
    assert "mode" in err
    assert "удали" in err  # подсказка, как вернуть настройки по умолчанию


def test_quit_ends_program_after_session():
    assert main.after_session(ExitReason.QUIT, None) is None


def test_lost_link_reconnects_and_says_why():
    assert main.after_session(ExitReason.LINK_LOST, "Связь с дроном потеряна: Too many packets lost") == (
        False, "Связь с дроном потеряна: Too many packets lost")
    assert main.after_session(ExitReason.LINK_LOST, None) == (False, "Связь с дроном потеряна")


def test_reboot_reconnects_after_power_cycle():
    assert main.after_session(ExitReason.REBOOT, None) == (True, "")


def test_profile_saver_writes_config(tmp_path):
    path = str(tmp_path / "config.json")
    save = main.profile_saver(path)
    assert save("Pad", ControllerProfile()) == ""
    assert load_config(path).joystick.profile("Pad") == ControllerProfile()


def test_profile_saver_returns_error_text(tmp_path):
    folder = tmp_path / "config.json"
    folder.mkdir()
    assert "калибровку" in main.profile_saver(str(folder))("Pad", ControllerProfile())


def test_driver_install_restarts_program_elevated(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "run_elevated", lambda program, parameters: calls.append((program, parameters)) or 0)
    monkeypatch.setattr(main, "FROZEN", True)
    assert main.install_driver_elevated() == 0
    monkeypatch.setattr(main, "FROZEN", False)
    main.install_driver_elevated()
    assert calls[0][1] == "--install-driver"  # .exe запускает сам себя
    assert calls[1][1].endswith('main.py" --install-driver')  # из исходников — через python main.py


def test_install_driver_flag_only_installs(monkeypatch):
    folders = []
    monkeypatch.setattr(main, "install_driver", lambda folder: folders.append(folder) or 0)
    assert main.main(["--install-driver"]) == 0
    assert folders == [main.DRIVER_DIR]


def test_driver_package_is_shipped_with_sources():
    assert (main.DRIVER_DIR / "crazyradio_2.0.inf").exists()
    assert (main.DRIVER_DIR / "crazyradio-driver.cer").exists()


# ---------- цикл сеансов: подключение -> полёт -> переподключение ----------

from cfpilot.sim import SimLink  # noqa: E402


class LostLink(SimLink):
    """Симулятор, у которого можно «оборвать» связь."""

    def __init__(self, error=None):
        super().__init__(FlightMode.HOVER)
        self.lost = error
        self.closed = False

    @property
    def error(self):
        return self.lost

    def close(self):
        self.closed = True


def script_sessions(monkeypatch, reasons):
    """App.run сразу завершает сеанс с заданной причиной — по одной на сеанс."""
    feed = iter(reasons)

    def run(app):
        app.exit_reason = next(feed)
        app.running = False

    monkeypatch.setattr(main.App, "run", run)


def test_simulator_session_quits_cleanly(monkeypatch, tmp_path):
    script_sessions(monkeypatch, [ExitReason.QUIT])
    assert main.fly(AppConfig(), use_sim=True, config_path=str(tmp_path / "config.json")) == 0


def test_lost_link_goes_back_to_connection_screen(monkeypatch, tmp_path):
    links = [LostLink("Связь с дроном потеряна: Too many packets lost"), LostLink()]
    calls = []

    def connect_drone(config, dashboard, config_path, save_profile, reason, reboot):
        calls.append((reason, reboot))
        return links[len(calls) - 1], DeckInfo(flow=True), config

    monkeypatch.setattr(main, "connect_drone", connect_drone)
    script_sessions(monkeypatch, [ExitReason.LINK_LOST, ExitReason.QUIT])
    assert main.fly(AppConfig(), use_sim=False, config_path=str(tmp_path / "config.json")) == 0
    assert calls == [("", False), ("Связь с дроном потеряна: Too many packets lost", False)]
    assert all(link.closed for link in links)


def test_r_reboots_drone_on_next_connection(monkeypatch, tmp_path):
    calls = []

    def connect_drone(config, dashboard, config_path, save_profile, reason, reboot):
        calls.append(reboot)
        return LostLink(), DeckInfo(flow=True), config

    monkeypatch.setattr(main, "connect_drone", connect_drone)
    script_sessions(monkeypatch, [ExitReason.REBOOT, ExitReason.QUIT])
    main.fly(AppConfig(), use_sim=False, config_path=str(tmp_path / "config.json"))
    assert calls == [False, True]


def test_closing_connection_screen_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "connect_drone", lambda *args: None)
    assert main.fly(AppConfig(), use_sim=False, config_path=str(tmp_path / "config.json")) == 0


def test_forced_mode_without_decks_is_explained(monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(main, "connect_drone", lambda config, *args: (LostLink(), DeckInfo(), config))
    monkeypatch.setattr(main, "show_error", lambda dashboard, title, error, hints: shown.append(title))
    config = replace(AppConfig(), mode="hover")
    assert main.fly(config, use_sim=False, config_path=str(tmp_path / "config.json")) == 1
    assert shown == ["Неподходящий режим"]


def test_fatal_error_on_mac_uses_system_alert(monkeypatch):
    calls = []
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main.subprocess, "run", lambda args, check: calls.append(args))
    main._message_box('Нет "радио"\nвставь')
    assert calls[0][:2] == ["osascript", "-e"]
    assert r'message "Нет \"радио\"\nвставь" as critical' in calls[0][2]
