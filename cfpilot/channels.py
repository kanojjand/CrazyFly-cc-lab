"""Поиск дрона по всем каналам Crazyradio: дрон может сидеть не на том канале, что записан в config.json."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from cfpilot.i18n import t

LAST_CHANNEL = 125  # каналы Crazyradio — 0…125
RATES = ("250K", "1M", "2M")
DEFAULT_RATE = "2M"
DEFAULT_ADDRESS = 0xE7E7E7E7E7
SCAN_ARC = 1  # повторов пакета при поиске — как в cflib.scan_interface (подключение потом ставит своё)
NULL_PACKET = (0xFF,)  # пустой пакет: дрон подтверждает его и ничего не делает
PROBES = 20  # пакетов каждому найденному каналу — по доле ответов настоящий дрон отличается от «призрака»
# Дрон рядом с радио отвечает и на соседних каналах. Всё, что ближе этого, считаем одним дроном
# (Bitcraze и так советует разносить дроны на 2M хотя бы на 2 канала).
GHOST_SPREAD = 2  # каналов
RELIABLE = 0.5  # доля ответов, при которой на канале можно летать

_URI = re.compile(r"^radio://(\d+)/(\d+)(?:/(250K|1M|2M))?(?:/([0-9A-Fa-f]{1,10}))?$")


@dataclass(frozen=True)
class RadioUri:
    dongle: int
    channel: int
    rate: str = DEFAULT_RATE
    address: int = DEFAULT_ADDRESS

    def __str__(self) -> str:
        return f"radio://{self.dongle}/{self.channel}/{self.rate}/{self.address:010X}"


def parse_radio_uri(uri: str) -> RadioUri | None:
    """None — не радио-адрес (usb://, с параметрами после «?»): по каналам такой не ищут."""
    match = _URI.match(uri.strip())
    if match is None or int(match[2]) > LAST_CHANNEL:
        return None
    dongle, channel, rate, address = match.groups()
    return RadioUri(int(dongle), int(channel), rate or DEFAULT_RATE,
                    int(address, 16) if address else DEFAULT_ADDRESS)


@dataclass(frozen=True)
class OnAir:
    """Дрон ответил на этом канале и скорости; answers — доля подтверждённых пробных пакетов (0…1)."""

    uri: RadioUri
    answers: float

    def describe(self) -> str:
        return t("канал {channel} · {rate} · связь {answers:.0%}", channel=self.uri.channel, rate=self.uri.rate,
                 answers=self.answers)


def distinct_drones(found: Sequence[OnAir], preferred: RadioUri | None = None) -> tuple[OnAir, ...]:
    """Из каждой кучки соседних каналов — лучший ответ (при равенстве — свой адрес, потом 2M). По порядку каналов."""

    def rank(hit: OnAir) -> tuple:
        return hit.answers, hit.uri == preferred, hit.uri.rate == DEFAULT_RATE

    kept: list[OnAir] = []
    for hit in sorted(found, key=rank, reverse=True):
        if all(abs(hit.uri.channel - other.uri.channel) > GHOST_SPREAD for other in kept):
            kept.append(hit)
    return tuple(sorted(kept, key=lambda hit: (hit.uri.channel, RATES.index(hit.uri.rate))))


def scan_channels(uri: str, probes: int = PROBES) -> tuple[OnAir, ...]:
    """Ищет дрон с адресом из uri на всех каналах и скоростях — 1–2 секунды (378 пакетов по USB).

    Бросает исключение, если радио не открылось. Зависнуть может, если умер общий поток радио cflib, —
    поэтому зовут его в отдельном потоке с таймаутом (см. connection).
    """
    from cflib.crtp.radiodriver import RadioManager
    from cflib.drivers.crazyradio import Crazyradio

    target = parse_radio_uri(uri)
    if target is None:
        raise ValueError(f"По каналам ищутся только адреса radio://…, а не {uri}")
    rates = {"250K": Crazyradio.DR_250KPS, "1M": Crazyradio.DR_1MPS, "2M": Crazyradio.DR_2MPS}
    radio = RadioManager.open(target.dongle)
    try:
        radio.set_address(tuple(target.address.to_bytes(5, "big")))
        radio.set_arc(SCAN_ARC)
        found = []
        for rate in RATES:
            radio.set_data_rate(rates[rate])
            for channel in radio.scan_channels(0, LAST_CHANNEL, NULL_PACKET):
                radio.set_channel(channel)
                answered = sum(_acked(radio.send_packet(NULL_PACKET)) for _ in range(probes))
                found.append(OnAir(RadioUri(target.dongle, channel, rate, target.address), answered / probes))
        return tuple(found)
    finally:
        radio.close()


def _acked(ack) -> bool:
    return ack is not None and bool(ack.ack)
