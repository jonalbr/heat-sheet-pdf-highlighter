import json

import pytest

from src.config.settings import AppSettings
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
        "version","search_str","mark_only_relevant_lines","enable_filter","highlight_mode","language"
    }


def test_save_and_reload_persists_changes(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("search_str", "NewTerm")
    # Recreate instance to force disk load
    app2 = AppSettings(settings_file)
    assert app2.settings["search_str"] == "NewTerm"


def test_update_setting_writes_file(settings_file):
    app = AppSettings(settings_file)
    app.update_setting("enable_filter", 1)
    data = json.loads(settings_file.read_text())
    assert data["enable_filter"] == 1


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


def test_validator_coercions(settings_file):
    app = AppSettings(settings_file)
    # Directly tamper and revalidate
    app.settings.update({
        "mark_only_relevant_lines": 5,
        "enable_filter": 5,
        "highlight_mode": "BAD",
        "language": "zz",
        "ask_for_update": "MAYBE",
        "update_available": "MAYBE",
        "newest_version_available": "-1.0.0",
        "update_channel": "beta",
        "verify_sha": "maybe",
        "update_cache_ttl_seconds": -10,
        "releases_cache_ttl_seconds": 0,
        "watermark_enabled": "maybe",
        "watermark_color": "notcolor",
        "watermark_size": -5,
        "watermark_position": "left",
    })
    app.validate_settings()
    s = app.settings
    assert s["mark_only_relevant_lines"] in (0,1)
    assert s["enable_filter"] in (0,1)
    assert s["highlight_mode"] == "NAMES_DIFF_COLOR"
    assert s["language"] == "en"
    assert s["ask_for_update"] == "True"
    assert s["update_available"] == "False"
    assert s["newest_version_available"] == "0.0.0"
    assert s["update_channel"] == "stable"
    assert s["verify_sha"] == "True"
    assert s["update_cache_ttl_seconds"] == 86400
    assert s["releases_cache_ttl_seconds"] == 600
    assert s["watermark_enabled"] == "False"
    assert s["watermark_color"] == "#FFA500"
    assert s["watermark_size"] == 16
    assert s["watermark_position"] == "top"


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
