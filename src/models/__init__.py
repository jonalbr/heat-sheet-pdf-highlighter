"""
Enums and data models
"""

from enum import StrEnum
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class UpdateDecision:
    """One confirmed update action and its version reminder preference."""

    action: Literal["update_now", "later", "cancel"]
    skip_version: bool = False


class MarkingMode(StrEnum):
    """Choose which search matches receive the independently configured colors."""

    ALL_MATCHES = "all_matches"
    DISTINGUISH_NAMES = "distinguish_names"
    NAMED_MATCHES = "named_matches"
