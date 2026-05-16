from pathlib import Path

import sync_version


def _write_pyproject(path: Path, version: str) -> None:
    path.write_text(f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8")


def test_stable_version_derivation():
    artifacts = sync_version.derive_version_artifacts("1.5.0")

    assert artifacts.display_version == "1.5.0"
    assert artifacts.numeric_version == "1.5.0.0"


def test_rc_version_derivation():
    artifacts = sync_version.derive_version_artifacts("1.5.0rc2")

    assert artifacts.display_version == "1.5.0rc2"
    assert artifacts.numeric_version == "1.5.0.0"


def test_invalid_version_rejected():
    try:
        sync_version.derive_version_artifacts("1.5.0-beta1")
    except ValueError as exc:
        assert "Unsupported version" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("Expected beta version to be rejected.")


def test_release_version_rejects_legacy_hyphenated_rc_form():
    try:
        sync_version.validate_release_version("1.5.0-rc1")
    except ValueError as exc:
        assert "Unsupported release version" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("Expected legacy hyphenated rc form to be rejected for release tags.")


def test_check_passes_when_generated_files_match(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    runtime = tmp_path / "_version.py"
    inno = tmp_path / "setup_version.iss"
    _write_pyproject(pyproject, "1.5.0rc1")

    sync_version.write_generated_files(
        pyproject_path=pyproject,
        runtime_version_path=runtime,
        inno_version_path=inno,
    )

    stale = sync_version.find_stale_generated_files(
        pyproject_path=pyproject,
        runtime_version_path=runtime,
        inno_version_path=inno,
    )

    assert stale == []


def test_check_reports_stale_generated_files(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    runtime = tmp_path / "_version.py"
    inno = tmp_path / "setup_version.iss"
    _write_pyproject(pyproject, "1.5.0")
    sync_version.write_generated_files(
        pyproject_path=pyproject,
        runtime_version_path=runtime,
        inno_version_path=inno,
    )
    runtime.write_text("stale", encoding="utf-8")

    stale = sync_version.find_stale_generated_files(
        pyproject_path=pyproject,
        runtime_version_path=runtime,
        inno_version_path=inno,
    )

    assert stale == [runtime]


def test_read_project_version_reports_missing_project_version(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\n', encoding="utf-8")

    try:
        sync_version.read_project_version(pyproject)
    except ValueError as exc:
        assert "Missing [project].version" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("Expected missing project.version to be rejected.")


def test_read_project_version_requires_string(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "demo"\nversion = 150\n', encoding="utf-8")

    try:
        sync_version.read_project_version(pyproject)
    except ValueError as exc:
        assert "must be a string" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("Expected non-string project.version to be rejected.")


def test_main_write_mode(monkeypatch):
    calls = []
    monkeypatch.setattr(sync_version, "write_generated_files", lambda: calls.append("write"))

    assert sync_version.main(["--write"]) == 0
    assert calls == ["write"]


def test_main_check_mode_reports_stale(monkeypatch, capsys):
    monkeypatch.setattr(sync_version, "find_stale_generated_files", lambda: [sync_version.Path("src/_version.py")])

    assert sync_version.main(["--check"]) == 1
    assert "Generated version artifacts are stale" in capsys.readouterr().err


def test_main_reports_invalid_project_metadata(monkeypatch, capsys):
    monkeypatch.setattr(
        sync_version,
        "find_stale_generated_files",
        lambda: (_ for _ in ()).throw(ValueError("broken metadata")),
    )

    assert sync_version.main(["--check"]) == 1
    assert "broken metadata" in capsys.readouterr().err


def test_main_check_mode_passes_when_current(monkeypatch):
    monkeypatch.setattr(sync_version, "find_stale_generated_files", lambda: [])

    assert sync_version.main(["--check"]) == 0
