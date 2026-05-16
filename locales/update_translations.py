"""Maintain gettext translation artifacts for the project."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_GETTEXT_BIN = Path(r"C:\msys64\usr\bin")
POT_FILE = SCRIPT_DIR / "base.pot"
DEFAULT_PO_FILE = "base.po"
SOURCE_FILES = (Path("..") / "src" / "gui" / "ui_strings.py",)


class TranslationToolError(RuntimeError):
    """Raised when a required gettext tool cannot be located."""


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run one translation-maintenance command."""
    return subprocess.run(cmd, cwd=cwd, check=True)


def _tool_candidates(tool_name: str) -> tuple[str, ...]:
    """Return executable names suitable for the host platform."""
    if os.name == "nt":
        return (f"{tool_name}.exe", tool_name)
    return (tool_name, f"{tool_name}.exe")


def find_gettext_tool(tool_name: str, *, env_var: str) -> Path:
    """Locate one gettext executable via env override, PATH, or MSYS2 default."""
    explicit = os.environ.get(env_var)
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.exists():
            return candidate
        raise TranslationToolError(f"Configured {env_var} does not exist: {candidate}")

    for candidate_name in _tool_candidates(tool_name):
        path_hit = shutil.which(candidate_name)
        if path_hit:
            return Path(path_hit)

    for candidate_name in _tool_candidates(tool_name):
        candidate = DEFAULT_GETTEXT_BIN / candidate_name
        if candidate.exists():
            return candidate

    raise TranslationToolError(
        f"Could not find {tool_name}. Install GNU gettext or set {env_var} to the executable path."
    )


def locale_directories(base_dir: Path = SCRIPT_DIR) -> list[Path]:
    """Return locale directories that contain an ``LC_MESSAGES`` folder."""
    return sorted(path for path in base_dir.iterdir() if path.is_dir() and (path / "LC_MESSAGES").is_dir())


def extract_strings(xgettext: Path, *, pot_file: Path = POT_FILE, source_files: tuple[Path, ...] = SOURCE_FILES) -> None:
    """Generate the POT template from the centralized UI string source."""
    run(
        [
            str(xgettext),
            "--from-code=UTF-8",
            "--language=Python",
            "--keyword=ngettext:1,2",
            "--keyword=n_:1,2",
            "--keyword=_:1",
            "--keyword=gettext:1",
            "--keyword=self._:1",
            "--keyword=self.n_:1,2",
            "-o",
            str(pot_file),
            *(str(path) for path in source_files),
        ],
        cwd=SCRIPT_DIR,
    )


def update_po_files(
    *,
    msginit: Path,
    msgmerge: Path,
    pot_file: Path = POT_FILE,
    locales_dir: Path = SCRIPT_DIR,
    po_file: str = DEFAULT_PO_FILE,
) -> None:
    """Create missing PO files and merge the latest POT template into each locale."""
    for locale_dir in locale_directories(locales_dir):
        po_path = locale_dir / "LC_MESSAGES" / po_file
        if not po_path.exists():
            run(
                [
                    str(msginit),
                    f"--locale={locale_dir.name}",
                    "-i",
                    str(pot_file),
                    "-o",
                    str(po_path),
                    "--no-translator",
                ]
            )
        run([str(msgmerge), "-U", str(po_path), str(pot_file)])


def review_translations(*, non_interactive: bool, po_file: str = DEFAULT_PO_FILE) -> None:
    """Run the German review / English autofill helper."""
    cmd = [sys.executable, str(SCRIPT_DIR / "po_update_and_review.py"), "--po-file", po_file]
    if non_interactive:
        cmd.append("--non-interactive")
    run(cmd)


def compile_mo_files(*, msgfmt: Path, locales_dir: Path = SCRIPT_DIR, po_file: str = DEFAULT_PO_FILE) -> None:
    """Compile every available PO file into its MO sibling."""
    for locale_dir in locale_directories(locales_dir):
        po_path = locale_dir / "LC_MESSAGES" / po_file
        if po_path.exists():
            run([str(msgfmt), "-o", str(po_path.with_suffix(".mo")), str(po_path)])


def remove_po_backups(locales_dir: Path = SCRIPT_DIR) -> None:
    """Delete gettext backup files left behind by ``msgmerge``."""
    for backup in locales_dir.rglob("*.po~"):
        backup.unlink()


def update_translations(*, non_interactive: bool, compile_only: bool, po_file: str = DEFAULT_PO_FILE) -> None:
    """Run the requested translation-maintenance workflow."""
    msgfmt = find_gettext_tool("msgfmt", env_var="MSGFMT_PATH")
    if not compile_only:
        xgettext = find_gettext_tool("xgettext", env_var="XGETTEXT_PATH")
        msgmerge = find_gettext_tool("msgmerge", env_var="MSGMERGE_PATH")
        msginit = find_gettext_tool("msginit", env_var="MSGINIT_PATH")
        extract_strings(xgettext)
        update_po_files(msginit=msginit, msgmerge=msgmerge, po_file=po_file)
        review_translations(non_interactive=non_interactive, po_file=po_file)
    compile_mo_files(msgfmt=msgfmt, po_file=po_file)
    remove_po_backups()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update gettext POT/PO/MO artifacts.")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Update translations without prompting for German text.",
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Only compile existing PO files to MO files.",
    )
    parser.add_argument(
        "--po-file",
        default=DEFAULT_PO_FILE,
        help=f"PO filename inside each locale LC_MESSAGES directory (default: {DEFAULT_PO_FILE}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv)
    try:
        update_translations(
            non_interactive=args.non_interactive,
            compile_only=args.compile_only,
            po_file=args.po_file,
        )
    except TranslationToolError as exc:
        print(exc, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"Translation command failed with exit code {exc.returncode}: {' '.join(map(str, exc.cmd))}",
            file=sys.stderr,
        )
        return exc.returncode or 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI use, not unit tests
    raise SystemExit(main())
