@echo off
setlocal
cd /d "%~dp0"
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "SEEDSOW_PYTHON=py -3"
    goto run
)
python --version >nul 2>&1
if not errorlevel 1 (
    set "SEEDSOW_PYTHON=python"
    goto run
)
echo Python 3 was not found. Install Python 3 and try again.
pause
exit /b 1

:run
echo Starting local FTP. Keep this window open while using it.
echo Closing this window stops the PC FTP service.
echo Use STOP.cmd for a graceful stop after the current game operation.
%SEEDSOW_PYTHON% "%~dp0ftp_server.py" --config "%~dp0config.json"
set "SEEDSOW_EXIT=%ERRORLEVEL%"
if not "%SEEDSOW_EXIT%"=="0" (
    echo Startup or operation failed. No dependencies were installed automatically.
    echo If a module is missing, install requirements.txt with your Python 3 interpreter.
    pause
)
exit /b %SEEDSOW_EXIT%
