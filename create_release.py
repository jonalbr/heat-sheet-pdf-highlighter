"""
Release automation script for Heat Sheet PDF Highlighter.

This script updates version numbers in pyproject.toml, setup.py, setup.iss,
and src/constants.py, creates a signed git tag, and pushes it to the remote
repository.

Arguments:
    version (str): The new version string (e.g., 1.2.3 or 1.2.3-rc1).
    --local (flag): If specified, only updates version locally, runs build.bat,
                    and then reverts changes. Does not commit, tag, or push.
"""

import argparse
from contextlib import contextmanager
import subprocess
import sys
import re
import os
from pathlib import Path
import shutil

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

SETUP_PY = Path("setup.py")
SETUP_ISS = Path("setup.iss")
PYPROJECT_TOML = Path("pyproject.toml")
CONSTANTS_PY = Path("src/constants.py")
UV_LOCK = Path("uv.lock")


def check_version_input(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\d+)?", version):
        print("Invalid version format. Use x.y.z or x.y.z-rcN.")
        sys.exit(1)


def update_version(version: str) -> None:
    # Derive a numeric, 4-part file version for Inno (strip prerelease, append .0 if needed)
    base_match = re.match(r"(\d+\.\d+\.\d+)", version)
    if not base_match:
        print("Failed to parse base numeric version from input.")
        sys.exit(1)
    base_numeric = base_match.group(1)
    file_version = base_numeric if base_numeric.count(".") == 3 else f"{base_numeric}.0"
    # setup.py
    if SETUP_PY.exists():
        text = SETUP_PY.read_text(encoding="utf-8")
        text = re.sub(r"(version\s*=\s*['\"])([^'\"]+)(['\"])", rf"\g<1>{version}\g<3>", text)
        SETUP_PY.write_text(text, encoding="utf-8")
    # pyproject.toml
    if PYPROJECT_TOML.exists():
        text = PYPROJECT_TOML.read_text(encoding="utf-8")
        text = re.sub(r'(^version\s*=\s*")([^"]+)(")', rf"\g<1>{version}\g<3>", text, count=1, flags=re.MULTILINE)
        PYPROJECT_TOML.write_text(text, encoding="utf-8")
    # setup.iss
    if SETUP_ISS.exists():
        text = SETUP_ISS.read_text(encoding="utf-8")
        # Update display version
        text = re.sub(r'(#define\s+MyAppVersion\s*")([^"]+)(")', rf"\g<1>{version}\g<3>", text)
        # Ensure numeric file version define exists and is updated
        if re.search(r'#define\s+MyAppVersionNumeric\s*"[^"]*"', text):
            text = re.sub(r'(#define\s+MyAppVersionNumeric\s*")([^"]*)(")', rf"\g<1>{file_version}\g<3>", text)
        else:
            # Insert after MyAppVersion define
            text = re.sub(r'(#define\s+MyAppVersion\s*"[^"]*"\s*)', rf"\g<1>\n#define MyAppVersionNumeric \"{file_version}\"", text, count=1)
        SETUP_ISS.write_text(text, encoding="utf-8")
    # src/constants.py
    if CONSTANTS_PY.exists():
        text = CONSTANTS_PY.read_text(encoding="utf-8")
        text = re.sub(r"(VERSION_STR\s*=\s*['\"])([^'\"]+)(['\"])", rf"\g<1>{version}\g<3>", text)
        CONSTANTS_PY.write_text(text, encoding="utf-8")


def refresh_lockfile() -> None:
    """Refresh uv.lock after version-bearing project metadata changes."""
    if UV_LOCK.exists():
        run(["uv", "lock"])


def build_project() -> None:
    """Invoke the repo's build script on Windows."""
    bat = Path("build.bat")
    if not bat.exists():
        print("build.bat not found in repository root. Aborting local build.")
        sys.exit(1)
    # Use cmd /c to run .bat reliably
    run(["cmd", "/c", str(bat.resolve())])


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
        description="Create a release: update versions, tag, and push. Use --local to build with a temporary version change and then revert."
    )
    parser.add_argument("version", help="Version string, e.g. 1.2.3 or 1.2.3-rc1")
    parser.add_argument("--local", action="store_true", help="Only change version locally, run build, then revert changes. No commit/tag/push.")
    parser.add_argument("--no-build", action="store_true", help="Do not run the build; useful as a dry-run to test update+revert behavior")
    parser.add_argument("--screenshot-pdf", dest="screenshot_pdf", default=None, help="Optional PDF used to generate preview screenshot")
    return parser.parse_args()


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


def _load_version_file_snapshots() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in (PYPROJECT_TOML, SETUP_PY, SETUP_ISS, CONSTANTS_PY, UV_LOCK) if path.exists()}


@contextmanager
def _temporary_environment_value(key: str, value: str):
    had_existing_value = key in os.environ
    previous_value = os.environ[key] if had_existing_value else ""
    os.environ[key] = value
    try:
        yield
    finally:
        if had_existing_value:
            os.environ[key] = previous_value
        else:
            os.environ.pop(key, None)


def _run_local_release_flow(version: str, no_build: bool, screenshot_pdf: str | None) -> None:
    originals = _load_version_file_snapshots()
    try:
        update_version(version)
        with _temporary_environment_value("HSPH_SKIP_BUILD_PAUSE", "true"):
            if no_build:
                print("--no-build specified: skipping actual build (dry-run).")
            else:
                build_project()
            _capture_release_screenshots(screenshot_pdf)
    finally:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")
    print("Local flow complete and version changes reverted.")


def _collect_release_artifacts() -> list[str]:
    artifacts = (
        PYPROJECT_TOML,
        SETUP_PY,
        SETUP_ISS,
        CONSTANTS_PY,
        UV_LOCK,
        *(path for per_target in SCREENSHOT_PATHS.values() for path in per_target.values()),
    )
    return [str(path) for path in artifacts if path.exists()]


def _run_release_flow(version: str, screenshot_pdf: str | None) -> None:
    update_version(version)
    refresh_lockfile()
    _capture_release_screenshots(screenshot_pdf)

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

    version = args.version.strip()
    check_version_input(version)

    if args.local:
        _run_local_release_flow(version, args.no_build, args.screenshot_pdf)
        return

    _run_release_flow(version, args.screenshot_pdf)


if __name__ == "__main__":
    main()
