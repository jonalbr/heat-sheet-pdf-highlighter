"""
Application settings management
"""
import json
from pathlib import Path
from typing import Dict
import os

from ..constants import VERSION_STR, LANGUAGE_OPTIONS


class AppSettings:
    def __init__(self, settings_file: Path):
        self.settings_file = settings_file
        # Ephemeral mode: don't read/write user settings, use defaults only
        self._ephemeral = os.getenv("HSPH_USE_DEFAULT_SETTINGS") in ("1", "true", "True")
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
            "update_channel": "stable",
            "verify_sha": "True",
            # Cache TTLs (seconds)
            "update_cache_ttl_seconds": 86400,  # 1 day
            "releases_cache_ttl_seconds": 600,  # 10 minutes
            "names": "",
            "watermark_enabled": "False",
            "watermark_text": "",
            "watermark_color": "#FFA500",
            "watermark_size": 16,
            "watermark_position": "top",
        }
        self.settings: Dict = self.load_settings()
        # Apply optional environment override for language (e.g., forced 'en' in screenshot mode)
        force_lang = os.getenv("HSPH_FORCE_LANGUAGE")
        if force_lang:
            self.settings["language"] = force_lang if force_lang in LANGUAGE_OPTIONS else "en"
        self.validate_settings()

    def load_settings(self) -> Dict:
        """Load settings from a JSON file. If the file doesn't exist, return default settings."""
        if not self._ephemeral and self.settings_file.exists():
            settings: Dict = json.loads(self.settings_file.read_text())
            return self.validate_settings(settings)
        else:
            # Return a copy so callers don't mutate the canonical defaults
            return dict(self.default_settings)

    def save_settings(self):
        """Save the current settings to a JSON file."""
        if self._ephemeral:
            # Skip writing when using ephemeral defaults
            return
        self.settings_file.write_text(json.dumps(self.settings, indent=4))

    def update_setting(self, key, value):
        """Update a specific setting and save the file."""
        self.settings[key] = value
        self.save_settings()

    def reset_to_defaults(self):
        """Reset settings to defaults and save."""
        self.settings = dict(self.default_settings)
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
            "update_channel": lambda v: v if v in ["stable", "rc"] else "stable",
            "verify_sha": lambda v: v if v in ["True", "False"] else "True",
            "update_cache_ttl_seconds": lambda v: int(v) if str(v).isdigit() and int(v) > 0 else 86400,
            "releases_cache_ttl_seconds": lambda v: int(v) if str(v).isdigit() and int(v) > 0 else 600,
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
        # Migrate legacy 'beta' flag if present
        if "update_channel" not in settings_dict and "beta" in settings_dict:
            beta_val = settings_dict.get("beta")
            if isinstance(beta_val, str):
                settings_dict["update_channel"] = "rc" if beta_val == "True" else "stable"
            elif isinstance(beta_val, bool):
                settings_dict["update_channel"] = "rc" if beta_val else "stable"
        validated = {}

        # Validate known keys first
        for key in self.default_settings:
            value = settings_dict.get(key, self.default_settings[key])
            validated[key] = self._validate_value(key, value)

    # Optionally keep extra keys? Currently we ignore them (drops legacy 'beta').
        if settings is not None:
            return validated

        self.settings = validated
        self.save_settings()
        return validated  # Ensure a Dict is always returned
