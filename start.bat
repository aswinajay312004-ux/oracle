@echo off
setlocal
REM Start POPPYS AI from Command Prompt or by double-clicking this file.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call setup-local.bat
  if errorlevel 1 goto :failed
)
if not defined POPPYS_PORT set "POPPYS_PORT=8008"
echo POPPYS AI: http://127.0.0.1:%POPPYS_PORT%
echo Press Ctrl+C here to stop the app.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port %POPPYS_PORT%
if errorlevel 1 goto :failed
exit /b 0
:failed
echo POPPYS AI could not start. Read the error above.
pause
exit /b 1
