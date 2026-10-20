@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "LOG=%~dp0_diag.txt"

> "%LOG%" echo === portable-mode diagnostic ===
>> "%LOG%" echo time: %date% %time%
>> "%LOG%" echo folder: %cd%

set "PORTABLE_TMP=%~dp0_tmp"
if not exist "%PORTABLE_TMP%" mkdir "%PORTABLE_TMP%" >nul 2>&1
set "TEMP=%PORTABLE_TMP%"
set "TMP=%PORTABLE_TMP%"
>> "%LOG%" echo redirected TEMP: %TEMP%

set "EXE="
for %%f in (*.exe) do set "EXE=%%~ff"
if not defined EXE goto NO_EXE
>> "%LOG%" echo exe: %EXE%
>> "%LOG%" echo.

>> "%LOG%" echo --- version ---
"%EXE%" --version >> "%LOG%" 2>&1
>> "%LOG%" echo exit: %errorlevel%
>> "%LOG%" echo.

>> "%LOG%" echo --- check ---
"%EXE%" --check >> "%LOG%" 2>&1
>> "%LOG%" echo exit: %errorlevel%
>> "%LOG%" echo.
>> "%LOG%" echo === done ===

echo Diagnostic finished. Log:
echo   %LOG%
start "" notepad "%LOG%"
pause
exit /b 0

:NO_EXE
echo [ERROR] No .exe found in this folder.
pause
exit /b 1
