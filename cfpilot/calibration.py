"""Мастер калибровки пульта: какой это пульт, ход осей, какая ось — какой стик, какие кнопки — какие команды.

Логика без pygame: хост (экран подключения или полёта) раз в кадр зовёт update(dt),
клавиши переводит в confirm() / skip() / back() / cancel(), а рисует по view().
Пульт определяется сам: стик или кнопку двигают — программа смотрит, что изменилось.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence, Union

from cfpilot.config import MIN_AXIS_RANGE, AxisMap, AxisRange, Binding, Bindings, ControllerProfile, InvertMap
from cfpilot.i18n import N_, t

STILL = 0.03  # изменение оси за кадр меньше этого — пульт не трогают
CENTER_HOLD = 1.2  # с — столько пульт должен лежать спокойно, чтобы запомнить положение покоя
AXIS_DETECT = 0.5  # ось ушла от положения в начале шага дальше этого — значит, её двигают
AXIS_HOLD = 0.4  # с — подержать стик в упоре, чтобы ось засчиталась
SWITCH_HOLD = 0.15  # с — тумблер на оси должен постоять в новом положении
MIN_HALF_RANGE = 0.3  # пружинный стик должен пройти хотя бы столько в каждую сторону от центра
MIN_THROTTLE_RANGE = 1.0  # газ — хотя бы половину полного хода
MIN_MOVED_AXES = 3  # газ, вперёд/назад, влево/вправо — без них лететь нечем


class Joystick(Protocol):
    def get_name(self) -> str: ...
    def get_instance_id(self) -> int: ...
    def get_numaxes(self) -> int: ...
    def get_axis(self, index: int) -> float: ...
    def get_numbuttons(self) -> int: ...
    def get_button(self, index: int) -> bool: ...


@dataclass(frozen=True)
class AxisStep:
    role: str  # поле AxisMap / InvertMap
    title: str
    instruction: str
    optional: bool = False


@dataclass(frozen=True)
class ButtonStep:
    action: str  # поле Bindings
    title: str
    instruction: str


AXIS_STEPS = (
    AxisStep("throttle", N_("Газ"), N_("Подними стик газа ВВЕРХ до упора и подержи. "
                                       "Если он уже вверху — опусти и подними снова.")),
    AxisStep("pitch", N_("Вперёд"), N_("Толкни стик наклона ВПЕРЁД (от себя) до упора и подержи.")),
    AxisStep("roll", N_("Вправо"), N_("Отклони стик наклона ВПРАВО до упора и подержи.")),
    AxisStep("yaw", N_("Поворот"), N_("Отклони стик поворота ВПРАВО до упора и подержи. "
                                      "Если поворачивать будешь кнопками — Backspace."), optional=True),
)
BUTTON_STEPS = (
    ButtonStep("takeoff", N_("Взлёт"), N_("Нажми кнопку или щёлкни тумблер для ВЗЛЁТА.")),
    ButtonStep("land", N_("Посадка"), N_("Нажми кнопку или щёлкни тумблер для ПОСАДКИ.")),
    ButtonStep("kill", N_("Аварийный стоп"), N_("Кнопка или тумблер, который мгновенно ГЛУШИТ МОТОРЫ. "
                                                "Очень советую назначить.")),
    ButtonStep("hold", N_("Удержание газа"), N_("Кнопка удержания газа (режим manual): нажал — газ стоит.")),
    ButtonStep("yaw_left", N_("Поворот влево"), N_("Кнопка поворота влево, пока зажата.")),
    ButtonStep("yaw_right", N_("Поворот вправо"), N_("Кнопка поворота вправо, пока зажата.")),
    ButtonStep("throw", N_("Бросок"), N_("Кнопка броска: нажал — подбрасываешь дрон, он сам ловится в воздухе.")),
    ButtonStep("flip_forward", N_("Сальто вперёд"), N_("Кнопка САЛЬТО 360° ВПЕРЁД. На DualSense — стрелка ↑.")),
    ButtonStep("flip_backward", N_("Сальто назад"), N_("Кнопка САЛЬТО 360° НАЗАД. На DualSense — стрелка ↓.")),
    ButtonStep("flip_left", N_("Бочка влево"), N_("Кнопка БОЧКИ 360° ВЛЕВО. На DualSense — стрелка ←.")),
    ButtonStep("flip_right", N_("Бочка вправо"), N_("Кнопка БОЧКИ 360° ВПРАВО. На DualSense — стрелка →.")),
    ButtonStep("dash_forward", N_("Рывок 45° вперёд"), N_("Курок РЫВКА: пока зажат — дрон наклонён на 45° вперёд "
                                                         "и разгоняется. На DualSense — R2.")),
    ButtonStep("dash_backward", N_("Рывок 45° назад"), N_("Курок рывка НАЗАД (наклон 45° назад, торможение). "
                                                         "На DualSense — L2.")),
)
CHOOSE, CENTER, RANGE, SUMMARY = "choose", "center", "range", "summary"
Step = Union[str, AxisStep, ButtonStep]
STEPS: tuple[Step, ...] = (CHOOSE, CENTER, RANGE) + AXIS_STEPS + BUTTON_STEPS + (SUMMARY,)
BACK, CANCEL = ("←", N_("назад")), ("Esc", N_("отмена"))
_TITLES = {step.action: step.title for step in BUTTON_STEPS}


@dataclass(frozen=True)
class CalibrationView:
    controller: str
    step: int  # 1..total
    total: int
    title: str
    instruction: str
    keys: tuple[tuple[str, str], ...]  # подсказки: (клавиша, что делает)
    progress: float | None = None  # 0..1 — сколько ещё держать
    axes: tuple[float, ...] = ()
    ranges: tuple[tuple[float, float], ...] = ()  # (low, high) каждой оси за калибровку
    active_axis: int | None = None
    buttons: tuple[bool, ...] = ()
    done: tuple[tuple[str, str], ...] = ()  # уже назначено: (что, чем)
    message: str = ""
    controllers: tuple[str, ...] = ()


def describe_binding(binding: Binding) -> str:
    if binding.button is not None:
        return t("кнопка {button}", button=binding.button)
    if binding.above is not None:
        return t("ось {axis} > {threshold:+.1f}", axis=binding.axis, threshold=binding.above)
    return t("ось {axis} < {threshold:+.1f}", axis=binding.axis, threshold=binding.below)


def read_buttons(joystick: Joystick) -> tuple[bool, ...]:
    """Кнопки пульта, а за ними — крестовины (hat) как 4 кнопки каждая: ↑ ↓ ← →.

    DualSense в pygame отдаёт стрелки кнопками 11..14, но другие пульты (и DualSense без HIDAPI) —
    крестовиной; так стрелки назначаются в мастере одинаково.
    """
    buttons = tuple(bool(joystick.get_button(i)) for i in range(joystick.get_numbuttons()))
    hats = getattr(joystick, "get_numhats", None)
    for index in range(hats() if hats is not None else 0):
        x, y = joystick.get_hat(index)
        buttons += (y > 0, y < 0, x < 0, x > 0)
    return buttons


def _snapshot(joystick: Joystick) -> tuple[tuple[float, ...], tuple[bool, ...]]:
    # pygame отдаёт -32768/32767, то есть чуть меньше -1 — подрезаем.
    axes = tuple(max(-1.0, min(1.0, joystick.get_axis(i))) for i in range(joystick.get_numaxes()))
    return axes, read_buttons(joystick)


def _newly_pressed(buttons: Sequence[bool], before: Sequence[bool]) -> int | None:
    for index, pressed in enumerate(buttons):
        if pressed and not (index < len(before) and before[index]):
            return index
    return None


def _strongest_move(axes: Sequence[float], before: Sequence[float], skip: set[int]) -> tuple[int, float] | None:
    """Ось, ушедшая дальше всех от положения `before` (и не меньше AXIS_DETECT): (номер, смещение)."""
    best = None
    for index, value in enumerate(axes[:len(before)]):
        delta = value - before[index]
        if index not in skip and abs(delta) >= AXIS_DETECT and (best is None or abs(delta) > abs(best[1])):
            best = (index, delta)
    return best


class Calibrator:
    def __init__(self, list_joysticks: Callable[[], Sequence[Joystick]]) -> None:
        self._list_joysticks = list_joysticks
        self.joystick: Joystick | None = None
        self.finished = False
        self.cancelled = False
        self._index = 0
        self._axes: tuple[float, ...] = ()
        self._buttons: tuple[bool, ...] = ()
        self._previous: tuple[float, ...] = ()
        self._start_axes: tuple[float, ...] = ()  # положение в начале шага: от него ищем движение
        self._start_buttons: tuple[bool, ...] = ()
        self._center: tuple[float, ...] = ()
        self._low: list[float] = []
        self._high: list[float] = []
        self._roles: dict[str, tuple[int, bool]] = {}  # роль -> (ось, инверсия)
        self._bindings: dict[str, Binding] = {}
        self._candidate = None
        self._timer = 0.0
        self._message = ""
        self._choices: tuple[str, ...] = ()
        self._pick_start: dict[int, tuple] = {}  # пульт -> положение, когда его увидели на шаге выбора
        self._rested: set[int] = set()  # оси, побывавшие в покое за текущий шаг

    @property
    def step(self) -> Step:
        return STEPS[self._index]

    @property
    def result(self) -> tuple[str, ControllerProfile] | None:
        if not self.finished or self.joystick is None:
            return None
        return self.joystick.get_name(), self.profile()

    # ---------- клавиши ----------

    def confirm(self) -> None:
        step = self.step
        if step == CHOOSE:
            joysticks = list(self._list_joysticks())
            if joysticks:
                self._select(joysticks[0])
        elif step == CENTER and self._axes:
            self._capture_center()
        elif step == RANGE:
            self._finish_range()
        elif step == SUMMARY:
            self.finished = True

    def skip(self) -> None:
        step = self.step
        if isinstance(step, AxisStep) and step.optional:
            self._roles.pop(step.role, None)
            self._enter(self._index + 1)
        elif isinstance(step, ButtonStep):
            self._bindings.pop(step.action, None)
            self._enter(self._index + 1)
        else:
            self._message = t("Этот шаг пропустить нельзя")

    def back(self) -> None:
        if self._index > STEPS.index(CENTER):
            self._enter(self._index - 1)

    def cancel(self) -> None:
        self.cancelled = True

    def controller_removed(self, instance_id: int) -> None:
        if self.joystick is not None and self.joystick.get_instance_id() == instance_id:
            self.controller_lost()

    def controller_lost(self) -> None:
        """Пульт пропал (отключили или он перестал отвечать) — начинаем с выбора пульта."""
        self.joystick = None
        self._pick_start = {}
        self._index = 0
        self._message = t("Пульт отключился — подключи его снова")

    # ---------- кадр ----------

    def update(self, dt: float) -> None:
        if self.finished or self.cancelled:
            return
        if self.joystick is None:
            self._update_choose()
            return
        self._previous = self._axes
        try:
            self._axes, self._buttons = _snapshot(self.joystick)
        except RuntimeError:  # pygame.error: устройство закрыто, а событие об отключении не дошло
            self.controller_lost()
            return
        # Кнопка, зажатая с начала шага, перестаёт быть «старой», как только её отпустили.
        self._start_buttons = tuple(before and now for before, now in zip(self._start_buttons, self._buttons))
        if self._center:
            self._low = [min(low, value) for low, value in zip(self._low, self._axes)]
            self._high = [max(high, value) for high, value in zip(self._high, self._axes)]
        step = self.step
        if step == CENTER:
            self._update_center(dt)
        elif step == RANGE:
            if _newly_pressed(self._buttons, self._start_buttons) is not None:
                self._finish_range()
        elif isinstance(step, AxisStep):
            self._update_axis(step, dt)
        elif isinstance(step, ButtonStep):
            self._update_button(step, dt)
        elif step == SUMMARY:
            if _newly_pressed(self._buttons, self._start_buttons) is not None:
                self.finished = True

    def _update_choose(self) -> None:
        joysticks = list(self._list_joysticks())
        self._choices = tuple(joystick.get_name() for joystick in joysticks)
        if len(joysticks) == 1:
            self._select(joysticks[0])
            return
        # Несколько пультов: какой тронули — тот и настраиваем.
        for joystick in joysticks:
            axes, buttons = _snapshot(joystick)
            start_axes, start_buttons = self._pick_start.setdefault(joystick.get_instance_id(), (axes, buttons))
            if _newly_pressed(buttons, start_buttons) is not None or _strongest_move(axes, start_axes, set()):
                self._select(joystick)
                return

    def _select(self, joystick: Joystick) -> None:
        self.joystick = joystick
        self._axes, self._buttons = _snapshot(joystick)
        self._center, self._low, self._high = (), [], []
        self._roles, self._bindings = {}, {}
        self._enter(STEPS.index(CENTER))

    def _enter(self, index: int) -> None:
        self._index = index
        self._start_axes, self._start_buttons = self._axes, self._buttons
        self._candidate = None
        self._timer = 0.0
        self._message = ""
        self._rested = set()
        step = self.step
        if isinstance(step, AxisStep):
            self._roles.pop(step.role, None)
        elif isinstance(step, ButtonStep):
            self._bindings.pop(step.action, None)

    def _update_center(self, dt: float) -> None:
        moving = len(self._previous) != len(self._axes) or any(
            abs(now - before) > STILL for now, before in zip(self._axes, self._previous))
        self._timer = 0.0 if moving else self._timer + dt
        if self._timer >= CENTER_HOLD:
            self._capture_center()

    def _capture_center(self) -> None:
        self._center = self._axes
        self._low, self._high = list(self._axes), list(self._axes)
        self._enter(STEPS.index(RANGE))

    def _finish_range(self) -> None:
        moved = sum(1 for low, high in zip(self._low, self._high) if high - low >= MIN_AXIS_RANGE)
        if moved < MIN_MOVED_AXES:
            self._message = t("Подвигай все стики до упора — пока вижу движение только у {moved} осей", moved=moved)
            self._start_buttons = self._buttons
            return
        self._enter(self._index + 1)

    def _update_axis(self, step: AxisStep, dt: float) -> None:
        # Отклонение считаем от покоя, а не от начала шага: газ без пружины могли оставить вверху.
        # Но ось засчитываем, только если в этом шаге она побывала в покое: так тумблер, переключённый
        # после калибровки центра, не примут за стик, а газ, оставленный вверху, — за перевёрнутый.
        for index, (value, rest) in enumerate(zip(self._axes, self._center)):
            if abs(value - rest) < AXIS_DETECT / 2:
                self._rested.add(index)
        skip = {axis for axis, _ in self._roles.values()} | (set(range(len(self._center))) - self._rested)
        move = _strongest_move(self._axes, self._center, skip)
        if move is None:
            self._candidate, self._timer = None, 0.0
            return
        candidate = (move[0], move[1] < 0)  # стик двигают в «плюс»: ушёл в минус — ось перевёрнута
        if candidate != self._candidate:
            self._candidate, self._timer = candidate, 0.0
            return
        self._timer += dt
        if self._timer < AXIS_HOLD:
            return
        problem = self._range_problem(candidate[0], step.role)
        if problem:
            self._message, self._timer = problem, 0.0
            return
        self._roles[step.role] = candidate
        self._enter(self._index + 1)

    def _range_problem(self, axis: int, role: str) -> str:
        """Калибровку стика, который не довели до упора, сохранять нельзя: он не дал бы полного хода."""
        low, high, center = self._low[axis], self._high[axis], self._center[axis]
        if role == "throttle":
            if high - low >= MIN_THROTTLE_RANGE:
                return ""
            return t("Проведи газ от самого низа до самого верха, потом снова подними и подержи")
        if center - low < MIN_HALF_RANGE or high - center < MIN_HALF_RANGE:
            return t("Покрути этот стик до упора во все стороны, потом снова отклони и подержи")
        return ""

    def _update_button(self, step: ButtonStep, dt: float) -> None:
        button = _newly_pressed(self._buttons, self._start_buttons)
        if button is not None:
            self._offer(step, Binding(button=button))
            return
        # Тумблеры EdgeTX приходят осями: ищем ось (не стик), которая сдвинулась и стоит.
        sticks = {axis for axis, _ in self._roles.values()}
        move = _strongest_move(self._axes, self._start_axes, sticks)
        if move is None:
            self._candidate, self._timer = None, 0.0
            return
        axis, delta = move
        threshold = round(self._start_axes[axis] + delta / 2, 1)
        binding = Binding(axis=axis, above=threshold) if delta > 0 else Binding(axis=axis, below=threshold)
        if binding != self._candidate:
            self._candidate, self._timer = binding, 0.0
            return
        self._timer += dt
        if self._timer >= SWITCH_HOLD:
            self._offer(step, binding)

    def _offer(self, step: ButtonStep, binding: Binding) -> None:
        clash = self._clash(binding)
        if clash:
            # Одна кнопка на две команды (взлёт и стоп!) — опасно, не даём.
            self._message = t("Уже занято: {clash}. Нажми другую кнопку или Backspace", clash=t(clash))
            self._start_axes, self._start_buttons = self._axes, self._buttons
            self._candidate, self._timer = None, 0.0
            return
        self._bindings[step.action] = binding
        self._enter(self._index + 1)

    def _clash(self, binding: Binding) -> str:
        for action, other in self._bindings.items():
            same_button = binding.button is not None and binding.button == other.button
            same_switch = (binding.axis is not None and binding.axis == other.axis
                           and (binding.above is None) == (other.above is None))
            if same_button or same_switch:
                return _TITLES[action]
        return ""

    # ---------- результат ----------

    def profile(self) -> ControllerProfile:
        axis_of = {role: axis for role, (axis, _) in self._roles.items()}
        return ControllerProfile(
            axes=AxisMap(roll=axis_of.get("roll"), pitch=axis_of.get("pitch"), throttle=axis_of["throttle"],
                         yaw=axis_of.get("yaw")),
            invert=InvertMap(**{role: inverted for role, (_, inverted) in self._roles.items()}),
            calibration=tuple(self._axis_range(axis) for axis in sorted(axis_of.values())),
            bindings=Bindings(**self._bindings),
        )

    def _axis_range(self, axis: int) -> AxisRange:
        low, high = round(self._low[axis], 3), round(self._high[axis], 3)
        center = min(max(round(self._center[axis], 3), low), high)
        return AxisRange(axis=axis, low=low, center=center, high=high)

    def _done(self) -> tuple[tuple[str, str], ...]:
        done = []
        for step in AXIS_STEPS:
            if step.role in self._roles:
                axis, inverted = self._roles[step.role]
                done.append((t(step.title), t("ось {axis}, обратная", axis=axis) if inverted else t("ось {axis}", axis=axis)))
        for step in BUTTON_STEPS:
            if step.action in self._bindings:
                done.append((t(step.title), describe_binding(self._bindings[step.action])))
        return tuple(done)

    # ---------- экран ----------

    def view(self) -> CalibrationView:
        title, instruction, keys = self._texts()
        progress = None
        if self.step == CENTER:
            progress = min(1.0, self._timer / CENTER_HOLD)
        elif isinstance(self.step, AxisStep) and self._candidate is not None:
            progress = min(1.0, self._timer / AXIS_HOLD)
        active = self._candidate[0] if isinstance(self.step, AxisStep) and self._candidate else None
        message = self._message
        if self.step == SUMMARY and "kill" not in self._bindings and not message:
            message = t("Аварийный стоп на пульте не назначен — останется только Пробел на клавиатуре")
        return CalibrationView(
            controller=self.joystick.get_name() if self.joystick is not None else "",
            step=self._index + 1,
            total=len(STEPS),
            title=title,
            instruction=instruction,
            keys=keys,
            progress=progress,
            axes=self._axes,
            ranges=tuple(zip(self._low, self._high)),
            active_axis=active,
            buttons=self._buttons,
            done=self._done(),
            message=message,
            controllers=self._choices,
        )

    def _texts(self) -> tuple[str, str, tuple[tuple[str, str], ...]]:
        step = self.step
        if step == CHOOSE:
            if not self._choices:
                return (t("Подключи пульт"), t("Пульт не найден. Подключи его к компьютеру (USB или Bluetooth)."),
                        (CANCEL,))
            return (t("Какой пульт настраиваем?"), t("Подключено несколько пультов — тронь стик или нажми кнопку "
                                                    "на том, который нужен."),
                    (("Enter", N_("первый в списке")), CANCEL))
        if step == CENTER:
            return (t("Положение покоя"), t("Отпусти стики и не трогай пульт — запомню, где у него центр. "
                                            "Газ без пружины оставь внизу."),
                    (("Enter", N_("запомнить сейчас")), CANCEL))
        if step == RANGE:
            return (t("Ход стиков"), t("Покрути оба стика по кругу до упора, газ проведи от низа до верха. "
                                       "Потом нажми любую кнопку на пульте."),
                    (("Enter", N_("дальше")), BACK, CANCEL))
        if step == SUMMARY:
            return (t("Готово"), t("Проверь, что получилось. Сохраню раскладку под именем этого пульта — "
                                   "в следующий раз он подхватится сам."),
                    (("Enter", N_("сохранить")), BACK, CANCEL))
        skip = (("Backspace", N_("пропустить")),)
        if isinstance(step, AxisStep):
            return t(step.title), t(step.instruction), (skip if step.optional else ()) + (BACK, CANCEL)
        return t(step.title), t(step.instruction) + t(" Не нужна — Backspace."), skip + (BACK, CANCEL)
