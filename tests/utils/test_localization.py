import builtins
from unittest import mock

import pytest

# Target module
from src.utils import localization


def test_get_available_languages_returns_constant():
    from src.constants import LANGUAGE_OPTIONS
    result = localization.get_available_languages()
    assert result is LANGUAGE_OPTIONS
    assert isinstance(result, (list, tuple))
    assert len(result) >= 1


def test_setup_translation_success(monkeypatch):
    def fake_gettext(msg: str):
        return f"T:{msg}"

    def fake_ngettext(singular: str, plural: str, n: int):
        return singular if n == 1 else plural

    class FakeTranslation:
        def install(self):
            builtins._ = fake_gettext  # type: ignore[attr-defined]

        def gettext(self, msg):
            return fake_gettext(msg)

        def ngettext(self, singular, plural, n):
            return fake_ngettext(singular, plural, n)

    def fake_translation(domain, localedir=None, languages=None):  # noqa: D401
        assert domain == "base"
        assert localedir is not None
        assert isinstance(languages, list) and len(languages) == 1
        return FakeTranslation()

    with mock.patch("src.utils.localization.gettext.translation", side_effect=fake_translation) as m:
        g, n = localization.setup_translation("en")
        assert m.called
        assert g("Hello") == "T:Hello"
        assert n("item", "items", 1) == "item"
        assert n("item", "items", 2) == "items"

    assert hasattr(builtins, "_")
    assert builtins._("Hello") == "T:Hello"  # type: ignore[attr-defined]


def test_setup_translation_failure(monkeypatch):
    def raise_fn(*_a, **_k):
        raise FileNotFoundError("missing mo file")

    with mock.patch("src.utils.localization.gettext.translation", side_effect=raise_fn):
        with pytest.raises(FileNotFoundError):
            localization.setup_translation("xx")
