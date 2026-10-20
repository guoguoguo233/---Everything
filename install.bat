@echo off
setlocal
title xyb-weekly-journal - Installer
cd /d "%~dp0"

echo ============================================================
echo   xyb-weekly-journal  Installer
echo ============================================================
echo.

rem ---------- locate Python ----------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY goto NO_PYTHON

rem ---------- hand over to the Python installer ----------
chcp 65001 >nul
%PY% "tools\install.py"
if errorlevel 1 goto FAILED
exit /b 0

:NO_PYTHON
chcp 65001 >nul
echo [1/4] Python not found on this computer.
echo        Trying to install Python 3.12 with winget...
echo.
where winget >nul 2>nul
if errorlevel 1 goto PY_MANUAL
winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
echo.
echo Python installer has finished.
echo Please CLOSE this window, then double-click install.bat again.
pause
exit /b 1

:PY_MANUAL
echo.
echo winget is not available on this system.
echo Please install Python manually:
echo   1. Open https://www.python.org/downloads/
echo   2. Download Python 3.10 or newer
echo   3. IMPORTANT: check "Add python.exe to PATH" during setup
echo   4. After installation, double-click install.bat again
pause
exit /b 1

:FAILED
echo.
echo Installation failed. Please read the messages above.
echo If pip failed because of the network, try:
echo   python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
pause
exit /b 1
