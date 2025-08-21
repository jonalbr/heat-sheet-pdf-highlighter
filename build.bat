@echo off
setlocal

REM --- Load environment variables from .env file ---
for /f "delims== tokens=1,2" %%a in (.env) do set %%a=%%b

REM --- Check if virtual environment is activated ---
if defined VIRTUAL_ENV (
    echo Using virtual environment: %VIRTUAL_ENV%
    set PY_LAUNCHER=%VIRTUAL_ENV%\Scripts\python.exe
) else (
    echo Error: No virtual environment detected. Please activate a Python virtual environment before running this script.
    goto end
)

set CX_FREEZE_SETUP=setup.py
set INNO_COMPILER="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
set INNO_SCRIPT=setup.iss

REM --- Ensure Python 3.13 is used ---
echo Using Python version:
%PY_LAUNCHER% --version

REM --- Check current directory ---
echo Current directory:
cd

REM --- Building the application with cx_Freeze ---
echo Building application with cx_Freeze...
%PY_LAUNCHER% %CX_FREEZE_SETUP% build
if %ERRORLEVEL% neq 0 (
    echo Failed to build with cx_Freeze!
    goto end
)

REM --- Compiling the installer with Inno Setup ---
echo Compiling Inno Setup Script...
%INNO_COMPILER% %INNO_SCRIPT%
if %ERRORLEVEL% neq 0 (
    echo Failed to compile Inno Setup script!
    goto end
)

REM --- Generate SHA256 file for the installer ---
echo Generating SHA256 checksum for installer...
for %%F in (heat_sheet_pdf_highlighter_installer.exe) do (
    if exist "%%~fF" (
        for /f "tokens=1" %%H in ('certutil -hashfile "%%~fF" SHA256 ^| find /i /v "SHA256" ^| find /i /v "certutil"') do (
            echo %%H  %%~nxF> "%%~fF.sha256"
        )
        echo Created: %%~fF.sha256
    ) else (
        echo Installer not found in current directory, attempting to locate in project root...
        if exist "%CD%\heat_sheet_pdf_highlighter_installer.exe" (
            for /f "tokens=1" %%H in ('certutil -hashfile "%CD%\heat_sheet_pdf_highlighter_installer.exe" SHA256 ^| find /i /v "SHA256" ^| find /i /v "certutil"') do (
                echo %%H  heat_sheet_pdf_highlighter_installer.exe> "%CD%\heat_sheet_pdf_highlighter_installer.exe.sha256"
            )
            echo Created: %CD%\heat_sheet_pdf_highlighter_installer.exe.sha256
        ) else (
            echo Warning: Installer file not found. Skipping SHA generation.
        )
    )
)

echo Build and compilation successful!
:end
timeout /t 10
endlocal