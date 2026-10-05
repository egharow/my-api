@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Finance tracker - import old budget sheet
cd /d "%~dp0"
if not exist ".venv\Scripts\finance.exe" goto :nosetup
if "%FINANCE_HOME%"=="" set "FINANCE_HOME=%USERPROFILE%\Finance"
if "%~1"=="" goto :nofile
echo Reading %~1 ...
echo.
".venv\Scripts\finance.exe" sheet-import "%~1"
echo.
set "OK="
set /p OK=Does this look right? Type Y and press Enter to save it, or just press Enter to stop: 
if /i "%OK%"=="Y" ".venv\Scripts\finance.exe" sheet-import "%~1" --save
echo.
pause
exit /b 0

:nofile
echo.
echo   Drag your downloaded budget spreadsheet (the .xlsx file) onto this icon
echo   instead of double-clicking it.
echo.
pause
exit /b 1

:nosetup
echo.
echo   The app is not set up yet. Double-click setup.bat first.
echo.
pause
exit /b 1
