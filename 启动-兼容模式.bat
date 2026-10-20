@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

rem Redirect TEMP to a folder next to this exe.
rem A single-file EXE extracts itself to TEMP on every run; Huorong / 360 /
rem Defender may block that, which looks like "clicked but nothing happens".
set "PORTABLE_TMP=%~dp0_tmp"
if not exist "%PORTABLE_TMP%" mkdir "%PORTABLE_TMP%" >nul 2>&1
set "TEMP=%PORTABLE_TMP%"
set "TMP=%PORTABLE_TMP%"

set "EXE="
for %%f in (*.exe) do set "EXE=%%~ff"
if not defined EXE goto NO_EXE

"%EXE%" %*
exit /b %errorlevel%

:NO_EXE
echo [ERROR] exe not found in this folder.
pause
exit /b 1
