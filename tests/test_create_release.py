import os
from types import SimpleNamespace

import create_release


def test_local_release_flow_skips_pause_without_simulating_ci(monkeypatch):
    observed_env = {}

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("HSPH_SKIP_BUILD_PAUSE", raising=False)
    monkeypatch.setattr(create_release, "_load_version_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "update_version", lambda version: None)
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: None)

    def fake_build_project():
        observed_env["github_actions"] = os.getenv("GITHUB_ACTIONS")
        observed_env["skip_pause"] = os.getenv("HSPH_SKIP_BUILD_PAUSE")

    monkeypatch.setattr(create_release, "build_project", fake_build_project)

    create_release._run_local_release_flow("1.5.0", no_build=False, screenshot_pdf=None)

    assert observed_env == {"github_actions": None, "skip_pause": "true"}
    assert os.getenv("HSPH_SKIP_BUILD_PAUSE") is None


def test_local_release_flow_restores_lockfile_after_temporary_build(tmp_path, monkeypatch):
    file_map = {
        "PYPROJECT_TOML": tmp_path / "pyproject.toml",
        "SETUP_PY": tmp_path / "setup.py",
        "SETUP_ISS": tmp_path / "setup.iss",
        "CONSTANTS_PY": tmp_path / "constants.py",
        "UV_LOCK": tmp_path / "uv.lock",
    }
    for attr_name, path in file_map.items():
        path.write_text(f"original-{attr_name}", encoding="utf-8")
        monkeypatch.setattr(create_release, attr_name, path)

    def fake_update_version(version):
        for attr_name in ("PYPROJECT_TOML", "SETUP_PY", "SETUP_ISS", "CONSTANTS_PY"):
            getattr(create_release, attr_name).write_text(f"temporary-{version}", encoding="utf-8")

    def fake_build_project():
        create_release.UV_LOCK.write_text("temporary-lock", encoding="utf-8")

    monkeypatch.setattr(create_release, "update_version", fake_update_version)
    monkeypatch.setattr(create_release, "build_project", fake_build_project)
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: None)

    create_release._run_local_release_flow("1.5.0", no_build=False, screenshot_pdf=None)

    for attr_name, path in file_map.items():
        assert path.read_text(encoding="utf-8") == f"original-{attr_name}"


def test_release_flow_refreshes_lockfile_before_staging(monkeypatch):
    calls = []

    monkeypatch.setattr(create_release, "update_version", lambda version: calls.append("version"))
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: calls.append("lock"))
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: calls.append("screenshots"))
    monkeypatch.setattr(create_release, "_collect_release_artifacts", lambda: [])
    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: SimpleNamespace(returncode=0))
    monkeypatch.setattr(create_release, "ensure_ssh_signing", lambda: calls.append("signing"))
    monkeypatch.setattr(create_release, "create_and_push_signed_tag", lambda version: calls.append("tag"))

    create_release._run_release_flow("1.5.0", screenshot_pdf=None)

    assert calls[:3] == ["version", "lock", "screenshots"]
