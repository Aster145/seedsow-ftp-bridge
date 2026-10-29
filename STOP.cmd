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
echo Python 3 was not found. The FTP service was not changed.
pause
exit /b 1

:run
%SEEDSOW_PYTHON% -c "import ftplib; f=ftplib.FTP(); f.connect('127.0.0.1',2121,timeout=10); f.login('seedsow','seedsow'); print(f.sendcmd('SITE STOP')); f.close()"
set "SEEDSOW_EXIT=%ERRORLEVEL%"
if not "%SEEDSOW_EXIT%"=="0" (
    echo Could not stop the FTP service at 127.0.0.1:2121. No process was killed.
    pause
)
exit /b %SEEDSOW_EXIT%
