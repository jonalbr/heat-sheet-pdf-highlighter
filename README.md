# Heat Sheet PDF Highlighter

## Overview
Heat Sheet PDF Highlighter is a Python application designed to facilitate the highlighting of heat sheets in PDF format. This tool is especially useful for individuals and organizations needing to annotate and highlight structured documents like event line-ups or timetables in a standardized PDF format.

![Screenshot of Heat Sheet PDF Highlighter](images/app_screenshot.png "Screenshot of the application")

## Features
- **PDF Annotation:** Automatically highlight specified lines in a PDF which contain the search term.
- **GUI Support:** A user-friendly graphical interface for easier interaction.
- **Persistence:** Settings like language or the last search term are saved between executions for convenience
- **Installation via EXE:** A ready to install .exe can be found under releases (windows only).
- **Localization:** Support for english and german both for the installation process and the application itself.
- **Updates from the App:** Supports checking for updates and the installation of the update without leaving the application.

## Installation
To install the Heat Sheet PDF Highlighter using the provided .exe file on Windows, follow these steps:

- Download the latest release of the application from the releases page.
- Locate the downloaded .exe file and double-click on it to start the installation process.
- Follow the on-screen instructions to complete the installation.
- Once the installation is finished, you can launch the application by searching for "Heat Sheet PDF Highlighter" in the Start menu or by double-clicking on the desktop shortcut, if created.
Note: The .exe file is only available for Windows operating systems. For other platforms or if you wish not to install, you can follow the instructions below to run the application using Python.

## Run Heat Sheet PDF Highlighter script without installing
It is possible to run Heat Sheet PDF Highlighter as a python script. This script is tested with python 3.11.
All necessary Python dependencies can be installed via the provided `requirements.txt`.

To set up the Heat Sheet PDF Highlighter, clone the repository and install the required dependencies:

```bash
git clone https://github.com/jonalbr/heat-sheet-pdf-highlighter.git
cd heat-sheet-pdf-highlighter
pip install -r requirements.txt
```

## Usage

Launch the application using the command:

```bash
python heat_sheet_pdf_highlighter.py
```

Follow the GUI prompts to load a PDF and specify your highlighting preferences.


# Development

## Updating locale

Install `gettext` with `msys64` and ensure the paths in the update `.bat` scripts are correct:

1. Install `msys2`: Download the installer from the official website [https://www.msys2.org/](https://www.msys2.org/) and follow the instructions to install it.

2. Open the `MSYS2 MSYS` terminal from the start menu.

3. Update the package database and core system packages with:

    ```bash
    pacman -Syu
    ```

    If needed, close the `MSYS2 MSYS` terminal and open it again from the start menu.

4. Install `gettext` with:

    ```bash
    pacman -S gettext
    ```

5. Now `gettext` should be installed at the path `C:\msys64\usr\bin\`, and the commands `xgettext`, `msgmerge`, and `msginit` should be available.

6. If installed in a different location or with a different method, update the path in the update`.bat` scripts in `locales\`:

    `update_po_files.bat`:
    ```bat
    set XGETTEXT_PATH=C:\msys64\usr\bin\xgettext.exe
    set MSGMERGE_PATH=C:\msys64\usr\bin\msgmerge.exe
    set MSGINIT_PATH=C:\msys64\usr\bin\msginit.exe
    ```
    `update_mo_files.bat`:
    ```bat
    set MSGFMT_PATH=C:\msys64\usr\bin\msgfmt.exe
    ```



`update_po_files.bat` and `update_mo_files.bat` are used for handling translations in the application:

1. **update_po_files.bat**: This script is used to extract translatable strings from the source code and update the `.po` files with these strings. To run this script, open a command prompt in the `locales` directory and type `./update_po_files.bat`.

2. After running `update_po_files.bat`, you'll need to manually add the translations for the new strings in the `.po` files. These files are located in the `LC_MESSAGES` subdirectories under each language directory in `locales` (for example, `locales/de/LC_MESSAGES/base.po`).

3. **update_mo_files.bat**: After you've added the translations, you can use this script to compile the `.po` files into binary `.mo` files, which are used by the application at runtime. To run this script, open a command prompt in the `locales` directory and type `./update_mo_files.bat`.

Please note that these scripts require certain tools (`xgettext`, `msginit`, and `msgmerge` for `update_po_files.bat`, and `msgfmt` for `update_mo_files.bat`) to be installed and their paths to be correctly set in the scripts. The paths are currently set to locations in `C:\msys64\usr\bin\`, so you'll need to adjust them if your tools are installed in different locations.

## Building EXE

To build the application for deployment:
Note: Due to security reasons the AppId is not public, you are not able to create executables that will be recognized as an update. 

### **Requirements:** 
1.  You need to install `Inno Setup`, which is necessary for creating the executable. `Inno Setup` is a tool used for creating installers for Windows applications. You can download `Inno Setup` from [this link](https://jrsoftware.org/isdl.php). The path "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" is the expected location of the Inno Setup Compiler (ISCC.exe) executable file (default installation path for Inno Setup version 6 on a 64-bit Windows system).

If you have Inno Setup installed in a different location, you would need to modify the path accordingly in the `build.bat` script to point to the correct location of the ISCC.exe file.

2. You also need to install the dependencies listed in the `requirements_build.txt` file. You can install them using the following command:
```bash
pip install -r requirements_build.txt
```

### Setting up the Environment Variables

To create an executable, you need to set up an environment variable `AppId`. This can be done using a `.env` file.

1. Create a new file in the root directory of the project and name it `.env`.

2. Open the `.env` file and add the following line (yes two {{ in front, but just one } after):

    ```
    AppId={{Your_AppId}
    ```

Replace `Your_AppId` with your actual AppId. The AppId is a unique identifier for your application and is used for things like identifying your application to the operating system and the app store, and for handling deep links. It should be in the format of a GUID (Globally Unique Identifier), which is a string of 32 hexadecimal digits, grouped as 8-4-4-4-12 and enclosed in curly braces.

3. Save and close the `.env` file.

The application will now use the value of `AppId` from the `.env` file. Please note that the `.env` file should not be included in any version control system (it's already included in the `.gitignore` file) as it contains sensitive information.


### **Windows:**
#### **Setting up a Virtual Environment and Installing Dependencies**

Before building the application, it's recommended to create a Python virtual environment and install the necessary dependencies. Otherwise unnecessary packages might end up in your executable and make it larger than needed.

1. Open a command prompt in the root directory of the project.

2. Create a new virtual environment:

    ```bash
    python -m venv .venv
    ```

3. Activate the virtual environment:

    ```bash
    .venv\Scripts\activate
    ```

4. Once the virtual environment is activated, install the dependencies listed in the `requirements_build.txt` file:

    ```bash
    pip install -r requirements_build.txt
    ```

Now you're ready to build the application. Make sure to keep the virtual environment activated while building.

### **Build:**


Run the `build.bat` script to compile the application. Make sure to open a command prompt in the directory where the `build.bat` script is located.

```bash
./build.bat
```


# Contributing
Contributions are welcome! Feel free to open issues or submit pull requests to improve the functionality of the Heat Sheet PDF Highlighter.

# License
This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
