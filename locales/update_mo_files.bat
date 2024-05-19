@echo off
set MSGFMT_PATH=C:\msys64\usr\bin\msgfmt.exe
for /D %%G in ("*") do (
    cd %%G\LC_MESSAGES
    "%MSGFMT_PATH%" -o base.mo base.po
    cd ..\..
)