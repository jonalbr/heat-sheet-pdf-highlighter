import hashlib

import pytest

import build_windows_installer as build


def test_resolve_exec_handles_empty_and_path_hits(monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda executable: "C:\\Tools\\uv.exe" if executable == "uv" else None)

    assert build._resolve_exec([]) == []
    assert build._resolve_exec(["uv", "sync"]) == ["C:\\Tools\\uv.exe", "sync"]
    assert build._resolve_exec(["C:\\Tools\\uv.exe", "sync"]) == ["C:\\Tools\\uv.exe", "sync"]


def test_run_uses_resolved_command(monkeypatch):
    calls = []
    monkeypatch.setattr(build, "_resolve_exec", lambda cmd: ["resolved", *cmd[1:]])
    monkeypatch.setattr(build.subprocess, "run", lambda cmd, check=True: calls.append((cmd, check)) or "done")

    assert build.run(["uv", "sync"], check=False) == "done"
    assert calls == [(["resolved", "sync"], False)]


def test_load_env_file_reads_simple_pairs(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nAppId={{demo-guid}}\nINNO_COMPILER=C:\\Tools\\ISCC.exe\n", encoding="utf-8")
    monkeypatch.delenv("AppId", raising=False)
    monkeypatch.delenv("INNO_COMPILER", raising=False)

    build.load_env_file(env_file)

    assert build.os.environ["AppId"] == "{{demo-guid}}"
    assert build.os.environ["INNO_COMPILER"] == "C:\\Tools\\ISCC.exe"


def test_load_env_file_missing_is_allowed(tmp_path, capsys):
    build.load_env_file(tmp_path / ".env")

    assert ".env not found" in capsys.readouterr().out


def test_ensure_windows_rejects_non_windows(monkeypatch):
    monkeypatch.setattr(build.os, "name", "posix")

    with pytest.raises(build.BuildError):
        build.ensure_windows()


def test_ensure_uv_available_rejects_missing_uv(monkeypatch):
    monkeypatch.setattr(build.shutil, "which", lambda executable: None if executable == "uv" else executable)

    with pytest.raises(build.BuildError):
        build.ensure_uv_available()


def test_ensure_project_venv_accepts_missing_or_matching_env(tmp_path, monkeypatch):
    project_venv = tmp_path / ".venv"
    project_venv.mkdir()
    monkeypatch.setattr(build, "PROJECT_VENV", project_venv)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    build.ensure_project_venv()

    monkeypatch.setenv("VIRTUAL_ENV", str(project_venv))
    build.ensure_project_venv()


def test_ensure_project_venv_rejects_other_env(tmp_path, monkeypatch):
    project_venv = tmp_path / ".venv"
    other_venv = tmp_path / "other"
    project_venv.mkdir()
    other_venv.mkdir()
    monkeypatch.setattr(build, "PROJECT_VENV", project_venv)
    monkeypatch.setenv("VIRTUAL_ENV", str(other_venv))

    with pytest.raises(build.BuildError):
        build.ensure_project_venv()


@pytest.mark.parametrize(
    ("github_actions", "expected"),
    [
        ("", ["uv", "sync", "--all-groups"]),
        ("true", ["uv", "sync", "--locked", "--all-groups"]),
    ],
)
def test_sync_dependencies_uses_locked_mode_only_in_ci(github_actions, expected, monkeypatch):
    calls = []
    if github_actions:
        monkeypatch.setenv("GITHUB_ACTIONS", github_actions)
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(build, "run", lambda cmd: calls.append(cmd))

    build.sync_dependencies()

    assert calls == [expected]


def test_ensure_python_314_accepts_supported_version(monkeypatch):
    monkeypatch.setattr(build.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(build.sys, "version", "3.14.0")

    build.ensure_python_314()


def test_ensure_python_314_rejects_other_versions(monkeypatch):
    monkeypatch.setattr(build.sys, "version_info", (3, 13, 0))
    monkeypatch.setattr(build.sys, "version", "3.13.0")

    with pytest.raises(build.BuildError):
        build.ensure_python_314()


def test_find_inno_compiler_prefers_explicit_path(tmp_path, monkeypatch):
    compiler = tmp_path / "ISCC.exe"
    compiler.write_text("", encoding="utf-8")
    monkeypatch.setenv("INNO_COMPILER", str(compiler))

    assert build.find_inno_compiler() == compiler


def test_generate_installer_checksum(tmp_path):
    installer = tmp_path / "installer.exe"
    output = tmp_path / "installer.exe.sha256"
    installer.write_bytes(b"installer-bytes")

    build.generate_installer_checksum(installer, output)

    expected = hashlib.sha256(b"installer-bytes").hexdigest()
    assert output.read_text(encoding="ascii") == f"{expected}  installer.exe\n"


def test_find_inno_compiler_rejects_missing_explicit_path(monkeypatch):
    monkeypatch.setenv("INNO_COMPILER", "C:\\missing\\ISCC.exe")

    with pytest.raises(build.BuildError):
        build.find_inno_compiler()


def test_find_inno_compiler_uses_path_hit(monkeypatch):
    monkeypatch.delenv("INNO_COMPILER", raising=False)
    monkeypatch.setattr(build.shutil, "which", lambda executable: "C:\\Tools\\ISCC.exe" if executable == "iscc.exe" else None)

    assert build.find_inno_compiler() == build.Path("C:\\Tools\\ISCC.exe")


def test_find_inno_compiler_uses_standard_install_root(tmp_path, monkeypatch):
    compiler = tmp_path / "Inno Setup 6" / "ISCC.exe"
    compiler.parent.mkdir()
    compiler.write_text("", encoding="utf-8")
    monkeypatch.delenv("INNO_COMPILER", raising=False)
    monkeypatch.setattr(build.shutil, "which", lambda _executable: None)
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.delenv("ProgramFiles", raising=False)

    assert build.find_inno_compiler() == compiler


def test_find_inno_compiler_reports_missing_compiler(monkeypatch):
    monkeypatch.delenv("INNO_COMPILER", raising=False)
    monkeypatch.setattr(build.shutil, "which", lambda _executable: None)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramFiles", raising=False)

    with pytest.raises(build.BuildError):
        build.find_inno_compiler()


def test_build_application_and_compile_installer_call_expected_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(build, "run", lambda cmd: calls.append(cmd))

    build.build_application()
    build.compile_installer(build.Path("ISCC.exe"))

    assert calls == [["uv", "run", "cxfreeze", "build"], ["ISCC.exe", "setup.iss"]]


def test_generate_installer_checksum_rejects_missing_installer(tmp_path):
    with pytest.raises(build.BuildError):
        build.generate_installer_checksum(tmp_path / "missing.exe", tmp_path / "missing.sha256")


def test_main_runs_complete_success_path(monkeypatch):
    calls = []
    monkeypatch.setattr(build, "load_env_file", lambda: calls.append("env"))
    monkeypatch.setattr(build, "ensure_windows", lambda: calls.append("windows"))
    monkeypatch.setattr(build, "ensure_uv_available", lambda: calls.append("uv"))
    monkeypatch.setattr(build, "ensure_project_venv", lambda: calls.append("venv"))
    monkeypatch.setattr(build, "sync_dependencies", lambda: calls.append("sync"))
    monkeypatch.setattr(build, "ensure_python_314", lambda: calls.append("python"))
    monkeypatch.setattr(build, "find_inno_compiler", lambda: build.Path("ISCC.exe"))
    monkeypatch.setattr(build, "build_application", lambda: calls.append("build"))
    monkeypatch.setattr(build, "compile_installer", lambda compiler: calls.append(("compile", compiler)))
    monkeypatch.setattr(build, "generate_installer_checksum", lambda: calls.append("checksum"))

    assert build.main() == 0
    assert calls == ["env", "windows", "uv", "venv", "sync", "python", "build", ("compile", build.Path("ISCC.exe")), "checksum"]


def test_main_reports_build_errors(monkeypatch, capsys):
    monkeypatch.setattr(build, "load_env_file", lambda: (_ for _ in ()).throw(build.BuildError("boom")))

    assert build.main() == 1
    assert "boom" in capsys.readouterr().err


def test_main_reports_subprocess_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        build,
        "load_env_file",
        lambda: (_ for _ in ()).throw(build.subprocess.CalledProcessError(7, ["uv", "sync"])),
    )

    assert build.main() == 7
    assert "uv sync" in capsys.readouterr().err
