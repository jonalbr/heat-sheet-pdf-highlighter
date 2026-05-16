"""
Version handling
"""

import re
from dataclasses import dataclass

_VERSION_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-?(?P<label>rc|beta)(?P<number>\d+))?$"
)


@dataclass
class Version:
    major: int = 0
    minor: int = 0
    patch: int = 0
    rc: int | None = None

    def __str__(self):
        if self.rc is not None:
            return f"{self.major}.{self.minor}.{self.patch}rc{self.rc}"
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def from_str(cls, version: str):
        """
        Create a Version object from 'X.Y.Z' or canonical PEP 440 'X.Y.ZrcN'.

        A leading ``v`` is accepted for Git tags. Legacy ``-rcN`` and
        ``-betaN`` strings are accepted as ``rc``-equivalents for
        backwards-compatible reads, but newly-created releases use canonical
        ``rc`` notation.

        Args:
            version (str): The version string.

        Returns:
            Version: The Version object.
        """
        match = _VERSION_RE.fullmatch(version)
        if match is None:
            raise ValueError(f"Invalid version string: {version}")

        major = int(match.group("major"))
        minor = int(match.group("minor"))
        patch = int(match.group("patch"))
        rc = int(match.group("number")) if match.group("number") is not None else None

        return cls(major, minor, patch, rc)

    def _sort_key(self):
        # Release candidates come before the final release of the same base
        # version. Finals use a higher stage rank than RCs.
        stage_rank = 1 if self.rc is None else 0
        rc_number = self.rc if self.rc is not None else 0
        return (self.major, self.minor, self.patch, stage_rank, rc_number)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._sort_key() > other._sort_key()

    def __le__(self, other):
        return self < other or self == other

    def __ge__(self, other):
        return self > other or self == other

    def __eq__(self, other):
        if not isinstance(other, Version):
            return False
        return (self.major, self.minor, self.patch, self.rc) == (other.major, other.minor, other.patch, other.rc)
