@echo off
rem Update POPPYS AI to the newest version from GitHub; the share link keeps working. See docs\DOCKER.md.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-live.ps1" %*
echo.
pause
