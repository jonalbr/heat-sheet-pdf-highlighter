"""Utility logging helpers for application startup.

Module to configure logging. Other modules can import the helpers for testing.
"""

from __future__ import annotations

import logging
from typing import Optional


def configure_basic_logging(level: int = logging.INFO, log_file: Optional[str] = None) -> None:
    """Configure a minimal logging setup if no handlers exist.

    Args:
        level: Logging level to apply (int).
        log_file: Optional path to a file to log to. If None, logs go to stderr.
    """
    root = logging.getLogger()
    if root.handlers:
        # Respect existing configuration
        return

    handlers = None
    if log_file:
        handlers = [logging.FileHandler(log_file, mode="a", encoding="utf-8")]

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def parse_log_level(level_str: Optional[str]) -> int:
    if not level_str:
        return logging.INFO
    try:
        return int(level_str)
    except (ValueError, TypeError):
        level_str = level_str.upper()
        return getattr(logging, level_str, logging.INFO)
