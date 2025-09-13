from src.gui.ui_strings import build_strings


def test_no_duplicate_ui_string_keys():
    # Should not raise AssertionError
    s = build_strings(lambda x: x)
    keys = list(s.keys())
    dups = {k for k in keys if keys.count(k) > 1}
    assert not dups, f"Duplicate string keys found: {dups}"
