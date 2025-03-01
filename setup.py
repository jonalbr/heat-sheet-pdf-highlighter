import sys
import logging
from pathlib import Path
from cx_Freeze import setup, Executable

# Set up logging
logging.basicConfig(filename="cx_freeze.log", filemode="w", level=logging.DEBUG)
logger = logging.getLogger()


class StreamToLogger:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level

    def write(self, message):
        if message.rstrip() != "":
            self.logger.log(self.level, message.rstrip())

    def flush(self):
        pass


# Redirect stdout and stderr to the logger
sys.stdout = StreamToLogger(logger, logging.INFO)
sys.stderr = StreamToLogger(logger, logging.ERROR)

# Define the base directory for your project
base_dir = Path(__file__).parent

# Dependencies are automatically detected, but it might need fine tuning.
build_exe_options = {
    "packages": [],  # Required packages
    "excludes": [
        "PyQt6",
        "matplotlib",
        "PySide2",
        "numpy",
        "unittest",
        "jupyter_client",
        "jupyter_core",
        "matplotlib_inline",
        "multiprocessing",
        "scipy",
    ],  # Exclude unnecessary packages to reduce size
    "includes": ["pymupdf", "PIL", "requests", "pymupdf.mupdf", "pymupdf.utils"],  # Include required packages
    "include_files": [
        (str(base_dir / "assets"), "assets"),
        (str(base_dir / "locales"), "locales"),
        (str(base_dir / "update_app.bat"), "update_app.bat"),
    ],
    "build_exe": "cx_build",  # Output directory
}

# GUI applications require a different base on Windows (the default is for a console application).
base = None
if sys.platform == "win32":
    base = "Win32GUI"

setup(
    name="Heat Sheet PDF Highlighter",
    version="1.2.0",
    description="Heat Sheet PDF Highlighter",
    author="Jonas Albrecht",
    maintainer="Jonas Albrecht",
    url="https://github.com/jonalbr/heat-sheet-pdf-highlighter",
    license="GPL-3.0",
    license_file=str(base_dir / "LICENSE"),
    options={"build_exe": build_exe_options},
    executables=[Executable(str(base_dir / "heat_sheet_pdf_highlighter.py"), base=base, icon=str(base_dir / "assets/icon_no_background.ico"))],
)
