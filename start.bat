@echo off
REM Start POPPYS AI from Command Prompt (cmd) or by double-clicking this file.
cd /d "%~dp0"
echo Starting POPPYS AI... open http://127.0.0.1:8008 in your browser.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8008
pause
