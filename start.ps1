$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & (Join-Path $PSScriptRoot 'setup-local.bat')
    if ($LASTEXITCODE -ne 0) { throw 'Local Python setup failed.' }
}
$port = if ($env:POPPYS_PORT) { $env:POPPYS_PORT } else { '8008' }
Write-Host "POPPYS AI: http://127.0.0.1:$port (Ctrl+C to stop)"
& $python -m uvicorn backend.main:app --host 127.0.0.1 --port $port
if ($LASTEXITCODE -ne 0) { throw 'POPPYS AI did not start.' }
