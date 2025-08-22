import sys
import logging
from pathlib import Path
from cx_Freeze import setup, Executable
import importlib.util

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

def wheel_native_files(pkg):
    spec = importlib.util.find_spec(pkg)
    if spec is None or spec.origin is None:
        raise ImportError(f"Cannot find package '{pkg}' or its origin.")
    here = Path(spec.origin).parent
    return list(here.glob("*.pyd")) + list(here.glob("*.dll"))

# Dependencies are automatically detected, but it might need fine tuning.
build_exe_options = {
    "packages": ["tkinter", "pymupdf"],
    "includes": [
        "PIL", 
        "requests", 
        "gettext"
    ],
    "include_files": [
        (str(base_dir / "assets"), "assets"),
        (str(base_dir / "locales"), "locales"),
        (str(base_dir / "update_app.bat"), "update_app.bat"),
    ],
    "build_exe": "cx_build",  # Output directory
    "optimize": 2,  # Enable optimization level 2 (new recommended setting)
    "include_msvcr": True,  # Include MSVC runtime (essential for PyMuPDF DLLs)
    "silent_level": 1,
    # Keep PyMuPDF unzipped for proper DLL loading
    "zip_exclude_packages": ["pymupdf"],
    # Add this to exclude problematic modules that might interfere
    "excludes": ["test", "unittest"],
}

build_exe_options["include_files"].extend(
    (str(f), f"lib/{pkg}/{f.name}")
    for pkg in ["pymupdf"]
    for f in wheel_native_files(pkg)
)

# GUI applications require a different base on Windows (the default is for a console application).
base = None
if sys.platform == "win32":
    base = "Win32GUI"

setup(
    name="Heat Sheet PDF Highlighter",
    version="1.3.3-rc3",
    description="Heat Sheet PDF Highlighter",
    author="Jonas Albrecht",
    maintainer="Jonas Albrecht",
    url="https://github.com/jonalbr/heat-sheet-pdf-highlighter",
    license="GPL-3.0",
    # Use relative license file path per setuptools requirements
    license_files=["LICENSE"],
    options={"build_exe": build_exe_options},
    executables=[
        Executable(
            str(base_dir / "main.py"), base=base, icon=str(base_dir / "assets/icon_no_background.ico"), target_name="heat_sheet_pdf_highlighter.exe"
        )
    ],
)
