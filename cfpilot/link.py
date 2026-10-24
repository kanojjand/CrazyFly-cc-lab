"""Связь с настоящим Crazyflie через Crazyradio (cflib)."""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Sequence

from cfpilot.config import FlightLimits
from cfpilot.flight import (
    Action,
    AttitudeSetpoint,
    FlightMode,
    HoverSetpoint,
    Output,
    Setpoint,
    StopSetpoint,
    ZDistanceSetpoint,
    failsafe_setpoint,
)
from cfpilot.i18n import N_, t

log = logging.getLogger(__name__)

TELEMETRY_PERIOD_MS = 100
ESTIMATOR_RESET_PAUSE = 0.1  # с, как в cflib.utils.reset_estimator
PARAMS_TIMEOUT = 30.0  # с на загрузку параметров при подключении
KEEPALIVE_PERIOD = 0.05  # с — как часто failsafe-поток проверяет главный цикл
KEEPALIVE_AFTER = 0.1  # с тишины главного цикла, после которых шлёт failsafe-поток
# Супервизор прошивки ещё 2 с после последней тяги считает дрон летящим. Если в это время
# команды перестанут приходить (> 0.5 с) или дрон разоружат, он решит, что дрон падает,
# и заблокирует его до перезагрузки («Locked, reboot required»). Поэтому после тяги ещё
# SUPERVISOR_SETTLE с повторяем «стоп» и не разоружаем. Разоружать после посадки и не нужно:
# прошивка делает это сама по таймауту посадки («Landing timeout, disarming»).
SUPERVISOR_SETTLE = 3.0  # с
STOP_REPEAT_PERIOD = 0.1  # с — как часто повторять «стоп», пока супервизор не увидел посадку
SUPERVISOR_TUMBLED = 0x20  # биты лога supervisor.info
SUPERVISOR_LOCKED = 0x40
TELEMETRY_VARS = (
    ("pm.vbat", "float"),
    ("stabilizer.roll", "float"),
    ("stabilizer.pitch", "float"),
    ("stabilizer.yaw", "float"),
    ("supervisor.info", "uint16_t"),
)
ZRANGE_VAR = ("range.zrange", "uint16_t")  # мм, есть только с Flow/Z-ranger
# Высота из оценки положения — настоящая только с Lighthouse/Loco. Без дек это барометр:
# высота над уровнем моря с дрейфом, показывать её как «высоту» нельзя.
POSITION_Z_VAR = ("stateEstimate.z", "float")
# Бросок. В руке и в полёте датчик ускорения показывает ≈ 1 g (вес или тягу), в свободном падении — почти 0.
# «Невесомость» бывает и в руке, если её резко опустить, поэтому бросок — это толчок рукой и сразу после
# него невесомость. Заметить её надо за сотые доли секунды, поэтому — отдельный частый лог.
FALL_VARS = (("acc.x", "float"), ("acc.y", "float"), ("acc.z", "float"))
FALL_PERIOD_MS = 10  # чаще прошивка не шлёт
FREE_FALL_G = 0.3
FREE_FALL_MS = 40  # столько подряд, без пропусков
FALL_GAP_MS = 30  # пакеты пропали дольше — отсчёт невесомости начинаем заново
THROW_PUSH_G = 1.5  # толчок рукой при броске
THROW_PUSH_MS = 500  # невесомость не позже этого после толчка
FALL_STALE = 0.2  # с — данные датчика старше этого не в счёт: лог не запустился или связь подвисла
KALMAN_VARS = (("kalman.varPX", "float"), ("kalman.varPY", "float"), ("kalman.varPZ", "float"))
VARIANCE_SAMPLES = 10  # как в cflib.utils.reset_estimator
VARIANCE_THRESHOLD = 0.001
# Дрон подтверждает каждый пакет (cflib сообщает об этом «качеством связи» сотни раз в секунду).
# Тишина дольше этого — связь мертва, даже если cflib молчит: так бывает, когда радио выдернули.
LINK_SILENCE = 1.5  # с
CLOSE_TIMEOUT = 3.0  # с — дольше связь закрывается, только если cflib завис на мёртвом радио
LINK_LOST_SILENT = N_("Дрон не отвечает — связь потеряна (выключили дрон или выдернули радио?)")

_drivers_ready = False


def init_drivers_once() -> None:
    """cflib.crtp.init_drivers() при повторном вызове регистрирует драйверы ещё раз."""
    global _drivers_ready
    if not _drivers_ready:
        import cflib.crtp

        cflib.crtp.init_drivers()
        _drivers_ready = True


@dataclass(frozen=True)
class Telemetry:
    battery: float | None = None  # В
    height: float | None = None  # м
    roll: float | None = None
    pitch: float | None = None
    yaw: float | None = None
    link_quality: float | None = None  # %
    supervisor: int | None = None  # биты supervisor.info; None — прошивка не сообщает
    thrown: bool | None = None  # дрон подбросили (толчок и невесомость); None — датчик ускорения молчит


@dataclass(frozen=True)
class DeckInfo:
    flow: bool = False
    zranger: bool = False
    lighthouse: bool = False
    loco: bool = False

    def best_mode(self) -> FlightMode:
        if self.flow or self.lighthouse or self.loco:
            return FlightMode.HOVER
        if self.zranger:
            return FlightMode.ALTHOLD
        return FlightMode.MANUAL

    def supports(self, mode: FlightMode) -> bool:
        if mode is FlightMode.HOVER:
            return self.flow or self.lighthouse or self.loco
        if mode is FlightMode.ALTHOLD:
            return self.flow or self.zranger or self.lighthouse or self.loco
        return True

    def describe(self) -> str:
        names = [
            name
            for name, present in (
                ("Flow", self.flow),
                ("Z-ranger", self.zranger),
                ("Lighthouse", self.lighthouse),
                ("Loco", self.loco),
            )
            if present
        ]
        return ", ".join(names) if names else t("нет")


_DECK_PARAMS = {
    "flow": ("deck.bcFlow2", "deck.bcFlow"),
    "zranger": ("deck.bcZRanger2", "deck.bcZRanger"),
    "lighthouse": ("deck.bcLighthouse4",),
    "loco": ("deck.bcLoco",),
}


def detect_decks(read_param: Callable[[str], str | None]) -> DeckInfo:
    """read_param возвращает значение параметра строкой или None, если его нет."""

    def present(names: tuple[str, ...]) -> bool:
        return any(read_param(name) not in (None, "0", 0) for name in names)

    return DeckInfo(**{deck: present(names) for deck, names in _DECK_PARAMS.items()})


def telemetry_from_log(data: dict, previous: Telemetry) -> Telemetry:
    zrange = data.get("range.zrange")
    height = zrange / 1000.0 if zrange is not None else data.get("stateEstimate.z")
    return replace(
        previous,
        battery=data.get("pm.vbat", previous.battery),
        height=height if height is not None else previous.height,
        roll=data.get("stabilizer.roll", previous.roll),
        pitch=data.get("stabilizer.pitch", previous.pitch),
        yaw=data.get("stabilizer.yaw", previous.yaw),
        supervisor=data.get("supervisor.info", previous.supervisor),
    )


@dataclass(frozen=True)
class FallSense:
    """Что датчик ускорения говорил о броске; всё — мс по часам дрона."""

    last: int | None = None  # последний отсчёт
    weightless_since: int | None = None  # начало невесомости без пропусков
    pushed: int | None = None  # последний толчок рукой

    @property
    def thrown(self) -> bool:
        if self.last is None or self.weightless_since is None or self.pushed is None:
            return False
        return (self.last - self.weightless_since >= FREE_FALL_MS
                and self.weightless_since - self.pushed <= THROW_PUSH_MS)


def sense_fall(previous: FallSense, timestamp: int, acc: tuple[float, float, float]) -> FallSense:
    g = math.hypot(*acc)
    unbroken = previous.last is not None and timestamp - previous.last <= FALL_GAP_MS
    since = None
    if g < FREE_FALL_G:
        since = previous.weightless_since if unbroken and previous.weightless_since is not None else timestamp
    pushed = timestamp if g >= THROW_PUSH_G else previous.pushed
    return FallSense(last=timestamp, weightless_since=since, pushed=pushed)


def supervisor_problem(info: int | None) -> str:
    """Почему прошивка не даст взлететь. Пустая строка — всё в порядке (или прошивка не сообщает).

    «Авария» (бит 0x80) сюда не входит: если дрон стоит ровно, её снимает запрос recovery при взлёте.
    """
    if info is None:
        return ""
    if info & SUPERVISOR_LOCKED:
        return t("Дрон заблокирован прошивкой — нажми R: перезагружу его по радио и подключусь снова")
    if info & SUPERVISOR_TUMBLED:
        return t("Дрон перевёрнут или стоит криво — поставь его ровно")
    return ""


def variance_settled(history: Sequence[tuple[float, ...]]) -> bool:
    """Оценщик нашёл положение: дисперсии X/Y/Z перестали меняться (логика cflib)."""
    if len(history) < VARIANCE_SAMPLES:
        return False
    recent = history[-VARIANCE_SAMPLES:]
    return all(max(column) - min(column) < VARIANCE_THRESHOLD for column in zip(*recent))


def _reset_estimator(cf, sleep: Callable[[float], None]) -> None:
    try:
        cf.param.set_value("kalman.resetEstimation", "1")
        sleep(ESTIMATOR_RESET_PAUSE)
        cf.param.set_value("kalman.resetEstimation", "0")
    except (KeyError, AttributeError) as error:
        log.warning("Не удалось сбросить оценщик положения: %s", error)


def _send_setpoint(commander, setpoint: Setpoint) -> None:
    if isinstance(setpoint, HoverSetpoint):
        commander.send_hover_setpoint(setpoint.vx, setpoint.vy, setpoint.yawrate, setpoint.z)
    elif isinstance(setpoint, ZDistanceSetpoint):
        commander.send_zdistance_setpoint(setpoint.roll, setpoint.pitch, setpoint.yawrate, setpoint.z)
    elif isinstance(setpoint, AttitudeSetpoint):
        # Legacy RPYT is supported by older firmware too. The onboard flightmode
        # parameters select angles or rates before arming. Zero thrust stays zero.
        commander.send_setpoint(setpoint.roll, setpoint.pitch, setpoint.yawrate, setpoint.thrust)
    elif isinstance(setpoint, StopSetpoint):
        commander.send_stop_setpoint()
    else:
        raise TypeError(f"Неизвестный setpoint: {setpoint!r}")


def _gives_lift(setpoint: Setpoint) -> bool:
    if isinstance(setpoint, AttitudeSetpoint):
        return setpoint.thrust > 0
    return not isinstance(setpoint, StopSetpoint)


RATE_PARAMS = ("flightmode.stabModeRoll", "flightmode.stabModePitch")


def _stab_mode(rate: bool) -> str:
    return "0" if rate else "1"  # 0 — угловая скорость, 1 — угол


def _firmware_rate(cf) -> bool | None:
    """Как прошивка сейчас понимает крен/тангаж (по подтверждённым параметрам): True — скорости, None — вперемешку."""
    values = {str(cf.param.get_value(name)) for name in RATE_PARAMS}
    if len(values) != 1:
        return None
    return values == {_stab_mode(True)}


def _configure_attitude(cf, rate: bool, sleep: Callable[[float], None]) -> None:
    """Configure legacy RPYT on the ground; never arm with unconfirmed modes."""
    settings = {
        "flightmode.althold": "0",
        "flightmode.poshold": "0",
        "flightmode.posSet": "0",
        "flightmode.stabModeRoll": _stab_mode(rate),
        "flightmode.stabModePitch": _stab_mode(rate),
        "flightmode.stabModeYaw": "0",
    }
    cf.commander.send_setpoint(0.0, 0.0, 0.0, 0)
    try:
        for name, expected in settings.items():
            if str(cf.param.get_value(name)) == expected:
                continue
            cf.param.set_value(name, expected)
            for _ in range(50):
                if str(cf.param.get_value(name)) == expected:
                    break
                sleep(0.01)
            else:
                raise RuntimeError(t("Дрон не подтвердил параметр {name} — арминг отменён", name=name))
    except Exception:
        cf.commander.send_stop_setpoint()
        raise


def execute_output(cf, output: Output, sleep: Callable[[float], None] = time.sleep) -> None:
    """Выполняет действия (арминг, сброс оценщика) и шлёт setpoint, если он есть."""
    for action in output.actions:
        if action in (Action.ARM, Action.REARM):
            if action is Action.ARM and isinstance(output.setpoint, AttitudeSetpoint):
                _configure_attitude(cf, output.setpoint.rate, sleep)
            # Упал после аварийной остановки, но стоит ровно — снимаем «аварию», иначе не взлетит.
            cf.supervisor.send_crash_recovery_request()
            cf.supervisor.send_arming_request(True)
        elif action is Action.DISARM:
            cf.supervisor.send_arming_request(False)
        elif action is Action.RESET_ESTIMATOR:
            _reset_estimator(cf, sleep)
    if output.setpoint is not None:
        _send_setpoint(cf.commander, output.setpoint)


def radio_status() -> str:
    import cflib.crtp

    init_drivers_once()
    return "\n".join(f"{name}: {status}" for name, status in cflib.crtp.get_interfaces_status().items())


def heal_shared_radio(manager=None) -> None:
    """Убирает умерший общий поток радио из cflib.

    cflib держит один поток на Crazyradio. Если радио выдернули или USB дал сбой, поток умирает молча,
    а все следующие подключения ждут его ответа вечно — отсюда «помогает только перезапуск программы».
    Без мёртвого потока следующее подключение просто откроет радио заново.
    """
    if manager is None:
        from cflib.crtp.radiodriver import RadioManager as manager
    radios, lock = getattr(manager, "_radios", None), getattr(manager, "_lock", None)
    if radios is None or lock is None:  # другая версия cflib — лечить нечего
        return
    with lock:
        for index, shared in enumerate(radios):
            if shared is None or shared.is_alive():
                continue
            log.warning("Поток радио %d умер — открою радио заново", index)
            with contextlib.suppress(Exception):  # радио могли выдернуть — закрываем «по возможности»
                if shared._radio is not None:
                    shared._radio.close()
            radios[index] = None


def reboot_drone(uri: str) -> None:
    """Перезагружает дрон по радио — как кнопкой питания, только не вставая с места.

    Снимает блокировку прошивки («Locked, reboot required»). Связь с дроном перед этим должна быть закрыта.
    Бросает исключение, если дрон не ответил (выключен, другой адрес).
    """
    from cflib.utils.power_switch import PowerSwitch

    init_drivers_once()
    switch = PowerSwitch(uri)
    try:
        switch.stm_power_cycle()
    finally:
        switch.close()


def close_link_with_timeout(scf, timeout: float = CLOSE_TIMEOUT) -> None:
    """cflib ждёт закрытия связи без таймаута, а на мёртвом радио не дождётся. Ждём сами — и бросаем."""

    def close() -> None:
        try:
            scf.close_link()
        except Exception:  # cflib бросает голый Exception; связь всё равно уже не наша
            log.exception("Ошибка при закрытии связи")

    worker = threading.Thread(target=close, name="cfpilot-close", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        log.warning("Связь не закрылась за %.0f с — бросаю её", timeout)
        heal_shared_radio()


class CrazyflieLink:
    def __init__(self, uri: str, limits: FlightLimits, cache_dir: str = "./cache",
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.uri = uri
        self._limits = limits
        self._cache_dir = cache_dir
        self._clock = clock
        self._scf = None
        self._lock = threading.Lock()  # телеметрия (пишет поток cflib)
        self._send_lock = threading.Lock()  # отправка (главный цикл + failsafe-поток)
        self._telemetry = Telemetry()
        self._error: str | None = None
        self._params_ready = threading.Event()
        self._needs_position = False
        self._variances: deque = deque(maxlen=VARIANCE_SAMPLES)
        self._fall = FallSense()
        self._fall_heard: float | None = None  # когда пришёл последний отсчёт датчика ускорения (часы ПК)
        self._last_setpoint: Setpoint | None = None
        self._last_update = 0.0
        self._last_sent = 0.0  # когда реально ушёл последний setpoint
        self._last_lift: float | None = None  # когда ушла последняя команда с тягой
        self._last_heard: float | None = None  # когда дрон последний раз подтвердил пакет
        self._failsafe_stopped = False
        self._rate_mode: bool | None = None  # крен/тангаж в прошивке: True — скорости (подтверждено)
        self._rate_requested: bool | None = None  # какой режим последним записали в параметры
        self._stop_keepalive = threading.Event()
        self._keepalive: threading.Thread | None = None

    @property
    def telemetry(self) -> Telemetry:
        with self._lock:
            telemetry, heard = self._telemetry, self._fall_heard
        if heard is None or self._clock() - heard > FALL_STALE:
            return replace(telemetry, thrown=None)  # старый отсчёт «падает» не должен запустить моторы
        return telemetry

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def position_ready(self) -> bool:
        if not self._needs_position:
            return True
        with self._lock:
            return variance_settled(tuple(self._variances))

    @property
    def rate_ready(self) -> bool:
        """Прошивка понимает крен/тангаж как угловые скорости — можно крутить сальто."""
        return self._rate_mode is True

    @property
    def failsafe_stopped(self) -> bool:
        """True — failsafe сам посадил дрон, пока главный цикл молчал."""
        return self._failsafe_stopped

    def connect(self) -> DeckInfo:
        """Блокирующее подключение; бросает исключение, если дрон не ответил."""
        from cflib.crazyflie import Crazyflie
        from cflib.crazyflie.syncCrazyflie import SyncCrazyflie

        init_drivers_once()
        cf = Crazyflie(rw_cache=self._cache_dir)
        cf.connection_lost.add_callback(self._on_connection_lost)
        cf.fully_connected.add_callback(lambda uri: self._params_ready.set())
        cf.link_statistics.link_quality_updated.add_callback(self._on_link_quality)
        scf = SyncCrazyflie(self.uri, cf=cf)
        scf.open_link()
        self._scf = scf
        if not self._params_ready.wait(PARAMS_TIMEOUT) or self._error is not None:
            raise ConnectionError(self._error or t("Дрон не прислал параметры — перезапусти его и попробуй снова"))
        decks = detect_decks(self._read_param)
        height_vars = ((ZRANGE_VAR,) if decks.flow or decks.zranger else ()) + (
            (POSITION_Z_VAR,) if decks.lighthouse or decks.loco else ())
        self._start_log(cf, "cfpilot", TELEMETRY_VARS + height_vars, self._on_log_data)
        self._start_log(cf, "cfpilot-fall", FALL_VARS, self._on_fall_data, FALL_PERIOD_MS)
        if decks.lighthouse or decks.loco:
            self._needs_position = self._start_log(cf, "cfpilot-kalman", KALMAN_VARS, self._on_variance)
        self._keepalive = threading.Thread(target=self._keepalive_loop, name="cfpilot-failsafe", daemon=True)
        self._keepalive.start()
        return decks

    def execute(self, output: Output) -> None:
        if self._scf is None or self._error is not None:
            return
        if Action.RESET_ESTIMATOR in output.actions:
            with self._lock:
                self._variances.clear()
        with self._send_lock:
            now = self._clock()
            if Action.ARM in output.actions:
                self._failsafe_stopped = False
            setpoint = output.setpoint
            if self._failsafe_stopped and not isinstance(setpoint, StopSetpoint):
                setpoint = None  # failsafe уже посадил дрон — без нового взлёта моторы не крутим
            actions = output.actions
            arming = Action.ARM in actions
            if not arming:
                setpoint = self._match_attitude_mode(setpoint)
            if self._settling(now):  # disarm сейчас = блокировка до перезагрузки (см. SUPERVISOR_SETTLE)
                actions = tuple(action for action in actions if action is not Action.DISARM)
            execute_output(self._scf.cf, replace(output, setpoint=setpoint, actions=actions))
            if arming and isinstance(setpoint, AttitudeSetpoint):  # execute_output настроил и проверил режим
                self._rate_mode = self._rate_requested = setpoint.rate
            if setpoint is not None:
                self._last_setpoint = setpoint
                self._mark_sent(setpoint, now)
            self._last_update = now

    def _match_attitude_mode(self, setpoint: Setpoint | None) -> Setpoint | None:
        """Смена углы <-> скорости в полёте (сальто): параметры пишутся асинхронно, по радио.

        Пока прошивка не подтвердила новый режим, шлём крен и тангаж 0: в режиме углов это «ровно»,
        в режиме скоростей — «не вращайся». Так любой пакет безопасен, в каком бы режиме ни была прошивка.
        """
        if not isinstance(setpoint, AttitudeSetpoint) or self._rate_mode is None:
            return setpoint
        cf = self._scf.cf
        try:
            if self._rate_requested != setpoint.rate:
                for name in RATE_PARAMS:
                    cf.param.set_value(name, _stab_mode(setpoint.rate))
                self._rate_requested = setpoint.rate
            confirmed = _firmware_rate(cf)
        except (KeyError, AttributeError) as error:
            log.warning("Не удалось сменить режим крена/тангажа: %s", error)
            confirmed = None
        if confirmed is not None:
            self._rate_mode = confirmed
        if confirmed == setpoint.rate:
            return setpoint
        return replace(setpoint, roll=0.0, pitch=0.0, rate=self._rate_mode)

    def close(self) -> None:
        self._stop_keepalive.set()
        if self._keepalive is not None:
            self._keepalive.join(timeout=1.0)
        if self._scf is None:
            return
        try:
            if self._error is None:
                with self._send_lock:  # на случай, если failsafe-поток не успел завершиться
                    self._stop_and_settle()
        finally:
            with self._send_lock:  # failsafe-поток, переживший join, не должен увидеть полузакрытую связь
                scf, self._scf = self._scf, None
            close_link_with_timeout(scf)

    def _stop_and_settle(self) -> None:
        """Глушим моторы и ждём, пока прошивка зафиксирует посадку, — иначе она заблокирует дрон."""
        commander = self._scf.cf.commander
        commander.send_stop_setpoint()
        # Число повторов считаем заранее: ожидание не должно зависеть от того, идут ли часы.
        left = SUPERVISOR_SETTLE - (self._clock() - self._last_lift) if self._last_lift is not None else 0.0
        for _ in range(math.ceil(max(0.0, left) / STOP_REPEAT_PERIOD)):
            time.sleep(STOP_REPEAT_PERIOD)
            commander.send_stop_setpoint()
        commander.send_notify_setpoint_stop()
        time.sleep(0.1)  # очередь пакетов не сбрасывается при закрытии

    def keepalive_tick(self, now: float) -> None:
        """Если главный цикл молчит — шлём failsafe-команду (зависнуть, потом сесть).

        После остановки моторов ещё повторяем «стоп», пока прошивка не увидит посадку.
        """
        with self._send_lock:
            if self._scf is None or self._error is not None:
                return
            if self._last_heard is not None and now - self._last_heard > LINK_SILENCE:
                self._error = t(LINK_LOST_SILENT)  # слать уже некуда; главный цикл увидит ошибку и переподключится
                return
            last = self._last_setpoint
            if isinstance(last, StopSetpoint):
                if self._settling(now) and now - self._last_sent >= STOP_REPEAT_PERIOD:
                    _send_setpoint(self._scf.cf.commander, last)
                    self._last_sent = now
                return
            stale = now - self._last_update
            if last is None or stale < KEEPALIVE_AFTER:
                return
            setpoint = failsafe_setpoint(last, stale, self._limits)
            _send_setpoint(self._scf.cf.commander, setpoint)
            self._mark_sent(setpoint, now)
            if isinstance(setpoint, StopSetpoint):
                self._failsafe_stopped = True
                self._last_setpoint = setpoint

    def _mark_sent(self, setpoint: Setpoint, now: float) -> None:
        self._last_sent = now
        if _gives_lift(setpoint):
            self._last_lift = now

    def _settling(self, now: float) -> bool:
        """Прошивка, возможно, ещё считает дрон летящим (см. SUPERVISOR_SETTLE)."""
        return self._last_lift is not None and now - self._last_lift < SUPERVISOR_SETTLE

    def _keepalive_loop(self) -> None:
        while not self._stop_keepalive.wait(KEEPALIVE_PERIOD):
            try:
                self.keepalive_tick(self._clock())
            except Exception:  # поток безопасности не должен умирать молча
                log.exception("Ошибка в failsafe-потоке")

    def _read_param(self, name: str) -> str | None:
        try:
            return self._scf.cf.param.get_value(name)
        except KeyError:
            return None

    def _start_log(self, cf, name: str, variables, callback, period_ms: int = TELEMETRY_PERIOD_MS) -> bool:
        from cflib.crazyflie.log import LogConfig

        config = LogConfig(name=name, period_in_ms=period_ms)
        for variable, fetch_as in variables:
            if cf.log.toc.get_element_by_complete_name(variable) is not None:
                config.add_variable(variable, fetch_as)
        if not config.variables:
            return False
        cf.log.add_config(config)
        config.data_received_cb.add_callback(callback)
        config.error_cb.add_callback(lambda conf, msg: log.warning("Ошибка лога %s: %s", conf.name, msg))
        config.start()
        return True

    def _on_log_data(self, timestamp, data, config) -> None:
        with self._lock:
            self._telemetry = telemetry_from_log(data, self._telemetry)

    def _on_fall_data(self, timestamp, data, config) -> None:
        acc = tuple(data.get(name) for name, _ in FALL_VARS)
        if None in acc:  # без какой-то оси «невесомость» не отличить от лежания на боку
            return
        with self._lock:
            self._fall = sense_fall(self._fall, timestamp, acc)
            self._fall_heard = self._clock()
            self._telemetry = replace(self._telemetry, thrown=self._fall.thrown)

    def _on_variance(self, timestamp, data, config) -> None:
        with self._lock:
            self._variances.append(tuple(data.get(name, 0.0) for name, _ in KALMAN_VARS))

    def _on_link_quality(self, quality: float) -> None:
        self._last_heard = self._clock()
        with self._lock:
            self._telemetry = replace(self._telemetry, link_quality=quality)

    def _on_connection_lost(self, uri: str, message: str) -> None:
        self._error = t("Связь с дроном потеряна: {message}", message=message)
        self._params_ready.set()  # не ждать параметры до таймаута
