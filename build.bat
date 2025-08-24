@echo off
setlocal

REM --- Load environment variables from .env file (if present) ---
if exist .env (
     for /f "usebackq delims== tokens=1,2" %%a in (".env") do set %%a=%%b
 ) else (
     echo .env not found, continuing without loading extra environment vars.
 )

REM --- Select Python interpreter ---
if defined VIRTUAL_ENV (
    echo Using virtual environment: %VIRTUAL_ENV%
    set PY_LAUNCHER=%VIRTUAL_ENV%\Scripts\python.exe
) else (
    echo No virtual environment detected, using system Python on PATH.
    set PY_LAUNCHER=python
)

set CX_FREEZE_SETUP=setup.py
REM --- Resolve Inno Setup compiler path (prefer ProgramFiles(x86), fallback to ProgramFiles) ---
if defined ProgramFiles(x86) (
     set "INNO_COMPILER=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
 ) else (
     set "INNO_COMPILER=%ProgramFiles%\Inno Setup 6\ISCC.exe"
 )

set "INNO_SCRIPT=setup.iss"

REM --- Ensure Python 3.13 is used ---
echo Using Python version:
%PY_LAUNCHER% --version

REM --- Check current directory ---
echo Current directory:
cd

REM --- Building the application with cx_Freeze ---
echo Building application with cx_Freeze...
"%PY_LAUNCHER%" "%CX_FREEZE_SETUP%" build
if %ERRORLEVEL% neq 0 (
    echo Failed to build with cx_Freeze!
    exit /b 1
)

REM --- Compiling the installer with Inno Setup ---
echo Compiling Inno Setup Script...
if not exist "%INNO_COMPILER%" (
    echo Inno Setup compiler not found at: "%INNO_COMPILER%"
    echo Ensure Inno Setup 6 is installed on this machine.
    exit /b 1
)
"%INNO_COMPILER%" "%INNO_SCRIPT%"
if %ERRORLEVEL% neq 0 (
    echo Failed to compile Inno Setup script!
    exit /b 1
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
REM In GitHub Actions, timeout reads from redirected stdin and returns an error.
REM Skip waiting when running in CI to avoid non-zero exit codes post-build.
if /i "%GITHUB_ACTIONS%"=="true" (
    rem CI detected, no pause
) else (
    echo Build complete. Exiting in 10 seconds...
    timeout /t 10 /nobreak >nul 2>&1
)
endlocal
exit /b