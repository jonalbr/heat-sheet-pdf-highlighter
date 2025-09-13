"""Pytest session configuration enforcing use of the local .venv.

By default tests will abort early if they are not executed using the
project's virtual environment interpreter. This helps avoid subtle
mismatches in installed packages (e.g. missing pytest-cov) and ensures
reproducible results.

Override / disable by setting environment variable:
    HSPDFH_ALLOW_GLOBAL=1

Rationale: Developers occasionally run `py -m pytest` with a global
interpreter which lacks dev dependencies; failing fast produces a
clear actionable message.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
import warnings

# Ensure the project `src` directory is on sys.path for `import src.*` style imports.
# This avoids needing editable installs during local test runs.
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _is_expected_venv() -> bool:
    """Heuristic check that current interpreter resides in project .venv.

    We look for a `.venv` directory at project root and ensure sys.executable
    path contains that directory. This is lightweight and avoids importing
    packaging internals.
    """
    project_root = Path(__file__).resolve().parents[1]
    venv_dir = project_root / ".venv"
    if not venv_dir.exists():  # No local venv => nothing to enforce
        return True
    exe_path = Path(sys.executable).resolve()
    return str(venv_dir.resolve()) in str(exe_path)


def pytest_sessionstart(session: pytest.Session):  # type: ignore[override]
    if os.environ.get("HSPDFH_ALLOW_GLOBAL") == "1":
        return
    if not _is_expected_venv():
        warnings.warn(
            "Tests must be run inside the project .venv (or set HSPDFH_ALLOW_GLOBAL=1 to override). "
            f"Current interpreter: {sys.executable}",
            stacklevel=0,
        )
        raise SystemExit("Aborting: not using project .venv and no override set")
