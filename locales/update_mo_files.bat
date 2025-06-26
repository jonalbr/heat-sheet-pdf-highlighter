@echo off

REM Change to the directory where this script is located
cd /d "%~dp0"
echo Current directory: %cd%

set MSGFMT_PATH=C:\msys64\usr\bin\msgfmt.exe
for /D %%G in ("*") do (
    if exist "%%G\LC_MESSAGES" (
        echo Entering %%G\LC_MESSAGES
        pushd %%G\LC_MESSAGES
        echo Running: "%MSGFMT_PATH%" -o base.mo base.po
        "%MSGFMT_PATH%" -o base.mo base.po
        if errorlevel 1 echo msgfmt failed in %%G\LC_MESSAGES, but continuing...
        popd
    )
)

exit /b 0