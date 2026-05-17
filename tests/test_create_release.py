from types import SimpleNamespace

import pytest

import create_release


@pytest.mark.parametrize("version", ["1.5.0", "1.5.0rc1"])
def test_check_version_input_accepts_stable_and_rc(version):
    create_release.check_version_input(version)


def test_check_version_input_rejects_beta():
    with pytest.raises(SystemExit):
        create_release.check_version_input("1.5.0-beta1")


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.5.0", True), ("1.5.0rc1", False)],
)
def test_should_capture_release_screenshots_only_for_stable_versions(version, expected):
    assert create_release._should_capture_release_screenshots(version) is expected


def test_local_release_flow_builds_and_captures_screenshots(monkeypatch):
    calls = []

    monkeypatch.setattr(create_release, "_load_version_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "_load_screenshot_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "update_version", lambda version: None)
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: None)
    monkeypatch.setattr(create_release, "build_project", lambda: calls.append("build"))
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: calls.append("screenshots"))

    create_release._run_local_release_flow("1.5.0", no_build=False, screenshot_pdf=None)

    assert calls == ["build", "screenshots"]


def test_local_release_flow_skips_automatic_screenshots_for_rc(monkeypatch, capsys):
    calls = []

    monkeypatch.setattr(create_release, "_load_version_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "_load_screenshot_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "update_version", lambda version: None)
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: None)
    monkeypatch.setattr(create_release, "build_project", lambda: calls.append("build"))
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: calls.append("screenshots"))

    create_release._run_local_release_flow("1.5.0rc1", no_build=False, screenshot_pdf=None)

    assert calls == ["build"]
    assert "RC release detected" in capsys.readouterr().out


def test_local_release_flow_no_build_skips_screenshots(monkeypatch):
    calls = []

    monkeypatch.setattr(create_release, "_load_version_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "_load_screenshot_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "update_version", lambda version: None)
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: None)
    monkeypatch.setattr(create_release, "build_project", lambda: calls.append("build"))
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: calls.append("screenshots"))

    create_release._run_local_release_flow("1.5.0", no_build=True, screenshot_pdf=None)

    assert calls == []


def test_local_release_flow_restores_lockfile_after_temporary_build(tmp_path, monkeypatch):
    file_map = {
        "PYPROJECT_TOML": tmp_path / "pyproject.toml",
        "RUNTIME_VERSION_PY": tmp_path / "_version.py",
        "INNO_VERSION_ISS": tmp_path / "setup_version.iss",
        "UV_LOCK": tmp_path / "uv.lock",
    }
    for attr_name, path in file_map.items():
        path.write_text(f"original-{attr_name}", encoding="utf-8")
        monkeypatch.setattr(create_release, attr_name, path)

    def fake_update_version(version):
        for attr_name in ("PYPROJECT_TOML", "RUNTIME_VERSION_PY", "INNO_VERSION_ISS"):
            getattr(create_release, attr_name).write_text(f"temporary-{version}", encoding="utf-8")

    def fake_build_project():
        create_release.UV_LOCK.write_text("temporary-lock", encoding="utf-8")

    monkeypatch.setattr(create_release, "update_version", fake_update_version)
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: create_release.UV_LOCK.write_text("temporary-lock", encoding="utf-8"))
    monkeypatch.setattr(create_release, "build_project", fake_build_project)
    monkeypatch.setattr(create_release, "_load_screenshot_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: None)

    create_release._run_local_release_flow("1.5.0", no_build=False, screenshot_pdf=None)

    for attr_name, path in file_map.items():
        assert path.read_text(encoding="utf-8") == f"original-{attr_name}"


def test_local_release_flow_restores_screenshots(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"original-image")

    monkeypatch.setattr(create_release, "_load_version_file_snapshots", lambda: {})
    monkeypatch.setattr(create_release, "_load_screenshot_file_snapshots", lambda: {screenshot: screenshot.read_bytes()})
    monkeypatch.setattr(create_release, "update_version", lambda version: None)
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: None)
    monkeypatch.setattr(create_release, "build_project", lambda: None)
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: screenshot.write_bytes(b"temporary-image"))

    create_release._run_local_release_flow("1.5.0", no_build=False, screenshot_pdf=None)

    assert screenshot.read_bytes() == b"original-image"


def test_local_release_flow_can_keep_regenerated_screenshots(tmp_path, monkeypatch, capsys):
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"original-image")

    monkeypatch.setattr(create_release, "_load_version_file_snapshots", lambda: {})
    monkeypatch.setattr(
        create_release,
        "_load_screenshot_file_snapshots",
        lambda: (_ for _ in ()).throw(AssertionError("screenshots should not be snapshotted")),
    )
    monkeypatch.setattr(create_release, "update_version", lambda version: None)
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: None)
    monkeypatch.setattr(create_release, "build_project", lambda: None)
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: screenshot.write_bytes(b"kept-image"))

    create_release._run_local_release_flow(
        "1.5.0",
        no_build=False,
        screenshot_pdf=None,
        keep_screenshots=True,
    )

    assert screenshot.read_bytes() == b"kept-image"
    assert "regenerated screenshots kept" in capsys.readouterr().out


def test_screenshots_only_flow_captures_without_release_steps(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        create_release,
        "_capture_release_screenshots",
        lambda screenshot_pdf: calls.append(("screenshots", screenshot_pdf)),
    )

    create_release._run_screenshots_only_flow("preview.pdf")

    assert calls == [("screenshots", "preview.pdf")]
    assert "Screenshot flow complete." in capsys.readouterr().out


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


def test_release_flow_skips_automatic_screenshots_for_rc(monkeypatch, capsys):
    calls = []

    monkeypatch.setattr(create_release, "update_version", lambda version: calls.append("version"))
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: calls.append("lock"))
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: calls.append("screenshots"))
    monkeypatch.setattr(create_release, "_collect_release_artifacts", lambda: [])
    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: SimpleNamespace(returncode=0))
    monkeypatch.setattr(create_release, "ensure_ssh_signing", lambda: calls.append("signing"))
    monkeypatch.setattr(create_release, "create_and_push_signed_tag", lambda version: calls.append("tag"))

    create_release._run_release_flow("1.5.0rc1", screenshot_pdf=None)

    assert calls == ["version", "lock", "signing", "tag"]
    assert "RC release detected" in capsys.readouterr().out


def test_update_version_uses_uv_and_regenerates_artifacts(monkeypatch):
    calls = []

    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: calls.append(cmd))
    monkeypatch.setattr(create_release, "write_generated_files", lambda: calls.append(["sync"]))

    create_release.update_version("1.5.0rc1")

    assert calls == [["uv", "version", "--frozen", "1.5.0rc1"], ["sync"]]


def test_refresh_lockfile_runs_only_when_lockfile_exists(tmp_path, monkeypatch):
    calls = []
    lockfile = tmp_path / "uv.lock"
    monkeypatch.setattr(create_release, "UV_LOCK", lockfile)
    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: calls.append(cmd))

    create_release.refresh_lockfile()
    lockfile.write_text("lock", encoding="utf-8")
    create_release.refresh_lockfile()

    assert calls == [["uv", "lock"]]


def test_build_project_requires_driver(tmp_path, monkeypatch):
    monkeypatch.setattr(create_release, "BUILD_SCRIPT", tmp_path / "missing.py")

    with pytest.raises(SystemExit):
        create_release.build_project()


def test_build_project_uses_current_python(tmp_path, monkeypatch):
    script = tmp_path / "build_windows_installer.py"
    script.write_text("", encoding="utf-8")
    calls = []
    monkeypatch.setattr(create_release, "BUILD_SCRIPT", script)
    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: calls.append(cmd))

    create_release.build_project()

    assert calls == [[create_release.sys.executable, str(script.resolve())]]


def test_capture_target_screenshot_skips_when_python_missing(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(create_release.shutil, "which", lambda _name: None)

    create_release._capture_target_screenshot("main", tmp_path / "main.png", "light")

    assert "Python executable not found" in capsys.readouterr().out


def test_capture_target_screenshot_builds_preview_command_and_reports_success(tmp_path, monkeypatch, capsys):
    output = tmp_path / "preview.png"
    calls = []

    def fake_run(cmd, timeout):
        calls.append((cmd, timeout))
        output.write_bytes(b"png")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(create_release.shutil, "which", lambda name: "python.exe" if name == "python" else None)
    monkeypatch.setattr(create_release.subprocess, "run", fake_run)

    create_release._capture_target_screenshot("preview", output, "dark", pdf_for_preview="preview.pdf", delay=0.25)

    assert calls == [
        (
            [
                "python.exe",
                "main.py",
                "--use-default-settings",
                "--screenshot",
                str(output),
                "--screenshot-target",
                "preview",
                "--screenshot-theme",
                "dark",
                "--screenshot-delay",
                "0.25",
                "--screenshot-pdf",
                "preview.pdf",
            ],
            25,
        )
    ]
    assert "Captured dark preview screenshot" in capsys.readouterr().out


def test_capture_target_screenshot_reports_nonzero_and_exceptions(tmp_path, monkeypatch, capsys):
    output = tmp_path / "main.png"
    monkeypatch.setattr(create_release.shutil, "which", lambda name: "python.exe" if name == "python" else None)
    monkeypatch.setattr(create_release.subprocess, "run", lambda cmd, timeout: SimpleNamespace(returncode=2))

    create_release._capture_target_screenshot("main", output, "light")

    monkeypatch.setattr(
        create_release.subprocess,
        "run",
        lambda cmd, timeout: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    create_release._capture_target_screenshot("main", output, "light")

    out = capsys.readouterr().out
    assert "command returned 2" in out
    assert "Screenshot capture error" in out


def test_resolve_exec_and_run_use_path_resolution(monkeypatch):
    calls = []
    monkeypatch.setattr(create_release.shutil, "which", lambda exe: "C:\\Tools\\git.exe" if exe == "git" else None)
    monkeypatch.setattr(
        create_release.subprocess,
        "run",
        lambda cmd, check=True: calls.append((cmd, check)) or SimpleNamespace(returncode=0),
    )

    assert create_release._resolve_exec([]) == []
    create_release.run(["git", "status"], check=False)
    create_release.run(["C:\\Tools\\git.exe", "status"])

    assert calls == [
        (["C:\\Tools\\git.exe", "status"], False),
        (["C:\\Tools\\git.exe", "status"], True),
    ]


def test_ensure_ssh_signing_configures_git(monkeypatch):
    calls = []
    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: calls.append((cmd, check)))

    create_release.ensure_ssh_signing()

    assert calls == [(["git", "config", "--global", "gpg.format", "ssh"], False)]


def test_create_and_push_signed_tag_pushes_new_tag(monkeypatch):
    calls = []
    monkeypatch.setattr(create_release, "run", lambda cmd, check=True: calls.append((cmd, check)) or SimpleNamespace(returncode=0))

    create_release.create_and_push_signed_tag("1.5.0")

    assert calls == [
        (["git", "tag", "-s", "v1.5.0", "-m", "v1.5.0"], False),
        (["git", "push", "origin", "v1.5.0"], True),
    ]


def test_create_and_push_signed_tag_pushes_existing_local_tag_missing_remote(monkeypatch):
    calls = []
    returncodes = iter([1, 0, 1, 0])
    monkeypatch.setattr(
        create_release,
        "run",
        lambda cmd, check=True: calls.append((cmd, check)) or SimpleNamespace(returncode=next(returncodes)),
    )

    create_release.create_and_push_signed_tag("1.5.0")

    assert calls[-1] == (["git", "push", "origin", "v1.5.0"], True)


def test_create_and_push_signed_tag_noops_when_remote_tag_exists(monkeypatch, capsys):
    returncodes = iter([1, 0, 0])
    monkeypatch.setattr(
        create_release,
        "run",
        lambda cmd, check=True: SimpleNamespace(returncode=next(returncodes)),
    )

    create_release.create_and_push_signed_tag("1.5.0")

    assert "already exists on origin" in capsys.readouterr().out


def test_create_and_push_signed_tag_exits_on_unknown_creation_failure(monkeypatch):
    returncodes = iter([1, 1])
    monkeypatch.setattr(
        create_release,
        "run",
        lambda cmd, check=True: SimpleNamespace(returncode=next(returncodes)),
    )

    with pytest.raises(SystemExit):
        create_release.create_and_push_signed_tag("1.5.0")


def test_parse_args_reads_release_options(monkeypatch):
    monkeypatch.setattr(
        create_release.sys,
        "argv",
        [
            "create_release.py",
            "1.5.0rc1",
            "--local",
            "--no-build",
            "--keep-screenshots",
            "--screenshot-pdf",
            "preview.pdf",
        ],
    )

    args = create_release._parse_args()

    assert args.version == "1.5.0rc1"
    assert args.local is True
    assert args.no_build is True
    assert args.keep_screenshots is True
    assert args.screenshots_only is False
    assert args.screenshot_pdf == "preview.pdf"


def test_parse_args_supports_screenshots_only_without_version(monkeypatch):
    monkeypatch.setattr(
        create_release.sys,
        "argv",
        ["create_release.py", "--screenshots-only", "--screenshot-pdf", "preview.pdf"],
    )

    args = create_release._parse_args()

    assert args.version is None
    assert args.screenshots_only is True
    assert args.screenshot_pdf == "preview.pdf"


@pytest.mark.parametrize(
    "argv",
    [
        ["create_release.py"],
        ["create_release.py", "--screenshots-only", "1.5.0"],
        ["create_release.py", "--screenshots-only", "--local"],
        ["create_release.py", "1.5.0", "--keep-screenshots"],
    ],
)
def test_parse_args_rejects_invalid_option_combinations(monkeypatch, argv):
    monkeypatch.setattr(create_release.sys, "argv", argv)

    with pytest.raises(SystemExit):
        create_release._parse_args()


def test_capture_release_screenshots_captures_light_and_dark_variants(monkeypatch):
    calls = []

    monkeypatch.setattr(
        create_release,
        "_capture_target_screenshot",
        lambda target, out_path, theme, **kwargs: calls.append((target, out_path, theme, kwargs)),
    )

    create_release._capture_release_screenshots(screenshot_pdf=None)

    assert calls == [
        ("main", create_release.SCREENSHOT_PATHS["main"]["light"], "light", {}),
        ("filter", create_release.SCREENSHOT_PATHS["filter"]["light"], "light", {}),
        ("watermark", create_release.SCREENSHOT_PATHS["watermark"]["light"], "light", {}),
        ("devtools", create_release.SCREENSHOT_PATHS["devtools"]["light"], "light", {}),
        ("main", create_release.SCREENSHOT_PATHS["main"]["dark"], "dark", {}),
        ("filter", create_release.SCREENSHOT_PATHS["filter"]["dark"], "dark", {}),
        ("watermark", create_release.SCREENSHOT_PATHS["watermark"]["dark"], "dark", {}),
        ("devtools", create_release.SCREENSHOT_PATHS["devtools"]["dark"], "dark", {}),
    ]


def test_capture_release_screenshots_includes_preview_when_pdf_exists(tmp_path, monkeypatch):
    calls = []
    preview_pdf = tmp_path / "preview.pdf"
    preview_pdf.write_text("pdf", encoding="utf-8")
    monkeypatch.setattr(
        create_release,
        "_capture_target_screenshot",
        lambda target, out_path, theme, **kwargs: calls.append((target, theme, kwargs)),
    )

    create_release._capture_release_screenshots(str(preview_pdf))

    assert ("preview", "light", {"pdf_for_preview": str(preview_pdf)}) in calls
    assert ("preview", "dark", {"pdf_for_preview": str(preview_pdf)}) in calls


def test_capture_release_screenshots_reports_missing_preview_pdf(monkeypatch, capsys):
    monkeypatch.setattr(create_release, "_capture_target_screenshot", lambda *args, **kwargs: None)

    create_release._capture_release_screenshots("missing.pdf")

    assert "screenshot PDF not found" in capsys.readouterr().out


def test_load_and_restore_file_snapshots_handles_missing_and_existing_files(tmp_path):
    existing = tmp_path / "existing.txt"
    missing = tmp_path / "missing.txt"
    existing.write_bytes(b"before")

    snapshots = create_release._load_file_snapshots((existing, missing))
    existing.write_bytes(b"after")
    missing.write_bytes(b"temporary")
    create_release._restore_file_snapshots(snapshots)

    assert existing.read_bytes() == b"before"
    assert not missing.exists()


def test_load_screenshot_file_snapshots_collects_configured_paths(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"png")
    monkeypatch.setattr(create_release, "SCREENSHOT_PATHS", {"main": {"light": screenshot}})

    assert create_release._load_screenshot_file_snapshots() == {screenshot: b"png"}


def test_collect_release_artifacts_returns_existing_files(tmp_path, monkeypatch):
    pyproject = tmp_path / "pyproject.toml"
    runtime = tmp_path / "_version.py"
    inno = tmp_path / "setup_version.iss"
    lock = tmp_path / "uv.lock"
    screenshot = tmp_path / "screenshot.png"
    for path in (pyproject, runtime, screenshot):
        path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(create_release, "PYPROJECT_TOML", pyproject)
    monkeypatch.setattr(create_release, "RUNTIME_VERSION_PY", runtime)
    monkeypatch.setattr(create_release, "INNO_VERSION_ISS", inno)
    monkeypatch.setattr(create_release, "UV_LOCK", lock)
    monkeypatch.setattr(create_release, "SCREENSHOT_PATHS", {"main": {"light": screenshot}})

    assert create_release._collect_release_artifacts() == [str(pyproject), str(runtime), str(screenshot)]


def test_release_flow_stages_and_commits_changes(monkeypatch):
    calls = []
    monkeypatch.setattr(create_release, "update_version", lambda version: calls.append(("version", version)))
    monkeypatch.setattr(create_release, "refresh_lockfile", lambda: calls.append(("lock",)))
    monkeypatch.setattr(create_release, "_capture_release_screenshots", lambda screenshot_pdf: calls.append(("screenshots", screenshot_pdf)))
    monkeypatch.setattr(create_release, "_collect_release_artifacts", lambda: ["pyproject.toml"])

    def fake_run(cmd, check=True):
        calls.append(("run", cmd, check))
        if cmd[:4] == ["git", "diff", "--cached", "--quiet"]:
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(create_release, "run", fake_run)
    monkeypatch.setattr(create_release, "ensure_ssh_signing", lambda: calls.append(("signing",)))
    monkeypatch.setattr(create_release, "create_and_push_signed_tag", lambda version: calls.append(("tag", version)))

    create_release._run_release_flow("1.5.0", screenshot_pdf="preview.pdf")

    assert ("run", ["git", "add", "pyproject.toml"], False) in calls
    assert ("run", ["git", "commit", "-m", "chore(release): v1.5.0"], True) in calls
    assert ("run", ["git", "push"], False) in calls


def test_main_routes_to_local_or_release_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(
        create_release,
        "_parse_args",
        lambda: SimpleNamespace(
            version=" 1.5.0 ",
            local=True,
            no_build=True,
            keep_screenshots=True,
            screenshots_only=False,
            screenshot_pdf=None,
        ),
    )
    monkeypatch.setattr(create_release, "check_version_input", lambda version: calls.append(("check", version)))
    monkeypatch.setattr(
        create_release,
        "_run_local_release_flow",
        lambda version, no_build, screenshot_pdf, keep_screenshots: calls.append(
            ("local", version, no_build, screenshot_pdf, keep_screenshots)
        ),
    )
    monkeypatch.setattr(
        create_release,
        "_run_release_flow",
        lambda version, screenshot_pdf: calls.append(("release", version, screenshot_pdf)),
    )

    create_release.main()
    monkeypatch.setattr(
        create_release,
        "_parse_args",
        lambda: SimpleNamespace(
            version="1.5.1",
            local=False,
            no_build=False,
            keep_screenshots=False,
            screenshots_only=False,
            screenshot_pdf="preview.pdf",
        ),
    )
    create_release.main()

    assert calls == [
        ("check", "1.5.0"),
        ("local", "1.5.0", True, None, True),
        ("check", "1.5.1"),
        ("release", "1.5.1", "preview.pdf"),
    ]


def test_main_routes_to_screenshots_only_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(
        create_release,
        "_parse_args",
        lambda: SimpleNamespace(
            version=None,
            local=False,
            no_build=False,
            keep_screenshots=False,
            screenshots_only=True,
            screenshot_pdf="preview.pdf",
        ),
    )
    monkeypatch.setattr(create_release, "check_version_input", lambda version: calls.append(("check", version)))
    monkeypatch.setattr(
        create_release,
        "_run_screenshots_only_flow",
        lambda screenshot_pdf: calls.append(("screenshots_only", screenshot_pdf)),
    )

    create_release.main()

    assert calls == [("screenshots_only", "preview.pdf")]

