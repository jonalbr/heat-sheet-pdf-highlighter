"""
Enums and data models
"""

from enum import StrEnum


class MarkingMode(StrEnum):
    """Choose which search matches receive the independently configured colors."""

    ALL_MATCHES = "all_matches"
    DISTINGUISH_NAMES = "distinguish_names"
    NAMED_MATCHES = "named_matches"
