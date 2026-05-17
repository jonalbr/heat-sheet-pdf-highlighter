"""
Release automation script for Heat Sheet PDF Highlighter.

This script updates the canonical version in pyproject.toml, regenerates the
derived version artifacts, creates a signed git tag, and pushes it to the
remote repository.

Arguments:
    version (str): The new version string (e.g., 1.2.3 or 1.2.3rc1).
    --local (flag): If specified, only updates version locally, runs the
                    Windows build driver, and then reverts changes. Does not
                    commit, tag, or push.
    --keep-screenshots (flag): With --local, keep regenerated screenshots
                               while still reverting temporary version files.
    --screenshots-only (flag): Regenerate documentation screenshots without
                               changing version files, building, tagging, or
                               pushing.
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path
import shutil

from sync_version import INNO_VERSION_ISS, RUNTIME_VERSION_PY, validate_release_version, write_generated_files

SCREENSHOT_THEMES = ("light", "dark")
SCREENSHOT_PATHS = {
    "main": {
        "light": Path("images/app_screenshot_light.png"),
        "dark": Path("images/app_screenshot_dark.png"),
    },
    "filter": {
        "light": Path("images/app_screenshot_filter_light.png"),
        "dark": Path("images/app_screenshot_filter_dark.png"),
    },
    "watermark": {
        "light": Path("images/app_screenshot_watermark_light.png"),
        "dark": Path("images/app_screenshot_watermark_dark.png"),
    },
    "devtools": {
        "light": Path("images/app_screenshot_devtools_light.png"),
        "dark": Path("images/app_screenshot_devtools_dark.png"),
    },
    "preview": {
        "light": Path("images/app_screenshot_preview_light.png"),
        "dark": Path("images/app_screenshot_preview_dark.png"),
    },
}

PYPROJECT_TOML = Path("pyproject.toml")
UV_LOCK = Path("uv.lock")
BUILD_SCRIPT = Path("build_windows_installer.py")


def check_version_input(version: str) -> None:
    try:
        validate_release_version(version)
    except ValueError:
        print("Invalid version format. Use x.y.z or x.y.zrcN.")
        sys.exit(1)


def _should_capture_release_screenshots(version: str) -> bool:
    """Return whether automatic release screenshot capture should run."""
    return "rc" not in version


def update_version(version: str) -> None:
    """Update canonical project version and regenerate derived artifacts."""
    run(["uv", "version", "--frozen", version])
    write_generated_files()


def refresh_lockfile() -> None:
    """Refresh uv.lock after version-bearing project metadata changes."""
    if UV_LOCK.exists():
        run(["uv", "lock"])


def build_project() -> None:
    """Invoke the repo's Windows build driver."""
    if not BUILD_SCRIPT.exists():
        print(f"{BUILD_SCRIPT} not found in repository root. Aborting local build.")
        sys.exit(1)
    run([sys.executable, str(BUILD_SCRIPT.resolve())])


def _capture_target_screenshot(
    target: str,
    out_path: Path,
    theme: str,
    timeout_sec: int = 25,
    pdf_for_preview: str | None = None,
    delay: float = 0.6,
) -> None:
    """Capture a screenshot for a specific target window/dialog via main.py flags.

    target: one of 'main', 'filter', 'watermark', 'devtools', 'preview'
    out_path: where to save the PNG
    pdf_for_preview: optional PDF path required when target='preview'
    theme: screenshot theme to force ('light' or 'dark')
    delay: seconds to wait before capture for UI settle
    """
    python_exe = shutil.which("python") or shutil.which("py")
    if not python_exe:
        print("Python executable not found; skipping screenshot capture.")
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        python_exe,
        "main.py",
        "--use-default-settings",
        "--screenshot",
        str(out_path),
        "--screenshot-target",
        target,
        "--screenshot-theme",
        theme,
        "--screenshot-delay",
        str(delay),
    ]
    if target == "preview" and pdf_for_preview:
        cmd.extend(["--screenshot-pdf", pdf_for_preview])
    try:
        res = subprocess.run(cmd, timeout=timeout_sec)
        if res.returncode == 0 and out_path.exists():
            print(f"Captured {theme} {target} screenshot -> {out_path}")
        else:
            print(f"Skipping {theme} {target} screenshot (command returned {res.returncode}).")
    except Exception as e:
        print(f"Screenshot capture error for {theme} {target}: {e}")


def _resolve_exec(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    exe = cmd[0]
    # If first element has no path separators, resolve via PATH
    if not any(sep in exe for sep in ("/", "\\")):
        resolved = shutil.which(exe)
        if resolved:
            return [resolved, *cmd[1:]]
    return cmd


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(_resolve_exec(cmd), check=check)


def ensure_ssh_signing() -> None:
    # Configure git to sign tags with SSH if not already configured
    run(["git", "config", "--global", "gpg.format", "ssh"], check=False)
    # Do not force a specific key here; assume user configured SSH signing key already.


def create_and_push_signed_tag(version: str) -> None:
    tag = f"v{version}"

    # Helper functions
    def tag_exists_local(t: str) -> bool:
        return run(["git", "rev-parse", "-q", "--verify", f"{t}^{{tag}}"], check=False).returncode == 0

    def tag_exists_remote(t: str) -> bool:
        return run(["git", "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{t}"], check=False).returncode == 0

    # Try to create signed tag
    create = run(["git", "tag", "-s", tag, "-m", tag], check=False)
    if create.returncode != 0:
        # If it already exists locally, try to push if missing remotely; otherwise noop
        if tag_exists_local(tag):
            if not tag_exists_remote(tag):
                print(f"Tag {tag} exists locally but not on origin; pushing…")
                run(["git", "push", "origin", tag])
                return
            else:
                print(f"Tag {tag} already exists on origin; nothing to do.")
                return
        # Unknown error creating the tag
        print(f"Failed to create tag {tag}. Ensure your git is configured for signing and try again.")
        sys.exit(1)

    # Tag created successfully; push it
    run(["git", "push", "origin", tag])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a release: update versions, tag, and push. Use --local to build with a temporary "
            "version change and then revert, or --screenshots-only to refresh documentation images only."
        )
    )
    parser.add_argument("version", nargs="?", help="Version string, e.g. 1.2.3 or 1.2.3rc1")
    parser.add_argument("--local", action="store_true", help="Only change version locally, run build, then revert changes. No commit/tag/push.")
    parser.add_argument("--no-build", action="store_true", help="Do not run the build; useful as a dry-run to test update+revert behavior")
    parser.add_argument(
        "--keep-screenshots",
        action="store_true",
        help="With --local, keep regenerated screenshots while reverting temporary version files.",
    )
    parser.add_argument(
        "--screenshots-only",
        action="store_true",
        help="Only regenerate documentation screenshots. No version change, build, commit, tag, or push.",
    )
    parser.add_argument("--screenshot-pdf", dest="screenshot_pdf", default=None, help="Optional PDF used to generate preview screenshot")
    args = parser.parse_args()
    if args.screenshots_only:
        if args.version is not None:
            parser.error("--screenshots-only does not take a version")
        if args.local or args.no_build or args.keep_screenshots:
            parser.error("--screenshots-only cannot be combined with --local, --no-build, or --keep-screenshots")
    else:
        if args.version is None:
            parser.error("version is required unless --screenshots-only is used")
        if args.keep_screenshots and not args.local:
            parser.error("--keep-screenshots can only be used with --local")
    return args


def _capture_release_screenshots(screenshot_pdf: str | None) -> None:
    for theme in SCREENSHOT_THEMES:
        _capture_target_screenshot("main", SCREENSHOT_PATHS["main"][theme], theme)
        _capture_target_screenshot("filter", SCREENSHOT_PATHS["filter"][theme], theme)
        _capture_target_screenshot("watermark", SCREENSHOT_PATHS["watermark"][theme], theme)
        _capture_target_screenshot("devtools", SCREENSHOT_PATHS["devtools"][theme], theme)
        if screenshot_pdf and os.path.exists(screenshot_pdf):
            _capture_target_screenshot(
                "preview",
                SCREENSHOT_PATHS["preview"][theme],
                theme,
                pdf_for_preview=screenshot_pdf,
            )
    if not screenshot_pdf:
        print("Preview screenshots skipped; pass --screenshot-pdf PATH to include them.")
    elif not os.path.exists(screenshot_pdf):
        print(f"Preview screenshots skipped; screenshot PDF not found: {screenshot_pdf}")


def _load_file_snapshots(paths: tuple[Path, ...]) -> dict[Path, bytes | None]:
    return {
        path: path.read_bytes() if path.exists() else None
        for path in paths
    }


def _load_version_file_snapshots() -> dict[Path, bytes | None]:
    return _load_file_snapshots((PYPROJECT_TOML, RUNTIME_VERSION_PY, INNO_VERSION_ISS, UV_LOCK))


def _load_screenshot_file_snapshots() -> dict[Path, bytes | None]:
    screenshot_paths = tuple(path for per_target in SCREENSHOT_PATHS.values() for path in per_target.values())
    return _load_file_snapshots(screenshot_paths)


def _restore_file_snapshots(snapshots: dict[Path, bytes | None]) -> None:
    for path, content in snapshots.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)


def _run_local_release_flow(
    version: str,
    no_build: bool,
    screenshot_pdf: str | None,
    keep_screenshots: bool = False,
) -> None:
    version_originals = _load_version_file_snapshots()
    screenshot_originals = None if keep_screenshots else _load_screenshot_file_snapshots()
    try:
        update_version(version)
        refresh_lockfile()
        if no_build:
            print("--no-build specified: skipping build and screenshot capture (dry-run).")
        else:
            build_project()
            if _should_capture_release_screenshots(version):
                _capture_release_screenshots(screenshot_pdf)
            else:
                print("RC release detected: skipping automatic screenshot capture.")
    finally:
        _restore_file_snapshots(version_originals)
        if screenshot_originals is not None:
            _restore_file_snapshots(screenshot_originals)
    if keep_screenshots:
        print("Local flow complete; temporary version changes reverted and regenerated screenshots kept.")
    else:
        print("Local flow complete and temporary version/screenshot changes reverted.")


def _run_screenshots_only_flow(screenshot_pdf: str | None) -> None:
    _capture_release_screenshots(screenshot_pdf)
    print("Screenshot flow complete.")


def _collect_release_artifacts() -> list[str]:
    artifacts = (
        PYPROJECT_TOML,
        RUNTIME_VERSION_PY,
        INNO_VERSION_ISS,
        UV_LOCK,
        *(path for per_target in SCREENSHOT_PATHS.values() for path in per_target.values()),
    )
    return [str(path) for path in artifacts if path.exists()]


def _run_release_flow(version: str, screenshot_pdf: str | None) -> None:
    update_version(version)
    refresh_lockfile()
    if _should_capture_release_screenshots(version):
        _capture_release_screenshots(screenshot_pdf)
    else:
        print("RC release detected: skipping automatic screenshot capture.")

    to_stage = _collect_release_artifacts()
    if to_stage:
        run(["git", "add", *to_stage], check=False)

    commit = run(["git", "diff", "--cached", "--quiet"], check=False)
    if commit.returncode == 1:
        run(["git", "commit", "-m", f"chore(release): v{version}"])
        run(["git", "push"], check=False)

    ensure_ssh_signing()
    create_and_push_signed_tag(version)
    print(f"Created and pushed signed tag v{version}")


def main() -> None:
    args = _parse_args()

    if args.screenshots_only:
        _run_screenshots_only_flow(args.screenshot_pdf)
        return

    version = args.version.strip()
    check_version_input(version)

    if args.local:
        _run_local_release_flow(version, args.no_build, args.screenshot_pdf, args.keep_screenshots)
        return

    _run_release_flow(version, args.screenshot_pdf)


if __name__ == "__main__":  # pragma: no cover - exercised via CLI use, not unit tests
    main()
