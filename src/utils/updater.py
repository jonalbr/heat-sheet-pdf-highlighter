"""
Update checking and downloading functionality
"""

import datetime
import hashlib
import os
import re
import subprocess
import tempfile
import time
import threading
from typing import TYPE_CHECKING
import logging

import requests

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

from ..config.paths import Paths
from ..version import Version
from .cache import load_update_cache, save_update_cache, invalidate_releases_cache


class UpdateChecker:
    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app_instance = app_instance  # Reference to main app for settings and callbacks
        self.app_settings = self.app_instance.app_settings
        self.update_callback = self.app_instance.on_version_update
        self.gui_callbacks = self.app_instance.update_dialogs
        self.paths = Paths()
        # Cache the last located download/sha URLs so the GUI can trigger a direct install
        self.last_download_url: str | None = None
        self.last_sha_url: str | None = None
        self.last_version_tag: str | None = None
        self.last_update_context: tuple[str, int] | None = None
        # Concurrency guards
        self._download_lock = threading.Lock()
        self._active_download: bool = False
        self._active_check: bool = False
        self._check_lock = threading.RLock()
        self._channel = self.app_settings.settings.get("update_channel", "stable")
        self._channel_revision = 0
        self._checking_context: tuple[str, int] | None = None
        self._pending_channel_refresh = False

    def channel_changed(self) -> None:
        """Invalidate discovered assets and any outstanding old-channel UI work."""
        with self._check_lock:
            self._channel = self.app_settings.settings.get("update_channel", "stable")
            self._channel_revision += 1
            self.last_download_url = self.last_sha_url = self.last_version_tag = None
            self.last_update_context = None
            self._pending_channel_refresh = self._active_check

    def _update_context(self) -> tuple[str, int]:
        """Capture a channel revision, detecting changes made through settings."""
        with self._check_lock:
            if self._channel != self.app_settings.settings.get("update_channel", "stable"):
                self.channel_changed()
            return self._channel, self._channel_revision

    def is_current_context(self, context: tuple[str, int]) -> bool:
        """Check the channel and revision, including a switch away and back."""
        return context == self._update_context() and not getattr(self.app_instance, "_closing", False)

    def check_for_app_updates(self, current_version: Version, force_check: bool = False, quiet: bool = False):
        """
        Checks for application updates by comparing the current version with the latest version available.
        Args:
            current_version (Version): The current version of the application.
            force_check (bool): If True, forces an update check regardless of cache. Defaults to False.
            quiet (bool): Suppress any popups and only update labels/callbacks.
        Returns:
            Version: The latest version available.
        """
        with self._check_lock:
            context = self._update_context()
            if self._active_check:
                if self._checking_context is not None and context != self._checking_context:
                    self._pending_channel_refresh = True
                return self._stored_newest_version_or(current_version)
            self._active_check = True
            self._checking_context = context
        now = datetime.datetime.now()

        try:
            # Use TTL from app settings when available
            ttl_seconds = int(self.app_settings.settings.get("update_cache_ttl_seconds", 86400))
            cache_expiry = datetime.timedelta(seconds=ttl_seconds)

            cache_time, latest_version = (None, None) if force_check else load_update_cache(channel=context[0])
            cache_hit = cache_time is not None and now - cache_time < cache_expiry
            if force_check:
                # If a forced update is requested, invalidate the releases cache without unlinking
                invalidate_releases_cache()

            # Perform the update check
            if not cache_hit:
                latest_version = self._get_latest_version_from_github(
                    current_version=current_version, force_check=force_check, quiet=quiet, context=context,
                )
            with self._check_lock:
                publish = self.is_current_context(context)
                if publish:
                    if isinstance(latest_version, Version) and cache_hit:
                        self._record_latest_version(latest_version, context[0])
                    if isinstance(latest_version, Version) and not cache_hit:
                        save_update_cache(fetched_at=now, latest_version=latest_version, channel=context[0])
                else:
                    self._pending_channel_refresh = True
            # Tk dispatch may wait for the main thread; never hold our lock here.
            if publish:
                self.update_callback(latest_version, current_version, context)
        finally:
            # Release guard even if request failed/raised or cache hit occurred
            with self._check_lock:
                self._active_check = False
                self._checking_context = None
                refresh = self._pending_channel_refresh
                self._pending_channel_refresh = False
        if refresh and not getattr(self.app_instance, "_closing", False):
            return self.check_for_app_updates(current_version, force_check=True, quiet=True)
        return latest_version

    def _fetch_release_info(self, url: str):
        response = requests.get(url=url, timeout=30)
        response.raise_for_status()
        return response.json()

    def list_releases(self, channel: str = "stable") -> list[dict]:
        """Return a list of release dicts with tag, prerelease flag, and assets filtered by channel."""
        url = self.paths.GITHUB_RELEASES
        releases_info = self._fetch_release_info(url)
        items: list[dict] = []
        for rel in releases_info:
            rel: dict
            tag = rel.get("tag_name", "")
            if not isinstance(tag, str):
                logging.getLogger("updater").warning("Skipping release with invalid tag_name: %r", tag)
                continue
            try:
                Version.from_str(tag)
            except ValueError:
                logging.getLogger("updater").warning("Skipping release with invalid version tag: %r", tag)
                continue
            is_prerelease = bool(rel.get("prerelease"))
            if is_prerelease and not self._is_rc_tag(tag):
                continue
            if channel != "rc" and is_prerelease:
                continue
            exe_url, sha_url = self._select_release_assets(rel)
            items.append(
                {
                    "tag": tag,
                    "prerelease": bool(rel.get("prerelease")),
                    "exe_url": exe_url,
                    "sha_url": sha_url,
                    "body": rel.get("body", "_No release notes provided._"),
                }
            )
        return items

    @staticmethod
    def _is_rc_tag(tag: str) -> bool:
        # New releases use canonical PEP 440 ``rc`` notation. Keep accepting
        # the historical hyphenated form so older GitHub releases remain visible.
        return bool(re.fullmatch(r"v?\d+\.\d+\.\d+-?rc\d+", tag))

    def _select_release_assets(self, release: dict) -> tuple[str | None, str | None]:
        """Pick the .exe and .sha256 assets from a release, if present."""
        exe_url = None
        sha_url = None
        assets: list[dict] = release.get("assets", [])
        for asset in assets:
            name: str = asset.get("name", "")
            if name.lower().endswith(".exe"):
                exe_url = asset.get("browser_download_url")
            elif name.lower().endswith(".sha256"):
                sha_url = asset.get("browser_download_url")
        return exe_url, sha_url

    def _handle_rc_releases(self, latest_version: Version, download_url: str | None, sha_url: str | None):
        releases_info = self._fetch_release_info(self.paths.GITHUB_RELEASES)
        pre_releases = [
            release
            for release in releases_info
            if release.get("prerelease") and self._is_rc_tag(release.get("tag_name", ""))
        ]
        if pre_releases:
            latest_pre_release = pre_releases[0]
            latest_pre_release_version = Version.from_str(latest_pre_release["tag_name"])
            if latest_pre_release_version > latest_version:
                latest_version = latest_pre_release_version
                exe_url, sha_pre = self._select_release_assets(latest_pre_release)
                download_url = exe_url or download_url
                sha_url = sha_pre
        return latest_version, download_url, sha_url

    def _get_latest_version_from_github(
        self, current_version: Version, force_check: bool = False, quiet: bool = False, context=None,
    ):
        context = context if context is not None else self._update_context()
        release_url = self.paths.GITHUB_LATEST_RELEASE
        try:
            release_info = self._fetch_release_info(release_url)
            latest_version = Version.from_str(release_info["tag_name"])
            download_url, sha_url = self._select_release_assets(release_info)

            # Apply channel policy (stable vs rc)
            latest_version, download_url, sha_url = self._apply_channel_policy(
                latest_version, download_url, sha_url, channel=context[0],
            )
            if not self.is_current_context(context):
                return current_version

            # Validate required assets and possibly short-circuit
            short_circuit = self._validate_required_assets(
                latest_version=latest_version,
                current_version=current_version,
                download_url=download_url,
                sha_url=sha_url,
                force_check=force_check,
                quiet=quiet,
            )
            if short_circuit is not None:
                return short_circuit

            with self._check_lock:
                if not self.is_current_context(context):
                    return current_version
                self._record_latest_version(latest_version, context[0])
                self.last_download_url = download_url
                self.last_sha_url = sha_url
                self.last_version_tag = str(latest_version)
                self.last_update_context = context

            # If we're quiet or shouldn't prompt, just return the version
            if quiet or not self._should_prompt_user(latest_version, current_version, force_check, context[0]):
                return latest_version

            # Prompt and possibly kick off the update
            self._handle_user_prompt(latest_version, download_url, sha_url, context)
            return latest_version

        except requests.exceptions.RequestException as e:
            return self._handle_update_check_exception(e, current_version, force_check, quiet or not self.is_current_context(context))
        except (KeyError, TypeError, ValueError) as e:
            return self._handle_invalid_release_metadata(e, current_version, force_check, quiet or not self.is_current_context(context))

    def _apply_channel_policy(
        self,
        latest_version: Version,
        download_url: str | None,
        sha_url: str | None,
        channel: str | None = None,
    ) -> tuple[Version, str | None, str | None]:
        """Apply update channel policy (stable/rc) and return possibly updated values."""
        channel = channel if channel is not None else self.app_settings.settings.get("update_channel", "stable")
        if channel == "rc":
            return self._handle_rc_releases(latest_version, download_url, sha_url)
        return latest_version, download_url, sha_url

    def _validate_required_assets(
        self,
        latest_version: Version,
        current_version: Version,
        download_url: str | None,
        sha_url: str | None,
        force_check: bool,
        quiet: bool,
    ) -> Version | bool | None:
        """Ensure required assets exist according to settings; return a short-circuit value or None to continue."""
        verify_sha_globally = self.app_settings.settings.get("verify_sha", "True") == "True"
        # If SHA verification is required and missing for a newer release, treat as no update
        if verify_sha_globally and latest_version > current_version and not sha_url:
            if force_check and not quiet:
                self.gui_callbacks.show_up_to_date()
            return current_version

        # If there's no installer asset at all, we cannot update but still return latest for display
        if latest_version > current_version and not download_url:
            if force_check and not quiet:
                self.gui_callbacks.show_update_error_retry("Installer asset not found for the latest release.")
            return latest_version

        return None

    def _handle_update_check_exception(
        self,
        e: requests.exceptions.RequestException,
        current_version: Version,
        force_check: bool,
        quiet: bool,
    ) -> Version | bool:
        logging.getLogger("updater").exception("RequestException while checking updates: %s", e)
        if force_check and not quiet:
            if self.gui_callbacks.show_update_error_retry(str(e)):
                result = self.check_for_app_updates(current_version, force_check)
                return result if result is not None else False
            print(f"Failed to check for updates: {str(e)}")
        else:
            print(f"Failed to check for updates: {str(e)}")
        return False

    def _handle_invalid_release_metadata(
        self,
        e: Exception,
        current_version: Version,
        force_check: bool,
        quiet: bool,
    ) -> Version | bool:
        logging.getLogger("updater").exception("Invalid release metadata while checking updates: %s", e)
        if force_check and not quiet:
            if self.gui_callbacks.show_update_error_retry("Invalid release metadata received from GitHub."):
                result = self.check_for_app_updates(current_version, force_check)
                return result if result is not None else False
        return False

    def download_and_run_installer(self, download_url: str, sha_url: str | None = None):
        """
        Downloads the installer from the given URL and runs it.

        Args:
            download_url (str): The URL to download the installer from.
            sha_url (str | None): Optional URL to the .sha256 file for checksum verification. If None, no verification is performed.
        """
        if self._active_download or not self._download_lock.acquire(blocking=False):
            return
        self._active_download = True
        installer_path = None
        reserved = False
        try:
            # None is retained for older non-GUI callback implementations.
            reserved = self.gui_callbacks.start_download_ui() is not False
            if not reserved or self.gui_callbacks.is_download_cancelled():
                return
            with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as temp_file:
                installer_path = temp_file.name
            cancelled, total_size = self._download_with_progress(download_url, installer_path)
            if cancelled or self.gui_callbacks.is_download_cancelled():
                return
            if total_size and self.gui_callbacks.get_progress_value() != total_size:
                logging.getLogger("updater").warning("Downloaded size mismatch")
            if sha_url and not self._verify_sha256(installer_path, sha_url):
                return
            if self.gui_callbacks.is_download_cancelled():
                return
            self.gui_callbacks.close_application()
            self._spawn_installer(installer_path)
            installer_path = None  # The update batch now owns this file.
        except requests.exceptions.HTTPError as exc:
            self.gui_callbacks.show_download_error(str(exc))
        except Exception:
            logging.getLogger("updater").exception("Unexpected error downloading installer")
            raise
        finally:
            if installer_path is not None:
                self._safe_unlink(installer_path)
            try:
                if reserved:
                    self.gui_callbacks.finish_download_ui()
            finally:
                self._active_download = False
                self._download_lock.release()

    # --- helper methods to reduce complexity ---

    def _record_latest_version(self, latest_version: Version, channel: str) -> None:
        """Publish the selected channel's version and legacy reminder mirror."""
        self.app_settings.update_settings({
            "ask_for_update": "False" if self._is_skipped(latest_version, channel) else "True",
            "newest_version_available": str(latest_version),
        })

    def _is_skipped(self, version: Version, channel: str) -> bool:
        """Match only the explicitly skipped version in this channel."""
        return self.app_settings.settings.get("skipped_update_versions", {}).get(channel) == str(version)

    def _stored_newest_version_or(self, fallback: Version) -> Version:
        stored_value = self.app_settings.settings.get("newest_version_available", str(fallback))
        try:
            return Version.from_str(stored_value)
        except (TypeError, ValueError):
            logging.getLogger("updater").warning("Invalid stored newest version %r; using %s", stored_value, fallback)
            self.app_settings.update_setting("newest_version_available", "0.0.0")
            return fallback

    def _should_prompt_user(self, latest_version: Version, current_version: Version, force_check: bool, channel=None) -> bool:
        channel = channel if channel is not None else self._update_context()[0]
        should_prompt = latest_version > current_version and (force_check or not self._is_skipped(latest_version, channel))
        if not should_prompt and force_check:
            self.gui_callbacks.show_up_to_date()
        return should_prompt

    def _handle_user_prompt(self, latest_version: Version, download_url: str | None, sha_url: str | None, context=None) -> None:
        context = context if context is not None else self._update_context()
        if not self.is_current_context(context):
            return
        decision = self.gui_callbacks.show_update_available(latest_version, lambda: self.is_current_context(context))
        with self._check_lock:
            if not self.is_current_context(context) or decision.action == "cancel":
                return
            skipped = dict(self.app_settings.settings.get("skipped_update_versions", {}))
            if decision.skip_version:
                skipped[context[0]] = str(latest_version)
            elif skipped.get(context[0]) == str(latest_version):
                del skipped[context[0]]
            self.app_settings.update_settings({
                "skipped_update_versions": skipped,
                "ask_for_update": "False" if skipped.get(context[0]) == str(latest_version) else "True",
            })
        if decision.action == "update_now":
            # Verify SHA based on global setting (applies to any channel)
            verify_sha = self.app_settings.settings.get("verify_sha", "True") == "True"
            if not download_url:
                self.gui_callbacks.show_download_error("Installer asset not found in the selected release.")
                return
            # Run download in a background thread to avoid freezing Tk main loop
            threading.Thread(
                target=lambda: self.download_discovered_installer(download_url, sha_url if verify_sha else None, context),
                daemon=True,
            ).start()
            return

    def download_discovered_installer(self, download_url: str, sha_url: str | None, context) -> None:
        """Reject an install queued before the selected channel changed."""
        if context is not None and self.is_current_context(context):
            self.download_and_run_installer(download_url, sha_url)

    def _download_with_progress(self, url: str, dest_path: str) -> tuple[bool, int]:
        """Download a file streaming to dest_path with GUI progress. Returns (cancelled, total_size).

        Notes on performance:
        - Use a large chunk size to avoid excessive GUI updates from a background thread.
        - Throttle status updates to ~4 times per second.
        """
        # Conservative connect/read timeouts to avoid stalls while keeping stream active
        response = requests.get(url, stream=True, timeout=(10, 60))
        response.raise_for_status()

        total_size_in_bytes = int(response.headers.get("content-length", 0))
        # Adaptive chunk size: smaller files => smaller chunks for smoother initial progress
        if 0 < total_size_in_bytes < 4 * 1024 * 1024:
            block_size = 256 * 1024  # 256 KiB
        else:
            block_size = 1024 * 1024  # 1 MiB

        self.gui_callbacks.setup_download_progress(total_size_in_bytes)
        start_time = time.time()
        cancelled = False

        with open(dest_path, "wb") as file:
            last_update_time = time.time()
            status_published = False
            for data in response.iter_content(chunk_size=block_size):
                if self.gui_callbacks.is_download_cancelled():
                    cancelled = True
                    break
                file.write(data)
                self.gui_callbacks.update_download_progress(len(data))
                current_time = time.time()
                # Always show the first real progress update immediately, then throttle later updates.
                if not status_published or current_time - last_update_time >= 0.25:
                    self.gui_callbacks.update_download_status(start_time, total_size_in_bytes)
                    status_published = True
                    last_update_time = current_time
        return cancelled, total_size_in_bytes

    def _verify_sha256(self, installer_path: str, sha_url: str) -> bool:
        try:
            sha_resp = requests.get(sha_url, timeout=30)
            sha_resp.raise_for_status()
            m = re.search(r"\b[a-fA-F0-9]{64}\b", sha_resp.text)
            if not m:
                raise ValueError("Invalid .sha256 file format")
            expected_sha = m.group(0).lower()

            sha256 = hashlib.sha256()
            with open(installer_path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    sha256.update(chunk)
            actual_sha = sha256.hexdigest().lower()

            if actual_sha != expected_sha:
                self.gui_callbacks.show_download_error("Checksum mismatch. Downloaded file is corrupted.")
                return False
            return True
        except requests.exceptions.RequestException as e:
            logging.getLogger("updater").exception("Failed to fetch .sha256 file: %s", e)
            self.gui_callbacks.show_download_error(str(e))
            return False
        except OSError as e:
            logging.getLogger("updater").exception("Failed to read installer file for checksum: %s", e)
            self.gui_callbacks.show_download_error(str(e))
            return False
        except Exception as e:
            logging.getLogger("updater").exception("Unexpected error during SHA verification: %s", e)
            self.gui_callbacks.show_download_error(str(e))
            return False

    def _spawn_installer(self, installer_path: str) -> None:
        pid = os.getpid()

        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Run the update script without showing a window
        kwargs = {"startupinfo": startupinfo} if startupinfo is not None else {}
        subprocess.Popen([str(self.paths.update_script_path), str(pid), installer_path], **kwargs)

    @staticmethod
    def _safe_unlink(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass
