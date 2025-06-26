@echo off

REM Change to the directory where this script is located
cd /d "%~dp0"

echo Compiling .po files to .mo files...
set MSGFMT_PATH=C:\msys64\usr\bin\msgfmt.exe
for /D %%G in ("*") do (
    if exist "%%G\\LC_MESSAGES" (
        pushd "%%G\\LC_MESSAGES"
        "%MSGFMT_PATH%" -o base.mo base.po
        popd
    )
)