"""
Version handling
"""
import re
from dataclasses import dataclass


@dataclass
class Version:
    major: int = 0
    minor: int = 0
    patch: int = 0
    rc: int | None = None

    def __str__(self):
        if self.rc:
            return f"{self.major}.{self.minor}.{self.patch}-rc{self.rc}"
        else:
            return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def from_str(cls, version: str):
        """
        Create a Version object from a version string in the format 'X.Y.Z' or 'X.Y.Z-rcN'.

        Args:
            version (str): The version string.

        Returns:
            Version: The Version object.
        """
        # Extract the version numbers and the optional rc number
        parts = re.findall(r"\d+", version)

        # Convert the version numbers to integers
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2])

        # If there's a fourth part, it's the rc number
        rc = int(parts[3]) if len(parts) > 3 else None

        return cls(major, minor, patch, rc)

    def __lt__(self, other):
        self_rc = self.rc if self.rc is not None else -1
        other_rc = other.rc if other.rc is not None else -1
        return (self.major, self.minor, self.patch, self_rc) < (other.major, other.minor, other.patch, other_rc)

    def __gt__(self, other):
        self_rc = self.rc if self.rc is not None else -1
        other_rc = other.rc if other.rc is not None else -1
        return (self.major, self.minor, self.patch, self_rc) > (other.major, other.minor, other.patch, other_rc)
    
    def __le__(self, other):
        return self < other or self == other
    
    def __ge__(self, other):
        return self > other or self == other
    
    def __eq__(self, other):
        if not isinstance(other, Version):
            return False
        return (self.major, self.minor, self.patch, self.rc) == (other.major, other.minor, other.patch, other.rc)
