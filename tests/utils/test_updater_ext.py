"""Extended tests for UpdateChecker covering caching, prompts, downloads, channels, concurrency, metadata.

We reuse ideas from test_updater_sha.py but broaden coverage.

Each section has TODO markers that will be filled in progressively.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
import logging
import sys
import datetime
import hashlib
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.updater import UpdateChecker  # type: ignore  # noqa: E402
from src.version import Version  # type: ignore  # noqa: E402

# --- Shared dummy objects (kept minimal; specialized versions may be defined per test) ---

class DummyGUIExtended:
    def __init__(self):
        self.progress_events: list[int] = []
        self.status_updates: int = 0
        self.cancel = False
        self.up_to_date_calls = 0
        self.update_available_prompts: list[Version] = []
        self.reminder_choices: list[bool] = []
        self.errors: list[tuple[str,str]] = []
        self.download_errors: list[str] = []
        self.closed = False
        self.started = False
        self.finished = False

        # Configurable scripted responses
        self._update_available_response: Any = None  # None -> cancel, True -> yes, False -> no
        self._reminder_choice_response: bool = False

    # ----- GUI callback interface -----
    def show_up_to_date(self):  # Used when forced check and nothing new or treated as up to date
        self.up_to_date_calls += 1

    def show_update_available(self, latest_version: Version):
        self.update_available_prompts.append(latest_version)
        return self._update_available_response

    def show_update_reminder_choice(self):
        self.reminder_choices.append(self._reminder_choice_response)
        return self._reminder_choice_response

    def show_update_error_retry(self, msg: str):
        self.errors.append(("retry", msg))
        # For simplicity never retry in baseline tests
        return False

    def show_download_error(self, msg: str):
        self.errors.append(("download", msg))
        self.download_errors.append(msg)

    def setup_download_progress(self, total_size: int):
        # We rely on update_download_progress to accumulate size
        self._expected_total = total_size
        self._progress_value = 0.0

    def update_download_progress(self, data_size: int):
        self._progress_value += data_size
        self.progress_events.append(data_size)

    def update_download_status(self, start_time: float, total_size: int):
        # Count invocations; detailed formatting not needed for logic tests
        self.status_updates += 1

    def close_application(self):
        self.closed = True

    def get_progress_value(self) -> float:
        return self._progress_value

    def start_download_ui(self):
        self.started = True

    def is_download_cancelled(self) -> bool:
        return self.cancel

    def finish_download_ui(self):
        self.finished = True

class DummySettingsExtended:
    def __init__(self, overrides: dict[str,str] | None = None):
        base = {
            "beta": "False",
            "newest_version_available": "0.0.0",
            "ask_for_update": "True",
            "version": "0.0.0",
            "update_channel": "stable",
            "verify_sha": "True",
            "update_cache_ttl_seconds": "86400",
        }
        if overrides:
            base.update(overrides)
        self.settings = base

    def update_setting(self, k: str, v: str):
        self.settings[k] = v

class DummyAppExtended:
    def __init__(self, overrides: dict[str,str] | None = None):
        self.update_dialogs = DummyGUIExtended()
        self.app_settings = DummySettingsExtended(overrides)
        self.on_version_update = lambda latest, current: None

# --- Helpers / Monkeypatch utilities ---

class PatchRequestsGet:
    """Patch requests.get with scripted responses.
    script: list of call handlers -> each handler receives (url, kwargs) and returns an object with .status_code, .headers, .iter_content(), .text, .json().
    If handler raises, that simulates network errors.
    """
    def __init__(self, script):
        import requests  # local import
        self._requests = requests
        self._orig = requests.get
        self._script = script
        self.calls: list[str] = []
        self.index = 0

    def __enter__(self):
        def _fake_get(url, *a, **kw):
            self.calls.append(url)
            if self.index >= len(self._script):
                raise AssertionError("Unexpected extra requests.get call: " + url)
            handler = self._script[self.index]
            self.index += 1
            return handler(url, *a, **kw)
        self._requests.get = _fake_get  # type: ignore
        return self

    def __exit__(self, exc_type, exc, tb):
        self._requests.get = self._orig  # type: ignore
        return False

class DummyResponse:
    def __init__(self, *, status: int = 200, headers: dict[str,str] | None = None, json_data=None, text: str = "", body: bytes | None = None, chunk: int = 1024):
        self.status_code = status
        self._headers = headers or {}
        self._json_data = json_data
        self.text = text
        self._body = body or b""
        self._chunk = chunk
        self._raised = False

    @property
    def headers(self):
        return self._headers

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._json_data is None:
            return {}
        return self._json_data

    def iter_content(self, chunk_size: int):
        data = self._body
        for i in range(0, len(data), chunk_size):
            yield data[i:i+chunk_size]

# Placeholder tests (implemented progressively)

def test_placeholder_sanity():
    app = DummyAppExtended()
    uc = UpdateChecker(app)  # type: ignore[arg-type]
    # Sanity: initial guards false
    assert not uc._active_check and not uc._active_download

# Cache tests

def _fake_cache_functions(monkey: dict, load_result, expect_saved: bool = False):
    calls = {"load": 0, "save": 0, "invalidate": 0}

    def fake_load():
        calls["load"] += 1
        return load_result

    def fake_save(*a, **k):
        calls["save"] += 1

    def fake_invalidate(*a, **k):
        calls["invalidate"] += 1

    monkey["load_update_cache"] = fake_load
    monkey["save_update_cache"] = fake_save
    monkey["invalidate_releases_cache"] = fake_invalidate
    return calls

def test_cache_hit_uses_cached_version():
    """When cache TTL not expired and not force_check, should use cached version and not call network."""
    app = DummyAppExtended({"update_cache_ttl_seconds": "3600"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    # Monkeypatch cache functions in module namespace
    import src.utils.updater as updater_mod  # type: ignore
    original_load = updater_mod.load_update_cache
    original_save = updater_mod.save_update_cache
    original_inval = updater_mod.invalidate_releases_cache
    try:
        now = datetime.datetime.now()
        cached_version = Version.from_str("2.3.4")
        def fake_load():
            return now - datetime.timedelta(seconds=10), cached_version
        def fake_save(*a, **k):
            raise AssertionError("save_update_cache should not be called on cache hit")
        def fake_invalidate(*a, **k):
            raise AssertionError("invalidate_releases_cache should not be called without force_check")
        updater_mod.load_update_cache = fake_load  # type: ignore
        updater_mod.save_update_cache = fake_save  # type: ignore
        updater_mod.invalidate_releases_cache = fake_invalidate  # type: ignore

        current = Version.from_str("1.0.0")
        latest = uc.check_for_app_updates(current_version=current, force_check=False, quiet=True)
        assert latest == cached_version, "Expected cached version returned"
        # Because we returned early, last_download_url should remain None
        assert uc.last_download_url is None
    finally:
        updater_mod.load_update_cache = original_load  # type: ignore
        updater_mod.save_update_cache = original_save  # type: ignore
        updater_mod.invalidate_releases_cache = original_inval  # type: ignore


def test_force_check_invalidates_cache():
    # Disable sha requirement so a missing sha won't short-circuit; provide an exe asset in fake fetch
    app = DummyAppExtended({"update_cache_ttl_seconds": "3600", "verify_sha": "False"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    import src.utils.updater as updater_mod  # type: ignore
    original_load = updater_mod.load_update_cache
    original_save = updater_mod.save_update_cache
    original_inval = updater_mod.invalidate_releases_cache
    original_fetch = uc._fetch_release_info
    try:
        # load should be called but ignored due to force
        def fake_load():
            return datetime.datetime.now() - datetime.timedelta(seconds=10), Version.from_str("9.9.9")
        invalidated = {"done": False}
        def fake_invalidate():
            invalidated["done"] = True
        # Provide a minimal latest release response
        def fake_fetch(url: str):
            return {"tag_name": "3.0.0", "assets": [{"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"}], "prerelease": False}
        saved = {"count": 0}
        def fake_save(*a, **k):
            saved["count"] += 1
        updater_mod.load_update_cache = fake_load  # type: ignore
        updater_mod.invalidate_releases_cache = fake_invalidate  # type: ignore
        updater_mod.save_update_cache = fake_save  # type: ignore
        uc._fetch_release_info = fake_fetch  # type: ignore

        current = Version.from_str("2.0.0")
        latest = uc.check_for_app_updates(current_version=current, force_check=True, quiet=True)
        assert invalidated["done"], "Expected cache invalidation on force check"
        assert latest == Version.from_str("3.0.0")
        assert saved["count"] == 1, "Expected save after forced fetch"
    finally:
        updater_mod.load_update_cache = original_load  # type: ignore
        updater_mod.save_update_cache = original_save  # type: ignore
        updater_mod.invalidate_releases_cache = original_inval  # type: ignore
        uc._fetch_release_info = original_fetch  # type: ignore


def test_active_check_guard_returns_existing_version():
    app = DummyAppExtended()
    uc = UpdateChecker(app)  # type: ignore[arg-type]
    # Simulate an already active check
    uc._active_check = True
    app.app_settings.update_setting("newest_version_available", "5.6.7")
    result = uc.check_for_app_updates(current_version=Version.from_str("1.0.0"), force_check=False, quiet=True)
    assert result == Version.from_str("5.6.7"), "Expected guard path to return stored newest_version_available"
    # Should not flip the guard off because we never set it (but function returns early)
    assert uc._active_check is True

# Prompt logic tests

def test_prompt_cancel_path_no_download_thread():
    app = DummyAppExtended()
    gui = app.update_dialogs
    gui._update_available_response = None  # cancel
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    def fake_fetch(url: str):
        # Provide both exe and sha so validation passes and prompt is shown
        return {"tag_name": "1.2.0", "assets": [
            {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
            {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
        ], "prerelease": False}
    uc._fetch_release_info = fake_fetch  # type: ignore

    current = Version.from_str("1.0.0")
    uc._get_latest_version_from_github(current_version=current, force_check=True)
    # Cancel means no download triggered; last_download_url still cached though (since we set before thread?)
    # In current implementation caching happens before prompt; so we expect it populated.
    assert uc.last_download_url is not None
    assert uc._active_download is False


def test_prompt_yes_starts_download_thread_without_sha_when_verify_false():
    app = DummyAppExtended({"verify_sha": "False"})
    gui = app.update_dialogs
    gui._update_available_response = True  # yes
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    # Provide release with exe & sha
    def fake_fetch(url: str):
        return {"tag_name": "2.0.0", "assets": [
            {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
            {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
        ], "prerelease": False}
    uc._fetch_release_info = fake_fetch  # type: ignore

    # Patch download_and_run_installer to avoid network and assert args
    called: dict[str, Optional[tuple[str, str | None]]] = {"args": None}
    def fake_download(url, sha):
        called["args"] = (url, sha)
    uc.download_and_run_installer = fake_download  # type: ignore

    current = Version.from_str("1.0.0")
    uc._get_latest_version_from_github(current_version=current, force_check=True)
    # Because verify_sha False, sha passed to download should be None
    assert called["args"] is not None
    assert called["args"][1] is None, "Expected sha_url suppressed when verify_sha False"


def test_prompt_no_sets_reminder_flag_when_user_chooses_remind_later():
    app = DummyAppExtended()
    gui = app.update_dialogs
    gui._update_available_response = False  # user clicked No
    gui._reminder_choice_response = True  # user chooses don't remind again
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    def fake_fetch(url: str):
        return {"tag_name": "3.1.0", "assets": [
            {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
            {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
        ], "prerelease": False}
    uc._fetch_release_info = fake_fetch  # type: ignore

    current = Version.from_str("3.0.0")
    uc._get_latest_version_from_github(current_version=current, force_check=True)
    assert app.app_settings.settings["ask_for_update"] == "False", "Expected suppression flag set when user chooses reminder opt-out"


def test_prompt_yes_caches_metadata():
    app = DummyAppExtended()
    gui = app.update_dialogs
    gui._update_available_response = True
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    def fake_fetch(url: str):
        return {"tag_name": "4.0.0", "assets": [
            {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
            {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
        ], "prerelease": False}
    uc._fetch_release_info = fake_fetch  # type: ignore

    # Avoid spawning thread network path
    uc.download_and_run_installer = lambda *a, **k: None  # type: ignore

    current = Version.from_str("3.0.0")
    uc._get_latest_version_from_github(current_version=current, force_check=True)
    assert uc.last_download_url == "https://example/installer.exe"
    assert uc.last_sha_url == "https://example/installer.exe.sha256"
    assert uc.last_version_tag == "4.0.0"

# Download tests

def test_download_success_without_sha():
    app = DummyAppExtended({"verify_sha": "False"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    body = b"A" * (512 * 1024)  # 512 KiB (< 4MB triggers small chunk mode)
    # Patch requests.get for the download
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get
    def fake_get(url, stream=False, timeout=(10,60)):
        assert stream
        return DummyResponse(headers={"content-length": str(len(body))}, body=body)
    updater_mod.requests.get = fake_get  # type: ignore
    try:
        uc.download_and_run_installer("https://example/installer.exe", None)
        # Verify GUI callbacks received
        assert app.update_dialogs.started and app.update_dialogs.finished
        assert app.update_dialogs.get_progress_value() == len(body)
        assert app.update_dialogs.status_updates >= 1
    finally:
        updater_mod.requests.get = original_get  # type: ignore


def test_download_cancel_midstream():
    app = DummyAppExtended({"verify_sha": "False"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    chunks = [b"X" * 200000] * 10  # produce multiple chunks
    class CancelResponse(DummyResponse):
        def iter_content(self, chunk_size: int):
            for i, c in enumerate(chunks):
                if i == 2:  # cancel after a couple chunks
                    app.update_dialogs.cancel = True
                yield c
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get
    def fake_get(url, stream=False, timeout=(10,60)):
        return CancelResponse(headers={"content-length": str(len(b''.join(chunks)))}, body=b"".join(chunks))
    updater_mod.requests.get = fake_get  # type: ignore
    try:
        uc.download_and_run_installer("https://example/installer.exe", None)
        # Because cancelled, application should not be closed (download stops early)
        assert app.update_dialogs.cancel is True
        assert app.update_dialogs.get_progress_value() < len(b"".join(chunks))
        assert uc._active_download is False
    finally:
        updater_mod.requests.get = original_get  # type: ignore


def test_download_size_mismatch_warning():
    app = DummyAppExtended({"verify_sha": "False"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    body = b"B" * (300 * 1024)
    class MismatchResponse(DummyResponse):
        def iter_content(self, chunk_size: int):
            # Only yield half intentionally
            yield body[: len(body)//2]
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get
    logged = {"warn": False}
    orig_logger = logging.getLogger("updater").warning
    def fake_warn(*a, **k):
        logged["warn"] = True
        orig_logger(*a, **k)
    logging.getLogger("updater").warning = fake_warn  # type: ignore
    def fake_get(url, stream=False, timeout=(10,60)):
        # Claim full length but send only half
        return MismatchResponse(headers={"content-length": str(len(body))}, body=body)
    updater_mod.requests.get = fake_get  # type: ignore
    try:
        uc.download_and_run_installer("https://example/installer.exe", None)
        assert logged["warn"], "Expected size mismatch warning logged"
    finally:
        updater_mod.requests.get = original_get  # type: ignore
        logging.getLogger("updater").warning = orig_logger  # type: ignore

def test_sha_verification_success():
    app = DummyAppExtended({"verify_sha": "True"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    installer_bytes = b"installer-bytes-12345"
    sha_hex = hashlib.sha256(installer_bytes).hexdigest()

    # Patch requests.get for download then sha
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get

    def get_sequence():
        states = {"step": 0}
        def _get(url, stream=False, timeout=(10,60)):
            if states["step"] == 0:
                states["step"] += 1
                return DummyResponse(headers={"content-length": str(len(installer_bytes))}, body=installer_bytes)
            else:
                return DummyResponse(text=f"{sha_hex}  installer.exe\n")
        return _get

    updater_mod.requests.get = get_sequence()  # type: ignore

    # Patch _spawn_installer to avoid executing
    uc._spawn_installer = lambda p: None  # type: ignore

    try:
        uc.download_and_run_installer("https://example/installer.exe", "https://example/installer.exe.sha256")
        assert not app.update_dialogs.errors, f"Unexpected errors: {app.update_dialogs.errors}"
    finally:
        updater_mod.requests.get = original_get  # type: ignore


def test_sha_invalid_format_error():
    app = DummyAppExtended({"verify_sha": "True"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    body = b"X" * 1024
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get

    def get_sequence():
        states = {"step": 0}
        def _get(url, stream=False, timeout=(10,60)):
            if states["step"] == 0:
                states["step"] += 1
                return DummyResponse(headers={"content-length": str(len(body))}, body=body)
            else:
                return DummyResponse(text="NOT_A_SHA_VALUE")
        return _get
    updater_mod.requests.get = get_sequence()  # type: ignore
    uc._spawn_installer = lambda p: None  # type: ignore
    try:
        uc.download_and_run_installer("https://example/installer.exe", "https://example/installer.exe.sha256")
        assert any("download" in e and "Invalid" in m for e,m in app.update_dialogs.errors), "Expected invalid sha format error"
    finally:
        updater_mod.requests.get = original_get  # type: ignore


def test_sha_network_error():
    app = DummyAppExtended({"verify_sha": "True"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    body = b"DATA" * 256
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get
    import requests

    def get_sequence():
        states = {"step": 0}
        def _get(url, stream=False, timeout=(10,60)):
            if states["step"] == 0:
                states["step"] += 1
                return DummyResponse(headers={"content-length": str(len(body))}, body=body)
            raise requests.ConnectionError("network down")
        return _get
    updater_mod.requests.get = get_sequence()  # type: ignore
    uc._spawn_installer = lambda p: None  # type: ignore
    try:
        uc.download_and_run_installer("https://example/installer.exe", "https://example/installer.exe.sha256")
        assert any("download" == e and "network down" in m for e,m in app.update_dialogs.errors), "Expected network error surfaced"
    finally:
        updater_mod.requests.get = original_get  # type: ignore


def test_sha_io_error_reading_installer(tmp_path: Path | None = None):
    # Simulate removing installer file before sha verify to trigger OSError
    app = DummyAppExtended({"verify_sha": "True"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    body = b"DATA" * 256
    import src.utils.updater as updater_mod  # type: ignore
    original_get = updater_mod.requests.get

    # We'll delete the file right after download by monkeypatching _verify_sha256 to call original but after deletion
    real_verify = uc._verify_sha256

    def delete_then_verify(path, sha_url):
        try:
            os.unlink(path)
        except OSError:
            pass
        return real_verify(path, sha_url)

    uc._verify_sha256 = delete_then_verify  # type: ignore

    def get_sequence():
        states = {"step": 0}
        def _get(url, stream=False, timeout=(10,60)):
            if states["step"] == 0:
                states["step"] += 1
                return DummyResponse(headers={"content-length": str(len(body))}, body=body)
            # Provide valid sha so failure is due to IO
            digest = hashlib.sha256(body).hexdigest()
            return DummyResponse(text=f"{digest}  installer.exe\n")
        return _get

    updater_mod.requests.get = get_sequence()  # type: ignore
    uc._spawn_installer = lambda p: None  # type: ignore
    try:
        uc.download_and_run_installer("https://example/installer.exe", "https://example/installer.exe.sha256")
        # Expect an error referencing file or similar
        assert any(e == "download" for e,_ in app.update_dialogs.errors), "Expected download error from IO issue"
    finally:
        updater_mod.requests.get = original_get  # type: ignore
        uc._verify_sha256 = real_verify  # type: ignore

# Channel tests

def test_channel_stable_skips_prerelease():
    app = DummyAppExtended({"update_channel": "stable"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    assets = [
        {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
        {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
    ]
    stable_release = {"tag_name": "2.0.0", "assets": assets, "prerelease": False}
    prerelease = {"tag_name": "2.1.0-rc1", "assets": assets, "prerelease": True}

    def fake_fetch(url: str):
        if url.endswith("/latest"):
            return stable_release
        return [prerelease, stable_release]

    uc._fetch_release_info = fake_fetch  # type: ignore

    current = Version.from_str("1.0.0")
    latest = uc._get_latest_version_from_github(current_version=current, force_check=True, quiet=True)
    assert latest == Version.from_str("2.0.0"), "Stable channel should not adopt prerelease tag"


def test_channel_rc_adopts_newer_prerelease():
    app = DummyAppExtended({"update_channel": "rc"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    assets = [
        {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
        {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
    ]
    stable_release = {"tag_name": "2.0.0", "assets": assets, "prerelease": False}
    prerelease = {"tag_name": "2.1.0-rc1", "assets": assets, "prerelease": True}

    def fake_fetch(url: str):
        if url.endswith("/latest"):
            return stable_release
        return [prerelease, stable_release]

    uc._fetch_release_info = fake_fetch  # type: ignore

    current = Version.from_str("1.0.0")
    latest = uc._get_latest_version_from_github(current_version=current, force_check=True, quiet=True)
    assert latest == Version.from_str("2.1.0-rc1"), "RC channel should adopt newer prerelease"

def test_concurrency_download_guard():
    app = DummyAppExtended({"verify_sha": "False"})
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    # First call sets _active_download and blocks second call
    uc._active_download = True
    uc.download_and_run_installer("https://example/installer.exe", None)
    # Nothing to assert beyond no exception and still active
    assert uc._active_download is True


def test_concurrency_check_guard_and_release():
    app = DummyAppExtended()
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    # Patch fetch to simulate slight delay so we can assert guard flips back
    def fake_fetch(url: str):
        return {"tag_name": "1.0.1", "assets": [
            {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
            {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
        ], "prerelease": False}
    uc._fetch_release_info = fake_fetch  # type: ignore

    current = Version.from_str("1.0.0")
    latest = uc.check_for_app_updates(current, force_check=True, quiet=True)
    assert latest == Version.from_str("1.0.1")
    assert uc._active_check is False, "Guard should release after check"


def test_metadata_set_on_successful_check():
    app = DummyAppExtended()
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    def fake_fetch(url: str):
        return {"tag_name": "7.8.9", "assets": [
            {"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"},
            {"name": "heat_sheet_pdf_highlighter_installer.exe.sha256", "browser_download_url": "https://example/installer.exe.sha256"},
        ], "prerelease": False}
    uc._fetch_release_info = fake_fetch  # type: ignore

    current = Version.from_str("7.0.0")
    uc._get_latest_version_from_github(current_version=current, force_check=True, quiet=True)
    assert uc.last_download_url == "https://example/installer.exe"
    assert uc.last_sha_url == "https://example/installer.exe.sha256"
    assert uc.last_version_tag == "7.8.9"
