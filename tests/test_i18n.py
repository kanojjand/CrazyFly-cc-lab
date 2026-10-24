"""Английский вариант: перевод, полнота словаря и то, что на английском на экран не попадает кириллица."""

import ast
import os
import re
import string
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from cfpilot import i18n  # noqa: E402
from cfpilot.calibration import STEPS, Calibrator  # noqa: E402
from cfpilot.channels import OnAir, RadioUri  # noqa: E402
from cfpilot.connection import Stage, Status  # noqa: E402
from cfpilot.controls import Sticks  # noqa: E402
from cfpilot.flight import FlightMode, Phase  # noqa: E402
from cfpilot.lang_en import EN  # noqa: E402
from cfpilot.link import Telemetry  # noqa: E402
from cfpilot.screens import ConnectView  # noqa: E402
from cfpilot.theme import Painter  # noqa: E402
from cfpilot.ui import WINDOW_SIZE, Dashboard, View  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted((ROOT / "cfpilot").glob("*.py")) + [ROOT / "main.py"]
CHECKED = [path for path in SOURCES if path.name != "lang_en.py"]  # в словаре русские ключи — так и задумано
CYRILLIC = re.compile("[А-Яа-яЁё]")
NATIVE_NAMES = {"Русский"}  # язык на клавише F2 подписан на нём самом — его ищет тот, кто его читает
MARKERS = {"t", "N_"}
# Русский текст, который пользователь в окне не видит: журнал, справка командной строки, вывод в консоль.
NOT_SHOWN = {"debug", "info", "warning", "exception", "error", "print", "add_argument", "ArgumentParser"}
NOT_SHOWN_ERRORS = {"TypeError", "ValueError"}  # внутренние ошибки программы, до пользователя не доходят


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    return {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _marked_texts() -> set[str]:
    texts = set()
    for path in SOURCES:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _call_name(node) in MARKERS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and CYRILLIC.search(first.value):
                    texts.add(first.value)
    return texts


def _unmarked(path: Path) -> list[str]:
    """Кириллица, которая может попасть на экран мимо перевода."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents, docs = _parents(tree), _docstrings(tree)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and CYRILLIC.search(node.value)):
            continue
        if id(node) in docs or node.value in NATIVE_NAMES:
            continue
        parent = parents.get(id(node))
        while isinstance(parent, (ast.JoinedStr, ast.FormattedValue, ast.BinOp, ast.keyword)):
            parent = parents.get(id(parent))
        if isinstance(parent, ast.Call):
            name = _call_name(parent)
            if name in MARKERS or name in NOT_SHOWN or name in NOT_SHOWN_ERRORS:
                continue
        found.append(f"{path.name}:{node.lineno}: {node.value[:50]}")
    return found


def test_russian_is_the_source_text():
    assert i18n.t("Ищу дрон") == "Ищу дрон"
    assert i18n.t("попытка {n}", n=3) == "попытка 3"


def test_english_translation_with_values():
    i18n.set_language("en")
    assert i18n.t("Ищу дрон") == EN["Ищу дрон"]
    assert i18n.t("Текст, которого нет в словаре") == "Текст, которого нет в словаре"  # не падаем


def test_unknown_language_is_rejected():
    with pytest.raises(ValueError):
        i18n.set_language("de")


def test_next_language_cycles():
    assert i18n.next_language("ru") == "en"
    assert i18n.next_language("en") == "ru"


def test_every_marked_text_has_english():
    missing = sorted(_marked_texts() - set(EN))
    assert not missing, "нет перевода:\n" + "\n".join(missing)


def test_dictionary_has_no_leftovers():
    unused = sorted(set(EN) - _marked_texts())
    assert not unused, "перевод того, чего в программе уже нет:\n" + "\n".join(unused)


def test_translations_keep_placeholders():
    def fields(text):
        return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name)

    broken = [key for key, value in EN.items() if fields(key) != fields(value)]
    assert not broken
    assert not [value for value in EN.values() if CYRILLIC.search(value)]


@pytest.mark.parametrize("path", CHECKED, ids=lambda path: path.name)
def test_no_russian_text_reaches_screen_untranslated(path):
    assert _unmarked(path) == []


@pytest.fixture
def english_screen(monkeypatch):
    i18n.set_language("en")
    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    shown = []
    render = Painter.render

    def recording(self, text, font, color):
        shown.append(text)
        return render(self, text, font, color)

    monkeypatch.setattr(Painter, "render", recording)
    yield Dashboard(screen), shown
    pygame.quit()


def _russian(shown):
    return [text for text in shown if CYRILLIC.search(text) and text not in NATIVE_NAMES]


def test_english_connection_screens(english_screen):
    dash, shown = english_screen
    drones = (OnAir(RadioUri(0, 20), 1.0), OnAir(RadioUri(0, 100), 0.4))
    for stage in Stage:
        dash.draw_connect(ConnectView(uri="radio://0/80/2M/E7E7E7E7E7", status=Status(stage, 3, drones=drones),
                                      controller="DualSense"))
    dash.draw_connect(ConnectView(uri="radio://0/80/2M/E7E7E7E7E7", status=Status(Stage.CHOOSING, 1,
                                                                                    drones=drones[:1])))
    assert _russian(shown) == []


def test_english_flight_screens(english_screen):
    dash, shown = english_screen
    for mode in FlightMode:
        for phase in Phase:
            for monitor in (False, True):
                view = View(uri="radio://0/80/2M/E7E7E7E7E7", mode=mode, decks="Flow", phase=phase,
                            input_source="keyboard", telemetry=Telemetry(battery=3.9, height=0.4, link_quality=80.0),
                            sticks=Sticks(), target_height=0.5, thrust=30000 if mode is FlightMode.MANUAL else None,
                            trim=None if mode is FlightMode.HOVER else (0.5, 0.0), battery_before_flight=4.0,
                            monitor=monitor, axes=(0.1,) * 6, buttons=(True,) * 8,
                            height_sensor=mode is not FlightMode.MANUAL)
                dash.draw(view)
    dash.draw(View(uri="x", mode=FlightMode.MANUAL, decks="", phase=Phase.IDLE, input_source="", sticks=Sticks(),
                   telemetry=Telemetry(), target_height=None, thrust=0, joystick_name="Pad", controller_profile=True))
    dash.draw(View(uri="x", mode=FlightMode.MANUAL, decks="", phase=Phase.IDLE, input_source="", sticks=Sticks(),
                   telemetry=Telemetry(), target_height=None, thrust=0, joystick_name="Pad"))
    assert _russian(shown) == []


def test_english_calibration_screens(english_screen):
    dash, shown = english_screen
    calibrator = Calibrator(lambda: [])
    for index in range(len(STEPS)):
        calibrator._enter(index)
        dash.draw_calibration(calibrator.view())
    assert _russian(shown) == []
