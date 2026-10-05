@echo off
rem Drag your downloaded budget spreadsheet (.xlsx) onto this file. It shows a preview first and saves only if you say Y.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat
if "%FINANCE_HOME%"=="" set FINANCE_HOME=%USERPROFILE%\Finance
if "%~1"=="" (
  echo Drag the Excel file of your old budget sheet onto this icon.
  pause
  exit /b 1
)
finance sheet-import "%~1"
echo.
set /p OK=Does this look right? Type Y and press Enter to save it, or just press Enter to stop: 
if /i "%OK%"=="Y" finance sheet-import "%~1" --save
echo.
pause
