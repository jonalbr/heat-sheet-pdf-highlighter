"""Exercise reminder persistence and channel changes without external effects."""

import datetime
import threading
from types import SimpleNamespace

import pytest

from src.config.paths import Paths
from src.config.settings import AppSettings
from src.models import UpdateDecision
from src.utils import cache, updater
from src.version import Version


CURRENT = Version.from_str("2.1.0")


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.delenv("HSPH_USE_DEFAULT_SETTINGS", raising=False)
    monkeypatch.setattr(Paths, "update_cache_file", tmp_path / "update-cache.json")
    monkeypatch.setattr(Paths, "releases_cache_file", tmp_path / "releases-cache.json")
    settings = AppSettings(tmp_path / "settings.json")
    state = SimpleNamespace(
        decision=UpdateDecision("cancel"), prompts=[], publications=[], downloads=[], errors=[],
        stable="99.0.0", rc="99.1.0rc1", up_to_date=[], fetched=[],
    )

    def prompt(version, is_current):
        assert is_current()
        state.prompts.append(str(version))
        return state.decision

    gui = SimpleNamespace(
        show_update_available=prompt,
        show_up_to_date=lambda: state.up_to_date.append(True),
        show_update_error_retry=lambda message: state.errors.append(message) or False,
        show_download_error=lambda message: state.errors.append(message),
    )
    app = SimpleNamespace(
        app_settings=settings, update_dialogs=gui, _closing=False,
        on_version_update=lambda *args: state.publications.append(args),
    )
    checker = updater.UpdateChecker(app)

    def release(tag):
        return {
            "tag_name": tag, "prerelease": "rc" in tag,
            "assets": [
                {"name": "installer.exe", "browser_download_url": f"https://example/{tag}.exe"},
                {"name": "installer.exe.sha256", "browser_download_url": f"https://example/{tag}.sha256"},
            ],
        }

    def fetch(url):
        state.fetched.append(url)
        return release(state.stable) if url.endswith("/latest") else [release(state.rc), release(state.stable)]

    def forbidden(*args, **kwargs):
        pytest.fail("External request or installer start")

    monkeypatch.setattr(updater.requests, "get", forbidden)
    monkeypatch.setattr(checker, "_fetch_release_info", fetch)
    monkeypatch.setattr(checker, "_spawn_installer", forbidden)
    monkeypatch.setattr(
        checker, "download_and_run_installer",
        lambda url, sha: state.downloads.append((url, sha, app.app_settings.snapshot())),
    )

    class ImmediateThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(updater, "threading", SimpleNamespace(
        Thread=ImmediateThread, Lock=threading.Lock, RLock=threading.RLock,
    ))
    state.app, state.checker, state.fetch = app, checker, fetch
    state.check = lambda **kwargs: checker._get_latest_version_from_github(CURRENT, **kwargs)

    def change(channel):
        app.app_settings.update_setting("update_channel", channel)
        checker.channel_changed()

    state.change = change
    return state


def test_later_without_check_keeps_automatic_reminders(scenario):
    scenario.decision = UpdateDecision("later")
    scenario.check()
    scenario.check()
    assert scenario.prompts == ["99.0.0", "99.0.0"]
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {}
    assert scenario.downloads == []


@pytest.mark.parametrize("action", ["later", "update_now"])
def test_confirmed_skip_survives_restart_and_precedes_download(scenario, action):
    scenario.decision = UpdateDecision(action, True)
    scenario.check()
    scenario.app.app_settings = AppSettings(scenario.app.app_settings.settings_file)
    restarted = updater.UpdateChecker(scenario.app)
    restarted._fetch_release_info = scenario.fetch
    restarted._get_latest_version_from_github(CURRENT)
    assert scenario.prompts == ["99.0.0"]
    assert scenario.app.app_settings.settings["ask_for_update"] == "False"
    if action == "update_now":
        assert len(scenario.downloads) == 1
        assert scenario.downloads[0][1] == "https://example/99.0.0.sha256"
        assert scenario.downloads[0][2]["skipped_update_versions"] == {"stable": "99.0.0"}
    else:
        assert scenario.downloads == []


def test_cancel_discards_checkbox_and_preserves_previous_skip(scenario):
    scenario.app.app_settings.update_setting("skipped_update_versions", {"stable": "99.0.0"})
    scenario.decision = UpdateDecision("cancel", True)
    scenario.check(force_check=True)
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {"stable": "99.0.0"}
    scenario.stable = "99.0.1"
    scenario.check()
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {"stable": "99.0.0"}
    assert scenario.downloads == []


@pytest.mark.parametrize("action", ["later", "update_now"])
def test_manual_check_bypasses_skip_and_unchecked_confirmation_restores_reminders(scenario, action):
    scenario.app.app_settings.update_setting("skipped_update_versions", {"stable": "99.0.0", "rc": "99.1.0rc1"})
    scenario.decision = UpdateDecision(action)
    scenario.check(force_check=True)
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {"rc": "99.1.0rc1"}
    assert scenario.app.app_settings.settings["ask_for_update"] == "True"
    scenario.decision = UpdateDecision("later")
    scenario.check()
    assert scenario.prompts == ["99.0.0", "99.0.0"]


@pytest.mark.parametrize("channel,new_version", [
    ("stable", "99.0.1"), ("rc", "99.1.0rc2"), ("rc", "99.1.0"),
])
def test_newer_versions_and_final_after_rc_are_offered(scenario, channel, new_version):
    scenario.change(channel)
    scenario.decision = UpdateDecision("later", True)
    scenario.check()
    original = scenario.prompts[-1]
    scenario.check()
    assert len(scenario.prompts) == 1
    if "rc" in new_version:
        scenario.rc = new_version
    else:
        scenario.stable = new_version
    scenario.decision = UpdateDecision("later")
    scenario.check()
    assert scenario.prompts == [original, new_version]


@pytest.mark.parametrize("first,second", [("stable", "rc"), ("rc", "stable")])
def test_same_version_skip_is_independent_per_channel_and_survives_return(scenario, first, second):
    scenario.rc = "98.0.0rc1"  # Both channels select the same final release.
    scenario.change(first)
    scenario.decision = UpdateDecision("later", True)
    scenario.check()
    scenario.change(second)
    assert scenario.checker.last_download_url is None
    assert scenario.checker.last_sha_url is None
    assert scenario.checker.last_version_tag is None
    scenario.decision = UpdateDecision("later")
    scenario.check()
    scenario.change(first)
    scenario.check()
    assert scenario.prompts == ["99.0.0", "99.0.0"]
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {first: "99.0.0"}


def test_lower_channel_version_is_not_hidden_by_skipped_higher_rc(scenario):
    scenario.change("rc")
    scenario.decision = UpdateDecision("later", True)
    scenario.check()
    scenario.change("stable")
    scenario.decision = UpdateDecision("later")
    scenario.check()
    assert scenario.prompts == ["99.1.0rc1", "99.0.0"]
    assert scenario.app.app_settings.settings["newest_version_available"] == "99.0.0"


def test_quiet_check_preserves_suppression(scenario):
    scenario.app.app_settings.update_setting("skipped_update_versions", {"rc": "99.1.0rc1"})
    scenario.change("rc")
    scenario.check(force_check=True, quiet=True)
    scenario.check(force_check=True, quiet=True)
    assert scenario.prompts == scenario.up_to_date == []
    assert scenario.app.app_settings.settings["ask_for_update"] == "False"
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {"rc": "99.1.0rc1"}


def test_cache_is_channel_specific_and_force_check_still_prompts(scenario):
    checker = scenario.checker
    checker.check_for_app_updates(CURRENT, quiet=True)
    assert cache.load_update_cache("stable")[1] == Version.from_str("99.0.0")
    assert cache.load_update_cache("rc") == (None, None)
    scenario.change("rc")
    checker.check_for_app_updates(CURRENT, quiet=True)
    assert cache.load_update_cache("rc")[1] == Version.from_str("99.1.0rc1")
    fetched = len(scenario.fetched)
    checker.check_for_app_updates(CURRENT, quiet=True)
    assert len(scenario.fetched) == fetched
    scenario.app.app_settings.update_setting("skipped_update_versions", {"rc": "99.1.0rc1"})
    checker.check_for_app_updates(CURRENT, force_check=True)
    assert scenario.prompts == ["99.1.0rc1"]


def test_inflight_old_channel_result_is_discarded_and_quiet_refresh_runs(scenario):
    checker = scenario.checker

    def fetch(url):
        if len(scenario.fetched) == 0:
            scenario.change("rc")
            # The new request must not overlap the active fetch.
            checker.check_for_app_updates(CURRENT, force_check=True, quiet=True)
        return scenario.fetch(url)

    checker._fetch_release_info = fetch
    latest = checker.check_for_app_updates(CURRENT, force_check=True)
    assert latest == Version.from_str("99.1.0rc1")
    assert scenario.prompts == []
    assert [str(args[0]) for args in scenario.publications] == ["99.1.0rc1"]
    assert checker.last_version_tag == "99.1.0rc1"
    assert cache.load_update_cache("stable") == (None, None)
    assert cache.load_update_cache("rc")[1] == latest
    assert not checker._active_check


def test_real_concurrent_channel_refresh_waits_for_active_check(scenario):
    checker = scenario.checker
    entered, release = threading.Event(), threading.Event()
    results = []

    def fetch(url):
        if not entered.is_set():
            entered.set()
            assert release.wait(3)
        return scenario.fetch(url)

    checker._fetch_release_info = fetch
    worker = threading.Thread(target=lambda: results.append(checker.check_for_app_updates(CURRENT, force_check=True)))
    worker.start()
    try:
        assert entered.wait(3)
        scenario.change("rc")
        checker.check_for_app_updates(CURRENT, force_check=True, quiet=True)
        assert scenario.fetched == []
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert results == [Version.from_str("99.1.0rc1")]
    assert [str(args[0]) for args in scenario.publications] == ["99.1.0rc1"]
    assert scenario.prompts == []
    assert not checker._active_check


@pytest.mark.parametrize("return_to_original", [False, True])
def test_channel_change_during_dialog_discards_decision(scenario, return_to_original):
    def prompt(version, is_current):
        assert is_current()
        scenario.change("rc")
        if return_to_original:
            scenario.change("stable")
        return UpdateDecision("update_now", True)

    scenario.app.update_dialogs.show_update_available = prompt
    scenario.check()
    assert scenario.downloads == []
    assert scenario.app.app_settings.settings["skipped_update_versions"] == {}
    assert scenario.checker.last_download_url is None


def test_queued_install_from_previous_channel_is_rejected(scenario):
    scenario.check(quiet=True)
    context = scenario.checker.last_update_context
    scenario.change("rc")
    scenario.checker.download_discovered_installer("old.exe", "old.sha256", context)
    assert scenario.downloads == []


def test_legacy_cache_without_channel_is_ignored(scenario):
    cache._write_json_atomic(Paths.update_cache_file, {
        "fetched_at": datetime.datetime.now().isoformat(), "latest_version": "99.0.0",
    })
    assert cache.load_update_cache("stable") == cache.load_update_cache("rc") == (None, None)
