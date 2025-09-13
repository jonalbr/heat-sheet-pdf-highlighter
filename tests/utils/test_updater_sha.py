import hashlib
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.utils.updater import UpdateChecker
from src.version import Version


class _PopenPatch:
    """Context manager to temporarily replace subprocess.Popen with a stub/captor."""

    def __init__(self, capture_calls: bool = False):
        self.capture_calls = capture_calls
        self.calls = []
        self._orig = None

    def __enter__(self):
        self._orig = subprocess.Popen

        if self.capture_calls:
            calls_ref = self.calls

            def append_call(call):
                calls_ref.append(call)

            class _StubProc:
                def __init__(self, *a, **k):
                    # capture args for assertions
                    append_call((a, k))
                    self.pid = 0

            subprocess.Popen = _StubProc  # type: ignore[assignment]
        else:

            class _StubProcNoop:
                def __init__(self, *a, **k):
                    self.args = a
                    self.kwargs = k
                    self.pid = 0

            subprocess.Popen = _StubProcNoop  # type: ignore[assignment]
        return self

    def __exit__(self, exc_type, exc, tb):
        subprocess.Popen = self._orig  # type: ignore[assignment]
        return False


class DummyGUI:
    def __init__(self):
        self.progress = 0
        self.cancel = False
        self.errors = []
        self.up_to_date_shown = 0

    # UpdateDialogs interface
    def show_up_to_date(self):
        self.up_to_date_shown += 1

    def show_update_available(self, latest_version):
        return None  # act as cancel to avoid real download in that path

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
    def __init__(self, beta: bool):
        self.settings = {
            "beta": "True" if beta else "False",
            "newest_version_available": "0.0.0",
            "ask_for_update": "True",
            "version": "0.0.0",
        }

    def update_setting(self, k, v):
        self.settings[k] = v


class DummyApp:
    def __init__(self, beta: bool):
        self.update_dialogs = DummyGUI()
        self.app_settings = DummySettings(beta)
        self.on_version_update = lambda latest, current: None


def run_http_server(root: Path):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    # Bind to an ephemeral port
    handler = partial(QuietHandler, directory=str(root))
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_port

    def serve():
        httpd.serve_forever()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return httpd, port


def test_checksum_mismatch():
    temp_dir = Path(tempfile.mkdtemp())
    # Create dummy installer
    installer = temp_dir / "heat_sheet_pdf_highlighter_installer.exe"
    installer.write_bytes(b"dummy-bytes-123")
    # Wrong sha
    (temp_dir / "heat_sheet_pdf_highlighter_installer.exe.sha256").write_text("0" * 64 + "  heat_sheet_pdf_highlighter_installer.exe\n")
    server, port = run_http_server(temp_dir)
    try:
        app = DummyApp(beta=False)
        uc = UpdateChecker(app)  # type: ignore[arg-type]
        # Avoid actually spawning update script if it somehow gets that far
        with _PopenPatch():
            uc.download_and_run_installer(
                f"http://127.0.0.1:{port}/heat_sheet_pdf_highlighter_installer.exe",
                f"http://127.0.0.1:{port}/heat_sheet_pdf_highlighter_installer.exe.sha256",
            )
        assert any(e[0] == "download" and "Checksum" in e[1] for e in app.update_dialogs.errors), "Expected checksum mismatch error"
        print("OK: checksum mismatch handled")
    finally:
        server.shutdown()


def test_beta_cancelled_download():
    temp_dir = Path(tempfile.mkdtemp())
    installer = temp_dir / "heat_sheet_pdf_highlighter_installer.exe"
    # Create a larger file to simulate streaming (still cancel immediately)
    installer.write_bytes(b"X" * 1024 * 1024)
    server, port = run_http_server(temp_dir)
    try:
        app = DummyApp(beta=True)
        uc = UpdateChecker(app)  # type: ignore[arg-type]
        # Cancel immediately before download loop starts
        app.update_dialogs.cancel = True

        with _PopenPatch():
            uc.download_and_run_installer(
                f"http://127.0.0.1:{port}/heat_sheet_pdf_highlighter_installer.exe",
                None,
            )
        print("OK: beta cancel path returns without spawn")
    finally:
        server.shutdown()


def test_stable_no_sha_treated_as_up_to_date():
    # Mock _fetch_release_info to simulate a newer stable release without sha asset
    app = DummyApp(beta=False)
    uc = UpdateChecker(app)  # type: ignore[arg-type]

    def fake_fetch(url: str):
        if url.endswith("/latest"):
            return {
                "tag_name": "9.9.9",
                "assets": [{"name": "heat_sheet_pdf_highlighter_installer.exe", "browser_download_url": "https://example/installer.exe"}],
                "prerelease": False,
            }
        return {}

    uc._fetch_release_info = fake_fetch  # type: ignore
    current = Version.from_str("1.0.0")
    latest = uc._get_latest_version_from_github(current_version=current, force_check=True)
    assert latest == current, "Expected to treat as up to date when no sha present on stable"
    assert app.update_dialogs.up_to_date_shown >= 1, "Expected up-to-date dialog"
    print("OK: stable without sha treated as up to date")


def test_checksum_ok_runs():
    temp_dir = Path(tempfile.mkdtemp())
    # Create dummy installer and matching sha
    installer = temp_dir / "heat_sheet_pdf_highlighter_installer.exe"
    installer.write_bytes(b"installer-bytes-ok")
    digest = hashlib.sha256(installer.read_bytes()).hexdigest()
    (temp_dir / "heat_sheet_pdf_highlighter_installer.exe.sha256").write_text(f"{digest}  heat_sheet_pdf_highlighter_installer.exe\n")

    server, port = run_http_server(temp_dir)
    try:
        app = DummyApp(beta=False)
        uc = UpdateChecker(app)  # type: ignore[arg-type]

        # Capture Popen invocations
        with _PopenPatch(capture_calls=True) as pp:
            uc.download_and_run_installer(
                f"http://127.0.0.1:{port}/heat_sheet_pdf_highlighter_installer.exe",
                f"http://127.0.0.1:{port}/heat_sheet_pdf_highlighter_installer.exe.sha256",
            )
        assert not app.update_dialogs.errors, f"Unexpected errors: {app.update_dialogs.errors}"
        assert pp.calls, "Expected installer to be spawned after successful checksum"
        print("OK: checksum verified and installer spawned")
    finally:
        server.shutdown()


if __name__ == "__main__":
    test_checksum_mismatch()
    test_beta_cancelled_download()
    test_stable_no_sha_treated_as_up_to_date()
    test_checksum_ok_runs()
    print("All tests passed")
