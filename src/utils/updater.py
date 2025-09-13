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
        now = datetime.datetime.now()

        # Use TTL from app settings when available
        ttl_seconds = int(self.app_settings.settings.get("update_cache_ttl_seconds", 86400))
        cache_expiry = datetime.timedelta(seconds=ttl_seconds)

        if not force_check:
            cache_time, latest_version = load_update_cache()
            if cache_time and now - cache_time < cache_expiry:
                self.update_callback(latest_version, current_version)
                return latest_version
        else:
            # If a forced update is requested, invalidate the releases cache without unlinking
            invalidate_releases_cache()

        # Perform the update check
        latest_version = self._get_latest_version_from_github(current_version=current_version, force_check=force_check, quiet=quiet)

        # Cache the result using JSON - only cache if we got a valid version
        if isinstance(latest_version, Version):
            save_update_cache(fetched_at=now, latest_version=latest_version)

        self.update_callback(latest_version, current_version)
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
            if channel != "rc" and rel.get("prerelease"):
                continue
            tag = rel.get("tag_name", "")
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

    def _handle_beta_releases(self, latest_version: Version, download_url: str | None, sha_url: str | None):
        releases_info = self._fetch_release_info(self.paths.GITHUB_RELEASES)
        pre_releases = [release for release in releases_info if release["prerelease"]]
        if pre_releases:
            latest_pre_release = pre_releases[0]
            latest_pre_release_version = Version.from_str(latest_pre_release["tag_name"])
            if latest_pre_release_version > latest_version:
                latest_version = latest_pre_release_version
                exe_url, sha_pre = self._select_release_assets(latest_pre_release)
                download_url = exe_url or download_url
                sha_url = sha_pre
                self.app_settings.update_setting("newest_version_available", str(latest_version))
                self.app_settings.update_setting("ask_for_update", "True")
        else:
            self.app_settings.update_setting("newest_version_available", str(latest_version))
            self.app_settings.update_setting("ask_for_update", "True")
        return latest_version, download_url, sha_url

    def _get_latest_version_from_github(self, current_version: Version, force_check: bool = False, quiet: bool = False):
        release_url = self.paths.GITHUB_LATEST_RELEASE
        try:
            release_info = self._fetch_release_info(release_url)
            latest_version = Version.from_str(release_info["tag_name"])
            download_url, sha_url = self._select_release_assets(release_info)

            # Apply channel policy (stable vs rc)
            latest_version, download_url, sha_url = self._apply_channel_policy(latest_version, download_url, sha_url)

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

            # Update settings when a newer version exists
            self._update_settings_if_newer(latest_version)

            # If we're quiet or shouldn't prompt, just return the version
            if quiet or not self._should_prompt_user(latest_version, current_version, force_check):
                return latest_version

            # Prompt and possibly kick off the update
            self._handle_user_prompt(latest_version, download_url, sha_url)
            return latest_version

        except requests.exceptions.RequestException as e:
            return self._handle_update_check_exception(e, current_version, force_check, quiet)

    def _apply_channel_policy(
        self,
        latest_version: Version,
        download_url: str | None,
        sha_url: str | None,
    ) -> tuple[Version, str | None, str | None]:
        """Apply update channel policy (stable/rc) and return possibly updated values."""
        channel = self.app_settings.settings.get("update_channel", "stable")
        if channel == "rc":
            return self._handle_beta_releases(latest_version, download_url, sha_url)
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

    def download_and_run_installer(self, download_url: str, sha_url: str | None = None):
        """
        Downloads the installer from the given URL and runs it.

        Args:
            download_url (str): The URL to download the installer from.
            sha_url (str | None): Optional URL to the .sha256 file for checksum verification. If None, no verification is performed.
        """
        # Start download UI state
        self.gui_callbacks.start_download_ui()

        # Create a temporary file for the installer
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as temp_file:
            installer_path = temp_file.name

        try:
            cancelled, total_size_in_bytes = self._download_with_progress(download_url, installer_path)
        except requests.exceptions.HTTPError as e:
            self.gui_callbacks.show_download_error(str(e))
            self._safe_unlink(installer_path)
            return
        except Exception as e:
            logging.getLogger("updater").exception("Unexpected error downloading installer: %s", e)
            self._safe_unlink(installer_path)
            raise

        if cancelled:
            self._safe_unlink(installer_path)
            return

        if total_size_in_bytes != 0:
            current_value = self.gui_callbacks.get_progress_value()
            if current_value != total_size_in_bytes:
                logging.getLogger("updater").warning("Downloaded size mismatch: progress %s vs expected %s", current_value, total_size_in_bytes)

        # Finish download UI state
        self.gui_callbacks.finish_download_ui()

        # Verify SHA256 if URL provided
        if sha_url and not self._verify_sha256(installer_path, sha_url):
            # Error already shown inside _verify_sha256
            self._safe_unlink(installer_path)
            return

        # Close the application and run installer
        self.gui_callbacks.close_application()
        self._spawn_installer(installer_path)

    # --- helper methods to reduce complexity ---

    def _update_settings_if_newer(self, latest_version: Version) -> None:
        if latest_version > Version.from_str(self.app_settings.settings["newest_version_available"]):
            self.app_settings.update_setting("ask_for_update", "True")
            self.app_settings.update_setting("newest_version_available", str(latest_version))

    def _should_prompt_user(self, latest_version: Version, current_version: Version, force_check: bool) -> bool:
        should_prompt = latest_version > current_version and (self.app_settings.settings["ask_for_update"] == "True" or force_check)
        if not should_prompt and force_check:
            self.gui_callbacks.show_up_to_date()
        return should_prompt

    def _handle_user_prompt(self, latest_version: Version, download_url: str | None, sha_url: str | None) -> None:
        update_choice = self.gui_callbacks.show_update_available(latest_version)
        if update_choice is None:
            return  # Cancel
        if update_choice:
            # Verify SHA based on global setting (applies to any channel)
            verify_sha = self.app_settings.settings.get("verify_sha", "True") == "True"
            if not download_url:
                self.gui_callbacks.show_download_error("Installer asset not found in the selected release.")
                return
            self.download_and_run_installer(download_url, sha_url if verify_sha else None)
            return
        # User clicked "No"
        if self.gui_callbacks.show_update_reminder_choice():
            self.app_settings.update_setting("ask_for_update", "False")

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
        # Use 1 MiB chunks to dramatically cut the number of GUI updates
        block_size = 1024 * 1024

        self.gui_callbacks.setup_download_progress(total_size_in_bytes)
        start_time = time.time()
        cancelled = False

        with open(dest_path, "wb") as file:
            last_update_time = time.time()
            for data in response.iter_content(chunk_size=block_size):
                if self.gui_callbacks.is_download_cancelled():
                    cancelled = True
                    break
                file.write(data)
                self.gui_callbacks.update_download_progress(len(data))
                current_time = time.time()
                if current_time - last_update_time >= 0.25:
                    self.gui_callbacks.update_download_status(start_time, total_size_in_bytes)
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

        # Create a STARTUPINFO object
        startupinfo = subprocess.STARTUPINFO()

        # Set the STARTF_USESHOWWINDOW flag
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Run the update script without showing a window
        subprocess.Popen([str(self.paths.update_script_path), str(pid), installer_path], startupinfo=startupinfo)

    @staticmethod
    def _safe_unlink(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass
