import json
from pathlib import Path

import pytest

from cfpilot.config import (
    AppConfig,
    AxisMap,
    AxisRange,
    Binding,
    Bindings,
    ConfigError,
    ControllerProfile,
    InvertMap,
    JoystickSettings,
    load_config,
    save_controller_profile,
    save_preferences,
    save_trim,
    save_uri,
)

PAD = "DualSense Wireless Controller"
PROFILE = ControllerProfile(
    axes=AxisMap(roll=2, pitch=3, throttle=1, yaw=None),
    invert=InvertMap(pitch=True, throttle=True),
    calibration=(AxisRange(axis=1, low=-1.0, center=0.02, high=0.98), AxisRange(axis=2, low=-0.9, center=0.0, high=1.0)),
    bindings=Bindings(takeoff=Binding(button=0), kill=Binding(axis=4, above=0.5)),
)


def write(tmp_path, data) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_missing_file_gives_defaults(tmp_path):
    config = load_config(str(tmp_path / "nope.json"))
    assert config == AppConfig()


def test_defaults_are_sane():
    config = AppConfig()
    assert config.uri.startswith("radio://")
    assert config.flight.min_height <= config.flight.takeoff_height <= config.flight.max_height
    assert config.joystick.axes.throttle == 2


def test_partial_override_keeps_other_defaults(tmp_path):
    path = write(tmp_path, {"uri": "radio://0/90/2M/E7E7E7E701", "flight": {"max_speed": 0.3}})
    config = load_config(path)
    assert config.uri == "radio://0/90/2M/E7E7E7E701"
    assert config.flight.max_speed == 0.3
    assert config.flight.takeoff_height == AppConfig().flight.takeoff_height


def test_joystick_section_is_parsed(tmp_path):
    path = write(
        tmp_path,
        {
            "joystick": {
                "name_filter": "radiomaster",
                "axes": {"yaw": 0, "roll": 3},
                "invert": {"pitch": True},
                "bindings": {"kill": {"axis": 4, "above": 0.5}, "takeoff": {"button": 0}},
            }
        },
    )
    joystick = load_config(path).joystick
    assert joystick.name_filter == "radiomaster"
    assert joystick.axes.yaw == 0
    assert joystick.axes.roll == 3
    assert joystick.axes.pitch == 1
    assert joystick.invert.pitch is True
    assert joystick.bindings.kill == Binding(axis=4, above=0.5)
    assert joystick.bindings.takeoff == Binding(button=0)
    assert joystick.bindings.land is None


def test_unknown_key_is_reported(tmp_path):
    path = write(tmp_path, {"flight": {"max_sped": 1.0}})
    with pytest.raises(ConfigError, match="max_sped"):
        load_config(path)


def test_bad_json_is_reported(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))


@pytest.mark.parametrize(
    "flight",
    [
        {"min_height": 1.0, "takeoff_height": 0.5},
        {"max_height": 0.1},
        {"max_speed": -1},
        {"thrust_max": 70000},
        {"thrust_min": 60000, "thrust_max": 50000},
        {"max_speed": "fast"},
        {"trim_roll": 15.0},
        {"trim_pitch": -10.5},
    ],
)
def test_invalid_flight_limits_are_rejected(tmp_path, flight):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"flight": flight}))


@pytest.mark.parametrize(
    "flight",
    [
        {"takeoff_speed": float("nan")},
        {"max_speed": float("inf")},
        {"max_angle": 150},
        {"land_speed": 3.0},
        {"max_speed": True},
        {"thrust_max": 50000.5},
    ],
)
def test_nonsense_numbers_are_rejected(tmp_path, flight):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"flight": flight}))


@pytest.mark.parametrize(
    "joystick",
    [
        {"axes": {"throttle": "2"}},
        {"axes": {"roll": 4.0}},
        {"axes": {"yaw": -1}},
        {"axes": {"throttle": None}},  # без оси газа в manual был бы вечный средний газ
        {"invert": {"pitch": 1}},
        {"name_filter": 5},
        {"bindings": {"kill": {"button": "0"}}},
        {"bindings": {"kill": {"button": -1}}},
        {"bindings": {"kill": {"axis": 4, "above": 1.5}}},
    ],
)
def test_wrong_joystick_types_are_rejected(tmp_path, joystick):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"joystick": joystick}))


def test_gamepad_layout_with_hold_and_shoulder_yaw_is_parsed(tmp_path):
    bindings = {"hold": {"button": 1}, "yaw_left": {"button": 9}, "yaw_right": {"button": 10}}
    path = write(tmp_path, {"joystick": {"axes": {"yaw": None}, "bindings": bindings}})
    joystick = load_config(path).joystick
    assert joystick.axes.yaw is None
    assert (joystick.bindings.hold, joystick.bindings.yaw_left, joystick.bindings.yaw_right) == (
        Binding(button=1), Binding(button=9), Binding(button=10))


def test_unreadable_file_is_reported(tmp_path):
    folder = tmp_path / "config.json"
    folder.mkdir()
    with pytest.raises(ConfigError):
        load_config(str(folder))


def test_save_trim_keeps_other_settings(tmp_path):
    path = write(tmp_path, {"uri": "radio://0/90/2M/E7E7E7E701", "flight": {"max_speed": 0.3}})
    save_trim(path, 1.5, -0.5)
    config = load_config(path)
    assert (config.flight.trim_roll, config.flight.trim_pitch) == (1.5, -0.5)
    assert config.uri == "radio://0/90/2M/E7E7E7E701"
    assert config.flight.max_speed == 0.3


def test_config_saved_with_bom_is_read(tmp_path):
    # Некоторые редакторы (и PowerShell 5) пишут UTF-8 с меткой BOM в начале — это не ошибка в настройках.
    path = tmp_path / "config.json"
    path.write_text('{"mode": "manual"}', encoding="utf-8-sig")
    assert load_config(str(path)).mode == "manual"


def test_language_and_fullscreen_settings(tmp_path):
    config = load_config(write(tmp_path, {"language": "en", "fullscreen": True}))
    assert (config.language, config.fullscreen) == ("en", True)
    assert (AppConfig().language, AppConfig().fullscreen) == ("ru", False)


@pytest.mark.parametrize("data, key", [({"language": "de"}, "language"), ({"fullscreen": "yes"}, "fullscreen")])
def test_bad_language_or_fullscreen_is_explained(tmp_path, data, key):
    with pytest.raises(ConfigError, match=key):
        load_config(write(tmp_path, data))


def test_save_preferences_keeps_other_settings(tmp_path):
    path = write(tmp_path, {"uri": "radio://0/90/2M/E7E7E7E701"})
    save_preferences(path, "en", True)
    config = load_config(path)
    assert (config.language, config.fullscreen, config.uri) == ("en", True, "radio://0/90/2M/E7E7E7E701")


def test_save_uri_keeps_other_settings(tmp_path):
    path = write(tmp_path, {"uri": "radio://0/80/2M/E7E7E7E7E7", "flight": {"max_speed": 0.3}})
    save_uri(path, "radio://0/85/2M/E7E7E7E7E7")
    config = load_config(path)
    assert config.uri == "radio://0/85/2M/E7E7E7E7E7"
    assert config.flight.max_speed == 0.3


def test_save_trim_creates_missing_file(tmp_path):
    path = str(tmp_path / "config.json")
    save_trim(path, -1.0, 0.0)
    assert load_config(path).flight.trim_roll == -1.0


def test_save_trim_leaves_no_temp_file_when_replace_fails(tmp_path, monkeypatch):
    path = write(tmp_path, {})

    def locked(self, target):  # так бывает, когда OneDrive держит config.json
        raise PermissionError("file is in use")

    monkeypatch.setattr(Path, "replace", locked)
    with pytest.raises(ConfigError):
        save_trim(path, 1.0, 0.0)
    assert sorted(item.name for item in tmp_path.iterdir()) == ["config.json"]


def test_save_trim_failure_is_reported(tmp_path):
    folder = tmp_path / "config.json"
    folder.mkdir()
    with pytest.raises(ConfigError):
        save_trim(str(folder), 1.0, 0.0)


def test_non_utf8_file_is_reported(tmp_path):
    path = tmp_path / "config.json"
    path.write_bytes(b'{"uri": "\xff\xfe"}')
    with pytest.raises(ConfigError):
        load_config(str(path))


@pytest.mark.parametrize("data", [{"mode": "unknown"}, {"input": "mouse"}, {"joystick": {"deadzone": 1.5}}, {"uri": ""}])
def test_invalid_choices_are_rejected(tmp_path, data):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, data))


@pytest.mark.parametrize(
    "binding",
    [{}, {"button": 1, "axis": 2, "above": 0.5}, {"axis": 2}, {"axis": 1, "above": 0.5, "below": -0.5}],
)
def test_invalid_bindings_are_rejected(tmp_path, binding):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"joystick": {"bindings": {"kill": binding}}}))


def test_controller_profile_round_trips_through_config_json(tmp_path):
    path = write(tmp_path, {"mode": "manual", "joystick": {"deadzone": 0.1}})
    save_controller_profile(path, PAD, PROFILE)
    config = load_config(path)
    assert config.mode == "manual"  # остальное не тронуто
    assert config.joystick.deadzone == 0.1
    assert config.joystick.profile(PAD) == PROFILE


def test_saving_profile_again_replaces_it_and_keeps_others(tmp_path):
    path = write(tmp_path, {})
    save_controller_profile(path, "RadioMaster TX16S Joystick", ControllerProfile())
    save_controller_profile(path, PAD, ControllerProfile())
    save_controller_profile(path, PAD, PROFILE)
    joystick = load_config(path).joystick
    assert [name for name, _ in joystick.profiles] == ["RadioMaster TX16S Joystick", PAD]
    assert joystick.profile(PAD) == PROFILE


def test_controller_with_profile_gets_its_own_layout():
    settings = JoystickSettings(deadzone=0.1).with_profile(PAD, PROFILE)
    mine = settings.for_controller(PAD)
    assert (mine.axes, mine.invert, mine.calibration, mine.bindings) == (
        PROFILE.axes, PROFILE.invert, PROFILE.calibration, PROFILE.bindings)
    assert mine.deadzone == 0.1  # общие настройки остаются
    assert settings.for_controller("Xbox Controller") == settings  # чужой пульт — общая раскладка


def test_with_profile_replaces_profile_of_the_same_controller():
    settings = JoystickSettings().with_profile(PAD, ControllerProfile()).with_profile(PAD, PROFILE)
    assert settings.profiles == ((PAD, PROFILE),)


@pytest.mark.parametrize(
    "calibration",
    [[{"axis": 1, "low": -0.05, "center": 0.0, "high": 0.05}],  # стик не двигали
     [{"axis": 1, "low": -1.0, "center": 1.0, "high": 0.5}],  # центр вне хода
     [{"axis": 1, "low": -1.0, "center": 0.0, "high": 1.0}, {"axis": 1, "low": -1.0, "center": 0.0, "high": 1.0}],
     {"axis": 1}],
)
def test_invalid_calibration_is_rejected(tmp_path, calibration):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, {"joystick": {"profiles": {PAD: {"calibration": calibration}}}}))


def test_typo_inside_profile_is_reported_with_its_name(tmp_path):
    with pytest.raises(ConfigError, match="DualSense"):
        load_config(write(tmp_path, {"joystick": {"profiles": {PAD: {"axis": {}}}}}))


@pytest.mark.parametrize(
    "bindings",
    [{"takeoff": {"button": 0}, "kill": {"button": 0}},  # взлёт и стоп одной кнопкой
     {"takeoff": {"axis": 4, "above": 0.5}, "land": {"axis": 4, "above": 0.0}}],
)
def test_one_button_for_two_commands_is_rejected(tmp_path, bindings):
    with pytest.raises(ConfigError, match="одну кнопку"):
        load_config(write(tmp_path, {"joystick": {"bindings": bindings}}))


def test_three_position_switch_may_give_two_commands(tmp_path):
    bindings = {"takeoff": {"axis": 5, "above": 0.5}, "land": {"axis": 5, "below": -0.5}}
    assert load_config(write(tmp_path, {"joystick": {"bindings": bindings}})).joystick.bindings.land is not None


@pytest.mark.parametrize(
    "calibration, problem",
    [([{"axis": 2, "low": -1.0, "center": 0.95, "high": 1.0}], "центр"),  # стик крена: центр почти в упоре
     ([{"axis": 1, "low": -0.2, "center": -0.2, "high": 0.1}], "газ")],  # газ почти не ходит
)
def test_hand_edited_profile_with_broken_axis_is_rejected(tmp_path, calibration, problem):
    profile = {"axes": {"roll": 2, "pitch": 3, "throttle": 1, "yaw": None}, "calibration": calibration}
    with pytest.raises(ConfigError, match=problem):
        load_config(write(tmp_path, {"joystick": {"profiles": {PAD: profile}}}))


def test_throttle_resting_at_bottom_is_valid():
    # У газа без пружины покой — внизу: центр в упоре для газа — норма.
    ControllerProfile(axes=AxisMap(throttle=2), calibration=(AxisRange(axis=2, low=-1.0, center=-1.0, high=1.0),))


def test_old_trigger_flips_become_dashes_and_dualsense_gets_arrow_flips(tmp_path):
    """Профиль прошлой версии: flip_* на курках были коротким рывком — теперь это dash_*, а сальто — на стрелках."""
    old = {"bindings": {"takeoff": {"button": 0}, "flip_forward": {"axis": 5, "above": 0.0},
                        "flip_backward": {"axis": 4, "above": 0.0}}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"joystick": {"profiles": {PAD: old, "Other Pad": old}}}), encoding="utf-8")
    joystick = load_config(str(path)).joystick
    bindings = joystick.profile(PAD).bindings
    assert bindings.dash_forward == Binding(axis=5, above=0.0)
    assert bindings.dash_backward == Binding(axis=4, above=0.0)
    assert (bindings.flip_forward, bindings.flip_backward, bindings.flip_left, bindings.flip_right) == (
        Binding(button=11), Binding(button=12), Binding(button=13), Binding(button=14))
    other = joystick.profile("Other Pad").bindings
    assert other.dash_forward == Binding(axis=5, above=0.0) and other.flip_forward is None


def test_maneuver_settings_are_checked(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"flight": {"dash_angle": 90}}), encoding="utf-8")
    with pytest.raises(ConfigError, match="dash_angle"):
        load_config(str(path))
