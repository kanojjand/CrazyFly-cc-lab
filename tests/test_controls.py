import pytest

from cfpilot.config import AxisMap, AxisRange, Binding, Bindings, InvertMap, JoystickSettings
from cfpilot.controls import (
    KeyState,
    Sticks,
    apply_deadzone,
    apply_expo,
    binding_active,
    binding_problems,
    calibrate,
    clamp,
    joystick_sticks,
    keyboard_sticks,
    read_axis,
    step_throttle_level,
)


class TestShaping:
    def test_clamp_limits_both_sides(self):
        assert clamp(1.7) == 1.0
        assert clamp(-3.0) == -1.0
        assert clamp(0.25) == 0.25

    def test_deadzone_zeroes_small_values(self):
        assert apply_deadzone(0.04, 0.05) == 0.0
        assert apply_deadzone(-0.05, 0.05) == 0.0

    def test_deadzone_rescales_so_output_is_continuous(self):
        assert apply_deadzone(1.0, 0.1) == pytest.approx(1.0)
        assert apply_deadzone(-1.0, 0.1) == pytest.approx(-1.0)
        assert apply_deadzone(0.55, 0.1) == pytest.approx(0.5)

    def test_zero_expo_is_linear(self):
        assert apply_expo(0.5, 0.0) == pytest.approx(0.5)

    def test_expo_softens_center_but_keeps_endpoints(self):
        assert apply_expo(0.5, 0.5) < 0.5
        assert apply_expo(1.0, 0.5) == pytest.approx(1.0)
        assert apply_expo(-1.0, 0.5) == pytest.approx(-1.0)


class TestKeyboard:
    def test_no_keys_gives_neutral_sticks(self):
        assert keyboard_sticks(KeyState(), throttle_level=None) == Sticks()

    def test_arrows_map_to_roll_and_pitch(self):
        sticks = keyboard_sticks(KeyState(forward=True, right=True), throttle_level=None)
        assert sticks.pitch == 1.0
        assert sticks.roll == 1.0

    def test_opposite_keys_cancel(self):
        sticks = keyboard_sticks(KeyState(left=True, right=True), throttle_level=None)
        assert sticks.roll == 0.0

    def test_spring_throttle_follows_w_and_s(self):
        assert keyboard_sticks(KeyState(up=True), throttle_level=None).throttle == 1.0
        assert keyboard_sticks(KeyState(down=True), throttle_level=None).throttle == -1.0

    def test_yaw_keys(self):
        assert keyboard_sticks(KeyState(yaw_right=True), throttle_level=None).yaw == 1.0
        assert keyboard_sticks(KeyState(yaw_left=True), throttle_level=None).yaw == -1.0

    def test_held_throttle_level_is_used_when_given(self):
        sticks = keyboard_sticks(KeyState(up=True), throttle_level=-0.3)
        assert sticks.throttle == -0.3

    def test_throttle_level_integrates_and_clamps(self):
        level = step_throttle_level(-1.0, KeyState(up=True), dt=0.5, rate=1.0)
        assert level == pytest.approx(-0.5)
        assert step_throttle_level(0.9, KeyState(up=True), dt=1.0, rate=1.0) == 1.0
        assert step_throttle_level(-0.2, KeyState(), dt=1.0, rate=1.0) == -0.2


def _settings(**overrides) -> JoystickSettings:
    base = dict(deadzone=0.0, expo=0.0)
    base.update(overrides)
    return JoystickSettings(**base)


class TestJoystick:
    def test_read_axis_returns_zero_for_missing_axis(self):
        assert read_axis([0.5], index=3, invert=False) == 0.0
        assert read_axis([0.5], index=None, invert=False) == 0.0

    def test_read_axis_inverts(self):
        assert read_axis([0.5], index=0, invert=True) == -0.5

    def test_default_mapping_is_aetr(self):
        sticks = joystick_sticks([0.1, 0.2, 0.3, 0.4], (), _settings())
        assert sticks == Sticks(roll=0.1, pitch=0.2, throttle=0.3, yaw=0.4)

    def test_custom_mapping_and_inversion(self):
        settings = _settings(
            axes=AxisMap(roll=3, pitch=2, throttle=1, yaw=0),
            invert=InvertMap(pitch=True),
        )
        sticks = joystick_sticks([0.4, 0.3, 0.2, 0.1], (), settings)
        assert sticks == Sticks(roll=0.1, pitch=-0.2, throttle=0.3, yaw=0.4)

    def test_throttle_skips_deadzone_because_manual_mode_needs_raw_value(self):
        sticks = joystick_sticks([0.02, 0.02, 0.02, 0.02], (), _settings(deadzone=0.05))
        assert sticks.roll == 0.0
        assert sticks.throttle == 0.02

    def test_yaw_from_shoulder_buttons_with_yaw_axis_disabled(self):
        settings = _settings(
            axes=AxisMap(yaw=None),
            bindings=Bindings(yaw_left=Binding(button=9), yaw_right=Binding(button=10)),
        )
        buttons = [False] * 11
        assert joystick_sticks([0.0, 0.0, 0.0, 0.9], buttons, settings).yaw == 0.0  # ось поворота не читается
        buttons[10] = True
        assert joystick_sticks([0.0] * 4, buttons, settings).yaw == 1.0
        buttons[9] = True
        assert joystick_sticks([0.0] * 4, buttons, settings).yaw == 0.0  # обе сразу гасят друг друга


class TestBindings:
    def test_missing_binding_is_inactive(self):
        assert binding_active(None, axes=[1.0], buttons=[True]) is False

    def test_button_binding(self):
        binding = Binding(button=1)
        assert binding_active(binding, axes=[], buttons=[False, True]) is True
        assert binding_active(binding, axes=[], buttons=[True, False]) is False

    def test_button_out_of_range_is_inactive(self):
        assert binding_active(Binding(button=5), axes=[], buttons=[True]) is False

    def test_axis_above_threshold(self):
        binding = Binding(axis=0, above=0.5)
        assert binding_active(binding, axes=[0.9], buttons=[]) is True
        assert binding_active(binding, axes=[0.1], buttons=[]) is False

    def test_problems_cover_every_binding(self):
        bindings = Bindings(hold=Binding(button=20), yaw_left=Binding(axis=9, above=0.5))
        problems = binding_problems(bindings, num_axes=6, num_buttons=17)
        assert len(problems) == 2
        assert any(problem.startswith("hold") for problem in problems)

    def test_axis_below_threshold(self):
        binding = Binding(axis=0, below=-0.5)
        assert binding_active(binding, axes=[-0.9], buttons=[]) is True
        assert binding_active(binding, axes=[0.0], buttons=[]) is False


class TestCalibration:
    RANGE = AxisRange(axis=0, low=-0.8, center=0.1, high=0.9)

    def test_stick_limits_and_rest_map_to_full_travel(self):
        assert calibrate(-0.8, self.RANGE) == -1.0
        assert calibrate(0.1, self.RANGE) == 0.0
        assert calibrate(0.9, self.RANGE) == 1.0
        assert calibrate(0.5, self.RANGE) == pytest.approx(0.5)

    def test_throttle_uses_whole_travel_from_bottom_to_top(self):
        throttle = AxisRange(axis=2, low=-0.9, center=-0.9, high=0.9)  # без пружины — покой внизу
        assert calibrate(-0.9, throttle, centered=False) == -1.0
        assert calibrate(0.0, throttle, centered=False) == pytest.approx(0.0)
        assert calibrate(0.9, throttle, centered=False) == 1.0

    def test_values_past_calibrated_limits_are_clamped(self):
        assert calibrate(1.0, self.RANGE) == 1.0

    def test_without_calibration_raw_value_is_used(self):
        assert calibrate(0.3, None) == 0.3

    def test_joystick_sticks_apply_calibration(self):
        settings = JoystickSettings(
            axes=AxisMap(roll=0, pitch=1, throttle=2, yaw=None), deadzone=0.0, expo=0.0,
            calibration=(self.RANGE, AxisRange(axis=2, low=-0.9, center=-0.9, high=0.9)),
        )
        sticks = joystick_sticks([0.9, 0.0, 0.9], [], settings)
        assert (sticks.roll, sticks.throttle) == (1.0, 1.0)
