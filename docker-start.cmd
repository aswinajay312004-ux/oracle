@echo off
rem Start POPPYS AI in Docker: double-click this file, or run it from Command Prompt. See docs\DOCKER.md.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0docker-start.ps1" %*
echo.
pause
