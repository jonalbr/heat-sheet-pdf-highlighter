"""
Cache management utilities
"""
import datetime
import json
from ..version import Version
from ..config.paths import Paths


def save_cache(cache_time: datetime.datetime, latest_version: Version):
    """Save update check cache."""
    cache_data = {"cache_time": cache_time.isoformat(), "latest_version": str(latest_version)}
    cache_file = Paths().cache_file
    cache_file.touch()
    cache_file.write_text(json.dumps(cache_data, indent=4))


def load_cache():
    """Load update check cache."""
    cache_file = Paths().cache_file
    cache_file.touch()
    try:
        data = json.loads(cache_file.read_text())
    except (UnicodeDecodeError, json.JSONDecodeError):
        cache_file.unlink()
        cache_file.touch()
        return None, None
    cache_time = datetime.datetime.fromisoformat(data["cache_time"])
    latest_version = Version.from_str(data["latest_version"])
    return cache_time, latest_version
