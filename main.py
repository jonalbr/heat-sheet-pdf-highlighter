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

from src.utils.logging import configure_basic_logging, parse_log_level


if __name__ == "__main__":
    # Pass CLI args (except the program name)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--log-level", dest="log_level", default=None, help="Logging level name or numeric value")
    parser.add_argument("--log-file", dest="log_file", default=None, help="Path to log file (optional)")
    args, _ = parser.parse_known_args(sys.argv[1:])

    env_level = os.getenv("LOG_LEVEL")
    env_file = os.getenv("LOG_FILE")

    level_value = args.log_level if args.log_level is not None else env_level
    file_value = args.log_file if args.log_file is not None else env_file

    level = parse_log_level(level_value)
    log_file = file_value or None

    configure_basic_logging(level=level, log_file=log_file)
    from src.app import main

    main()
