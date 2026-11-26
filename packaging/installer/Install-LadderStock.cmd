@echo off
rem LadderStock installer entry: double-click me.
rem The bundle must sit next to this file as .\LadderStock\ (onedir output).
setlocal
chcp 65001 >nul
where powershell >nul 2>nul
if errorlevel 1 (
  echo [ERROR] PowerShell not found. Windows 10/11 ships it; cannot continue.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set RC=%errorlevel%
if not "%RC%"=="0" (
  echo.
  echo [ERROR] install failed with code %RC%. Read the message above.
)
pause
exit /b %RC%
