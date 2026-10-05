@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Finance tracker - import
cd /d "%~dp0"
if not exist ".venv\Scripts\finance.exe" goto :nosetup
if "%FINANCE_HOME%"=="" set "FINANCE_HOME=%USERPROFILE%\Finance"
".venv\Scripts\finance.exe" import
echo.
pause
exit /b 0

:nosetup
echo.
echo   The app is not set up yet. Double-click setup.bat first.
echo.
pause
exit /b 1
