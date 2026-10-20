@echo off
setlocal
title xyb-weekly-journal
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
    where py >nul 2>nul && set "PY=py -3"
)
if not defined PY goto NO_PY

chcp 65001 >nul
%PY% "xyb\cli.py"
if errorlevel 1 goto ABNORMAL
exit /b 0

:NO_PY
echo.
echo Python not found. Please double-click install.bat first.
pause
exit /b 1

:ABNORMAL
echo.
echo ============================================================
echo The program exited with an error.
echo   - If it says modules are missing, run install.bat first.
echo   - If it says Python is not found, install Python 3.10+.
echo ============================================================
pause
exit /b 1
