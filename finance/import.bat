@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat
if "%FINANCE_HOME%"=="" set FINANCE_HOME=%USERPROFILE%\Finance
finance import
echo.
pause
