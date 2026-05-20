"""
Path configuration and management
"""

import os
import sys
from pathlib import Path

from ..constants import APP_NAME


def _get_bundle_dir(
    *,
    frozen: bool | None = None,
    meipass: str | Path | None = None,
    executable: str | Path | None = None,
    module_file: str | Path = __file__,
) -> Path:
    """Return the asset root for source, PyInstaller, or cx_Freeze runtimes."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return Path(module_file).resolve().parent.parent.parent

    pyinstaller_root = meipass
    if pyinstaller_root is None and hasattr(sys, "_MEIPASS"):
        pyinstaller_root = getattr(sys, "_MEIPASS")
    if pyinstaller_root is not None:
        return Path(pyinstaller_root)

    frozen_executable = sys.executable if executable is None else executable
    return Path(frozen_executable).parent


class Paths:
    """
    Encapsulates all path logic and constants for the application.
    """

    @staticmethod
    def get_settings_path():
        """Determine the correct path for storing application settings based on the operating system."""
        match os.name:
            case "nt":  # Windows
                appdata_env = os.getenv("APPDATA")
                if appdata_env is None:
                    raise RuntimeError("APPDATA environment variable is not set.")
                appdata = Path(appdata_env)  # Roaming folder
                settings_dir = appdata / APP_NAME
            case "posix":
                if "darwin" in sys.platform:  # macOS
                    settings_dir = Path.home() / f"Library/Application Support/{APP_NAME}"
                else:  # Assuming Linux
                    settings_dir = Path.home() / f".config/{APP_NAME}"
            case _:
                raise Exception("Unsupported OS")

        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir

    # Handle source, PyInstaller, and cx_Freeze runtimes.
    bundle_dir = _get_bundle_dir()

    locales_dir = bundle_dir / "locales"
    tcl_lib_path = bundle_dir / "assets" / "tkBreeze"
    icon_path = bundle_dir / "assets" / "icon" / "app_icon.ico"
    logo_path = bundle_dir / "assets" / "icon" / "app_icon_transparent_cut.png"
    ocr_tessdata_dir = bundle_dir / "assets" / "ocr" / "tessdata"
    update_script_path = bundle_dir / "update_app.bat"

    # Centralized external URLs
    GITHUB_API_BASE = "https://api.github.com/repos/jonalbr/heat-sheet-pdf-highlighter"
    GITHUB_RELEASES = GITHUB_API_BASE + "/releases"
    GITHUB_LATEST_RELEASE = GITHUB_API_BASE + "/releases/latest"

    settings_path = get_settings_path()
    settings_file = settings_path / "settings.json"
    update_cache_file = settings_path / "update_check_cache.json"
    releases_cache_file = settings_path / "releases_cache.json"

    @staticmethod
    def is_valid_path(path: str):
        """
        Checks if the given path is a valid file path.

        Args:
            path (str): The path to be checked.

        Returns:
            str: The valid file path.

        Raises:
            ValueError: If the path is empty.
            FileNotFoundError: If the file is not found at the given path.
            ValueError: If the path is not a valid file path.
        """
        if not path:
            raise ValueError("Invalid Path")
        if not Path(path).exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not Path(path).is_file():
            raise ValueError(f"Invalid file path: {path}")
        return path
