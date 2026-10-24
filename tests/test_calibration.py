"""Мастер калибровки: «пульт» — подставной объект, время — шаги по DT."""

import pytest

from cfpilot.calibration import (
    read_buttons,
    AXIS_HOLD,
    CENTER,
    CENTER_HOLD,
    CHOOSE,
    RANGE,
    SUMMARY,
    AxisStep,
    ButtonStep,
    Calibrator,
    describe_binding,
)
from cfpilot.config import AxisMap, Binding, InvertMap, JoystickSettings
from cfpilot.controls import joystick_sticks

DT = 0.02


class Pad:
    """Геймпад как DualSense в SDL: 0/1 — левый стик, 2/3 — правый, 4/5 — курки (в покое -1)."""

    def __init__(self, name="DualSense Wireless Controller", instance_id=1, axes=None, buttons=16):
        self.name = name
        self.instance_id = instance_id
        self.axes = list(axes if axes is not None else [0.0, 0.0, 0.0, 0.0, -1.0, -1.0])
        self.buttons = [False] * buttons

    def get_name(self):
        return self.name

    def get_instance_id(self):
        return self.instance_id

    def get_numaxes(self):
        return len(self.axes)

    def get_axis(self, index):
        return self.axes[index]

    def get_numbuttons(self):
        return len(self.buttons)

    def get_button(self, index):
        return self.buttons[index]


def run(calibrator, seconds):
    for _ in range(round(seconds / DT)):
        calibrator.update(DT)


def hold_axis(calibrator, pad, axis, value, seconds=AXIS_HOLD + 0.1):
    pad.axes[axis] = value
    run(calibrator, seconds)


def tap(calibrator, pad, button):
    pad.buttons[button] = True
    calibrator.update(DT)
    pad.buttons[button] = False
    calibrator.update(DT)


def sweep(calibrator, pad, axes):
    """Шаг «ход стиков»: каждую ось — в оба упора и обратно в покой."""
    for axis in axes:
        rest = pad.axes[axis]
        for value in (-1.0, 1.0, rest):
            pad.axes[axis] = value
            calibrator.update(DT)


def through_range(calibrator, pad, axes=(0, 1, 2, 3)):
    calibrator.update(DT)  # единственный пульт выбирается сам
    run(calibrator, CENTER_HOLD + 0.1)
    assert calibrator.step == RANGE
    sweep(calibrator, pad, axes)
    tap(calibrator, pad, 15)


def complete_until_buttons(calibrator, pad):
    through_range(calibrator, pad)
    for axis, value in ((1, -1.0), (3, -1.0), (2, 1.0)):  # газ вверх (у SDL это минус), вперёд, вправо
        hold_axis(calibrator, pad, axis, value)
        pad.axes[axis] = 0.0
    calibrator.update(DT)
    calibrator.skip()  # поворот — кнопками


def complete_dualsense(calibrator, pad):
    complete_until_buttons(calibrator, pad)
    for button in (0, 2, 3, 1, 9, 10):  # взлёт, посадка, стоп, удержание, поворот влево/вправо
        tap(calibrator, pad, button)
    while calibrator.step != SUMMARY:  # бросок, сальто, рывки — без кнопок
        calibrator.skip()
    calibrator.confirm()


def test_single_controller_is_picked_without_asking():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    calibrator.update(DT)
    assert calibrator.joystick is pad
    assert calibrator.step == CENTER


def test_no_controller_asks_to_plug_one_in():
    calibrator = Calibrator(lambda: [])
    calibrator.update(DT)
    assert calibrator.step == CHOOSE
    assert "Подключи" in calibrator.view().title


def test_with_several_controllers_the_touched_one_is_picked():
    first, second = Pad("Xbox Controller", instance_id=1), Pad(instance_id=2)
    calibrator = Calibrator(lambda: [first, second])
    calibrator.update(DT)
    assert calibrator.joystick is None
    assert calibrator.view().controllers == ("Xbox Controller", "DualSense Wireless Controller")
    second.buttons[0] = True
    calibrator.update(DT)
    assert calibrator.joystick is second


def test_center_is_captured_after_controller_lies_still():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    calibrator.update(DT)
    run(calibrator, CENTER_HOLD / 2)
    pad.axes[0] = 0.5  # тронули — отсчёт заново
    run(calibrator, CENTER_HOLD / 2)
    assert calibrator.step == CENTER
    assert 0 < calibrator.view().progress < 1
    run(calibrator, CENTER_HOLD)
    assert calibrator.step == RANGE


def test_range_needs_sticks_moved_to_their_limits():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    calibrator.update(DT)
    calibrator.confirm()  # центр — сразу по Enter
    sweep(calibrator, pad, (0,))
    tap(calibrator, pad, 0)
    assert calibrator.step == RANGE
    assert "только у 1" in calibrator.view().message


def test_gamepad_calibration_builds_profile():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    complete_dualsense(calibrator, pad)
    device, profile = calibrator.result
    assert device == "DualSense Wireless Controller"
    assert profile.axes == AxisMap(roll=2, pitch=3, throttle=1, yaw=None)
    assert profile.invert == InvertMap(roll=False, pitch=True, throttle=True, yaw=False)
    assert profile.bindings.takeoff == Binding(button=0)
    assert profile.bindings.kill == Binding(button=3)
    assert profile.bindings.yaw_right == Binding(button=10)
    assert [item.axis for item in profile.calibration] == [1, 2, 3]


def test_calibrated_profile_flies_in_the_directions_shown():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    complete_dualsense(calibrator, pad)
    settings = JoystickSettings(deadzone=0.0, expo=0.0).with_profile(*calibrator.result)
    settings = settings.for_controller(pad.name)
    sticks = joystick_sticks([0.0, -1.0, 1.0, -1.0, -1.0, -1.0], [False] * 16, settings)
    assert (sticks.throttle, sticks.pitch, sticks.roll) == (1.0, 1.0, 1.0)


def test_stick_not_moved_to_both_limits_is_not_accepted():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    through_range(calibrator, pad, axes=(0, 1, 2))  # ось 3 на шаге хода не двигали
    hold_axis(calibrator, pad, 1, -1.0)
    pad.axes[1] = 0.0
    hold_axis(calibrator, pad, 3, -1.0)
    assert isinstance(calibrator.step, AxisStep) and calibrator.step.role == "pitch"
    assert "до упора" in calibrator.view().message


def test_throttle_left_up_after_range_step_keeps_its_direction():
    pad = Pad("RadioMaster TX16S Joystick", axes=[0.0, 0.0, -1.0, 0.0, -1.0])  # газ (ось 2) без пружины, внизу
    calibrator = Calibrator(lambda: [pad])
    calibrator.update(DT)
    calibrator.confirm()
    sweep(calibrator, pad, (0, 1, 3))
    pad.axes[2] = 1.0  # газ провели вверх и там оставили
    tap(calibrator, pad, 0)
    run(calibrator, 1.0)
    assert calibrator.step.role == "throttle"  # вверху с начала шага — не засчитываем
    pad.axes[2] = -1.0
    calibrator.update(DT)
    hold_axis(calibrator, pad, 2, 1.0)
    assert calibrator.step.role == "pitch"
    assert calibrator.view().done[0] == ("Газ", "ось 2")


def test_radiomaster_switch_on_an_axis_is_bound_with_threshold():
    pad = Pad("RadioMaster TX16S Joystick", axes=[0.0, 0.0, -1.0, 0.0, -1.0, 0.0])
    calibrator = Calibrator(lambda: [pad])
    through_range(calibrator, pad, axes=(0, 1, 2, 3))
    pad.axes[2] = -1.0
    calibrator.update(DT)
    hold_axis(calibrator, pad, 2, 1.0)  # газ
    for axis in (1, 0, 3):  # вперёд, вправо, поворот
        hold_axis(calibrator, pad, axis, 1.0)
        pad.axes[axis] = 0.0
        calibrator.update(DT)
    assert isinstance(calibrator.step, ButtonStep) and calibrator.step.action == "takeoff"
    hold_axis(calibrator, pad, 4, 1.0, seconds=0.3)  # двухпозиционный тумблер SA
    hold_axis(calibrator, pad, 5, -1.0, seconds=0.3)  # трёхпозиционный SB вниз
    done = dict(calibrator.view().done)
    assert done["Взлёт"] == "ось 4 > +0.0"
    assert done["Посадка"] == "ось 5 < -0.5"


def test_one_button_cannot_get_two_commands():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    complete_until_buttons(calibrator, pad)
    tap(calibrator, pad, 0)  # взлёт
    tap(calibrator, pad, 0)  # посадка — той же кнопкой
    assert calibrator.step.action == "land"
    assert "Уже занято: Взлёт" in calibrator.view().message
    tap(calibrator, pad, 2)
    assert calibrator.step.action == "kill"


def test_missing_kill_switch_is_pointed_out_in_summary():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    complete_until_buttons(calibrator, pad)
    while calibrator.step != SUMMARY:
        calibrator.skip()
    assert "Аварийный стоп" in calibrator.view().message
    calibrator.confirm()
    assert calibrator.result[1].bindings.kill is None


def test_required_stick_cannot_be_skipped():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    through_range(calibrator, pad)
    calibrator.skip()
    assert calibrator.step.role == "throttle"
    assert "нельзя" in calibrator.view().message


def test_back_redoes_previous_step():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    through_range(calibrator, pad)
    hold_axis(calibrator, pad, 1, -1.0)
    pad.axes[1] = 0.0
    calibrator.back()
    assert calibrator.step.role == "throttle"
    assert calibrator.view().done == ()


def test_unplugged_controller_goes_back_to_choosing():
    pad = Pad()
    plugged = [pad]
    calibrator = Calibrator(lambda: plugged)
    through_range(calibrator, pad)
    plugged.clear()
    calibrator.controller_removed(pad.instance_id)
    calibrator.update(DT)
    assert calibrator.step == CHOOSE
    assert calibrator.joystick is None


def test_cancel_gives_no_result():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    calibrator.update(DT)
    calibrator.cancel()
    assert calibrator.cancelled
    assert calibrator.result is None


@pytest.mark.parametrize(
    "binding, text",
    [(Binding(button=3), "кнопка 3"), (Binding(axis=4, above=0.5), "ось 4 > +0.5"),
     (Binding(axis=5, below=-0.5), "ось 5 < -0.5")],
)
def test_binding_descriptions(binding, text):
    assert describe_binding(binding) == text


def test_controller_that_stops_answering_goes_back_to_choosing():
    pad = Pad()
    calibrator = Calibrator(lambda: [pad])
    calibrator.update(DT)

    def dead(index):
        raise RuntimeError("Joystick not initialized")  # pygame.error — подкласс RuntimeError

    pad.get_axis = dead
    calibrator.update(DT)
    assert calibrator.joystick is None
    assert "отключился" in calibrator.view().message


def test_flip_arrows_and_dash_triggers_can_be_calibrated_on_controller():
    pad = Pad()
    pad.buttons = [False] * 16
    calibrator = Calibrator(lambda: [pad])
    complete_until_buttons(calibrator, pad)
    for button in (0, 2, 3, 1, 9, 10):
        tap(calibrator, pad, button)
    calibrator.skip()  # бросок — пропускаем
    for button in (11, 12, 13, 14):  # стрелки DualSense: ↑ ↓ ← →
        tap(calibrator, pad, button)
    # Рывок 45° вперёд — курок R2 (ось 5), назад — L2 (ось 4)
    hold_axis(calibrator, pad, 5, 1.0, seconds=0.3)
    pad.axes[5] = -1.0
    calibrator.update(DT)
    hold_axis(calibrator, pad, 4, 1.0, seconds=0.3)
    pad.axes[4] = -1.0
    calibrator.update(DT)
    assert calibrator.step == SUMMARY
    done = dict(calibrator.view().done)
    assert "Сальто вперёд" in done and "Бочка вправо" in done
    assert "Рывок 45° вперёд" in done and "Рывок 45° назад" in done
    tap(calibrator, pad, 0)  # подтверждаем кнопкой на контроллере
    assert calibrator.finished
    device, profile = calibrator.result
    assert profile.bindings.flip_forward == Binding(button=11)
    assert profile.bindings.flip_right == Binding(button=14)
    assert profile.bindings.dash_forward == Binding(axis=5, above=0.0)
    assert profile.bindings.dash_backward == Binding(axis=4, above=0.0)


def test_hat_directions_are_read_as_extra_buttons():
    pad = Pad()
    pad.get_numhats = lambda: 1
    pad.get_hat = lambda index: (-1, 1)
    buttons = read_buttons(pad)
    assert buttons[len(pad.buttons):] == (True, False, True, False)  # ↑ и ←
