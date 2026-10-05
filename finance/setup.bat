@echo off
setlocal EnableExtensions
title Finance tracker - one-time setup
cd /d "%~dp0"

echo ==========================================================
echo   Finance tracker - one-time setup
echo ==========================================================
echo.
echo This takes 2 to 5 minutes. You will see progress below.
echo Please do not close this window until it says DONE.
echo.

echo [1/4] Looking for Python...
set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if not defined PY python --version >nul 2>nul
if not defined PY if not errorlevel 1 set "PY=python"
if not defined PY goto :nopython
%PY% --version
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto :oldpython
echo       Found it.
echo.

echo [2/4] Creating a private environment for the app...
if exist ".venv\Scripts\python.exe" goto :venvok
%PY% -m venv .venv
if errorlevel 1 goto :fail
:venvok
echo       Ready.
echo.

echo [3/4] Installing the app (this is the slow part, progress is shown)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :fail
echo.

echo [4/4] Creating your data folder...
if "%FINANCE_HOME%"=="" set "FINANCE_HOME=%USERPROFILE%\Finance"
".venv\Scripts\finance.exe" init --owner Ely --owner Shir
if errorlevel 1 goto :fail

echo.
echo ==========================================================
echo   DONE.
echo ==========================================================
echo Your data folder is:  %FINANCE_HOME%
echo   inbox\     put new statements here
echo   archive\   originals are filed here for you
echo.
echo Next: double-click first-run-rules.bat (if you have it), then
echo import-old-sheet.bat, then dashboard.bat. See START-HERE.txt.
echo.
pause
exit /b 0

:nopython
echo.
echo   Python was NOT found on this computer.
echo   1. Go to https://www.python.org/downloads/ and download Python.
echo   2. Run the installer and TICK the box "Add python to PATH".
echo   3. Close this window and double-click setup.bat again.
echo   (If you already installed it, restart the computer once and try again.)
echo.
pause
exit /b 1

:oldpython
echo.
echo   Your Python is too old. This app needs Python 3.10 or newer.
echo   Install a newer one from https://www.python.org/downloads/ and run setup.bat again.
echo.
pause
exit /b 1

:fail
echo.
echo   ----------------------------------------------------------
echo   Something went wrong (the message is just above).
echo   Please take a photo or screenshot of this window and send it to me.
echo   ----------------------------------------------------------
echo.
pause
exit /b 1
