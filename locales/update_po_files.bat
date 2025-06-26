@echo off
setlocal

REM Change to the directory where this script is located
cd /d "%~dp0"

REM Set the paths to your xgettext, msginit, and msgmerge executables
set XGETTEXT_PATH=C:\msys64\usr\bin\xgettext.exe
set MSGMERGE_PATH=C:\msys64\usr\bin\msgmerge.exe
set MSGINIT_PATH=C:\msys64\usr\bin\msginit.exe

REM Set the names of your source code file, .pot file, and .po file
set SOURCE_CODE_FILE=..\heat_sheet_pdf_highlighter.py
set POT_FILE=base.pot
set PO_FILE=base.po

REM Run xgettext to create a .pot file from the source code file
"%XGETTEXT_PATH%" --from-code=UTF-8 --language=Python --keyword=ngettext:1,2 --keyword=n_:1,2 -o "%POT_FILE%" "%SOURCE_CODE_FILE%"

REM Loop over each directory
for /D %%G in ("*") do (
    REM Change into the LC_MESSAGES subdirectory
    cd %%G\LC_MESSAGES

    REM If the .po file doesn't exist, create it with msginit
    if not exist "%PO_FILE%" (
        "%MSGINIT_PATH%" --locale=%%G -i "..\..\%POT_FILE%" -o "%PO_FILE%" --no-translator
    )

    REM Run msgmerge to update the .po file with the new translatable strings from the .pot file
    "%MSGMERGE_PATH%" -U "%PO_FILE%" "..\..\%POT_FILE%"

    REM Change back to the parent directory
    cd ..\..
)

REM Run the review/update script for German and autofill for English
echo Running po_update_and_review.py to update German and autofill English...
call ..\.venv\Scripts\activate.bat
python po_update_and_review.py

REM Compile .po to .mo files
echo Compiling .po files to .mo files...
set MSGFMT_PATH=C:\msys64\usr\bin\msgfmt.exe
for /D %%G in ("*") do (
    if exist "%%G\\LC_MESSAGES" (
        pushd "%%G\\LC_MESSAGES"
        "%MSGFMT_PATH%" -o base.mo base.po
        popd
    )
)

endlocal