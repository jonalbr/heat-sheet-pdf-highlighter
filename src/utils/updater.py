"""
Update checking and downloading functionality - Pure logic, no GUI dependencies
"""
import datetime
import os
import subprocess
import tempfile
import time
import hashlib
import re
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

from ..config.paths import Paths
from ..constants import CACHE_EXPIRY
from ..version import Version
from .cache import save_cache, load_cache


class UpdateChecker:
    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app_instance = app_instance  # Reference to main app for settings and callbacks
        self.app_settings = self.app_instance.app_settings
        self.update_callback = self.app_instance.on_version_update
        self.gui_callbacks = self.app_instance.update_dialogs
        self.paths = Paths()

    def check_for_app_updates(self, current_version: Version, force_check: bool = False):
        """
        Checks for application updates by comparing the current version with the latest version available.
        Args:
            current_version (Version): The current version of the application.
            force_check (bool): If True, forces an update check regardless of cache. Defaults to False.
        Returns:
            Version: The latest version available.
        """
        now = datetime.datetime.now()

        if not force_check:
            cache_time, latest_version = load_cache()
            if cache_time and now - cache_time < CACHE_EXPIRY:
                self.update_callback(latest_version, current_version)
                return latest_version

        # Perform the update check
        latest_version = self._get_latest_version_from_github(current_version=current_version, force_check=force_check)           
        
        # Cache the result using JSON - only cache if we got a valid version
        if isinstance(latest_version, Version):
            save_cache(now, latest_version)

        self.update_callback(latest_version, current_version)

        return latest_version

    def _fetch_release_info(self, url: str):
        response = requests.get(url)
        response.raise_for_status()
        return response.json()

    def _select_release_assets(self, release: dict) -> tuple[str | None, str | None]:
        """Pick the .exe and .sha256 assets from a release, if present."""
        exe_url = None
        sha_url = None
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if name.lower().endswith(".exe"):
                exe_url = asset.get("browser_download_url")
            elif name.lower().endswith(".sha256"):
                sha_url = asset.get("browser_download_url")
        return exe_url, sha_url

    def _handle_beta_releases(self, latest_version: Version, download_url: str | None, sha_url: str | None):
        beta_url = "https://api.github.com/repos/jonalbr/heat-sheet-pdf-highlighter/releases"
        releases_info = self._fetch_release_info(beta_url)
        pre_releases = [release for release in releases_info if release["prerelease"]]
        if pre_releases:
            latest_pre_release = pre_releases[0]
            latest_pre_release_version = Version.from_str(latest_pre_release["tag_name"])
            if latest_pre_release_version > latest_version:
                latest_version = latest_pre_release_version
                exe_url, sha_pre = self._select_release_assets(latest_pre_release)
                download_url = exe_url or download_url
                sha_url = sha_pre  # for beta we'll ignore sha requirement
                self.app_settings.update_setting("newest_version_available", str(latest_version))
                self.app_settings.update_setting("ask_for_update", "True")
        else:
            self.app_settings.update_setting("newest_version_available", str(latest_version))
            self.app_settings.update_setting("ask_for_update", "True")
        return latest_version, download_url, sha_url

    def _get_latest_version_from_github(self, current_version: Version, force_check: bool = False):
        release_url = "https://api.github.com/repos/jonalbr/heat-sheet-pdf-highlighter/releases/latest"
        try:
            release_info = self._fetch_release_info(release_url)
            latest_version = Version.from_str(release_info["tag_name"])
            download_url, sha_url = self._select_release_assets(release_info)

            if self.app_settings.settings["beta"] == "True":
                latest_version, download_url, sha_url = self._handle_beta_releases(latest_version, download_url, sha_url)

            # If we're on stable channel (beta disabled) require a SHA asset; otherwise treat as no update
            if self.app_settings.settings["beta"] != "True":
                if latest_version > current_version and not sha_url:
                    # No sha file found for a newer stable release -> treat as no update found
                    if force_check:
                        self.gui_callbacks.show_up_to_date()
                    return current_version

            # If there's no installer asset at all, we cannot update
            if latest_version > current_version and not download_url:
                if force_check:
                    self.gui_callbacks.show_update_error_retry("Installer asset not found for the latest release.")
                return latest_version

            # Use a guard clause to update settings when necessary (only after SHA check for stable)
            if latest_version > Version.from_str(self.app_settings.settings["newest_version_available"]):
                self.app_settings.update_setting("ask_for_update", "True")
                self.app_settings.update_setting("newest_version_available", str(latest_version))

            # Check if an update is needed
            if not (latest_version > current_version and (self.app_settings.settings["ask_for_update"] == "True" or force_check)):
                if force_check:
                    self.gui_callbacks.show_up_to_date()
                return latest_version

            # Prompt the user to install the update if needed
            update_choice = self.gui_callbacks.show_update_available(latest_version)

            if update_choice is None:
                # User clicked "Cancel" – ask again next time
                return latest_version
            elif update_choice:
                # User clicked "Yes" - download and install (on main thread like original)
                # Only verify SHA for stable channel; beta can proceed without sha
                verify_sha = (self.app_settings.settings["beta"] != "True")
                if not download_url:
                    self.gui_callbacks.show_download_error("Installer asset not found in the selected release.")
                    return latest_version
                self.download_and_run_installer(download_url, sha_url if verify_sha else None)
            else:
                # User clicked "No" - ask if they want to be reminded again
                choice = self.gui_callbacks.show_update_reminder_choice()
                if choice:
                    self.app_settings.update_setting("ask_for_update", "False")
                
            return latest_version

        except requests.exceptions.RequestException as e:
            if force_check:
                if self.gui_callbacks.show_update_error_retry(str(e)):
                    return self.check_for_app_updates(current_version, force_check)
                else:
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

        download_cancelled = False
        
        # Download the installer exe
        try:
            response = requests.get(download_url, stream=True)
            response.raise_for_status()

            total_size_in_bytes = int(response.headers.get("content-length", 0))
            block_size = 1024  # 1 KB

            self.gui_callbacks.setup_download_progress(total_size_in_bytes)
            
            start_time = time.time()

            with open(installer_path, "wb") as file:
                last_update_time = time.time()
                for data in response.iter_content(block_size):
                    # Check if download was cancelled
                    if self.gui_callbacks.is_download_cancelled():
                        download_cancelled = True
                        break
                    
                    file.write(data)
                    self.gui_callbacks.update_download_progress(len(data))
                    current_time = time.time()
                    if current_time - last_update_time >= 0.25:  # Update the GUI every 1/4 second
                        self.gui_callbacks.update_download_status(start_time, total_size_in_bytes)
                        last_update_time = current_time

            # File is now closed, safe to delete if cancelled
            if download_cancelled:
                try:
                    os.unlink(installer_path)  # Delete partial file
                except OSError:
                    pass
                return

            if total_size_in_bytes != 0:
                current_value = self.gui_callbacks.get_progress_value()
                if current_value != total_size_in_bytes:
                    print("ERROR, something went wrong")

        except requests.exceptions.HTTPError as e:
            self.gui_callbacks.show_download_error(str(e))
            # Clean up partial file on error
            try:
                os.unlink(installer_path)
            except OSError:
                pass
            return
        except Exception:
            # Clean up partial file on any other error
            try:
                os.unlink(installer_path)
            except OSError:
                pass
            raise

        # Finish download UI state
        self.gui_callbacks.finish_download_ui()

        # Verify SHA256 if URL provided (stable updates)
        if sha_url:
            try:
                sha_resp = requests.get(sha_url, timeout=30)
                sha_resp.raise_for_status()
                # Extract first 64-hex sequence from the file
                m = re.search(r"\b[a-fA-F0-9]{64}\b", sha_resp.text)
                if not m:
                    raise ValueError("Invalid .sha256 file format")
                expected_sha = m.group(0).lower()

                # Compute sha256 of downloaded installer
                sha256 = hashlib.sha256()
                with open(installer_path, "rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        sha256.update(chunk)
                actual_sha = sha256.hexdigest().lower()

                if actual_sha != expected_sha:
                    self.gui_callbacks.show_download_error("Checksum mismatch. Downloaded file is corrupted.")
                    try:
                        os.unlink(installer_path)
                    except OSError:
                        pass
                    return
            except Exception as e:
                self.gui_callbacks.show_download_error(str(e))
                try:
                    os.unlink(installer_path)
                except OSError:
                    pass
                return

        # Close the application
        self.gui_callbacks.close_application()

        # Get the current process id
        pid = os.getpid()

        # Create a STARTUPINFO object
        startupinfo = subprocess.STARTUPINFO()

        # Set the STARTF_USESHOWWINDOW flag
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Run the update script without showing a window
        subprocess.Popen([str(self.paths.update_script_path), str(pid), installer_path], startupinfo=startupinfo)
