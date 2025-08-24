"""
Cache management utilities
"""

import datetime
import json

from ..config.paths import Paths
from ..version import Version


def save_update_cache(fetched_at: datetime.datetime, latest_version: Version):
    """Save update check cache."""
    cache_data = {"fetched_at": fetched_at.isoformat(), "latest_version": str(latest_version)}
    cache_file = Paths().update_cache_file
    cache_file.touch()
    cache_file.write_text(json.dumps(cache_data, indent=4))


def load_update_cache():
    """Load update check cache."""
    cache_file = Paths().update_cache_file
    cache_file.touch()
    try:
        data = json.loads(cache_file.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            cache_file.unlink()
        except Exception:
            pass
        cache_file.touch()
        return None, None
    try:
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
    except Exception:
        return None, None
    latest_version = Version.from_str(data["latest_version"])
    return fetched_at, latest_version


def save_releases_cache(releases: list[dict], channel: str, fetched_at: datetime.datetime):
    """Save releases list cache."""
    cache_file = Paths().releases_cache_file
    payload = {"fetched_at": fetched_at.isoformat(), "channel": channel, "releases": releases}
    cache_file.touch()
    cache_file.write_text(json.dumps(payload, indent=4))


def load_releases_cache():
    """Load releases list cache."""
    cache_file = Paths().releases_cache_file
    cache_file.touch()
    try:
        data: dict = json.loads(cache_file.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError):
        try:
            cache_file.unlink()
        except Exception:
            pass
        cache_file.touch()
        return None, None, None

    try:
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
    except Exception:
        return None, None, None

    channel = data.get("channel")
    releases = data.get("releases", [])
    return fetched_at, channel, releases
