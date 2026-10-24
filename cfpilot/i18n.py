"""Язык интерфейса. Исходный текст — русский, прямо в коде; английский — словарь в cfpilot/lang_en.py.

    t("Ищу дрон")              — перевести сейчас
    t("попытка {n}", n=3)      — с подстановкой (в словаре те же {имена})
    N_("Ищу дрон")             — только пометить: константу переводят там, где её показывают — t(константа)

Каждый помеченный текст обязан быть в словаре — это проверяет tests/test_i18n.py.
"""

from __future__ import annotations

from cfpilot.lang_en import EN

LANGUAGES = ("ru", "en")
LANGUAGE_NAMES = {"ru": "Русский", "en": "English"}  # каждое — на своём языке: так его и ищут
_current = {"language": "ru"}


def set_language(language: str) -> None:
    if language not in LANGUAGES:
        raise ValueError(f"Неизвестный язык: {language}")
    _current["language"] = language


def language() -> str:
    return _current["language"]


def next_language(current: str) -> str:
    return LANGUAGES[(LANGUAGES.index(current) + 1) % len(LANGUAGES)]


def N_(text: str) -> str:  # noqa: N802 — имя как в gettext
    return text


def t(text: str, **values) -> str:
    if _current["language"] == "en":
        text = EN.get(text, text)
    return text.format(**values) if values else text
