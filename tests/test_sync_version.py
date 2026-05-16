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

    assert artifacts.display_version == "1.5.0-rc2"
    assert artifacts.numeric_version == "1.5.0.0"


def test_invalid_version_rejected():
    try:
        sync_version.derive_version_artifacts("1.5.0-beta1")
    except ValueError as exc:
        assert "Unsupported version" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("Expected beta version to be rejected.")


def test_release_version_rejects_canonical_rc_form():
    try:
        sync_version.validate_release_version("1.5.0rc1")
    except ValueError as exc:
        assert "Unsupported release version" in str(exc)
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("Expected canonical rc form to be rejected for release tags.")


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
