@echo off
rem Share POPPYS AI with other computers through a free Cloudflare link (docs\DOCKER.md). Close or Ctrl+C to stop.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0share-online.ps1" %*
echo.
pause
