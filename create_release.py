import subprocess
import sys
import re
from pathlib import Path
import shutil

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
    file_version = base_numeric if base_numeric.count('.') == 3 else f"{base_numeric}.0"
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
    if len(sys.argv) < 2:
        print("Usage: python create_release.py <version>")
        sys.exit(1)
    version = sys.argv[1].strip()
    check_version_input(version)

    update_version(version)

    # Optionally stage and commit version bumps if there are changes
    run(["git", "add", str(SETUP_PY), str(SETUP_ISS), str(CONSTANTS_PY)], check=False)
    # Commit only if there is any staged change
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
