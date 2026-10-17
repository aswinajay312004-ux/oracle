# One-time setup on the Windows laptop. Safe to run again.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv')) { py -3 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (-not (Test-Path '.env.poppys')) {
  Write-Host 'Creating the restricted POPPYS_APP Oracle account (existing GARMENT_DEMO data is not touched)...' -ForegroundColor Cyan
  .\.venv\Scripts\python.exe scripts\bootstrap_local.py
}
Write-Host 'Granting read access for the explorer and insert access for verified BOM tables...' -ForegroundColor Cyan
.\.venv\Scripts\python.exe scripts\grant_bom_tables.py
.\.venv\Scripts\python.exe scripts\check_oracle.py
Write-Host 'Setup finished. Next: python scripts\discover_schema.py (asks for the GARMENT_DEMO password), then .\start.ps1' -ForegroundColor Green
