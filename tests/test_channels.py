"""Поиск дрона по всем каналам: разбор адресов, «призраки» на соседних каналах, сканирование подменённого радио."""

from types import SimpleNamespace

import cflib.crtp.radiodriver
import pytest
from cflib.drivers.crazyradio import Crazyradio

from cfpilot import channels
from cfpilot.channels import OnAir, RadioUri, distinct_drones, parse_radio_uri, scan_channels

HOME = RadioUri(0, 80, "2M", 0xE7E7E7E7E7)


def hit(channel, answers=1.0, rate="2M"):
    return OnAir(RadioUri(0, channel, rate, 0xE7E7E7E7E7), answers)


@pytest.mark.parametrize(
    "uri, parsed",
    [("radio://0/80/2M/E7E7E7E7E7", HOME),
     ("radio://0/80/2M", HOME),  # адрес по умолчанию
     ("radio://0/80", HOME),  # и скорость по умолчанию
     ("radio://1/5/250K/e7e7e7e701", RadioUri(1, 5, "250K", 0xE7E7E7E701)),
     ("usb://0", None),
     ("radio://0/126/2M", None),  # каналов всего 0–125
     ("radio://0/80/2M/E7E7E7E7E7?rate_limit=100", None)],
)
def test_parse_radio_uri(uri, parsed):
    assert parse_radio_uri(uri) == parsed


def test_uri_text_round_trips():
    uri = RadioUri(0, 5, "250K", 0xE7E7E7E701)
    assert str(uri) == "radio://0/5/250K/E7E7E7E701"
    assert parse_radio_uri(str(uri)) == uri


def test_describe_shows_channel_rate_and_answers():
    assert hit(85, 0.95).describe() == "канал 85 · 2M · связь 95%"


def test_ghosts_on_neighbour_channels_are_one_drone():
    drones = distinct_drones([hit(79, 0.4), hit(80, 1.0), hit(81, 0.6), hit(80, 0.3, rate="1M")])
    assert drones == (hit(80, 1.0),)


def test_far_apart_channels_are_different_drones():
    assert distinct_drones([hit(90), hit(20, 0.7)]) == (hit(20, 0.7), hit(90))


def test_equal_answers_prefer_own_drone_then_2m():
    preferred = RadioUri(0, 81, "2M", 0xE7E7E7E7E7)
    assert distinct_drones([hit(80), hit(81)], preferred) == (hit(81),)
    assert distinct_drones([hit(80, rate="1M"), hit(80)]) == (hit(80),)


def test_nothing_on_air():
    assert distinct_drones([]) == ()


class FakeRadio:
    """Общий канал cflib к Crazyradio: дрон «сидит» на 2M, канал 85; на канале 86 — слабый «призрак»."""

    def __init__(self, fail_scan=False):
        self.fail_scan = fail_scan
        self.channel = self.rate = self.address = self.arc = None
        self.sent = 0
        self.closed = False

    def set_address(self, address):
        self.address = address

    def set_arc(self, arc):
        self.arc = arc

    def set_data_rate(self, rate):
        self.rate = rate

    def set_channel(self, channel):
        self.channel = channel

    def scan_channels(self, start, stop, packet):
        if self.fail_scan:
            raise Exception("Crazyradio: USB error")
        assert (start, stop) == (0, channels.LAST_CHANNEL)
        return (85, 86) if self.rate == Crazyradio.DR_2MPS else ()

    def send_packet(self, data):
        self.sent += 1
        answered = self.channel == 85 or (self.channel == 86 and self.sent % 4 == 0)
        return SimpleNamespace(ack=answered)

    def close(self):
        self.closed = True


@pytest.fixture
def radio(monkeypatch):
    fake = FakeRadio()
    opened = []

    def open_radio(devid):
        opened.append(devid)
        return fake

    monkeypatch.setattr(cflib.crtp.radiodriver.RadioManager, "open", staticmethod(open_radio))
    fake.opened = opened
    return fake


def test_scan_finds_drone_on_every_channel_and_measures_answers(radio):
    found = scan_channels("radio://0/80/2M/E7E7E7E701", probes=8)
    assert found == (OnAir(RadioUri(0, 85, "2M", 0xE7E7E7E701), 1.0), OnAir(RadioUri(0, 86, "2M", 0xE7E7E7E701), 0.25))
    assert radio.address == (0xE7, 0xE7, 0xE7, 0xE7, 0x01)
    assert radio.arc == channels.SCAN_ARC
    assert radio.opened == [0]
    assert radio.closed  # радио отдаём сразу — им будет пользоваться подключение


def test_scan_closes_radio_even_on_error(radio):
    radio.fail_scan = True
    with pytest.raises(Exception, match="USB error"):
        scan_channels("radio://0/80/2M")
    assert radio.closed


def test_scan_rejects_non_radio_uri():
    with pytest.raises(ValueError, match="radio://"):
        scan_channels("usb://0")
