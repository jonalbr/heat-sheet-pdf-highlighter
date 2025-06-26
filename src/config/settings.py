"""
Application settings management
"""
import json
from pathlib import Path
from typing import Dict

from ..constants import VERSION_STR, LANGUAGE_OPTIONS


class AppSettings:
    def __init__(self, settings_file: Path):
        self.settings_file = settings_file
        self.default_settings = {
            "version": VERSION_STR,
            "search_str": "SGS Hamburg",
            "mark_only_relevant_lines": 1,  # 0 or 1
            "enable_filter": 0,  # 0 or 1
            "highlight_mode": "NAMES_DIFF_COLOR",  # "ONLY_NAMES" or "NAMES_DIFF_COLOR"
            "language": "de",
            "ask_for_update": "True",
            "update_available": "False",
            "newest_version_available": "0.0.0",
            "beta": "False",
            "names": "",
            "watermark_enabled": "False",
            "watermark_text": "",
            "watermark_color": "#FFA500",
            "watermark_size": 16,
            "watermark_position": "top",
        }
        self.settings: Dict = self.load_settings()
        self.validate_settings()

    def load_settings(self) -> Dict:
        """Load settings from a JSON file. If the file doesn't exist, return default settings."""
        if self.settings_file.exists():
            settings: Dict = json.loads(self.settings_file.read_text())
            return self.validate_settings(settings)
        else:
            return self.default_settings

    def save_settings(self):
        """Save the current settings to a JSON file."""
        self.settings_file.write_text(json.dumps(self.settings, indent=4))

    def update_setting(self, key, value):
        """Update a specific setting and save the file."""
        self.settings[key] = value
        self.save_settings()

    def _validate_value(self, key: str, value):
        validators = {
            "version": lambda v: VERSION_STR,
            "search_str": lambda v: v if isinstance(v, str) else "",
            "mark_only_relevant_lines": lambda v: v if v in [0, 1] else 1,
            "enable_filter": lambda v: v if v in [0, 1] else 0,
            "highlight_mode": lambda v: v if v in ["ONLY_NAMES", "NAMES_DIFF_COLOR"] else "NAMES_DIFF_COLOR",
            "language": lambda v: v if v in LANGUAGE_OPTIONS else "en",
            "ask_for_update": lambda v: v if v in ["True", "False"] else "True",
            "update_available": lambda v: v if v in ["True", "False"] else "False",
            "newest_version_available": lambda v: v if isinstance(v, str) and v >= VERSION_STR else "0.0.0",
            "names": lambda v: v if isinstance(v, str) else "",
            "beta": lambda v: v if v in ["True", "False"] else "False",
            "watermark_enabled": lambda v: v if v in ["True", "False"] else "False",
            "watermark_text": lambda v: v if isinstance(v, str) else "",
            "watermark_color": lambda v: v if isinstance(v, str) and v.startswith("#") else "#FFA500",
            "watermark_size": lambda v: int(v) if str(v).isdigit() and int(v) > 0 else 16,
            "watermark_position": lambda v: v if v in ["top", "bottom"] else "top",
        }
        return validators[key](value) if key in validators else None

    def validate_settings(self, settings: Dict | None = None):
        # Get provided settings or use saved settings
        settings_dict = settings or getattr(self, "settings", {}) or {}
        validated = {}

        # Validate known keys first
        for key in self.default_settings:
            value = settings_dict.get(key, self.default_settings[key])
            validated[key] = self._validate_value(key, value)

        # Optionally keep extra keys? Currently we ignore them.
        if settings is not None:
            return validated

        self.settings = validated
        self.save_settings()
        return validated  # Ensure a Dict is always returned
