"""
Application settings management
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
import tempfile
from threading import RLock
from typing import Dict
import os
import re

from ..constants import (
    HIGHLIGHT_COLOR_DEFAULT, HIGHLIGHT_COLOR_NAMES, LANGUAGE_OPTIONS, VERSION_STR,
    WATERMARK_DEFAULT_X_RATIO, WATERMARK_DEFAULT_Y_RATIO,
)
from ..models import MarkingMode
from ..version import Version


def _is_valid_ratio(value) -> bool:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return 0.0 <= numeric_value <= 1.0


def validated_highlight_color(value, default: str) -> str:
    """Normalize a complete RGB hex color, falling back for malformed settings."""
    return value.upper() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default


def _validated_newest_version(value) -> str:
    if not isinstance(value, str):
        return "0.0.0"
    try:
        parsed_value = Version.from_str(value)
    except (TypeError, ValueError):
        return "0.0.0"
    return str(parsed_value) if parsed_value >= Version.from_str(VERSION_STR) else "0.0.0"


@dataclass(frozen=True)
class SettingsNotice:
    """A filesystem problem to explain on the GUI thread."""

    kind: str
    path: Path
    backup_path: Path | None = None
    detail: str = ""


class AppSettings:
    def __init__(self, settings_file: Path):
        self.settings_file = settings_file
        self._lock = RLock()
        self._write_blocked = False
        self._notices: list[SettingsNotice] = []
        self._reported: set[str] = set()
        # Ephemeral mode: don't read/write user settings, use defaults only
        self._ephemeral = os.getenv("HSPH_USE_DEFAULT_SETTINGS") in ("1", "true", "True")
        self.default_settings = {
            "version": VERSION_STR,
            "search_str": "SGS Hamburg",
            "mark_only_relevant_lines": 1,  # 0 or 1
            "marking_mode": MarkingMode.ALL_MATCHES.value,
            "highlight_color_default": HIGHLIGHT_COLOR_DEFAULT,
            "highlight_color_names": HIGHLIGHT_COLOR_NAMES,
            "theme_mode": "system",  # "system", "light", or "dark"
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
            "watermark_x_ratio": WATERMARK_DEFAULT_X_RATIO,
            "watermark_y_ratio": WATERMARK_DEFAULT_Y_RATIO,
            "ocr_enabled": "True",
            "ocr_language": "deu",
            "ocr_dpi": 300,
            "ocr_reduce_large_outputs": "True",
            "ocr_settings_version": 3,
        }
        self.settings: Dict = self.load_settings()
        # Apply optional environment override for language (e.g., forced 'en' in screenshot mode)
        force_lang = os.getenv("HSPH_FORCE_LANGUAGE")
        if force_lang:
            self.settings["language"] = force_lang if force_lang in LANGUAGE_OPTIONS else "en"
        # Apply optional environment override for theme before UI construction
        # (used by screenshot mode to avoid inheriting a dark system theme).
        force_theme = os.getenv("HSPH_FORCE_THEME")
        if force_theme:
            self.settings["theme_mode"] = force_theme if force_theme in {"system", "light", "dark"} else "light"
        self.validate_settings()

    def load_settings(self) -> Dict:
        """Recover invalid data only after preserving its exact original bytes."""
        with self._lock:
            if self._ephemeral:
                return dict(self.default_settings)
            try:
                original = self.settings_file.read_bytes()
            except FileNotFoundError:
                return dict(self.default_settings)
            except OSError as exc:
                self._write_blocked = True
                self._notice("read_failed", exc)
                return dict(self.default_settings)
            try:
                settings = json.loads(original.decode("utf-8"))
                if not isinstance(settings, dict):
                    raise ValueError("Settings JSON root must be an object")
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
                backup = None
                try:
                    with tempfile.NamedTemporaryFile(
                        "wb", dir=self.settings_file.parent,
                        prefix=f"{self.settings_file.name}.corrupt-", suffix=".bak", delete=False,
                    ) as stream:
                        backup = Path(stream.name)
                        stream.write(original)
                        stream.flush()
                        os.fsync(stream.fileno())
                except OSError as backup_error:
                    self._cleanup(backup)
                    self._write_blocked = True
                    self._notice("backup_failed", backup_error)
                else:
                    self._notice("recovered", exc, backup)
                return dict(self.default_settings)
            return self.validate_settings(settings)

    def _notice(self, kind, error, backup_path=None):
        logging.getLogger(__name__).warning("Settings %s (%s): %s", kind, self.settings_file, error)
        if kind not in self._reported:
            self._reported.add(kind)
            self._notices.append(SettingsNotice(kind, self.settings_file, backup_path, str(error)))

    @staticmethod
    def _cleanup(path):
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("Could not remove temporary settings file %s", path)

    def take_notices(self) -> list[SettingsNotice]:
        """Consume pending notices without calling Tk from a worker."""
        with self._lock:
            notices, self._notices = self._notices, []
            return notices

    def snapshot(self) -> Dict:
        """Return a consistent independent copy of the validated settings."""
        with self._lock:
            return dict(self.settings)

    def save_settings(self) -> bool:
        """Atomically persist the current state; retain session values on failure."""
        with self._lock:
            if self._ephemeral:
                return True
            if self._write_blocked:
                return False
            temporary = None
            try:
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.settings_file.parent,
                    prefix=f"{self.settings_file.name}.", suffix=".tmp", delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    json.dump(self.settings, stream, indent=4)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.settings_file)
            except OSError as exc:
                self._notice("write_failed", exc)
                return False
            finally:
                self._cleanup(temporary)
            self._reported.discard("write_failed")
            self._notices = [notice for notice in self._notices if notice.kind != "write_failed"]
            return True

    def update_settings(self, changes: Dict) -> bool:
        """Validate and publish related changes under one lock, then save once."""
        with self._lock:
            self.settings = self.validate_settings(self.settings | changes)
            return self.save_settings()

    def update_setting(self, key, value) -> bool:
        """Update a single setting through the same transaction as batch edits."""
        return self.update_settings({key: value})

    def reset_to_defaults(self) -> bool:
        """Reset the session even if its defaults cannot be persisted."""
        with self._lock:
            self.settings = dict(self.default_settings)
            return self.save_settings()

    def _validate_value(self, key: str, value):
        validators = {
            "version": lambda v: VERSION_STR,
            "search_str": lambda v: v if isinstance(v, str) else "",
            "mark_only_relevant_lines": lambda v: v if v in [0, 1] else 1,
            "marking_mode": lambda v: v if v in tuple(MarkingMode) else MarkingMode.ALL_MATCHES.value,
            "highlight_color_default": lambda v: validated_highlight_color(v, HIGHLIGHT_COLOR_DEFAULT),
            "highlight_color_names": lambda v: validated_highlight_color(v, HIGHLIGHT_COLOR_NAMES),
            "theme_mode": lambda v: v if v in ["system", "light", "dark"] else "system",
            "language": lambda v: v if v in LANGUAGE_OPTIONS else "en",
            "ask_for_update": lambda v: v if v in ["True", "False"] else "True",
            "update_available": lambda v: v if v in ["True", "False"] else "False",
            "newest_version_available": _validated_newest_version,
            "names": lambda v: v if isinstance(v, str) else "",
            "update_channel": lambda v: v if v in ["stable", "rc"] else "stable",
            "verify_sha": lambda v: v if v in ["True", "False"] else "True",
            "update_cache_ttl_seconds": lambda v: int(v) if str(v).isdigit() and int(v) > 0 else 86400,
            "releases_cache_ttl_seconds": lambda v: int(v) if str(v).isdigit() and int(v) > 0 else 600,
            "watermark_enabled": lambda v: v if v in ["True", "False"] else "False",
            "watermark_text": lambda v: v if isinstance(v, str) else "",
            "watermark_color": lambda v: v if isinstance(v, str) and v.startswith("#") else "#FFA500",
            "watermark_size": lambda v: int(v) if str(v).isdigit() and int(v) > 0 else 16,
            "watermark_position": lambda v: v if v in ["top", "bottom", "custom"] else "top",
            "watermark_x_ratio": lambda v: float(v) if _is_valid_ratio(v) else WATERMARK_DEFAULT_X_RATIO,
            "watermark_y_ratio": lambda v: float(v) if _is_valid_ratio(v) else WATERMARK_DEFAULT_Y_RATIO,
            "ocr_enabled": lambda v: v if v in ["True", "False"] else "True",
            "ocr_language": lambda v: v if v in ["deu", "eng", "deu+eng"] else "deu",
            "ocr_dpi": lambda v: int(v) if str(v).isdigit() and 72 <= int(v) <= 600 else 300,
            "ocr_reduce_large_outputs": lambda v: v if v in ["True", "False"] else "True",
            "ocr_settings_version": lambda v: 3,
        }
        try:
            return validators[key](value) if key in validators else None
        except (TypeError, ValueError, OverflowError):
            return self.default_settings[key]

    def validate_settings(self, settings: Dict | None = None):
        """Normalize a candidate, or validate and persist the current session."""
        with self._lock:
            validated = self._validated_settings(
                settings if settings is not None else getattr(self, "settings", {}),
            )
            if settings is None:
                self.settings = validated
                self.save_settings()
            return validated

    def _validated_settings(self, settings: Dict) -> Dict:
        settings_dict = dict(settings)
        # Migrate legacy 'beta' flag if present
        if "update_channel" not in settings_dict and "beta" in settings_dict:
            beta_val = settings_dict.get("beta")
            if isinstance(beta_val, str):
                settings_dict["update_channel"] = "rc" if beta_val == "True" else "stable"
            elif isinstance(beta_val, bool):
                settings_dict["update_channel"] = "rc" if beta_val else "stable"
        self._migrate_ocr_defaults(settings_dict)
        self._migrate_marking_settings(settings_dict)
        validated = {}

        # Validate known keys first
        for key in self.default_settings:
            value = settings_dict.get(key, self.default_settings[key])
            validated[key] = self._validate_value(key, value)

        return validated

    def _migrate_marking_settings(self, settings: Dict) -> None:
        """Preserve legacy filter output while separating selection and color."""
        if "marking_mode" in settings:
            return
        only_names = settings.get("highlight_mode") == "ONLY_NAMES"
        settings["marking_mode"] = (
            MarkingMode.NAMED_MATCHES.value if only_names else MarkingMode.DISTINGUISH_NAMES.value
        ) if settings.get("enable_filter") == 1 else MarkingMode.ALL_MATCHES.value
        if only_names:
            settings["highlight_color_names"] = validated_highlight_color(
                settings.get("highlight_color_names"), HIGHLIGHT_COLOR_DEFAULT,
            )

    def _migrate_ocr_defaults(self, settings_dict: Dict) -> None:
        if settings_dict.get("ocr_settings_version") == 3:
            return
        if settings_dict.get("ocr_language") == "deu+eng":
            settings_dict["ocr_language"] = "deu"
        settings_dict["ocr_reduce_large_outputs"] = "True"
        settings_dict["ocr_settings_version"] = 3
