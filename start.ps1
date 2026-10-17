$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = if (Test-Path '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
Write-Host 'POPPYS AI is starting at http://127.0.0.1:8008  (press Ctrl+C to stop)' -ForegroundColor Green
& $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8008
