from contextlib import contextmanager
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import hashlib
import subprocess
import threading

import pytest

from src.utils.updater import UpdateChecker
from src.version import Version


class DummyGUI:
    def __init__(self):
        self.cancel = False
        self.errors = []
        self.progress = 0
        self.up_to_date_shown = 0

    def show_up_to_date(self):
        self.up_to_date_shown += 1

    def show_update_available(self, latest_version):
        return None

    def show_update_reminder_choice(self):
        return False

    def show_update_error_retry(self, msg: str):
        self.errors.append(("retry", msg))
        return False

    def show_download_error(self, msg: str):
        self.errors.append(("download", msg))

    def setup_download_progress(self, total_size: int):
        self.progress = 0

    def update_download_progress(self, data_size: int):
        self.progress += data_size

    def update_download_status(self, start_time: float, total_size: int):
        pass

    def close_application(self):
        pass

    def get_progress_value(self) -> float:
        return float(self.progress)

    def start_download_ui(self):
        pass

    def is_download_cancelled(self) -> bool:
        return self.cancel

    def finish_download_ui(self):
        pass


class DummySettings:
    def __init__(self):
        self.settings = {
            "newest_version_available": "0.0.0",
            "ask_for_update": "True",
            "version": "0.0.0",
            "update_channel": "stable",
            "verify_sha": "True",
        }

    def update_setting(self, key, value):
        self.settings[key] = value


class DummyApp:
    def __init__(self):
        self.update_dialogs = DummyGUI()
        self.app_settings = DummySettings()
        self.on_version_update = lambda latest, current: None


@pytest.fixture
def app():
    return DummyApp()


@pytest.fixture
def popen_calls(monkeypatch):
    calls = []

    class StubProc:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
            self.pid = 0

    monkeypatch.setattr(subprocess, "Popen", StubProc)
    return calls


@contextmanager
def running_http_server(root: Path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def write_installer(root: Path, body: bytes, sha_text: str | None = None) -> None:
    installer = root / "heat_sheet_pdf_highlighter_installer.exe"
    installer.write_bytes(body)
    if sha_text is not None:
        (root / "heat_sheet_pdf_highlighter_installer.exe.sha256").write_text(sha_text)


def test_checksum_mismatch_reports_download_error(tmp_path, app, popen_calls):
    write_installer(tmp_path, b"dummy-bytes-123", "0" * 64 + "  installer.exe\n")

    with running_http_server(tmp_path) as base_url:
        UpdateChecker(app).download_and_run_installer(
            f"{base_url}/heat_sheet_pdf_highlighter_installer.exe",
            f"{base_url}/heat_sheet_pdf_highlighter_installer.exe.sha256",
        )

    assert any(kind == "download" and "Checksum" in message for kind, message in app.update_dialogs.errors)
    assert popen_calls == []


def test_cancelled_download_stops_before_spawn(tmp_path, app, popen_calls):
    write_installer(tmp_path, b"X" * 1024 * 1024)
    app.update_dialogs.cancel = True

    with running_http_server(tmp_path) as base_url:
        UpdateChecker(app).download_and_run_installer(f"{base_url}/heat_sheet_pdf_highlighter_installer.exe", None)

    assert popen_calls == []


def test_stable_release_without_sha_is_treated_as_up_to_date(app):
    checker = UpdateChecker(app)
    checker._fetch_release_info = lambda url: {
        "tag_name": "9.9.9",
        "assets": [{"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"}],
        "prerelease": False,
    }

    latest = checker._get_latest_version_from_github(current_version=Version.from_str("1.0.0"), force_check=True)

    assert latest == Version.from_str("1.0.0")
    assert app.update_dialogs.up_to_date_shown == 1


def test_matching_checksum_spawns_installer(tmp_path, app, popen_calls):
    installer_body = b"installer-bytes-ok"
    digest = hashlib.sha256(installer_body).hexdigest()
    write_installer(tmp_path, installer_body, f"{digest}  installer.exe\n")

    with running_http_server(tmp_path) as base_url:
        UpdateChecker(app).download_and_run_installer(
            f"{base_url}/heat_sheet_pdf_highlighter_installer.exe",
            f"{base_url}/heat_sheet_pdf_highlighter_installer.exe.sha256",
        )

    assert app.update_dialogs.errors == []
    assert popen_calls
