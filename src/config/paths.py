"""
Path configuration and management
"""
import os
import sys
from pathlib import Path

from ..constants import APP_NAME

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

    # Handle both single file and multi-file builds
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            # PyInstaller
            bundle_dir = Path(getattr(sys, '_MEIPASS'))
        else:
            # cx_Freeze
            bundle_dir = Path(sys.executable).parent
    else:
        # Development - go up from src/config to project root
        bundle_dir = Path(__file__).resolve().parent.parent.parent

    locales_dir = bundle_dir / "locales"
    tcl_lib_path = bundle_dir / "assets" / "ttk-Breeze-0.6"
    icon_path = bundle_dir / "assets" / "icon_no_background.ico"
    logo_path = bundle_dir / "assets" / "logo_no_background.png"
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