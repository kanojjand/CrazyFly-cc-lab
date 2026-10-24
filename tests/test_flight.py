from dataclasses import replace

import pytest

from cfpilot.config import FlightLimits
from cfpilot.controls import Sticks, apply_expo
from cfpilot.flight import (
    ESTIMATOR_TIMEOUT,
    KILL_COOLDOWN,
    STALE_LAND,
    THROW_WAIT,
    Action,
    AttitudeSetpoint,
    Command,
    FlightMode,
    FlightState,
    HoverSetpoint,
    Phase,
    StopSetpoint,
    ZDistanceSetpoint,
    failsafe_setpoint,
    step,
    throttle_to_thrust,
)

LIMITS = FlightLimits(
    takeoff_height=0.5,
    min_height=0.2,
    max_height=1.0,
    takeoff_speed=0.5,
    land_speed=0.5,
    max_speed=0.4,
    climb_rate=0.5,
    max_yaw_rate=90.0,
    max_angle=10.0,
    arm_delay=1.0,
    thrust_min=10000,
    thrust_max=50000,
    land_thrust_ramp=1.0,
    hover_throttle_deadzone=0.1,
)
DT = 0.1
NEUTRAL = Sticks()
LOW_THROTTLE = Sticks(throttle=-1.0)
FLYING = FlightState(phase=Phase.FLYING, height=0.5, thrust=30000)


def tick(state, mode, sticks=NEUTRAL, command=Command()):
    return step(state, DT, sticks, command, mode, LIMITS)


def run(state, mode, ticks, sticks=NEUTRAL):
    for _ in range(ticks):
        state, _ = tick(state, mode, sticks)
    return state


def run_until(state, mode, phase, max_ticks=100):
    """Крутит цикл, пока не сменится фаза; возвращает (state, output, число тиков)."""
    for count in range(1, max_ticks + 1):
        state, output = tick(state, mode)
        if state.phase is phase:
            return state, output, count
    raise AssertionError(f"phase {phase} not reached, stuck in {state.phase}")


class TestTakeoff:
    def test_idle_without_command_sends_nothing(self):
        state, output = tick(FlightState(), FlightMode.HOVER)
        assert state.phase is Phase.IDLE
        assert output.setpoint is None
        assert output.actions == ()

    def test_hover_takeoff_arms_and_resets_estimator(self):
        state, output = tick(FlightState(), FlightMode.HOVER, command=Command(takeoff=True))
        assert state.phase is Phase.ARMING
        assert output.actions == (Action.ARM, Action.RESET_ESTIMATOR)
        assert output.setpoint is None

    def test_manual_takeoff_only_arms_and_unlocks_thrust(self):
        state, output = tick(FlightState(), FlightMode.MANUAL, LOW_THROTTLE, Command(takeoff=True))
        assert state.phase is Phase.ARMING
        assert output.actions == (Action.ARM,)
        assert output.setpoint == AttitudeSetpoint(0.0, 0.0, 0.0, 0)

    def test_manual_takeoff_refused_when_throttle_is_up(self):
        state, output = tick(FlightState(), FlightMode.MANUAL, Sticks(throttle=0.0), Command(takeoff=True))
        assert state.phase is Phase.IDLE
        assert output.setpoint is None
        assert output.message

    def test_arming_waits_for_arm_delay_then_climbs(self):
        state, _ = tick(FlightState(), FlightMode.HOVER, command=Command(takeoff=True))
        state, output, ticks = run_until(state, FlightMode.HOVER, Phase.TAKING_OFF)
        assert ticks == 10
        assert output.setpoint == HoverSetpoint(0.0, 0.0, 0.0, 0.0)

    def test_land_during_arming_cancels(self):
        state, output = tick(FlightState(phase=Phase.ARMING), FlightMode.HOVER, command=Command(land=True))
        assert state.phase is Phase.IDLE
        assert output.setpoint == StopSetpoint()

    def test_takeoff_ramps_height_and_ignores_sticks(self):
        state = FlightState(phase=Phase.TAKING_OFF)
        _, output = tick(state, FlightMode.HOVER, Sticks(pitch=1.0, yaw=1.0))
        assert output.setpoint == HoverSetpoint(vx=0.0, vy=0.0, yawrate=0.0, z=pytest.approx(0.05))

    def test_takeoff_reaches_target_and_switches_to_flying(self):
        state, output, ticks = run_until(FlightState(phase=Phase.TAKING_OFF), FlightMode.HOVER, Phase.FLYING)
        assert ticks == 10
        assert state.height == pytest.approx(LIMITS.takeoff_height)
        assert output.setpoint.z == pytest.approx(LIMITS.takeoff_height)

    def test_althold_takeoff_uses_zdistance_setpoint(self):
        _, output = tick(FlightState(phase=Phase.TAKING_OFF), FlightMode.ALTHOLD)
        assert isinstance(output.setpoint, ZDistanceSetpoint)

    def test_manual_goes_straight_to_flying_after_arming(self):
        state = FlightState(phase=Phase.ARMING, timer=0.95)
        state, output = tick(state, FlightMode.MANUAL, LOW_THROTTLE)
        assert state.phase is Phase.FLYING
        assert output.setpoint == AttitudeSetpoint(0.0, 0.0, 0.0, 0)


class TestFlyingSigns:
    """Знаки как в cfclient: стик вперёд = полёт вперёд, стик вправо = вправо."""

    def test_hover_forward_is_positive_vx(self):
        _, output = tick(FLYING, FlightMode.HOVER, Sticks(pitch=1.0))
        assert output.setpoint.vx == pytest.approx(LIMITS.max_speed)

    def test_hover_right_is_negative_vy(self):
        _, output = tick(FLYING, FlightMode.HOVER, Sticks(roll=1.0))
        assert output.setpoint.vy == pytest.approx(-LIMITS.max_speed)

    def test_hover_yaw_right_is_negative_yawrate(self):
        _, output = tick(FLYING, FlightMode.HOVER, Sticks(yaw=1.0))
        assert output.setpoint.yawrate == pytest.approx(-LIMITS.max_yaw_rate)

    def test_althold_forward_is_negative_pitch(self):
        _, output = tick(FLYING, FlightMode.ALTHOLD, Sticks(pitch=1.0, roll=1.0))
        assert isinstance(output.setpoint, ZDistanceSetpoint)
        assert output.setpoint.pitch == pytest.approx(-LIMITS.max_angle)
        assert output.setpoint.roll == pytest.approx(LIMITS.max_angle)

    def test_manual_uses_cflib_send_setpoint_convention(self):
        sticks = Sticks(roll=1.0, pitch=1.0, yaw=1.0, throttle=1.0)
        _, output = tick(FLYING, FlightMode.MANUAL, sticks)
        assert output.setpoint == AttitudeSetpoint(
            roll=LIMITS.max_angle, pitch=LIMITS.max_angle, yawrate=LIMITS.max_yaw_rate, thrust=LIMITS.thrust_max
        )


class TestHeight:
    def test_throttle_up_climbs(self):
        state, _ = tick(FLYING, FlightMode.HOVER, Sticks(throttle=1.0))
        assert state.height == pytest.approx(0.55)

    def test_throttle_inside_deadzone_holds_height(self):
        state, _ = tick(FLYING, FlightMode.HOVER, Sticks(throttle=0.08))
        assert state.height == pytest.approx(0.5)

    def test_height_is_clamped_to_limits(self):
        state = run(FLYING, FlightMode.HOVER, ticks=50, sticks=Sticks(throttle=1.0))
        assert state.height == pytest.approx(LIMITS.max_height)
        state = run(state, FlightMode.HOVER, ticks=50, sticks=Sticks(throttle=-1.0))
        assert state.height == pytest.approx(LIMITS.min_height)


class TestLanding:
    def test_land_descends_then_stops_motors(self):
        state, output = tick(FLYING, FlightMode.HOVER, command=Command(land=True))
        assert state.phase is Phase.LANDING
        assert output.setpoint.z == pytest.approx(0.45)
        state, output, _ = run_until(state, FlightMode.HOVER, Phase.IDLE)
        assert output.setpoint == StopSetpoint()
        assert output.actions == (Action.DISARM,)

    def test_land_during_takeoff_starts_from_current_height(self):
        state = FlightState(phase=Phase.TAKING_OFF, height=0.3)
        state, output = tick(state, FlightMode.HOVER, command=Command(land=True))
        assert state.phase is Phase.LANDING
        assert output.setpoint.z == pytest.approx(0.25)

    def test_manual_land_ramps_thrust_down(self):
        state, output = tick(FLYING, FlightMode.MANUAL, command=Command(land=True))
        assert state.phase is Phase.LANDING
        assert output.setpoint.thrust == 25000
        state, output, ticks = run_until(state, FlightMode.MANUAL, Phase.IDLE)
        assert ticks == 3
        assert output.setpoint == StopSetpoint()

    def test_manual_land_with_motors_already_off_finishes_at_once(self):
        state = FlightState(phase=Phase.FLYING, thrust=0)
        state, output = tick(state, FlightMode.MANUAL, command=Command(land=True))
        assert state.phase is Phase.IDLE
        assert output.setpoint == StopSetpoint()


class TestKill:
    @pytest.mark.parametrize("phase", list(Phase))
    def test_kill_stops_motors_in_any_phase(self, phase):
        state = FlightState(phase=phase, height=0.5)
        state, output = tick(state, FlightMode.HOVER, command=Command(kill=True, takeoff=True))
        assert state.phase is Phase.KILLED
        assert output.setpoint == StopSetpoint()

    def test_kill_disarms_once(self):
        state, output = tick(FLYING, FlightMode.HOVER, command=Command(kill=True))
        assert output.actions == (Action.DISARM,)
        _, output = tick(state, FlightMode.HOVER, command=Command(kill=True))
        assert output.actions == ()

    def test_stop_is_repeated_for_a_while_after_kill(self):
        state, _ = tick(FLYING, FlightMode.HOVER, command=Command(kill=True))
        outputs = []
        for _ in range(8):
            state, output = tick(state, FlightMode.HOVER)
            outputs.append(output.setpoint)
        assert outputs[:4] == [StopSetpoint()] * 4
        assert outputs[-1] is None

    def test_takeoff_blocked_right_after_kill(self):
        state, _ = tick(FLYING, FlightMode.HOVER, command=Command(kill=True))
        state, output = tick(state, FlightMode.HOVER, command=Command(takeoff=True))
        assert state.phase is Phase.KILLED
        assert output.message

    def test_can_take_off_again_after_cooldown(self):
        state = FlightState(phase=Phase.KILLED, timer=KILL_COOLDOWN)
        state, _ = tick(state, FlightMode.HOVER, command=Command(takeoff=True))
        assert state.phase is Phase.ARMING


class TestTakeoffGuards:
    def test_hover_takeoff_needs_centered_throttle(self):
        state, output = tick(FlightState(), FlightMode.HOVER, Sticks(throttle=0.8), Command(takeoff=True))
        assert state.phase is Phase.IDLE
        assert "центр" in output.message

    def test_manual_arming_aborts_if_throttle_raised(self):
        state, _ = tick(FlightState(), FlightMode.MANUAL, LOW_THROTTLE, Command(takeoff=True))
        state, output = tick(state, FlightMode.MANUAL, Sticks(throttle=0.5))
        assert state.phase is Phase.IDLE
        assert output.setpoint == StopSetpoint()
        assert Action.DISARM in output.actions

    def test_waits_for_position_estimate(self):
        state = FlightState(phase=Phase.ARMING, timer=LIMITS.arm_delay)
        state, output = tick(state, FlightMode.HOVER, command=Command(estimator_ready=False))
        assert state.phase is Phase.ARMING
        assert output.setpoint is None
        state, _ = tick(state, FlightMode.HOVER, command=Command(estimator_ready=True))
        assert state.phase is Phase.TAKING_OFF

    def test_gives_up_if_position_never_found(self):
        state = FlightState(phase=Phase.ARMING, timer=LIMITS.arm_delay + ESTIMATOR_TIMEOUT)
        state, output = tick(state, FlightMode.HOVER, command=Command(estimator_ready=False))
        assert state.phase is Phase.IDLE
        assert output.setpoint == StopSetpoint()
        assert output.message


class TestTrim:
    """Трим — постоянный наклон против сноса. Знаки как у стиков: + вправо, + вперёд."""

    TRIMMED = replace(LIMITS, trim_roll=1.5, trim_pitch=-0.5)

    def test_manual_levels_to_trim(self):
        _, output = step(FLYING, DT, Sticks(throttle=0.0), Command(), FlightMode.MANUAL, self.TRIMMED)
        assert (output.setpoint.roll, output.setpoint.pitch) == pytest.approx((1.5, -0.5))

    def test_trim_adds_to_stick(self):
        _, output = step(FLYING, DT, Sticks(roll=1.0), Command(), FlightMode.MANUAL, self.TRIMMED)
        assert output.setpoint.roll == pytest.approx(LIMITS.max_angle + 1.5)

    def test_althold_trim_follows_zdistance_signs(self):
        _, output = step(FLYING, DT, NEUTRAL, Command(), FlightMode.ALTHOLD, self.TRIMMED)
        assert (output.setpoint.roll, output.setpoint.pitch) == pytest.approx((1.5, 0.5))

    def test_hover_ignores_trim(self):
        _, output = step(FLYING, DT, NEUTRAL, Command(), FlightMode.HOVER, self.TRIMMED)
        assert (output.setpoint.vx, output.setpoint.vy) == (0.0, 0.0)

    def test_failsafe_levels_to_trim(self):
        attitude = failsafe_setpoint(AttitudeSetpoint(5.0, 5.0, 30.0, 40000), 1.0, self.TRIMMED)
        assert attitude == AttitudeSetpoint(1.5, -0.5, 0.0, 40000)
        zdistance = failsafe_setpoint(ZDistanceSetpoint(5.0, -5.0, 20.0, 0.4), 1.0, self.TRIMMED)
        assert zdistance == ZDistanceSetpoint(1.5, 0.5, 0.0, 0.4)


class TestFailsafe:
    def test_fresh_command_is_repeated(self):
        last = HoverSetpoint(0.3, 0.0, 10.0, 0.6)
        assert failsafe_setpoint(last, 0.1, LIMITS) == last

    def test_hover_holds_position_when_stale(self):
        last = HoverSetpoint(0.3, -0.2, 10.0, 0.6)
        assert failsafe_setpoint(last, 1.0, LIMITS) == HoverSetpoint(0.0, 0.0, 0.0, 0.6)

    def test_hover_descends_then_stops(self):
        last = HoverSetpoint(0.3, 0.0, 0.0, 0.6)
        held = failsafe_setpoint(last, STALE_LAND + 0.5, LIMITS)
        assert held.z == pytest.approx(0.6 - 0.5 * LIMITS.land_speed)
        assert failsafe_setpoint(last, STALE_LAND + 10.0, LIMITS) == StopSetpoint()

    def test_zdistance_levels_out(self):
        last = ZDistanceSetpoint(5.0, -5.0, 20.0, 0.4)
        assert failsafe_setpoint(last, 1.0, LIMITS) == ZDistanceSetpoint(0.0, 0.0, 0.0, 0.4)
        assert failsafe_setpoint(last, STALE_LAND + 10.0, LIMITS) == StopSetpoint()

    def test_manual_levels_and_ramps_thrust_down(self):
        last = AttitudeSetpoint(5.0, 5.0, 30.0, 40000)
        assert failsafe_setpoint(last, 1.0, LIMITS) == AttitudeSetpoint(0.0, 0.0, 0.0, 40000)
        assert failsafe_setpoint(last, STALE_LAND + 10.0, LIMITS) == StopSetpoint()

    def test_stop_stays_stop(self):
        assert failsafe_setpoint(StopSetpoint(), 100.0, LIMITS) == StopSetpoint()


class TestThrust:
    def test_bottom_stick_turns_motors_off(self):
        assert throttle_to_thrust(-1.0, LIMITS) == 0

    def test_full_stick_is_max_thrust(self):
        assert throttle_to_thrust(1.0, LIMITS) == LIMITS.thrust_max

    def test_mid_stick_is_mid_thrust(self):
        assert throttle_to_thrust(0.0, LIMITS) == 30000


HOVER_THROTTLE = Sticks(throttle=0.4)  # газ на висение, выставленный перед броском
THROW = Command(throw=True)
FALLING = Command(thrown=True, height=1.3)


def ready_for_throw(mode, sticks=NEUTRAL):
    state, _ = tick(FlightState(), mode, sticks, THROW)
    state, _ = tick(state, mode, sticks)  # дрон в руке — ловить можно только после этого
    return state


class TestThrow:
    def test_manual_throw_arms_but_keeps_motors_off(self):
        state, output = tick(FlightState(), FlightMode.MANUAL, LOW_THROTTLE, THROW)
        assert state.phase is Phase.THROW_READY
        assert output.actions == (Action.ARM,)
        assert output.setpoint == AttitudeSetpoint(0.0, 0.0, 0.0, 0)  # нулевая тяга снимает защиту газа
        assert "подбрось" in output.message

    def test_waiting_keeps_motors_off_even_with_throttle_up(self):
        state = ready_for_throw(FlightMode.MANUAL)
        state, output = tick(state, FlightMode.MANUAL, HOVER_THROTTLE)
        assert state.phase is Phase.THROW_READY
        assert output.setpoint.thrust == 0  # моторы стоят, пока дрон в руке
        assert output.actions == (Action.REARM,)  # прошивка могла снять арминг (дрон держали криво)

    def test_throw_flag_left_over_from_before_does_not_start_motors(self):
        state, _ = tick(FlightState(), FlightMode.MANUAL, HOVER_THROTTLE, THROW)
        for _ in range(3):  # флаг «брошен» застрял с прошлого раза, а дрон в руке
            state, output = tick(state, FlightMode.MANUAL, HOVER_THROTTLE, FALLING)
        assert state.phase is Phase.THROW_READY
        assert output.setpoint.thrust == 0

    def test_manual_catch_flies_with_pilots_throttle(self):
        state = ready_for_throw(FlightMode.MANUAL)
        state, output = tick(state, FlightMode.MANUAL, HOVER_THROTTLE, FALLING)
        thrust = throttle_to_thrust(HOVER_THROTTLE.throttle, LIMITS)
        assert state.phase is Phase.FLYING
        assert output.setpoint == AttitudeSetpoint(0.0, 0.0, 0.0, thrust)  # ровно — стабилизатор выровняет
        assert output.actions == (Action.ARM,)  # снять «аварию» и взвести моторы прямо в воздухе
        assert "Поймал" in output.message

    def test_arming_is_repeated_shortly_after_catch(self):
        state = ready_for_throw(FlightMode.MANUAL)
        state, _ = tick(state, FlightMode.MANUAL, HOVER_THROTTLE, FALLING)
        actions = []
        for _ in range(5):
            state, output = tick(state, FlightMode.MANUAL, HOVER_THROTTLE)
            actions.append(output.actions)
        assert actions[0] == (Action.REARM,)  # подтверждение, а не новый взлёт: failsafe не сбрасывает
        assert actions[-1] == ()  # повторяем недолго, дальше — обычный полёт

    def test_no_catch_with_throttle_at_bottom(self):
        state = ready_for_throw(FlightMode.MANUAL)
        state, output = tick(state, FlightMode.MANUAL, LOW_THROTTLE, FALLING)
        assert state.phase is Phase.THROW_READY
        assert output.setpoint.thrust == 0
        assert "газ" in output.message.lower()

    @pytest.mark.parametrize("mode, setpoint", [(FlightMode.HOVER, HoverSetpoint(0.0, 0.0, 0.0, 1.0)),
                                                (FlightMode.ALTHOLD, ZDistanceSetpoint(0.0, 0.0, 0.0, 1.0))])
    def test_height_modes_catch_at_current_height(self, mode, setpoint):
        state, output = tick(FlightState(), mode, NEUTRAL, THROW)
        assert output.actions == (Action.ARM, Action.RESET_ESTIMATOR)
        state, _ = tick(state, mode)
        state, output = tick(state, mode, NEUTRAL, FALLING)
        assert state.phase is Phase.FLYING
        assert output.setpoint == setpoint  # 1.3 м выше max_height — держим потолок из настроек
        assert state.height == LIMITS.max_height

    def test_height_mode_waits_for_estimator(self):
        state = ready_for_throw(FlightMode.HOVER)
        state, _ = tick(state, FlightMode.HOVER, NEUTRAL, Command(thrown=True, height=0.8, estimator_ready=False))
        assert state.phase is Phase.THROW_READY

    def test_height_mode_needs_centered_throttle(self):
        state, output = tick(FlightState(), FlightMode.HOVER, Sticks(throttle=1.0), THROW)
        assert state.phase is Phase.IDLE
        assert "газ" in output.message

    @pytest.mark.parametrize("cancel", [THROW, Command(land=True)])
    def test_throw_can_be_cancelled(self, cancel):
        state = ready_for_throw(FlightMode.MANUAL)
        state, output = tick(state, FlightMode.MANUAL, HOVER_THROTTLE, cancel)
        assert state.phase is Phase.IDLE
        assert output.setpoint == StopSetpoint()
        assert output.actions == (Action.DISARM,)

    def test_waiting_too_long_turns_motors_off(self):
        state = ready_for_throw(FlightMode.MANUAL)
        state = run(state, FlightMode.MANUAL, round(THROW_WAIT / DT) + 1, HOVER_THROTTLE)
        assert state.phase is Phase.IDLE

    def test_kill_beats_throw(self):
        state = ready_for_throw(FlightMode.MANUAL)
        state, output = tick(state, FlightMode.MANUAL, HOVER_THROTTLE, Command(kill=True, thrown=True))
        assert state.phase is Phase.KILLED
        assert output.setpoint == StopSetpoint()

    def test_throw_in_normal_flight_changes_nothing(self):
        state, output = tick(FLYING, FlightMode.MANUAL, HOVER_THROTTLE, FALLING)
        assert state.phase is Phase.FLYING
        assert output.actions == ()


def test_acro_mode_flight():
    """Verify that in ACRO mode, rate setpoints are sent proportional to sticks."""
    state = FlightState(phase=Phase.FLYING)
    limits = LIMITS
    sticks = Sticks(roll=0.5, pitch=-0.5, yaw=0.2, throttle=0.5)

    state, out = step(state, 0.02, sticks, Command(), FlightMode.ACRO, limits)
    assert isinstance(out.setpoint, AttitudeSetpoint)
    assert out.setpoint.rate is True
    assert 0 < out.setpoint.roll <= limits.acro_acceleration * 0.02
    assert out.setpoint.pitch == -out.setpoint.roll
    assert out.setpoint.thrust > 0


# ---------- сальто, бочка и рывок 45° ----------

FLIP_DT = 0.02
HOVER_STICK = Sticks(throttle=0.4)


def flip_ticks(state, mode, command=Command(), sticks=HOVER_STICK, max_ticks=500):
    """Крутит манёвр до конца; возвращает (state, список Output)."""
    outputs = []
    for _ in range(max_ticks):
        state, out = step(state, FLIP_DT, sticks, command, mode, LIMITS)
        outputs.append(out)
        if state.phase is not Phase.FLIPPING:
            break
    return state, outputs


@pytest.mark.parametrize("mode", [FlightMode.MANUAL, FlightMode.ACRO])
def test_flip_forward_boosts_spins_full_turn_and_recovers(mode):
    thrust = throttle_to_thrust(HOVER_STICK.throttle, LIMITS)
    state, out = step(FLYING, FLIP_DT, HOVER_STICK, Command(flip_forward=True), mode, LIMITS)
    assert state.phase is Phase.FLIPPING and state.flip_stage == "boost"
    assert "Сальто вперёд" in out.message
    assert out.setpoint == AttitudeSetpoint(0.0, 0.0, 0.0, round(thrust * LIMITS.flip_boost), rate=True)
    state, outputs = flip_ticks(state, mode)
    assert state.phase is Phase.FLYING
    spin = [o.setpoint for o in outputs if o.setpoint.rate and o.setpoint.pitch]
    assert all(s.roll == 0 and s.pitch > 0 and s.thrust == round(thrust * LIMITS.flip_spin_thrust) for s in spin)
    assert sum(s.pitch for s in spin) * FLIP_DT == pytest.approx(LIMITS.flip_angle)
    assert max(s.pitch for s in spin) == LIMITS.flip_rate
    recover = outputs[-2].setpoint  # последний пакет перед выходом — выравнивание
    if mode is FlightMode.MANUAL:
        assert not recover.rate and recover.pitch == LIMITS.trim_pitch  # углы: прошивка выровняет сама
    else:
        assert recover.rate and recover.pitch == 0.0  # acro: просто перестаём вращаться
    assert recover.thrust > thrust
    assert "Сальто готово" in outputs[-1].message


def test_roll_flips_turn_around_roll_axis():
    state, _ = step(FLYING, FLIP_DT, HOVER_STICK, Command(flip_left=True), FlightMode.ACRO, LIMITS)
    state, outputs = flip_ticks(state, FlightMode.ACRO)
    spin = [o.setpoint for o in outputs if o.setpoint.roll]
    assert spin and all(s.roll < 0 and s.pitch == 0 for s in spin)


def test_flip_waits_until_firmware_switches_to_rates_and_gives_up():
    state, _ = step(FLYING, FLIP_DT, HOVER_STICK, Command(flip_forward=True), FlightMode.MANUAL, LIMITS)
    not_ready = Command(rate_ready=False)
    state, outputs = flip_ticks(state, FlightMode.MANUAL, not_ready)
    assert state.phase is Phase.FLYING
    assert all(o.setpoint.pitch == 0 for o in outputs[:-1])  # ни одного вращения
    assert "сальто отменено" in outputs[-1].message


@pytest.mark.parametrize("mode", [FlightMode.HOVER, FlightMode.ALTHOLD])
def test_flip_needs_manual_or_acro(mode):
    state, out = step(FLYING, FLIP_DT, Sticks(), Command(flip_forward=True), mode, LIMITS)
    assert state.phase is Phase.FLYING
    assert "manual" in out.message


def test_flip_cannot_start_motors_at_zero_throttle():
    state, out = step(FLYING, DT, LOW_THROTTLE, Command(flip_forward=True), FlightMode.MANUAL, LIMITS)
    assert state.phase is Phase.FLYING
    assert out.setpoint is None and "газ" in out.message


def test_cutting_throttle_or_landing_aborts_flip():
    state, _ = step(FLYING, FLIP_DT, HOVER_STICK, Command(flip_forward=True), FlightMode.MANUAL, LIMITS)
    cut, out = step(state, FLIP_DT, LOW_THROTTLE, Command(), FlightMode.MANUAL, LIMITS)
    assert cut.phase is Phase.FLYING and out.setpoint.thrust == 0
    landing, _ = step(state, FLIP_DT, HOVER_STICK, Command(land=True), FlightMode.MANUAL, LIMITS)
    assert landing.phase is Phase.LANDING
    killed, out = step(state, FLIP_DT, HOVER_STICK, Command(kill=True), FlightMode.MANUAL, LIMITS)
    assert killed.phase is Phase.KILLED and isinstance(out.setpoint, StopSetpoint)


def test_manual_dash_tilts_45_while_trigger_is_held():
    thrust = throttle_to_thrust(HOVER_STICK.throttle, LIMITS)
    state, out = step(FLYING, FLIP_DT, HOVER_STICK, Command(dash=1), FlightMode.MANUAL, LIMITS)
    assert out.setpoint.pitch == LIMITS.dash_angle and not out.setpoint.rate
    assert out.setpoint.thrust == round(thrust * LIMITS.dash_boost)
    assert "Рывок 45° вперёд" in out.message
    state, out = step(state, FLIP_DT, HOVER_STICK, Command(dash=1), FlightMode.MANUAL, LIMITS)
    assert out.setpoint.pitch == LIMITS.dash_angle and out.message == ""
    state, out = step(state, FLIP_DT, HOVER_STICK, Command(), FlightMode.MANUAL, LIMITS)
    assert out.setpoint.pitch == LIMITS.trim_pitch  # отпустил курок — стик в центре, дрон выравнивается


def test_manual_dash_backward_and_time_limit():
    state, out = step(FLYING, FLIP_DT, HOVER_STICK, Command(dash=-1), FlightMode.MANUAL, LIMITS)
    assert out.setpoint.pitch == -LIMITS.dash_angle
    for _ in range(100):
        state, out = step(state, FLIP_DT, HOVER_STICK, Command(dash=-1), FlightMode.MANUAL, LIMITS)
    assert out.setpoint.pitch == LIMITS.trim_pitch  # дольше DASH_MAX_TIME не держим


def test_althold_dash_keeps_height():
    state, out = step(FLYING, FLIP_DT, Sticks(), Command(dash=1), FlightMode.ALTHOLD, LIMITS)
    assert out.setpoint == ZDistanceSetpoint(0.0, -LIMITS.dash_angle, -0.0, FLYING.height)


def test_acro_dash_turns_45_and_leaves_leveling_to_pilot():
    state, out = step(FLYING, FLIP_DT, HOVER_STICK, Command(dash=1), FlightMode.ACRO, LIMITS)
    assert state.phase is Phase.FLIPPING and out.setpoint.rate and out.setpoint.pitch == LIMITS.dash_rate
    turned = out.setpoint.pitch * FLIP_DT
    state, outputs = flip_ticks(state, FlightMode.ACRO, Command(dash=1))
    turned += sum(o.setpoint.pitch for o in outputs[:-1]) * FLIP_DT
    assert turned == pytest.approx(LIMITS.dash_angle)
    assert state.phase is Phase.FLYING and outputs[-1].setpoint.pitch == 0.0  # дальше не вращается
    state, out = step(state, FLIP_DT, HOVER_STICK, Command(dash=1), FlightMode.ACRO, LIMITS)
    assert state.phase is Phase.FLYING  # курок всё ещё зажат — второй раз не наклоняет


def test_hover_dash_is_explained():
    state, out = step(FLYING, FLIP_DT, Sticks(), Command(dash=1), FlightMode.HOVER, LIMITS)
    assert isinstance(out.setpoint, HoverSetpoint) is False and "althold" in out.message


def test_acro_throw_does_not_arm():
    state, out = step(FlightState(), DT, Sticks(), Command(throw=True), FlightMode.ACRO, LIMITS)
    assert state.phase is Phase.IDLE
    assert out.actions == ()


def test_acro_arming_always_prepares_rate_mode_at_zero_thrust():
    state, out = step(FlightState(), DT, LOW_THROTTLE, Command(takeoff=True), FlightMode.ACRO, LIMITS)
    assert out.setpoint.rate and out.setpoint.thrust == 0
    for _ in range(round(LIMITS.arm_delay / DT) + 1):
        state, out = step(state, DT, LOW_THROTTLE, Command(), FlightMode.ACRO, LIMITS)
        assert out.setpoint.rate and out.setpoint.thrust == 0
    assert state.phase is Phase.FLYING


def test_acro_land_and_failsafe_preserve_rates_and_reduce_actual_commanded_thrust():
    state, out = step(FLYING, DT, Sticks(throttle=0.5), Command(), FlightMode.ACRO, LIMITS)
    prior = out.setpoint.thrust
    state, out = step(state, DT, Sticks(roll=1), Command(land=True), FlightMode.ACRO, LIMITS)
    assert out.setpoint.rate
    assert out.setpoint.roll == out.setpoint.pitch == 0
    assert 0 < out.setpoint.thrust < prior
    stale = failsafe_setpoint(out.setpoint, STALE_LAND + 0.1, LIMITS)
    assert stale.rate
    assert stale.roll == stale.pitch == 0
    assert stale.thrust < out.setpoint.thrust


def test_acro_land_at_zero_throttle_cannot_start_motors():
    state, out = step(FLYING, DT, LOW_THROTTLE, Command(), FlightMode.ACRO, LIMITS)
    assert state.thrust == 0
    state, out = step(state, DT, LOW_THROTTLE, Command(land=True), FlightMode.ACRO, LIMITS)
    assert isinstance(out.setpoint, StopSetpoint)


@pytest.mark.parametrize("axis", ["roll", "pitch", "yaw"])
def test_soft_acro_ramps_in_and_release_stops_rotation_immediately(axis):
    state = FLYING
    sticks = Sticks(**{axis: 1.0}, throttle=0.5)
    state, out = step(state, 0.02, sticks, Command(), FlightMode.ACRO, LIMITS)
    assert getattr(out.setpoint, axis if axis != "yaw" else "yawrate") == pytest.approx(4.8)
    for _ in range(50):
        state, out = step(state, 0.02, sticks, Command(), FlightMode.ACRO, LIMITS)
    maximum = LIMITS.max_yaw_rate if axis == "yaw" else LIMITS.max_acro_rate
    assert getattr(out.setpoint, axis if axis != "yaw" else "yawrate") == pytest.approx(maximum)
    state, out = step(state, 0.02, Sticks(throttle=0.5), Command(), FlightMode.ACRO, LIMITS)
    assert state.acro_rates == (0.0, 0.0, 0.0)
    assert out.setpoint.thrust == throttle_to_thrust(0.5, LIMITS)


def test_soft_acro_half_stick_is_gentler_and_dt_independent():
    results = []
    for dt in (0.01, 0.02):
        state = FLYING
        for _ in range(round(0.5 / dt)):
            state, out = step(state, dt, Sticks(pitch=0.5, throttle=0.5), Command(), FlightMode.ACRO, LIMITS)
        results.append(out.setpoint.pitch)
    assert results == pytest.approx([24.75, 24.75])


def test_soft_acro_zero_gas_resets_rates_and_kill_is_immediate():
    state = replace(FLYING, acro_rates=(90, 90, 90))
    state, out = step(state, 0.02, Sticks(pitch=1, throttle=-1), Command(), FlightMode.ACRO, LIMITS)
    assert state.acro_rates == (0.0, 0.0, 0.0)
    assert out.setpoint.thrust == 0
    state, out = step(state, 0.02, Sticks(pitch=1, throttle=1), Command(kill=True), FlightMode.ACRO, LIMITS)
    assert state.phase is Phase.KILLED
    assert isinstance(out.setpoint, StopSetpoint)


def test_soft_acro_reversal_has_no_lingering_command_in_previous_direction():
    state = replace(FLYING, acro_rates=(0, 90, 0))
    state, out = step(state, 0.02, Sticks(pitch=-1, throttle=0.5), Command(), FlightMode.ACRO, LIMITS)
    assert out.setpoint.pitch == pytest.approx(-4.8)
