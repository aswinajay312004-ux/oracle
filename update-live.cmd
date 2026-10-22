@echo off
setlocal
cd /d "%~dp0"
git pull --ff-only
if errorlevel 1 (
  echo Update stopped. Check local changes and Git access before retrying.
  exit /b 1
)
echo Updated. If start-dev.bat is running, refresh the browser. Otherwise run start.bat.
exit /b 0
