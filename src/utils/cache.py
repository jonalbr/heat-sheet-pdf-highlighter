"""
Cache management utilities with atomic writes and safe invalidation.

Goals:
- Avoid unlinking cache files to reduce Windows file-lock errors (WinError 32).
- Use atomic replace with retry for writes to prevent partial reads.
"""

import datetime
import json
import logging
import os
import tempfile
import time
from pathlib import Path

from ..config.paths import Paths
from ..version import Version


def _write_json_atomic(target: Path, payload: dict, retries: int = 5, delay: float = 0.1) -> None:
    """Write JSON to target path atomically using a temp file and os.replace, with retries.

    This minimizes contention and avoids unlink while another reader may have the file open.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp: str | None = None
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, dir=target.parent, suffix=".tmp", encoding="utf-8") as tf:
                tmp = tf.name
                json.dump(payload, tf, indent=4)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp, target)
            return
        except Exception as e:
            last_err = e
            # Clean up temp if replace failed
            try:
                if tmp and os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass
            time.sleep(delay)
    # If still failing, log and raise last error to caller
    logging.getLogger("cache").exception("Atomic write failed for %s: %s", target, last_err)
    if last_err:
        raise last_err


def save_update_cache(fetched_at: datetime.datetime, latest_version: Version):
    """Save update check cache using atomic write."""
    cache_data = {"fetched_at": fetched_at.isoformat(), "latest_version": str(latest_version)}
    cache_file = Paths().update_cache_file
    _write_json_atomic(cache_file, cache_data)


def load_update_cache():
    """Load update check cache. Returns (fetched_at, latest_version) or (None, None)."""
    cache_file = Paths().update_cache_file
    if not cache_file.exists():
        return None, None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logging.getLogger("cache").exception("Invalid cache file, resetting: %s", e)
        # Reset to a well-formed empty state without unlinking
        try:
            _write_json_atomic(cache_file, {"fetched_at": "1970-01-01T00:00:00", "latest_version": "0.0.0"})
        except Exception as e:
            logging.getLogger("cache").exception("Failed to reset update cache: %s", e)
        return None, None
    try:
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
    except Exception as e:
        logging.getLogger("cache").exception("Invalid fetched_at in cache: %s", e)
        return None, None
    latest_version = Version.from_str(data["latest_version"])
    return fetched_at, latest_version


def save_releases_cache(releases: list[dict], channel: str, fetched_at: datetime.datetime):
    """Save releases list cache using atomic write."""
    cache_file = Paths().releases_cache_file
    payload = {"fetched_at": fetched_at.isoformat(), "channel": channel, "releases": releases}
    _write_json_atomic(cache_file, payload)


def load_releases_cache():
    """Load releases list cache. Returns (fetched_at, channel, releases) or (None, None, None)."""
    cache_file = Paths().releases_cache_file
    if not cache_file.exists():
        return None, None, None
    try:
        data: dict = json.loads(cache_file.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logging.getLogger("cache").exception("Invalid releases cache, resetting: %s", e)
        # Reset to a valid minimal state without unlinking
        try:
            _write_json_atomic(cache_file, {"fetched_at": "1970-01-01T00:00:00", "channel": "", "releases": []})
        except Exception as e:
            logging.getLogger("cache").exception("Failed to reset releases cache: %s", e)
        return None, None, None

    try:
        fetched_at = datetime.datetime.fromisoformat(data["fetched_at"])
    except Exception as e:
        logging.getLogger("cache").exception("Invalid fetched_at in releases cache: %s", e)
        return None, None, None

    channel = data.get("channel")
    releases = data.get("releases", [])
    return fetched_at, channel, releases


def invalidate_releases_cache() -> None:
    """Mark releases cache as expired without deleting the file.

    This avoids unlink on Windows and prompts reload by setting a very old fetched_at.
    """
    cache_file = Paths().releases_cache_file
    payload = {"fetched_at": "1970-01-01T00:00:00", "channel": "", "releases": []}
    try:
        _write_json_atomic(cache_file, payload)
    except Exception as e:
        logging.getLogger("cache").exception("Failed to invalidate releases cache: %s", e)
