#!/usr/bin/env python3
"""
Heat Sheet PDF Highlighter - Main Entry Point

CLI and environment options (logging):

- Environment variables:
    - LOG_LEVEL: logging level name (DEBUG, INFO, etc.) or numeric value.
    - LOG_FILE: path to a file to append logs to (optional). If not set, logs go to stderr.

- CLI flags (override env):
    - --log-level LEVEL
    - --log-file PATH

The entrypoint applies a minimal logging configuration before starting the app so
modules using the logging subsystem have a handler available.
"""

import argparse
import os
import sys

from src.app import main
from src.utils.logging import configure_basic_logging, parse_log_level


if __name__ == "__main__":
    # Pass CLI args (except the program name)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level name or numeric value")
    parser.add_argument("--log-file", dest="log_file", default=None, help="Path to log file (optional)")
    parser.add_argument(
        "--use-default-settings",
        dest="use_defaults",
        action="store_true",
        help="Launch app without reading/writing user settings (use built-in defaults)",
    )
    parser.add_argument(
        "--screenshot",
        dest="screenshot_path",
        default=None,
        help="If set, write a PNG screenshot of the main window to this path and exit",
    )
    parser.add_argument(
        "--screenshot-target",
        dest="screenshot_target",
        choices=["main", "filter", "watermark", "devtools", "preview"],
        default=None,
        help="Target window to capture when using --screenshot",
    )
    parser.add_argument(
        "--screenshot-pdf",
        dest="screenshot_pdf",
        default=None,
        help="Optional PDF file path to enable watermark preview screenshots",
    )
    parser.add_argument(
        "--screenshot-delay",
        dest="screenshot_delay",
        type=float,
        default=None,
        help="Optional delay in seconds before capture to allow UI to stabilize",
    )
    args, _ = parser.parse_known_args(sys.argv[1:])

    env_level = os.getenv("LOG_LEVEL")
    env_file = os.getenv("LOG_FILE")

    level_value = args.log_level if args.log_level is not None else env_level
    file_value = args.log_file if args.log_file is not None else env_file

    level = parse_log_level(level_value)
    log_file = file_value or None

    configure_basic_logging(level=level, log_file=log_file)

    # Optional: run with default/ephemeral settings to get a consistent UI state
    if args.use_defaults or args.screenshot_path:
        os.environ["HSPH_USE_DEFAULT_SETTINGS"] = "1"
    if args.screenshot_path:
        os.environ["HSPH_SCREENSHOT_MODE"] = "1"
        os.environ["HSPH_SCREENSHOT_PATH"] = os.path.abspath(args.screenshot_path)
        # Force English in screenshots for consistency
        os.environ["HSPH_FORCE_LANGUAGE"] = "en"
        if args.screenshot_target:
            os.environ["HSPH_SCREENSHOT_TARGET"] = args.screenshot_target
        if args.screenshot_pdf:
            os.environ["HSPH_SCREENSHOT_PDF"] = os.path.abspath(args.screenshot_pdf)
        if args.screenshot_delay is not None:
            os.environ["HSPH_SCREENSHOT_DELAY"] = str(args.screenshot_delay)

    main()
