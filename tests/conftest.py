import pytest

from cfpilot import i18n


@pytest.fixture(autouse=True)
def russian_after_each_test():
    """Язык — общее состояние программы: тест, переключивший его (F2, --lang en), не должен влиять на другие."""
    yield
    i18n.set_language("ru")
