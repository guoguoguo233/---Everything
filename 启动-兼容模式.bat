@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PORTABLE_TMP=%~dp0_tmp"
if not exist "%PORTABLE_TMP%" mkdir "%PORTABLE_TMP%" >nul 2>&1
set "TEMP=%PORTABLE_TMP%"
set "TMP=%PORTABLE_TMP%"

echo ============================================================
echo  Portable launcher
echo  TEMP redirected to: %PORTABLE_TMP%
echo  (this avoids security software blocking %%TEMP%% writes)
echo ============================================================
echo.

set "EXE="
for %%f in (*.exe) do set "EXE=%%~ff"
if not defined EXE goto NO_EXE

"%EXE%" %*
set "RC=%errorlevel%"

echo.
echo ============================================================
echo  Program exited with code %RC%
echo  If nothing appeared above, the exe was blocked before start.
echo ============================================================
pause
exit /b %RC%

:NO_EXE
echo [ERROR] No .exe found in this folder.
echo Put this .bat in the same folder as 校友邦周志助手.exe
pause
exit /b 1
