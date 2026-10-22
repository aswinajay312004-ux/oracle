@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call setup-local.bat
  if errorlevel 1 exit /b 1
)
if not defined POPPYS_PORT set "POPPYS_PORT=8008"
echo POPPYS AI development server: http://127.0.0.1:%POPPYS_PORT%
echo Python and frontend file changes restart the server. Refresh the browser to see the new page.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port %POPPYS_PORT% --reload --reload-dir backend --reload-dir frontend --reload-include "*.py" --reload-include "*.js" --reload-include "*.css" --reload-include "*.html"
exit /b %ERRORLEVEL%
