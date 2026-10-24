"""CrazyflieLink поверх подменённого cflib: проверяем, что зовём настоящие имена API."""

import threading
from unittest.mock import MagicMock, call

import cflib.crazyflie
import cflib.crazyflie.syncCrazyflie
import cflib.crtp
import pytest
from cflib.utils.callbacks import Caller

import main
from cfpilot import link as link_module
from cfpilot.channels import OnAir, RadioUri
from cfpilot.config import FlightLimits
from cfpilot.flight import STALE_LAND, Action, AttitudeSetpoint, FlightMode, HoverSetpoint, Output, StopSetpoint
from cfpilot.link import CrazyflieLink, DeckInfo

PARAMS = {"deck.bcFlow2": "1", "deck.bcZRanger2": "0", "deck.bcLighthouse4": "0", "deck.bcLoco": "0"}
URI = "radio://0/80/2M/E7E7E7E7E7"
HOVER = HoverSetpoint(0.3, 0.0, 0.0, 0.5)


class FakeCrazyflie:
    def __init__(self, rw_cache=None):
        self.connection_lost = Caller()
        self.fully_connected = Caller()
        self.link_statistics = MagicMock()
        self.link_statistics.link_quality_updated = Caller()
        self.param = MagicMock()
        self.param.get_value.side_effect = lambda name: PARAMS[name]
        self.log = MagicMock()
        self.log.toc.get_element_by_complete_name.return_value = object()
        # Настоящий add_config привязывает cf к конфигу; link=None — start() ничего не шлёт.
        self.log.add_config.side_effect = lambda config: setattr(config, "cf", MagicMock(link=None))
        self.commander = MagicMock()
        self.supervisor = MagicMock()


class FakeSync:
    instances = []
    send_params = True

    def __init__(self, uri, cf):
        self.uri = uri
        self.cf = cf
        self.closed = False
        FakeSync.instances.append(self)

    def open_link(self):
        if FakeSync.send_params:
            self.cf.fully_connected.call(self.uri)

    def close_link(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def fake_cflib(monkeypatch):
    FakeSync.instances = []
    FakeSync.send_params = True
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: None)
    monkeypatch.setattr(cflib.crazyflie, "Crazyflie", FakeCrazyflie)
    monkeypatch.setattr(cflib.crazyflie.syncCrazyflie, "SyncCrazyflie", FakeSync)
    monkeypatch.setattr(link_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(link_module, "KEEPALIVE_PERIOD", 3600.0)  # фоновый поток в тестах молчит
    return FakeSync


def connected(fake_cflib, clock=None):
    link = CrazyflieLink(URI, FlightLimits(), cache_dir="unused", clock=clock or Clock())
    decks = link.connect()
    return link, decks, fake_cflib.instances[-1].cf


def logged_names(cf, index=0):
    return [variable.name for variable in cf.log.add_config.call_args_list[index].args[0].variables]


def test_connect_detects_decks_and_starts_telemetry(fake_cflib):
    link, decks, cf = connected(fake_cflib)
    assert decks == DeckInfo(flow=True)
    assert "pm.vbat" in logged_names(cf)
    assert "range.zrange" in logged_names(cf)
    assert "supervisor.info" in logged_names(cf)
    assert link.position_ready  # с Flow deck ждать абсолютное положение не нужно
    link.close()


def log_config(cf, name):
    return next(item.args[0] for item in cf.log.add_config.call_args_list if item.args[0].name == name)


def test_fast_accelerometer_log_reports_throw(fake_cflib):
    clock = Clock()
    link, _, cf = connected(fake_cflib, clock)
    assert link.telemetry.thrown is None  # датчик ещё ничего не прислал — бросок не распознать
    fall_log = log_config(cf, "cfpilot-fall")
    assert fall_log.period_in_ms == link_module.FALL_PERIOD_MS
    assert [variable.name for variable in fall_log.variables] == ["acc.x", "acc.y", "acc.z"]
    fall_log.data_received_cb.call(990, {"acc.x": 0.0, "acc.y": 0.0, "acc.z": 2.2}, fall_log)  # толчок рукой
    for timestamp in range(1000, 1000 + link_module.FREE_FALL_MS + 10, 10):
        fall_log.data_received_cb.call(timestamp, {"acc.x": 0.02, "acc.y": 0.0, "acc.z": 0.05}, fall_log)
    assert link.telemetry.thrown
    clock.now += link_module.FALL_STALE + 0.05  # данные перестали приходить — старый «брошен» не в счёт
    assert link.telemetry.thrown is None
    fall_log.data_received_cb.call(2000, {"acc.x": 0.0, "acc.y": 0.0, "acc.z": 1.4}, fall_log)  # моторы поймали
    assert link.telemetry.thrown is False
    link.close()


def test_rearm_does_not_undo_failsafe_stop(fake_cflib):
    clock = Clock()
    link, _, cf = connected(fake_cflib, clock)
    link.execute(Output(HOVER))
    link.keepalive_tick(clock.now + 60.0)  # главный цикл молчал — failsafe посадил дрон
    assert link.failsafe_stopped
    link.execute(Output(None, (Action.REARM,)))
    assert link.failsafe_stopped  # подтверждение арминга — не новый взлёт
    link.close()


def test_telemetry_and_link_quality_callbacks(fake_cflib):
    link, _, cf = connected(fake_cflib)
    telemetry_log = log_config(cf, "cfpilot")
    telemetry_log.data_received_cb.call(0, {"pm.vbat": 3.8, "range.zrange": 400}, telemetry_log)
    cf.link_statistics.link_quality_updated.call(87.0)
    assert link.telemetry.battery == pytest.approx(3.8)
    assert link.telemetry.height == pytest.approx(0.4)
    assert link.telemetry.link_quality == 87.0
    link.close()


def test_execute_sends_setpoints(fake_cflib):
    link, _, cf = connected(fake_cflib)
    link.execute(Output(HOVER))
    cf.commander.send_hover_setpoint.assert_called_once_with(0.3, 0.0, 0.0, 0.5)
    link.close()


def test_lost_connection_stops_sending(fake_cflib):
    link, _, cf = connected(fake_cflib)
    cf.connection_lost.call(URI, "Too many packets lost")
    assert "потеряна" in link.error
    link.execute(Output(HOVER))
    cf.commander.send_hover_setpoint.assert_not_called()
    link.close()
    cf.commander.send_stop_setpoint.assert_not_called()
    assert fake_cflib.instances[-1].closed


def test_close_stops_motors_and_closes_link(fake_cflib):
    link, _, cf = connected(fake_cflib)
    link.close()
    assert cf.commander.mock_calls == [call.send_stop_setpoint(), call.send_notify_setpoint_stop()]
    assert fake_cflib.instances[-1].closed
    link.close()  # повторное закрытие — без ошибок


def test_close_right_after_flight_waits_until_supervisor_sees_landing(fake_cflib, monkeypatch):
    clock = Clock()
    link, _, cf = connected(fake_cflib, clock)
    monkeypatch.setattr(link_module.time, "sleep", lambda seconds: setattr(clock, "now", clock.now + seconds))
    link.execute(Output(HOVER))
    landed_at = clock.now
    link.close()
    assert clock.now - landed_at >= link_module.SUPERVISOR_SETTLE
    stops = cf.commander.send_stop_setpoint.call_count
    assert stops >= link_module.SUPERVISOR_SETTLE / link_module.STOP_REPEAT_PERIOD
    assert cf.commander.mock_calls[-1] == call.send_notify_setpoint_stop()


class TestSupervisorSettle:
    """Прошивка ещё 2 с после тяги считает дрон летящим: тишина или disarm в это время = блокировка."""

    def test_keeps_sending_stop_after_landing(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(HOVER))
        link.execute(Output(StopSetpoint()))
        link.keepalive_tick(clock.now + 0.2)
        link.keepalive_tick(clock.now + 0.25)  # слишком рано для повтора
        link.keepalive_tick(clock.now + 0.4)
        assert cf.commander.send_stop_setpoint.call_count == 3
        link.keepalive_tick(clock.now + link_module.SUPERVISOR_SETTLE + 0.5)  # посадка уже зафиксирована
        assert cf.commander.send_stop_setpoint.call_count == 3
        link.close()

    def test_disarm_is_skipped_until_landing_is_seen(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(HOVER))
        link.execute(Output(StopSetpoint(), (Action.DISARM,)))
        cf.supervisor.send_arming_request.assert_not_called()
        clock.now += link_module.SUPERVISOR_SETTLE + 0.1
        link.execute(Output(StopSetpoint(), (Action.DISARM,)))
        cf.supervisor.send_arming_request.assert_called_once_with(False)
        link.close()

    def test_failsafe_landing_also_settles(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(HOVER))  # z = 0.5, главный цикл дальше молчит
        descending = clock.now + STALE_LAND + 1.0
        link.keepalive_tick(descending)  # failsafe ещё снижается — это тоже тяга
        link.keepalive_tick(descending + 0.6)  # сел сам
        assert link.failsafe_stopped
        link.keepalive_tick(descending + 0.8)
        assert cf.commander.send_stop_setpoint.call_count == 2
        link.keepalive_tick(descending + link_module.SUPERVISOR_SETTLE + 0.5)
        assert cf.commander.send_stop_setpoint.call_count == 2
        link.close()


def test_without_height_deck_mode_is_manual_and_zrange_not_logged(fake_cflib, monkeypatch):
    monkeypatch.setitem(PARAMS, "deck.bcFlow2", "0")
    link, decks, cf = connected(fake_cflib)
    assert decks.best_mode() is FlightMode.MANUAL
    assert "range.zrange" not in logged_names(cf)
    # Без дек «высота» прошивки — барометр (высота над морем с дрейфом): её не просим и не показываем.
    assert "stateEstimate.z" not in logged_names(cf)
    telemetry_log = log_config(cf, "cfpilot")
    telemetry_log.data_received_cb.call(0, {"pm.vbat": 3.9}, telemetry_log)
    assert link.telemetry.height is None
    link.close()


def test_params_timeout_raises(fake_cflib, monkeypatch):
    FakeSync.send_params = False
    monkeypatch.setattr(link_module, "PARAMS_TIMEOUT", 0.01)
    with pytest.raises(ConnectionError, match="параметры"):
        CrazyflieLink(URI, FlightLimits(), cache_dir="unused").connect()


class TestFailsafeThread:
    def test_quiet_while_main_loop_is_alive(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(HOVER))
        clock.now += 0.05
        link.keepalive_tick(clock.now)
        assert cf.commander.send_hover_setpoint.call_count == 1
        link.close()

    def test_repeats_then_holds_when_main_loop_stalls(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(HOVER))
        link.keepalive_tick(clock.now + 0.15)
        cf.commander.send_hover_setpoint.assert_called_with(0.3, 0.0, 0.0, 0.5)
        link.keepalive_tick(clock.now + 1.0)
        cf.commander.send_hover_setpoint.assert_called_with(0.0, 0.0, 0.0, 0.5)
        link.close()

    def test_lands_by_itself_and_blocks_until_new_takeoff(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(HOVER))
        link.keepalive_tick(clock.now + 60.0)
        assert link.failsafe_stopped
        cf.commander.send_stop_setpoint.assert_called_once()
        link.execute(Output(HOVER))  # главный цикл «проснулся» со старой командой
        assert cf.commander.send_hover_setpoint.call_count == 1
        link.keepalive_tick(clock.now + 61.0)  # поток больше ничего не шлёт
        assert cf.commander.send_stop_setpoint.call_count == 1
        link.execute(Output(None, (Action.ARM,)))
        assert not link.failsafe_stopped
        link.close()

    def test_nothing_sent_on_ground(self, fake_cflib):
        clock = Clock()
        link, _, cf = connected(fake_cflib, clock)
        link.execute(Output(StopSetpoint()))
        link.keepalive_tick(clock.now + 10.0)
        assert cf.commander.send_stop_setpoint.call_count == 1
        link.close()


def test_lighthouse_waits_for_position(fake_cflib, monkeypatch):
    monkeypatch.setitem(PARAMS, "deck.bcFlow2", "0")
    monkeypatch.setitem(PARAMS, "deck.bcLighthouse4", "1")
    link, _, cf = connected(fake_cflib)
    assert "stateEstimate.z" in logged_names(cf)  # с Lighthouse высота из оценки положения — настоящая
    variance_log = log_config(cf, "cfpilot-kalman")
    assert "kalman.varPX" in [variable.name for variable in variance_log.variables]
    assert not link.position_ready
    for _ in range(link_module.VARIANCE_SAMPLES):
        variance_log.data_received_cb.call(0, {"kalman.varPX": 0.01, "kalman.varPY": 0.01, "kalman.varPZ": 0.01},
                                           variance_log)
    assert link.position_ready
    link.execute(Output(None, (Action.RESET_ESTIMATOR,)))
    assert not link.position_ready  # после сброса ждём заново
    link.close()


def test_variance_settled_rules():
    settled = [(0.01, 0.01, 0.01)] * link_module.VARIANCE_SAMPLES
    assert link_module.variance_settled(settled)
    assert not link_module.variance_settled(settled[:-1])
    drifting = settled[:-1] + [(0.02, 0.01, 0.01)]
    assert not link_module.variance_settled(drifting)


def test_drivers_initialised_only_once(monkeypatch):
    calls = []
    monkeypatch.setattr(link_module, "_drivers_ready", False)
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: calls.append(1))
    link_module.init_drivers_once()
    link_module.init_drivers_once()
    assert calls == [1]


def test_scan_prints_drones_from_all_channels(monkeypatch, capsys):
    scanned = []
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: None)
    monkeypatch.setattr(cflib.crtp, "get_interfaces_status", lambda: {"radio": "Crazyradio version 5.0"})
    monkeypatch.setattr(main, "scan_channels", lambda uri: scanned.append(uri) or (
        OnAir(RadioUri(0, 20), 1.0), OnAir(RadioUri(0, 85), 1.0), OnAir(RadioUri(0, 86), 0.3)))
    assert main.run_scan("radio://0/80/2M/E7E7E7E701") == 0
    output = capsys.readouterr().out
    assert scanned == ["radio://0/80/2M/E7E7E7E701"]  # ищем дрон с адресом из настроек
    assert "radio://0/20/2M/E7E7E7E7E7" in output
    assert "radio://0/85/2M/E7E7E7E7E7" in output
    assert "radio://0/86" not in output  # «призрак» соседнего канала — тот же дрон
    assert "Crazyradio version 5.0" in output


def test_scan_of_non_radio_address_uses_default_address(monkeypatch):
    scanned = []
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: None)
    monkeypatch.setattr(cflib.crtp, "get_interfaces_status", lambda: {})
    monkeypatch.setattr(main, "scan_channels", lambda uri: scanned.append(uri) or ())
    main.run_scan("usb://0")
    assert scanned == ["radio://0/80/2M/E7E7E7E7E7"]


def test_scan_without_radio_driver_explains_zadig(monkeypatch, capsys):
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: None)
    monkeypatch.setattr(cflib.crtp, "get_interfaces_status", lambda: {"radio": "Crazyradio not found"})
    assert main.run_scan(URI) == 1
    assert "libusb-win32" in capsys.readouterr().out


def test_scan_without_drones_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: None)
    monkeypatch.setattr(cflib.crtp, "get_interfaces_status", lambda: {})
    monkeypatch.setattr(main, "scan_channels", lambda uri: ())
    assert main.run_scan(URI) == 1
    assert "Ни на одном канале" in capsys.readouterr().out


def test_silent_drone_is_reported_as_lost_link(fake_cflib):
    # Радио выдернули: cflib не сообщает ни об ошибке, ни о пакетах — замечаем по тишине.
    clock = Clock()
    link, _, cf = connected(fake_cflib, clock)
    cf.link_statistics.link_quality_updated.call(95.0)
    clock.now += link_module.LINK_SILENCE / 2
    link.keepalive_tick(clock.now)
    assert link.error is None
    clock.now += link_module.LINK_SILENCE
    link.keepalive_tick(clock.now)
    assert "не отвечает" in link.error
    link.close()
    cf.commander.send_stop_setpoint.assert_not_called()  # слать уже некуда


def test_hanging_close_is_abandoned(fake_cflib, monkeypatch):
    released = threading.Event()
    healed = []
    monkeypatch.setattr(FakeSync, "close_link", lambda self: released.wait(5.0))
    monkeypatch.setattr(link_module, "CLOSE_TIMEOUT", 0.05)
    monkeypatch.setattr(link_module, "heal_shared_radio", lambda: healed.append(True))
    link, _, cf = connected(fake_cflib)
    cf.connection_lost.call(URI, "radio unplugged")
    link.close()  # не висит, хотя cflib так и не закрыл связь
    released.set()
    assert healed == [True]


class DeadRadio:
    def __init__(self, alive):
        self.alive = alive
        self._radio = MagicMock()

    def is_alive(self):
        return self.alive


def test_heal_drops_only_dead_shared_radio():
    alive, dead = DeadRadio(True), DeadRadio(False)
    manager = type("Manager", (), {"_radios": [alive, dead, None], "_lock": threading.Lock()})
    link_module.heal_shared_radio(manager)
    assert manager._radios == [alive, None, None]
    dead._radio.close.assert_called_once()
    alive._radio.close.assert_not_called()


def test_heal_ignores_unknown_cflib_layout():
    link_module.heal_shared_radio(type("Manager", (), {}))  # без _radios — ничего не делаем


def test_reboot_power_cycles_drone(monkeypatch):
    switches = []

    class FakeSwitch:
        def __init__(self, uri):
            self.uri = uri
            self.calls = []
            switches.append(self)

        def stm_power_cycle(self):
            self.calls.append("cycle")

        def close(self):
            self.calls.append("close")

    import cflib.utils.power_switch

    monkeypatch.setattr(cflib.utils.power_switch, "PowerSwitch", FakeSwitch)
    monkeypatch.setattr(cflib.crtp, "init_drivers", lambda **kwargs: None)
    link_module.reboot_drone(URI)
    assert (switches[0].uri, switches[0].calls) == (URI, ["cycle", "close"])


def test_flip_switches_firmware_to_rates_in_flight_and_back(fake_cflib):
    """Параметры flightmode пишутся по радио не сразу: до подтверждения — только безопасные «0, 0»."""
    link, _, cf = connected(fake_cflib)
    store = {**PARAMS, "flightmode.althold": "0", "flightmode.poshold": "0", "flightmode.posSet": "0",
             "flightmode.stabModeRoll": "1", "flightmode.stabModePitch": "1", "flightmode.stabModeYaw": "0"}
    written = {}
    cf.param.get_value.side_effect = lambda name: store[name]
    cf.param.set_value.side_effect = lambda name, value: written.__setitem__(name, value)
    link.execute(Output(AttitudeSetpoint(0.0, 0.0, 0.0, 0), (Action.ARM,)))  # арминг в режиме углов
    assert not link.rate_ready
    spin = AttitudeSetpoint(0.0, 720.0, 0.0, 30000, rate=True)
    link.execute(Output(spin))
    assert written == {"flightmode.stabModeRoll": "0", "flightmode.stabModePitch": "0"}
    cf.commander.send_setpoint.assert_called_with(0.0, 0.0, 0.0, 30000)  # прошивка ещё в углах — «ровно»
    assert not link.rate_ready
    store.update(written)  # дрон подтвердил
    link.execute(Output(spin))
    assert link.rate_ready
    cf.commander.send_setpoint.assert_called_with(0.0, 720.0, 0.0, 30000)
    link.execute(Output(AttitudeSetpoint(0.0, 5.0, 0.0, 30000)))  # обратно в углы: пока не подтвердил — «не вращайся»
    assert written["flightmode.stabModePitch"] == "1"
    cf.commander.send_setpoint.assert_called_with(0.0, 0.0, 0.0, 30000)
    store.update(written)
    link.execute(Output(AttitudeSetpoint(0.0, 5.0, 0.0, 30000)))
    assert not link.rate_ready
    cf.commander.send_setpoint.assert_called_with(0.0, 5.0, 0.0, 30000)
    link.close()
