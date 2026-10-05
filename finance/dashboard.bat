@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat
if "%FINANCE_HOME%"=="" set FINANCE_HOME=%USERPROFILE%\Finance
echo Opening the dashboard in your browser. Close this window to stop it.
finance serve
pause
