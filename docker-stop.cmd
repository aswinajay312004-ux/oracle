@echo off
rem Stop POPPYS AI in Docker (add -Oracle to stop the Oracle container too). See docs\DOCKER.md.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docker-stop.ps1" %*
echo.
pause
