import json
from pathlib import Path
import threading

import pytest

from src.config.settings import AppSettings, _is_valid_ratio
from src.config import settings as settings_module
from src.constants import VERSION_STR, LANGUAGE_OPTIONS


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "settings.json"


def test_defaults_loaded_when_file_missing(settings_file):
    app = AppSettings(settings_file)
    assert app.settings_file == settings_file
    # Ensure defaults applied
    assert app.settings["version"] == VERSION_STR
    assert set(app.settings.keys()) >= {
        "version",
        "search_str",
        "mark_only_relevant_lines",
        "marking_mode",
        "highlight_color_default",
        "highlight_color_names",
        "theme_mode",
        "language",
        "ocr_enabled",
        "ocr_language",
        "ocr_dpi",
        "ocr_reduce_large_outputs",
        "ocr_settings_version",
    }
    assert app.settings["ocr_enabled"] == "True"
    assert app.settings["ocr_language"] == "deu"
    assert app.settings["ocr_dpi"] == 300
    assert app.settings["ocr_reduce_large_outputs"] == "True"
    assert app.settings["ocr_settings_version"] == 3


def test_save_and_reload_persists_changes(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("search_str", "NewTerm")
    # Recreate instance to force disk load
    app2 = AppSettings(settings_file)
    assert app2.settings["search_str"] == "NewTerm"


@pytest.mark.parametrize("enabled", [0, 1])
@pytest.mark.parametrize("legacy", ["ONLY_NAMES", "NAMES_DIFF_COLOR"])
def test_legacy_marking_migration(settings_file, enabled, legacy):
    settings_file.write_text(json.dumps({"enable_filter": enabled, "highlight_mode": legacy}))
    app = AppSettings(settings_file)
    expected = "all_matches" if not enabled else (
        "named_matches" if legacy == "ONLY_NAMES" else "distinguish_names"
    )
    assert app.settings["marking_mode"] == expected
    assert app.settings["highlight_color_names"] == ("#FFFFA6" if legacy == "ONLY_NAMES" else "#C4FAF8")
    assert "enable_filter" not in app.settings
    assert "highlight_mode" not in app.settings
    assert AppSettings(settings_file).settings == app.settings


def test_new_marking_values_take_precedence_and_normalize_colors(settings_file):
    settings_file.write_text(json.dumps({
        "enable_filter": 1, "highlight_mode": "ONLY_NAMES", "marking_mode": "distinguish_names",
        "highlight_color_names": "#abcdef", "highlight_color_default": "#fedcba",
    }))
    app = AppSettings(settings_file)
    assert app.settings["marking_mode"] == "distinguish_names"
    assert app.settings["highlight_color_names"] == "#ABCDEF"
    assert app.settings["highlight_color_default"] == "#FEDCBA"
    app.validate_settings()
    assert AppSettings(settings_file).settings == app.settings


@pytest.mark.parametrize("color", [None, 123, "#123", "#1234567", "#GGGGGG", "red", "#abcdef\n"])
def test_invalid_highlight_colors_fall_back(settings_file, color):
    app = AppSettings(settings_file)
    app.settings.update(highlight_color_default=color, highlight_color_names=color)
    app.validate_settings()
    assert app.settings["highlight_color_default"] == "#FFFFA6"
    assert app.settings["highlight_color_names"] == "#C4FAF8"


def test_legacy_only_names_preserves_explicit_new_name_color(settings_file):
    settings_file.write_text(json.dumps({
        "enable_filter": 1, "highlight_mode": "ONLY_NAMES", "highlight_color_names": "#abcdef",
    }))
    assert AppSettings(settings_file).settings["highlight_color_names"] == "#ABCDEF"


def test_theme_mode_persists(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("theme_mode", "dark")
    app2 = AppSettings(settings_file)
    assert app2.settings["theme_mode"] == "dark"


def test_update_setting_writes_file(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("marking_mode", "named_matches")
    data = json.loads(settings_file.read_text())
    assert data["marking_mode"] == "named_matches"


def test_reset_to_defaults(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("names", "Alice,Bob")
    assert app.settings["names"]
    app.reset_to_defaults()
    assert app.settings["names"] == ""


def test_environment_ephemeral_mode_skip_write(settings_file, monkeypatch):
    monkeypatch.setenv("HSPH_USE_DEFAULT_SETTINGS", "1")
    app = AppSettings(settings_file)
    app.update_setting("search_str", "Changed")
    # File should not be created
    assert not settings_file.exists()


def test_language_force_env(settings_file, monkeypatch):
    monkeypatch.setenv("HSPH_FORCE_LANGUAGE", LANGUAGE_OPTIONS[0])
    app = AppSettings(settings_file)
    assert app.settings["language"] == LANGUAGE_OPTIONS[0]


def test_language_force_env_invalid_fallback_en(settings_file, monkeypatch):
    monkeypatch.setenv("HSPH_FORCE_LANGUAGE", "zz")
    app = AppSettings(settings_file)
    assert app.settings["language"] == "en"


def test_theme_force_env(settings_file, monkeypatch):
    monkeypatch.setenv("HSPH_FORCE_THEME", "light")
    app = AppSettings(settings_file)
    assert app.settings["theme_mode"] == "light"


def test_theme_force_env_invalid_fallback_light(settings_file, monkeypatch):
    monkeypatch.setenv("HSPH_FORCE_THEME", "sepia")
    app = AppSettings(settings_file)
    assert app.settings["theme_mode"] == "light"


@pytest.mark.parametrize("color,expected", [
    ("#abcdef", "#ABCDEF"), ("#000000", "#000000"), ("#", "#FFA500"),
    ("#123", "#FFA500"), ("#1234567", "#FFA500"), ("#gg0000", "#FFA500"),
    ("red", "#FFA500"), (None, "#FFA500"), (["#123456"], "#FFA500"),
])
def test_watermark_colors_are_complete_rgb_and_normalized(settings_file, color, expected):
    app = AppSettings(settings_file)
    app.update_setting("watermark_color", color)
    assert AppSettings(settings_file).snapshot()["watermark_color"] == expected


@pytest.mark.parametrize("size,expected", [
    (1, 1), (100, 100), ("24", 24), (0, 16), (101, 16), (-1, 16),
    ("1.5", 16), ("", 16), ("²", 16), (None, 16), (True, 16),
])
def test_watermark_size_bounds_persist_consistently(settings_file, size, expected):
    app = AppSettings(settings_file)
    app.update_setting("watermark_size", size)
    assert AppSettings(settings_file).snapshot()["watermark_size"] == expected


def test_validator_coercions(settings_file):
    app = AppSettings(settings_file)
    # Directly tamper and revalidate
    app.settings.update(
        {
            "mark_only_relevant_lines": 5,
            "marking_mode": "BAD",
            "highlight_color_default": "#123",
            "highlight_color_names": "#gggggg",
            "language": "zz",
            "ask_for_update": "MAYBE",
            "update_available": "MAYBE",
            "newest_version_available": "-1.0.0",
            "update_channel": "beta",
            "theme_mode": "bad",
            "verify_sha": "maybe",
            "update_cache_ttl_seconds": -10,
            "releases_cache_ttl_seconds": 0,
            "watermark_enabled": "maybe",
            "watermark_color": "notcolor",
            "watermark_size": -5,
            "watermark_position": "left",
            "watermark_x_ratio": 2,
            "watermark_y_ratio": -1,
            "ocr_enabled": "maybe",
            "ocr_language": "invalid",
            "ocr_dpi": 20,
            "ocr_reduce_large_outputs": "maybe",
            "ocr_settings_version": "bad",
        }
    )
    app.validate_settings()
    s = app.settings
    assert s["mark_only_relevant_lines"] in (0, 1)
    assert s["marking_mode"] == "all_matches"
    assert s["highlight_color_default"] == "#FFFFA6"
    assert s["highlight_color_names"] == "#C4FAF8"
    assert s["language"] == "en"
    assert s["ask_for_update"] == "True"
    assert s["update_available"] == "False"
    assert s["newest_version_available"] == "0.0.0"
    assert s["update_channel"] == "stable"
    assert s["theme_mode"] == "system"
    assert s["verify_sha"] == "True"
    assert s["update_cache_ttl_seconds"] == 86400
    assert s["releases_cache_ttl_seconds"] == 600
    assert s["watermark_enabled"] == "False"
    assert s["watermark_color"] == "#FFA500"
    assert s["watermark_size"] == 16
    assert s["watermark_position"] == "top"
    assert s["watermark_x_ratio"] == 0.5
    assert s["watermark_y_ratio"] == 0.05
    assert s["ocr_enabled"] == "True"
    assert s["ocr_language"] == "deu"
    assert s["ocr_dpi"] == 300
    assert s["ocr_reduce_large_outputs"] == "True"
    assert s["ocr_settings_version"] == 3


def test_migration_from_initial_ocr_defaults(settings_file):
    settings_file.write_text(
        json.dumps(
            {
                "version": VERSION_STR,
                "ocr_language": "deu+eng",
                "ocr_reduce_large_outputs": "True",
            }
        )
    )

    app = AppSettings(settings_file)

    assert app.settings["ocr_language"] == "deu"
    assert app.settings["ocr_reduce_large_outputs"] == "True"
    assert app.settings["ocr_settings_version"] == 3

    app.update_setting("ocr_language", "deu+eng")
    app2 = AppSettings(settings_file)
    assert app2.settings["ocr_language"] == "deu+eng"


def test_custom_watermark_position_persists(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("watermark_position", "custom")
    app.update_setting("watermark_x_ratio", 0.25)
    app.update_setting("watermark_y_ratio", 0.75)

    app2 = AppSettings(settings_file)

    assert app2.settings["watermark_position"] == "custom"
    assert app2.settings["watermark_x_ratio"] == 0.25
    assert app2.settings["watermark_y_ratio"] == 0.75


def test_migration_from_legacy_beta_flag(settings_file):
    # Create legacy content
    legacy = {"beta": "True", "version": VERSION_STR}
    settings_file.write_text(json.dumps(legacy))
    app = AppSettings(settings_file)
    assert app.settings["update_channel"] == "rc"


def test_migration_from_legacy_beta_flag_false(settings_file):
    legacy = {"beta": False, "version": VERSION_STR}
    settings_file.write_text(json.dumps(legacy))
    app = AppSettings(settings_file)
    assert app.settings["update_channel"] == "stable"


def test_ignore_unknown_keys(settings_file):
    data = {"unknown_key": 123, "version": VERSION_STR}
    settings_file.write_text(json.dumps(data))
    app = AppSettings(settings_file)
    assert "unknown_key" not in app.settings


def test_validate_settings_returns_dict_when_explicit(settings_file):
    app = AppSettings(settings_file)
    d = app.validate_settings({"version": VERSION_STR})
    assert isinstance(d, dict)
    assert d["version"] == VERSION_STR


def test_validate_settings_explicit_empty_dict_uses_defaults_not_live_state(settings_file):
    app = AppSettings(settings_file)
    app.settings["search_str"] = "live-value"

    validated = app.validate_settings({})

    assert validated["search_str"] == "SGS Hamburg"


def test_is_valid_ratio_rejects_non_numeric_values():
    assert _is_valid_ratio("not-a-number") is False


def test_invalid_newest_version_string_resets(settings_file):
    app = AppSettings(settings_file)
    app.settings["newest_version_available"] = "not-a-version"
    app.validate_settings()
    assert app.settings["newest_version_available"] == "0.0.0"


def test_valid_newest_version_string_is_kept(settings_file):
    app = AppSettings(settings_file)
    app.settings["newest_version_available"] = "99.0.0"
    app.validate_settings()
    assert app.settings["newest_version_available"] == "99.0.0"


def test_non_string_newest_version_resets(settings_file):
    app = AppSettings(settings_file)
    app.settings["newest_version_available"] = 123
    app.validate_settings()
    assert app.settings["newest_version_available"] == "0.0.0"


@pytest.mark.parametrize("original", [
    b"", b'{"search_str":', b'{"names":"\xff"}',
    b"null", b"[]", b'["names"]', b'"text"', b"123", b"1.5", b"true", b"false",
])
def test_corrupt_settings_recover_exact_original_bytes(settings_file, original):
    settings_file.write_bytes(original)
    app = AppSettings(settings_file)
    assert app.snapshot() == app.default_settings
    notice, = app.take_notices()
    assert notice.kind == "recovered"
    assert notice.backup_path.read_bytes() == original
    assert json.loads(settings_file.read_text(encoding="utf-8")) == app.default_settings
    assert app.take_notices() == []
    assert AppSettings(settings_file).take_notices() == []


def test_repeated_corruption_preserves_distinct_backups(settings_file):
    originals = [b"first broken file", b"second broken file"]
    backups = []
    for original in originals:
        settings_file.write_bytes(original)
        backups.append(AppSettings(settings_file).take_notices()[0].backup_path)
    assert backups[0] != backups[1]
    assert [path.read_bytes() for path in backups] == originals


def fail_file_operation(monkeypatch, operation):
    """Inject faults while still closing the real temporary file on Windows."""
    def fail(*args, **kwargs):
        raise PermissionError(f"simulated {operation} failure")

    if operation == "create":
        monkeypatch.setattr(settings_module.tempfile, "NamedTemporaryFile", fail)
    elif operation in ("fsync", "replace"):
        monkeypatch.setattr(settings_module.os, operation, fail)
    else:
        factory = settings_module.tempfile.NamedTemporaryFile

        class FaultyFile:
            def __init__(self, *args, **kwargs):
                self.stream = factory(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.stream, name)

            def __enter__(self):
                if operation in ("write", "flush"):
                    setattr(self, operation, fail)
                return self

            def __exit__(self, *args):
                self.stream.__exit__(*args)
                if operation == "close":
                    fail()

        monkeypatch.setattr(settings_module.tempfile, "NamedTemporaryFile", FaultyFile)


@pytest.mark.parametrize("operation", ["create", "write", "flush", "fsync", "close", "replace"])
def test_atomic_write_failure_preserves_old_file_and_cleans_temporary(settings_file, monkeypatch, operation):
    app = AppSettings(settings_file)
    original = settings_file.read_bytes()
    with monkeypatch.context() as patch:
        fail_file_operation(patch, operation)
        assert app.update_settings({"search_str": "Änderung", "names": "Müller"}) is False
        assert app.snapshot()["search_str"] == "Änderung"
        assert settings_file.read_bytes() == original
        assert sorted(settings_file.parent.iterdir()) == [settings_file]
        assert [notice.kind for notice in app.take_notices()] == ["write_failed"]
        app.save_settings()
        assert app.take_notices() == []
    assert app.save_settings() is True
    assert AppSettings(settings_file).snapshot()["names"] == "Müller"
    # After a successful save, a new failure is explained again.
    fail_file_operation(monkeypatch, "replace")
    app.save_settings()
    assert [notice.kind for notice in app.take_notices()] == ["write_failed"]


@pytest.mark.parametrize("operation", ["create", "write", "flush", "fsync", "close"])
def test_backup_failure_protects_original_for_entire_session(settings_file, monkeypatch, operation):
    original = b'{"damaged":\xff'
    settings_file.write_bytes(original)
    with monkeypatch.context() as patch:
        fail_file_operation(patch, operation)
        app = AppSettings(settings_file)
    assert app.snapshot() == app.default_settings
    assert [notice.kind for notice in app.take_notices()] == ["backup_failed"]
    assert app.update_setting("names", "Session only") is False
    assert app.snapshot()["names"] == "Session only"
    app.validate_settings()
    assert app.reset_to_defaults() is False
    assert app.save_settings() is False
    assert settings_file.read_bytes() == original
    assert list(settings_file.parent.iterdir()) == [settings_file]


def test_access_failure_is_not_corruption_and_blocks_overwrite(settings_file, monkeypatch):
    original = b'{"names": "Unread"}'
    settings_file.write_bytes(original)
    read = Path.read_bytes

    def unreadable(path):
        if path == settings_file:
            raise PermissionError("access denied")
        return read(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", unreadable)
        app = AppSettings(settings_file)
    assert app.snapshot() == app.default_settings
    assert [notice.kind for notice in app.take_notices()] == ["read_failed"]
    assert app.update_setting("names", "Session") is False
    assert settings_file.read_bytes() == original
    assert list(settings_file.parent.iterdir()) == [settings_file]


def test_directory_creation_failure_does_not_prevent_start(settings_file, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("mkdir denied")

    monkeypatch.setattr(Path, "mkdir", denied)
    app = AppSettings(settings_file)
    assert app.snapshot() == app.default_settings
    assert [notice.kind for notice in app.take_notices()] == ["write_failed"]
    assert not settings_file.exists()


def test_successful_retry_removes_an_unshown_write_warning(settings_file, monkeypatch):
    app = AppSettings(settings_file)
    with monkeypatch.context() as patch:
        fail_file_operation(patch, "replace")
        app.update_setting("names", "Retry")
    assert app.save_settings()
    assert app.take_notices() == []


def test_ephemeral_settings_perform_no_filesystem_operations(settings_file, monkeypatch):
    settings_file.write_bytes(b"\xff damaged")
    monkeypatch.setenv("HSPH_USE_DEFAULT_SETTINGS", "1")

    def forbidden(*args, **kwargs):
        pytest.fail("Ephemeral settings touched the filesystem")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(Path, "mkdir", forbidden)
        patch.setattr(settings_module.tempfile, "NamedTemporaryFile", forbidden)
        patch.setattr(settings_module.os, "replace", forbidden)
        app = AppSettings(settings_file)
        app.update_settings({"names": "Session", "theme_mode": "dark"})
        app.validate_settings()
        app.reset_to_defaults()
        assert app.take_notices() == []
    assert settings_file.read_bytes() == b"\xff damaged"


def test_batch_normalizes_all_values_and_saves_once(settings_file, monkeypatch):
    app = AppSettings(settings_file)
    replacements = []
    replace = settings_module.os.replace

    def record(source, target):
        replacements.append(json.loads(Path(source).read_text(encoding="utf-8")))
        replace(source, target)

    monkeypatch.setattr(settings_module.os, "replace", record)
    assert app.update_settings({"names": "Älice", "highlight_color_names": "#abcdef", "ocr_dpi": 1})
    assert len(replacements) == 1
    assert replacements[0]["names"] == "Älice"
    assert replacements[0]["highlight_color_names"] == "#ABCDEF"
    assert replacements[0]["ocr_dpi"] == 300
    independent = app.snapshot()
    independent["names"] = "Changed copy"
    assert app.snapshot()["names"] == "Älice"


def test_concurrent_updates_and_snapshots_are_serialized(settings_file, monkeypatch):
    app = AppSettings(settings_file)
    entered, release, second_started, reader_started = (threading.Event() for _ in range(4))
    replace = settings_module.os.replace
    results, errors = [], []

    def controlled_replace(source, target):
        if threading.current_thread().name == "first":
            entered.set()
            assert release.wait(5)
        replace(source, target)

    def first():
        app.update_settings({"names": "Together", "search_str": "Together"})

    def second():
        second_started.set()
        app.update_setting("verify_sha", "False")

    def reader():
        reader_started.set()
        results.append(app.snapshot())

    def guarded(callback):
        try:
            callback()
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(settings_module.os, "replace", controlled_replace)
    workers = [threading.Thread(target=guarded, args=(callback,), name=name)
               for name, callback in (("first", first), ("second", second), ("reader", reader))]
    try:
        workers[0].start()
        assert entered.wait(5)
        for worker in workers[1:]:
            worker.start()
        assert second_started.wait(5) and reader_started.wait(5)
        assert results == []
    finally:
        release.set()
        for worker in workers:
            if worker.ident is not None:
                worker.join(5)
                assert not worker.is_alive()
    assert errors == []
    assert results[0]["names"] == results[0]["search_str"] == "Together"
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted == app.snapshot()
    assert persisted["verify_sha"] == "False"
    assert persisted["names"] == "Together"


@pytest.mark.parametrize("value", ["²", "9" * 5000, [], {}, None])
def test_malformed_individual_values_do_not_prevent_start(settings_file, value):
    settings_file.write_text(json.dumps({
        "watermark_size": value, "ocr_dpi": value, "ocr_settings_version": value,
        "watermark_x_ratio": value, "language": value, "marking_mode": value,
    }), encoding="utf-8")
    app = AppSettings(settings_file)
    assert app.snapshot()["ocr_dpi"] == 300
    assert app.snapshot()["watermark_size"] == 16
    assert app.take_notices() == []
