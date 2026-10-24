"""Подключение с повторами: настоящие потоки, подставные «дроны», нулевые паузы."""

import threading

import pytest

from cfpilot import connection
from cfpilot.channels import OnAir, RadioUri
from cfpilot.connection import Connector, Stage, check_radio, friendly_error
from cfpilot.driver import RadioUsb
from cfpilot.link import DeckInfo

WAIT = 5.0  # с — верхняя граница для join, чтобы зависший тест не висел вечно
URI = "radio://0/80/2M/E7E7E7E7E7"


class FakeLink:
    uri = ""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.closed = threading.Event()

    def connect(self):
        if isinstance(self.behaviour, threading.Event):
            self.behaviour.wait(WAIT)  # «завис» cflib — до команды теста
            return DeckInfo()
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour

    def close(self):
        self.closed.set()


def factory(*behaviours):
    links = [FakeLink(item) for item in behaviours]
    feed = iter(links)

    def make_link(uri):
        link = next(feed)
        link.uri = uri
        return link

    return links, make_link


def connector(make_link, **options):
    defaults = dict(check=lambda: None, heal=lambda: None, retry_delay=0.0, reboot_pause=0.0, radio_poll=0.0)
    return Connector(URI, make_link, **{**defaults, **options})


def on_air(*drones):
    """Итог поиска: (канал, доля ответов) на скорости 2M с адресом по умолчанию."""
    return [OnAir(RadioUri(0, channel), answers) for channel, answers in drones]


def wait_until(condition):
    for _ in range(500):
        if condition():
            return
        threading.Event().wait(0.01)
    raise AssertionError("не дождались")


def test_keeps_trying_until_drone_answers():
    lost = Exception("Too many packets lost")
    links, make_link = factory(lost, lost, DeckInfo(flow=True))
    worker = connector(make_link)
    worker.start()
    worker.join(WAIT)
    assert worker.status.stage is Stage.CONNECTED
    assert worker.status.attempt == 3
    assert all(link.closed.is_set() for link in links[:2])  # неудачные попытки не держат радио
    link, decks = worker.take()
    assert (link, decks) == (links[2], DeckInfo(flow=True))
    assert link.uri == worker.status.uri == URI  # без поиска — прямо по адресу из настроек
    assert not links[2].closed.is_set()
    assert worker.take() is None  # связь забрали — второй раз её не отдаст


def test_status_explains_last_failure():
    gate = threading.Event()
    _, make_link = factory(Exception("Too many packets lost"), gate)
    worker = connector(make_link)
    worker.start()
    wait_until(lambda: worker.status.attempt == 2)
    assert worker.status.stage is Stage.SEARCHING
    assert "Дрон не отвечает" in worker.status.error
    gate.set()
    worker.join(WAIT)


def test_waits_for_radio_and_driver():
    problems = iter([Stage.NO_RADIO, Stage.NO_DRIVER])
    seen = []

    def check():
        stage = next(problems, None)
        seen.append(stage)
        return stage

    _, make_link = factory(DeckInfo())
    worker = connector(make_link, check=check)
    worker.start()
    worker.join(WAIT)
    assert seen == [Stage.NO_RADIO, Stage.NO_DRIVER, None]
    assert worker.status.stage is Stage.CONNECTED


def test_hung_attempt_is_abandoned_and_radio_reopened():
    hung = threading.Event()
    healed = []
    links, make_link = factory(hung, DeckInfo())
    worker = connector(make_link, attempt_timeout=0.1, heal=lambda: healed.append(True))
    worker.start()
    worker.join(WAIT)
    assert worker.status.stage is Stage.CONNECTED
    assert healed  # перед следующей попыткой радио открывается заново
    hung.set()  # брошенная попытка всё-таки подключилась — связь закрывается сама
    assert links[0].closed.wait(WAIT)
    assert not links[1].closed.is_set()


def test_reboot_happens_before_next_attempt():
    calls = []
    _, make_link = factory(DeckInfo())
    worker = connector(make_link, reboot=calls.append, reboot_first=True)
    assert worker.status.stage is Stage.REBOOTING
    worker.start()
    worker.join(WAIT)
    assert calls == [URI]
    assert worker.status.stage is Stage.CONNECTED


def test_failed_reboot_is_reported_and_connection_continues():
    def reboot(uri):
        raise Exception(f"Failed to connect to Crazyflie at {uri}")

    gate = threading.Event()
    _, make_link = factory(gate)
    worker = connector(make_link, reboot=reboot, reboot_first=True)
    worker.start()
    wait_until(lambda: worker.status.stage is Stage.SEARCHING)
    assert "перезагрузку" in worker.status.error
    gate.set()
    worker.join(WAIT)


def test_reboot_requested_while_searching():
    calls = []
    lost = Exception("Too many packets lost")
    _, make_link = factory(*([lost] * 3), DeckInfo())
    worker = connector(make_link, reboot=calls.append)
    worker.request_reboot()
    worker.start()
    worker.join(WAIT)
    assert calls == [URI]


def test_without_reboot_function_request_is_ignored():
    _, make_link = factory(DeckInfo())
    worker = connector(make_link)
    worker.request_reboot()
    assert not worker.can_reboot
    worker.start()
    worker.join(WAIT)
    assert worker.status.stage is Stage.CONNECTED


def test_cancel_closes_connected_link():
    links, make_link = factory(DeckInfo())
    worker = connector(make_link)
    worker.start()
    worker.join(WAIT)
    worker.cancel()
    assert links[0].closed.is_set()


def test_cancel_during_attempt_stops_retrying():
    gate = threading.Event()
    links, make_link = factory(gate)
    worker = connector(make_link)
    worker.start()
    worker.cancel()
    worker.join(WAIT)
    gate.set()
    assert links[0].closed.wait(WAIT)  # попытка дошла до конца уже после отмены — связь закрыта
    assert worker.take() is None


@pytest.mark.parametrize(
    "raw, friendly",
    [("Too many packets lost", "Дрон не отвечает"),
     ("Couldn't load link driver: Cannot find a Crazyradio Dongle\n\nTraceback...", "Crazyradio не открывается"),
     ("No driver found or malformed URI: radio://x", "Неверный адрес"),
     ("Дрон не прислал параметры\nподробности", "Дрон не прислал параметры"),
     ("", "неизвестная ошибка")],
)
def test_friendly_errors(raw, friendly):
    assert friendly_error(raw).startswith(friendly)


@pytest.mark.parametrize(
    "state, stage",
    [(RadioUsb.ABSENT, Stage.NO_RADIO), (RadioUsb.NO_DRIVER, Stage.NO_DRIVER), (RadioUsb.READY, None),
     (RadioUsb.UNKNOWN, None)],
)
def test_check_radio(monkeypatch, state, stage):
    monkeypatch.setattr(connection, "radio_usb_state", lambda: state)
    assert check_radio() is stage


def test_unexpected_error_does_not_kill_connection_thread():
    calls = []

    def check():
        calls.append(True)
        if len(calls) == 1:
            raise ValueError("ctypes сломался")
        return None

    _, make_link = factory(DeckInfo())
    worker = connector(make_link, check=check)
    worker.start()
    worker.join(WAIT)
    assert worker.status.stage is Stage.CONNECTED


def test_hung_reboot_is_abandoned():
    hung = threading.Event()
    healed = []
    _, make_link = factory(DeckInfo())
    worker = connector(make_link, reboot=lambda uri: hung.wait(WAIT), reboot_first=True, reboot_timeout=0.1,
                       heal=lambda: healed.append(True))
    worker.start()
    worker.join(WAIT)
    hung.set()
    assert worker.status.stage is Stage.CONNECTED
    assert healed


class TestSearchOnAllChannels:
    def test_drone_on_another_channel_is_found_and_confirmed(self):
        links, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((85, 1.0)))
        worker.start()
        wait_until(lambda: worker.status.stage is Stage.CHOOSING)
        # Адрес E7E7E7E7E7 у всех дронов с завода: вдруг это соседский — сами не подключаемся.
        assert [str(drone.uri) for drone in worker.status.drones] == ["radio://0/85/2M/E7E7E7E7E7"]
        assert not worker.connected
        worker.choose("radio://0/85/2M/E7E7E7E7E7")
        worker.join(WAIT)
        assert worker.status.stage is Stage.CONNECTED
        assert links[0].uri == worker.status.uri == "radio://0/85/2M/E7E7E7E7E7"

    def test_weak_own_drone_still_wins_its_neighbourhood(self):
        links, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((80, 0.3), (110, 1.0)))
        worker.start()
        worker.join(WAIT)
        assert links[0].uri == URI

    def test_own_drone_wins_over_others(self):
        links, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((20, 1.0), (80, 0.9), (100, 1.0)))
        worker.start()
        worker.join(WAIT)
        assert links[0].uri == URI

    def test_scan_looks_for_address_from_settings(self):
        scanned = []
        _, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: scanned.append(uri) or on_air((80, 1.0)))
        worker.start()
        worker.join(WAIT)
        assert scanned == [URI]

    def test_several_drones_wait_for_choice(self):
        links, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((20, 1.0), (100, 0.6)))
        worker.start()
        wait_until(lambda: worker.status.stage is Stage.CHOOSING)
        drones = worker.status.drones
        assert [drone.uri.channel for drone in drones] == [20, 100]
        assert not worker.connected  # к чужому дрону сами не подключаемся
        worker.choose(str(drones[1].uri))
        worker.join(WAIT)
        assert worker.status.stage is Stage.CONNECTED
        assert links[0].uri == "radio://0/100/2M/E7E7E7E7E7"

    def test_ghost_channels_are_one_drone_in_the_list(self):
        _, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((84, 0.3), (85, 1.0), (86, 0.5)))
        worker.start()
        wait_until(lambda: worker.status.stage is Stage.CHOOSING)
        assert [drone.uri.channel for drone in worker.status.drones] == [85]
        worker.cancel()
        worker.join(WAIT)

    def test_own_drone_beside_its_ghost_connects_directly(self):
        links, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((79, 1.0), (80, 0.9)))
        worker.start()
        worker.join(WAIT)
        assert links[0].uri == URI

    def test_empty_air_is_explained_and_search_continues(self):
        drone_on = threading.Event()
        _, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((80, 1.0)) if drone_on.is_set() else [])
        worker.start()
        wait_until(lambda: "Ни на одном канале" in worker.status.error)
        assert worker.status.stage is Stage.SEARCHING
        assert "E7E7E7E7E7" in worker.status.error
        drone_on.set()
        worker.join(WAIT)
        assert worker.status.stage is Stage.CONNECTED

    def test_scan_error_is_reported(self):
        scans, gate = [], threading.Event()

        def scan(uri):
            scans.append(uri)
            if len(scans) == 1:
                raise Exception("Cannot find a Crazyradio Dongle")
            gate.wait(WAIT)
            return on_air((80, 1.0))

        _, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=scan)
        worker.start()
        wait_until(lambda: len(scans) == 2)
        assert "Crazyradio не открывается" in worker.status.error
        gate.set()
        worker.join(WAIT)
        assert worker.status.stage is Stage.CONNECTED

    def test_hung_scan_is_abandoned_and_radio_reopened(self):
        hung, healed, scans = threading.Event(), [], []

        def scan(uri):
            scans.append(uri)
            if len(scans) == 1:
                hung.wait(WAIT)
            return on_air((80, 1.0))

        _, make_link = factory(DeckInfo())
        worker = connector(make_link, scan=scan, scan_timeout=0.1, heal=lambda: healed.append(True))
        worker.start()
        worker.join(WAIT)
        hung.set()
        assert worker.status.stage is Stage.CONNECTED
        assert healed

    def test_chosen_drone_is_kept_after_failed_attempt_and_rebooted(self):
        reboots, lost = [], Exception("Too many packets lost")
        _, make_link = factory(lost, DeckInfo())
        worker = connector(make_link, scan=lambda uri: on_air((85, 1.0)), reboot=reboots.append)
        original = worker._try_once

        def try_once():  # после первой неудачи просят перезагрузку — как клавишей R
            outcome = original()
            if outcome is None:
                worker.request_reboot()
            return outcome

        worker._try_once = try_once
        worker.start()
        wait_until(lambda: worker.status.stage is Stage.CHOOSING)
        worker.choose("radio://0/85/2M/E7E7E7E7E7")
        worker.join(WAIT)  # выбранный дрон дальше — «свой»: второй раз не спрашиваем
        assert reboots == ["radio://0/85/2M/E7E7E7E7E7"]
        assert worker.status.stage is Stage.CONNECTED
