"""Подключение к дрону с повторами: пока окно открыто, программа ищет дрон сама — на всех каналах.

Включили дрон позже программы, перезагрузили его, выдернули и вставили радио — подключение просто
продолжается, перезапускать ничего не надо. Работает в фоне, окно в это время живёт.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol, Sequence

from cfpilot.channels import DEFAULT_ADDRESS, RELIABLE, OnAir, distinct_drones, parse_radio_uri
from cfpilot.driver import RadioUsb, radio_usb_state
from cfpilot.i18n import N_, t
from cfpilot.link import DeckInfo, heal_shared_radio

log = logging.getLogger(__name__)

RETRY_DELAY = 1.0  # с между попытками
RADIO_POLL = 0.5  # с — как часто смотреть, не вставили ли радио и не встал ли драйвер
ATTEMPT_TIMEOUT = 45.0  # с — первое подключение качает описание параметров (10–20 с); дольше — зависло
SCAN_TIMEOUT = 15.0  # с — поиск по всем каналам идёт 1–2 с; дольше — зависло радио
REBOOT_PAUSE = 2.0  # с — дрону после перезагрузки нужно проснуться
REBOOT_TIMEOUT = 10.0  # с — перезагрузка на мёртвом радио ждала бы ответа вечно
WAIT_STEP = 0.1  # с — как часто ожидание проверяет, не отменили ли его


class Stage(Enum):
    NO_RADIO = "no_radio"
    NO_DRIVER = "no_driver"
    SEARCHING = "searching"
    CHOOSING = "choosing"  # в эфире несколько дронов — ждём, какой выберут
    REBOOTING = "rebooting"
    CONNECTED = "connected"


@dataclass(frozen=True)
class Status:
    stage: Stage = Stage.SEARCHING
    attempt: int = 0
    error: str = ""  # почему не вышла прошлая попытка
    uri: str = ""  # к какому дрону подключаемся
    drones: tuple[OnAir, ...] = ()  # из каких выбирать (Stage.CHOOSING), по порядку каналов


class Link(Protocol):
    uri: str

    def connect(self) -> DeckInfo: ...
    def close(self) -> None: ...


_FRIENDLY = (
    ("Too many packets lost", N_("Дрон не отвечает: он включён? Совпадает адрес (uri в config.json)?")),
    ("Cannot find a Crazyradio", N_("Crazyradio не открывается: вставь его заново или закрой cfclient")),
    ("No driver found", N_("Неверный адрес дрона в config.json (uri)")),
    ("Failed to connect to Crazyflie", N_("Дрон не ответил на перезагрузку: он включён?")),
)


def friendly_error(text: str) -> str:
    """Ошибки cflib — понятными словами; незнакомые — первой строкой как есть."""
    for marker, message in _FRIENDLY:
        if marker in text:
            return t(message)
    lines = text.strip().splitlines()
    return lines[0] if lines else t("неизвестная ошибка")


def check_radio() -> Stage | None:
    """Причина, по которой пробовать подключаться бессмысленно; None — радио на месте (или не узнать)."""
    state = radio_usb_state()
    if state is RadioUsb.ABSENT:
        return Stage.NO_RADIO
    if state is RadioUsb.NO_DRIVER:
        return Stage.NO_DRIVER
    return None


def _close_quietly(link: Link) -> None:
    try:
        link.close()
    except Exception:  # связь и так не удалась — главное, не уронить поток подключения
        log.exception("Ошибка при закрытии неудавшейся связи")


def nothing_on_air(uri: str) -> str:
    target = parse_radio_uri(uri)
    address = target.address if target is not None else DEFAULT_ADDRESS
    return t("Ни на одном канале нет дрона с адресом {address:010X} — он включён?", address=address)


class Connector:
    """uri — адрес из настроек. scan(uri) ищет дрон с тем же адресом на всех каналах; None — не искать,
    подключаться прямо по uri. make_link и reboot получают адрес, к которому подключаемся сейчас."""

    def __init__(self, uri: str, make_link: Callable[[str], Link], *,
                 scan: Callable[[str], Sequence[OnAir]] | None = None,
                 check: Callable[[], Stage | None] = check_radio, reboot: Callable[[str], None] | None = None,
                 reboot_first: bool = False, heal: Callable[[], None] = heal_shared_radio,
                 retry_delay: float = RETRY_DELAY, attempt_timeout: float = ATTEMPT_TIMEOUT,
                 scan_timeout: float = SCAN_TIMEOUT, reboot_pause: float = REBOOT_PAUSE,
                 reboot_timeout: float = REBOOT_TIMEOUT, radio_poll: float = RADIO_POLL) -> None:
        self._uri = uri
        self._make_link = make_link
        self._scan = scan
        self._check = check
        self._reboot = reboot
        self._heal = heal
        self._retry_delay = retry_delay
        self._attempt_timeout = attempt_timeout
        self._scan_timeout = scan_timeout
        self._reboot_pause = reboot_pause
        self._reboot_timeout = reboot_timeout
        self._radio_poll = radio_poll
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reboot_requested = threading.Event()
        if reboot_first and reboot is not None:
            self._reboot_requested.set()
        self._status = Status(Stage.REBOOTING if self._reboot_requested.is_set() else Stage.SEARCHING, uri=uri)
        self._result: tuple[Link, DeckInfo] | None = None
        self._thread: threading.Thread | None = None
        self._attempt = 0
        self._error = ""
        self._drones: tuple[OnAir, ...] = ()
        self._chosen: str | None = None
        self._choice_made = threading.Event()

    @property
    def status(self) -> Status:
        with self._lock:
            return self._status

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._result is not None

    @property
    def can_reboot(self) -> bool:
        return self._reboot is not None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="cfpilot-connector", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def request_reboot(self) -> None:
        if self._reboot is not None:
            self._reboot_requested.set()

    def choose(self, uri: str) -> None:
        """Дрон, выбранный из списка (Stage.CHOOSING): следующая попытка — к нему, без поиска."""
        with self._lock:
            self._chosen = uri
        self._choice_made.set()

    def take(self) -> tuple[Link, DeckInfo] | None:
        """Забирает подключённый дрон: закрывать связь дальше — забота взявшего."""
        with self._lock:
            result, self._result = self._result, None
            return result

    def cancel(self) -> None:
        self._stop.set()
        result = self.take()
        if result is not None:
            _close_quietly(result[0])

    def _publish(self, stage: Stage) -> None:
        with self._lock:
            self._status = Status(stage, self._attempt, self._error, self._uri, self._drones)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._step():
                    return
            except Exception:  # поток не должен умирать молча — иначе экран вечно «ищет» и ничего не делает
                log.exception("Сбой в потоке подключения")
                self._error = t("Внутренняя ошибка подключения — пробую снова")
                self._publish(Stage.SEARCHING)
                self._stop.wait(self._retry_delay)

    def _step(self) -> bool:
        """Один шаг: проверка радио, перезагрузка или попытка. True — дрон подключён (или отменили)."""
        problem = self._check()
        if problem is not None:
            self._publish(problem)
            self._stop.wait(self._radio_poll)
            return False
        if self._reboot_requested.is_set():
            self._reboot_now()
            return False
        self._attempt += 1
        if not self._drones:  # пока выбирают дрон, список на экране не мигает «поиском»
            self._publish(Stage.SEARCHING)
        uri = self._target()
        if uri is None:
            self._publish(Stage.CHOOSING if self._drones else Stage.SEARCHING)
            self._wait(self._choice_made, self._retry_delay)  # выбор дрона прерывает паузу
            return False
        self._uri = uri
        self._publish(Stage.SEARCHING)
        outcome = self._try_once()
        if outcome is None:
            self._heal()  # если попытку сорвал умерший поток радио — следующая откроет радио заново
            self._stop.wait(self._retry_delay)
            return False
        with self._lock:
            if not self._stop.is_set():
                self._result = outcome
                self._status = Status(Stage.CONNECTED, self._attempt, uri=self._uri)
                return True
        _close_quietly(outcome[0])  # отменили, пока подключались
        return True

    def _wait(self, done: threading.Event, timeout: float) -> bool:
        """Ждёт done не дольше timeout; False — не дождались или отменили."""
        deadline = time.monotonic() + timeout
        while not done.wait(WAIT_STEP):
            if self._stop.is_set() or time.monotonic() >= deadline:
                return False
        return True

    def _run_limited(self, name: str, work: Callable[[], object], timeout: float) -> dict | None:
        """work в своём потоке: cflib умеет зависнуть навсегда. {"value": …} или {"error": …}; None — не дождались."""
        box: dict = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["value"] = work()
            except Exception as error:  # cflib бросает голый Exception с текстом причины
                box["error"] = friendly_error(str(error) or error.__class__.__name__)
            done.set()

        threading.Thread(target=run, name=name, daemon=True).start()
        return box if self._wait(done, timeout) else None

    def _reboot_now(self) -> None:
        self._reboot_requested.clear()
        self._publish(Stage.REBOOTING)
        result = self._run_limited("cfpilot-reboot", lambda: self._reboot(self._uri), self._reboot_timeout)
        if result is not None:
            self._error = result.get("error", "")
        else:
            self._error = "" if self._stop.is_set() else t("Перезагрузка зависла — открываю радио заново")
        self._heal()
        self._stop.wait(self._reboot_pause)

    def _target(self) -> str | None:
        """Куда подключаться: дрон, выбранный в списке, иначе — по итогам поиска на всех каналах."""
        self._choice_made.clear()  # до чтения выбора: выбор, сделанный между ними, разбудит следующую паузу
        with self._lock:
            chosen, self._chosen = self._chosen, None
        if chosen is not None or self._scan is None:
            self._drones = ()
            return chosen or self._uri
        result = self._run_limited("cfpilot-scan", lambda: tuple(self._scan(self._uri)), self._scan_timeout)
        if result is not None and "error" not in result:
            return self._pick(result["value"])
        self._drones = ()
        if result is not None:
            self._error = result["error"]
        else:
            self._error = "" if self._stop.is_set() else t("Поиск по каналам завис — открываю радио заново")
        self._heal()  # если поиск сорвал умерший поток радио — следующий откроет радио заново
        return None

    def _pick(self, found: Sequence[OnAir]) -> str | None:
        """Свой дрон (адрес из настроек или выбранный раньше) на месте — к нему. Остальные — None и список
        в self._drones: адрес E7E7E7E7E7 у всех дронов с завода, и дрон на другом канале может быть соседским."""
        preferred = parse_radio_uri(self._uri)
        drones = distinct_drones(found, preferred)
        reliable = any(hit.uri == preferred and hit.answers >= RELIABLE for hit in found)
        if reliable or any(drone.uri == preferred for drone in drones):  # отвечает хорошо или лучше «призраков»
            self._drones = ()
            return self._uri
        self._drones = drones
        self._error = "" if drones else nothing_on_air(self._uri)
        return None

    def _try_once(self) -> tuple[Link, DeckInfo] | None:
        """Одна попытка в своём потоке: cflib умеет зависнуть навсегда, а ждать мы готовы не дольше таймаута."""
        link = self._make_link(self._uri)
        box: dict = {}
        done = threading.Event()

        def attempt() -> None:
            try:
                box["decks"] = link.connect()
            except Exception as error:  # cflib бросает голый Exception с текстом причины
                box["error"] = friendly_error(str(error) or error.__class__.__name__)
            with self._lock:
                box["done"] = True
                abandoned = box.get("abandoned", False)
            done.set()
            if abandoned or "error" in box:
                _close_quietly(link)  # брошенная попытка всё-таки дошла до конца — связь уже никому не нужна

        threading.Thread(target=attempt, name="cfpilot-connect", daemon=True).start()
        self._wait(done, self._attempt_timeout)
        with self._lock:  # итог берём под замком: попытка могла закончиться прямо сейчас
            if not box.get("done"):
                box["abandoned"] = True
                self._error = "" if self._stop.is_set() else t("Подключение зависло — открываю радио заново")
                return None
        if "error" in box:
            self._error = box["error"]
            log.info("Попытка %d не удалась: %s", self._attempt, self._error)
            return None
        self._error = ""
        return link, box["decks"]
