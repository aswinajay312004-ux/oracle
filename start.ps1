$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = if (Test-Path '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
Write-Host 'POPPYS AI is starting at http://127.0.0.1:8008  (press Ctrl+C to stop)' -ForegroundColor Green
Write-Host 'Log: data\logs\app.jsonl (one JSON line per request and event)'
# One worker (SQLite and the log file are owned by one process); no Server header; the app writes its own
# access log with request IDs, so uvicorn's is off.
& $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8008 --workers 1 --no-server-header --no-access-log
