:: update_app.bat
@echo off
set pid=%1
set installer_path=%2

echo Waiting for process with PID %pid% to finish...

:LOOP
tasklist /FI "PID eq %pid%" 2>NUL | find /I /N "%pid%">NUL
if "%ERRORLEVEL%"=="0" (
    echo Process with PID %pid% is still running...
    goto LOOP
)

echo Process with PID %pid% has finished. Starting installer...

start /wait "" "%installer_path%" /SILENT

echo Installer has finished. Deleting installer file ...
echo %installer_path%

del /F "%installer_path%"
exit /b