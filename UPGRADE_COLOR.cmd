@echo off
setlocal
cd /d "%~dp0"
echo COLOR UPGRADE: Requires the installed v0.1.0 monochrome script.
echo First stop FTP, hold L to leave the bridge, then enter Receive PC.
echo The upgrade saves ftp_console_color.lua and preserves ftp_console.lua.
echo Transfer takes about 6 minutes. Confirm PC FILE SAVED before pressing L then Y.
pause
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0transport.py" upgrade-color --config "%~dp0config.json"
) else (
    python "%~dp0transport.py" upgrade-color --config "%~dp0config.json"
)
pause
