"""Build the frozen Windows app, installer, and checksum."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_VENV = Path(".venv")
ENV_FILE = Path(".env")
INNO_SCRIPT = Path("setup.iss")
INSTALLER = Path("heat_sheet_pdf_highlighter_installer.exe")
INSTALLER_SHA256 = Path(f"{INSTALLER}.sha256")


class BuildError(RuntimeError):
    """Raised when a required local build precondition is not met."""


def _print(message: str) -> None:
    """Print progress immediately so child-process output stays ordered."""
    print(message, flush=True)


def _resolve_exec(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    executable = cmd[0]
    if not any(separator in executable for separator in ("/", "\\")):
        resolved = shutil.which(executable)
        if resolved:
            return [resolved, *cmd[1:]]
    return cmd


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command, resolving executables from PATH when useful."""
    return subprocess.run(_resolve_exec(cmd), check=check)


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load simple ``KEY=VALUE`` lines from ``.env`` into ``os.environ``."""
    if not path.exists():
        _print(".env not found, continuing without loading extra environment vars.")
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def ensure_windows() -> None:
    """Fail early when the Windows-only installer build runs elsewhere."""
    if os.name != "nt":
        raise BuildError("The Windows installer build must be run on Windows.")


def ensure_uv_available() -> None:
    """Require uv as the only supported project workflow."""
    if shutil.which("uv") is None:
        raise BuildError("uv is required for this build. Install uv and run this script again.")


def ensure_project_venv() -> None:
    """Reject builds started from a different active virtual environment."""
    active_venv = os.environ.get("VIRTUAL_ENV")
    if not active_venv:
        return

    expected = PROJECT_VENV.resolve()
    active = Path(active_venv).resolve()
    if active != expected:
        raise BuildError(
            f"Another virtual environment is active: {active_venv}\n"
            "Deactivate it and use the uv-managed project .venv for this repository."
        )


def sync_dependencies() -> None:
    """Sync project dependencies, keeping CI locked."""
    _print("Using uv-managed project environment.")
    _print("Syncing dependencies with uv...")
    cmd = ["uv", "sync", "--all-groups"]
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        cmd.insert(2, "--locked")
    run(cmd)


def ensure_python_314() -> None:
    """Require the supported Python minor version for the frozen build."""
    _print("Using Python version:")
    _print(f"Python {sys.version.split()[0]}")
    if sys.version_info[:2] != (3, 14):
        raise BuildError("Python 3.14 is required for this build.")


def find_inno_compiler() -> Path:
    """Locate ``ISCC.exe`` from explicit config, PATH, or standard install roots."""
    explicit = os.environ.get("INNO_COMPILER")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.exists():
            return candidate
        raise BuildError(f"Configured INNO_COMPILER does not exist: {candidate}")

    path_hit = shutil.which("iscc.exe")
    if path_hit:
        return Path(path_hit)

    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Inno Setup 6" / "ISCC.exe"
        if candidate.exists():
            return candidate

    raise BuildError(
        "Inno Setup compiler not found. Install Inno Setup 6 or set INNO_COMPILER to the full ISCC.exe path."
    )


def build_application() -> None:
    """Build the frozen executable from ``pyproject.toml`` cx_Freeze config."""
    _print("Current directory:")
    _print(str(Path.cwd()))
    _print("Building application with cx_Freeze...")
    run(["uv", "run", "cxfreeze", "build"])


def compile_installer(compiler: Path) -> None:
    """Compile the Inno Setup installer."""
    _print("Compiling Inno Setup Script...")
    run([str(compiler), str(INNO_SCRIPT)])


def generate_installer_checksum(installer: Path = INSTALLER, output: Path = INSTALLER_SHA256) -> None:
    """Write the updater-facing SHA256 sidecar for the installer."""
    if not installer.exists():
        raise BuildError(f"Installer not found after build: {installer}")

    digest = hashlib.sha256()
    with installer.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)

    output.write_text(f"{digest.hexdigest()}  {installer.name}\n", encoding="ascii")
    _print(f"Created: {output.resolve()}")


def main() -> int:
    """Run the complete local/CI Windows installer build."""
    try:
        load_env_file()
        ensure_windows()
        ensure_uv_available()
        ensure_project_venv()
        sync_dependencies()
        ensure_python_314()
        compiler = find_inno_compiler()
        build_application()
        compile_installer(compiler)
        _print("Generating SHA256 checksum for installer...")
        generate_installer_checksum()
    except BuildError as exc:
        print(exc, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"Build command failed with exit code {exc.returncode}: {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        return exc.returncode or 1

    _print("Build and compilation successful!")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI use, not unit tests
    raise SystemExit(main())
