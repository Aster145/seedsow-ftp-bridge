@echo off
setlocal
cd /d "%~dp0"
echo FIRST INSTALL: Enter Receive PC in the game before continuing.
echo This only sends the installer. Follow the final game confirmation steps.
echo The transfer takes about 12 minutes. Keep OBS and the game running.
pause
py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0transport.py" install --config "%~dp0config.json"
) else (
    python "%~dp0transport.py" install --config "%~dp0config.json"
)
pause
