from unittest.mock import MagicMock, call

import pytest

from cfpilot.flight import (
    Action,
    AttitudeSetpoint,
    FlightMode,
    HoverSetpoint,
    Output,
    StopSetpoint,
    ZDistanceSetpoint,
)
from cfpilot.link import (
    FREE_FALL_MS,
    THROW_PUSH_MS,
    DeckInfo,
    FallSense,
    Telemetry,
    detect_decks,
    execute_output,
    sense_fall,
    supervisor_problem,
    telemetry_from_log,
)
from cfpilot.sim import SIM_THROW_AFTER, SimLink

IN_HAND = (0.0, 0.0, 1.0)  # датчик ускорения в руке и в полёте: ≈ 1 g
PUSH = (0.1, 0.0, 2.2)  # рука толкает дрон вверх
WEIGHTLESS = (0.05, -0.1, 0.1)  # после броска: почти 0 g


def feed(samples, start=FallSense()):
    """samples: (мс, ускорение) по порядку — как приходят из лога."""
    state = start
    for timestamp, acc in samples:
        state = sense_fall(state, timestamp, acc)
    return state


def every_10ms(start, stop, acc):
    return [(timestamp, acc) for timestamp in range(start, stop + 1, 10)]


class TestThrowSensing:
    def test_push_then_weightless_is_a_throw(self):
        state = feed([(990, PUSH)] + every_10ms(1000, 1000 + FREE_FALL_MS, WEIGHTLESS))
        assert state.thrown

    def test_short_weightless_moment_is_not_yet_a_throw(self):
        state = feed([(990, PUSH)] + every_10ms(1000, 1000 + FREE_FALL_MS - 10, WEIGHTLESS))
        assert not state.thrown

    def test_hand_jerked_down_without_push_is_not_a_throw(self):
        state = feed([(990, IN_HAND)] + every_10ms(1000, 1100, WEIGHTLESS))
        assert not state.thrown  # невесомость была, толчка — нет: моторы в руке не запускаем

    def test_push_long_before_is_not_a_throw(self):
        state = feed([(1000 - THROW_PUSH_MS - 10, PUSH)] + every_10ms(1000 - THROW_PUSH_MS, 990, IN_HAND)
                     + every_10ms(1000, 1100, WEIGHTLESS))
        assert not state.thrown

    def test_lost_packets_restart_the_count(self):
        state = feed([(990, PUSH), (1000, WEIGHTLESS), (1100, WEIGHTLESS)])  # между ними могло быть что угодно
        assert not state.thrown

    def test_caught_drone_is_no_longer_thrown(self):
        state = feed([(990, PUSH)] + every_10ms(1000, 1100, WEIGHTLESS) + [(1110, (0.0, 0.0, 1.4))])
        assert not state.thrown

    def test_telemetry_keeps_throw_flag_between_logs(self):
        previous = Telemetry(thrown=True)
        assert telemetry_from_log({"pm.vbat": 3.9}, previous).thrown


class TestDecks:
    @pytest.mark.parametrize(
        "decks, mode",
        [
            (DeckInfo(flow=True), FlightMode.HOVER),
            (DeckInfo(lighthouse=True), FlightMode.HOVER),
            (DeckInfo(loco=True), FlightMode.HOVER),
            (DeckInfo(zranger=True), FlightMode.ALTHOLD),
            (DeckInfo(), FlightMode.MANUAL),
        ],
    )
    def test_best_mode(self, decks, mode):
        assert decks.best_mode() is mode

    def test_detect_reads_any_known_param(self):
        values = {"deck.bcFlow2": "1", "deck.bcZRanger2": "0", "deck.bcLoco": "0"}
        decks = detect_decks(values.get)
        assert decks == DeckInfo(flow=True)

    def test_detect_treats_missing_params_as_absent(self):
        assert detect_decks(lambda name: None) == DeckInfo()

    def test_describe(self):
        assert DeckInfo().describe() == "нет"
        assert "Flow" in DeckInfo(flow=True, zranger=True).describe()


class TestTelemetry:
    def test_prefers_zranger_height(self):
        data = {"pm.vbat": 3.9, "stateEstimate.z": 0.4, "range.zrange": 512, "stabilizer.roll": 1.0,
                "stabilizer.pitch": 2.0, "stabilizer.yaw": 3.0}
        telemetry = telemetry_from_log(data, Telemetry(link_quality=90.0))
        assert telemetry.height == pytest.approx(0.512)
        assert telemetry.battery == pytest.approx(3.9)
        assert telemetry.link_quality == 90.0
        assert (telemetry.roll, telemetry.pitch, telemetry.yaw) == (1.0, 2.0, 3.0)

    def test_falls_back_to_state_estimate(self):
        telemetry = telemetry_from_log({"stateEstimate.z": 0.4}, Telemetry())
        assert telemetry.height == pytest.approx(0.4)

    def test_reads_supervisor_state(self):
        assert telemetry_from_log({"supervisor.info": 0x40}, Telemetry()).supervisor == 0x40


class TestSupervisor:
    """Биты supervisor.info из прошивки: 0x20 перевёрнут, 0x40 заблокирован, 0x80 авария."""

    def test_ready_drone_has_no_problem(self):
        assert supervisor_problem(0x0E) == ""  # заармлен, авто-арминг, может лететь

    def test_old_firmware_without_supervisor_log_has_no_problem(self):
        assert supervisor_problem(None) == ""

    def test_locked_drone_is_rebooted_from_the_program(self):
        assert "нажми R" in supervisor_problem(0x40)

    def test_tumbled_drone_must_be_put_upright(self):
        assert "ровно" in supervisor_problem(0x20 | 0x80)

    def test_crash_alone_is_not_a_problem(self):
        assert supervisor_problem(0x80) == ""  # снимается запросом recovery при взлёте


class TestExecute:
    def test_hover(self):
        cf = MagicMock()
        execute_output(cf, Output(HoverSetpoint(0.1, -0.2, 30.0, 0.5)))
        cf.commander.send_hover_setpoint.assert_called_once_with(0.1, -0.2, 30.0, 0.5)

    def test_zdistance(self):
        cf = MagicMock()
        execute_output(cf, Output(ZDistanceSetpoint(1.0, -2.0, 3.0, 0.4)))
        cf.commander.send_zdistance_setpoint.assert_called_once_with(1.0, -2.0, 3.0, 0.4)

    def test_attitude(self):
        cf = MagicMock()
        execute_output(cf, Output(AttitudeSetpoint(1.0, 2.0, 3.0, 40000)))
        cf.commander.send_setpoint.assert_called_once_with(1.0, 2.0, 3.0, 40000)

    def test_stop_only_stops_motors(self):
        cf = MagicMock()
        execute_output(cf, Output(StopSetpoint()))
        assert cf.commander.mock_calls == [call.send_stop_setpoint()]

    def test_nothing_to_send(self):
        cf = MagicMock()
        execute_output(cf, Output())
        assert cf.commander.mock_calls == []

    def test_actions_run_before_setpoint(self):
        cf = MagicMock()
        sleeps = []
        execute_output(cf, Output(HoverSetpoint(0, 0, 0, 0), (Action.ARM, Action.RESET_ESTIMATOR)), sleep=sleeps.append)
        # Сначала снимаем «аварию» (если дрон упал, но стоит ровно), потом армим.
        assert cf.supervisor.mock_calls == [call.send_crash_recovery_request(), call.send_arming_request(True)]
        assert cf.param.set_value.mock_calls == [
            call("kalman.resetEstimation", "1"),
            call("kalman.resetEstimation", "0"),
        ]
        assert sleeps == [pytest.approx(0.1)]
        cf.commander.send_hover_setpoint.assert_called_once()

    def test_failed_estimator_reset_does_not_block_flight(self):
        cf = MagicMock()
        cf.param.set_value.side_effect = KeyError("kalman.resetEstimation")
        execute_output(cf, Output(HoverSetpoint(0, 0, 0, 0), (Action.RESET_ESTIMATOR,)), sleep=lambda _: None)
        cf.commander.send_hover_setpoint.assert_called_once()


class TestSim:
    def test_connect_reports_decks_for_requested_mode(self):
        assert SimLink(FlightMode.HOVER).connect().best_mode() is FlightMode.HOVER
        assert SimLink(FlightMode.ALTHOLD).connect().best_mode() is FlightMode.ALTHOLD
        assert SimLink(FlightMode.MANUAL).connect().best_mode() is FlightMode.MANUAL

    def test_hover_setpoint_moves_height_towards_target(self):
        clock = iter([0.0, 0.5, 1.0])
        link = SimLink(FlightMode.HOVER, clock=lambda: next(clock))
        link.connect()
        link.execute(Output(HoverSetpoint(0, 0, 0, 0.5)))
        link.execute(Output(HoverSetpoint(0, 0, 0, 0.5)))
        assert 0.0 < link.telemetry.height <= 0.5
        link.execute(Output(StopSetpoint()))
        assert link.telemetry.height == 0.0
        assert link.error is None
        link.close()

    def test_waiting_for_throw_is_simulated_as_throw(self):
        now = [0.0]
        link = SimLink(FlightMode.MANUAL, clock=lambda: now[0])
        waiting = Output(AttitudeSetpoint(0, 0, 0, 0), (Action.REARM,))  # так программа ждёт броска
        while now[0] < SIM_THROW_AFTER + 0.1:
            link.execute(waiting)
            assert not link.telemetry.thrown or now[0] >= SIM_THROW_AFTER
            now[0] += 0.125  # двоичная дробь: время складывается без ошибок округления
        assert link.telemetry.thrown and link.telemetry.height > 0  # «подбросили»
        link.execute(Output(AttitudeSetpoint(0, 0, 0, 40000), (Action.ARM,)))  # поймали
        assert not link.telemetry.thrown

    def test_single_arm_of_normal_takeoff_is_not_a_throw(self):
        now = [0.0]
        link = SimLink(FlightMode.MANUAL, clock=lambda: now[0])
        link.execute(Output(AttitudeSetpoint(0, 0, 0, 0), (Action.ARM,)))
        for _ in range(100):  # газ ещё внизу — моторы стоят, но это не бросок
            now[0] += 0.05
            link.execute(Output(AttitudeSetpoint(0, 0, 0, 0)))
        assert not link.telemetry.thrown
        assert link.telemetry.height == 0.0


def firmware_params(cf):
    values = {
        "flightmode.althold": "0", "flightmode.poshold": "0", "flightmode.posSet": "0",
        "flightmode.stabModeRoll": "1", "flightmode.stabModePitch": "1", "flightmode.stabModeYaw": "0",
    }
    cf.param.get_value.side_effect = values.__getitem__
    cf.param.set_value.side_effect = values.__setitem__
    return values


def test_acro_uses_legacy_packets_with_confirmed_onboard_rate_mode():
    import struct
    from cflib.crazyflie.commander import Commander
    from cflib.crtp.crtpstack import CRTPPort
    cf = MagicMock()
    cf.commander = Commander(cf)
    values = firmware_params(cf)
    execute_output(cf, Output(AttitudeSetpoint(0, 0, 0, 0, rate=True), (Action.ARM,)))
    assert values["flightmode.stabModeRoll"] == "0"
    assert values["flightmode.stabModePitch"] == "0"
    for thrust in (0, 18000, 50000, 65535, 0):
        execute_output(cf, Output(AttitudeSetpoint(100, -200, 30, thrust, rate=True)))
        packet = cf.send_packet.call_args.args[0]
        assert packet.port == CRTPPort.COMMANDER
        assert struct.unpack('<fffH', packet.data) == (100, 200, 30, thrust)


def test_manual_arm_restores_angles_after_acro():
    cf = MagicMock()
    values = firmware_params(cf)
    execute_output(cf, Output(AttitudeSetpoint(0, 0, 0, 0, rate=True), (Action.ARM,)))
    execute_output(cf, Output(AttitudeSetpoint(0, 0, 0, 0), (Action.ARM,)))
    assert values["flightmode.stabModeRoll"] == "1"
    assert values["flightmode.stabModePitch"] == "1"
    assert values["flightmode.stabModeYaw"] == "0"


def test_unconfirmed_rate_mode_blocks_arm_and_positive_thrust():
    cf = MagicMock()
    firmware_params(cf)
    cf.param.set_value.side_effect = None  # firmware never acknowledges the write
    with pytest.raises(RuntimeError, match="stabModeRoll"):
        execute_output(cf, Output(AttitudeSetpoint(100, 200, 0, 30000, rate=True), (Action.ARM,)),
                       sleep=lambda _: None)
    cf.supervisor.send_arming_request.assert_not_called()
    cf.commander.send_setpoint.assert_called_once_with(0.0, 0.0, 0.0, 0)
    cf.commander.send_stop_setpoint.assert_called_once()


def test_acro_zero_throttle_uses_literal_zero_without_generic_stop():
    cf = MagicMock()
    execute_output(cf, Output(AttitudeSetpoint(90, 180, 0, 0, rate=True)))
    cf.commander.send_setpoint.assert_called_once_with(90, 180, 0, 0)
    cf.commander.send_setpoint_manual.assert_not_called()
    cf.commander.send_stop_setpoint.assert_not_called()


def test_acro_simulator_integrates_both_rates_and_holds_attitude_at_neutral():
    from cfpilot.sim import _simulate
    telemetry = Telemetry(battery=4.0, height=1.0, roll=10.0, pitch=20.0, yaw=0.0)
    telemetry = _simulate(telemetry, AttitudeSetpoint(100, -200, 0, 38000, rate=True), 0.1)
    assert telemetry.roll == 20.0
    assert telemetry.pitch == 0.0
    neutral = _simulate(telemetry, AttitudeSetpoint(0, 0, 0, 38000, rate=True), 0.1)
    assert neutral.roll == 20.0
    assert neutral.pitch == 0.0
