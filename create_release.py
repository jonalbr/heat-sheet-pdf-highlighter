"""
Release automation script for Heat Sheet PDF Highlighter.

This script updates version numbers in setup.py, setup.iss, and src/constants.py,
creates a signed git tag, and pushes it to the remote repository.

Arguments:
    version (str): The new version string (e.g., 1.2.3 or 1.2.3-rc1).
    --local (flag): If specified, only updates version locally, runs build.bat,
                    and then reverts changes. Does not commit, tag, or push.
"""

import argparse
import subprocess
import sys
import re
import os
from pathlib import Path
import shutil

SCREENSHOT_PATH = Path("images/app_screenshot.png")
SCREENSHOT_FILTER = Path("images/app_screenshot_filter.png")
SCREENSHOT_WATERMARK = Path("images/app_screenshot_watermark.png")
SCREENSHOT_DEVTOOLS = Path("images/app_screenshot_devtools.png")
SCREENSHOT_PREVIEW = Path("images/app_screenshot_preview.png")

SETUP_PY = Path("setup.py")
SETUP_ISS = Path("setup.iss")
CONSTANTS_PY = Path("src/constants.py")


def check_version_input(version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-(?:rc|beta)\d+)?", version):
        print("Invalid version format. Use x.y.z or x.y.z-rcN/-betaN.")
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


def build_project() -> None:
    """Invoke the repo's build script on Windows."""
    bat = Path("build.bat")
    if not bat.exists():
        print("build.bat not found in repository root. Aborting local build.")
        sys.exit(1)
    # Use cmd /c to run .bat reliably
    run(["cmd", "/c", str(bat.resolve())])


def _try_capture_screenshot(timeout_sec: int = 20) -> None:
    """Invoke main.py in screenshot mode to produce a PNG of the main window."""
    python_exe = shutil.which("python") or shutil.which("py")
    if not python_exe:
        print("Python executable not found; skipping screenshot capture.")
        return
    SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    # Let main.py control off-screen and default settings
    cmd = [python_exe, "main.py", "--use-default-settings", "--screenshot", str(SCREENSHOT_PATH)]
    try:
        res = subprocess.run(cmd, env=env, timeout=timeout_sec)
        if res.returncode == 0 and SCREENSHOT_PATH.exists():
            print(f"Updated screenshot at {SCREENSHOT_PATH}")
        else:
            print("Screenshot capture failed or was skipped.")
    except Exception as e:
        print(f"Screenshot capture error: {e}")


def _capture_target_screenshot(target: str, out_path: Path, timeout_sec: int = 25, pdf_for_preview: str | None = None, delay: float = 0.2) -> None:
    """Capture a screenshot for a specific target window/dialog via main.py flags.

    target: one of 'main', 'filter', 'watermark', 'devtools', 'preview'
    out_path: where to save the PNG
    pdf_for_preview: optional PDF path required when target='preview'
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
        "--screenshot-delay",
        str(delay),
    ]
    if target == "preview" and pdf_for_preview:
        cmd.extend(["--screenshot-pdf", pdf_for_preview])
    try:
        res = subprocess.run(cmd, timeout=timeout_sec)
        if res.returncode == 0 and out_path.exists():
            print(f"Captured {target} screenshot -> {out_path}")
        else:
            print(f"Skipping {target} screenshot (command returned {res.returncode}).")
    except Exception as e:
        print(f"Screenshot capture error for {target}: {e}")


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a release: update versions, tag, and push. Use --local to build with a temporary version change and then revert."
    )
    parser.add_argument("version", help="Version string, e.g. 1.2.3 or 1.2.3-rc1")
    parser.add_argument("--local", action="store_true", help="Only change version locally, run build, then revert changes. No commit/tag/push.")
    parser.add_argument("--no-build", action="store_true", help="Do not run the build; useful as a dry-run to test update+revert behavior")
    parser.add_argument("--screenshot-pdf", dest="screenshot_pdf", default=None, help="Optional PDF used to generate preview screenshot")
    args = parser.parse_args()

    version = args.version.strip()
    check_version_input(version)

    if args.local:
        # Save originals to restore after build/dry-run
        originals: dict[Path, str] = {}
        for p in (SETUP_PY, SETUP_ISS, CONSTANTS_PY):
            if p.exists():
                originals[p] = p.read_text(encoding="utf-8")

        try:
            update_version(version)
            # Simulate CI environment so build scripts that check for GITHUB_ACTIONS skip interactive pauses
            old_env_val = None
            env_key = "GITHUB_ACTIONS"
            if env_key in os.environ:
                old_env_val = os.environ[env_key]
            os.environ[env_key] = "true"
            try:
                if args.no_build:
                    print("--no-build specified: skipping actual build (dry-run).")
                else:
                    build_project()
                # Exercise screenshot capture path locally; don't fail build if it skips
                _try_capture_screenshot()
                # Also capture additional dialogs for docs
                _capture_target_screenshot("filter", SCREENSHOT_FILTER)
                _capture_target_screenshot("watermark", SCREENSHOT_WATERMARK)
                _capture_target_screenshot("devtools", SCREENSHOT_DEVTOOLS)
                if args.screenshot_pdf and os.path.exists(args.screenshot_pdf):
                    _capture_target_screenshot("preview", SCREENSHOT_PREVIEW, pdf_for_preview=args.screenshot_pdf)
            finally:
                # restore environment
                if old_env_val is None:
                    del os.environ[env_key]
                else:
                    os.environ[env_key] = old_env_val
        finally:
            # Revert files to their original state
            for p, content in originals.items():
                p.write_text(content, encoding="utf-8")
        print("Local flow complete and version changes reverted.")
        return

    # Non-local: proceed with normal release flow
    update_version(version)

    # Capture screenshots (with updated version visible) BEFORE staging so they are part of the same commit.
    _try_capture_screenshot()
    _capture_target_screenshot("filter", SCREENSHOT_FILTER)
    _capture_target_screenshot("watermark", SCREENSHOT_WATERMARK)
    _capture_target_screenshot("devtools", SCREENSHOT_DEVTOOLS)
    # Optionally produce preview screenshot if a PDF was supplied (mirrors local behavior)
    if args.screenshot_pdf and os.path.exists(args.screenshot_pdf):
        _capture_target_screenshot("preview", SCREENSHOT_PREVIEW, pdf_for_preview=args.screenshot_pdf)

    # Collect files to stage (only those that exist)
    to_stage: list[str] = []
    for p in (SETUP_PY, SETUP_ISS, CONSTANTS_PY,
              SCREENSHOT_PATH, SCREENSHOT_FILTER, SCREENSHOT_WATERMARK, SCREENSHOT_DEVTOOLS, SCREENSHOT_PREVIEW):
        if p.exists():
            to_stage.append(str(p))

    if to_stage:
        run(["git", "add", *to_stage], check=False)

    # Commit only if there is any staged change (version bumps and/or screenshots updated)
    commit = run(["git", "diff", "--cached", "--quiet"], check=False)  # returncode 0 if no diff
    if commit.returncode == 1:
        run(["git", "commit", "-m", f"chore(release): v{version}"])
        # Pushing the commit might be blocked by branch policies; user can open PR.
        run(["git", "push"], check=False)

    ensure_ssh_signing()
    create_and_push_signed_tag(version)
    print(f"Created and pushed signed tag v{version}")


if __name__ == "__main__":
    main()
