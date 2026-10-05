@echo off
rem One-time setup for Windows. Needs Python 3.10+ from python.org (tick "Add python to PATH").
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (echo Python was not found. Install it from https://www.python.org/downloads/ and run this again. & pause & exit /b 1)

if not exist .venv (%PY% -m venv .venv || (echo Could not create the virtual environment. & pause & exit /b 1))
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -e . || (echo Install failed. & pause & exit /b 1)

rem Data lives outside this code folder. Keep it out of OneDrive/Drive-synced folders.
if "%FINANCE_HOME%"=="" set FINANCE_HOME=%USERPROFILE%\Finance
finance init --owner Ely --owner Shir

echo.
echo Done. Your folder is: %FINANCE_HOME%
echo   inbox\    drop new statements here
echo   archive\  originals are filed here automatically
echo Then double-click import.bat
pause
