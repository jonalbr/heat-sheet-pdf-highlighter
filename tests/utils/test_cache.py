import datetime
import json
import logging

import pytest

from src.utils import cache as cache_mod
from src.version import Version
from src.config.paths import Paths


@pytest.fixture(autouse=True)
def isolate_cache_paths(tmp_path, monkeypatch):
    """Redirect Paths.settings_path to a temp directory so real user cache isn't touched."""
    # Re-point Paths class attributes that were computed at import time.
    monkeypatch.setattr(Paths, "settings_path", tmp_path)
    monkeypatch.setattr(Paths, "settings_file", tmp_path / "settings.json")
    monkeypatch.setattr(Paths, "update_cache_file", tmp_path / "update_check_cache.json")
    monkeypatch.setattr(Paths, "releases_cache_file", tmp_path / "releases_cache.json")
    yield


def test_load_update_cache_missing_returns_none():
    fetched, latest = cache_mod.load_update_cache()
    assert fetched is None and latest is None


def test_save_and_load_update_cache_roundtrip():
    ts = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    v = Version.from_str("1.2.3")
    cache_mod.save_update_cache(ts, v)
    fetched, latest = cache_mod.load_update_cache()
    assert fetched == ts
    assert latest == v


def test_load_update_cache_corrupt_resets(monkeypatch, caplog):
    # Write corrupt JSON
    Paths.update_cache_file.write_text("{not-json", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="cache"):
        fetched, latest = cache_mod.load_update_cache()
    assert fetched is None and latest is None
    # After corruption, file should be reset to a valid JSON structure
    data = json.loads(Paths.update_cache_file.read_text(encoding="utf-8"))
    assert data["latest_version"] == "0.0.0"


def test_load_update_cache_invalid_fetched_at(monkeypatch, caplog):
    cache_mod._write_json_atomic(Paths.update_cache_file, {"fetched_at": "INVALID", "latest_version": "1.0.0"})
    with caplog.at_level(logging.ERROR, logger="cache"):
        fetched, latest = cache_mod.load_update_cache()
    assert fetched is None and latest is None


def test_releases_cache_roundtrip():
    rels = [{"tag_name": "v1"}, {"tag_name": "v2"}]
    ts = datetime.datetime.now()
    cache_mod.save_releases_cache(rels, "stable", ts)
    fetched, channel, releases = cache_mod.load_releases_cache()
    assert fetched == ts
    assert channel == "stable"
    assert releases == rels


def test_releases_cache_corrupt(monkeypatch, caplog):
    Paths.releases_cache_file.write_text("corrupt", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="cache"):
        fetched, channel, releases = cache_mod.load_releases_cache()
    assert fetched is None and channel is None and releases is None
    # ensure file was reset to minimal valid JSON
    data = json.loads(Paths.releases_cache_file.read_text(encoding="utf-8"))
    assert data["releases"] == []


def test_releases_cache_invalid_fetched_at(caplog):
    cache_mod._write_json_atomic(Paths.releases_cache_file, {"fetched_at": "BAD_TS", "channel": "stable", "releases": []})
    with caplog.at_level(logging.ERROR, logger="cache"):
        fetched, channel, releases = cache_mod.load_releases_cache()
    assert fetched is None and channel is None and releases is None


def test_invalidate_releases_cache():
    # Pre-populate with something
    cache_mod.save_releases_cache([{"tag_name": "v1"}], "stable", datetime.datetime.now())
    cache_mod.invalidate_releases_cache()
    fetched, channel, releases = cache_mod.load_releases_cache()
    # After invalidation fetched_at becomes epoch, channel empty, releases empty
    assert channel in (None, "" )  # Depending on invalidation write vs read error fallback
    assert releases in ([], None)


def test_atomic_write_failure(monkeypatch, caplog):
    # Force os.replace to raise to exercise retry and exception path.
    attempts = {"count": 0}
    def failing_replace(src, dst):  # signature order reversed purposely? real is os.replace(src, dst)
        attempts["count"] += 1
        raise OSError("simulated replace failure")
    monkeypatch.setattr(cache_mod.os, "replace", failing_replace)
    with caplog.at_level(logging.ERROR, logger="cache"):
        with pytest.raises(OSError):
            cache_mod._write_json_atomic(Paths.update_cache_file, {"a": 1}, retries=2, delay=0)
    assert attempts["count"] == 2


def test_atomic_write_json_dump_failure(monkeypatch, caplog):
    original_dump = cache_mod.json.dump
    attempts = {"count": 0}
    def failing_dump(payload, tf, indent):
        attempts["count"] += 1
        raise ValueError("dump fail")
    monkeypatch.setattr(cache_mod.json, "dump", failing_dump)
    with caplog.at_level(logging.ERROR, logger="cache"):
        with pytest.raises(ValueError):
            cache_mod._write_json_atomic(Paths.update_cache_file, {"a": 1}, retries=2, delay=0)
    # ensure we attempted multiple times
    assert attempts["count"] == 2
    monkeypatch.setattr(cache_mod.json, "dump", original_dump)


def test_invalidate_releases_cache_write_failure(monkeypatch, caplog):
    def failing_write_json_atomic(target, payload, retries=5, delay=0.1):
        raise OSError("write fail")
    monkeypatch.setattr(cache_mod, "_write_json_atomic", failing_write_json_atomic)
    with caplog.at_level(logging.ERROR, logger="cache"):
        cache_mod.invalidate_releases_cache()
    # Should log error but not raise
    assert any("Failed to invalidate releases cache" in rec.message for rec in caplog.records)
