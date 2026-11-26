@echo off
rem Uninstall LadderStock (program only; data and config are kept by default).
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
set RC=%errorlevel%
pause
exit /b %RC%
