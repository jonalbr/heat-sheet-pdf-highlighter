import sys
import re

SETUP_PY = "setup.py"
SETUP_ISS = "setup.iss"
HEAT_SHEET_PDF_HIGHLIGHTER = "heat_sheet_pdf_highlighter.py"


def update_version(version):
    # Update version in setup.py
    with open(SETUP_PY, "r") as file:
        setup_content = file.read()
    setup_content = re.sub(r"(version\s*=\s*['\"])([^'\"]+)(['\"])", r"\g<1>" + version + r"\g<3>", setup_content)
    with open(SETUP_PY, "w") as file:
        file.write(setup_content)

    # Update version in setup.iss
    try:
        with open(SETUP_ISS, "r") as file:
            iss_content = file.read()
        iss_content = re.sub(r"(#define MyAppVersion\s*['\"])([^'\"]+)(['\"])", r"\g<1>" + version + r"\g<3>", iss_content)
        with open(SETUP_ISS, "w") as file:
            file.write(iss_content)
    except Exception as e:
        print(f"Error updating setup.iss: {e}")

    # Update version in heat_sheet_pdf_highlighter.py
    try:
        with open(HEAT_SHEET_PDF_HIGHLIGHTER, "r") as file:
            gui_content = file.read()
        gui_content = re.sub(r"(VERSION_STR\s*=\s*['\"])([^'\"]+)(['\"])", r"\g<1>" + version + r"\g<3>", gui_content)
        with open(HEAT_SHEET_PDF_HIGHLIGHTER, "w") as file:
            file.write(gui_content)
    except Exception as e:
        print(f"Error updating heat_sheet_pdf_highlighter.py: {e}")


def check_version_input(version):
    if not re.match(r"\d+\.\d+\.\d+", version):
        print("Invalid version format. Please use the format x.y.z.")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        version = sys.argv[1]
        check_version_input(version)
    else:
        version = input("Enter the new version: ")
        check_version_input(version)

    update_version(version)
    print("Version updated successfully.")
