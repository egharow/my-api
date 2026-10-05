@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Finance tracker - dashboard (close this window to stop it)
cd /d "%~dp0"
if not exist ".venv\Scripts\finance.exe" goto :nosetup
if "%FINANCE_HOME%"=="" set "FINANCE_HOME=%USERPROFILE%\Finance"
echo Opening the dashboard in your browser. Keep this window open while you use it.
".venv\Scripts\finance.exe" serve
echo.
pause
exit /b 0

:nosetup
echo.
echo   The app is not set up yet. Double-click setup.bat first.
echo.
pause
exit /b 1
