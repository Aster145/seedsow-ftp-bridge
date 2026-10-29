@echo off
setlocal
cd /d "%~dp0"
echo Stop the FTP service with STOP.cmd before using this command.
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0transport.py" leave --config "%~dp0config.json"
) else (
    python "%~dp0transport.py" leave --config "%~dp0config.json"
)
pause
