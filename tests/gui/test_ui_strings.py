from src.gui.ui_strings import build_strings, get_ui_string, plural_strings, _xgettext_dummy
import logging


def test_no_duplicate_ui_string_keys():
    # Should not raise AssertionError
    s = build_strings(lambda x: x)
    keys = list(s.keys())
    dups = {k for k in keys if keys.count(k) > 1}
    assert not dups, f"Duplicate string keys found: {dups}"


def test_get_ui_string_existing_key():
    s = build_strings(lambda x: x)
    assert get_ui_string(s, "error") == "Error"


def test_get_ui_string_with_default():
    s = {}
    assert get_ui_string(s, "missing_key", default="Fallback") == "Fallback"


def test_get_ui_string_missing_no_default_logs_warning(caplog):
    caplog.set_level(logging.WARNING, logger="ui_strings")
    s = {}
    msg = get_ui_string(s, "totally_missing")
    assert msg.startswith("Error: Key missing: totally_missing")
    assert any("Missing translation key" in rec.message for rec in caplog.records)


def test_plural_strings_structure():
    assert "processing_complete" in plural_strings
    assert set(plural_strings["processing_complete"].keys()) == {"singular", "plural"}
    assert "Processed:" in plural_strings["processed_pages"]["singular"]


def test_xgettext_dummy_invocation():
    # Provide a dummy ngettext-like function capturing arguments to ensure lines execute
    captured = []

    def fake_ngettext(singular, plural, n):
        captured.append((singular, plural, n))
        return singular if n == 1 else plural

    _xgettext_dummy(fake_ngettext)
    assert len(captured) == 2
    assert all(isinstance(t[0], str) and isinstance(t[1], str) for t in captured)
